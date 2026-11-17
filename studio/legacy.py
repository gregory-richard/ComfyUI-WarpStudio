"""Detect an old WarpPipe or Civitai Updater that is still active.

Those packages attach to the same node types and panel as Warp Studio, so while
one of them is active Warp Studio switches itself off. The goodbye releases
(WarpPipe 5.1.0, Civitai Updater 1.10.0) switch themselves off instead and are
not counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GOODBYE_VERSIONS = {"warppipe": (5, 1, 0), "civitai-updater": (1, 10, 0)}
DISPLAY = {"warppipe": "WarpPipe", "civitai-updater": "Civitai Updater"}


@dataclass(frozen=True)
class OldPackage:
    name: str
    version: str
    path: Path


def read_project(folder: Path) -> tuple[str, str] | None:
    """Name and version from the [project] table of folder/pyproject.toml."""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not table:
        return None
    values = {}
    for key in ("name", "version"):
        found = re.search(rf'^\s*{key}\s*=\s*["\']([^"\']*)["\']', table.group(1), re.MULTILINE)
        if not found:
            return None
        values[key] = found.group(1)
    return values["name"], values["version"]


def version_tuple(version: str) -> tuple[int, int, int]:
    parts = []
    for piece in version.strip().split("."):
        leading = re.match(r"\d+", piece)
        if not leading:
            break
        parts.append(int(leading.group()))
    parts = [*parts, 0, 0, 0][:3]
    return parts[0], parts[1], parts[2]


def find_active_old_packages(custom_node_dirs: list[Path], own_root: Path) -> list[OldPackage]:
    try:
        own = own_root.resolve()
    except OSError:
        own = own_root
    found = []
    for directory in custom_node_dirs:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.name.startswith(".") or child.name.endswith(".disabled"):
                continue
            if not child.is_dir():
                continue
            try:
                if child.resolve() == own:
                    continue
            except OSError:
                continue
            project = read_project(child)
            if project is None:
                continue
            name, version = project
            goodbye = GOODBYE_VERSIONS.get(name)
            if goodbye and version_tuple(version) < goodbye:
                found.append(OldPackage(name, version, child))
    return found


def stand_down_message(found: list[OldPackage]) -> str:
    names = [f"{DISPLAY.get(p.name, p.name)} {p.version}" for p in found]
    if len(names) == 1:
        subject, rest = names[0], "it"
        verb = "is"
    else:
        subject, rest = " and ".join(names), "them"
        verb = "are"
    return (
        f"Warp Studio is installed but switched off, because {subject} {verb} still "
        f"installed. Update or remove {rest} in the Manager, then restart."
    )
