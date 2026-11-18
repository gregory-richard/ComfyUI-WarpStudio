"""Where an Updates download goes and what it is called.

The renamer names the new release as a dry run would (with the AI naming
model), and the file lands in its base's home folder. Without a usable AI name
it keeps Civitai's file name. A name already taken, or a Civitai file name that
is not a plain file name, is refused, and nothing goes outside the model roots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .constants import INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .model_download import DownloadError, file_sha256
from .naming_check import needs_review
from .naming_scheme import FORBIDDEN_CHARS, RESERVED_STEMS
from .rename_folders import _norm, home_folders
from .renamer import RENAME_TYPES, Entry, _entry_key, _sort_model, name_groups

OUTSIDE_ROOTS_MESSAGE = "That folder isn't one of your model folders."
_TAKEN_SUFFIXES = (*SUPPORTED_MODEL_EXTENSIONS, INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX)


@dataclass
class Target:
    folder: Path
    stem: str
    suffix: str
    # The stem is the renamer's name, not Civitai's file name.
    named: bool

    @property
    def path(self) -> Path:
        # Never ``with_suffix``: stems contain dots.
        return self.folder / (self.stem + self.suffix)


def inside_roots(path: Path, roots: list[Path]) -> bool:
    """Whether the path is one of the roots or below one."""
    here = _norm(os.path.abspath(str(path)))
    for root in roots:
        top = _norm(os.path.abspath(str(root)))
        if here == top or here.startswith(top.rstrip(os.sep) + os.sep):
            return True
    return False


def stem_taken(folder: Path, stem: str) -> bool:
    """The folder holds a weights file, an info or a preview of that stem, in any case."""
    try:
        names = os.listdir(folder)
    except (FileNotFoundError, NotADirectoryError):
        return False
    wanted = {(stem + suffix).lower() for suffix in _TAKEN_SUFFIXES}
    return any(name.lower() in wanted for name in names)


def base_folder(type_roots: list[Path], reference_folder: Path, code: str) -> Path | None:
    """The home folder of a base code beside the reference folder (or below it), else None.

    Homes are judged as the dry run judges them, from the names of the files on
    disk: one row per weights file or info file (the two of one stem counted once).
    """
    rows: list[dict] = []
    walked = set()
    for root in type_roots:
        for folder, _dirs, names in os.walk(os.path.normpath(str(root))):
            if _norm(folder) in walked:
                continue  # nested roots list a folder twice
            walked.add(_norm(folder))
            stems = {}
            for name in names:
                lower = name.lower()
                if lower.endswith(INFO_SIDECAR_SUFFIX):
                    stem = name[: -len(INFO_SIDECAR_SUFFIX)]
                elif os.path.splitext(lower)[1] in SUPPORTED_MODEL_EXTENSIONS:
                    stem = os.path.splitext(name)[0]
                else:
                    continue
                stems.setdefault(stem.lower(), stem)
            rows.extend(
                {"folder": folder, "oldStem": stem, "status": "kept"} for stem in stems.values()
            )
    homes = home_folders(rows)
    for parent in (reference_folder.parent, reference_folder):
        home = homes.get((_norm(str(parent)), code))
        if home:
            return Path(home)
    return None


class _Stop:
    """The renamer's job control, over a should-stop callable."""

    def __init__(self, should_stop: Callable[[], bool]) -> None:
        self._should_stop = should_stop

    def wait_if_paused(self) -> None:
        pass

    def is_cancelled(self) -> bool:
        return bool(self._should_stop())


def _reference_stem(reference: Path) -> str:
    """A weights file's name minus its suffix, an info file's name minus ``.civitai.info``."""
    name = reference.name
    if name.lower().endswith(INFO_SIDECAR_SUFFIX):
        return name[: -len(INFO_SIDECAR_SUFFIX)]
    return name[: -len(reference.suffix)] if reference.suffix else name


def ai_stem(
    model_id: str,
    version: dict,
    file: dict,
    *,
    model_type: str,
    reference: Path,
    client,
    generator,
    decisions,
    base_codes,
    cache,
    clip_name: str,
    should_stop: Callable[[], bool],
) -> tuple[str, str] | None:
    """(new stem, base code) for one release as a dry run names it, else None.

    None when the renamer skips the type or the name needs a look. The new file
    stands in under the name the user gave the version they have, so the renamer
    reads a creator from it when Civitai has none and judges an official file by
    it. A Civitai or naming-model error propagates.
    """
    if model_type not in RENAME_TYPES:
        return None
    model_id = str(model_id)
    folder = reference.parent
    stem = _reference_stem(reference)
    suffix = os.path.splitext(str(file.get("name") or ""))[1]
    entry = Entry(
        path=folder / (stem + suffix),
        folder=folder,
        stem=stem,
        model_type=model_type,
        metadata_only=False,
        info=version,
        model_id=model_id,
        version_id=str(version["id"]),
        sha256=file_sha256(file),
        # No file yet: the precision comes from Civitai's file metadata.
        has_weights=False,
        identity={},
        found={},
    )
    model = client.get_model(model_id, raise_on_error=True)
    if not isinstance(model, dict):
        return None
    group, _rows = _sort_model(model_id, model, [entry], decisions)
    if group is None:
        return None
    rows: list[dict] = []
    name_groups(
        [group],
        generator,
        decisions=decisions,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        progress=lambda *args: None,
        emit=rows.append,
        control=_Stop(should_stop),
    )
    key = _entry_key(entry)
    row = next((row for row in rows if row.get("key") == key), None)
    if row is None or row.get("status") not in ("change", "unchanged"):
        return None
    if needs_review(row.get("flags")):
        return None
    return row["newStem"], row["parts"]["base"]


def _bare_name(name: str) -> bool:
    """A plain file name: no folder, no character Windows refuses, not a device name."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return False
    if any(ch in FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return False
    stem = os.path.splitext(name)[0]
    return bool(stem.strip()) and stem.split(".")[0].strip().lower() not in RESERVED_STEMS


def choose_target(
    file: dict,
    *,
    reference_folder: Path,
    type_roots: list[Path],
    named: tuple[str, str] | None,
    fallback_code: str,
) -> Target:
    """Where the release's file goes: the AI's name if free, else Civitai's, in its base's home.

    A base with no home there keeps the file beside the old version.
    """
    name = file.get("name") if isinstance(file, dict) else None
    name = name if isinstance(name, str) else ""
    civitai_stem, suffix = os.path.splitext(name)
    if not _bare_name(name) or suffix.lower() not in SUPPORTED_MODEL_EXTENSIONS:
        raise DownloadError(f"Civitai's file name can't be used: {name}")
    if not inside_roots(reference_folder, type_roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    code = named[1] if named and named[1] else fallback_code
    folder = (base_folder(type_roots, reference_folder, code) if code else None) or reference_folder
    if not inside_roots(folder, type_roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    if named and _bare_name(named[0] + suffix) and not stem_taken(folder, named[0]):
        return Target(folder, named[0], suffix, True)
    if not stem_taken(folder, civitai_stem):
        return Target(folder, civitai_stem, suffix, False)
    raise DownloadError(f"A file named {name} is already there.")
