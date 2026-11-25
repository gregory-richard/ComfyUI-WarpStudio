"""The renamer's naming scheme: ``creator - title - version [precision] (base)``.

This module only formats and checks names; it never cleans or shortens them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

SEPARATOR = " - "
FORBIDDEN_CHARS = set('<>:"/\\|?*')
RESERVED_STEMS = (
    {"con", "prn", "aux", "nul"}
    | {"com%d" % n for n in range(1, 10)}
    | {"lpt%d" % n for n in range(1, 10)}
)
REQUIRED_PARTS = ("creator", "title", "base")
PART_NAMES = ("creator", "title", "version", "precision", "base")

_BASE_SUFFIX = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


@dataclass
class NameParts:
    creator: str
    title: str
    version: str = ""
    precision: str = ""
    base: str = ""


def format_part(text: str) -> str:
    """Lowercase, collapse whitespace to single spaces, trim. Nothing else."""
    return " ".join(text.split()).lower()


def assemble(parts: NameParts) -> str:
    """Join the parts into a file stem (no extension)."""
    pieces = [parts.creator, parts.title]
    tail = " ".join(p for p in (parts.version, parts.precision) if p)
    if tail:
        pieces.append(tail)
    stem = SEPARATOR.join(pieces)
    if parts.base:
        stem += " (%s)" % parts.base
    return stem


def parse_stem(stem: str) -> NameParts:
    """Best-effort split of an existing stem back into parts."""
    base = ""
    rest = stem
    match = _BASE_SUFFIX.match(stem)
    if match:
        rest, base = match.group(1), match.group(2).strip()
    pieces = [p.strip() for p in rest.split(SEPARATOR)]
    creator = pieces[0] if pieces else ""
    title = pieces[1] if len(pieces) > 1 else ""
    version = " ".join(p for p in pieces[2:] if p)
    return NameParts(creator, title, version, "", base)


def own_name(stems: Iterable[str]) -> Optional[NameParts]:
    """The parts of the first stem that has both a creator and a title, else None."""
    for stem in stems:
        parts = parse_stem(stem)
        if parts.creator and parts.title:
            return parts
    return None


def name_tail(stem: str) -> str:
    """The version and base of a named stem, e.g. ``xxl (moss)``; the stem itself otherwise."""
    parts = parse_stem(stem)
    if not (parts.creator and parts.title):
        return stem
    tail = parts.version
    if parts.base:
        tail += " (%s)" % parts.base
    return tail.strip() or stem


def _has_forbidden(text: str) -> bool:
    return any(ch in FORBIDDEN_CHARS or ord(ch) < 32 or ord(ch) == 127 for ch in text)


def part_problems(parts: NameParts) -> List[str]:
    """Problem codes for the parts, in part order."""
    problems: List[str] = []
    for name in PART_NAMES:
        value = getattr(parts, name)
        if name in REQUIRED_PARTS and not value.strip():
            problems.append("empty:%s" % name)
        if SEPARATOR in value:
            problems.append("separator:%s" % name)
        if _has_forbidden(value):
            problems.append("forbidden:%s" % name)
    return problems


def stem_problems(stem: str) -> List[str]:
    """Problem codes for a whole stem that Windows would refuse or mangle."""
    problems: List[str] = []
    if stem.lower() in RESERVED_STEMS:
        problems.append("reserved")
    if stem.endswith((".", " ")):
        problems.append("trailing")
    return problems


def target_path(folder: Path, stem: str, suffix: str) -> Path:
    """``folder / (stem + suffix)``; never ``with_suffix`` (stems contain dots)."""
    return folder / (stem + suffix)
