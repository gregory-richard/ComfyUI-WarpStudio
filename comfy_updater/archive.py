"""The archive folder: a drive old models are moved to, laid out like the models folder.

Everything here only reads the archive. Its results are kept in the plugin's own
data folder, never inside the archive, because nothing in this package may write,
rename or delete anything there.
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

    Also counts the weights files that have no info file beside them, which the
    check cannot say anything about.
    """
    entries: list[dict] = []
    without_info = 0
    for model_type, pairs in archive_type_folders(archive_root, type_roots).items():
        # Two roots on D may share a name; their one archive folder is listed once.
        seen_folders: set[str] = set()
        for archive_folder, _d_root in pairs:
            folder_key = os.path.normcase(str(archive_folder))
            if folder_key in seen_folders:
                continue
            seen_folders.add(folder_key)
            for dir_path, dir_names, file_names in os.walk(archive_folder):
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
                        }
                    )
    return entries, {"models": len(entries), "withoutInfo": without_info}


def archive_reference_path(
    archive_root: Path,
    archived_path: Path,
    model_type: str,
    type_roots: dict[str, list[Path]],
) -> Optional[Path]:
    """Where a download from an archived row goes, as a reference path on D.

    It keeps the subfolders below the archive's type folder when D has them,
    else it uses the type root itself. The file does not have to exist yet. None
    when the row lies in no archive type folder, or would leave the D root.
    """
    for archive_folder, d_root in archive_type_folders(archive_root, type_roots).get(model_type, []):
        try:
            relative = archived_path.relative_to(archive_folder)
        except ValueError:
            continue
        if ".." in relative.parts:
            continue
        subfolder = d_root / relative.parent
        target_dir = subfolder if subfolder.is_dir() else d_root
        return target_dir / archived_path.name
    return None


def load_archive_items(data_dir: Path, archive_folder: str) -> list[dict]:
    """The saved archive check's items, or nothing when they are missing, outdated or for another folder."""
    data = _load_results(data_dir, archive_folder)
    items = data.get("items") if data else None
    return items if isinstance(items, list) else []


def load_archive_summary(data_dir: Path, archive_folder: str) -> dict:
    """The saved archive check's summary and when it ran, under the same rules as the items."""
    data = _load_results(data_dir, archive_folder)
    if not data or not isinstance(data.get("summary"), dict):
        return {}
    return {"checkedAt": data.get("checkedAt", ""), **data["summary"]}


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


def _load_results(data_dir: Path, archive_folder: str) -> dict | None:
    if not archive_folder:
        return None
    data = read_data_file(data_dir / ARCHIVE_RESULTS_FILENAME)
    if not data or data.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        return None
    saved = data.get("archiveFolder")
    if not isinstance(saved, str) or _folder_key(saved) != _folder_key(archive_folder):
        return None
    return data


def _folder_key(folder: str) -> str:
    return os.path.normcase(os.path.normpath(folder))
