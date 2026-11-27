"""Where an Updates download goes and what it is called.

The renamer names the new release as a dry run would (with the AI naming
model), and the file lands in its base's home folder. A base with no code yet
takes the AI's code. Without a usable AI name it keeps Civitai's file name. A
name already taken, or a Civitai file name that is not a plain file name, is
refused, and nothing goes outside the model roots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from .constants import INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .model_download import DownloadError, file_sha256
from .naming_check import needs_review
from .naming_scheme import FORBIDDEN_CHARS, RESERVED_STEMS, NameParts, assemble
from .path_identity import path_key
from .rename_folders import _norm, home_folders
from .renamer import RENAME_TYPES, Entry, _sort_model, name_groups

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
    """The folder holds a weights file, an info or a preview of that stem (``path_key``; the suffix in any case).

    The drive is asked as well: a case-insensitive drive mounted on Linux has
    ``Foo.civitai.info`` at ``foo.civitai.info``, and the info file is written there.
    """
    try:
        names = os.listdir(folder)
    except (FileNotFoundError, NotADirectoryError):
        return False
    wanted = path_key(stem)
    if any(path_key(name[: len(stem)]) == wanted and name[len(stem):].lower() in _TAKEN_SUFFIXES for name in names):
        return True
    return any(os.path.lexists(Path(folder) / (stem + suffix)) for suffix in _TAKEN_SUFFIXES)


class AiName(NamedTuple):
    stem: str
    code: str
    # The base model whose code is the AI's, not yet in the table; "" for a known base.
    new_base: str


def base_folder(type_roots: list[Path], reference_folder: Path, code: str) -> Path | None:
    """The home folder of a base code beside the reference folder (or below it), else None."""
    return _home_beside(_homes(type_roots), reference_folder, code)


def _homes(type_roots: list[Path]) -> dict:
    """{(parent folder, base code): home folder}, judged as the dry run judges them.

    From the names of the files on disk: one row per weights file or info file
    (the two of one stem counted once).
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
                stems.setdefault(path_key(stem), stem)

            rows.extend(
                {"folder": folder, "oldStem": stem, "status": "kept"} for stem in stems.values()
            )
    return home_folders(rows)


def _home_beside(homes: dict, reference_folder: Path, code: str) -> Path | None:
    for parent in (reference_folder.parent, reference_folder):
        home = homes.get((_norm(str(parent)), code))
        if home:
            return Path(home)
    return None


def _new_home(homes: dict, reference_folder: Path, code: str) -> Path | None:
    """A folder named after a base with no home yet, beside the old version's folder when that
    folder is named after its own base (a library kept one folder per base), else None."""
    if not _bare_name(code):
        return None
    here = _norm(str(reference_folder))
    for (_parent, home_code), home in homes.items():
        if _norm(home) == here and home_code.lower() == reference_folder.name.lower():
            return reference_folder.parent / code
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


class NamedRelease(NamedTuple):
    """One release's name as the renamer gave it, in parts."""

    parts: NameParts
    # The row's base model; its code is ``parts.base``.
    base_model: str
    # ``base_model`` when the code is the AI's (not yet in the table), else "".
    new_base: str

    @property
    def stem(self) -> str:
        return assemble(self.parts)


class _OwnNames:
    """The user's creator and title for a model stand in for its saved decisions, so the AI
    decides only the version and a new base's code."""

    def __init__(self, decisions, own: tuple[str, str]) -> None:
        self._decisions = decisions
        self._own = own

    def model(self, model_id) -> dict:
        return {**self._decisions.model(model_id), "creator": self._own[0], "title": self._own[1]}

    def file(self, file_key: str) -> dict:
        return self._decisions.file(file_key)

    def version(self, version_id):
        return self._decisions.version(version_id)


def ai_stems(
    model_id: str,
    model: dict,
    releases: list[tuple[dict, dict]],
    *,
    model_type: str,
    reference: Path,
    own: tuple[str, str] | None,
    generator,
    decisions,
    base_codes,
    cache,
    clip_name: str,
    should_stop: Callable[[], bool],
) -> dict[str, NamedRelease]:
    """The names of a model's releases as a dry run gives them, keyed by version id.

    One AI call names them all (more only on a retry). Every release stands in
    for a file beside the reference, under the name the user gave the version
    they have, so the renamer reads a creator from it when Civitai has none and
    judges an official file by it. A release gets no name when the renamer skips
    the type, the name needs a look, a new base has no code, the name is the
    reference's own, or another release has the same name. ``own`` is the user's
    (creator, title) for the model. A Civitai or naming-model error propagates.
    """
    if model_type not in RENAME_TYPES or not releases:
        return {}
    model_id = str(model_id)
    folder = reference.parent
    stem = _reference_stem(reference)
    entries: list[Entry] = []
    for version, file in releases:
        version_id = str(version["id"])
        suffix = os.path.splitext(str(file.get("name") or ""))[1]
        entries.append(
            Entry(
                # One path per release: the rows are told apart by it.
                path=folder / f"{stem}.{version_id}{suffix}",
                folder=folder,
                stem=stem,
                model_type=model_type,
                metadata_only=False,
                info=version,
                model_id=model_id,
                version_id=version_id,
                sha256=file_sha256(file),
                # No file yet: the precision comes from Civitai's file metadata.
                has_weights=False,
                identity={},
                found={},
            )
        )
    decided = _OwnNames(decisions, own) if own else decisions
    group, _rows = _sort_model(model_id, model, entries, decided)
    if group is None:
        return {}
    rows: list[dict] = []
    name_groups(
        [group],
        generator,
        decisions=decided,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        progress=lambda *args: None,
        emit=rows.append,
        control=_Stop(should_stop),
    )
    names: dict[str, NamedRelease] = {}
    for row in rows:
        if row.get("status") != "change":
            continue
        flags = list(row.get("flags") or [])
        new_base = "new-base-code" in flags
        if needs_review([flag for flag in flags if flag != "new-base-code"]):
            continue
        parts = row["parts"]
        if new_base and not parts["base"]:
            continue
        base_model = str(row.get("baseModel") or "")
        names[str(row["versionId"])] = NamedRelease(
            NameParts(**parts), base_model, base_model if new_base else ""
        )
    return names


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
) -> AiName | None:
    """The new stem and base code for one release as a dry run names it, else None.

    None when the renamer skips the type or the name needs a look. A base with
    no code yet is no reason to look: the AI's code is used (``new_base`` says
    so), unless it gave none. See ``ai_stems``. A Civitai or naming-model error
    propagates.
    """
    if model_type not in RENAME_TYPES:
        return None
    model = client.get_model(str(model_id), raise_on_error=True)
    if not isinstance(model, dict):
        return None
    named = ai_stems(
        model_id,
        model,
        [(version, file)],
        model_type=model_type,
        reference=reference,
        own=None,
        generator=generator,
        decisions=decisions,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        should_stop=should_stop,
    ).get(str(version["id"]))
    return AiName(named.stem, named.parts.base, named.new_base) if named else None


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

    A base with no home there gets a folder of its own beside the old version's
    when that one is named after its base; otherwise the file stays beside the
    old version. The folder is not made here.
    """
    name = file.get("name") if isinstance(file, dict) else None
    name = name if isinstance(name, str) else ""
    civitai_stem, suffix = os.path.splitext(name)
    if not _bare_name(name) or suffix.lower() not in SUPPORTED_MODEL_EXTENSIONS:
        raise DownloadError(f"Civitai's file name can't be used: {name}")
    if not inside_roots(reference_folder, type_roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    code = named[1] if named and named[1] else fallback_code
    folder = reference_folder
    if code:
        homes = _homes(type_roots)
        new_home = _new_home(homes, reference_folder, code)
        if new_home is not None and not inside_roots(new_home, type_roots):
            new_home = None  # roots that are per-base folders: a sibling of one is outside them all
        folder = _home_beside(homes, reference_folder, code) or new_home or folder
    if not inside_roots(folder, type_roots):
        raise DownloadError(OUTSIDE_ROOTS_MESSAGE)
    if named and _bare_name(named[0] + suffix) and not stem_taken(folder, named[0]):
        return Target(folder, named[0], suffix, True)
    if not stem_taken(folder, civitai_stem):
        return Target(folder, civitai_stem, suffix, False)
    raise DownloadError(f"A file named {name} is already there.")
