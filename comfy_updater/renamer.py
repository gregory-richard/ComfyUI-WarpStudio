"""Model renamer dry run: collect library entries, sort them against live Civitai data, name them.

Code here only formats (``format_part``) and checks names; every word comes from
Civitai, the remembered decisions or the AI, and a failed check flags the row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import queue
import threading
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .civitai_client import CivitaiRequestError
from .constants import INFO_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .file_precision import read_precision
from .naming_ai import (
    PROMPT_VERSION,
    ModelAnswer,
    ModelInput,
    Slot,
    build_group_message,
    build_message,
    credit_lines,
    parse_answer,
    retry_message,
    split_group_answer,
)
from .naming_check import invented_words, maker_in_description
from .naming_scheme import NameParts, assemble, format_part, parse_stem, part_problems, stem_problems
from .path_resolver import list_model_files
from .rename_apply import file_identity
from .rename_store import AnswerCache, BaseCodeStore, DecisionStore
from .sidecar import read_json
from .updater_service import _dedupe_model_files, _stored_sha256

RENAME_TYPES = ("checkpoint", "lora", "unet", "embedding")
MAIN_FILE_TYPES = {"Model", "Pruned Model", "Diffusion Model"}
TYPE_FIT = {
    "lora": {"LORA", "LoCon", "DoRA", "LyCORIS"},
    "checkpoint": {"Checkpoint"},
    "unet": {"Checkpoint"},
    "embedding": {"TextualInversion"},
}


@dataclass
class Entry:
    path: Path
    folder: Path
    stem: str
    model_type: str
    metadata_only: bool
    info: dict
    model_id: str
    version_id: str
    sha256: str
    has_weights: bool
    # Size and mtime of ``path`` now; apply skips the entry when they changed.
    identity: dict = field(default_factory=dict)


@dataclass
class Placed:
    entry: Entry
    file: Optional[dict]
    file_key: str


@dataclass
class ModelGroup:
    model_id: str
    model: dict
    placed: List[Placed] = field(default_factory=list)
    slots_by_key: Dict[str, List[Placed]] = field(default_factory=dict)


def _identifier(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    return str(value).strip()


def collect_entries(roots: Dict[str, List[Path]]) -> List[Entry]:
    """Every entry with a Civitai identity in the renamed folders, weights or metadata-only."""
    rename_roots = {model_type: paths for model_type, paths in roots.items() if model_type in RENAME_TYPES}
    entries: List[Entry] = []
    # Overlapping roots (one folder registered under two types, nested roots) list a file twice.
    for item in _dedupe_model_files(list_model_files(rename_roots, include_sidecar_only=True)):
        path: Path = item["path"]
        metadata_only = bool(item.get("metadataOnly"))
        info = read_json(item["infoPath"])
        if not isinstance(info, dict):
            continue
        model_id = _identifier(info.get("modelId"))
        version_id = _identifier(info.get("id"))
        if not model_id or not version_id:
            continue
        if metadata_only:
            stem = path.name[: -len(INFO_SIDECAR_SUFFIX)]
        else:
            stem = path.name[: -len(path.suffix)] if path.suffix else path.name
        entries.append(
            Entry(
                path=path,
                folder=path.parent,
                stem=stem,
                model_type=item["modelType"],
                metadata_only=metadata_only,
                info=info,
                model_id=model_id,
                version_id=version_id,
                sha256=_stored_sha256(info),
                has_weights=not metadata_only,
                identity=_identity(path),
            )
        )
    return entries


def _identity(path: Path) -> dict:
    try:
        return file_identity(path)
    except OSError:
        return {}


def path_file_key(path: Path) -> str:
    return "path:" + str(path.absolute()).lower()


def _entry_key(entry: Entry) -> str:
    return str(entry.path).lower()


def _match_file(version: dict, sha256: str) -> Optional[dict]:
    """The live file this entry is: by SHA-256, or without a hash the primary/main file."""
    files = [item for item in (version.get("files") or []) if isinstance(item, dict)]
    if sha256:
        for item in files:
            hashes = item.get("hashes") if isinstance(item.get("hashes"), dict) else {}
            value = hashes.get("SHA256") or hashes.get("sha256") or ""
            if isinstance(value, str) and value.strip().lower() == sha256.lower():
                return item
        return None
    primary = next((item for item in files if item.get("primary") is True), None)
    if primary is not None:
        return primary
    return next((item for item in files if item.get("type") in MAIN_FILE_TYPES), None)


def _finished_row(entry: Entry, file_key: str, status: str, reason: str) -> dict:
    return {
        "key": _entry_key(entry),
        "modelId": entry.model_id,
        "versionId": entry.version_id,
        "fileKey": file_key,
        "modelPath": str(entry.path),
        "folder": str(entry.folder),
        "oldStem": entry.stem,
        "newStem": entry.stem,
        "parts": asdict(parse_stem(entry.stem)),
        "changed": [],
        "flags": [],
        "status": status,
        "reason": reason,
        "selected": False,
        **entry.identity,
    }


def _find_version(model: dict, version_id: str) -> Optional[dict]:
    for version in model.get("modelVersions") or []:
        if isinstance(version, dict) and _identifier(version.get("id")) == version_id:
            return version
    return None


def _split_duplicates(placed: List[Placed]) -> Tuple[List[Placed], List[dict]]:
    """Same version and same file in one folder: keep one, row the rest.

    The file is the matched live file, else the entry's own stored hash, so two
    different unmatched files of one version are never duplicates. Only entries
    with neither share the empty bucket.
    """
    buckets: Dict[tuple, List[Placed]] = {}
    for item in placed:
        if item.file is not None:
            file_id = "file:" + _identifier(item.file.get("id"))
        elif item.entry.sha256:
            file_id = "sha:" + item.entry.sha256.lower()
        else:
            file_id = ""
        bucket = (str(item.entry.folder).lower(), item.entry.version_id, file_id)
        buckets.setdefault(bucket, []).append(item)
    duplicates: Dict[int, Placed] = {}
    rows: List[dict] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        with_weights = [item for item in members if item.entry.has_weights]
        # Never offer real weights for removal while a metadata-only copy survives.
        candidates = with_weights or members
        survivor = min(candidates, key=lambda item: (item.entry.stem.lower(), item.entry.stem))
        for item in members:
            if item is survivor:
                continue
            duplicates[id(item)] = survivor
            row = _finished_row(item.entry, item.file_key, "duplicate", "duplicate")
            row["survivorKey"] = _entry_key(survivor.entry)
            rows.append(row)
    kept = [item for item in placed if id(item) not in duplicates]
    return kept, rows


def _slots(placed: List[Placed]) -> Dict[str, List[Placed]]:
    """One slot per version, or per version and file when one folder holds several of its files."""
    per_file_versions = set()
    seen: Dict[tuple, set] = {}
    for item in placed:
        if item.file is None:
            continue
        bucket = (item.entry.version_id, str(item.entry.folder).lower())
        seen.setdefault(bucket, set()).add(_identifier(item.file.get("id")))
    for (version_id, _folder), file_ids in seen.items():
        if len(file_ids) >= 2:
            per_file_versions.add(version_id)
    slots: Dict[str, List[Placed]] = {}
    for item in placed:
        key = item.entry.version_id
        if key in per_file_versions and item.file is not None:
            key = f"{key}:{_identifier(item.file.get('id'))}"
        slots.setdefault(key, []).append(item)
    return slots


def _by_model(entries: List[Entry]) -> Dict[str, List[Entry]]:
    """Entries per model, so each model is fetched once and sorted whole."""
    by_model: Dict[str, List[Entry]] = {}
    for entry in entries:
        by_model.setdefault(entry.model_id, []).append(entry)
    return by_model


def _classify_model(
    model_id: str, model_entries: List[Entry], client, decisions: DecisionStore
) -> Tuple[Optional[ModelGroup], List[dict]]:
    """Fetch one model and sort all its entries: the group to name (or None) and its finished rows."""
    model_entries = sorted(model_entries, key=lambda e: (str(e.folder).lower(), e.stem.lower(), e.stem))
    try:
        model = client.get_model(model_id, raise_on_error=True)
        if not isinstance(model, dict):
            raise CivitaiRequestError(f"Civitai returned no data for model {model_id}")
    except CivitaiRequestError as exc:
        not_found = getattr(exc, "status", None) == 404
        rows = []
        for entry in model_entries:
            status, reason = ("kept", "not-on-civitai") if not_found else ("error", str(exc))
            rows.append(_finished_row(entry, path_file_key(entry.path), status, reason))
        return None, rows
    try:
        return _sort_model(model_id, model, model_entries, decisions)
    except Exception as exc:  # noqa: BLE001 - one model's odd live data must not end the run
        reason = str(exc) or type(exc).__name__
        return None, [_finished_row(entry, path_file_key(entry.path), "error", reason) for entry in model_entries]


def classify(
    entries: List[Entry],
    client,
    decisions: DecisionStore,
    *,
    progress: Callable[[int, int, str], None],
    control=None,
) -> Tuple[List[ModelGroup], List[dict]]:
    """Fetch each model once and sort its entries into groups to name and finished rows."""
    by_model = _by_model(entries)
    groups: List[ModelGroup] = []
    rows: List[dict] = []
    total = len(by_model)
    for index, (model_id, model_entries) in enumerate(by_model.items()):
        if control is not None:
            control.wait_if_paused()
            if control.is_cancelled():
                break
        progress(index, total, f"Fetching model {model_id} from Civitai")
        group, model_rows = _classify_model(model_id, model_entries, client, decisions)
        rows.extend(model_rows)
        if group is not None:
            groups.append(group)
    else:
        progress(total, total, f"Fetched {total} models")
    return groups, rows


def _sort_model(
    model_id: str, model: dict, model_entries: List[Entry], decisions: DecisionStore
) -> Tuple[Optional[ModelGroup], List[dict]]:
    """One fetched model's entries: the group to name (or None) and its finished rows."""
    rows: List[dict] = []
    live_type = model.get("type")
    placed: List[Placed] = []
    for entry in model_entries:
        version = _find_version(model, entry.version_id)
        if version is None:
            rows.append(_finished_row(entry, path_file_key(entry.path), "kept", "release-gone"))
            continue
        matched = _match_file(version, entry.sha256)
        file_key = f"file:{_identifier(matched.get('id'))}" if matched and _identifier(matched.get("id")) else ""
        if not file_key:
            file_key = path_file_key(entry.path)
        if live_type not in TYPE_FIT.get(entry.model_type, set()):
            rows.append(_finished_row(entry, file_key, "kept", "type-mismatch"))
            continue
        if matched is not None and matched.get("type") not in MAIN_FILE_TYPES:
            rows.append(_finished_row(entry, file_key, "kept", "not-main-file"))
            continue
        if decisions.file(file_key).get("keep") is True:
            rows.append(_finished_row(entry, file_key, "kept", "kept-by-you"))
            continue
        placed.append(Placed(entry=entry, file=matched, file_key=file_key))

    placed, duplicate_rows = _split_duplicates(placed)
    rows.extend(duplicate_rows)
    if not placed:
        return None, rows
    return ModelGroup(model_id=model_id, model=model, placed=placed, slots_by_key=_slots(placed)), rows


# --- Naming -----------------------------------------------------------------

PRECISION_TYPES = ("checkpoint", "unet")


@dataclass
class _Pending:
    """One group on its way through the AI."""

    group: ModelGroup
    model_input: ModelInput
    slot_members: List[Tuple[Slot, List[Placed]]]
    message: str
    cache_key: str
    credit_text: str  # the description's credit lines, as the AI saw them
    source: str
    retried: bool = False


def _text(value) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _model_input(group: ModelGroup, base_codes: BaseCodeStore) -> Tuple[ModelInput, List[Tuple[Slot, List[Placed]]]]:
    model = group.model
    slot_members: List[Tuple[Slot, List[Placed]]] = []
    for index, (key, members) in enumerate(group.slots_by_key.items(), start=1):
        first = members[0]
        version = _find_version(model, first.entry.version_id) or {}
        file = first.file or {}
        slot = Slot(
            key=str(index),
            version_id=first.entry.version_id,
            file_id=_identifier(file.get("id")),
            version_name=_text(version.get("name")),
            base_model=_text(version.get("baseModel")),
            # Only a slot per file (key "<version>:<file>") shows its file name.
            file_name=_text(file.get("name")) if ":" in key else "",
        )
        slot_members.append((slot, members))
    slots = [slot for slot, _members in slot_members]
    new_bases: List[str] = []
    for slot in slots:
        if slot.base_model and base_codes.code(slot.base_model) is None and slot.base_model not in new_bases:
            new_bases.append(slot.base_model)
    description = model.get("description")
    model_input = ModelInput(
        model_id=group.model_id,
        creator=_text(_as_dict(model.get("creator")).get("username")),
        title=_text(model.get("name")),
        description=description if isinstance(description, str) else "",
        slots=slots,
        new_bases=new_bases,
    )
    return model_input, slot_members


def _prepare(group: ModelGroup, base_codes: BaseCodeStore, clip_name: str) -> _Pending:
    model_input, slot_members = _model_input(group, base_codes)
    message = build_message(model_input)
    slots = model_input.slots
    source = " ".join([model_input.title] + [s.version_name for s in slots] + [s.file_name for s in slots])
    return _Pending(
        group=group,
        model_input=model_input,
        slot_members=slot_members,
        message=message,
        cache_key=AnswerCache.key(
            group.model_id, {"message": message, "promptVersion": PROMPT_VERSION, "clipName": clip_name}
        ),
        # The maker check reads the same credit lines the AI saw.
        credit_text=credit_lines(model_input.description),
        source=source,
    )


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def _parts_flags(parts: NameParts) -> List[str]:
    """Unsafe-name problem codes for assembled parts."""
    flags = list(part_problems(parts))
    stem = assemble(parts)
    flags.extend(stem_problems(stem))
    back = parse_stem(stem)
    tail = " ".join(p for p in (parts.version, parts.precision) if p)
    if (back.creator, back.title, back.version, back.base) != (parts.creator, parts.title, tail, parts.base):
        # A part like "foo -" or "- foo" makes a new separator once assembled.
        flags.append("separator")
    if not all(_is_ascii(getattr(parts, name)) for name in ("creator", "title", "version", "precision", "base")):
        flags.append("non-ascii")
    return flags


def _invented_message(word: str) -> str:
    if word.lower().startswith("v") and word[1:2].isdigit():
        return f'"{word}" is not a version number in the source.'
    return f'"{word}" is not in the model title, version names or file names.'


def _flag_message(code: str, parts: NameParts) -> str:
    kind, _, part = code.partition(":")
    value = getattr(parts, part, "") if part else ""
    if kind == "empty":
        return f"The {part} is empty."
    if kind == "forbidden":
        return f'The {part} "{value}" has a character Windows does not allow in file names.'
    if kind == "separator":
        if part:
            return f'The {part} "{value}" contains " - ".'
        return 'A part starts or ends with "-", which makes a new " - " in the file name.'
    if kind == "non-ascii":
        return "Use only plain letters a-z, digits, spaces, dots and hyphens; no emojis or other letters."
    if kind == "reserved":
        return f'"{assemble(parts)}" is a name Windows reserves.'
    if kind == "trailing":
        return f'"{assemble(parts)}" ends with a dot or a space.'
    return code


def _creator_from_answer(pending: _Pending, answer: ModelAnswer) -> str:
    """The answer's original maker, unless it is just the uploader again."""
    original = format_part(answer.original)
    if original and original != format_part(pending.model_input.creator):
        return original
    return ""


def _answer_problems(pending: _Pending, answer: ModelAnswer) -> List[str]:
    """What is wrong with the AI's own answer, as sentences for the retry."""
    if answer.unparsed:
        return ['The answer had no "title:" line.']
    problems: List[str] = []
    for word in invented_words(pending.source, [answer.title, *answer.versions.values()]):
        problems.append(_invented_message(word))
    original = _creator_from_answer(pending, answer)
    if original and not maker_in_description(original, pending.credit_text):
        problems.append(f'"{answer.original}" is not named in the credit lines.')
    for name in pending.model_input.new_bases:
        if not format_part(answer.bases.get(name, "")):
            problems.append(f'There is no "base {name}:" line.')
    for slot, _members in pending.slot_members:
        probe = NameParts(
            creator=original or "creator",
            title=format_part(answer.title),
            version=format_part(answer.versions.get(slot.key, "")),
            precision="",
            base=format_part(answer.bases.get(slot.base_model, "")) or "base",
        )
        problems.extend(_flag_message(code, probe) for code in _parts_flags(probe))
    return list(dict.fromkeys(problems))


def _precision(placed: Placed, version: dict) -> str:
    entry = placed.entry
    if entry.model_type not in PRECISION_TYPES:
        return ""
    if entry.has_weights:
        return read_precision(entry.path)
    mains = [f for f in (version.get("files") or []) if isinstance(f, dict) and f.get("type") in MAIN_FILE_TYPES]
    if len(mains) != 1:
        return ""
    return _text(_as_dict(mains[0].get("metadata")).get("fp"))


def _is_official_file(placed: Placed) -> bool:
    if placed.entry.model_type not in PRECISION_TYPES or placed.file is None:
        return False
    name = _text(placed.file.get("name"))
    return bool(name) and os.path.splitext(name)[0].lower() == placed.entry.stem.lower()


def _changed(old_stem: str, parts: NameParts) -> List[str]:
    """Part names whose values differ from the current stem's (precision is read off the version tail)."""
    old = parse_stem(old_stem)
    old_tail = old.version
    if parts.precision and (old_tail == parts.precision or old_tail.endswith(" " + parts.precision)):
        old_version = old_tail[: -len(parts.precision)].rstrip()
        precision_changed = False
    else:
        old_version = old_tail
        precision_changed = bool(parts.precision)
    changed = [name for name in ("creator", "title") if getattr(old, name) != getattr(parts, name)]
    if old_version != parts.version:
        changed.append("version")
    if precision_changed:
        changed.append("precision")
    if old.base != parts.base:
        changed.append("base")
    return changed


def _group_rows(
    pending: _Pending, answer: ModelAnswer, decisions: DecisionStore, base_codes: BaseCodeStore
) -> List[dict]:
    group = pending.group
    model_decision = decisions.model(group.model_id)
    group_flags: List[str] = []
    if "creator" in model_decision:
        creator = model_decision["creator"]
    else:
        creator = _creator_from_answer(pending, answer)
        if creator:
            group_flags.append("maker-from-description")
            if not maker_in_description(creator, pending.credit_text):
                group_flags.append("maker-not-in-description")
        else:
            creator = pending.model_input.creator
    title_from_answer = "title" not in model_decision
    title = answer.title if title_from_answer else model_decision["title"]
    title_invented = invented_words(pending.source, [answer.title]) if title_from_answer and not answer.unparsed else []

    rows: List[dict] = []
    for slot, members in pending.slot_members:
        version_live = _find_version(group.model, slot.version_id) or {}
        for placed in members:
            entry = placed.entry
            flags = list(group_flags)
            version = decisions.file(placed.file_key).get("version")
            if version is None:
                version = decisions.version(entry.version_id)
            version_from_answer = version is None
            if version_from_answer:
                version = answer.versions.get(slot.key, "")
            if answer.unparsed and (title_from_answer or version_from_answer):
                flags.append("unparsed")
            invented = list(title_invented)
            if version_from_answer and not answer.unparsed:
                invented.extend(invented_words(pending.source, [version]))
            flags.extend(f"invented:{word}" for word in invented)
            code = base_codes.code(slot.base_model) if slot.base_model else None
            if code is None:
                code = answer.bases.get(slot.base_model, "")
                flags.append("new-base-code")
            parts = NameParts(
                creator=format_part(creator),
                title=format_part(title),
                version=format_part(version),
                precision=format_part(_precision(placed, version_live)),
                base=format_part(code),
            )
            flags.extend(_parts_flags(parts))
            if _is_official_file(placed):
                flags.append("official-file")
            if placed.file is None and entry.sha256:
                flags.append("file-not-in-release")
            new_stem = assemble(parts)
            status = "unchanged" if new_stem == entry.stem else "change"
            flags = list(dict.fromkeys(flags))
            rows.append(
                {
                    "key": _entry_key(entry),
                    "modelId": entry.model_id,
                    "versionId": entry.version_id,
                    "fileKey": placed.file_key,
                    "modelPath": str(entry.path),
                    "folder": str(entry.folder),
                    "oldStem": entry.stem,
                    "newStem": new_stem,
                    "parts": asdict(parts),
                    "changed": _changed(entry.stem, parts),
                    "flags": flags,
                    "status": status,
                    "reason": "",
                    "selected": status == "change" and not flags,
                    # For re-assembling after an edit and for the sidecar title after apply.
                    "baseModel": slot.base_model,
                    "liveTitle": pending.model_input.title,
                    **entry.identity,
                }
            )
    return rows


def _disk_stems(folder: Path) -> Set[str]:
    """Lowercased stems of the weights files and info sidecars in a folder."""
    stems: Set[str] = set()
    try:
        names = [item.name for item in folder.iterdir() if item.is_file()]
    except OSError:
        return stems
    for name in names:
        lower = name.lower()
        if lower.endswith(INFO_SIDECAR_SUFFIX):
            stems.add(lower[: -len(INFO_SIDECAR_SUFFIX)])
            continue
        suffix = os.path.splitext(lower)[1]
        if suffix in SUPPORTED_MODEL_EXTENSIONS:
            stems.add(lower[: -len(suffix)])
    return stems


def _add_flag(row: dict, flag: str) -> None:
    if flag not in row["flags"]:
        row["flags"].append(flag)
    row["selected"] = False


class _FolderCheck:
    """Collisions between new names in one folder, and with files outside the rename set.

    A file on disk counts as outside the rename set once its entry is known to keep
    its name. Entries not decided yet (still being fetched) count as renamed; when
    one turns out to stay, rows already placed on its name are flagged in place.
    """

    def __init__(self, entries: Iterable[Entry]) -> None:
        self._renaming: Dict[str, Dict[str, int]] = {}  # folder -> stem -> entries that may still be renamed
        for entry in entries:
            stems = self._renaming.setdefault(str(entry.folder).lower(), {})
            stem = entry.stem.lower()
            stems[stem] = stems.get(stem, 0) + 1
        self._disk: Dict[str, Set[str]] = {}
        self._by_new: Dict[Tuple[str, str], List[dict]] = {}

    @classmethod
    def for_groups(cls, groups: List[ModelGroup]) -> "_FolderCheck":
        return cls(placed.entry for group in groups for placed in group.placed)

    def stays(self, entry: Entry) -> None:
        """This entry keeps its name: its file is now outside the rename set."""
        folder = str(entry.folder).lower()
        stems = self._renaming.get(folder, {})
        stem = entry.stem.lower()
        if stems.get(stem, 0) <= 0:
            return
        stems[stem] -= 1
        if stems[stem] == 0:
            # Its file was listed from this folder, so it is on disk.
            for row in self._by_new.get((folder, stem), []):
                _add_flag(row, "exists")

    def place(self, row: dict) -> None:
        """Flag the row; an earlier row it collides with is flagged too, in place."""
        folder = row["folder"].lower()
        new = row["newStem"].lower()
        if folder not in self._disk:
            self._disk[folder] = _disk_stems(Path(row["folder"]))
        if new in self._disk[folder] and self._renaming.get(folder, {}).get(new, 0) <= 0:
            _add_flag(row, "exists")
        peers = self._by_new.setdefault((folder, new), [])
        peers.append(row)
        if len(peers) > 1:
            for peer in peers:
                _add_flag(peer, "collision")


def _error_rows(group: ModelGroup, exc: Exception) -> List[dict]:
    return [_finished_row(p.entry, p.file_key, "error", str(exc) or type(exc).__name__) for p in group.placed]


# Models per Generate Text call: the long system prompt is read once for all of them.
# Measured: K=3 scored within 2 cases of K=1 on both regression sets at 0.49 s/model (K=1: ~1.04);
# K=4 did not (31/50 against 39/50).
GROUP_SIZE = 3
# Answer room per model in a call (a one-model answer is well under this).
ANSWER_TOKENS_PER_MODEL = 160


class _Namer:
    """Asks the AI for the groups given to ``add``, a batch of calls at a time.

    Every group is checked, retried (alone, with the single-model retry message)
    and cached on its own, whatever ``group_size`` put it in a call with.
    """

    def __init__(
        self,
        generator,
        *,
        decisions: DecisionStore,
        base_codes: BaseCodeStore,
        cache: AnswerCache,
        clip_name: str,
        folders: "_FolderCheck",
        emit: Callable[[dict], None],
        batch_size: int,
        group_size: int,
        lock=None,
    ) -> None:
        self.generator = generator
        self.decisions = decisions
        self.base_codes = base_codes
        self.cache = cache
        self.clip_name = clip_name
        self.folders = folders
        self.emit = emit
        self.batch_size = max(1, int(batch_size))
        self.group_size = max(1, int(group_size))
        # Held while rows are placed and emitted: the fetch side emits rows too.
        self.lock = lock if lock is not None else threading.RLock()
        self.done = 0  # groups whose rows are emitted
        self._new: List[_Pending] = []
        self._retries: List[Tuple[_Pending, str]] = []

    def _emit_rows(self, rows: List[dict]) -> None:
        with self.lock:
            for row in rows:
                if row["status"] in ("change", "unchanged"):
                    self.folders.place(row)
                self.emit(row)
            self.done += 1

    def _finish(self, pending: _Pending, answer: ModelAnswer) -> None:
        try:
            rows = _group_rows(pending, answer, self.decisions, self.base_codes)
        except Exception as exc:  # noqa: BLE001 - one model's odd data must not end the run
            rows = _error_rows(pending.group, exc)
        self._emit_rows(rows)

    def add(self, group: ModelGroup) -> None:
        """A cached answer that still passes the checks names the group now; else it waits for the AI."""
        try:
            pending = _prepare(group, self.base_codes, self.clip_name)
            cached = self.cache.get(pending.cache_key)
            answer = None
            if cached is not None and isinstance(cached.get("text"), str):
                answer = parse_answer(cached["text"], pending.model_input)
                if _answer_problems(pending, answer):
                    answer = None  # the checks changed since it was cached: ask again
        except Exception as exc:  # noqa: BLE001
            self._emit_rows(_error_rows(group, exc))
            return
        if answer is not None:
            self._finish(pending, answer)
        else:
            self._new.append(pending)

    def has_work(self) -> bool:
        return bool(self._new or self._retries)

    def has_full_call(self) -> bool:
        """Enough waiting for at least one full call (a retry is always a full call)."""
        return bool(self._retries) or len(self._new) >= self.group_size

    def run_batch(self) -> None:
        """One queued ComfyUI job: retries first, each alone, then the new groups ``group_size`` per call."""
        calls: List[Tuple[List[_Pending], str]] = []
        while self._retries and len(calls) < self.batch_size:
            pending, message = self._retries.pop(0)
            calls.append(([pending], message))
        while self._new and len(calls) < self.batch_size:
            chunk, self._new = self._new[: self.group_size], self._new[self.group_size:]
            calls.append((chunk, build_group_message([pending.model_input for pending in chunk])))
        if not calls:
            return
        texts = self.generator.generate(
            [message for _chunk, message in calls],
            max_lengths=[len(chunk) * ANSWER_TOKENS_PER_MODEL for chunk, _message in calls],
        )
        for index, (chunk, _message) in enumerate(calls):
            # A missing output comes back as "": unparsed, so retried, flagged, never cached.
            text = texts[index] if index < len(texts) and isinstance(texts[index], str) else ""
            sections = [text] if len(chunk) == 1 else split_group_answer(text, len(chunk))
            for pending, section in zip(chunk, sections):
                self._check(pending, section)

    def _check(self, pending: _Pending, text: str) -> None:
        try:
            answer = parse_answer(text, pending.model_input)
            problems = _answer_problems(pending, answer)
        except Exception as exc:  # noqa: BLE001
            self._emit_rows(_error_rows(pending.group, exc))
            return
        if problems and not pending.retried:
            pending.retried = True
            # Retries go into the very next batch.
            self._retries.append((pending, retry_message(pending.model_input, problems)))
            return
        if not problems:
            # Cached per model, as this model's own answer: reusable whatever the group size.
            self.cache.put(pending.cache_key, {"text": text})
        self._finish(pending, answer)


def _stopped(control) -> bool:
    if control is None:
        return False
    control.wait_if_paused()
    return control.is_cancelled()


def name_groups(
    groups: List[ModelGroup],
    generator,
    *,
    decisions: DecisionStore,
    base_codes: BaseCodeStore,
    cache: AnswerCache,
    clip_name: str,
    progress: Callable[[int, int, str], None],
    emit: Callable[[dict], None],
    control,
    batch_size: int = 8,
    group_size: int = GROUP_SIZE,
) -> None:
    """Ask the AI per model (cached), check every answer, and emit each group's rows once decided.

    A row that later collides with another group's row is flagged in place, so the
    row dicts already emitted are updated too. A ``NamingModelError`` from the
    generator propagates: rows already emitted stay, unanswered groups get none.
    """
    total = len(groups)
    namer = _Namer(
        generator,
        decisions=decisions,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        folders=_FolderCheck.for_groups(groups),
        emit=emit,
        batch_size=batch_size,
        group_size=group_size,
    )
    for group in groups:
        if _stopped(control):
            return
        namer.add(group)
    while namer.has_work():
        if _stopped(control):
            return
        progress(namer.done, total, f"Naming models with the AI ({namer.done} of {total} done)")
        namer.run_batch()
    progress(total, total, f"Named {total} models")


def _summary(entries: List[Entry], rows: List[dict]) -> dict:
    summary = {"entries": len(entries), "change": 0, "unchanged": 0, "kept": 0, "duplicate": 0, "flagged": 0, "error": 0}
    for row in rows:
        status = row.get("status")
        if status in summary:
            summary[status] += 1
        if status in ("change", "unchanged") and row.get("flags"):
            summary["flagged"] += 1
    return summary


def _local_base_pairs(entries: List[Entry]) -> List[Tuple[str, str]]:
    """Each entry's base model from its own sidecar, with its current stem: no network needed."""
    pairs: List[Tuple[str, str]] = []
    for entry in entries:
        base = _text(entry.info.get("baseModel")).strip()
        if base:
            pairs.append((base, entry.stem))
    return pairs


# How long the naming side waits for the fetch side before it looks at cancel/pause again.
_POLL_SECONDS = 0.1


def run_dry_run(
    roots: Dict[str, List[Path]],
    client,
    generator,
    *,
    decisions: DecisionStore,
    base_codes: BaseCodeStore,
    cache: AnswerCache,
    clip_name: str,
    progress: Callable[[int, int, str], None],
    emit: Callable[[dict], None],
    control,
    batch_size: int = 8,
    group_size: int = GROUP_SIZE,
) -> Tuple[dict, List[dict]]:
    """The dry-run job body: collect, seed base codes, then fetch and name at the same time.

    A fetch thread classifies one model at a time, emits its finished rows (kept,
    duplicate, error) at once and hands its group to this thread, which names the
    groups as they come, ``group_size`` per call. Both stop on cancel; an error on
    the fetch thread ends the run as the job's error; a ``NamingModelError`` stops
    both and keeps the rows already emitted. Rows are only emitted under ``lock``,
    and never after the run returned.
    """
    rows: List[dict] = []
    lock = threading.RLock()
    stop = threading.Event()  # set under ``lock`` when the run ends: the fetch side emits nothing after it

    def keep(row: dict) -> None:
        rows.append(row)
        emit(row)

    entries = collect_entries(roots)
    if _stopped(control):
        return _summary(entries, rows), rows
    # Before the first question, so no model is named with a code the AI made up.
    base_codes.seed(_local_base_pairs(entries))

    by_model = _by_model(entries)
    total = len(by_model)
    folders = _FolderCheck(entries)
    namer = _Namer(
        generator,
        decisions=decisions,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        folders=folders,
        emit=keep,
        batch_size=batch_size,
        group_size=group_size,
        lock=lock,
    )
    counts = {"fetched": 0, "without_group": 0}

    def report() -> None:
        # Under the lock, and never once the run has ended: a late report from the fetch
        # side must not overwrite the job's last progress.
        with lock:
            if stop.is_set():
                return
            done = namer.done + counts["without_group"]
            progress(done, total, f"Named {done} of {total} · fetched {counts['fetched']} of {total}")

    handoff: "queue.Queue[Tuple[str, object]]" = queue.Queue()

    def fetch() -> None:
        try:
            for model_id, model_entries in by_model.items():
                if stop.is_set() or _stopped(control):
                    break
                group, model_rows = _classify_model(model_id, model_entries, client, decisions)
                renamed = {id(placed.entry) for placed in group.placed} if group is not None else set()
                with lock:
                    if stop.is_set():
                        break
                    for entry in model_entries:
                        if id(entry) not in renamed:
                            folders.stays(entry)
                    for row in model_rows:
                        keep(row)
                    counts["fetched"] += 1
                    if group is None:
                        counts["without_group"] += 1
                if group is not None:
                    handoff.put(("group", group))
                report()
        except BaseException as exc:  # noqa: BLE001 - handed to the naming side, which raises it
            handoff.put(("error", exc))
            return
        handoff.put(("done", None))

    fetching = True

    def take(wait: bool) -> None:
        """Hand every fetched group to the namer; with ``wait``, wait a moment for the first."""
        nonlocal fetching
        while fetching:
            try:
                kind, value = handoff.get(timeout=_POLL_SECONDS) if wait else handoff.get_nowait()
            except queue.Empty:
                return
            wait = False
            if kind == "error":
                raise value  # type: ignore[misc]
            if kind == "done":
                fetching = False
            else:
                namer.add(value)  # type: ignore[arg-type]

    worker = threading.Thread(target=fetch, name="renamer-fetch", daemon=True)
    worker.start()
    finished = False
    try:
        while not _stopped(control):
            # Wait for more only while there is not yet a full call to ask.
            take(wait=fetching and not namer.has_full_call())
            if namer.has_full_call() or (namer.has_work() and not fetching):
                report()
                namer.run_batch()
                report()
            elif not fetching and not namer.has_work():
                finished = True
                break
    finally:
        with lock:
            stop.set()
        # A cancelled fetch ends within a request. After a NamingModelError or a fetch error
        # one Civitai request may still be in flight and outlive the run: the client's
        # should_stop is the job's cancel flag, not ``stop``. It emits nothing once ``stop``
        # is set, so don't wait long for it.
        worker.join(timeout=1.0)
    if finished:
        progress(total, total, f"Named {total} models · fetched {total} of {total}")
    # Only now: with ``stop`` set no row can be added, so the summary matches the rows returned.
    with lock:
        return _summary(entries, rows), rows
