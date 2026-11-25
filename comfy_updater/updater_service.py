from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable

from .archive import (
    ARCHIVE_INCOMPLETE_ERROR,
    ARCHIVE_NO_MODEL_FOLDERS_ERROR,
    ArchiveListingError,
    list_archive_entries,
)
from .civitai_client import CivitaiClient, CivitaiRequestError
from .fallback_http import FallbackHttp
from .path_resolver import list_model_files, normalize_model_types, resolve_model_roots
from .preview_fallback import FallbackOutcome, PreviewFallback
from .rename_store import BASE_CODES_FILENAME
from .sidecar import (
    info_sidecar_path,
    preview_sidecar_path,
    read_json,
    sidecar_identifies_model,
    stored_sha256,
    write_json,
)
from .hashing import HashingCancelled, sha256_file

ProgressCallback = Callable[[int, int, str], None]
ItemCallback = Callable[[dict], None]
FilesCallback = Callable[[list[dict]], None]


class UpdaterService:
    def __init__(self, config_store):
        self.config_store = config_store

    def run_scan(
        self,
        payload: dict,
        progress: ProgressCallback,
        item_callback: ItemCallback | None = None,
        control=None,
    ) -> tuple[dict, list[dict]]:
        return self._run(payload, progress, mode="scan", item_callback=item_callback, control=control)

    def run_check_updates(
        self,
        payload: dict,
        progress: ProgressCallback,
        item_callback: ItemCallback | None = None,
        control=None,
        files_callback: FilesCallback | None = None,
        client=None,
    ) -> tuple[dict, list[dict]]:
        return self._run(
            payload, progress, mode="check", item_callback=item_callback, control=control,
            files_callback=files_callback, client=client,
        )

    def make_client(self, should_stop=None) -> CivitaiClient:
        """A Civitai client from the settings, for a caller that shares one across passes."""
        config = self.config_store.get()
        return CivitaiClient(
            api_key=config.get("apiKey", ""),
            timeout_seconds=int(config.get("requestTimeoutSeconds", 30)),
            max_retries=int(config.get("maxRetries", 4)),
            should_stop=should_stop,
        )

    def get_effective_roots(
        self, model_types: list[str] | None = None, include_custom_paths: bool = True
    ) -> dict[str, list[str]]:
        config = self.config_store.get()
        normalized = normalize_model_types(model_types)
        roots = resolve_model_roots(config, normalized, include_custom_paths=include_custom_paths)
        return {model_type: [str(path) for path in paths] for model_type, paths in roots.items()}

    def list_current_file_paths(self) -> set[str]:
        """Fast filesystem scan — just returns the set of model file paths (no hashing)."""
        paths, _warnings = self.inspect_current_files()
        return paths

    def inspect_current_files(self) -> tuple[set[str], list[dict]]:
        """Return current model paths and non-fatal sidecar discovery warnings."""
        config = self.config_store.get()
        model_types = normalize_model_types(None)
        roots = resolve_model_roots(config, model_types, include_custom_paths=True)
        sidecar_warnings: list[dict] = []
        files = list_model_files(
            roots,
            include_sidecar_only=bool(config.get("treatSidecarsAsInstalled", True)),
            sidecar_warnings=sidecar_warnings,
        )
        return {str(f["path"]).lower() for f in files}, sidecar_warnings

    def _run(
        self,
        payload: dict,
        progress: ProgressCallback,
        mode: str,
        item_callback: ItemCallback | None = None,
        control=None,
        files_callback: FilesCallback | None = None,
        client=None,
    ) -> tuple[dict, list[dict]]:
        config = self.config_store.get()
        model_types = normalize_model_types(payload.get("modelTypes"))
        include_custom = bool(payload.get("includeCustomPaths", True))
        refetch_metadata = bool(payload.get("refetchMetadata", False))
        force_rehash = bool(payload.get("forceRehash", False))
        request_delay_seconds = max(0.0, int(config.get("requestDelayMs", 120)) / 1000.0)

        roots = resolve_model_roots(config, model_types, include_custom_paths=include_custom)
        sidecar_warnings: list[dict] = []
        files = _dedupe_model_files(
            list_model_files(
                roots,
                include_sidecar_only=bool(config.get("treatSidecarsAsInstalled", True)),
                sidecar_warnings=sidecar_warnings,
            )
        )
        total = len(files)
        if files_callback:
            files_callback(files)

        progress(0, total, f"Discovered {total} model files")

        should_stop = control.is_cancelled if control else None
        if client is None:
            client = self.make_client(should_stop)
        # Files still without a preview after the Civitai step ask the other sources.
        fallback = (
            _ScanFallback(config, self.config_store.data_dir, should_stop)
            if mode == "scan" and PreviewFallback.is_enabled(config)
            else None
        )

        stats = {
            "total": total,
            "resolved": 0,
            "notFound": 0,
            "withUpdates": 0,
            "skipped": 0,
            "errors": 0,
            "previewsFound": 0,
        }
        items: list[dict] = []

        for index, model_entry in enumerate(files, start=1):
            if control:
                control.wait_if_paused()
                if control.is_cancelled():
                    break

            model_path = model_entry["path"]
            model_type = model_entry["modelType"]
            info_path = model_entry.get("infoPath")
            preview_path = model_entry.get("previewPath")
            metadata_only = bool(model_entry.get("metadataOnly"))
            progress(index - 1, total, f"{mode}: {model_path.name}")

            try:
                item = self._process_one(
                    client=client,
                    model_path=model_path,
                    model_type=model_type,
                    mode=mode,
                    refetch_metadata=refetch_metadata,
                    force_rehash=force_rehash,
                    info_path=info_path,
                    preview_path=preview_path,
                    metadata_only=metadata_only,
                    should_stop=should_stop,
                )
            except HashingCancelled:
                break
            except Exception as exc:  # noqa: BLE001 - return per-file errors without killing the whole job
                item = {
                    "modelPath": str(model_path),
                    "modelType": model_type,
                    "metadataOnly": metadata_only,
                    "modelId": "",
                    "status": "error",
                    "error": str(exc),
                    "hasUpdate": False,
                    "previewUrl": "",
                    "previewType": "image",
                    "nsfw": False,
                    "lastCheckedAt": _utc_now(),
                }
            # A lookup cut short by Stop can look like "not found"; the job is
            # over anyway, so it is not reported.
            if control and control.is_cancelled():
                break
            # modelPath is the sidecar for a metadata-only entry, so the card
            # copies this path to the weights instead.
            item["filePath"] = str(model_entry.get("filePath") or model_path)

            preview = preview_path or preview_sidecar_path(model_path)
            # Not for a file that errored: Civitai may serve it next time, and a
            # preview from elsewhere would then keep out Civitai's own.
            if (
                fallback is not None
                and not metadata_only
                and item.get("status") != "error"
                and not preview.exists()
            ):
                # Pause and Stop hold here as they do before the Civitai step.
                if control:
                    control.wait_if_paused()
                    if control.is_cancelled():
                        break
                outcome = fallback.run(
                    model_path=model_path,
                    info_path=info_path or info_sidecar_path(model_path),
                    preview_path=preview,
                    local_hash=item.get("localHash") or "",
                    refetch=refetch_metadata,
                    on_source=_announce_source(progress, index - 1, total, model_path.name),
                )
                # As above: a lookup cut short by Stop is not reported.
                if control and control.is_cancelled():
                    break
                if outcome is not None:
                    item["fallbackSource"] = outcome.source
                    stats["previewsFound"] += int(outcome.preview_written)
            item.setdefault("fallbackSource", "")
            item["hasLocalPreview"] = preview.exists()

            if item.get("status") == "ok":
                stats["resolved"] += 1
            if item.get("status") == "not_found":
                stats["notFound"] += 1
            if item.get("status") == "skipped":
                stats["skipped"] += 1
            if item.get("status") == "error":
                stats["errors"] += 1
            if item.get("hasUpdate"):
                stats["withUpdates"] += 1

            items.append(item)
            if item_callback:
                item_callback(item)
            progress(index, total, f"Processed {index}/{total}")
            if request_delay_seconds > 0 and item.get("status") != "skipped":
                time.sleep(request_delay_seconds)

        if mode == "scan":
            summary = {
                "mode": mode,
                "total": stats["total"],
                "refreshed": stats["resolved"],
                "skipped": stats["skipped"],
                "notFound": stats["notFound"],
                "errors": stats["errors"],
                "previewsFound": stats["previewsFound"],
                "modelTypes": model_types,
                "includeCustomPaths": include_custom,
                "sidecarWarnings": sidecar_warnings,
            }
        else:
            summary = {
                "mode": mode,
                "total": stats["total"],
                "resolved": stats["resolved"],
                "withUpdates": stats["withUpdates"],
                "notFound": stats["notFound"],
                "errors": stats["errors"],
                "modelTypes": model_types,
                "includeCustomPaths": include_custom,
                "sidecarWarnings": sidecar_warnings,
            }
        return summary, items

    def _process_one(
        self,
        client: CivitaiClient,
        model_path: Path,
        model_type: str,
        mode: str,
        refetch_metadata: bool,
        force_rehash: bool,
        info_path: Path | None = None,
        preview_path: Path | None = None,
        metadata_only: bool = False,
        should_stop=None,
        read_only: bool = False,
        existing_info: dict | None = None,
    ) -> dict:
        # read_only is for the archive: nothing next to its files may be
        # written, so no info file rewrite and no preview download.
        # existing_info is the info file as already read (the archive's
        # listing), so a drive lost since does not turn the model unknown.
        info_path = info_path or info_sidecar_path(model_path)
        preview_path = preview_path or preview_sidecar_path(model_path)

        if existing_info is None:
            existing_info = read_json(info_path)
        # A sidecar identifies a model only when it carries both ids. The stub
        # written for a not_found file records the hash but no identity, so it
        # must not count as "metadata already fetched".
        has_identity = _sidecar_identifies_model(existing_info)
        stored_hash = _stored_sha256(existing_info)

        if mode == "scan" and has_identity and not refetch_metadata:
            if not read_only:
                _download_preview_if_needed(client, preview_path, existing_info, force=False)
            _skip_url, _skip_type = _first_preview(existing_info)
            is_nsfw = False
            if isinstance(existing_info, dict):
                is_nsfw = bool(existing_info.get("nsfw"))
                if not is_nsfw:
                    model_info = existing_info.get("model")
                    if isinstance(model_info, dict):
                        is_nsfw = bool(model_info.get("nsfw"))
            return {
                "modelPath": str(model_path),
                "modelType": model_type,
                "metadataOnly": metadata_only,
                "modelId": existing_info.get("modelId", ""),
                "modelName": _model_name(existing_info),
                "baseModel": existing_info.get("baseModel", ""),
                "status": "skipped",
                "hasUpdate": False,
                "localHash": "",
                "localVersionId": existing_info.get("id", ""),
                "localVersionName": existing_info.get("name", ""),
                "latestVersionId": "",
                "latestVersionName": "",
                "latestBaseModel": "",
                "latestVersionDate": "",
                "previewUrl": _skip_url,
                "previewType": _skip_type,
                "remoteVersions": [],
                "modelUrl": "",
                "versionUrl": "",
                "downloadUrl": "",
                "nsfw": is_nsfw,
                "lastCheckedAt": _utc_now(),
            }

        local_hash = None
        version_data = None
        model_id = None

        can_use_sidecar = mode == "check" and has_identity and (metadata_only or not force_rehash)
        can_refetch_by_sidecar_id = (
            mode == "scan" and refetch_metadata and has_identity and (metadata_only or not force_rehash)
        )

        def identify_by_hash() -> tuple[str, dict | None]:
            # A stub sidecar already holds the file's hash: reuse it so a model
            # Civitai did not know last time is retried with one API call
            # instead of re-hashing gigabytes. Force rehash bypasses this.
            if stored_hash and not force_rehash:
                file_hash = stored_hash
            else:
                file_hash = sha256_file(model_path, should_stop=should_stop)
            return file_hash, client.get_version_by_hash(file_hash)

        if can_use_sidecar:
            model_id = existing_info.get("modelId")
            version_data = {
                "id": existing_info.get("id"),
                "name": existing_info.get("name"),
                "baseModel": existing_info.get("baseModel", ""),
                "modelId": model_id,
                "downloadUrl": existing_info.get("downloadUrl"),
                "publishedAt": existing_info.get("publishedAt", ""),
                "createdAt": existing_info.get("createdAt", ""),
                "model": existing_info.get("model", {}),
                "images": existing_info.get("images", []),
            }
            if not read_only:
                _download_preview_if_needed(client, preview_path, version_data, force=False)
        elif can_refetch_by_sidecar_id:
            model_id = existing_info.get("modelId")
            version_data = client.get_version(existing_info.get("id"))
            if version_data:
                model_id = version_data.get("modelId") or model_id
            else:
                # Fallback when the sidecar version id is stale or unavailable.
                if not metadata_only:
                    local_hash, version_data = identify_by_hash()
                    if version_data:
                        model_id = version_data.get("modelId")
        else:
            if not metadata_only:
                local_hash, version_data = identify_by_hash()
                if version_data:
                    model_id = version_data.get("modelId")

        if not version_data or not model_id:
            payload = {
                "modelPath": str(model_path),
                "modelType": model_type,
                "metadataOnly": metadata_only,
                "modelId": "",
                "modelName": "",
                "baseModel": "",
                "status": "not_found",
                "hasUpdate": False,
                "localHash": local_hash or "",
                "lastCheckedAt": _utc_now(),
                "previewUrl": "",
                "previewType": "image",
                "remoteVersions": [],
                "modelUrl": "",
                "versionUrl": "",
                "downloadUrl": "",
                "nsfw": False,
            }
            # What another source found still describes this file unless it was replaced since.
            filled_elsewhere = bool(_fallback_record(existing_info))
            replaced = _replaced(stored_hash, local_hash)
            if filled_elsewhere and not replaced:
                payload.update(_fallback_fields(existing_info, model_path.name))
            # A failed lookup cannot tell "removed" from "Civitai is down", so
            # it never replaces a sidecar that already identifies the model.
            # What another source found is kept until the file is replaced,
            # ids or not: it described the old file.
            rewrite = replaced if filled_elsewhere else not has_identity
            if mode == "scan" and not metadata_only and rewrite and not read_only:
                write_json(
                    info_path,
                    {
                        "id": "",
                        "modelId": "",
                        "name": model_path.name,
                        "files": [{"hashes": {"SHA256": local_hash or ""}}],
                        "extensions": {"source": "comfy-civitai-updater"},
                    },
                )
            return payload

        if mode == "scan":
            is_nsfw = False
            if isinstance(version_data, dict):
                model_info = version_data.get("model")
                if isinstance(model_info, dict):
                    is_nsfw = bool(model_info.get("nsfw"))

            model_url = client.model_page_url(model_id)
            version_url = client.version_page_url(model_id, version_data.get("id"))
            preview_url, preview_type = _first_preview(version_data)

            sidecar_payload = dict(version_data)
            if local_hash:
                _set_sha256_hash(sidecar_payload, local_hash)
            sidecar_payload.setdefault("extensions", {})
            sidecar_payload["extensions"]["source"] = "comfy-civitai-updater"
            sidecar_payload["extensions"]["updatedAt"] = _utc_now()
            # A stub from an earlier miss is replaced once the model is found.
            if (refetch_metadata or not has_identity) and not read_only:
                write_json(info_path, sidecar_payload)

            if not read_only:
                _download_preview_if_needed(client, preview_path, version_data, force=False)

            return {
                "modelPath": str(model_path),
                "modelType": model_type,
                "metadataOnly": metadata_only,
                "modelId": str(model_id),
                "modelName": _model_name(version_data),
                "baseModel": version_data.get("baseModel", ""),
                "status": "ok",
                "localHash": local_hash or "",
                "localVersionId": version_data.get("id"),
                "localVersionName": version_data.get("name", ""),
                "latestVersionId": "",
                "latestVersionName": "",
                "latestBaseModel": "",
                "latestVersionDate": "",
                "hasUpdate": False,
                "previewUrl": preview_url,
                "previewType": preview_type,
                "remoteVersions": [],
                "modelUrl": model_url,
                "versionUrl": version_url,
                "downloadUrl": "",
                "nsfw": is_nsfw,
                "lastCheckedAt": _utc_now(),
            }

        try:
            creator_name, model_versions, is_nsfw = client.get_model_versions_for_model(model_id)
        except CivitaiRequestError as exc:
            local_preview_url, local_preview_type = _first_preview(version_data)
            # A removed model's creator and source can only come from its info file.
            info_applies = not _replaced(stored_hash, local_hash)
            creator_name = _creator_name(existing_info) if info_applies else ""
            return {
                "modelPath": str(model_path),
                "modelType": model_type,
                "metadataOnly": metadata_only,
                "modelId": str(model_id),
                "modelName": _model_name(version_data),
                "baseModel": version_data.get("baseModel", ""),
                "status": "error",
                "error": str(exc),
                "hasUpdate": False,
                "localHash": local_hash or "",
                "localVersionId": version_data.get("id", ""),
                "localVersionName": version_data.get("name", ""),
                "localVersionDate": _version_date(version_data),
                "latestVersionId": "",
                "latestVersionName": "",
                "latestBaseModel": "",
                "latestVersionDate": "",
                "creatorName": creator_name,
                "creatorStatus": _creator_status(client, creator_name) if exc.status == 404 else "",
                "fallbackSource": _fallback_source(existing_info) if info_applies else "",
                "previewUrl": local_preview_url,
                "previewType": local_preview_type,
                "localPreviewUrl": local_preview_url,
                "localPreviewType": local_preview_type,
                "remoteVersions": [],
                "modelUrl": client.model_page_url(model_id),
                "versionUrl": client.version_page_url(model_id, version_data.get("id")),
                "downloadUrl": "",
                "nsfw": False,
                "lastCheckedAt": _utc_now(),
            }
        remote_versions = _normalize_remote_versions(client, model_id, model_versions)
        local_id = str(version_data.get("id") or "")
        local_date = _version_date(version_data)
        local_remote = next(
            (entry for entry in remote_versions if entry.get("versionId") == local_id),
            None,
        )
        # Sidecars written by other tools may carry no release date. Without
        # one, nothing can be judged newer, so take the date Civitai reports
        # for the very same version.
        if not local_date and local_remote and local_remote.get("versionDate"):
            local_date = local_remote["versionDate"]
            version_data = dict(version_data)
            version_data["publishedAt"] = local_date
        new_versions = [
            remote_version
            for remote_version in remote_versions
            if remote_version.get("versionDate")
            and remote_version.get("versionId", "") != local_id
            and local_date
            and remote_version.get("versionDate", "") > local_date
        ]
        primary_new_version = new_versions[0] if new_versions else {}
        local_name = version_data.get("name", "")
        local_preview_url, local_preview_type = _first_preview(version_data)
        # Sidecars written while the API was SFW-filtered often carry empty
        # image lists. Backfill the local preview from the freshly fetched
        # remote data for the same version so those models heal on re-check.
        if not local_preview_url and local_id:
            if local_remote and local_remote.get("previewUrl"):
                local_preview_url = local_remote["previewUrl"]
                local_preview_type = local_remote.get("previewType") or "image"
                if not read_only and not preview_path.exists():
                    _download_preview_media(client, preview_path, local_preview_url, local_preview_type)
        # Pick url and type as a pair so a local video preview keeps its
        # "video" type instead of inheriting the empty remote entry's default.
        if primary_new_version.get("previewUrl"):
            preview_url = primary_new_version.get("previewUrl", "")
            preview_type = primary_new_version.get("previewType") or "image"
        else:
            preview_url = local_preview_url
            preview_type = local_preview_type

        model_url = client.model_page_url(model_id)
        version_url = primary_new_version.get("versionUrl", "") or client.version_page_url(model_id, local_id)

        if local_hash and version_data and not read_only:
            sidecar_payload = dict(version_data)
            _set_sha256_hash(sidecar_payload, local_hash)
            sidecar_payload.setdefault("extensions", {})
            sidecar_payload["extensions"]["source"] = "comfy-civitai-updater"
            sidecar_payload["extensions"]["updatedAt"] = _utc_now()
            try:
                write_json(info_path, sidecar_payload)
            except OSError as exc:
                # The sidecar only caches this lookup; a read-only folder must
                # not turn a found update into an error.
                print(f"Civitai updater: could not write {info_path} ({exc})")
            _download_preview_if_needed(client, preview_path, version_data)

        item = {
            "modelPath": str(model_path),
            "modelType": model_type,
            "metadataOnly": metadata_only,
            "modelId": str(model_id),
            "modelName": _model_name(version_data),
            "baseModel": version_data.get("baseModel", ""),
            "status": "ok",
            "localHash": local_hash or "",
            "localVersionId": local_id,
            "localVersionName": local_name,
            "localVersionDate": local_date,
            "latestVersionId": primary_new_version.get("versionId", ""),
            "latestVersionName": primary_new_version.get("versionName", ""),
            "latestBaseModel": primary_new_version.get("baseModel", ""),
            "latestVersionDate": primary_new_version.get("versionDate", ""),
            "hasUpdate": bool(new_versions),
            "creatorName": creator_name,
            "previewUrl": preview_url,
            "previewType": preview_type,
            "localPreviewUrl": local_preview_url,
            "localPreviewType": local_preview_type,
            "remoteVersions": remote_versions,
            "modelUrl": model_url,
            "versionUrl": version_url,
            "downloadUrl": primary_new_version.get("downloadUrl", ""),
            "nsfw": is_nsfw,
            "lastCheckedAt": _utc_now(),
        }
        model_stats = client.model_stats(model_id)
        if model_stats:
            item["modelStats"] = model_stats
        return item

    def run_archive_check(
        self,
        payload: dict,
        progress: ProgressCallback,
        item_callback: ItemCallback | None = None,
        control=None,
        *,
        archive_root: Path,
        client=None,
    ) -> tuple[dict, list[dict]]:
        """Check the models in the archive folder against Civitai without writing there."""
        config = self.config_store.get()
        model_types = normalize_model_types(payload.get("modelTypes"))
        include_custom = bool(payload.get("includeCustomPaths", True))
        request_delay_seconds = max(0.0, int(config.get("requestDelayMs", 120)) / 1000.0)

        type_roots = resolve_model_roots(config, model_types, include_custom_paths=include_custom)
        try:
            entries, counts = list_archive_entries(archive_root, type_roots)
        except ArchiveListingError:
            # A partial listing would drop archived rows: the last results stay.
            return {"mode": "archive", "archiveError": ARCHIVE_INCOMPLETE_ERROR}, []
        if not counts["typeFolders"]:
            # Not laid out like the models folder (or an empty, unmounted mount
            # point): reporting every archived model gone would be wrong.
            return {"mode": "archive", "archiveError": ARCHIVE_NO_MODEL_FOLDERS_ERROR}, []
        entries = [entry for entry in entries if entry["modelType"] in model_types]
        total = len(entries)
        progress(0, total, f"Discovered {total} archived models")

        should_stop = control.is_cancelled if control else None
        if client is None:
            client = self.make_client(should_stop)
        stats = {"resolved": 0, "notFound": 0, "withUpdates": 0, "errors": 0}
        items: list[dict] = []

        for index, entry in enumerate(entries, start=1):
            if control:
                control.wait_if_paused()
                if control.is_cancelled():
                    break

            info_path = entry["infoPath"]
            progress(index - 1, total, f"archive: {info_path.name}")
            try:
                item = self._process_one(
                    client=client,
                    model_path=entry["path"],
                    model_type=entry["modelType"],
                    mode="check",
                    refetch_metadata=False,
                    force_rehash=False,
                    info_path=info_path,
                    preview_path=entry["previewPath"],
                    metadata_only=True,
                    should_stop=should_stop,
                    read_only=True,
                    existing_info=entry["info"],
                )
            except HashingCancelled:
                break
            except Exception as exc:  # noqa: BLE001 - return per-file errors without killing the whole job
                info = entry["info"]
                item = {
                    "modelPath": str(entry["path"]),
                    "modelType": entry["modelType"],
                    "metadataOnly": True,
                    "modelId": str(info.get("modelId") or "") if isinstance(info, dict) else "",
                    "status": "error",
                    "error": str(exc),
                    "hasUpdate": False,
                    "previewUrl": "",
                    "previewType": "image",
                    "nsfw": False,
                    "lastCheckedAt": _utc_now(),
                }
            # A lookup cut short by Stop can look like "not found"; not reported.
            if control and control.is_cancelled():
                break
            item["archived"] = True
            item["filePath"] = str(entry["filePath"])

            if item.get("status") == "ok":
                stats["resolved"] += 1
            if item.get("status") == "not_found":
                stats["notFound"] += 1
            if item.get("status") == "error":
                stats["errors"] += 1
            if item.get("hasUpdate"):
                stats["withUpdates"] += 1

            items.append(item)
            if item_callback:
                item_callback(item)
            progress(index, total, f"Processed {index}/{total}")
            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)

        summary = {
            "mode": "archive",
            "total": total,
            "resolved": stats["resolved"],
            "withUpdates": stats["withUpdates"],
            "notFound": stats["notFound"],
            "errors": stats["errors"],
            "withoutInfo": counts["withoutInfo"],
        }
        return summary, items


def _dedupe_model_files(files: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for entry in files:
        path = str(entry["path"]).lower()
        if path in seen:
            continue
        seen.add(path)
        deduped.append(entry)
    return deduped


def sidecar_seed_item(model_entry: dict) -> dict | None:
    """Build a placeholder check result from a file's .civitai.info alone.

    It carries only the installed version (no remote versions), which is
    enough for grouping to stop offering that release as new while the file
    waits to be re-checked. Returns None when the sidecar cannot identify the
    version.
    """
    info = read_json(model_entry["infoPath"])
    if not isinstance(info, dict) or not info.get("modelId") or not info.get("id"):
        return None
    model_info = info.get("model")
    model_nsfw = isinstance(model_info, dict) and bool(model_info.get("nsfw"))
    preview_url, preview_type = _first_preview(info)
    return {
        "modelPath": str(model_entry["path"]),
        "filePath": str(model_entry.get("filePath") or model_entry["path"]),
        "modelType": model_entry.get("modelType", ""),
        "metadataOnly": bool(model_entry.get("metadataOnly")),
        "modelId": str(info.get("modelId")),
        "modelName": _model_name(info),
        "baseModel": info.get("baseModel", ""),
        "status": "ok",
        "hasUpdate": False,
        "localVersionId": str(info.get("id")),
        "localVersionName": info.get("name", ""),
        "localVersionDate": _version_date(info),
        "previewUrl": preview_url,
        "previewType": preview_type,
        "localPreviewUrl": preview_url,
        "localPreviewType": preview_type,
        "remoteVersions": [],
        "nsfw": bool(info.get("nsfw")) or model_nsfw,
    }


def _version_date(version_data: dict) -> str:
    for key in ("publishedAt", "createdAt"):
        value = version_data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _model_name(version_data: dict) -> str:
    model = version_data.get("model")
    if isinstance(model, dict):
        name = model.get("name")
        if isinstance(name, str) and name:
            return name
    return ""


def _first_download_url(version_data: dict) -> str | None:
    direct_url = version_data.get("downloadUrl")
    if isinstance(direct_url, str) and direct_url:
        return direct_url

    files = version_data.get("files") or []
    for file_info in files:
        url = file_info.get("downloadUrl")
        if isinstance(url, str) and url:
            return url
    return None


def _first_preview(version_data: dict) -> tuple[str, str]:
    """Return (url, media_type) preferring images, falling back to video."""
    images = version_data.get("images") or []
    for preferred in ("image", "video"):
        for entry in images:
            if not isinstance(entry, dict) or entry.get("type") != preferred:
                continue
            url = entry.get("url")
            if isinstance(url, str) and url:
                return url, preferred
    return "", "image"


def _normalize_remote_versions(
    client: CivitaiClient,
    model_id: int | str,
    model_versions: list[dict] | None,
) -> list[dict]:
    normalized = []
    for version_data in model_versions or []:
        version_id = str(version_data.get("id") or "")
        version_date = _version_date(version_data)
        if not version_id or not version_date:
            continue
        preview_url, preview_type = _first_preview(version_data)
        normalized.append(
            {
                "versionId": version_id,
                "versionName": version_data.get("name", ""),
                "versionDate": version_date,
                "baseModel": version_data.get("baseModel", ""),
                "previewUrl": preview_url,
                "previewType": preview_type,
                "versionUrl": client.version_page_url(model_id, version_id),
                "downloadUrl": _first_download_url(version_data) or "",
                "availability": _version_availability(version_data),
                **_paid_access(version_data),
            }
        )
    normalized.sort(
        key=lambda version: (version.get("versionDate", ""), version.get("versionName", "").lower()),
        reverse=True,
    )
    return normalized


def _paid_access(version_data: dict) -> dict:
    """Whether downloading a release costs Buzz, and until when.

    ``paid`` is "permanent" (no free date), "early" (early access: free from
    ``paidUntil``, which can be empty when Civitai gives no end), or "".
    Civitai's public API names no price.
    """
    access = version_data.get("paidAccess")
    if isinstance(access, dict):
        if access.get("permanent"):
            return {"paid": "permanent", "paidUntil": ""}
        ends_at = access.get("endsAt") or version_data.get("earlyAccessDeadline")
        return {"paid": "early", "paidUntil": ends_at if isinstance(ends_at, str) else ""}
    # Releases fetched before paidAccess existed flag early access this way.
    if version_data.get("availability") == "EarlyAccess":
        deadline = version_data.get("earlyAccessDeadline")
        return {"paid": "early", "paidUntil": deadline if isinstance(deadline, str) else ""}
    return {"paid": "", "paidUntil": ""}


def _version_availability(version_data: dict) -> str:
    availability = version_data.get("availability")
    return availability if isinstance(availability, str) else ""


# Moved to sidecar.py; the old names stay importable from here.
_sidecar_identifies_model = sidecar_identifies_model
_stored_sha256 = stored_sha256


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _replaced(stored_hash: str, local_hash: str | None) -> bool:
    """True when the hash just computed differs from the one the info file records."""
    return bool(stored_hash and local_hash and local_hash.strip().lower() != stored_hash)


def _fallback_record(info: dict | None) -> dict:
    """The info file's ``extensions.fallback`` (which other source filled it in), or {}."""
    extensions = info.get("extensions") if isinstance(info, dict) else None
    record = extensions.get("fallback") if isinstance(extensions, dict) else None
    return record if isinstance(record, dict) else {}


def _fallback_source(info: dict | None) -> str:
    return _text(_fallback_record(info).get("source"))


def _creator_name(info: dict | None) -> str:
    creator = info.get("creator") if isinstance(info, dict) else None
    return _text(creator.get("username")) if isinstance(creator, dict) else ""


def _creator_status(client: CivitaiClient, username: str) -> str:
    """For a removed model: "active" if its creator still has models, "gone" if the account lists none,
    "missing" if Civitai has no such account, "" if unknown.

    One request per username per run; an unknown answer is remembered too.
    """
    if not username:
        return ""
    cache = client.creator_status_cache
    if username not in cache:
        cache[username] = client.creator_lookup(username) or ""
    return cache[username]


def _fallback_fields(info: dict, file_name: str) -> dict:
    """A No match card's fields from an info file another source filled in."""
    model = info.get("model")
    version_name = _text(info.get("name"))
    preview_url, preview_type = _first_preview(info)
    return {
        "modelName": _model_name(info),
        "creatorName": _creator_name(info),
        "baseModel": _text(info.get("baseModel")),
        # The stub's placeholder name is the file's own name, not a version.
        "localVersionName": "" if version_name == file_name else version_name,
        "nsfw": isinstance(model, dict) and bool(model.get("nsfw")),
        "fallbackSource": _fallback_source(info),
        "localPreviewUrl": preview_url,
        "localPreviewType": preview_type,
    }


# The progress line while each fallback source is asked about a file.
_FALLBACK_PROGRESS = {
    "ai-toolkit": "Looking in your ai-toolkit samples",
    "huggingface": "Asking Hugging Face for a preview",
    "civarchive": "Asking civarchive for a preview",
}


def _announce_source(progress: ProgressCallback, current: int, total: int, file_name: str) -> Callable[[str], None]:
    def announce(source: str) -> None:
        what = _FALLBACK_PROGRESS.get(source, "Looking for a preview elsewhere")
        progress(current, total, f"{what}: {file_name}")

    return announce


def _read_base_codes(data_dir: Path) -> dict[str, str]:
    """The renamer's base-model codes (name -> code), read only: its store rewrites the file when built.

    A missing or unreadable file gives {}.
    """
    payload = read_json(Path(data_dir) / BASE_CODES_FILENAME)
    codes = payload.get("codes") if isinstance(payload, dict) else None
    if not isinstance(codes, dict):
        return {}
    return {name: code for name, code in codes.items() if isinstance(name, str) and isinstance(code, str) and code}


class _ScanFallback:
    """One scan's preview fallback, built when the first file needs it.

    A failure is logged and costs only that file its fallback; the scan goes on.
    """

    def __init__(self, config: dict, data_dir: Path, should_stop=None) -> None:
        self._config = config
        self._data_dir = Path(data_dir)
        self._should_stop = should_stop
        self._fallback: PreviewFallback | None = None

    def run(
        self,
        *,
        model_path: Path,
        info_path: Path,
        preview_path: Path,
        local_hash: str,
        refetch: bool,
        on_source: Callable[[str], None],
    ) -> FallbackOutcome | None:
        try:
            if self._fallback is None:
                http = FallbackHttp(
                    timeout_seconds=int(self._config.get("requestTimeoutSeconds", 30)),
                    should_stop=self._should_stop,
                )
                base_codes = _read_base_codes(self._data_dir)
                self._fallback = PreviewFallback(self._config, self._data_dir, http, base_codes)
            return self._fallback.run(
                model_path=model_path,
                info_path=info_path,
                preview_path=preview_path,
                sha256=local_hash or stored_sha256(read_json(info_path)),
                refetch=refetch,
                on_source=on_source,
            )
        except Exception as exc:  # noqa: BLE001 - a fallback failure must not end the scan
            print(f"Civitai updater: could not look for a preview elsewhere for {model_path.name} ({exc})")
            return None


def _set_sha256_hash(version_data: dict, sha256_hash: str) -> None:
    files = version_data.get("files")
    if not isinstance(files, list) or not files:
        return
    first_file = files[0]
    if not isinstance(first_file, dict):
        return
    hashes = first_file.get("hashes")
    if not isinstance(hashes, dict):
        hashes = {}
    hashes["SHA256"] = sha256_hash
    first_file["hashes"] = hashes


def _download_preview_if_needed(
    client: CivitaiClient,
    preview_path: Path,
    version_data: dict,
    force: bool = False,
) -> None:
    preview_url, preview_type = _first_preview(version_data)
    if not preview_url:
        return
    if not force and preview_path.exists():
        return
    _download_preview_media(client, preview_path, preview_url, preview_type)


def _download_preview_media(
    client: CivitaiClient,
    preview_path: Path,
    preview_url: str,
    preview_type: str,
) -> None:
    if preview_type == "video":
        client.download_video_first_frame_as_png(preview_url, preview_path)
        return
    client.download_image_as_png(preview_url, preview_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
