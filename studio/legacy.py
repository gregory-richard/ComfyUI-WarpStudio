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
# WarpPipe's project names before it became "warppipe" (renamed 2026-02-09).
PROJECT_ALIASES = {"warp-pipe-comfyui": "warppipe", "comfyui-warppipe": "warppipe"}


@dataclass(frozen=True)
class OldPackage:
    name: str
    version: str
    path: Path


def _project_values(folder: Path) -> dict:
    """The name and/or version found in the [project] table (missing keys absent)."""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not table:
        return {}
    values = {}
    for key in ("name", "version"):
        found = re.search(rf"^\s*{key}\s*=\s*[\"']([^\"']*)[\"']", table.group(1), re.MULTILINE)
        if found:
            values[key] = found.group(1)
    return values


def read_project(folder: Path) -> tuple[str, str] | None:
    """Name and version from the [project] table of folder/pyproject.toml."""
    values = _project_values(folder)
    if "name" in values and "version" in values:
        return values["name"], values["version"]
    return None


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
            values = _project_values(child)
            if "name" not in values:
                # No readable project name: recognise a clone by its files.
                if (child / "warp_pipe.py").is_file():
                    found.append(OldPackage("warppipe", "unknown", child))
                elif (child / "comfy_updater" / "plugin.py").is_file():
                    found.append(OldPackage("civitai-updater", "unknown", child))
                continue
            name = PROJECT_ALIASES.get(values["name"], values["name"])
            goodbye = GOODBYE_VERSIONS.get(name)
            version = values.get("version", "")
            if goodbye and version_tuple(version) < goodbye:
                found.append(OldPackage(name, version, child))
    return found


def stand_down_message(found: list[OldPackage]) -> str:
    names = [
        DISPLAY.get(p.name, p.name)
        if p.version == "unknown"
        else f"{DISPLAY.get(p.name, p.name)} {p.version}"
        for p in found
    ]
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
