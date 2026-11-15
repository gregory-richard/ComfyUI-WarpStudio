"""Apply the model renamer's selected renames to the library, with a crash-recovery journal.

Every entry's weights file is renamed together with each file in its folder that
shares its stem (``.civitai.info``, ``.preview.png``, anything another tool left).
An existing target is never overwritten. A file Windows has locked is skipped.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, Callable, Optional

from .hashing import HashingCancelled, sha256_file
from .constants import INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .naming_scheme import target_path
from .rename_store import RenameJournal
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

_STEM_SOURCES = tuple(SUPPORTED_MODEL_EXTENSIONS) + (INFO_SIDECAR_SUFFIX,)
_KNOWN_SUFFIXES = (INFO_SIDECAR_SUFFIX, PREVIEW_SIDECAR_SUFFIX) + tuple(SUPPORTED_MODEL_EXTENSIONS)


# --- siblings and planning -------------------------------------------------


def _norm(path: Any) -> str:
    return os.path.normpath(str(path)).lower()


def _is_weights(name: str) -> bool:
    return name.lower().endswith(tuple(SUPPORTED_MODEL_EXTENSIONS))


def assign_siblings(folder: Path) -> dict[str, list[Path]]:
    """Lowercased stem -> its files; each file goes to the longest stem it starts with."""
    try:
        files = sorted((p for p in Path(folder).iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError:
        return {}
    stems = set()
    for path in files:
        lower = path.name.lower()
        for suffix in _STEM_SOURCES:
            if lower.endswith(suffix) and len(lower) > len(suffix):
                stems.add(lower[: -len(suffix)])
    longest_first = sorted(stems, key=len, reverse=True)
    result: dict[str, list[Path]] = {}
    for path in files:
        lower = path.name.lower()
        if TMP_MARKER in lower:
            continue  # A leftover of an interrupted case-only rename belongs to recovery.
        for stem in longest_first:
            if lower.startswith(stem + "."):
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


def plan(
    rows: list[dict], selected_keys: set[str], remove_duplicate_keys: set[str]
) -> tuple[list[dict], list[dict]]:
    """Rename steps for selected ``change`` rows, trash steps for chosen duplicates, and the skips.

    Trash steps come first so a survivor can take over a removed copy's name.
    An entry whose own file is gone, or is not the file the dry run saw, gets no
    steps: it is reported in the skips (``{"entry", "path", "reason"}``) instead.
    """
    cache: dict[str, dict[str, list[Path]]] = {}
    skipped: list[dict] = []

    def siblings(row: dict) -> list[Path]:
        folder = str(row.get("folder") or "")
        if folder not in cache:
            cache[folder] = assign_siblings(Path(folder))
        return list(cache[folder].get(str(row.get("oldStem") or "").lower(), []))

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
        if row.get("status") != "change" or key not in selected_keys or not old or not new or old == new:
            continue
        change_keys.add(key)
        files = checked_siblings(row)
        if files is None:
            continue
        folder = Path(row["folder"])
        for path in _ordered(files, _norm(key)):
            suffix = path.name[len(old):]
            target = target_path(folder, new, suffix)
            if str(target) == str(path):
                continue
            renames.append({"kind": "rename", "from": str(path), "to": str(target), "entry": key, "suffix": suffix})
    for key in sorted(k for k in selected_keys if k not in change_keys):
        skipped.append({"entry": key, "path": key, "reason": NOT_A_RENAME})

    renamed = {_norm(step["from"]) for step in renames}
    rows_by_key = {row.get("key"): row for row in rows}
    trashes: list[dict] = []
    for row in rows:
        key = row.get("key")
        survivor = rows_by_key.get(row.get("survivorKey"))
        if row.get("status") != "duplicate" or key not in remove_duplicate_keys:
            continue
        files = checked_siblings(row)
        if files is None:
            continue
        files = _ordered(files, _norm(key))
        # Exactly the survivor's own file; apply refuses the removal when it is gone.
        keep = _survivor_file(survivor) if survivor is not None and survivor is not row else ""
        shares_stem = survivor is not None and _norm(survivor.get("folder")) == _norm(row.get("folder")) and str(
            survivor.get("oldStem") or ""
        ).lower() == str(row.get("oldStem") or "").lower()
        other_weights = [path for path in files if _is_weights(path.name) and _norm(path) != _norm(key)]
        if shares_stem or other_weights:
            # The sidecars are shared with a model that stays; only the copy's own file goes.
            files = [path for path in files if _norm(path) == _norm(key)]
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


def plan_steps(rows: list[dict], selected_keys: set[str], remove_duplicate_keys: set[str]) -> list[dict]:
    """The steps of ``plan`` without its skips."""
    return plan(rows, selected_keys, remove_duplicate_keys)[0]


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
    """The same path apart from letter case (one file on Windows)."""
    return _norm(a) == _norm(b)


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
    marker = (TMP_MARKER + run_id[:8]).lower()
    try:
        names = os.listdir(dst.parent)
    except OSError:
        return None
    for name in names:
        lower = name.lower()
        if marker in lower and lower.replace(marker, "", 1) == dst.name.lower():
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
        if src.name != dst.name and _exists_as(dst):
            _move(dst, src, step.get("suffix"), run_id)
            return True
        return False
    if os.path.lexists(dst) and not os.path.lexists(src):
        _move(dst, src, step.get("suffix"), run_id)
        return True
    return False


def _undo_entry(steps: list[dict], run_id: str) -> tuple[bool, list[dict]]:
    """Undo an entry's rename steps in reverse. (anything restored, problems)."""
    restored = False
    problems: list[dict] = []
    for step in reversed(steps):
        if step.get("kind") == "trash":
            if step.get("done"):
                problems.append({"entry": step.get("entry"), "path": step.get("from"), "reason": IN_RECYCLE_BIN})
            continue
        try:
            restored = _undo_move(step, run_id) or restored
        except OSError as exc:
            problems.append({"entry": step.get("entry"), "path": step.get("to"), "reason": f"could not undo: {_reason(exc)}"})
    return restored, problems


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
    rename_steps: list[dict] = []
    for entry, indices in groups_in:
        if entry in refused:
            continue
        entry_steps = [dict(steps[index]) for index in indices]
        problem = _blocked(entry_steps, freed)
        if problem is not None:
            skipped.append(problem)
        elif entry_steps[0].get("kind") == "trash":
            trash_steps.extend(entry_steps)
        else:
            rename_steps.extend(entry_steps)
    # Removals run first so the names they free are free when a survivor is renamed.
    steps = trash_steps + rename_steps
    run_id = journal.begin(steps)
    groups = _group(steps)
    still_open: list[int] = []
    for position, (entry, indices) in enumerate(groups):
        if control is not None:
            control.wait_if_paused()
            if control.is_cancelled():
                break
        problem = _apply_entry(steps, indices, journal, run_id, trash, control)
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


def _survivor_problem(entry_steps: list[dict], trash_sources: set) -> Optional[dict]:
    """A removal needs the survivor's own file on disk, and not among the files removed."""
    first = entry_steps[0]
    if first.get("kind") != "trash":
        return None
    keep = first.get("to")
    if not keep or not os.path.lexists(keep) or _norm(keep) in trash_sources:
        return {"entry": first.get("entry"), "path": str(keep or ""), "reason": SURVIVOR_GONE}
    return None


def _blocked(entry_steps: list[dict], freed: Optional[set] = None) -> Optional[dict]:
    """Why an entry's renames cannot start, or None. Targets in ``freed`` count as free."""
    for step in entry_steps:
        if step.get("kind") != "rename":
            continue
        src, dst = Path(step["from"]), Path(step["to"])
        if not os.path.lexists(src):
            return {"entry": step.get("entry"), "path": str(src), "reason": SOURCE_MISSING}
        if os.path.lexists(dst) and not _same_name(src, dst) and _norm(dst) not in (freed or ()):
            return {"entry": step.get("entry"), "path": str(dst), "reason": TARGET_EXISTS}
    return None


def _copy_problem(entry_steps: list[dict], should_stop: Optional[Callable[[], bool]] = None) -> Optional[dict]:
    """A copy with weights goes only when it is byte-identical to the survivor's file.

    The Recycle Bin can delete a file outright (over its quota, a drive without
    one), so the kept copy must really be the same model. Sidecars need no check.
    """
    for step in entry_steps:
        if step.get("kind") != "trash" or step.get("done") or not _is_weights(Path(step["from"]).name):
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


def _apply_entry(steps, indices, journal, run_id, trash, control=None) -> Optional[dict]:
    entry = steps[indices[0]].get("entry")

    def give_up(path: Any, reason: str) -> dict:
        # The entry's steps are settled (not to be run by "finish"), except a file
        # stranded under a temporary name: that step stays open for recovery.
        stranded = _stranded([steps[index] for index in indices], run_id)
        stranded_keys = {_norm(path) for path in stranded}
        settle = [
            index
            for index in indices
            if not (steps[index].get("kind") == "rename" and _tmp_key(steps[index], run_id) in stranded_keys)
        ]
        journal.mark_done_many(settle)
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
        for index in indices:
            path = steps[index]["from"]
            if os.path.lexists(path):
                try:
                    trash(str(path))
                except OSError as exc:
                    return give_up(path, f"could not move to the Recycle Bin ({exc})")
        journal.mark_done_many(indices)
        return None

    # Checked again with nothing counted as freed: an earlier entry may have taken
    # a target, or a duplicate's removal may have failed.
    problem = _blocked([steps[index] for index in indices])
    if problem is not None:
        give_up(problem["path"], problem["reason"])
        return problem

    finished: list[dict] = []
    for index in indices:
        step = steps[index]
        try:
            _move(Path(step["from"]), Path(step["to"]), step.get("suffix"), run_id)
        except OSError as exc:
            _restored, problems = _undo_entry(finished, run_id)
            return give_up(step["from"], _with_problems(_reason(exc), problems))
        finished.append(step)
    journal.mark_done_many(indices)
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
    retry: set = set()
    for entry, indices in _group(steps):
        entry_steps = [steps[index] for index in indices]
        if action == "undo":
            restored, problems = _undo_entry(entry_steps, run_id)
            skipped.extend(problems)
            if any(problem["reason"] != IN_RECYCLE_BIN for problem in problems):
                # Half undone: its renames stay journaled so undo can run again.
                retry.update(index for index in indices if steps[index].get("kind") == "rename")
            elif restored:
                done.append(entry)
            continue
        if all(step.get("done") for step in entry_steps):
            continue
        problem = _finish_entry(entry_steps, journal, indices, run_id, trash)
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
        journal.keep_only(sorted(retry))
    else:
        journal.clear()
    return {"done": done, "skipped": skipped}


def _finish_entry(entry_steps, journal, indices, run_id, trash) -> Optional[dict]:
    entry = entry_steps[0].get("entry")
    problem = _copy_problem(entry_steps)
    if problem is not None:
        return problem
    for step in entry_steps:
        if step.get("done"):
            continue
        src = Path(step["from"])
        if step.get("kind") == "trash":
            if os.path.lexists(src):
                trash_sources = {_norm(other["from"]) for other in entry_steps}
                problem = _survivor_problem([step], trash_sources)
                if problem is not None:
                    return problem
                try:
                    trash(str(src))
                except OSError as exc:
                    return {"entry": entry, "path": str(src), "reason": f"could not move to the Recycle Bin ({exc})"}
            continue
        dst = Path(step["to"])
        try:
            _finish_move(step, run_id)
        except OSError as exc:
            _restored, problems = _undo_entry(entry_steps, run_id)
            return {"entry": entry, "path": str(dst), "reason": _with_problems(_reason(exc), problems)}
    journal.mark_done_many(indices)
    return None


def _finish_move(step: dict, run_id: str) -> None:
    src, dst = Path(step["from"]), Path(step["to"])
    tmp = _find_tmp(dst, run_id)
    if tmp is not None:
        _rename_new(tmp, dst)
        return
    if _same_name(src, dst):
        if _exists_as(dst):
            return  # Already renamed.
        if not os.path.lexists(src):
            raise FileNotFoundError(errno.ENOENT, SOURCE_MISSING, str(src))
        _move(src, dst, step.get("suffix"), run_id)
        return
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
