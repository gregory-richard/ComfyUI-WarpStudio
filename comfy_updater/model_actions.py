"""One download, start to finish: ask Civitai, name it, fetch it, file it, replace the old one.

The order keeps the library safe. The weights come through a ``.part`` file
checked against Civitai's SHA-256 (``download_to``). Only once they are in
place with their info file (and the preview, when Civitai has one) does a
Replace send the old version's files to the Recycle Bin, never one of the new
files: the old weights first, and their sidecars only once the weights are
gone. When the new info file can't be written, the old version is kept, so an
identified version is never swapped for an unidentified one. A failure or a
Stop before that leaves the old version as it was. Once the new weights are in
place, Stop no longer applies: what is left is quick, and stopping half-way
through the Replace would leave both versions half there.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .check_records import check_item_for, downloaded_item, update_check_records
from .civitai_client import CivitaiRequestError
from .constants import INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .download_queue import MB, DownloadRequest
from .download_target import OUTSIDE_ROOTS_MESSAGE, ai_stem, choose_target, inside_roots
from .jobs import _paid_access_now
from .model_download import (
    NO_SHA_MESSAGE,
    DownloadError,
    DownloadStopped,
    download_to,
    file_sha256,
    pick_file,
)
from .model_files import missing_info, stem_files
from .rename_apply import send_to_trash
from .sidecar import preview_path_for, stored_sha256, write_json
from .updater_service import _download_preview_if_needed, _paid_access

EARLY_ACCESS_ERROR = "This release is in early access."
RELEASE_GONE_ERROR = "Civitai no longer has this release."
NO_FILE_ERROR = "Civitai lists no model file for this release."
FILE_GONE_ERROR = "Civitai no longer has this file."
INFO_GONE_ERROR = "Its info file is gone, or its weights are already there."
OLD_FILE_GONE_WARNING = "The old version's file was already gone."
NEW_BASE_WARNING = "New base model {base}: its files get the code {code}."
KEPT_WITHOUT_INFO_WARNING = (
    "The old version was kept because the new version's info file could not be written."
)
# The reason when sending the old weights raised nothing but left them in place.
WEIGHTS_STILL_THERE = "it is still there"
_INFO_SOURCE = "comfy-civitai-updater"


def release_is_paid(version: dict) -> bool:
    """Whether the release costs Buzz today: a raw API version, or a check item's version."""
    if not isinstance(version, dict):
        return False
    access = version if "paid" in version else _paid_access(version)
    return bool(_paid_access_now(access).get("paid"))


def info_payload(version: dict, file: dict) -> dict:
    """The ``.civitai.info`` of a download: the version, with the downloaded file first.

    ``stored_sha256`` reads ``files[0]``, so the file that is on disk goes first.
    """
    payload = dict(version)
    files = version.get("files")
    others = [
        entry
        for entry in (files if isinstance(files, list) else [])
        if entry is not file and entry != file
    ]
    payload["files"] = [file, *others]
    payload["extensions"] = {"source": _INFO_SOURCE, "updatedAt": _utc_now()}
    return payload


@dataclass
class DownloadContext:
    config: dict
    data_dir: Path
    job_manager: Any
    decisions: Any
    base_codes: Any
    answer_cache: Any
    make_client: Callable[[Callable[[], bool]], Any]
    # None when no naming model can run.
    make_generator: Optional[Callable[[Callable[[], bool]], Any]]
    clip_name: str
    roots_for: Callable[[str], list]
    lora_roots: Callable[[], list]
    trash: Callable[[str], None] = send_to_trash
    # The names the check gave the releases (a ReleaseNameStore); None asks the AI for each.
    release_names: Any = None


def download_one(request: DownloadRequest, *, report, should_stop, ctx: DownloadContext) -> None:
    """Download one request, or raise ``DownloadError`` (the card shows its text) or a Stop.

    ``request.file_name`` is set once the name is known, and warnings are
    appended to ``request.warnings``.
    """
    if request.source == "gallery":
        _gallery(request, report, should_stop, ctx)
    else:
        _updates(request, report, should_stop, ctx)


# --- the two flows -------------------------------------------------------------


def _updates(request: DownloadRequest, report, should_stop, ctx: DownloadContext) -> None:
    client = ctx.make_client(should_stop)
    version = _release(client, request.version_id, report, should_stop)
    file = pick_file(version)
    if file is None:
        raise DownloadError(NO_FILE_ERROR)
    sha = file_sha256(file)
    if not sha:
        raise DownloadError(NO_SHA_MESSAGE)
    roots = ctx.roots_for(request.model_type)
    reference = Path(request.reference_path)
    if not request.reference_path or not inside_roots(reference.parent, roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    if request.replace_path and not inside_roots(Path(request.replace_path), roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)

    named = ctx.release_names.get(request.version_id) if ctx.release_names is not None else None
    if named is None:
        named = _ai_name(request, version, file, reference, client, report, should_stop, ctx)
    _stop_if_asked(should_stop)
    code = ctx.base_codes.code(str(version.get("baseModel") or "")) or ""
    try:
        target = choose_target(
            file,
            reference_folder=reference.parent,
            type_roots=roots,
            named=named,
            fallback_code=code,
        )
    except OSError as exc:
        raise DownloadError(f"Could not look in the model folder: {_reason(exc)}") from exc
    request.file_name = target.path.name

    made = _make_folder(target.folder)
    try:
        seen = _download(client, target.path, file, sha, report, should_stop, ctx)
    except BaseException:
        if made:
            _remove_if_empty(target.folder)
        raise

    # The new weights are in place and checked; nothing below raises.
    if target.named and named is not None and named.new_base:
        _learn_base_code(ctx, request, named.new_base, named.code)
    info_path = target.folder / (target.stem + INFO_SIDECAR_SUFFIX)
    preview_path = target.folder / (target.stem + PREVIEW_SIDECAR_SUFFIX)
    info_written = _write_info(info_path, version, file, request)
    _fetch_preview(client, preview_path, version)
    remove: list[str] = []
    if request.replace_path and not info_written:
        # An identified old version is never swapped for an unidentified new one.
        request.warnings.append(KEPT_WITHOUT_INFO_WARNING)
    elif request.replace_path:
        report(seen[0], seen[1], "Sending the old version to the Recycle Bin")
        old = Path(request.replace_path)
        _trash_old(old, [target.path, info_path, preview_path], request, ctx)
        # A record goes only with its weights: one the Recycle Bin refused stays listed.
        if not os.path.lexists(old):
            remove.append(request.replace_path)
    _record(
        ctx,
        request,
        remove=remove,
        source=request.check_item,
        weights=target.path,
        info_path=info_path,
        model_type=request.model_type,
    )


def _gallery(request: DownloadRequest, report, should_stop, ctx: DownloadContext) -> None:
    roots = ctx.lora_roots()
    found = missing_info(roots, request.name)
    if found is None:
        raise DownloadError(INFO_GONE_ERROR)
    info_path, info = found
    client = ctx.make_client(should_stop)
    version = _release(client, str(info.get("id")), report, should_stop)
    wanted = stored_sha256(info)
    if wanted:
        file = pick_file(version, wanted)
        if file is None:
            raise DownloadError(FILE_GONE_ERROR)
    else:
        file = pick_file(version)
        if file is None:
            raise DownloadError(NO_FILE_ERROR)
    sha = file_sha256(file)
    if not sha:
        raise DownloadError(NO_SHA_MESSAGE)
    name = file.get("name") if isinstance(file.get("name"), str) else ""
    suffix = os.path.splitext(name)[1]
    if suffix.lower() not in SUPPORTED_MODEL_EXTENSIONS:
        raise DownloadError(f"Civitai's file name can't be used: {name}")
    # Beside the info file, under its name; the info file is left as it is.
    target = info_path.parent / (info_path.name[: -len(INFO_SIDECAR_SUFFIX)] + suffix)
    if not inside_roots(target, roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    request.file_name = target.name

    _download(client, target, file, sha, report, should_stop, ctx)

    _fetch_preview(client, preview_path_for(info_path), version)
    source = check_item_for(ctx.job_manager, str(info_path))
    if source is not None:
        _record(
            ctx,
            request,
            remove=[str(info_path)],
            source=source,
            weights=target,
            info_path=info_path,
            model_type=source.get("modelType") or request.model_type,
        )


# --- the steps -----------------------------------------------------------------


def _release(client, version_id: str, report, should_stop) -> dict:
    """The release as Civitai has it now, refused when gone or in early access."""
    report(0, 0, "Asking Civitai for the release")
    try:
        version = client.get_version(version_id, raise_on_error=True)
    except CivitaiRequestError as exc:
        _stop_if_asked(should_stop)  # A lookup cut short by Stop is a stop.
        if exc.status == 404:
            raise DownloadError(RELEASE_GONE_ERROR) from exc
        raise DownloadError(str(exc)) from exc
    _stop_if_asked(should_stop)
    if not isinstance(version, dict):
        raise DownloadError(RELEASE_GONE_ERROR)
    if release_is_paid(version):
        raise DownloadError(EARLY_ACCESS_ERROR)
    return version


def _ai_name(request, version, file, reference, client, report, should_stop, ctx):
    """The dry run's name for the release, or None (no naming model, or it failed)."""
    if ctx.make_generator is None:
        return None
    file_name = file.get("name") or ""
    report(0, 0, f"Naming {file_name} with the AI")
    try:
        generator = ctx.make_generator(should_stop)
        if generator is None:
            return None
        return ai_stem(
            request.model_id or str(version.get("modelId") or ""),
            version,
            file,
            model_type=request.model_type,
            reference=reference,
            client=client,
            generator=generator,
            decisions=ctx.decisions,
            base_codes=ctx.base_codes,
            cache=ctx.answer_cache,
            clip_name=ctx.clip_name,
            should_stop=should_stop,
        )
    except DownloadStopped:
        raise
    except Exception as exc:  # noqa: BLE001 - no AI name is not a failed download
        print(f"Civitai updater: could not name {file_name} with the AI ({exc})")
        return None


def _make_folder(folder: Path) -> bool:
    """Make a new base's folder (its parent is the old version's); True when made here."""
    if folder.is_dir():
        return False
    try:
        folder.mkdir()
    except FileExistsError:
        return False
    except OSError as exc:
        raise DownloadError(f"Could not make the folder {folder.name}: {_reason(exc)}") from exc
    return True


def _remove_if_empty(folder: Path) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def _learn_base_code(ctx, request, base_model: str, code: str) -> None:
    """Keep the AI's code for a new base, so the next release of that base gets the same one."""
    try:
        ctx.base_codes.set(base_model, code)
    except OSError as exc:
        print(f"Civitai updater: could not save the code {code} for {base_model} ({exc})")
    request.warnings.append(NEW_BASE_WARNING.format(base=base_model, code=code))


def _download(client, target: Path, file: dict, sha: str, report, should_stop, ctx) -> tuple:
    """Stream the weights into place; returns the last (written, total) reported."""
    name = target.name
    seen = [0, 0]

    def on_progress(written: int, total: int) -> None:
        seen[0], seen[1] = written, total
        report(written, total, f"Downloading {name}: {written // MB} of {total // MB} MB")

    _stop_if_asked(should_stop)
    try:
        # Inside the roots (checked above); download_to leaves the folder to its caller.
        target.parent.mkdir(parents=True, exist_ok=True)
        download_to(
            target,
            str(file.get("downloadUrl") or ""),
            sha,
            session=client.session,
            api_key=str(ctx.config.get("apiKey") or "").strip(),
            timeout=_timeout(ctx.config),
            should_stop=should_stop,
            on_progress=on_progress,
            expected_size=_expected_size(file),
        )
    except FileExistsError as exc:
        raise DownloadError(f"A file named {name} is already there.") from exc
    except OSError as exc:
        raise DownloadError(f"Could not write {name}: {_reason(exc)}") from exc
    return seen[0], seen[1]


def _write_info(info_path: Path, version: dict, file: dict, request: DownloadRequest) -> bool:
    """Write the new info file; False (with a warning) when it could not be written."""
    try:
        if info_path.exists():  # Never overwritten, even one that turned up meanwhile.
            raise FileExistsError(errno.EEXIST, "a file of that name is already there")
        write_json(info_path, info_payload(version, file))
    except OSError as exc:
        request.warnings.append(f"Could not write {info_path.name}: {_reason(exc)}")
        return False
    return True


def _fetch_preview(client, preview_path: Path, version: dict) -> None:
    """Civitai's preview when there is none yet; a failure is silent, as in scans."""
    try:
        _download_preview_if_needed(client, preview_path, version)
    except Exception as exc:  # noqa: BLE001
        print(f"Civitai updater: could not fetch the preview {preview_path.name} ({exc})")


def _trash_old(old: Path, new_files: list, request: DownloadRequest, ctx: DownloadContext) -> None:
    """Send the old version's files to the Recycle Bin, never one of the new files.

    The weights go first and on their own. Their sidecars follow only once
    they are gone, so weights the Recycle Bin refused (a file ComfyUI holds
    open) keep their info file and preview.
    """
    files = stem_files(old)
    if not files:
        request.warnings.append(OLD_FILE_GONE_WARNING)
        return
    keep = {_norm(path) for path in new_files}
    weights, sidecars = files[0], files[1:]
    if _norm(weights) in keep:
        return  # Never the case (the new name was free); nothing of it is touched.
    try:
        ctx.trash(str(weights))
    except Exception as exc:  # noqa: BLE001 - a warning; the old version stays whole
        request.warnings.append(f"Could not send {weights.name} to the Recycle Bin: {_reason(exc)}")
        return
    if os.path.lexists(weights):
        request.warnings.append(
            f"Could not send {weights.name} to the Recycle Bin: {WEIGHTS_STILL_THERE}"
        )
        return
    for path in sidecars:
        if _norm(path) in keep:
            continue
        try:
            ctx.trash(str(path))
        except Exception as exc:  # noqa: BLE001 - the rest still go
            request.warnings.append(
                f"Could not send {path.name} to the Recycle Bin: {_reason(exc)}"
            )


def _record(
    ctx: DownloadContext,
    request: DownloadRequest,
    *,
    remove: list,
    source: dict,
    weights: Path,
    info_path: Path,
    model_type: str,
) -> None:
    """Give the new file its record in the check results; a failure costs only a warning.

    No record is added when the new info file can't be read; ``remove`` still goes.
    """
    try:
        item = downloaded_item(source, weights=weights, info_path=info_path, model_type=model_type)
        add = [item] if item else []
        if remove or add:
            update_check_records(ctx.job_manager, ctx.data_dir, remove=remove, add=add)
    except Exception as exc:  # noqa: BLE001
        print(f"Civitai updater: could not update the check results ({exc})")
        request.warnings.append(f"Could not update the check results: {_reason(exc)}")


def delete_model(
    weights: Path, *, trash: Callable[[str], None], job_manager, data_dir: Path
) -> dict:
    """Send a LoRA's weights and every sidecar to the Recycle Bin.

    The weights go first and on their own. If they can't go, ``DownloadError``
    is raised and nothing else is touched, so weights ComfyUI holds open keep
    their info file and preview. A sidecar that can't go is only a warning.
    The file then leaves the check results.
    """
    files = stem_files(weights)
    if not files:
        return {"removed": [], "warnings": []}
    weights = files[0]
    try:
        trash(str(weights))
    except Exception as exc:  # noqa: BLE001 - the card shows the reason
        raise DownloadError(
            f"Could not send {weights.name} to the Recycle Bin: {_reason(exc)}"
        ) from exc
    if os.path.lexists(weights):
        raise DownloadError(
            f"Could not send {weights.name} to the Recycle Bin: {WEIGHTS_STILL_THERE}"
        )
    removed = [weights.name]
    warnings: list[str] = []
    for path in files[1:]:
        try:
            trash(str(path))
            removed.append(path.name)
        except Exception as exc:  # noqa: BLE001 - the rest still go
            warnings.append(f"Could not send {path.name} to the Recycle Bin: {_reason(exc)}")
    try:
        update_check_records(job_manager, data_dir, remove=[str(weights)], add=[])
    except Exception as exc:  # noqa: BLE001
        print(f"Civitai updater: could not update the check results ({exc})")
        warnings.append(f"Could not update the check results: {_reason(exc)}")
    return {"removed": removed, "warnings": warnings}


# --- small helpers -------------------------------------------------------------


def _stop_if_asked(should_stop) -> None:
    if should_stop():
        raise DownloadStopped()


def _timeout(config: dict) -> float:
    try:
        value = float(config.get("requestTimeoutSeconds", 30))
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _expected_size(file: dict) -> int:
    try:
        return max(int(float(file.get("sizeKB") or 0) * 1024), 0)
    except (TypeError, ValueError):
        return 0


def _reason(exc: BaseException) -> str:
    """An error's own words, without the path an ``OSError`` carries."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


def _norm(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
