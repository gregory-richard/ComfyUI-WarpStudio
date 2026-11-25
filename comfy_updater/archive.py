"""The archive folder: a drive old models are moved to, laid out like the models folder.

Everything here only reads the archive. Its results are kept in the plugin's own
data folder, never inside the archive. The archive is written to only when the
user clicks for it: Archive copies a version in and Unarchive copies one back
(``model_moves``), each checked before the original goes to the Recycle Bin, and
Delete sends an archived file to that drive's recycle bin
(``model_actions.delete_model``). Nothing renames a file there or deletes one
for good.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .constants import (
    CACHE_SCHEMA_VERSION,
    INFO_SIDECAR_SUFFIX,
    PREVIEW_SIDECAR_SUFFIX,
    SUPPORTED_MODEL_EXTENSIONS,
)
from .path_resolver import _read_identifying_sidecar
from .sidecar import model_file_path, read_data_file, write_json

ARCHIVE_RESULTS_FILENAME = "archive_check.json"
ARCHIVE_NO_MODEL_FOLDERS_ERROR = "No model folders found in the archive folder."
ARCHIVE_INCOMPLETE_ERROR = "The archive couldn't be read completely, so its last results were kept."


class ArchiveListingError(OSError):
    """A folder in the archive couldn't be read, so the listing would be partial."""


def archive_type_folders(
    archive_root: Path, type_roots: dict[str, list[Path]]
) -> dict[str, list[tuple[Path, Path]]]:
    """For each model type, the archive folders that mirror one of its folders on D.

    The archive is laid out like the models folder, so a top folder belongs to a
    type when it has the name of one of that type's roots (case does not matter).
    Top folders that match no type are left alone.
    """
    top_folders: dict[str, Path] = {}
    try:
        for child in sorted(archive_root.iterdir(), key=lambda path: path.name.casefold()):
            if child.is_dir():
                top_folders.setdefault(child.name.casefold(), child)
    except OSError:
        pass

    matched: dict[str, list[tuple[Path, Path]]] = {}
    for model_type, roots in type_roots.items():
        matched[model_type] = [
            (top_folders[root.name.casefold()], root)
            for root in roots
            if root.name.casefold() in top_folders
        ]
    return matched


def list_archive_entries(
    archive_root: Path, type_roots: dict[str, list[Path]]
) -> tuple[list[dict], dict]:
    """One entry per .civitai.info in the archive that identifies a model.

    Each entry carries the info it was read from, so the check needs the drive
    no more once this listing is done. Also counts the weights files that have
    no info file beside them, which the check cannot say anything about, and
    the archive folders that matched a model type (none: nothing to check).
    """
    entries: list[dict] = []
    without_info = 0
    type_folders = 0
    unreadable: list[OSError] = []
    for model_type, pairs in archive_type_folders(archive_root, type_roots).items():
        # Two roots on D may share a name; their one archive folder is listed once.
        seen_folders: set[str] = set()
        for archive_folder, _d_root in pairs:
            seen_key = os.path.normcase(str(archive_folder))
            if seen_key in seen_folders:
                continue
            seen_folders.add(seen_key)
            type_folders += 1
            for dir_path, dir_names, file_names in os.walk(archive_folder, onerror=unreadable.append):
                dir_names.sort(key=str.casefold)
                names = sorted(file_names, key=str.casefold)
                info_stems = {
                    name[: -len(INFO_SIDECAR_SUFFIX)].casefold()
                    for name in names
                    if name.lower().endswith(INFO_SIDECAR_SUFFIX)
                }
                weights_by_stem: dict[str, Path] = {}
                for name in names:
                    if Path(name).suffix.lower() in SUPPORTED_MODEL_EXTENSIONS:
                        weights = Path(dir_path) / name
                        weights_by_stem.setdefault(weights.stem.casefold(), weights)
                        if weights.stem.casefold() not in info_stems:
                            without_info += 1
                for name in names:
                    if not name.lower().endswith(INFO_SIDECAR_SUFFIX):
                        continue
                    info_path = Path(dir_path) / name
                    info = _read_identifying_sidecar(info_path)
                    if info is None:
                        continue
                    stem = name[: -len(INFO_SIDECAR_SUFFIX)]
                    entries.append(
                        {
                            "modelType": model_type,
                            "path": info_path,
                            "filePath": weights_by_stem.get(stem.casefold()) or model_file_path(info_path, info),
                            "infoPath": info_path,
                            "previewPath": info_path.with_name(f"{stem}{PREVIEW_SIDECAR_SUFFIX}"),
                            "metadataOnly": True,
                            "archived": True,
                            "info": info,
                        }
                    )
    if unreadable:
        # os.walk skips what it can't read without a word; a drive that dropped
        # out would give a listing with whole folders missing.
        raise ArchiveListingError(str(getattr(unreadable[0], "filename", None) or archive_root))
    return entries, {"models": len(entries), "withoutInfo": without_info, "typeFolders": type_folders}


def overlaps_model_root(archive_folder: str, model_roots: dict) -> bool:
    """Whether the archive folder is a model folder, lies inside one or holds one.

    ``model_roots`` maps each model type to its folders, as the effective roots
    are given. Any of the three would let the check, a rename or a download
    write in the archive, and would count every model as its own duplicate.
    """
    archive = _absolute_key(archive_folder)
    for roots in model_roots.values():
        for root in roots or []:
            key = _absolute_key(str(root))
            try:
                common = os.path.commonpath([archive, key])
            except ValueError:  # on another drive
                continue
            if common in (archive, key):
                return True
    return False


def _absolute_key(folder: str) -> str:
    return folder_key(os.path.abspath(os.path.expanduser(folder.strip())))


def archive_reference_path(
    archive_root: Path,
    archived_path: Path,
    model_type: str,
    type_roots: dict[str, list[Path]],
) -> Optional[Path]:
    """Where a download from an archived row goes, as a reference path on D.

    The archive's top folder is matched by name (case does not matter) with one
    of the type's roots on D. The subfolders below it are kept when D has them,
    else the type root itself is used. Nothing in the archive is read, so this
    works while its drive is unplugged; the file does not have to exist yet.
    None when the row lies outside the archive, in no type folder, or would
    leave the D root.
    """
    try:
        parts = archived_path.relative_to(archive_root).parts
    except ValueError:
        return None
    if len(parts) < 2 or ".." in parts:
        return None
    type_folder = parts[0].casefold()
    for d_root in type_roots.get(model_type, []):
        if d_root.name.casefold() != type_folder:
            continue
        subfolder = d_root.joinpath(*parts[1:-1])
        target_dir = subfolder if subfolder.is_dir() else d_root
        return target_dir / archived_path.name
    return None


def load_archive_results(data_dir: Path, archive_folder: str) -> tuple[list[dict], dict]:
    """The saved archive check's items and its summary with when it ran, from one read.

    Both are empty when the results are missing, outdated or for another folder.
    """
    data = _load_results(data_dir, archive_folder)
    items = data.get("items") if data else None
    summary = data.get("summary") if data else None
    return (
        items if isinstance(items, list) else [],
        {"checkedAt": data.get("checkedAt", ""), **summary} if isinstance(summary, dict) else {},
    )


def load_archive_items(data_dir: Path, archive_folder: str) -> list[dict]:
    """The saved archive check's items, or nothing when they are missing, outdated or for another folder."""
    return load_archive_results(data_dir, archive_folder)[0]


def load_archive_summary(data_dir: Path, archive_folder: str) -> dict:
    """The saved archive check's summary and when it ran, under the same rules as the items."""
    return load_archive_results(data_dir, archive_folder)[1]


def save_archive_results(data_dir: Path, archive_folder: str, summary: dict, items: list[dict]) -> None:
    write_json(
        data_dir / ARCHIVE_RESULTS_FILENAME,
        {
            "schemaVersion": CACHE_SCHEMA_VERSION,
            "archiveFolder": archive_folder,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "items": items,
        },
    )


def drop_deleted_archive_items(data_dir: Path, archive_folder: str) -> None:
    """Rewrite the saved archive results without their ``deleted`` items, keeping when they ran."""
    data = _load_results(data_dir, archive_folder)
    items = data.get("items") if data else None
    if not isinstance(items, list):
        return
    kept = [item for item in items if not (isinstance(item, dict) and item.get("deleted"))]
    if len(kept) == len(items):
        return
    summary = data.get("summary")
    summary = {**summary, "total": len(kept)} if isinstance(summary, dict) else {"total": len(kept)}
    write_json(data_dir / ARCHIVE_RESULTS_FILENAME, {**data, "summary": summary, "items": kept})


def _load_results(data_dir: Path, archive_folder: str) -> dict | None:
    if not archive_folder:
        return None
    data = read_data_file(data_dir / ARCHIVE_RESULTS_FILENAME)
    if not data or data.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        return None
    saved = data.get("archiveFolder")
    if not isinstance(saved, str) or folder_key(saved) != folder_key(archive_folder):
        return None
    return data


def folder_key(folder: str) -> str:
    """One key for two spellings of a folder: a trailing separator never counts, case only on Windows."""
    return os.path.normcase(os.path.normpath(folder))
