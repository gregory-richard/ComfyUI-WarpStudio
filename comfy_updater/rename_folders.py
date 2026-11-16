"""Files filed under the wrong base: move them to the folder their base lives in.

A library kept in one folder per base (``loras/pony``, ``loras/ill``...) shows it
in the files themselves: most files in ``pony`` end in ``(pony)``. A base's home
is the folder that holds most of its files, where that base is also most of
what the folder holds. A file whose Civitai base has a home elsewhere, and that
sits in another base's home, moves there. Files in folders that are no base's
home (the root folder, a folder of one's own trainings) never move, and nothing
moves to a folder that does not exist.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Dict, Iterable, List, Set, Tuple

from .naming_scheme import parse_stem

NAMED = ("change", "unchanged")
FOLDER_FLAGS = {"exists", "collision"}

# A base's home holds at least this many of its files, and this share of them;
# the base is at least this share of what the folder holds.
MIN_FILES = 3
MIN_SHARE_OF_BASE = 0.6
MIN_SHARE_OF_FOLDER = 0.5


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _code(row: dict) -> str:
    """The base code a row ends up with: the new name's, or its current name's when it keeps it."""
    if row.get("status") in NAMED:
        return str((row.get("parts") or {}).get("base") or "")
    return parse_stem(str(row.get("oldStem") or "")).base


def home_folders(rows: Iterable[dict]) -> Dict[Tuple[str, str], str]:
    """{(parent folder, base code): the folder that is that base's home there}."""
    by_base: Dict[Tuple[str, str], Counter] = {}
    by_folder: Dict[str, Counter] = {}
    paths: Dict[str, str] = {}
    for row in rows:
        folder = str(row.get("folder") or "")
        code = _code(row)
        if not folder or not code:
            continue
        key = _norm(folder)
        paths.setdefault(key, folder)
        parent = _norm(os.path.dirname(folder))
        by_base.setdefault((parent, code), Counter())[key] += 1
        by_folder.setdefault(key, Counter())[code] += 1
    homes: Dict[Tuple[str, str], str] = {}
    for (parent, code), folders in by_base.items():
        folder, count = folders.most_common(1)[0]
        total = sum(folders.values())
        held = by_folder[folder]
        if count < MIN_FILES or count / total < MIN_SHARE_OF_BASE:
            continue
        if held[code] / sum(held.values()) < MIN_SHARE_OF_FOLDER:
            continue
        homes[(parent, code)] = paths[folder]
    return homes


def plan_moves(rows: List[dict], *, skip_flags: Iterable[str] = ("base-conflict",)) -> None:
    """Set ``newFolder`` on the named rows filed under another base's home (and drop it elsewhere).

    A row that only moves becomes a "change". A row whose base Civitai may have
    wrong (a ``base-conflict`` flag) stays where it is.
    """
    homes = home_folders(rows)
    base_homes = {_norm(folder) for folder in homes.values()}
    for row in rows:
        moved = bool(row.pop("newFolder", None))
        if row.get("status") not in NAMED:
            continue
        folder = str(row.get("folder") or "")
        target = homes.get((_norm(os.path.dirname(folder)), _code(row)))
        doubtful = any(str(flag).split(":", 1)[0] in set(skip_flags) for flag in row.get("flags") or [])
        if target and _norm(target) != _norm(folder) and _norm(folder) in base_homes and not doubtful:
            row["newFolder"] = target
            if row.get("status") == "unchanged":
                row["status"] = "change"
                row["selected"] = not row.get("flags")
            changed = list(row.get("changed") or [])
            if "folder" not in changed:
                changed.append("folder")
            row["changed"] = changed
        elif moved:
            row["changed"] = [part for part in row.get("changed") or [] if part != "folder"]
            if row.get("newStem") == row.get("oldStem"):
                row["status"] = "unchanged"
                row["selected"] = False


def target_folder(row: dict) -> str:
    """Where a row's files end up: its new folder, else the folder it is in."""
    return str(row.get("newFolder") or row.get("folder") or "")


def recheck_folder_flags(rows: List[dict], folders: Set[str], listing: Dict[str, Set[str]]) -> None:
    """Recompute "exists" and "collision" for the named rows that come from or go to these folders.

    ``folders`` and ``listing`` use lowercased paths; ``listing`` holds the
    lowercased stems on disk per folder. A stem on disk counts as free when a
    named row of this dry run takes its file away (renamed or moved).
    """
    named = [row for row in rows if row.get("status") in NAMED]
    leaving: Dict[str, Set[str]] = {}
    by_new: Dict[Tuple[str, str], List[dict]] = {}
    for row in named:
        source = str(row.get("folder") or "").lower()
        if row.get("status") == "change":
            leaving.setdefault(source, set()).add(str(row.get("oldStem") or "").lower())
        by_new.setdefault((target_folder(row).lower(), str(row.get("newStem") or "").lower()), []).append(row)
    for row in named:
        target = target_folder(row).lower()
        if target not in folders and str(row.get("folder") or "").lower() not in folders:
            continue
        new = str(row.get("newStem") or "").lower()
        flags = [flag for flag in row.get("flags") or [] if flag not in FOLDER_FLAGS]
        if row.get("status") == "change":
            on_disk = set(listing.get(target) or ()) - leaving.get(target, set())
            if new in on_disk:
                flags.append("exists")
            if len(by_new[(target, new)]) > 1:
                flags.append("collision")
        row["flags"] = flags
        row["selected"] = row.get("status") == "change" and not flags


def move_target_folders(rows: Iterable[dict]) -> Set[str]:
    """The lowercased folders the rows' files move into."""
    return {str(row["newFolder"]).lower() for row in rows if row.get("newFolder")}

