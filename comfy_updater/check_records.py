"""The check results a download changes, held in memory and saved, until the next check."""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .archive import ARCHIVE_RESULTS_FILENAME, folder_key
from .constants import CACHE_SCHEMA_VERSION
from .rename_apply import SAVED_PATH_FILES, rewrite_paths
from .sidecar import preview_path_for, read_data_file, write_json
from .updater_service import sidecar_seed_item

CHECK_JOB = "check-updates"
LAST_CHECK_FILENAME = "last_check.json"
# One writer of the held jobs and the saved check at a time.
_WRITE_LOCK = threading.Lock()
# What a fresh download takes from the record of another file of its model.
_CARRIED_FIELDS = ("remoteVersions", "creatorName", "modelUrl", "nsfw")


def _norm(path: object) -> str:
    text = str(path or "")
    return os.path.normcase(os.path.normpath(text)) if text else ""


def edit_check_items(
    items: list[dict], *, remove: Iterable[str], add: list[dict], moved: bool = False
) -> bool:
    """Mark the items whose ``modelPath`` or ``filePath`` is in ``remove`` deleted, then add ``add``.

    A deleted item keeps its row, so its card says Deleted until the next check,
    which no longer finds the file. With ``moved``, the file went to the other
    drive: the row is marked ``moved`` too, and its card no longer shows it. An
    added item replaces the item with its ``modelPath``, or else is appended.
    The list changes in place; True when anything changed.
    """
    gone = {_norm(path) for path in remove} - {""}
    changed = False
    if gone:
        for item in items:
            if (
                isinstance(item, dict)
                and not item.get("deleted")
                and (_norm(item.get("modelPath")) in gone or _norm(item.get("filePath")) in gone)
            ):
                item["deleted"] = True
                if moved:
                    item["moved"] = True
                changed = True
    for new in add:
        key = _norm(new.get("modelPath"))
        index = next(
            (
                position
                for position, item in enumerate(items)
                if key and isinstance(item, dict) and _norm(item.get("modelPath")) == key
            ),
            None,
        )
        if index is None:
            items.append(new)
        else:
            items[index] = new
        changed = True
    return changed


def update_check_records(
    job_manager,
    data_dir: Path,
    *,
    remove: list[str],
    add: list[dict],
    archive_folder: str = "",
    moved: bool = False,
) -> None:
    """Edit every held check job and the saved check (when it is of this schema) alike.

    The saved check is the drive's: an archived item is never added to it. With
    ``archive_folder``, the saved archive results are edited too, and they take
    the archived items. ``moved`` marks the removed items moved as well.
    """
    remove = [str(path) for path in remove if path]
    if not remove and not add:
        return
    with _WRITE_LOCK:
        _update_locked(job_manager, Path(data_dir), remove, add, moved)
        if archive_folder:
            archived = [item for item in add if item.get("archived")]
            _update_archive_locked(Path(data_dir), archive_folder, remove, archived, moved)


def _update_locked(
    job_manager, data_dir: Path, remove: list[str], add: list[dict], moved: bool
) -> None:
    # Each record gets its own copies, so an edit of one never shows in another.
    job_manager.edit_items(
        CHECK_JOB,
        lambda items: edit_check_items(items, remove=remove, add=copy.deepcopy(add), moved=moved),
    )
    path = data_dir / LAST_CHECK_FILENAME
    if not path.is_file():
        return
    payload = read_data_file(path)
    if payload is None or _schema(payload) != CACHE_SCHEMA_VERSION:
        return  # Unreadable or outdated: the panel ignores it, and so does this.
    items = payload.get("items")
    if not isinstance(items, list):
        return
    on_drive = [item for item in add if not item.get("archived")]
    if not edit_check_items(items, remove=remove, add=copy.deepcopy(on_drive), moved=moved):
        return
    summary = payload.get("summary")
    summary = job_manager.summarize_check_items(
        summary if isinstance(summary, dict) else {},
        [item for item in items if isinstance(item, dict)],
    )
    summary["total"] = sum(1 for item in items if isinstance(item, dict) and not item.get("deleted"))
    payload["summary"] = summary
    _write_atomic(path, payload)


def save_last_check(data_dir: Path, payload: dict) -> None:
    """Write last_check.json whole: the one way every writer of it goes."""
    with _WRITE_LOCK:
        _write_atomic(Path(data_dir) / LAST_CHECK_FILENAME, payload)


def rewrite_saved_paths(data_dir: Path, mapping: dict) -> None:
    """Point the saved check's (and progress.json's) ``…Path`` values at renamed files' new paths.

    The read and the write are one step under the lock, so a download's edit of
    the saved check in between is not lost.
    """
    if not mapping:
        return
    with _WRITE_LOCK:
        for name in SAVED_PATH_FILES:
            path = Path(data_dir) / name
            if not path.is_file():
                continue
            payload = read_data_file(path)
            if payload is None:
                continue  # Unreadable or corrupt: leave it for its owner to handle.
            if rewrite_paths(payload, mapping):
                _write_atomic(path, payload)


def _write_atomic(path: Path, payload: dict) -> None:
    """Write through a temporary file of its own, so two writers never share one; call under the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _update_archive_locked(
    data_dir: Path, archive_folder: str, remove: list[str], add: list[dict], moved: bool
) -> None:
    """Edit the saved archive results of ``archive_folder``; start them for a first archived row.

    Results that are missing, outdated or of another folder are of no use to
    the panel, so the first archived row starts new ones, with no check time.
    Results that can't be read are left as they are: they may be whole.
    """
    path = data_dir / ARCHIVE_RESULTS_FILENAME
    exists = path.is_file()
    payload = read_data_file(path) if exists else None
    if exists and payload is None:
        return
    usable = (
        payload is not None
        and _schema(payload) == CACHE_SCHEMA_VERSION
        and isinstance(payload.get("archiveFolder"), str)
        and folder_key(payload["archiveFolder"]) == folder_key(archive_folder)
        and isinstance(payload.get("items"), list)
    )
    if not usable:
        if not add:
            return
        items = copy.deepcopy(add)
        write_json(
            path,
            {
                "schemaVersion": CACHE_SCHEMA_VERSION,
                "archiveFolder": archive_folder,
                "checkedAt": "",
                "summary": {"mode": "archive", "total": _held(items)},
                "items": items,
            },
        )
        return
    items = payload["items"]
    if not edit_check_items(items, remove=remove, add=copy.deepcopy(add), moved=moved):
        return
    summary = payload.get("summary")
    summary = dict(summary) if isinstance(summary, dict) else {}
    summary["total"] = _held(items)
    payload["summary"] = summary
    write_json(path, payload)


def _held(items: list) -> int:
    return sum(1 for item in items if isinstance(item, dict) and not item.get("deleted"))


def _schema(payload: dict) -> int:
    try:
        return int(payload.get("schemaVersion") or 0)
    except (TypeError, ValueError):
        return 0


def check_item_for(job_manager, path: str) -> Optional[dict]:
    """A copy of the item for ``path`` (its ``modelPath``) in the newest held check job."""
    wanted = _norm(path)
    if not wanted:
        return None
    jobs = [job_manager.get(job_id) for job_id in job_manager.job_ids(CHECK_JOB)]
    jobs = [job for job in jobs if job is not None]
    if not jobs:
        return None
    newest = max(jobs, key=lambda job: job.startedAt or job.finishedAt or "")
    for item in list(newest.items):
        if isinstance(item, dict) and _norm(item.get("modelPath")) == wanted:
            return copy.deepcopy(item)
    return None


def downloaded_item(
    source: dict, *, weights: Path, info_path: Path, model_type: str
) -> Optional[dict]:
    """The record a fresh download gets until the next check, or None without an identifying info.

    It is read from the new info file, and carries the model's releases from
    ``source``, the record of another file of the same model.
    """
    item = sidecar_seed_item(
        {
            "path": Path(weights),
            "filePath": str(weights),
            "infoPath": Path(info_path),
            "modelType": model_type,
            "metadataOnly": False,
        }
    )
    if item is None:
        return None
    item["metadataOnly"] = False
    item["filePath"] = str(weights)
    if isinstance(source, dict) and str(source.get("modelId") or "") == item["modelId"]:
        for key in _CARRIED_FIELDS:
            if key in source:
                item[key] = copy.deepcopy(source[key])
    item["hasLocalPreview"] = preview_path_for(Path(info_path)).exists()
    item["fallbackSource"] = ""
    item["lastCheckedAt"] = datetime.now(timezone.utc).isoformat()
    return item
