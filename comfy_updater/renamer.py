"""Model renamer dry run: collect library entries, sort them against live Civitai data, name them.

Code here only formats (``format_part``) and checks names; every word comes from
Civitai, the remembered decisions or the AI, and a failed check flags the row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .civitai_client import CivitaiRequestError
from .constants import INFO_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .file_precision import read_precision
from .naming_ai import (
    PROMPT_VERSION,
    ModelAnswer,
    ModelInput,
    Slot,
    build_message,
    credit_lines,
    parse_answer,
    retry_message,
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


def classify(
    entries: List[Entry],
    client,
    decisions: DecisionStore,
    *,
    progress: Callable[[int, int, str], None],
    control=None,
) -> Tuple[List[ModelGroup], List[dict]]:
    """Fetch each model once and sort its entries into groups to name and finished rows."""
    by_model: Dict[str, List[Entry]] = {}
    for entry in entries:
        by_model.setdefault(entry.model_id, []).append(entry)

    groups: List[ModelGroup] = []
    rows: List[dict] = []
    total = len(by_model)
    for index, (model_id, model_entries) in enumerate(by_model.items()):
        if control is not None:
            control.wait_if_paused()
            if control.is_cancelled():
                break
        progress(index, total, f"Fetching model {model_id} from Civitai")
        model_entries = sorted(model_entries, key=lambda e: (str(e.folder).lower(), e.stem.lower(), e.stem))

        try:
            model = client.get_model(model_id, raise_on_error=True)
            if not isinstance(model, dict):
                raise CivitaiRequestError(f"Civitai returned no data for model {model_id}")
        except CivitaiRequestError as exc:
            not_found = getattr(exc, "status", None) == 404
            for entry in model_entries:
                status, reason = ("kept", "not-on-civitai") if not_found else ("error", str(exc))
                rows.append(_finished_row(entry, path_file_key(entry.path), status, reason))
            continue

        try:
            group, model_rows = _sort_model(model_id, model, model_entries, decisions)
        except Exception as exc:  # noqa: BLE001 - one model's odd live data must not end the run
            reason = str(exc) or type(exc).__name__
            rows.extend(_finished_row(entry, path_file_key(entry.path), "error", reason) for entry in model_entries)
            continue
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
    """Collisions between new names in one folder, and with files outside the rename set."""

    def __init__(self, groups: List[ModelGroup]) -> None:
        self._rename_stems: Dict[str, Set[str]] = {}
        for group in groups:
            for placed in group.placed:
                folder = str(placed.entry.folder).lower()
                self._rename_stems.setdefault(folder, set()).add(placed.entry.stem.lower())
        self._disk: Dict[str, Set[str]] = {}
        self._by_new: Dict[Tuple[str, str], List[dict]] = {}

    def place(self, row: dict) -> None:
        """Flag the row; an earlier row it collides with is flagged too, in place."""
        folder = row["folder"].lower()
        new = row["newStem"].lower()
        if folder not in self._disk:
            self._disk[folder] = _disk_stems(Path(row["folder"]))
        if new in self._disk[folder] - self._rename_stems.get(folder, set()):
            _add_flag(row, "exists")
        peers = self._by_new.setdefault((folder, new), [])
        peers.append(row)
        if len(peers) > 1:
            for peer in peers:
                _add_flag(peer, "collision")


def _error_rows(group: ModelGroup, exc: Exception) -> List[dict]:
    return [_finished_row(p.entry, p.file_key, "error", str(exc) or type(exc).__name__) for p in group.placed]


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
) -> None:
    """Ask the AI per model (cached), check every answer, and emit each group's rows once decided.

    A row that later collides with another group's row is flagged in place, so the
    row dicts already emitted are updated too. A ``NamingModelError`` from the
    generator propagates: rows already emitted stay, unanswered groups get none.
    """
    batch_size = max(1, int(batch_size))
    total = len(groups)
    done = 0
    folders = _FolderCheck(groups)

    def emit_rows(rows: List[dict]) -> None:
        nonlocal done
        for row in rows:
            if row["status"] in ("change", "unchanged"):
                folders.place(row)
            emit(row)
        done += 1

    def finish(pending: _Pending, answer: ModelAnswer) -> None:
        try:
            rows = _group_rows(pending, answer, decisions, base_codes)
        except Exception as exc:  # noqa: BLE001 - one model's odd data must not end the run
            rows = _error_rows(pending.group, exc)
        emit_rows(rows)

    def stopped() -> bool:
        if control is None:
            return False
        control.wait_if_paused()
        return control.is_cancelled()

    queue: List[Tuple[_Pending, str]] = []
    for group in groups:
        if stopped():
            return
        try:
            pending = _prepare(group, base_codes, clip_name)
            cached = cache.get(pending.cache_key)
            answer = None
            if cached is not None and isinstance(cached.get("text"), str):
                answer = parse_answer(cached["text"], pending.model_input)
                if _answer_problems(pending, answer):
                    answer = None  # the checks changed since it was cached: ask again
        except Exception as exc:  # noqa: BLE001
            emit_rows(_error_rows(group, exc))
            continue
        if answer is not None:
            finish(pending, answer)
        else:
            queue.append((pending, pending.message))

    while queue:
        if stopped():
            return
        batch, queue = queue[:batch_size], queue[batch_size:]
        progress(done, total, f"Naming models with the AI ({done} of {total} done)")
        texts = generator.generate([message for _pending, message in batch])
        retries: List[Tuple[_Pending, str]] = []
        for index, (pending, _message) in enumerate(batch):
            # A missing output comes back as "": unparsed, so retried, flagged, never cached.
            text = texts[index] if index < len(texts) and isinstance(texts[index], str) else ""
            try:
                answer = parse_answer(text, pending.model_input)
                problems = _answer_problems(pending, answer)
            except Exception as exc:  # noqa: BLE001
                emit_rows(_error_rows(pending.group, exc))
                continue
            if problems and not pending.retried:
                pending.retried = True
                retries.append((pending, retry_message(pending.model_input, problems)))
                continue
            if not problems:
                cache.put(pending.cache_key, {"text": text})
            finish(pending, answer)
        # Retries go into the very next batch.
        queue = retries + queue
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
) -> Tuple[dict, List[dict]]:
    """The dry-run job body: collect, classify, seed base codes, emit finished rows, name groups."""
    rows: List[dict] = []

    def keep(row: dict) -> None:
        rows.append(row)
        emit(row)

    entries = collect_entries(roots)
    groups, finished = classify(entries, client, decisions, progress=progress, control=control)
    pairs: List[Tuple[str, str]] = []
    for group in groups:
        for placed in group.placed:
            base = _text((_find_version(group.model, placed.entry.version_id) or {}).get("baseModel"))
            if base:
                pairs.append((base, placed.entry.stem))
    if control is None or not control.is_cancelled():
        # A stopped first run saw only some models: seeding from them would be final.
        base_codes.seed(pairs)
    for row in finished:
        keep(row)
    name_groups(
        groups,
        generator,
        decisions=decisions,
        base_codes=base_codes,
        cache=cache,
        clip_name=clip_name,
        progress=progress,
        emit=keep,
        control=control,
        batch_size=batch_size,
    )
    return _summary(entries, rows), rows
