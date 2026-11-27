"""Apply the model renamer's selected renames to the library, with a crash-recovery journal.

Every entry's weights file is renamed together with each file in its folder that
shares its stem (``.civitai.info``, ``.preview.png``, anything another tool left).
An existing target is never overwritten. A file Windows has locked is skipped.
A rename into a name another rename of the run gives up runs after that one; in a
swap (A takes B's name, B takes A's) one side waits under temporary names.
Every step that completes is appended to the rename log (``RenameLog``), if one is given.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, Callable, Optional

from .hashing import HashingCancelled, sha256_file
from .constants import INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .naming_scheme import _has_forbidden, stem_problems, target_path
from .path_identity import path_key
from .rename_store import RenameJournal, RenameLog
from .sidecar import read_data_file, read_json, write_json

TMP_MARKER = ".cu-tmp-"
SAVED_PATH_FILES = ("last_check.json", "progress.json")
TARGET_EXISTS = "target exists"
SURVIVOR_GONE = "the copy that was to be kept is gone"
SOURCE_MISSING = "source missing"
IN_RECYCLE_BIN = "already in the Recycle Bin; restore it from there"
OWN_FILE_GONE = "its file is gone or was renamed since the dry run; run a new dry run"
CHANGED_SINCE_DRY_RUN = "its file changed since the dry run; run a new dry run"
NOT_A_RENAME = "not a rename in this dry run"
SHARES_SURVIVOR_NAME = "shares its name with the copy that is kept; renamed together"
COPY_DIFFERS = "not identical to the copy that is kept; not removed"
COMPARE_STOPPED = "stopped before the copies were compared; not removed"
COMPARE_FAILED = "could not be compared"
# model_actions.WEIGHTS_STILL_THERE's wording; that module imports this one.
STILL_THERE = "could not move to the Recycle Bin (it is still there)"
OLD_NAME_TAKEN = "could not undo: its old name is taken by another file"
UNSAFE_NEW_NAME = "its new name is not a safe file name; not renamed"
BLOCKED_NEW_NAME = "its new name is flagged as unusable; not renamed"
# The flag kinds the panel refuses to apply (web/rename_view.js BLOCKING_KINDS).
BLOCKING_FLAG_KINDS = frozenset({"empty", "forbidden", "separator", "reserved", "trailing", "collision", "exists"})

_STEM_SOURCES = tuple(SUPPORTED_MODEL_EXTENSIONS) + (INFO_SIDECAR_SUFFIX,)
_KNOWN_SUFFIXES = (INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX) + tuple(SUPPORTED_MODEL_EXTENSIONS)


# --- siblings and planning -------------------------------------------------


def _norm(path: Any) -> str:
    """A path's identity (``path_key``): two spellings of one file share it where case does not count."""
    return path_key(os.path.normpath(str(path)))


def _is_weights(name: str) -> bool:
    return name.lower().endswith(tuple(SUPPORTED_MODEL_EXTENSIONS))


def assign_siblings(folder: Path) -> dict[str, list[Path]]:
    """Stem (``path_key``) -> its files; each file goes to the longest stem it starts with.

    Suffixes match in any case; the stem before them is the file's identity.
    """
    try:
        files = sorted((p for p in Path(folder).iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError:
        return {}
    stems = set()
    for path in files:
        lower = path.name.lower()
        for suffix in _STEM_SOURCES:
            if lower.endswith(suffix) and len(lower) > len(suffix):
                stems.add(path_key(path.name[: -len(suffix)]))
    longest_first = sorted(stems, key=len, reverse=True)
    result: dict[str, list[Path]] = {}
    for path in files:
        if TMP_MARKER in path.name.lower():
            continue  # A leftover of an interrupted case-only rename belongs to recovery.
        key = path_key(path.name)
        for stem in longest_first:
            if key.startswith(stem + "."):
                result.setdefault(stem, []).append(path)
                break
    return result


def _ordered(files: list[Path], key: str) -> list[Path]:
    """The entry's own file first, then other weights, then the .civitai.info, then the rest."""

    def rank(path: Path) -> tuple:
        lower = path.name.lower()
        if _norm(path) == key:
            first = 0
        elif _is_weights(lower):
            first = 1
        elif lower.endswith(INFO_SIDECAR_SUFFIX):
            first = 2
        else:
            first = 3
        return (first, lower)

    return sorted(files, key=rank)


def removable_copy_files(files: list[Path], stem: str, own: Any, keep: Any, *, shares_stem: bool) -> list[Path]:
    """The files an extra copy's removal takes, in order: its own file first.

    Only the kinds the plugin knows go: the ``.civitai.info``, the preview and
    weights (checked byte-identical before removal). Anything else of that name
    (an archive, a backup) stays. When the sidecars are shared with a model that
    stays (the kept copy has the same name, or other weights share them), only
    its own file goes. The kept copy's file never does.
    """
    stem = str(stem)
    stem_key = path_key(stem)

    def known(name: str) -> bool:
        # The stem is the file's identity; the suffix may be in any case.
        return path_key(name[: len(stem)]) == stem_key and name[len(stem):].lower() in _KNOWN_SUFFIXES

    own_key = _norm(own)
    files = [path for path in files if _norm(path) == own_key or known(path.name)]
    files = _ordered(files, own_key)
    other_weights = [path for path in files if _is_weights(path.name) and _norm(path) != own_key]
    if shares_stem or other_weights:
        files = [path for path in files if _norm(path) == own_key]
    if keep:
        files = [path for path in files if _norm(path) != _norm(keep)]
    return files


def file_identity(path: Any) -> dict:
    """Size and modification time of a file, recorded per row at dry-run time."""
    st = os.stat(path)
    return {"fileSize": st.st_size, "fileMtimeNs": st.st_mtime_ns}


def _identity_problem(row: dict, own: str) -> Optional[str]:
    """Why the row's own file is no longer the file the dry run saw, or None."""
    if "fileSize" not in row and "fileMtimeNs" not in row:
        return None  # A row without a recorded identity (older dry run): the sibling check alone.
    try:
        now = file_identity(own)
    except OSError:
        return OWN_FILE_GONE
    if now["fileSize"] != row.get("fileSize") or now["fileMtimeNs"] != row.get("fileMtimeNs"):
        return CHANGED_SINCE_DRY_RUN
    return None


def _new_stem_problem(row: dict, new: str) -> Optional[str]:
    """Why a row's ``newStem`` must not be applied, or None: not a bare file-name stem, or flagged."""
    if "/" in new or "\\" in new or _has_forbidden(new) or new.strip() in ("", ".", "..") or stem_problems(new):
        return UNSAFE_NEW_NAME
    if any(str(flag).split(":", 1)[0] in BLOCKING_FLAG_KINDS for flag in row.get("flags") or []):
        return BLOCKED_NEW_NAME
    return None


def plan(
    rows: list[dict], selected_keys: set[str], remove_duplicate_keys: set[str], delete_keys: Any = frozenset()
) -> tuple[list[dict], list[dict]]:
    """Rename steps for selected ``change`` rows, trash steps for chosen duplicates, and the skips.

    Trash steps come first so a survivor can take over a removed copy's name.
    ``delete_keys`` are entries the user deletes outright: their file and its
    sidecars go to the Recycle Bin (steps marked ``delete``), with no kept copy
    to check against, and they are not renamed.
    An entry whose own file is gone, or is not the file the dry run saw, gets no
    steps: it is reported in the skips (``{"entry", "path", "reason"}``) instead.
    """
    cache: dict[str, dict[str, list[Path]]] = {}
    skipped: list[dict] = []

    def siblings(row: dict) -> list[Path]:
        folder = str(row.get("folder") or "")
        if folder not in cache:
            cache[folder] = assign_siblings(Path(folder))
        return list(cache[folder].get(path_key(str(row.get("oldStem") or "")), []))

    def own_file(row: dict) -> str:
        return str(row.get("modelPath") or row.get("key") or "")

    def checked_siblings(row: dict) -> Optional[list[Path]]:
        """The row's files, or None (and a skip) when its own file is not the one the dry run saw."""
        own = own_file(row)
        files = siblings(row)
        problem = None
        if not any(_norm(path) == _norm(own) for path in files):
            problem = OWN_FILE_GONE
        else:
            problem = _identity_problem(row, own)
        if problem is not None:
            skipped.append({"entry": row.get("key"), "path": own, "reason": problem})
            return None
        return files

    renames: list[dict] = []
    change_keys = set()
    for row in rows:
        key = row.get("key")
        old, new = str(row.get("oldStem") or ""), str(row.get("newStem") or "")
        # A file filed under another base's folder moves to its base's (row["newFolder"]).
        moves = bool(row.get("newFolder")) and _norm(row["newFolder"]) != _norm(row.get("folder") or "")
        if key in delete_keys:
            continue  # deleted: not renamed as well
        if row.get("status") != "change" or key not in selected_keys or not old or not new or (old == new and not moves):
            continue
        change_keys.add(key)
        # The panel refuses these rows; any other caller must not move a file out of its folder.
        problem = _new_stem_problem(row, new)
        if problem is not None:
            skipped.append({"entry": key, "path": own_file(row), "reason": problem})
            continue
        files = checked_siblings(row)
        if files is None:
            continue
        folder = Path(row["newFolder"] if moves else row["folder"])
        entry_renames = []
        try:
            for path in _ordered(files, _norm(key)):
                suffix = path.name[len(old):]
                target = target_path(folder, new, suffix)
                if str(target) == str(path):
                    continue
                entry_renames.append(
                    {"kind": "rename", "from": str(path), "to": str(target), "entry": key, "suffix": suffix}
                )
        except ValueError:
            skipped.append({"entry": key, "path": own_file(row), "reason": UNSAFE_NEW_NAME})
            continue
        renames.extend(entry_renames)
    for key in sorted(k for k in selected_keys if k not in change_keys and k not in delete_keys):
        skipped.append({"entry": key, "path": key, "reason": NOT_A_RENAME})

    renamed = {_norm(step["from"]) for step in renames}
    rows_by_key = {row.get("key"): row for row in rows}
    trashes: list[dict] = []
    for row in rows:
        key = row.get("key")
        survivor = rows_by_key.get(row.get("survivorKey"))
        if key in delete_keys:
            files = checked_siblings(row)
            if files is None:
                continue
            for path in removable_copy_files(files, str(row.get("oldStem") or ""), key, "", shares_stem=False):
                trashes.append({"kind": "trash", "from": str(path), "to": "", "entry": key, "delete": True})
            continue
        if row.get("status") != "duplicate" or key not in remove_duplicate_keys:
            continue
        files = checked_siblings(row)
        if files is None:
            continue
        # Exactly the survivor's own file; apply refuses the removal when it is gone.
        keep = _survivor_file(survivor) if survivor is not None and survivor is not row else ""
        shares_stem = survivor is not None and _norm(survivor.get("folder")) == _norm(row.get("folder")) and path_key(
            str(survivor.get("oldStem") or "")
        ) == path_key(str(row.get("oldStem") or ""))
        files = removable_copy_files(files, str(row.get("oldStem") or ""), key, "", shares_stem=shares_stem)
        if not keep:
            # Planned anyway so apply reports the refused removal instead of dropping it.
            trashes.append({"kind": "trash", "from": str(files[0]), "to": "", "entry": key})
            continue
        protected = renamed | {_norm(keep)}
        removable = [path for path in files if _norm(path) not in protected]
        if not removable:
            # Its file is one the survivor's rename takes along (same stem).
            skipped.append({"entry": key, "path": own_file(row), "reason": SHARES_SURVIVOR_NAME})
            continue
        for path in removable:
            # A trash step's "to" is the survivor's file, checked before removal.
            trashes.append({"kind": "trash", "from": str(path), "to": str(keep), "entry": key})
    return trashes + renames, skipped


def plan_steps(
    rows: list[dict], selected_keys: set[str], remove_duplicate_keys: set[str], delete_keys: Any = frozenset()
) -> list[dict]:
    """The steps of ``plan`` without its skips."""
    return plan(rows, selected_keys, remove_duplicate_keys, delete_keys)[0]


def _survivor_file(row: dict) -> str:
    """The file the survivor row refers to: its weights, or its .civitai.info when info-only."""
    return str(row.get("modelPath") or row.get("key") or "")


# --- moving files -----------------------------------------------------------


def send_to_trash(path: str) -> None:
    """Move a file to the Recycle Bin; imported lazily so a missing package only breaks this."""
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise OSError(f"the send2trash package is not installed ({exc})") from exc
    send2trash(path)


def _same_name(a: Path, b: Path) -> bool:
    """The same path apart from letter case: one file on a case-insensitive volume, maybe two elsewhere.

    Case-insensitive on every platform, unlike ``_norm``: it picks the careful way
    to rename (through a temporary name, never onto a name that is there), right
    on both kinds of volume. A Linux mount of an NTFS drive is the case that needs it.
    """
    return os.path.normpath(str(a)).lower() == os.path.normpath(str(b)).lower()


def _taken(src: Path, dst: Path) -> bool:
    """``dst`` is on disk and is not ``src``'s own file under another spelling.

    The disk answers, not ``_norm``: two spellings are one file exactly when the
    volume says so, whatever the platform.
    """
    if not os.path.lexists(dst):
        return False
    if not _same_name(src, dst):
        return True
    try:
        return not os.path.samefile(src, dst)
    except OSError:
        return False  # ``src`` is gone: nothing of its own to tell apart


def _guess_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in _KNOWN_SUFFIXES:
        if lower.endswith(suffix) and len(lower) > len(suffix):
            return name[-len(suffix):]
    return os.path.splitext(name)[1]


def _tmp_path(dst: Path, suffix: Optional[str], run_id: str) -> Path:
    if suffix is None or not dst.name.endswith(suffix) or len(suffix) >= len(dst.name):
        suffix = _guess_suffix(dst.name)
    stem = dst.name[: len(dst.name) - len(suffix)]
    return dst.parent / (stem + TMP_MARKER + run_id[:8] + suffix)


def _find_tmp(dst: Path, run_id: str) -> Optional[Path]:
    marker = path_key(TMP_MARKER + run_id[:8])
    try:
        names = os.listdir(dst.parent)
    except OSError:
        return None
    for name in names:
        key = path_key(name)
        if marker in key and key.replace(marker, "", 1) == path_key(dst.name):
            return dst.parent / name
    return None


def _actual_name(path: Path) -> Optional[str]:
    """The on-disk spelling of ``path``'s name (case-insensitive match), or None."""
    try:
        names = os.listdir(path.parent)
    except OSError:
        return None
    if path.name in names:
        return path.name
    lower = path.name.lower()
    for name in names:
        if name.lower() == lower:
            return name
    return None


def _exists_as(path: Path) -> bool:
    """``path`` exists under exactly this spelling."""
    return _actual_name(path) == path.name


def _target_exists_error(path: Path) -> FileExistsError:
    return FileExistsError(errno.EEXIST, TARGET_EXISTS, str(path))


def _rename_new(src: Path, dst: Path) -> None:
    """Rename onto a name that must be free.

    ``os.rename`` refuses an existing target on Windows; the check covers POSIX,
    where it would overwrite.
    """
    if os.path.lexists(dst):
        raise _target_exists_error(dst)
    os.rename(src, dst)


def _move(src: Path, dst: Path, suffix: Optional[str], run_id: str) -> None:
    """Rename one file; never overwrites; a case-only rename goes through a temporary name."""
    if str(src) == str(dst):
        return
    if not _same_name(src, dst):
        _rename_new(src, dst)
        return
    tmp = _tmp_path(dst, suffix, run_id)
    _rename_new(src, tmp)
    try:
        # Fails on a distinct same-named file (case-sensitive filesystems) too.
        _rename_new(tmp, dst)
    except OSError:
        try:
            _rename_new(tmp, src)
        except OSError:
            pass  # Left under the temporary name; the journal is kept so recovery finds it.
        raise


def _reason(exc: BaseException) -> str:
    if isinstance(exc, FileExistsError):
        return TARGET_EXISTS
    if isinstance(exc, PermissionError):
        return f"file is in use or not permitted ({exc})"
    return f"could not rename ({exc})"


def _stranded(steps: list[dict], run_id: str) -> list[Path]:
    """Files of these steps still under a temporary name."""
    found = []
    for step in steps:
        if step.get("kind") == "rename":
            tmp = _find_tmp(Path(step["to"]), run_id)
            if tmp is not None:
                found.append(tmp)
    return found


def _undo_move(step: dict, run_id: str) -> bool:
    """Put one rename step's file back under its old name. True when something moved.

    Safe on steps that never ran or were already undone. Raises OSError on failure.
    """
    src, dst = Path(step["from"]), Path(step["to"])
    tmp = _find_tmp(dst, run_id)
    if tmp is not None:
        _rename_new(tmp, src)
        return True
    if _same_name(src, dst):
        # Both spellings there as two files (a case-sensitive volume): this rename never ran.
        if src.name != dst.name and _exists_as(dst) and not _taken(dst, src):
            _move(dst, src, step.get("suffix"), run_id)
            return True
        return False
    if os.path.lexists(dst) and not os.path.lexists(src):
        _move(dst, src, step.get("suffix"), run_id)
        return True
    return False


def _old_name_taken(step: dict, run_id: str) -> bool:
    """A renamed file (``applied``) that cannot go back: another file holds its old name."""
    src, dst = Path(step["from"]), Path(step["to"])
    return bool(
        step.get("applied")
        and _find_tmp(dst, run_id) is None
        and os.path.lexists(dst)
        # A case-only rename's old name is taken only by a second file (a case-sensitive volume).
        and _taken(dst, src)
    )


def _undo_entry(
    steps: list[dict], run_id: str, moved: Optional[list] = None, blocked: Optional[list] = None
) -> tuple[bool, list[dict]]:
    """Undo an entry's rename steps in reverse. (anything restored, problems).

    ``moved`` collects the steps whose file was put back. With ``blocked``, a
    renamed file whose old name is taken is collected there instead of left as is.
    """
    restored = False
    problems: list[dict] = []
    for step in reversed(steps):
        if step.get("kind") == "trash":
            if _in_recycle_bin(step):
                problems.append({"entry": step.get("entry"), "path": step.get("from"), "reason": IN_RECYCLE_BIN})
            continue
        if blocked is not None and _old_name_taken(step, run_id):
            blocked.append(step)
            continue
        try:
            if _undo_move(step, run_id):
                restored = True
                if moved is not None:
                    moved.append(step)
        except OSError as exc:
            problems.append({"entry": step.get("entry"), "path": step.get("to"), "reason": f"could not undo: {_reason(exc)}"})
    return restored, problems


def _in_recycle_bin(step: dict) -> bool:
    """A removal step whose file went to the Recycle Bin.

    A removal given up (its kept copy differs, the Recycle Bin refused) is done
    too, with its file still in place: nothing to restore. A step done without
    ``applied`` whose file is gone (the Recycle Bin took it and then failed, or a
    journal from before ``applied``) counts as removed.
    """
    if not step.get("done"):
        return False
    return bool(step.get("applied")) or not os.path.lexists(step.get("from") or "")


def _group(steps: list[dict]) -> list[tuple[Any, list[int]]]:
    groups: dict[Any, list[int]] = {}
    for index, step in enumerate(steps):
        groups.setdefault(step.get("entry"), []).append(index)
    return list(groups.items())


def _with_problems(reason: str, problems: list[dict]) -> str:
    if problems:
        reason += "; " + "; ".join(f"{p['path']}: {p['reason']}" for p in problems)
    return reason


# --- applying ---------------------------------------------------------------


def apply_steps(
    steps: list[dict],
    journal: RenameJournal,
    *,
    trash: Callable[[str], Any] = send_to_trash,
    progress: Optional[Callable[[int, int, str], Any]] = None,
    control: Any = None,
    log: Optional[RenameLog] = None,
    dry_run_job_id: str = "",
) -> dict:
    """Run planned steps entry by entry. ``{"done": [entry…], "skipped": [{"entry", "path", "reason"}]}``."""
    if journal.pending() is not None:
        raise RuntimeError("An earlier rename run did not finish; finish or undo it first.")
    done: list = []
    skipped: list[dict] = []
    trash_sources = {_norm(step["from"]) for step in steps if step.get("kind") == "trash"}
    groups_in = _group(steps)
    # Removals whose survivor is gone (or is itself being removed) never start.
    refused = set()
    for entry, indices in groups_in:
        entry_steps = [steps[index] for index in indices]
        problem = _survivor_problem(entry_steps, trash_sources)
        if problem is not None:
            refused.add(entry)
            skipped.append(problem)
    # Names a removed duplicate frees in this run; a survivor may plan to take one.
    freed = {
        _norm(step["from"]) for step in steps if step.get("kind") == "trash" and step.get("entry") not in refused
    }
    # Entries blocked from the start stay out of the journal: recovery must never
    # take a file that sits at a skipped entry's target for one of ours.
    trash_steps: list[dict] = []
    rename_entries: list[tuple[Any, list[dict]]] = []
    for entry, indices in groups_in:
        if entry in refused:
            continue
        entry_steps = [dict(steps[index]) for index in indices]
        if entry_steps[0].get("kind") == "trash":
            trash_steps.extend(entry_steps)
        else:
            rename_entries.append((entry, entry_steps))
    # A rename may take the name another rename of this run gives up: it runs after that one.
    ordered, parked, problems = _rename_order(rename_entries, freed)
    skipped.extend(problems)
    # Removals run first so the names they free are free when a survivor is renamed.
    steps = trash_steps + [step for _entry, entry_steps in ordered for step in entry_steps]
    run_id = journal.begin(steps, dry_run_job_id)
    record = _recorder(log, run_id, dry_run_job_id)
    groups = _group(steps)
    indices_of = dict(groups)
    # A swap's parked entry moves to temporary names just before the first entry that takes one of its names.
    park_owner = {
        _norm(steps[index]["from"]): entry for entry, indices in groups if entry in parked for index in indices
    }
    parked_now: set = set()
    still_open: list[int] = []
    for position, (entry, indices) in enumerate(groups):
        if control is not None:
            control.wait_if_paused()
            # A parked entry is always finished first: its files are under temporary names.
            if control.is_cancelled() and not parked_now:
                break
        for index in indices:
            owner = park_owner.get(_norm(steps[index].get("to") or ""))
            if owner is not None and owner != entry and owner not in parked_now:
                if _park(steps, indices_of[owner], run_id):
                    parked_now.add(owner)
                park_owner = {source: other for source, other in park_owner.items() if other != owner}
        problem = _apply_entry(steps, indices, journal, run_id, trash, control, record, parked=entry in parked_now)
        parked_now.discard(entry)
        if problem is None:
            done.append(entry)
        else:
            still_open.extend(problem.pop("openSteps", []))
            skipped.append(problem)
        if progress is not None:
            progress(position + 1, len(groups), f"Renamed {position + 1} of {len(groups)}")
    if still_open:
        # Only the stuck files stay journaled: recovery must not touch finished entries.
        journal.keep_only(still_open)
    else:
        journal.clear()
    return {"done": done, "skipped": skipped}


def _recorder(log: Optional[RenameLog], run_id: str, dry_run_job_id: str) -> Callable[..., None]:
    """Appends completed steps to the rename log; does nothing without one.

    A log that cannot be written is reported once per run, not once per entry.
    """
    warned = False

    def record(steps: list[dict], kind: Optional[str] = None) -> None:
        nonlocal warned
        if log is None or not steps:
            return
        warning = log.append(run_id, dry_run_job_id, steps, kind)
        if warning and not warned:
            warned = True
            print(warning)

    return record


def _sources(entry_steps: list[dict]) -> set:
    return {_norm(step["from"]) for step in entry_steps if step.get("kind") == "rename"}


def _held_by(entry_steps: list[dict], free: set) -> set:
    """The entry's targets that are taken: on disk, not freed, and not its own file under another case."""
    return {
        _norm(step["to"])
        for step in entry_steps
        if step.get("kind") == "rename"
        and _taken(Path(step["from"]), Path(step["to"]))
        and _norm(step["to"]) not in free
    }


def _swap_entry(waiting: dict, free: set, banned: set) -> Any:
    """A waiting entry to park under temporary names, or None.

    It is held back only by names other waiting entries give up, and it holds
    one of them back: a swap (A takes B's name, B takes A's), or a ring of them.
    """
    sources = {entry: _sources(entry_steps) for entry, entry_steps in waiting.items()}
    for entry, entry_steps in waiting.items():
        if entry in banned or any(
            not os.path.lexists(step["from"]) for step in entry_steps if step.get("kind") == "rename"
        ):
            continue
        others = set().union(*(names for other, names in sources.items() if other != entry))
        held = _held_by(entry_steps, free)
        if not held or not held <= others:
            continue
        if any(_held_by(waiting[other], free) & sources[entry] for other in waiting if other != entry):
            return entry
    return None


def _rename_order(entries: list[tuple[Any, list[dict]]], freed: set) -> tuple[list, set, list[dict]]:
    """The rename entries in the order they can run, the entries parked to break a swap,
    and the problems of the entries that cannot run.

    An entry whose target is the current name of another entry's file runs after
    that entry. In a swap one entry first moves to temporary names, so the others
    can take its names, and finishes after them. ``freed`` holds the names the
    run's removals free.
    """
    banned: set = set()
    while True:
        free = set(freed)
        waiting = dict(entries)
        runnable: set = set()
        parked: set = set()
        while waiting:
            ready = [entry for entry, entry_steps in waiting.items() if _blocked(entry_steps, free) is None]
            if not ready:
                swap = _swap_entry(waiting, free, banned | parked)
                if swap is None:
                    break
                parked.add(swap)
                free |= _sources(waiting[swap])
                continue
            for entry in ready:
                free |= _sources(waiting.pop(entry))
                runnable.add(entry)
        stuck = parked & set(waiting)
        if not stuck:
            break
        banned |= stuck  # a parked entry that still cannot run frees nothing
    problems = [problem for problem in (_blocked(entry_steps, free) for entry_steps in waiting.values()) if problem]
    steps_of = dict(entries)
    # Who gives up each name now, among the entries that run (a parked entry gives its up when parked).
    giver = {source: entry for entry in runnable if entry not in parked for source in _sources(steps_of[entry])}
    takers: dict = {}
    for entry in runnable:
        for step in steps_of[entry]:
            if step.get("kind") == "rename":
                takers.setdefault(_norm(step["to"]), set()).add(entry)
    order: list = []
    seen: set = set()

    def visit(entry: Any) -> None:
        if entry in seen:
            return
        seen.add(entry)
        for step in steps_of[entry]:
            before = giver.get(_norm(step.get("to") or "")) if step.get("kind") == "rename" else None
            if before is not None and before != entry:
                visit(before)
        order.append(entry)
        # A parked entry whose name this one takes finishes right after it.
        taken = {_norm(step["to"]) for step in steps_of[entry] if step.get("kind") == "rename"}
        for other in sorted(parked & runnable, key=str):
            if other not in seen and _sources(steps_of[other]) & taken:
                visit(other)

    for entry, _entry_steps in entries:
        if entry in runnable:
            visit(entry)
    return [(entry, steps_of[entry]) for entry in order], parked & runnable, problems


def _park(steps: list[dict], indices: list[int], run_id: str) -> bool:
    """Move an entry's files to temporary names, freeing its names for a swap. False (nothing moved) on failure."""
    moved: list[tuple[Path, Path]] = []
    for index in indices:
        step = steps[index]
        if step.get("kind") != "rename":
            continue
        src = Path(step["from"])
        tmp = _tmp_path(Path(step["to"]), step.get("suffix"), run_id)
        try:
            _rename_new(src, tmp)
        except OSError:
            for done_src, done_tmp in reversed(moved):
                try:
                    _rename_new(done_tmp, done_src)
                except OSError:
                    pass  # left under the temporary name; its turn or recovery finishes it
            return False
        moved.append((src, tmp))
    return True


def _survivor_problem(entry_steps: list[dict], trash_sources: set) -> Optional[dict]:
    """A removal needs the survivor's own file on disk, and not among the files removed."""
    first = entry_steps[0]
    if first.get("kind") != "trash" or first.get("delete"):
        return None  # a deletion keeps no copy
    keep = first.get("to")
    if not keep or not os.path.lexists(keep) or _norm(keep) in trash_sources:
        return {"entry": first.get("entry"), "path": str(keep or ""), "reason": SURVIVOR_GONE}
    return None


def _blocked(entry_steps: list[dict], freed: Optional[set] = None, run_id: str = "") -> Optional[dict]:
    """Why an entry's renames cannot start, or None. Targets in ``freed`` count as free.

    With ``run_id``, a file parked under this run's temporary name counts as there.
    """
    for step in entry_steps:
        if step.get("kind") != "rename":
            continue
        src, dst = Path(step["from"]), Path(step["to"])
        if not os.path.lexists(src) and not (run_id and _find_tmp(dst, run_id) is not None):
            return {"entry": step.get("entry"), "path": str(src), "reason": SOURCE_MISSING}
        if _taken(src, dst) and _norm(dst) not in (freed or ()):
            return {"entry": step.get("entry"), "path": str(dst), "reason": TARGET_EXISTS}
    return None


def _copy_problem(entry_steps: list[dict], should_stop: Optional[Callable[[], bool]] = None) -> Optional[dict]:
    """A copy with weights goes only when it is byte-identical to the survivor's file.

    The Recycle Bin can delete a file outright (over its quota, a drive without
    one), so the kept copy must really be the same model. Sidecars need no check.
    """
    for step in entry_steps:
        if step.get("kind") != "trash" or step.get("done") or step.get("delete") or not _is_weights(Path(step["from"]).name):
            continue
        src, keep = Path(step["from"]), Path(step.get("to") or "")
        if not os.path.lexists(src):
            continue

        def problem(reason: str) -> dict:
            return {"entry": step.get("entry"), "path": str(src), "reason": reason}

        try:
            if os.path.getsize(src) != os.path.getsize(keep):
                return problem(COPY_DIFFERS)
            if sha256_file(src, should_stop=should_stop) != sha256_file(keep, should_stop=should_stop):
                return problem(COPY_DIFFERS)
        except HashingCancelled:
            return problem(COMPARE_STOPPED)
        except OSError as exc:
            return problem(f"{COMPARE_FAILED} with the copy that is kept ({exc}); not removed")
    return None


def _apply_entry(steps, indices, journal, run_id, trash, control=None, record=None, parked=False) -> Optional[dict]:
    entry = steps[indices[0]].get("entry")
    record = record or _recorder(None, run_id, "")

    def give_up(path: Any, reason: str, went_through: tuple = ()) -> dict:
        # The entry's steps are settled (not to be run by "finish"), except a file
        # stranded under a temporary name: that step stays open for recovery.
        # ``went_through``: removals that did reach the Recycle Bin, for undo's report.
        stranded = _stranded([steps[index] for index in indices], run_id)
        stranded_keys = {_norm(path) for path in stranded}
        settle = [
            index
            for index in indices
            if not (steps[index].get("kind") == "rename" and _tmp_key(steps[index], run_id) in stranded_keys)
        ]
        journal.mark_done_many(settle, applied_to=went_through)
        problem = {"entry": entry, "path": str(path), "reason": reason}
        if stranded:
            problem["reason"] += "; left under the temporary name " + ", ".join(str(p) for p in stranded)
            problem["openSteps"] = [index for index in indices if index not in settle]
        return problem

    if steps[indices[0]].get("kind") == "trash":
        problem = _survivor_problem([steps[index] for index in indices], {_norm(steps[i]["from"]) for i in indices})
        if problem is None:
            should_stop = control.is_cancelled if control is not None else None
            problem = _copy_problem([steps[index] for index in indices], should_stop)
        if problem is not None:
            return give_up(problem["path"], problem["reason"])
        trashed: list[dict] = []
        binned: list[int] = []
        for index in indices:
            path = steps[index]["from"]
            if os.path.lexists(path):
                try:
                    trash(str(path))
                except OSError as exc:
                    problem = give_up(path, f"could not move to the Recycle Bin ({exc})", tuple(binned))
                    record(trashed)  # those are in the Recycle Bin all the same
                    return problem
                if os.path.lexists(path):
                    # send2trash can return while a file ComfyUI has loaded stays: its sidecars stay too.
                    problem = give_up(path, STILL_THERE, tuple(binned))
                    record(trashed)
                    return problem
                trashed.append(steps[index])
                binned.append(index)
        journal.mark_done_many(indices, applied_to=binned)
        record(trashed)
        return None

    # Checked again with nothing counted as freed: an earlier entry may have taken
    # a target, or a duplicate's removal may have failed.
    problem = _blocked([steps[index] for index in indices], run_id=run_id if parked else "")
    if problem is not None:
        if parked:
            # Back from the temporary names, where its old names are still free.
            _restored, problems = _undo_entry([steps[index] for index in indices], run_id)
            problem["reason"] = _with_problems(problem["reason"], problems)
        give_up(problem["path"], problem["reason"])
        return problem

    finished: list[dict] = []
    for index in indices:
        step = steps[index]
        try:
            if parked:
                _finish_move(step, run_id)  # from its temporary name
            else:
                _move(Path(step["from"]), Path(step["to"]), step.get("suffix"), run_id)
        except OSError as exc:
            if parked:
                # Its old names may be taken by now: what is left stays under the
                # temporary names, open for recovery.
                return give_up(step["to"], _reason(exc))
            _restored, problems = _undo_entry(finished, run_id)
            return give_up(step["from"], _with_problems(_reason(exc), problems))
        finished.append(step)
    journal.mark_done_many(indices, applied=True)
    record(finished)
    return None


def _tmp_key(step: dict, run_id: str) -> Optional[str]:
    tmp = _find_tmp(Path(step["to"]), run_id)
    return _norm(tmp) if tmp is not None else None


# --- recovery ---------------------------------------------------------------


def recover(
    journal: RenameJournal,
    action: str,
    *,
    trash: Callable[[str], Any] = send_to_trash,
    log: Optional[RenameLog] = None,
) -> dict:
    """Finish or undo an interrupted run, then clear the journal.

    The journal keeps the steps of every entry that could not be finished or
    undone (a locked file), and any file still under a temporary name, so the
    same action can be tried again.
    """
    if action not in ("finish", "undo"):
        raise ValueError(f"unknown recovery action {action!r}")
    data = journal.pending()
    done: list = []
    skipped: list[dict] = []
    if data is None:
        journal.clear()
        return {"done": done, "skipped": skipped}
    run_id = data["runId"]
    steps = data["steps"]
    record = _recorder(log, run_id, str(data.get("dryRunJobId") or ""))
    retry: set = set()
    put_back: set = set()
    if action == "undo":
        done, skipped, retry, put_back = _undo_run(steps, run_id, record)
    finishing = _group(steps) if action == "finish" else []
    for entry, indices in finishing:
        entry_steps = [steps[index] for index in indices]
        if all(step.get("done") for step in entry_steps):
            continue
        problem = _finish_entry(entry_steps, journal, indices, run_id, trash, record)
        if problem is None:
            done.append(entry)
        else:
            skipped.append(problem)
            # A removal already done stays out: it cannot be redone or undone.
            retry.update(index for index in indices if steps[index].get("kind") == "rename" or not steps[index].get("done"))
    open_steps = [index for index, step in enumerate(steps) if step.get("kind") == "rename" and _tmp_key(step, run_id)]
    if open_steps:
        stranded = [str(_find_tmp(Path(steps[index]["to"]), run_id)) for index in open_steps]
        skipped.append(
            {
                "entry": None,
                "path": stranded[0],
                "reason": "still under a temporary name: " + ", ".join(stranded),
            }
        )
    retry.update(open_steps)
    if retry:
        journal.keep_only(sorted(retry), put_back)
    else:
        journal.clear()
    return {"done": done, "skipped": skipped}


def _undo_run(steps: list[dict], run_id: str, record) -> tuple[list, list[dict], set, set]:
    """Undo every entry, the last one first. (done entries, problems, steps to keep, steps put back).

    An entry that took a name another gave up goes back first, freeing it. Passes
    repeat while files move. In a swap or ring every old name is held by another
    entry's renamed file: one entry waits under temporary names, as in apply.
    An entry whose renamed file cannot go back stays journaled, with a problem.
    """
    index_of = {id(step): index for index, step in enumerate(steps)}
    groups = list(reversed(_group(steps)))
    outcome: dict = {}  # entry -> (problems, blocked steps) of its last pass
    restored: set = set()
    put_back: set = set()
    parked: dict = {}  # entry -> its steps waiting under temporary names ([] when parking failed)

    def open_(entry: Any) -> bool:
        problems, blocked = outcome[entry]
        return bool(blocked) or any(problem["reason"] != IN_RECYCLE_BIN for problem in problems)

    left = groups
    while left:
        progress = False
        for entry, indices in left:
            moved: list[dict] = []
            blocked: list[dict] = []
            anything, problems = _undo_entry([steps[index] for index in indices], run_id, moved, blocked)
            if anything:
                progress = True
                restored.add(entry)
            # Only renames the log holds (done steps) are logged as reversed.
            record([{"from": step["to"], "to": step["from"]} for step in moved if step.get("done")], "undo")
            for step in moved:
                step.pop("applied", None)  # under its old name again
                put_back.add(index_of[id(step)])
            outcome[entry] = (problems, blocked)
        left = [(entry, indices) for entry, indices in left if open_(entry)]
        if progress:
            continue
        ring = _ring_entry(left, outcome, parked)
        if ring is None:
            break
        blocked = outcome[ring][1]
        # Its files move to the temporary names apply parks under; their undo then finds them there.
        stand_ins = [{"kind": "rename", "from": s["to"], "to": s["to"], "suffix": s.get("suffix")} for s in blocked]
        parked[ring] = blocked if _park(stand_ins, list(range(len(stand_ins))), run_id) else []
    for entry_steps in parked.values():
        for step in entry_steps:
            tmp = _find_tmp(Path(step["to"]), run_id)
            if tmp is not None:
                try:
                    _rename_new(tmp, Path(step["to"]))  # the ring did not close: back under the new name
                except OSError:
                    pass  # left under the temporary name; reported and kept below
    done = [entry for entry, _indices in groups if entry in restored and not open_(entry)]
    skipped: list[dict] = []
    retry: set = set()
    for entry, indices in groups:
        problems, blocked = outcome[entry]
        skipped.extend(problems)
        skipped.extend({"entry": entry, "path": step["to"], "reason": OLD_NAME_TAKEN} for step in blocked)
        if open_(entry):
            # Half undone: its renames stay journaled so undo can run again.
            retry.update(index for index in indices if steps[index].get("kind") == "rename")
    return done, skipped, retry, put_back


def _ring_entry(left: list, outcome: dict, tried: dict) -> Any:
    """An entry whose every blocked old name is held by another blocked entry's file, or None."""
    held: dict = {}
    for entry, _indices in left:
        for step in outcome[entry][1]:
            held.setdefault(_norm(step["to"]), set()).add(entry)
    for entry, _indices in left:
        problems, blocked = outcome[entry]
        if entry in tried or not blocked or any(problem["reason"] != IN_RECYCLE_BIN for problem in problems):
            continue
        if all(held.get(_norm(step["from"]), set()) - {entry} for step in blocked):
            return entry
    return None


def _finish_entry(entry_steps, journal, indices, run_id, trash, record) -> Optional[dict]:
    entry = entry_steps[0].get("entry")
    problem = _copy_problem(entry_steps)
    if problem is not None:
        return problem
    trashed: list[dict] = []
    renamed: list[dict] = []
    for step in entry_steps:
        if step.get("done"):
            continue
        src = Path(step["from"])
        if step.get("kind") == "trash":
            if os.path.lexists(src):
                trash_sources = {_norm(other["from"]) for other in entry_steps}
                problem = _survivor_problem([step], trash_sources)
                if problem is not None:
                    record(trashed)
                    return problem
                try:
                    trash(str(src))
                except OSError as exc:
                    record(trashed)
                    return {"entry": entry, "path": str(src), "reason": f"could not move to the Recycle Bin ({exc})"}
                if os.path.lexists(src):
                    record(trashed)  # still there: the rest of the entry stays as well
                    return {"entry": entry, "path": str(src), "reason": STILL_THERE}
                trashed.append(step)
            continue
        dst = Path(step["to"])
        try:
            _finish_move(step, run_id)
        except OSError as exc:
            _restored, problems = _undo_entry(entry_steps, run_id)
            record(trashed)
            return {"entry": entry, "path": str(dst), "reason": _with_problems(_reason(exc), problems)}
        renamed.append(step)
    # Every rename of the entry is under its new name now; a removal only when this run binned it.
    binned = {id(step) for step in trashed}
    went_through = [
        index for index, step in zip(indices, entry_steps) if step.get("kind") == "rename" or id(step) in binned
    ]
    journal.mark_done_many(indices, applied_to=went_through)
    record(trashed + renamed)
    return None


def _finish_move(step: dict, run_id: str) -> None:
    src, dst = Path(step["from"]), Path(step["to"])
    tmp = _find_tmp(dst, run_id)
    if tmp is not None:
        _rename_new(tmp, dst)
        return
    if _same_name(src, dst):
        if _exists_as(dst) and not _taken(src, dst):
            return  # Already renamed (not a second file of that spelling on a case-sensitive volume).
        if not os.path.lexists(src):
            raise FileNotFoundError(errno.ENOENT, SOURCE_MISSING, str(src))
        _move(src, dst, step.get("suffix"), run_id)
        return
    if step.get("applied") and os.path.lexists(dst):
        return  # Renamed; an undo found its old name taken by another file.
    if os.path.lexists(src):
        _move(src, dst, step.get("suffix"), run_id)
        return
    if os.path.lexists(dst):
        return  # Renamed before the crash, not yet marked.
    raise FileNotFoundError(errno.ENOENT, SOURCE_MISSING, str(src))


# --- records that store paths ---------------------------------------------


def send2trash_available() -> bool:
    """Whether duplicates can be sent to the Recycle Bin (the package may be missing)."""
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return False
    return True


def rewrite_paths(value: Any, mapping: dict[str, str]) -> bool:
    """Point every ``...Path`` value in ``value`` (in place) that names an old path at its new one."""
    lookup = {_norm(old): new for old, new in mapping.items()}
    return bool(lookup) and _rewrite_paths(value, lookup, False)


def update_saved_paths(data_dir: Path, mapping: dict[str, str]) -> None:
    """Point every saved ``…Path`` value that names an old path at its new path."""
    lookup = {_norm(old): new for old, new in mapping.items()}
    if not lookup:
        return
    for name in SAVED_PATH_FILES:
        path = Path(data_dir) / name
        if not path.is_file():
            continue
        payload = read_data_file(path)
        if payload is None:
            continue  # Unreadable or corrupt: leave it for its owner to handle.
        if _rewrite_paths(payload, lookup, False):
            write_json(path, payload)


def _rewrite_paths(value: Any, lookup: dict[str, str], under_path_key: bool) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in value.items():
            is_path_key = isinstance(key, str) and key.endswith("Path")
            if is_path_key and isinstance(item, str):
                new = lookup.get(_norm(item))
                if new is not None and new != item:
                    value[key] = new
                    changed = True
            elif isinstance(item, (dict, list)):
                changed = _rewrite_paths(item, lookup, is_path_key) or changed
    elif isinstance(value, list):
        for position, item in enumerate(value):
            if under_path_key and isinstance(item, str):
                new = lookup.get(_norm(item))
                if new is not None and new != item:
                    value[position] = new
                    changed = True
            elif isinstance(item, (dict, list)):
                changed = _rewrite_paths(item, lookup, False) or changed
    return changed


def refresh_sidecar_title(info_path: Path, live_title: str) -> None:
    """Bring the ``.civitai.info``'s saved model title up to date."""
    info = read_json(Path(info_path))
    if info is None:
        return
    model = info.get("model")
    if not isinstance(model, dict):
        model = {}
        info["model"] = model
    if model.get("name") == live_title:
        return
    model["name"] = live_title
    write_json(Path(info_path), info)
