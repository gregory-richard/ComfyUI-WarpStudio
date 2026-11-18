"""A LoRA's files on disk: resolving a name safely, and the info files whose weights are gone."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Collection, Optional

from .constants import INFO_SIDECAR_SUFFIX, SUPPORTED_MODEL_EXTENSIONS
from .rename_apply import assign_siblings
from .sidecar import model_file_path, read_json, sidecar_identifies_model


def lora_roots() -> list[Path]:
    """ComfyUI's ``loras`` folders; empty outside ComfyUI or when they can't be read."""
    try:
        import folder_paths

        return [Path(str(path)) for path in folder_paths.get_folder_paths("loras")]
    except Exception:
        return []


def _norm(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inside(root: Path, name: str) -> Optional[Path]:
    """``root / name`` when it stays inside the root; None for an absolute or escaping name."""
    if not name or "\0" in name:
        return None
    if os.path.isabs(name) or name[:1] in "/\\" or (len(name) > 1 and name[1] == ":"):
        return None
    base = _norm(root)
    target = _norm(Path(root) / name)
    try:
        if os.path.commonpath([base, target]) != base or target == base:
            return None
    except ValueError:
        return None
    return Path(root) / name


def resolve_name(roots: list[Path], name: str) -> Optional[Path]:
    """The existing file ``name`` names in the first root that has it, never outside a root."""
    for root in roots:
        candidate = _inside(root, name)
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _weights_extension(name: str) -> str:
    lower = name.lower()
    return next((ext for ext in SUPPORTED_MODEL_EXTENSIONS if lower.endswith(ext)), "")


def _weight_stems(folder: Path) -> set[str]:
    stems: set[str] = set()
    try:
        names = os.listdir(folder)
    except OSError:
        return stems
    for name in names:
        extension = _weights_extension(name)
        if extension and len(name) > len(extension):
            stems.add(name[: -len(extension)].lower())
    return stems


def missing_loras(roots: list[Path], on_disk: Collection[str] = frozenset()) -> list[dict]:
    """Info files that identify a model but have no weights beside them."""
    known = {_norm(name) for name in on_disk}
    found: list[dict] = []
    for root in roots:
        if not Path(root).is_dir():
            continue
        for folder, _dirs, names in os.walk(root):
            infos = [n for n in names if n.lower().endswith(INFO_SIDECAR_SUFFIX)]
            if not infos:
                continue
            stems = _weight_stems(Path(folder))
            for name in sorted(infos):
                if name[: -len(INFO_SIDECAR_SUFFIX)].lower() in stems:
                    continue
                info_path = Path(folder) / name
                info = read_json(info_path)
                if not sidecar_identifies_model(info):
                    continue
                identifier = os.path.relpath(model_file_path(info_path, info), root)
                key = _norm(identifier)
                if key in known:
                    continue
                known.add(key)
                found.append({"id": identifier, "root": Path(root), "infoPath": info_path, "info": info})
    return sorted(found, key=lambda entry: entry["id"].casefold())


def missing_info(roots: list[Path], name: str) -> Optional[tuple[Path, dict]]:
    """The info file behind a listed id, while its weights are still absent."""
    extension = _weights_extension(name)
    if not extension:
        return None
    stem = name[: -len(extension)]
    for root in roots:
        info_path = _inside(root, stem + INFO_SIDECAR_SUFFIX)
        if info_path is None or not info_path.is_file():
            continue
        if info_path.name[: -len(INFO_SIDECAR_SUFFIX)].lower() in _weight_stems(info_path.parent):
            return None
        info = read_json(info_path)
        if not sidecar_identifies_model(info):
            return None
        return info_path, info
    return None


def stem_files(weights: Path) -> list[Path]:
    """The weights and every file beside them that shares their stem, weights first."""
    weights = Path(weights)
    if not weights.is_file():
        return []
    siblings = assign_siblings(weights.parent).get(weights.stem.lower(), [])
    mine = _norm(weights)
    others = [p for p in siblings if _norm(p) != mine]
    if any(_weights_extension(p.name) for p in others):
        return [weights]  # Two weights share these sidecars, so they are not this one's alone.
    return [weights] + sorted(others, key=lambda p: p.name.lower())
