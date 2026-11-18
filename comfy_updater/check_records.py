"""The check results a download changes, held in memory and saved, until the next check."""

from __future__ import annotations

import copy
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .constants import CACHE_SCHEMA_VERSION
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


def edit_check_items(items: list[dict], *, remove: Iterable[str], add: list[dict]) -> bool:
    """Drop the items whose ``modelPath`` or ``filePath`` is in ``remove``, then add ``add``.

    An added item replaces the item with its ``modelPath``, or else is appended.
    The list changes in place; True when anything changed.
    """
    gone = {_norm(path) for path in remove} - {""}
    changed = False
    if gone:
        kept = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and (_norm(item.get("modelPath")) in gone or _norm(item.get("filePath")) in gone)
            )
        ]
        if len(kept) != len(items):
            items[:] = kept
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
    job_manager, data_dir: Path, *, remove: list[str], add: list[dict]
) -> None:
    """Edit every held check job and the saved check (when it is of this schema) alike."""
    remove = [str(path) for path in remove if path]
    if not remove and not add:
        return
    with _WRITE_LOCK:
        _update_locked(job_manager, Path(data_dir), remove, add)


def _update_locked(job_manager, data_dir: Path, remove: list[str], add: list[dict]) -> None:
    # Each record gets its own copies, so an edit of one never shows in another.
    job_manager.edit_items(
        CHECK_JOB, lambda items: edit_check_items(items, remove=remove, add=copy.deepcopy(add))
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
    if not edit_check_items(items, remove=remove, add=copy.deepcopy(add)):
        return
    summary = payload.get("summary")
    summary = job_manager.summarize_check_items(
        summary if isinstance(summary, dict) else {},
        [item for item in items if isinstance(item, dict)],
    )
    summary["total"] = len(items)
    payload["summary"] = summary
    write_json(path, payload)


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
