from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict
import functools
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional

from aiohttp import web

from .civitai_client import CivitaiClient
from .comfy_queue import (
    ComfyTextGenerator,
    NamingModelError,
    comfy_base_url,
    has_text_generation,
    list_naming_models,
)
from .constants import CACHE_SCHEMA_VERSION, INFO_SIDECAR_SUFFIX, SUPPORTED_MODEL_TYPES, USER_AGENT
from .download_queue import MODEL_DOWNLOAD_JOB, DownloadQueue, DownloadRequest, run_queue
from .jobs import MATURE_MODES, RESULT_MODES, _normalize_cached_item_urls
from .model_actions import (
    EARLY_ACCESS_ERROR,
    INFO_GONE_ERROR,
    DownloadContext,
    download_one,
    release_is_paid,
)
from .model_files import lora_roots, missing_info
from .naming_ai import SYSTEM_PROMPT
from .naming_check import base_named_by, base_vocabulary, name_check_flags, needs_review, users_own_words, xl_is_base
from .naming_scheme import NameParts, assemble, format_part, parse_stem
from .path_resolver import custom_path_problem, normalize_model_types, resolve_model_roots
from .rename_apply import (
    apply_steps,
    plan,
    recover,
    refresh_sidecar_title,
    rewrite_paths,
    send2trash_available,
    send_to_trash,
    update_saved_paths,
)
from .renamer import (
    RENAME_TYPES,
    _changed,
    _disk_stems,
    _mark_version_clashes,
    _names_from_files,
    _parts_flags,
    run_dry_run,
    title_clash_models,
)
from .rename_folders import plan_moves, recheck_folder_flags
from .rename_store import RenameLog
from .request_guard import request_rejection
from .sidecar import preview_path_for, read_data_file, write_json
from .updater_service import sidecar_seed_item

try:
    from server import PromptServer
except ModuleNotFoundError:  # pragma: no cover - only outside ComfyUI
    PromptServer = None


_ROUTES_REGISTERED = False


def _guarded(handler):
    """Refuse requests that did not come from the updater panel (see request_guard)."""

    @functools.wraps(handler)
    async def wrapper(request):
        reason = request_rejection(request.method, request.headers, request.host)
        if reason:
            return web.json_response({"error": reason}, status=403)
        return await handler(request)

    return wrapper


def register_routes(
    config_store,
    updater_service,
    job_manager,
    archive_store,
    *,
    decisions=None,
    base_codes=None,
    answer_cache=None,
    journal=None,
    dry_runs=None,
) -> None:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    if PromptServer is None or not getattr(PromptServer, "instance", None):
        print("Civitai updater: PromptServer is not available, routes were not registered.")
        return

    routes = PromptServer.instance.routes

    @routes.get("/warp-studio/updater/config")
    @_guarded
    async def get_config(request):  # noqa: ARG001
        config = config_store.get_public()
        return web.json_response(
            {
                "config": config,
                "supportedModelTypes": list(SUPPORTED_MODEL_TYPES),
                "effectiveRoots": updater_service.get_effective_roots(),
            }
        )

    @routes.post("/warp-studio/updater/config")
    @_guarded
    async def save_config(request):
        payload = await _read_json(request)
        incoming = _normalize_config_payload(payload)
        # Refused folders are left out and reported; the rest of the
        # settings (API key, other paths) are still saved.
        path_warnings = _drop_refused_custom_paths(
            incoming, config_store.get().get("customPaths")
        )
        updated = config_store.update(incoming)
        public = dict(updated)
        public["apiKey"] = ""
        public["hasApiKey"] = bool(updated.get("apiKey", "").strip())
        return web.json_response(
            {
                "config": public,
                "effectiveRoots": updater_service.get_effective_roots(),
                "pathWarnings": path_warnings,
            }
        )

    @routes.post("/warp-studio/updater/jobs/scan")
    @_guarded
    async def start_scan_job(request):
        payload = await _read_json(request)
        payload = _normalize_job_payload(payload)
        job = job_manager.start(
            "scan",
            lambda progress, item, control: updater_service.run_scan(payload, progress, item, control),
        )
        if not job:
            return web.json_response({"error": "A job is already running."}, status=409)
        return web.json_response({"jobId": job.id})

    @routes.post("/warp-studio/updater/jobs/check-updates")
    @_guarded
    async def start_check_updates_job(request):
        payload = await _read_json(request)
        payload = _normalize_job_payload(payload)

        progress_path = config_store.data_dir / "progress.json"
        cache_path = config_store.data_dir / "last_check.json"
        item_count = 0
        # The runner thread can emit items before job_manager.start() returns,
        # so it reads the job record from this holder instead of a closure
        # variable that may not be assigned yet.
        job_holder: dict = {}

        # Seed the job with the previous check's results so the panel keeps
        # showing them (marked provisional) while models are re-checked. Every
        # cached item is carried, not just the requested types: a check of one
        # type must not make the other types vanish from the panel.
        cache_data = await asyncio.to_thread(read_data_file, cache_path)
        seed_items = _seed_items_from_cache(cache_data)
        seeded_paths = {str(item.get("modelPath") or "").lower() for item in seed_items}

        def runner(progress, item_cb, control):
            nonlocal item_count

            def sync_seeds(files):
                # Without a previous check there is nothing stale to correct.
                if not seed_items:
                    return
                current_paths = {str(entry["path"]).lower() for entry in files}
                job_manager.sync_seeds_with_disk(
                    control.job_id,
                    payload["modelTypes"],
                    current_paths,
                    _seed_items_for_new_files(files, seeded_paths),
                )

            def item_cb_with_progress(item):
                nonlocal item_count
                item_cb(item)
                item_count += 1
                job_ref = job_holder.get("job")
                if job_ref is not None and item_count % 5 == 0:
                    _write_progress(progress_path, job_ref)

            summary, items = updater_service.run_check_updates(
                payload, progress, item_cb_with_progress, control, files_callback=sync_seeds,
            )
            # A check of a subset of types replaces only that subset in the
            # cache; the previous results for the other types are kept, so
            # the panel and the file-change detection still cover the whole
            # library.
            items = _merge_check_items(cache_data, items, payload.get("modelTypes"))
            summary = job_manager.summarize_check_items(summary, items)
            summary["total"] = len(items)
            if not control.is_cancelled():
                write_json(cache_path, {
                    "schemaVersion": CACHE_SCHEMA_VERSION,
                    "checkedAt": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                    "items": items,
                })
            progress_path.unlink(missing_ok=True)
            return summary, items

        job_ref = job_manager.start("check-updates", runner, seed_items=seed_items)
        if not job_ref:
            return web.json_response({"error": "A job is already running."}, status=409)
        job_holder["job"] = job_ref
        _write_progress(progress_path, job_ref)
        return web.json_response({"jobId": job_ref.id})

    @routes.get("/warp-studio/updater/last-check")
    @_guarded
    async def get_last_check(request):  # noqa: ARG001
        progress_path = config_store.data_dir / "progress.json"
        progress_data = read_data_file(progress_path)

        active = job_manager.get_active()
        if progress_data and active:
            return web.json_response({
                "sidecarWarnings": active.summary.get("sidecarWarnings", []),
                "data": {
                    "jobId": active.id,
                    "checkedAt": active.startedAt or "",
                    "summary": active.summary,
                    "itemCount": len(active.items),
                    "inProgress": True,
                }
            })

        if progress_data and not active:
            progress_path.unlink(missing_ok=True)

        cache_path = config_store.data_dir / "last_check.json"
        data = await asyncio.to_thread(read_data_file, cache_path)
        # The inspection walks every model root on disk — keep it off the
        # server event loop so large libraries don't stall other requests.
        current_paths, sidecar_warnings = await asyncio.to_thread(
            updater_service.inspect_current_files
        )
        if not data:
            return web.json_response({"data": None, "sidecarWarnings": sidecar_warnings})
        if int(data.get("schemaVersion") or 0) != CACHE_SCHEMA_VERSION:
            return web.json_response(
                {"data": None, "cacheInvalid": True, "sidecarWarnings": sidecar_warnings}
            )
        job = job_manager.load_cached_check(data)

        cached_paths = {str(item.get("modelPath", "")).lower() for item in data.get("items", []) if item.get("modelPath")}
        added = len(current_paths - cached_paths)
        removed = len(cached_paths - current_paths)

        return web.json_response({
            "sidecarWarnings": sidecar_warnings,
            "data": {
                "jobId": job.id,
                "checkedAt": data.get("checkedAt", ""),
                "summary": data.get("summary", {}),
                "itemCount": len(data.get("items", [])),
                "inProgress": False,
                "filesChanged": added + removed > 0,
                "filesAdded": added,
                "filesRemoved": removed,
            }
        })

    @routes.get("/warp-studio/updater/jobs/active")
    @_guarded
    async def get_active_job(request):  # noqa: ARG001
        active = job_manager.get_active()
        if not active:
            return web.json_response({"job": None})
        return web.json_response({"job": active.as_dict(include_items=False)})

    @routes.get("/warp-studio/updater/jobs/{job_id}")
    @_guarded
    async def get_job(request):
        job_id = request.match_info.get("job_id", "")
        job = job_manager.get(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        include_items = request.query.get("includeItems", "0").lower() in ("1", "true", "yes")
        return web.json_response(job.as_dict(include_items=include_items))

    @routes.get("/warp-studio/updater/jobs/{job_id}/items")
    @_guarded
    async def get_job_items(request):
        job_id = request.match_info.get("job_id", "")
        offset = _read_int_query(request, "offset", default=0, minimum=0, maximum=10_000_000)
        limit = _read_int_query(request, "limit", default=25, minimum=1, maximum=500)
        mode = request.query.get("mode", "").strip().lower() or None
        if mode is not None and mode not in RESULT_MODES:
            return web.json_response({"error": "invalid mode"}, status=400)
        model_types = _read_multi_query(request, "modelType")
        base_models = _read_multi_query(request, "baseModel")
        show_hidden = request.query.get("showHidden", "0").lower() in ("1", "true", "yes")
        sort = request.query.get("sort", "").strip().lower() or None
        group_by = request.query.get("groupBy", "").strip() or None
        then_by = request.query.get("thenBy", "").strip() or None
        collapsed = _read_multi_query(request, "collapsed")
        mature = request.query.get("mature", "").strip().lower()
        if mature not in MATURE_MODES:
            mature = str(config_store.get().get("matureMode") or "show")

        result = job_manager.get_items(
            job_id, offset=offset, limit=limit, mode=mode,
            model_types=model_types, base_models=base_models, sort=sort, show_hidden=show_hidden,
            group_by=group_by, then_by=then_by, mature=mature, collapsed=collapsed,
        )
        if not result:
            return web.json_response({"error": "job not found"}, status=404)

        return web.json_response(
            {
                "jobId": job_id,
                "totalItems": result["total"],
                "offset": result["offset"],
                "limit": result["limit"],
                "mode": mode,
                "facets": result["facets"],
                "groups": result["groups"],
                "grouping": result["grouping"],
                "startsMidPrimary": result["startsMidPrimary"],
                "startsMidSecondary": result["startsMidSecondary"],
                "matureHidden": result["matureHidden"],
                "matureMode": result["matureMode"],
                "modeCounts": result["modeCounts"],
                "items": result["items"],
            }
        )

    @routes.post("/warp-studio/updater/archived-updates")
    @_guarded
    async def archive_updates(request):
        payload = await _read_json(request)
        model_id, version_ids = _normalize_archive_payload(payload)
        if not model_id or not version_ids:
            return web.json_response({"error": "modelId and versionIds are required"}, status=400)
        archived = archive_store.archive(model_id, version_ids)
        return web.json_response({"modelId": model_id, "archivedVersionIds": archived})

    @routes.post("/warp-studio/updater/archived-updates/restore")
    @_guarded
    async def restore_updates(request):
        payload = await _read_json(request)
        model_id, version_ids = _normalize_archive_payload(payload)
        if not model_id or not version_ids:
            return web.json_response({"error": "modelId and versionIds are required"}, status=400)
        archived = archive_store.restore(model_id, version_ids)
        return web.json_response({"modelId": model_id, "archivedVersionIds": archived})

    @routes.post("/warp-studio/updater/jobs/{job_id}/pause")
    @_guarded
    async def pause_job(request):
        job_id = request.match_info.get("job_id", "")
        existing = job_manager.get(job_id)
        if _unpausable(existing):
            # A paused stream times out on the server's side and the download is lost.
            return web.json_response({"error": _PAUSE_ERRORS[existing.type]}, status=409)
        job = job_manager.pause(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.as_dict(include_items=False))

    @routes.post("/warp-studio/updater/jobs/{job_id}/resume")
    @_guarded
    async def resume_job(request):
        job_id = request.match_info.get("job_id", "")
        job = job_manager.resume(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.as_dict(include_items=False))

    @routes.post("/warp-studio/updater/jobs/{job_id}/stop")
    @_guarded
    async def stop_job(request):
        job_id = request.match_info.get("job_id", "")
        job = job_manager.cancel(job_id)
        if not job:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(job.as_dict(include_items=False))

    if None not in (decisions, base_codes, answer_cache, journal):
        _register_rename_routes(
            routes, config_store, job_manager, decisions, base_codes, answer_cache, journal, dry_runs
        )
        _register_model_action_routes(
            routes, config_store, job_manager, decisions, base_codes, answer_cache, journal
        )
    else:
        print(
            "Civitai updater: the renamer's stores are missing, so its routes and the"
            " download routes were not registered."
        )

    _ROUTES_REGISTERED = True
    print("Civitai updater: routes registered")


async def _read_json(request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _normalize_job_payload(payload: dict) -> dict:
    model_types = normalize_model_types(payload.get("modelTypes"))
    return {
        "modelTypes": model_types,
        "includeCustomPaths": bool(payload.get("includeCustomPaths", True)),
        "refetchMetadata": bool(payload.get("refetchMetadata", False)),
        "forceRehash": bool(payload.get("forceRehash", False)),
    }


def _normalize_config_payload(payload: dict) -> dict:
    incoming = {}

    if "apiKey" in payload:
        key = payload.get("apiKey")
        incoming["apiKey"] = key if isinstance(key, str) else ""

    if "cacheTtlMinutes" in payload:
        incoming["cacheTtlMinutes"] = payload.get("cacheTtlMinutes")
    if "requestTimeoutSeconds" in payload:
        incoming["requestTimeoutSeconds"] = payload.get("requestTimeoutSeconds")
    if "maxRetries" in payload:
        incoming["maxRetries"] = payload.get("maxRetries")
    if "requestDelayMs" in payload:
        incoming["requestDelayMs"] = payload.get("requestDelayMs")
    if "useComfyPaths" in payload:
        incoming["useComfyPaths"] = bool(payload.get("useComfyPaths"))
    if "useExtraModelPaths" in payload:
        incoming["useExtraModelPaths"] = bool(payload.get("useExtraModelPaths"))
    if "useCustomPaths" in payload:
        incoming["useCustomPaths"] = bool(payload.get("useCustomPaths"))
    if "treatSidecarsAsInstalled" in payload:
        incoming["treatSidecarsAsInstalled"] = bool(payload.get("treatSidecarsAsInstalled"))
    if "matureMode" in payload:
        incoming["matureMode"] = payload.get("matureMode")
    if "namingModel" in payload:
        naming_model = payload.get("namingModel")
        incoming["namingModel"] = naming_model.strip() if isinstance(naming_model, str) else ""

    # Preview fallback settings; ConfigStore validates and trims them.
    if "fallbackCivarchive" in payload:
        incoming["fallbackCivarchive"] = bool(payload.get("fallbackCivarchive"))
    for key in ("fallbackHuggingFace", "fallbackAiToolkitFolders"):
        if key in payload:
            incoming[key] = _normalize_paths_value(payload.get(key))
    if "fallbackOwnCreator" in payload:
        creator = payload.get("fallbackOwnCreator")
        incoming["fallbackOwnCreator"] = creator if isinstance(creator, str) else ""

    if "customPaths" in payload:
        custom = payload.get("customPaths")
        if isinstance(custom, dict):
            # Only normalize the model types the client actually sent —
            # filling in missing types as [] would silently wipe their
            # stored custom paths on every settings sync.
            cleaned = {}
            for model_type in SUPPORTED_MODEL_TYPES:
                if model_type in custom:
                    cleaned[model_type] = _normalize_paths_value(custom[model_type])
            if cleaned:
                incoming["customPaths"] = cleaned

    return incoming


def _normalize_paths_value(entries) -> list[str]:
    if isinstance(entries, str):
        raw_parts = entries.replace(";", "\n").splitlines()
        return [item.strip() for item in raw_parts if item.strip()]
    if isinstance(entries, list):
        return [item.strip() for item in entries if isinstance(item, str) and item.strip()]
    return []


def _drop_refused_custom_paths(incoming: dict, stored_paths=None) -> list[str]:
    """Remove newly added custom paths that may not become scan roots.

    Paths already in the stored config are kept as they are: the panel sends
    the whole list on every settings sync, and a folder that went missing (an
    unplugged drive) must not be dropped from it. Scans skip those anyway.
    Returns a message for each removed path.
    """
    custom_paths = incoming.get("customPaths")
    if not isinstance(custom_paths, dict):
        return []
    known = set()
    if isinstance(stored_paths, dict):
        for entries in stored_paths.values():
            if isinstance(entries, list):
                known.update(str(entry).strip().lower() for entry in entries)
    warnings = []
    for model_type, entries in custom_paths.items():
        kept = []
        for entry in entries:
            problem = None if entry.strip().lower() in known else custom_path_problem(entry)
            if problem:
                warnings.append(f"Custom {model_type} folder {entry} was not saved: {problem}")
            else:
                kept.append(entry)
        custom_paths[model_type] = kept
    return warnings


def _cached_check_items(cache_data) -> list[dict]:
    """Return the items of a cached check, or nothing for a missing or outdated cache."""
    if not isinstance(cache_data, dict):
        return []
    if int(cache_data.get("schemaVersion") or 0) != CACHE_SCHEMA_VERSION:
        return []
    return [item for item in cache_data.get("items", []) or [] if isinstance(item, dict)]


def _seed_items_from_cache(cache_data) -> list[dict]:
    """Build provisional seed items from a cached check for a new job.

    Each item is marked ``_seeded`` so the job can replace it once the file is
    re-checked. An empty list is returned for missing or outdated caches.
    """
    seeded: list[dict] = []
    for item in _cached_check_items(cache_data):
        entry = dict(_normalize_cached_item_urls(item))
        entry["_seeded"] = True
        seeded.append(entry)
    return seeded


def _merge_check_items(cache_data, fresh_items: list[dict], model_types: list[str] | None) -> list[dict]:
    """Combine a check of ``model_types`` with the cached results of the rest.

    Items of a checked type come only from the fresh run, so files that were
    deleted drop out; items of any other type are carried over unchanged.
    """
    checked_types = set(model_types or [])
    if not checked_types:
        return list(fresh_items)
    carried = [
        _normalize_cached_item_urls(item)
        for item in _cached_check_items(cache_data)
        if item.get("modelType") not in checked_types
    ]
    return carried + list(fresh_items)


def _seed_items_for_new_files(files: list[dict], known_paths: set[str]) -> list[dict]:
    """Seed items for files the previous check never saw.

    Built from each file's .civitai.info so the provisional view already knows
    which versions are installed; files without a usable sidecar get no seed
    and simply appear once they are checked.
    """
    seeded: list[dict] = []
    for entry in files:
        if str(entry["path"]).lower() in known_paths:
            continue
        seed = sidecar_seed_item(entry)
        if seed:
            seed["_seeded"] = True
            seeded.append(seed)
    return seeded


def _write_progress(progress_path, job_ref) -> None:
    try:
        write_json(progress_path, {
            "jobId": job_ref.id,
            "startedAt": job_ref.startedAt or "",
            "progress": job_ref.progress,
            "total": job_ref.total,
        })
    except Exception:  # noqa: BLE001
        pass


def _read_int_query(request, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.query.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _read_multi_query(request, key: str) -> list[str] | None:
    values = [value.strip() for value in request.query.getall(key, []) if value and value.strip()]
    return values if values else None


def _normalize_archive_payload(payload: dict) -> tuple[str, list[str]]:
    model_id = str(payload.get("modelId") or "").strip()
    raw_version_ids = payload.get("versionIds")
    version_ids = []
    if isinstance(raw_version_ids, list):
        normalized = set()
        for version_id in raw_version_ids:
            if version_id is None:
                continue
            cleaned = str(version_id).strip()
            if cleaned:
                normalized.add(cleaned)
        version_ids = sorted(normalized)
    return model_id, version_ids


# --- the model renamer ---------------------------------------------------------

RENAME_DRY_RUN_JOB = "rename-dry-run"
RENAME_APPLY_JOB = "rename-apply"
NAMING_MODEL_DOWNLOAD_JOB = "naming-model-download"
NAMING_MODEL_URL = (
    "https://huggingface.co/Comfy-Org/Qwen3.5/resolve/main/text_encoders/qwen3.5_9b_int8_convrot.safetensors"
)
NAMING_MODEL_FILENAME = "qwen3.5_9b_int8_convrot.safetensors"
RENAME_REASON_NO_TEXTGEN = "This ComfyUI has no Generate Text node. Update ComfyUI to use the renamer."
RENAME_REASON_NO_MODEL = "Choose a naming model in the settings, or download one."
JOURNAL_PENDING_ERROR = "An earlier rename was interrupted. Finish or undo it first."
STALE_DRY_RUN_ERROR = "These rows were already applied. Run a new dry run."
DRY_RUN_ACTIVE_ERROR = "Wait for the dry run to finish."
JOB_RUNNING_ERROR = "A job is already running."
RECOVERY_RUNNING_ERROR = "The interrupted rename is already being finished or undone."
CHECK_JOB = "check-updates"
DOWNLOAD_PAUSE_ERROR = "The naming model download cannot be paused. Stop it to cancel; it then starts over."
ERROR_TEXT_LIMIT = 300
_NAMED_STATUSES = ("change", "unchanged")
_MB = 1024 * 1024


def _register_rename_routes(
    routes, config_store, job_manager, decisions, base_codes, answer_cache, journal, dry_runs=None
) -> None:
    # Dry runs made before the last apply or recovery: their old stems may now name other files.
    applied_jobs: set = set()
    recovery_slot = ExclusiveSlot()
    rename_log = RenameLog(config_store.data_dir)
    if dry_runs is not None:
        restore_last_dry_run(job_manager, dry_runs, applied_jobs)

    async def persist_stale() -> Optional[str]:
        """Files are moving: the saved dry run stays read-only after a restart too.

        Returns a warning for the panel when that could not be saved (see DryRunStore.mark_applied).
        """
        if dry_runs is None:
            return None
        return await asyncio.to_thread(dry_runs.mark_applied)

    def busy():
        return web.json_response({"error": JOB_RUNNING_ERROR}, status=409)

    def refused(problem):
        status, message = problem
        return web.json_response({"error": message}, status=status)

    def journal_pending():
        return web.json_response(
            {"error": JOURNAL_PENDING_ERROR, "journal": _journal_status(journal)}, status=409
        )

    def dry_run_job(job_id: str):
        job = job_manager.get(job_id) if job_id else None
        return job if job is not None and job.type == RENAME_DRY_RUN_JOB else None

    @routes.get("/warp-studio/updater/rename/status")
    @_guarded
    async def get_rename_status(request):  # noqa: ARG001
        # Reads every text encoder's header: off the event loop.
        models = await asyncio.to_thread(list_naming_models)
        status = rename_status(config_store.get(), has_text_generation(), models, journal, base_codes=base_codes)
        # The dry run saved across restarts, for a panel that has no dry run of its own to show.
        status["lastDryRun"] = dry_runs.job_id() if dry_runs is not None else None
        return web.json_response(status)

    @routes.post("/warp-studio/updater/jobs/rename-dry-run")
    @_guarded
    async def start_rename_dry_run(request):  # noqa: ARG001
        if job_manager.get_active():
            return busy()
        if journal.pending() is not None:
            return journal_pending()
        config = config_store.get()
        models = await asyncio.to_thread(list_naming_models)
        status = rename_status(config, has_text_generation(), models, journal)
        if not status["available"]:
            return web.json_response({"error": status["reason"]}, status=409)
        naming_model = _matching_model(config.get("namingModel", ""), models)

        def runner(progress, emit, control):
            roots = resolve_model_roots(config, list(RENAME_TYPES), include_custom_paths=True)
            client = CivitaiClient(
                api_key=config.get("apiKey", ""),
                timeout_seconds=int(config.get("requestTimeoutSeconds", 30)),
                max_retries=int(config.get("maxRetries", 4)),
                should_stop=control.is_cancelled,
            )
            # Blocks on ComfyUI's own server: only ever called on this job thread.
            generator = ComfyTextGenerator(
                comfy_base_url(), naming_model, SYSTEM_PROMPT, should_stop=control.is_cancelled
            )
            try:
                return run_dry_run(
                    roots,
                    client,
                    generator,
                    decisions=decisions,
                    base_codes=base_codes,
                    cache=answer_cache,
                    clip_name=naming_model,
                    progress=progress,
                    emit=emit,
                    control=control,
                )
            except NamingModelError as exc:
                # ComfyUI's rejection lists every file it has; the panel gets the start.
                raise RuntimeError(_short_error(str(exc))) from exc

        on_complete = save_finished_dry_run(dry_runs) if dry_runs is not None else None
        job = job_manager.start(RENAME_DRY_RUN_JOB, runner, on_complete=on_complete)
        if not job:
            return busy()
        return web.json_response({"jobId": job.id})

    @routes.get("/warp-studio/updater/rename/rows")
    @_guarded
    async def get_rename_rows(request):
        job = dry_run_job(request.query.get("jobId", "").strip())
        if job is None:
            return web.json_response({"error": "job not found"}, status=404)
        return web.json_response(rename_rows_payload(job, applied_jobs))

    @routes.post("/warp-studio/updater/rename/decision")
    @_guarded
    async def save_rename_decision(request):
        payload = _normalize_decision_payload(await _read_json(request))
        model_id = payload.get("modelId", "")
        if not payload.get("jobId") or not model_id:
            return web.json_response({"error": "jobId and modelId of a dry run are required"}, status=400)
        job = job_manager.get(payload["jobId"])
        # Any active job refuses: the dry run is still changing its rows, an apply is moving files.
        problem = rename_job_problem(job, active_job=job_manager.get_active(), applied=applied_jobs)
        if problem:
            return refused(problem)
        rows = job.items
        model_rows = [row for row in rows if str(row.get("modelId")) == model_id]
        if not model_rows:
            return web.json_response({"error": "no rows of that model in this dry run"}, status=404)
        problem_text = _decision_problem(payload, model_rows)
        if problem_text:
            return web.json_response({"error": problem_text}, status=400)

        asked_again = payload.get("askAgain") is True
        if asked_again:
            decisions.ask_again(
                model_id,
                sorted({str(row.get("versionId")) for row in model_rows}),
                sorted({str(row.get("fileKey")) for row in model_rows}),
            )
            answer_cache.drop_model(model_id)
        else:
            if "title" in payload or "creator" in payload:
                decisions.set_model(model_id, title=payload.get("title"), creator=payload.get("creator"))
            if "version" in payload:
                if payload.get("fileKey"):
                    decisions.set_file(payload["fileKey"], version=payload["version"])
                else:
                    decisions.set_version(payload["versionId"], payload["version"])
            if "keep" in payload:
                decisions.set_file(payload["fileKey"], keep=payload["keep"])

        # The folders the model's files are in, and the ones they move into.
        folders = sorted(
            {str(row.get("folder") or "") for row in model_rows}
            | {str(row["newFolder"]) for row in model_rows if row.get("newFolder")}
        )
        listing = await asyncio.to_thread(lambda: {folder.lower(): _disk_stems(Path(folder)) for folder in folders})
        result = reassemble_rows(
            rows, decisions, base_codes, listing, model_id=model_id, asked_again=asked_again
        )
        if dry_runs is not None and dry_runs.job_id() == job.id:
            # Copied here, before the next request can edit the rows; written off the loop.
            write = dry_run_rows_writer(dry_runs, job.id, rows)
            try:
                await asyncio.to_thread(write)
            except OSError as exc:
                print(f"Civitai updater: could not save the dry run's edited rows ({exc}).")
        return web.json_response({"rows": result})

    @routes.post("/warp-studio/updater/rename/base-code")
    @_guarded
    async def save_base_code(request):
        payload = await _read_json(request)
        base_model = payload.get("baseModel")
        code = payload.get("code")
        if not isinstance(base_model, str) or not base_model.strip() or not isinstance(code, str):
            return web.json_response({"error": "baseModel and code are required"}, status=400)
        code = format_part(code)
        problem = _base_code_problem(code)
        if problem:
            return web.json_response({"error": problem}, status=400)
        base_codes.set(base_model, code)
        return web.json_response({"baseCodes": base_codes.all()})

    @routes.post("/warp-studio/updater/jobs/rename-apply")
    @_guarded
    async def start_rename_apply(request):
        payload = _normalize_apply_payload(await _read_json(request))
        job = job_manager.get(payload["jobId"]) if payload["jobId"] else None
        problem = rename_job_problem(job, active_job=job_manager.get_active(), applied=applied_jobs)
        if problem:
            return refused(problem)
        if journal.pending() is not None:
            return journal_pending()
        # A copy: later edits to the job's rows must not reach what this apply locks.
        rows = copy.deepcopy(list(job.items))
        selected = set(payload["keys"])
        remove = set(payload["removeDuplicates"])
        delete = set(payload["delete"])
        data_dir = config_store.data_dir

        def runner(progress, emit, control):  # noqa: ARG001
            summary = run_apply(
                rows, selected, remove, journal, data_dir=data_dir, decisions=decisions, base_codes=base_codes,
                check_jobs=job_manager, progress=progress, control=control,
                log=rename_log, dry_run_job_id=job.id, delete=delete,
            )
            return summary, []

        apply_job = job_manager.start(RENAME_APPLY_JOB, runner)
        if not apply_job:
            return busy()
        # No await since the check above, so no second apply or edit slipped in between.
        # Every dry run held (another tab's too) is stale once files move.
        mark_dry_runs_stale(job_manager, applied_jobs)
        response = {"jobId": apply_job.id}
        warning = await persist_stale()
        if warning:
            response["persistWarning"] = warning
        return web.json_response(response)

    @routes.post("/warp-studio/updater/rename/recover")
    @_guarded
    async def recover_rename(request):
        payload = await _read_json(request)
        action = payload.get("action")
        if action not in ("finish", "undo"):
            return web.json_response({"error": 'action must be "finish" or "undo"'}, status=400)
        if job_manager.get_active():
            return busy()
        # Two tabs must not finish or undo one run at the same time.
        if not recovery_slot.acquire():
            return web.json_response({"error": RECOVERY_RUNNING_ERROR}, status=409)
        try:
            pending = journal.pending()
            mark_dry_runs_stale(job_manager, applied_jobs)
            warning = await persist_stale()
            try:
                result = await asyncio.to_thread(functools.partial(recover, journal, action, log=rename_log))
                mapping = recovered_path_mapping(pending, result, action)
                await asyncio.to_thread(update_saved_paths, config_store.data_dir, mapping)
                update_check_job_paths(job_manager, mapping)
            except (OSError, ValueError) as exc:
                return web.json_response(
                    {"error": _short_error(str(exc)), "journal": _journal_status(journal)}, status=500
                )
        finally:
            recovery_slot.release()
        response = {"done": result["done"], "skipped": result["skipped"], "journal": _journal_status(journal)}
        if warning:
            response["persistWarning"] = warning
        return web.json_response(response)

    @routes.post("/warp-studio/updater/rename/reveal")
    @_guarded
    async def reveal_rename_file(request):
        return await reveal_response(request, dry_run_job)

    @routes.post("/warp-studio/updater/reveal")
    @_guarded
    async def reveal_job_file(request):
        # Any job's results: a checked file in Updates or Issues, or a dry run row.
        return await reveal_response(request, job_manager.get)

    @routes.post("/warp-studio/updater/local-preview")
    @_guarded
    async def local_preview(request):
        # The panel fetches this with the guard header and shows a blob URL.
        return await local_preview_response(request, job_manager.get)

    @routes.post("/warp-studio/updater/jobs/download-naming-model")
    @_guarded
    async def start_naming_model_download(request):  # noqa: ARG001
        if job_manager.get_active():
            return busy()
        models = await asyncio.to_thread(list_naming_models)
        existing = _existing_naming_model(models)
        if existing:
            # Already somewhere in the text encoder folders: no second 9.75 GB download.
            config_store.update({"namingModel": existing})
            return web.json_response({"jobId": None, "namingModel": existing, "reused": True})
        folders = _text_encoder_folders()
        if not folders:
            return web.json_response({"error": "ComfyUI has no text_encoders folder."}, status=409)
        folder = folders[0]
        if os.path.lexists(folder / NAMING_MODEL_FILENAME):
            return web.json_response(
                {"error": f"{NAMING_MODEL_FILENAME} is already in {folder}. Choose it in the settings."},
                status=409,
            )
        if job_manager.get_active():
            return busy()

        def runner(progress, emit, control):  # noqa: ARG001
            name = download_naming_model(folder, progress=progress, control=control)
            if name:
                config_store.update({"namingModel": name})
            return {"namingModel": name, "path": str(folder / name) if name else ""}, []

        job = job_manager.start(NAMING_MODEL_DOWNLOAD_JOB, runner)
        if not job:
            return busy()
        return web.json_response({"jobId": job.id})


# --- model downloads -------------------------------------------------------------

DOWNLOAD_BUSY_ERROR = "Another job is running. Try again when it's done."
MODEL_DOWNLOAD_PAUSE_ERROR = "Downloads cannot be paused. Stop cancels them."
REPLACE_HELD_ERROR = "That version is already being replaced."
DOWNLOAD_SOURCE_ERROR = 'source must be "updates" or "gallery"'
_DOWNLOAD_SOURCES = ("updates", "gallery")
# The download jobs, each refusing a pause in its own words.
_PAUSE_ERRORS = {
    NAMING_MODEL_DOWNLOAD_JOB: DOWNLOAD_PAUSE_ERROR,
    MODEL_DOWNLOAD_JOB: MODEL_DOWNLOAD_PAUSE_ERROR,
}


def _unpausable(job) -> bool:
    """A download job: a paused stream would time out on the server's side."""
    return job is not None and getattr(job, "type", None) in _PAUSE_ERRORS


def _is_model_download(job) -> bool:
    return job is not None and getattr(job, "type", None) == MODEL_DOWNLOAD_JOB


def _normalize_download_payload(payload: dict) -> dict:
    """Known keys only: the source, stripped strings, and Replace only for a literal true."""
    source = _clean_id(payload.get("source"))
    cleaned = {"source": source if source in _DOWNLOAD_SOURCES else ""}
    for key in ("jobId", "modelId", "versionId", "modelPath", "name"):
        cleaned[key] = _clean_id(payload.get(key))
    cleaned["replace"] = payload.get("replace") is True
    return cleaned


def download_problem(active_job, *, journal_pending: bool) -> Optional[tuple]:
    """Why no download may be queued now, as (HTTP status, message), or None.

    Only the download job may run beside a download: that also keeps the
    check results' file (``check_records``) to one writer at a time.
    """
    if active_job is not None and not _is_model_download(active_job):
        # A rename apply holds the journal while it runs: the job is the reason then.
        return 409, DOWNLOAD_BUSY_ERROR
    if journal_pending:
        return 409, JOURNAL_PENDING_ERROR
    return None


def updates_request(payload: dict, job, *, can_trash: bool, queue) -> DownloadRequest | tuple:
    """The request for a release the check results offer, or why not, as (HTTP status, message)."""
    if job is None or getattr(job, "type", None) != CHECK_JOB:
        return 404, "job not found"  # The panel's lost-job recovery reads this text.
    model_id = payload.get("modelId") or ""
    model_path = payload.get("modelPath") or ""
    item = next(
        (
            entry
            for entry in list(job.items)
            if isinstance(entry, dict)
            and model_id
            and model_path
            and str(entry.get("modelId") or "") == model_id
            and isinstance(entry.get("modelPath"), str)
            and entry["modelPath"]
            and _same_path(entry["modelPath"], model_path)
        ),
        None,
    )
    if item is None:
        return 400, "That file is not in these results."
    version_id = payload.get("versionId") or ""
    version = next(
        (
            entry
            for entry in item.get("remoteVersions") or []
            if isinstance(entry, dict)
            and version_id
            and str(entry.get("versionId") or "") == version_id
        ),
        None,
    )
    if version is None:
        return 400, "That release is not one of this model's."
    if release_is_paid(version):
        return 409, EARLY_ACCESS_ERROR
    replace = payload.get("replace") is True
    if replace and item.get("metadataOnly"):
        return 400, "Only a version on this drive can be replaced."
    if replace and not can_trash:
        return 409, "Replace needs the send2trash package."
    if replace and queue.holds(item["modelPath"]):
        return 409, REPLACE_HELD_ERROR
    return DownloadRequest(
        key=f"version:{version_id}",
        source="updates",
        model_id=model_id,
        version_id=version_id,
        model_type=str(item.get("modelType") or ""),
        reference_path=item["modelPath"],
        replace_path=item["modelPath"] if replace else "",
        # A copy: the check job's records change as downloads land.
        check_item=copy.deepcopy(item),
    )


def gallery_request(payload: dict, roots: list) -> DownloadRequest | tuple:
    """The request for a LoRA whose info file has no weights beside it, or why not."""
    name = payload.get("name") or ""
    if not name:
        return 400, "The name of a LoRA is required."
    found = missing_info(roots, name)
    if found is None:
        return 404, INFO_GONE_ERROR
    info_path, info = found
    return DownloadRequest(
        key=f"gallery:{name}",
        source="gallery",
        model_id=str(info.get("modelId")),
        version_id=str(info.get("id")),
        model_type="lora",
        name=name,
        reference_path=str(info_path),
    )


async def submit_download(
    queue, job_manager, request, runner, *, attempts: int = 20, delay: float = 0.05
) -> tuple:
    """Queue the request and start the download job when none drains the queue.

    Returns the request held (one already queued for the same release wins)
    and the download job's id. A job that has drained the queue is still
    active for a moment while it closes, so its successor's start is tried
    again meanwhile, up to ``attempts`` times ``delay`` apart. When no job
    starts (a job of another kind is running), the request is abandoned with
    ``DOWNLOAD_BUSY_ERROR`` and the id is None.
    """
    held, needs_job = queue.submit(request)
    if not needs_job:
        active = job_manager.get_active()
        return held, active.id if _is_model_download(active) else None
    for attempt in range(max(1, attempts)):
        job = job_manager.start(MODEL_DOWNLOAD_JOB, runner)
        if job is not None:
            return held, job.id
        active = job_manager.get_active()
        if active is not None and not _is_model_download(active):
            break
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    queue.abandon(held, DOWNLOAD_BUSY_ERROR)
    return held, None


def download_context(
    config_store, job_manager, decisions, base_codes, answer_cache, journal
) -> DownloadContext:
    """What one download needs, built on the job thread: it reads the naming models' headers."""
    config = config_store.get()
    models = list_naming_models()
    status = rename_status(config, has_text_generation(), models, journal)
    clip = _matching_model(config.get("namingModel", ""), models)

    def make_client(should_stop):
        # A new client for every download: nothing of an earlier lookup is kept,
        # so early access is asked again.
        return CivitaiClient(
            api_key=config.get("apiKey", ""),
            timeout_seconds=int(config.get("requestTimeoutSeconds", 30)),
            max_retries=int(config.get("maxRetries", 4)),
            should_stop=should_stop,
        )

    def make_generator(should_stop):
        # Blocks on ComfyUI's own server: only ever called on the job thread.
        return ComfyTextGenerator(comfy_base_url(), clip, SYSTEM_PROMPT, should_stop=should_stop)

    return DownloadContext(
        config=config,
        data_dir=config_store.data_dir,
        job_manager=job_manager,
        decisions=decisions,
        base_codes=base_codes,
        answer_cache=answer_cache,
        make_client=make_client,
        make_generator=make_generator if status["available"] else None,
        clip_name=clip,
        roots_for=lambda model_type: resolve_model_roots(
            config, [model_type], include_custom_paths=True
        ).get(model_type, []),
        lora_roots=lora_roots,
    )


def _register_model_action_routes(
    routes, config_store, job_manager, decisions, base_codes, answer_cache, journal
) -> DownloadQueue:
    queue = DownloadQueue()

    def work(request, report, should_stop):
        ctx = download_context(
            config_store, job_manager, decisions, base_codes, answer_cache, journal
        )
        download_one(request, report=report, should_stop=should_stop, ctx=ctx)

    def runner(progress, emit, control):  # noqa: ARG001
        return run_queue(queue, work, progress=progress, control=control)

    def refused(problem):
        status, message = problem
        return web.json_response({"error": message}, status=status)

    @routes.post("/warp-studio/updater/downloads")
    @_guarded
    async def start_download(request):
        payload = _normalize_download_payload(await _read_json(request))
        problem = download_problem(
            job_manager.get_active(), journal_pending=journal.pending() is not None
        )
        if problem:
            return refused(problem)
        if payload["source"] == "updates":
            job = job_manager.get(payload["jobId"]) if payload["jobId"] else None
            made = updates_request(payload, job, can_trash=send2trash_available(), queue=queue)
        elif payload["source"] == "gallery":
            # Looks in the LoRA folders: off the event loop. A job of another kind
            # started meanwhile still refuses the download job's start below.
            made = await asyncio.to_thread(lambda: gallery_request(payload, lora_roots()))
        else:
            return web.json_response({"error": DOWNLOAD_SOURCE_ERROR}, status=400)
        if isinstance(made, tuple):
            return refused(made)
        held, job_id = await submit_download(queue, job_manager, made, runner)
        if job_id is None and held.state == "error":
            return web.json_response({"error": held.error}, status=409)
        return web.json_response({"request": held.as_dict(), "jobId": job_id})

    @routes.get("/warp-studio/updater/downloads")
    @_guarded
    async def get_downloads(request):  # noqa: ARG001
        active = job_manager.get_active()
        return web.json_response(
            {
                "jobId": active.id if _is_model_download(active) else None,
                "requests": queue.snapshot(),
                "canTrash": send2trash_available(),
            }
        )

    @routes.post("/warp-studio/updater/downloads/{request_id}/stop")
    @_guarded
    async def stop_download(request):
        held = queue.stop(request.match_info.get("request_id", ""))
        if held is None:
            return web.json_response({"error": "download not found"}, status=404)
        return web.json_response({"request": held.as_dict()})

    return queue


REVEAL_REMOTE_ERROR = "Show in folder works only on the computer ComfyUI runs on."


def is_loopback(remote: Any) -> bool:
    """Whether a request's peer address (aiohttp ``request.remote``) is this machine."""
    if not isinstance(remote, str) or not remote.strip():
        return False
    try:
        address = ipaddress.ip_address(remote.strip().split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped is not None and mapped.is_loopback))


def reveal_command(path: Path, platform: str) -> list:
    """The file manager command that shows ``path`` (an argument list, run without a shell)."""
    if platform.startswith("win"):
        # Explorer reads "/select," and the path as one switch; the path stays its own argument.
        return ["explorer", "/select,", str(path)]
    if platform == "darwin":
        return ["open", "-R", str(path)]
    return ["xdg-open", str(Path(path).parent)]


def _launch_detached(command: list) -> None:
    """Start the file manager and don't wait for it; on Windows, bring its window to the front."""
    before = _explorer_windows() if sys.platform.startswith("win") else None
    subprocess.Popen(  # noqa: S603 - fixed program, path from the job's own results
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if before is not None and len(command) >= 3:
        folder = Path(command[-1]).parent.name
        threading.Thread(target=_focus_explorer, args=(before, folder), daemon=True).start()


def _explorer_windows() -> Optional[dict]:
    """Explorer's open folder windows, {hwnd: title}; None where that can't be read."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: dict = {}
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _param):
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value == "CabinetWClass":
                length = user32.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                found[int(hwnd or 0)] = title.value
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        return found
    except Exception:  # noqa: BLE001 - focusing is a nicety; the folder opens anyway
        return None


def _focus_explorer(before: dict, folder: str, timeout: float = 3.0) -> None:
    """Bring the Explorer window that just opened ``folder`` to the front.

    Windows keeps a window started by a background process (ComfyUI) behind the
    browser; joining the foreground window's input for a moment lets this one
    take the focus. Explorer may also reuse a window it already had open.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        for name in ("SetForegroundWindow", "BringWindowToTop", "IsIconic"):
            getattr(user32, name).argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

        user32.IsWindowVisible.argtypes = [wintypes.HWND]

        # "anima - File Explorer", or "anima and 2 more tabs - File Explorer".
        wanted = folder.lower()

        def names_folder(title: str) -> bool:
            title = title.lower()
            return title == wanted or title.startswith(wanted + " - ") or title.startswith(wanted + " and ")

        # A new window starts as an empty "File Explorer" frame; it can only take
        # the focus once it shows the folder.
        deadline = time.monotonic() + timeout
        target = None
        fallback = None
        while time.monotonic() < deadline:
            time.sleep(0.1)
            now = _explorer_windows() or {}
            fresh = [hwnd for hwnd in now if hwnd not in before]
            named = [hwnd for hwnd in fresh if names_folder(now[hwnd]) and user32.IsWindowVisible(hwnd)]
            if named:
                target = named[0]
                break
            fallback = fallback or (fresh[0] if fresh else None)
        if target is None:
            # No new window: Explorer may have opened the folder in a window it had open.
            now = _explorer_windows() or {}
            target = fallback or next((hwnd for hwnd, title in now.items() if names_folder(title)), None)
        if target is None:
            return
        for _attempt in range(5):
            foreground = user32.GetForegroundWindow()
            if foreground and int(foreground) == int(target):
                return
            other = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
            this = kernel32.GetCurrentThreadId()
            attached = bool(other and other != this and user32.AttachThreadInput(this, other, True))
            try:
                if user32.IsIconic(target):
                    user32.ShowWindow(target, 9)  # SW_RESTORE
                user32.BringWindowToTop(target)
                user32.SetForegroundWindow(target)
            finally:
                if attached:
                    user32.AttachThreadInput(this, other, False)
            time.sleep(0.2)
    except Exception:  # noqa: BLE001 - focusing is a nicety; the folder is open anyway
        return


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _job_file(job, wanted: str) -> Optional[str]:
    """``wanted`` when the job's own results list it (a checked file, a dry run row or its kept copy)."""
    for item in job.items:
        if not isinstance(item, dict):
            continue
        for field in ("modelPath", "survivorPath"):
            value = item.get(field)
            if isinstance(value, str) and value and _same_path(value, wanted):
                return value
    return None


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_LOCAL_PREVIEW_BYTES = 20 * 1024 * 1024


def _read_capped(path: Path, limit: int) -> Optional[bytes]:
    """Up to ``limit`` + 1 bytes of the file, or None when it cannot be read."""
    try:
        with open(path, "rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        return None


async def local_preview_response(request, get_job) -> web.Response:
    """Send the ``.preview.png`` of a file the job's results list, as image bytes.

    The picture is always the one next to the listed file, never the posted
    path itself. Open to any browser that passes the guard, like ComfyUI's own
    model previews.
    """
    payload = await _read_json(request)
    job_id = payload.get("jobId")
    wanted = payload.get("path")
    if not isinstance(job_id, str) or not job_id.strip() or not isinstance(wanted, str) or not wanted.strip():
        return web.json_response({"error": "jobId and the path of a listed file are required"}, status=400)
    job = get_job(job_id.strip())
    if job is None:
        return web.json_response({"error": "job not found"}, status=404)
    listed = _job_file(job, wanted)
    if listed is None:
        return web.json_response({"error": "That file is not in these results."}, status=400)
    data = await asyncio.to_thread(_read_capped, preview_path_for(Path(listed)), _MAX_LOCAL_PREVIEW_BYTES)
    if data is None:
        return web.json_response({"error": "This file has no local preview."}, status=404)
    if len(data) > _MAX_LOCAL_PREVIEW_BYTES:
        return web.json_response({"error": "The local preview is too large to show."}, status=413)
    if not data.startswith(_PNG_SIGNATURE):
        return web.json_response({"error": "The local preview is not a PNG image."}, status=415)
    return web.Response(body=data, content_type="image/png", headers={"Cache-Control": "no-cache"})


async def reveal_response(request, get_job, *, launch=_launch_detached, platform: str = sys.platform):
    """Show a file from a job's results in the file manager.

    The file is named by ``path`` (it must be one the job itself listed) or by a
    dry run row's ``key`` (with ``survivor`` for its kept copy). Only for a
    browser on the machine ComfyUI runs on.
    """
    if not is_loopback(getattr(request, "remote", None)):
        return web.json_response({"error": REVEAL_REMOTE_ERROR}, status=403)
    payload = await _read_json(request)
    job_id = payload.get("jobId")
    key = payload.get("key")
    wanted = payload.get("path")
    by_path = isinstance(wanted, str) and bool(wanted)
    if not isinstance(job_id, str) or not job_id.strip() or not (by_path or (isinstance(key, str) and key)):
        return web.json_response({"error": "jobId and the path or key of a listed file are required"}, status=400)
    job = get_job(job_id.strip())
    if job is None:
        return web.json_response({"error": "job not found"}, status=404)
    if by_path:
        target = _job_file(job, wanted)
        if target is None:
            return web.json_response({"error": "That file is not in these results."}, status=400)
    else:
        row = next((item for item in job.items if isinstance(item, dict) and item.get("key") == key), None)
        if row is None:
            return web.json_response({"error": "That file is not in this dry run."}, status=400)
        if payload.get("survivor") is True:
            target = row.get("survivorPath") if row.get("status") == "duplicate" else None
            if not target:
                return web.json_response({"error": "That row has no kept copy."}, status=400)
        else:
            target = row.get("modelPath")
    if not isinstance(target, str) or not target:
        return web.json_response({"error": "That row has no file."}, status=400)
    path = Path(target)
    if not os.path.lexists(path):
        return web.json_response(
            {"error": f"{path.name} is not there any more. Run a new dry run to see your files as they are now."},
            status=404,
        )
    try:
        launch(reveal_command(path, platform))
    except (OSError, ValueError) as exc:
        return web.json_response({"error": _short_error(f"Could not open the folder: {exc}")}, status=500)
    return web.json_response({"ok": True})


class ExclusiveSlot:
    """One holder at a time. Used only on the event loop, so a flag is enough (no loop-bound lock)."""

    def __init__(self) -> None:
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return False
        self._held = True
        return True

    def release(self) -> None:
        self._held = False


def mark_dry_runs_stale(job_manager, applied: set) -> None:
    """Every dry run held now (up to five finished, any tab's) may name files that are about to move."""
    applied.update(job_manager.job_ids(RENAME_DRY_RUN_JOB))


def restore_last_dry_run(job_manager, dry_runs, applied: set):
    """Bring the dry run saved before a restart back under its id; returns its job or None."""
    saved = dry_runs.load()
    if saved is None:
        return None
    job = job_manager.restore_job(
        RENAME_DRY_RUN_JOB,
        saved["jobId"],
        startedAt=saved["startedAt"],
        finishedAt=saved["finishedAt"],
        summary=saved["summary"],
        items=saved["rows"],
    )
    if saved["applied"]:
        applied.add(job.id)
    return job


def save_finished_dry_run(dry_runs) -> Callable[[dict], None]:
    """The dry run job's on_complete: a completed run replaces the saved one."""

    def save(snapshot: dict) -> None:
        if snapshot.get("status") == "completed":
            dry_runs.save(snapshot)

    return save


def dry_run_rows_writer(dry_runs, job_id: str, rows: list[dict]) -> Callable[[], None]:
    """Copy the rows and take a revision now; the returned call writes them (in a thread)."""
    snapshot = copy.deepcopy(rows)
    revision = dry_runs.next_revision()
    return lambda: dry_runs.update_rows(job_id, snapshot, revision)


def rename_rows_payload(job, applied: set) -> dict:
    return {
        "rows": list(job.items),
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "summary": job.summary,
        "errors": [_short_error(error) for error in job.errors],
        "applied": job.id in applied,
        "finishedAt": job.finishedAt,
        # When it came back from disk after a restart: the finish time, else None.
        "restoredFrom": job.restoredFrom,
    }


def update_check_job_paths(job_manager, mapping: dict) -> None:
    """Point the check results held in memory (cached and this session's) at the new paths."""
    if mapping:
        job_manager.edit_items(CHECK_JOB, lambda items: rewrite_paths(items, mapping))


def run_apply(
    rows: list[dict],
    selected: set,
    remove: set,
    journal,
    *,
    data_dir,
    decisions,
    base_codes,
    progress: Callable[[int, int, str], Any],
    control,
    check_jobs=None,
    trash: Callable[[str], Any] = send_to_trash,
    log=None,
    dry_run_job_id: str = "",
    delete: Any = frozenset(),
) -> dict:
    """The apply job body: plan, apply, then the follow-ups. Returns the job summary."""
    progress(0, 0, "Planning the renames")
    steps, planned_skips = plan(rows, selected, remove, delete)
    result = apply_steps(
        steps, journal, trash=trash, progress=progress, control=control, log=log, dry_run_job_id=dry_run_job_id
    )
    result["skipped"] = planned_skips + list(result.get("skipped") or [])
    return apply_followups(
        rows, steps, result, data_dir=data_dir, decisions=decisions, base_codes=base_codes, check_jobs=check_jobs
    )


def rename_job_problem(job, *, active_job, applied: set) -> Optional[tuple]:
    """Why this dry run's rows may not be edited or applied now, as (HTTP status, message)."""
    if job is None or getattr(job, "type", None) != RENAME_DRY_RUN_JOB:
        return 404, "job not found"
    if job.id in applied:
        return 409, STALE_DRY_RUN_ERROR
    if active_job is not None:
        if getattr(active_job, "id", None) == job.id:
            return 409, DRY_RUN_ACTIVE_ERROR
        return 409, JOB_RUNNING_ERROR
    return None


def _decision_problem(payload: dict, model_rows: list) -> str:
    """A decision's ids must belong to that model's rows in the dry run."""
    if payload.get("askAgain") is True:
        return ""
    file_keys = {str(row.get("fileKey")) for row in model_rows}
    version_ids = {str(row.get("versionId")) for row in model_rows}
    if "fileKey" in payload and payload["fileKey"] not in file_keys:
        return "That fileKey is not one of this model's rows."
    if "versionId" in payload and payload["versionId"] not in version_ids:
        return "That versionId is not one of this model's rows."
    if "version" in payload and not (payload.get("fileKey") or payload.get("versionId")):
        return "A version needs a versionId or fileKey."
    if "keep" in payload and not payload.get("fileKey"):
        return "Keep needs a fileKey."
    return ""


def _existing_naming_model(models: list) -> str:
    """A listed text encoder that already is the downloadable naming model (any subfolder)."""
    for model in models:
        if str(model).replace("\\", "/").rsplit("/", 1)[-1].lower() == NAMING_MODEL_FILENAME:
            return model
    return ""


def _text_encoder_folders() -> list[Path]:
    try:
        import folder_paths  # type: ignore
    except ImportError:
        return []
    try:
        return [Path(path) for path in folder_paths.get_folder_paths("text_encoders")]
    except Exception:  # noqa: BLE001 - an older ComfyUI without the folder key
        return []


def _short_error(text: str, limit: int = ERROR_TEXT_LIMIT) -> str:
    """Error text cut to a readable length; ComfyUI's rejections list every file it has."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _model_key(name: str) -> str:
    return str(name or "").replace("\\", "/").strip().lower()


def _matching_model(name: str, models: list[str]) -> str:
    """The listed spelling of the configured naming model (CLIPLoader checks it exactly)."""
    key = _model_key(name)
    return next((model for model in models if _model_key(model) == key), name)


def _journal_status(journal) -> dict:
    pending = journal.pending()
    if pending is None:
        return {"pending": False, "steps": 0}
    return {"pending": True, "steps": sum(1 for step in pending.get("steps", []) if not step.get("done"))}


def rename_status(config: dict, has_textgen: bool, models: list[str], journal, *, base_codes=None) -> dict:
    """What the Rename view needs before a dry run: can it run, and why not."""
    naming_model = str(config.get("namingModel") or "").strip()
    if not has_textgen:
        reason = RENAME_REASON_NO_TEXTGEN
    elif not naming_model:
        reason = RENAME_REASON_NO_MODEL
    elif _model_key(naming_model) not in {_model_key(model) for model in models}:
        reason = f"The naming model {naming_model} is not in your text encoder folders."
    else:
        reason = ""
    return {
        "available": not reason,
        "reason": reason,
        "namingModels": list(models),
        "namingModel": naming_model,
        "journal": _journal_status(journal),
        "baseCodes": base_codes.all() if base_codes is not None else {},
        "canRemoveDuplicates": send2trash_available(),
    }


def _clean_id(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_decision_payload(payload: dict) -> dict:
    """Known keys only: ids as non-empty strings, name parts as strings, flags as booleans."""
    cleaned: dict = {}
    for key in ("jobId", "modelId", "versionId", "fileKey"):
        value = _clean_id(payload.get(key))
        if value:
            cleaned[key] = value
    for key in ("title", "creator", "version"):
        # "" is a decision too (a version-less name).
        if isinstance(payload.get(key), str):
            cleaned[key] = payload[key]
    for key in ("keep", "askAgain"):
        if isinstance(payload.get(key), bool):
            cleaned[key] = payload[key]
    return cleaned


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return list(dict.fromkeys(items))


def _normalize_apply_payload(payload: dict) -> dict:
    return {
        "jobId": _clean_id(payload.get("jobId")),
        "keys": _string_list(payload.get("keys")),
        "removeDuplicates": _string_list(payload.get("removeDuplicates")),
        "delete": _string_list(payload.get("delete")),
    }


def _base_code_problem(code: str) -> str:
    if not code:
        return "The base code is empty."
    if _parts_flags(NameParts(creator="creator", title="title", base=code)):
        return f'"{code}" cannot be used in a file name.'
    return ""


# Flags that come from the parts themselves and are recomputed on every re-assembly.
_PARTS_FLAG_KINDS = {
    "empty", "forbidden", "separator", "non-ascii", "reserved", "trailing", "base-word", "version-format",
    "type-word", "base-number", "creator-in-title", "title-in-version", "noise-word",
}
_FOLDER_FLAGS = {"exists", "collision"}
# Clashes with the model's other versions, or with another model's title: rechecked on every re-assembly.
_CLASH_FLAGS = {"version-clash", "title-clash"}
# Where the creator came from: dropped once the user decides the creator.
_MAKER_FLAGS = {"maker-from-description", "maker-not-in-description", "creator-from-file-name", "maker-unknown"}
_ASSEMBLED_KEYS = ("newStem", "newFolder", "parts", "changed", "flags", "status")


def reassemble_rows(
    rows: list[dict],
    decisions,
    base_codes,
    folder_listing: dict,
    *,
    model_id: str,
    asked_again: bool = False,
) -> list[dict]:
    """Rebuild one model's rows from their parts and the saved decisions; no AI.

    ``rows`` is the whole dry run (its dicts are updated in place, so the job keeps
    the result); ``folder_listing`` maps each lowercased folder of the model to the
    lowercased stems on disk there. Collisions and existing names are rechecked for
    every row in those folders, version clashes for the model's rows, and title
    clashes for every row. Returns the model's rows plus every other row whose
    name, flags or selection changed (other models' rows in the same folders, or
    of the same creator and title).
    """
    model_id = str(model_id)
    mine = [row for row in rows if str(row.get("modelId")) == model_id]
    folders = {str(row.get("folder") or "").lower() for row in mine}
    folders |= {str(row["newFolder"]).lower() for row in mine if row.get("newFolder")}
    before = {id(row): _row_state(row) for row in rows}
    model_decision = decisions.model(model_id)
    for row in mine:
        status = row.get("status")
        if status in _NAMED_STATUSES or (status == "kept" and row.get("reason") in ("kept-by-you", "dry-run-again")):
            _reassemble_row(row, model_decision, decisions, base_codes)
        if asked_again and row.get("status") in _NAMED_STATUSES and "ask-again" not in row["flags"]:
            # The AI names it afresh on the next dry run.
            row["flags"].append("ask-again")
    _mark_version_clashes(mine)

    def decided(row: dict) -> bool:
        file_version = decisions.file(row.get("fileKey") or "").get("version")
        return bool(set(model_decision) & {"title", "creator"}) or file_version is not None             or decisions.version(row.get("versionId") or "") is not None

    # Versions still alike: the user's own names, as on the dry run.
    _names_from_files(mine, decided)
    # A base that changed (the version's own name, a new code) moves the file as on the dry run.
    plan_moves(rows)
    for row in mine:
        target = str(row.get("newFolder") or "")
        if target:
            folders.add(target.lower())
            if target.lower() not in folder_listing:
                folder_listing[target.lower()] = _disk_stems(Path(target))
    _recheck_title_clashes(rows, decisions)
    _recheck_folders(rows, folders, folder_listing)
    mine_ids = {id(row) for row in mine}
    return [
        row for row in rows
        if id(row) in mine_ids or (id(row) in before and before[id(row)] != _row_state(row))
    ]


def _row_state(row: dict) -> tuple:
    return (row.get("newStem"), row.get("status"), row.get("selected"), tuple(row.get("flags") or ()))


def _reassemble_row(row: dict, model_decision: dict, decisions, base_codes) -> None:
    file_decision = decisions.file(row.get("fileKey") or "")
    old_stem = str(row.get("oldStem") or "")
    if file_decision.get("keep") is True:
        if row.get("status") != "kept":
            # Remembered so "keep" can be undone without asking the AI again.
            row["beforeKeep"] = {key: row.get(key) for key in _ASSEMBLED_KEYS}
        row.update(
            status="kept", reason="kept-by-you", newStem=old_stem, parts=asdict(parse_stem(old_stem)),
            changed=[], flags=[], selected=False,
        )
        row.pop("newFolder", None)  # kept where it is, under its name
        return
    if row.get("status") == "kept":
        before = row.pop("beforeKeep", None)
        if not isinstance(before, dict):
            # Kept before this dry run, so the AI never named it: the next run will.
            row.update(reason="dry-run-again", selected=False)
            return
        row.update(before)
        if not row.get("newFolder"):
            row.pop("newFolder", None)
        row["reason"] = ""

    parts = dict(row.get("parts") or {})
    decided = set()
    for name in ("creator", "title"):
        if name in model_decision:
            parts[name] = format_part(model_decision[name])
            decided.add(name)
    version = file_decision.get("version")
    if version is None:
        version = decisions.version(row.get("versionId") or "")
    if version is not None:
        parts["version"] = format_part(version)
        decided.add("version")
    flags = list(row.get("flags") or [])
    # An older dry run flagged a version named for another base; it now takes that base.
    for flag in list(flags):
        kind, _, word = flag.partition(":")
        if kind != "base-conflict":
            continue
        named = base_named_by(word, base_codes.all(), exclude=str(row.get("baseModel") or ""))
        if named:
            row["civitaiBaseModel"] = row.get("baseModel")
            row["baseModel"] = named
            row["baseFrom"] = "version-name"
            flags.remove(flag)
    base_model = row.get("baseModel") or ""
    code = base_codes.code(base_model) if base_model else None
    if code is not None:
        parts["base"] = format_part(code)
        flags = [flag for flag in flags if flag != "new-base-code"]

    undecided_words = set()
    for name in ("title", "version"):
        if name not in decided:
            undecided_words.update(format_part(str(parts.get(name) or "")).split())
    kept_flags = []
    for flag in flags:
        kind, _, word = flag.partition(":")
        if kind in _PARTS_FLAG_KINDS or flag in _FOLDER_FLAGS or flag in _CLASH_FLAGS or flag == "official-file":
            # (official-file: older dry runs flagged files that kept their download name; no longer.)
            continue
        if flag in _MAKER_FLAGS and "creator" in decided:
            continue
        if kind == "glued-name" and "title" in decided:
            continue
        if kind == "invented" and word.lower() not in undecided_words:
            continue
        if flag == "unparsed" and {"title", "version"} <= decided:
            continue
        kept_flags.append(flag)

    name_parts = NameParts(
        creator=str(parts.get("creator") or ""),
        title=str(parts.get("title") or ""),
        version=str(parts.get("version") or ""),
        precision=str(parts.get("precision") or ""),
        base=str(parts.get("base") or ""),
    )
    kept_flags.extend(_parts_flags(name_parts))
    kept_flags.extend(users_own_words(_name_check_flags(row, name_parts, base_codes), decided, name_parts.title, name_parts.version))
    new_stem = assemble(name_parts)
    moves = bool(row.get("newFolder"))
    changed = _changed(old_stem, name_parts) + (["folder"] if moves else [])
    row.update(
        newStem=new_stem,
        parts=asdict(name_parts),
        changed=changed,
        flags=list(dict.fromkeys(kept_flags)),
        status="unchanged" if new_stem == old_stem and not moves else "change",
        reason="",
    )


def _name_check_flags(row: dict, parts: NameParts, base_codes) -> list:
    """The renamer's base-word and version-format check, on the parts as they are now."""
    base_model = str(row.get("baseModel") or "")
    names = [base_model, parts.base]
    names.extend(name for name in row.get("modelBaseModels") or [] if isinstance(name, str))
    for known, code in base_codes.all().items():
        names.extend((known, code))
    return name_check_flags(
        parts.title, parts.version, base_vocabulary(names), xl_base=xl_is_base(base_model), base_model=base_model,
        creator=parts.creator,
    )


def _recheck_title_clashes(rows: list[dict], decisions) -> None:
    """Recompute "title-clash" on every named row, and the selection of rows whose flags changed."""
    clashing = title_clash_models(rows, lambda model_id: "title" in decisions.model(model_id))
    for row in rows:
        if row.get("status") not in _NAMED_STATUSES:
            continue
        old = list(row.get("flags") or [])
        flags = [flag for flag in old if flag != "title-clash"]
        if str(row.get("modelId")) in clashing:
            flags.append("title-clash")
        if flags != old:
            row["flags"] = flags
            row["selected"] = row.get("status") == "change" and not needs_review(flags)


def _recheck_folders(rows: list[dict], folders: set, folder_listing: dict) -> None:
    """Recompute "exists" and "collision" for the named rows from or into these folders, and their selection."""
    recheck_folder_flags(rows, folders, folder_listing)


def apply_followups(
    rows: list[dict], steps: list[dict], result: dict, *, data_dir, decisions, base_codes, check_jobs=None
) -> dict:
    """After an apply: saved paths, sidecar titles, locked names, new base codes. Returns the job summary."""
    done = set(result.get("done") or [])
    rows_by_key = {row.get("key"): row for row in rows}
    rename_steps = [step for step in steps if step.get("kind") == "rename" and step.get("entry") in done]
    renamed = list(dict.fromkeys(step["entry"] for step in rename_steps))
    removed = list(
        dict.fromkeys(
            step["entry"] for step in steps
            if step.get("kind") == "trash" and step.get("entry") in done and step["entry"] not in renamed
        )
    )
    warnings: list = []

    def attempt(what: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - the files are renamed; report, don't fail the job
            warnings.append(_short_error(f"{what}: {exc}"))

    # Only renames: a trash step's "to" is the survivor's path, not a new name.
    mapping = {step["from"]: step["to"] for step in rename_steps}
    attempt("Saved check results were not updated", lambda: update_saved_paths(Path(data_dir), mapping))
    if check_jobs is not None:
        attempt("The check results shown were not updated", lambda: update_check_job_paths(check_jobs, mapping))

    for entry in renamed:
        row = rows_by_key.get(entry) or {}
        live_title = row.get("liveTitle")
        if not isinstance(live_title, str) or not live_title:
            continue  # Never fetched here: without the dry run's title the sidecar keeps its own.
        for step in rename_steps:
            if step["entry"] == entry and str(step["to"]).lower().endswith(INFO_SIDECAR_SUFFIX):
                info_path = Path(step["to"])
                attempt(
                    f"The title in {info_path.name} was not updated",
                    lambda path=info_path, title=live_title: refresh_sidecar_title(path, title),
                )

    # A version whose files carry different version parts is remembered per file.
    versions_seen: dict = {}
    for row in rows:
        if row.get("status") in _NAMED_STATUSES:
            versions_seen.setdefault(str(row.get("versionId")), set()).add((row.get("parts") or {}).get("version", ""))
    by_model: dict = {}
    for entry in renamed:
        row = rows_by_key.get(entry)
        if row and row.get("modelId"):
            by_model.setdefault(str(row["modelId"]), []).append(row)
    for model_id, model_rows in by_model.items():
        parts = model_rows[0].get("parts") or {}
        versions: dict = {}
        files: dict = {}
        for row in model_rows:
            version = (row.get("parts") or {}).get("version", "")
            version_id = str(row.get("versionId"))
            if len(versions_seen.get(version_id, ())) > 1:
                files[str(row.get("fileKey"))] = version
            else:
                versions[version_id] = version
        attempt(
            f"The names of model {model_id} were not remembered",
            lambda m=model_id, p=parts, v=versions, f=files: decisions.lock_applied(
                m, p.get("title", ""), p.get("creator", ""), versions=v, files=f
            ),
        )
        for row in model_rows:
            base_model = row.get("baseModel") or ""
            code = (row.get("parts") or {}).get("base", "")
            if "new-base-code" in (row.get("flags") or []) and base_model and code and base_codes.code(base_model) is None:
                attempt(
                    f"The base code for {base_model} was not saved",
                    lambda b=base_model, c=code: base_codes.set(b, c),
                )

    return {
        "renamed": len(renamed),
        "removed": len(removed),
        "skipped": list(result.get("skipped") or []),
        "warnings": warnings,
    }


def recovered_path_mapping(pending: Optional[dict], result: dict, action: str) -> dict:
    """Old -> new paths of the renames a "finish" completed, for the saved records."""
    if action != "finish" or not pending:
        return {}
    steps = [step for step in pending.get("steps", []) if isinstance(step, dict)]
    finished = set(result.get("done") or [])
    by_entry: dict = {}
    for step in steps:
        by_entry.setdefault(step.get("entry"), []).append(step)
    for entry, entry_steps in by_entry.items():
        # Entries done before the interruption never reached the saved records either.
        if all(step.get("done") for step in entry_steps):
            finished.add(entry)
    return {
        step["from"]: step["to"]
        for step in steps
        if step.get("kind") == "rename" and step.get("entry") in finished and step.get("from") and step.get("to")
    }


def download_naming_model(
    folder: Path,
    *,
    progress: Callable[[int, int, str], Any],
    control,
    http_get: Optional[Callable[..., Any]] = None,
    url: str = NAMING_MODEL_URL,
    filename: str = NAMING_MODEL_FILENAME,
    chunk_size: int = 8 * _MB,
) -> str:
    """Stream the naming model into ``folder`` through a ``.part`` file.

    Returns the file name, or "" when stopped. Never replaces an existing file.
    """
    folder = Path(folder)
    target = folder / filename
    part = folder / (filename + ".part")
    if os.path.lexists(target):
        raise FileExistsError(f"{target} already exists")
    if http_get is None:
        import requests

        http_get = requests.get
    folder.mkdir(parents=True, exist_ok=True)
    stopped = False
    try:
        with http_get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as response:
            if not response.ok:
                raise OSError(f"The naming model download failed (HTTP {response.status_code} {response.reason}).")
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            written = 0
            with part.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    # Never paused: a stalled stream times out and the download is lost.
                    if control.is_cancelled():
                        stopped = True
                        break
                    if not chunk:
                        continue
                    fh.write(chunk)
                    written += len(chunk)
                    progress(
                        written // _MB,
                        total // _MB,
                        f"Downloading the naming model: {written // _MB} of {total // _MB} MB",
                    )
        if stopped:
            part.unlink(missing_ok=True)
            return ""
        if total and written != total:
            raise OSError(f"The naming model download stopped early ({written} of {total} bytes).")
        if os.path.lexists(target):
            raise FileExistsError(f"{target} already exists")
        part.rename(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return filename
