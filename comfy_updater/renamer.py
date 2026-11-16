"""Model renamer dry run: collect library entries, sort them against live Civitai data, name them.

Code here only formats (``format_part``) and checks names; every word comes from
Civitai, the remembered decisions or the AI, and a failed check flags the row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import queue
import re
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
from .naming_check import (
    base_vocabulary,
    invented_words,
    maker_in_description,
    name_check_flags,
    strip_zeros,
    xl_is_base,
)
from .naming_scheme import (
    _BASE_SUFFIX,
    NameParts,
    assemble,
    format_part,
    parse_stem,
    part_problems,
    stem_problems,
)
from .path_resolver import list_model_files
from .rename_apply import assign_siblings, file_identity, removable_copy_files
from .updater_service import _first_preview
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


def _preview_fields(entry: Entry, model: Optional[dict] = None) -> dict:
    """What the panel's card shows for a row: a preview, the mature flag and the folder's type.

    The preview is the info file's first image (else video), else the live version's.
    Mature is the live model's flag, else the info file's model flag.
    """
    model = model if isinstance(model, dict) else {}
    url, kind = _safe_preview(entry.info)
    if not url:
        try:
            version = _find_version(model, entry.version_id) or {}
        except TypeError:  # malformed live data: its error row still gets the fields
            version = {}
        url, kind = _safe_preview(version)
    live_nsfw = model.get("nsfw")
    if isinstance(live_nsfw, bool):
        nsfw = live_nsfw
    else:
        nsfw = bool(_as_dict(entry.info.get("model")).get("nsfw"))
    return {"previewUrl": url, "previewType": kind, "nsfw": nsfw, "modelType": entry.model_type}


def _safe_preview(data: dict) -> Tuple[str, str]:
    images = data.get("images")
    return _first_preview(data) if isinstance(images, list) else ("", "image")


def _finished_row(entry: Entry, file_key: str, status: str, reason: str, model: Optional[dict] = None) -> dict:
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
        "metadataOnly": entry.metadata_only,
        **_preview_fields(entry, model),
        **entry.identity,
    }


def _find_version(model: dict, version_id: str) -> Optional[dict]:
    for version in model.get("modelVersions") or []:
        if isinstance(version, dict) and _identifier(version.get("id")) == version_id:
            return version
    return None


_WORD = re.compile(r"[a-z0-9]+")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _without_base(stem: str) -> str:
    match = _BASE_SUFFIX.match(stem)
    return match.group(1) if match else stem


def _words(text: str) -> List[str]:
    """The words of a name that count: runs of letters/digits, three or longer, each once."""
    return list(dict.fromkeys(word for word in _WORD.findall(text.lower()) if len(word) >= 3))


def _compacted(texts: Iterable[str]) -> str:
    """Lowercase letters and digits only; texts kept apart so no word spans two of them."""
    return "|".join(_NOT_WORD.sub("", text.lower()) for text in texts if isinstance(text, str))


def name_match_score(stem: str, live_texts: Iterable[str]) -> int:
    """Words of the stem (its base part left out) found in the live texts, minus those not found.

    A wrong word ("high" on the low file) counts against, so a longer name that
    repeats the title cannot outscore a shorter one that is right.
    """
    haystack = _compacted(live_texts)
    words = _words(_without_base(stem))
    found = sum(1 for word in words if word in haystack)
    return found - (len(words) - found)


def name_matches_info(stem: str, live_texts: Iterable[str]) -> bool:
    """Whether the title part of the stem shares a word with the live title, version and file name.

    A title with no word long enough to compare is given the benefit of the doubt.
    """
    title = parse_stem(stem).title or _without_base(stem)
    words = _words(title)
    if not words:
        return True
    haystack = _compacted(live_texts)
    return any(word in haystack for word in words)


def _live_names(model: dict, version: dict, placed: Placed) -> Dict[str, str]:
    """The live names a copy's file name is judged against."""
    file_name = placed.file.get("name") if placed.file is not None else ""
    return {
        "liveTitle": _text(model.get("name")),
        "uploader": _text(_as_dict(model.get("creator")).get("username")),
        "versionName": _text(version.get("name")),
        "civitaiFileName": _text(file_name),
    }


def _split_duplicates(placed: List[Placed], model: Optional[dict] = None) -> Tuple[List[Placed], List[dict]]:
    """Same version and same file in one folder: keep one, row the rest.

    The file is the matched live file, else the entry's own stored hash, so two
    different unmatched files of one version are never duplicates. Only entries
    with neither share the empty bucket.

    The copy kept: one with weights if any, then the one whose name best matches
    the live data (a copy saved under the wrong name long ago loses), then the
    alphabetically first.
    """
    model = model if isinstance(model, dict) else {}
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
    siblings: Dict[str, Dict[str, List[Path]]] = {}
    for (_folder, _version, file_id), members in buckets.items():
        if len(members) < 2:
            continue
        # How the copies are known to be one file; "none" (neither a live file nor a
        # stored hash) is a guess the panel does not mark for removal.
        same_file_by = file_id.split(":", 1)[0] if file_id else "none"
        same_file_by = "hash" if same_file_by == "sha" else same_file_by
        version = _find_version(model, members[0].entry.version_id) or {}
        names = {id(item): _live_names(model, version, item) for item in members}
        with_weights = [item for item in members if item.entry.has_weights]
        # Never offer real weights for removal while a metadata-only copy survives.
        candidates = with_weights or members

        def rank(item: Placed) -> tuple:
            live = names[id(item)]
            score = name_match_score(
                item.entry.stem, (live["uploader"], live["liveTitle"], live["versionName"], live["civitaiFileName"])
            )
            return (-score, item.entry.stem.lower(), item.entry.stem)

        survivor = min(candidates, key=rank)
        for item in members:
            if item is survivor:
                continue
            duplicates[id(item)] = survivor
            live = names[id(item)]
            row = _finished_row(item.entry, item.file_key, "duplicate", "duplicate", model)
            row["survivorKey"] = _entry_key(survivor.entry)
            row["survivorPath"] = str(survivor.entry.path)
            row.update(live)
            row["sameFileBy"] = same_file_by
            folder = str(item.entry.folder)
            if folder not in siblings:
                siblings[folder] = assign_siblings(item.entry.folder)
            # What Apply would move to the Recycle Bin, as the folder is now (Apply checks again).
            row["removeFiles"] = [
                str(path)
                for path in removable_copy_files(
                    siblings[folder].get(item.entry.stem.lower(), []),
                    item.entry.stem,
                    item.entry.path,
                    survivor.entry.path,
                    shares_stem=survivor.entry.stem.lower() == item.entry.stem.lower(),
                )
            ]
            row["nameMatchesInfo"] = name_matches_info(
                item.entry.stem, (live["liveTitle"], live["versionName"], live["civitaiFileName"])
            )
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
        return None, [_finished_row(entry, path_file_key(entry.path), "error", reason, model) for entry in model_entries]


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
            rows.append(_finished_row(entry, path_file_key(entry.path), "kept", "release-gone", model))
            continue
        matched = _match_file(version, entry.sha256)
        file_key = f"file:{_identifier(matched.get('id'))}" if matched and _identifier(matched.get("id")) else ""
        if not file_key:
            file_key = path_file_key(entry.path)
        if live_type not in TYPE_FIT.get(entry.model_type, set()):
            rows.append(_finished_row(entry, file_key, "kept", "type-mismatch", model))
            continue
        if matched is not None and matched.get("type") not in MAIN_FILE_TYPES:
            rows.append(_finished_row(entry, file_key, "kept", "not-main-file", model))
            continue
        if decisions.file(file_key).get("keep") is True:
            rows.append(_finished_row(entry, file_key, "kept", "kept-by-you", model))
            continue
        placed.append(Placed(entry=entry, file=matched, file_key=file_key))

    placed, duplicate_rows = _split_duplicates(placed, model)
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
    # Base-model names and codes the base-word check knows: the code table and this model's bases.
    base_names: List[str] = field(default_factory=list)
    # Per member (``id(placed)``): its precision part, and its saved version decision (None: the AI's).
    precisions: Dict[int, str] = field(default_factory=dict)
    decided_versions: Dict[int, Optional[str]] = field(default_factory=dict)
    # Base model -> saved code (None: the AI's answer gives it).
    base_code_of: Dict[str, Optional[str]] = field(default_factory=dict)
    retried: bool = False
    # Re-asked once because another model of the creator got the same title.
    clash_retried: bool = False
    clash_reasons: List[str] = field(default_factory=list)


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
        official=any(_is_official_file(placed) for placed in group.placed),
    )
    return model_input, slot_members


def _saved_version(placed: Placed, decisions: Optional[DecisionStore]) -> Optional[str]:
    """The user's version for this file (per file, else per version), or None for the AI's."""
    if decisions is None:
        return None
    version = decisions.file(placed.file_key).get("version")
    if version is None:
        version = decisions.version(placed.entry.version_id)
    return version


def _prepare(
    group: ModelGroup, base_codes: BaseCodeStore, clip_name: str, decisions: Optional[DecisionStore] = None
) -> _Pending:
    model_input, slot_members = _model_input(group, base_codes)
    message = build_message(model_input)
    slots = model_input.slots
    source = " ".join([model_input.title] + [s.version_name for s in slots] + [s.file_name for s in slots])
    precisions: Dict[int, str] = {}
    decided_versions: Dict[int, Optional[str]] = {}
    for slot, members in slot_members:
        version_live = _find_version(group.model, slot.version_id) or {}
        for placed in members:
            precisions[id(placed)] = _precision(placed, version_live)
            decided_versions[id(placed)] = _saved_version(placed, decisions)
    return _Pending(
        precisions=precisions,
        decided_versions=decided_versions,
        base_code_of={slot.base_model: base_codes.code(slot.base_model) for slot in slots if slot.base_model},
        group=group,
        model_input=model_input,
        slot_members=slot_members,
        message=message,
        cache_key=AnswerCache.key(
            group.model_id, {"message": message, "promptVersion": PROMPT_VERSION, "clipName": clip_name}
        ),
        # The maker check reads the same credit lines the AI saw.
        credit_text=credit_lines(model_input.description, official=model_input.official),
        source=source,
        base_names=_base_names(base_codes, slots),
    )


def _base_names(base_codes: BaseCodeStore, slots: List[Slot]) -> List[str]:
    names: List[str] = []
    for base_model, code in base_codes.all().items():
        names.extend((base_model, code))
    names.extend(slot.base_model for slot in slots if slot.base_model)
    return names


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
    if kind == "base-word":
        return f'"{part}" is a base model name; leave it out of the title and version.'
    if kind == "version-format":
        return f'write "{strip_zeros(part)}", not "{part}".'
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
    vocabulary = base_vocabulary(pending.base_names + list(answer.bases.values()))
    for slot, _members in pending.slot_members:
        probe = NameParts(
            creator=original or "creator",
            title=format_part(answer.title),
            version=format_part(answer.versions.get(slot.key, "")),
            precision="",
            base=format_part(answer.bases.get(slot.base_model, "")) or "base",
        )
        codes = _parts_flags(probe) + name_check_flags(
            probe.title, probe.version, vocabulary, xl_base=xl_is_base(slot.base_model)
        )
        problems.extend(_flag_message(code, probe) for code in codes)
    problems.extend(_version_clash_problems(pending, answer))
    return list(dict.fromkeys(problems))


def _version_clash_problems(pending: _Pending, answer: ModelAnswer) -> List[str]:
    """Two versions of the model whose files would get the same version, precision and base.

    Only where the AI gave at least one of the two versions: a clash between two
    saved versions is the user's own choice, flagged on the rows but not asked about.
    """
    seen: Dict[Tuple[str, str, str], Tuple[Slot, bool]] = {}
    problems: List[str] = []
    told: Set[Tuple[str, str]] = set()
    for slot, members in pending.slot_members:
        code = pending.base_code_of.get(slot.base_model) if slot.base_model else None
        base = format_part(code if code is not None else answer.bases.get(slot.base_model, ""))
        for placed in members:
            saved = pending.decided_versions.get(id(placed))
            version = format_part(saved if saved is not None else answer.versions.get(slot.key, ""))
            key = (version, format_part(pending.precisions.get(id(placed), "")), base)
            from_answer = saved is None
            if key not in seen:
                seen[key] = (slot, from_answer)
                continue
            other, other_from_answer = seen[key]
            if other.version_id == slot.version_id or not (from_answer or other_from_answer):
                continue
            pair = (other.version_id, slot.version_id)
            if pair in told:
                continue
            told.add(pair)
            became = f'both became "{version}"' if version else "both got no version"
            problems.append(
                f'Versions "{other.version_name}" and "{slot.version_name}" {became}; keep the words that tell them apart.'
            )
    return problems


def _mark_version_clashes(rows: List[dict]) -> None:
    """Flag ``version-clash`` on one model's named rows whose versions differ but whose names would not."""
    buckets: Dict[Tuple[str, str, str], List[dict]] = {}
    for row in rows:
        if row.get("status") not in ("change", "unchanged"):
            continue
        row["flags"] = [flag for flag in row.get("flags") or [] if flag != "version-clash"]
        parts = row.get("parts") or {}
        key = tuple(str(parts.get(name) or "") for name in ("version", "precision", "base"))
        buckets.setdefault(key, []).append(row)  # type: ignore[arg-type]
    for members in buckets.values():
        if len({str(row.get("versionId")) for row in members}) > 1:
            for row in members:
                row["flags"].append("version-clash")


def title_clash_models(rows: Iterable[dict], decided_titles: Callable[[str], bool]) -> Dict[str, List[str]]:
    """Models whose named rows share creator, title and base code with another model: model id -> the others' ids.

    The same title under different base codes is no clash (one idea released per
    base). Two models whose titles both came from the user are the user's choice.
    """
    by_name: Dict[Tuple[str, str, str], List[str]] = {}
    for row in rows:
        key = _title_key(row)
        if key is None:
            continue
        ids = by_name.setdefault(key, [])
        model_id = str(row.get("modelId"))
        if model_id not in ids:
            ids.append(model_id)
    clashes: Dict[str, List[str]] = {}
    for ids in by_name.values():
        if len(ids) < 2 or all(decided_titles(model_id) for model_id in ids):
            continue
        for model_id in ids:
            others = clashes.setdefault(model_id, [])
            others.extend(other for other in ids if other != model_id and other not in others)
    return clashes


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


def _file_name_creator(stem: str) -> str:
    """The creator part of a stem in the scheme's shape (``creator - title ...``), else ""."""
    parts = parse_stem(stem)
    return parts.creator if parts.title else ""


def _group_rows(
    pending: _Pending, answer: ModelAnswer, decisions: DecisionStore, base_codes: BaseCodeStore
) -> List[dict]:
    group = pending.group
    model_decision = decisions.model(group.model_id)
    group_flags: List[str] = []
    maker_found = "creator" in model_decision
    if "creator" in model_decision:
        creator = model_decision["creator"]
    else:
        creator = _creator_from_answer(pending, answer)
        if creator:
            maker_found = True
            group_flags.append("maker-from-description")
            if not maker_in_description(creator, pending.credit_text):
                group_flags.append("maker-not-in-description")
        else:
            creator = pending.model_input.creator
    # Civitai has no creator (a deleted account): the entry's own file name may still have one.
    creator_fallback = "creator" not in model_decision and not format_part(creator)
    title_from_answer = "title" not in model_decision
    title = answer.title if title_from_answer else model_decision["title"]
    title_invented = invented_words(pending.source, [answer.title]) if title_from_answer and not answer.unparsed else []

    model_bases = list(dict.fromkeys(slot.base_model for slot, _members in pending.slot_members if slot.base_model))
    vocabulary = base_vocabulary(pending.base_names + list(answer.bases.values()))
    rows: List[dict] = []
    for slot, members in pending.slot_members:
        version_live = _find_version(group.model, slot.version_id) or {}
        for placed in members:
            entry = placed.entry
            flags = list(group_flags)
            row_creator = creator
            if creator_fallback:
                row_creator = _file_name_creator(entry.stem)
                if row_creator:
                    flags.append("creator-from-file-name")
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
                creator=format_part(row_creator),
                title=format_part(title),
                version=format_part(version),
                precision=format_part(
                    pending.precisions[id(placed)] if id(placed) in pending.precisions
                    else _precision(placed, version_live)
                ),
                base=format_part(code),
            )
            flags.extend(_parts_flags(parts))
            flags.extend(name_check_flags(parts.title, parts.version, vocabulary, xl_base=xl_is_base(slot.base_model)))
            if _is_official_file(placed):
                flags.append("official-file")
                if not maker_found:
                    # An official model re-uploaded: the uploader is rarely its maker.
                    flags.append("maker-unknown")
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
                    "selected": False,
                    # For re-assembling after an edit and for the sidecar title after apply.
                    "baseModel": slot.base_model,
                    # The model's other bases, for the base-word check after an edit.
                    "modelBaseModels": model_bases,
                    "liveTitle": pending.model_input.title,
                    # The live Civitai uploader, whoever the name credits.
                    "uploader": pending.model_input.creator,
                    "metadataOnly": entry.metadata_only,
                    **_preview_fields(entry, group.model),
                    **entry.identity,
                }
            )
    _mark_version_clashes(rows)
    for row in rows:
        row["selected"] = row["status"] == "change" and not row["flags"]
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

    def unplace(self, row: dict) -> None:
        """Take a placed row back (it is being named again); a peer left alone loses its collision."""
        peers = self._by_new.get((row["folder"].lower(), row["newStem"].lower()), [])
        for index, peer in enumerate(peers):
            if peer is row:
                del peers[index]
                break
        else:
            return
        if len(peers) == 1 and "collision" in peers[0]["flags"]:
            peer = peers[0]
            peer["flags"] = [flag for flag in peer["flags"] if flag != "collision"]
            peer["selected"] = peer["status"] == "change" and not peer["flags"]


def _error_rows(group: ModelGroup, exc: Exception) -> List[dict]:
    return [_finished_row(p.entry, p.file_key, "error", str(exc) or type(exc).__name__, group.model) for p in group.placed]


# Models per Generate Text call: the long system prompt is read once for all of them.
# Measured: K=3 scored within 2 cases of K=1 on both regression sets at 0.49 s/model (K=1: ~1.04);
# K=4 did not (31/50 against 39/50).
GROUP_SIZE = 3
# Answer room per model in a call (a one-model answer is well under this).
ANSWER_TOKENS_PER_MODEL = 160


@dataclass
class _Named:
    """A model whose rows are emitted: kept so a title clash can re-ask it and replace its rows."""

    pending: _Pending
    rows: List[dict]
    title_decided: bool
    # The answer's text when it passed every check (None: it has problems), and whether it is cached.
    text: Optional[str] = None
    cached: bool = False


def _title_key(row: dict) -> Optional[Tuple[str, str, str]]:
    """(creator, title, base code) of a named row: two models clash only under the same base.

    One idea released per base (the same title as "(ill)" and "(krea2)") is the
    user's naming: the base codes already keep those names apart.
    """
    if row.get("status") not in ("change", "unchanged"):
        return None
    parts = row.get("parts") or {}
    title = str(parts.get("title") or "")
    return (str(parts.get("creator") or ""), title, str(parts.get("base") or "")) if title else None


class _Namer:
    """Asks the AI for the groups given to ``add``, a batch of calls at a time.

    Every group is checked, retried (alone, with the single-model retry message)
    and cached on its own, whatever ``group_size`` put it in a call with.

    Once a model's rows are out, its creator, title and base codes are compared
    with the other models named so far. Two models with the same creator and
    title under a shared base code are
    each re-asked once more, alone, with the other's Civitai title (a model
    whose title the user decided is not); the new rows replace the old ones in
    place. ``flag_title_clashes`` flags what still clashes at the end. An answer
    is cached only while it clashes with no other model.
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
        self._named: Dict[str, _Named] = {}
        self._by_title: Dict[Tuple[str, str, str], List[str]] = {}

    def _emit_rows(self, rows: List[dict]) -> None:
        with self.lock:
            for row in rows:
                if row["status"] in ("change", "unchanged"):
                    self.folders.place(row)
                self.emit(row)
            self.done += 1

    def _replace_rows(self, old_rows: List[dict], rows: List[dict]) -> List[dict]:
        """Update the emitted row dicts in place with the new ones (matched by key); returns the rows now out."""
        with self.lock:
            by_key = {row["key"]: row for row in old_rows}
            out: List[dict] = []
            for row in rows:
                old = by_key.pop(row["key"], None)
                if old is None:
                    if row["status"] in ("change", "unchanged"):
                        self.folders.place(row)
                    self.emit(row)
                    out.append(row)
                    continue
                if old.get("status") in ("change", "unchanged"):
                    self.folders.unplace(old)
                # The same keys: updated, never emptied, so a concurrent read sees a whole row.
                old.update(row)
                if old["status"] in ("change", "unchanged"):
                    self.folders.place(old)
                out.append(old)
            out.extend(by_key.values())  # never expected: the same entries are named again
            return out

    def _finish(self, pending: _Pending, answer: ModelAnswer, text: Optional[str] = None, cached: bool = False) -> None:
        """Rows from the answer: emitted (or replacing the model's earlier ones), then the title check.

        ``text`` is a clean answer to cache once it clashes with nothing; ``cached``: it already is.
        """
        try:
            rows = _group_rows(pending, answer, self.decisions, self.base_codes)
        except Exception as exc:  # noqa: BLE001 - one model's odd data must not end the run
            rows = _error_rows(pending.group, exc)
        model_id = pending.group.model_id
        with self.lock:
            previous = self._named.get(model_id)
            if previous is None:
                self._emit_rows(rows)
                old_partners: Set[str] = set()
            else:
                old_partners = self._unregister(model_id)
                rows = self._replace_rows(previous.rows, rows)
            named = _Named(
                pending=pending,
                rows=rows,
                title_decided="title" in self.decisions.model(model_id),
                text=text,
                cached=cached,
            )
            self._named[model_id] = named
            self._register(model_id)
            partners = self._partners(model_id)
            if partners:
                self._clash(model_id, partners)
            else:
                self._cache_if_clear(model_id)
            for other in old_partners - partners - {model_id}:
                self._cache_if_clear(other)

    # --- title clashes ---------------------------------------------------------

    def _keys(self, model_id: str) -> Set[Tuple[str, str, str]]:
        named = self._named.get(model_id)
        if named is None:
            return set()
        return {key for key in (_title_key(row) for row in named.rows) if key is not None}

    def _register(self, model_id: str) -> None:
        for key in self._keys(model_id):
            ids = self._by_title.setdefault(key, [])
            if model_id not in ids:
                ids.append(model_id)

    def _unregister(self, model_id: str) -> Set[str]:
        """Take the model's titles out; returns the models it clashed with."""
        partners = self._partners(model_id)
        for key in self._keys(model_id):
            ids = self._by_title.get(key, [])
            if model_id in ids:
                ids.remove(model_id)
        return partners

    def _partners(self, model_id: str) -> Set[str]:
        """Other models with the same creator and title, unless both titles are the user's."""
        named = self._named.get(model_id)
        partners: Set[str] = set()
        for key in self._keys(model_id):
            for other in self._by_title.get(key, []):
                if other == model_id:
                    continue
                if named is not None and named.title_decided and self._named[other].title_decided:
                    continue
                partners.add(other)
        return partners

    def _cache_if_clear(self, model_id: str) -> None:
        named = self._named.get(model_id)
        if named is None or named.text is None or named.cached or self._partners(model_id):
            return
        self.cache.put(named.pending.cache_key, {"text": named.text})
        named.cached = True

    def _clash(self, model_id: str, partners: Set[str]) -> None:
        """Uncache the clashing answers and re-ask, once, each model whose title came from the AI."""
        for member in [model_id, *sorted(partners)]:
            named = self._named[member]
            if named.title_decided:
                continue
            if named.cached:
                # Never kept while it clashes: the next dry run asks again.
                self.cache.drop(named.pending.cache_key)
                named.cached = False
            pending = named.pending
            if pending.clash_retried:
                continue
            other = model_id if member != model_id else sorted(partners)[0]
            other_title = self._named[other].pending.model_input.title
            shared = self._keys(member) & self._keys(other)
            title = sorted(shared)[0][1] if shared else ""
            reason = (
                f'Another model by this creator, "{other_title}", also became "{title}"; '
                "keep what sets this model apart."
            )
            pending.clash_retried = True
            pending.clash_reasons = [reason]
            self._retries.append((pending, retry_message(pending.model_input, [reason])))

    def flag_title_clashes(self) -> None:
        """Flag ``title-clash`` on the rows of every model that still shares creator and title with another."""
        with self.lock:
            for model_id, named in self._named.items():
                if self._partners(model_id):
                    for row in named.rows:
                        if row.get("status") in ("change", "unchanged"):
                            _add_flag(row, "title-clash")

    # --- asking ----------------------------------------------------------------

    def add(self, group: ModelGroup) -> None:
        """A cached answer that still passes the checks names the group now; else it waits for the AI."""
        try:
            pending = _prepare(group, self.base_codes, self.clip_name, self.decisions)
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
            self._finish(pending, answer, text=cached["text"], cached=True)
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
        model_id = pending.group.model_id
        try:
            answer = parse_answer(text, pending.model_input)
            problems = _answer_problems(pending, answer)
        except Exception as exc:  # noqa: BLE001
            if model_id in self._named:
                return  # a re-ask after a title clash: its earlier rows stay
            self._emit_rows(_error_rows(pending.group, exc))
            return
        if problems and not pending.retried:
            pending.retried = True
            # Retries go into the very next batch; after a clash re-ask the clash stays in the reasons.
            self._retries.append((pending, retry_message(pending.model_input, problems + pending.clash_reasons)))
            return
        if problems and model_id in self._named:
            return  # the clash re-ask came back worse: the earlier rows stay (and are flagged at the end)
        # Cached per model, as this model's own answer: reusable whatever the group size.
        self._finish(pending, answer, text=None if problems else text)


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
    try:
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
    finally:
        # Whenever naming ends (done, stopped or failed): the rows already out get their flags.
        namer.flag_title_clashes()


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
            # Whenever naming ends (done, stopped or failed), before the run's rows are final.
            namer.flag_title_clashes()
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
