from __future__ import annotations

import json
import re
from pathlib import Path

from .fallback_types import FallbackResult
from .file_precision import safetensors_header

_SAMPLE_RE = re.compile(r"^\d+__(\d{9})_(\d+)\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


def base_names_from_codes(codes: dict[str, str]) -> dict[str, str]:
    """Reverse a name -> code map. The first name per code wins, in file order."""
    names: dict[str, str] = {}
    for name, code in codes.items():
        names.setdefault(code, name)
    return names


def _json_dict(value) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _step_of(training_info: dict | None) -> int | None:
    step = (training_info or {}).get("step")
    if isinstance(step, int) and not isinstance(step, bool) and step >= 0:
        return step
    return None


def _safe_folder_name(name) -> bool:
    if not isinstance(name, str) or not name.strip() or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    return Path(name).name == name


def pick_sample(samples_dir: Path, step: int | None) -> Path | None:
    """Pick a sample image: the file's step, else the nearest earlier one; no step means the latest."""
    try:
        entries = list(Path(samples_dir).iterdir())
    except OSError:
        return None
    found: list[tuple[int, int, Path]] = []
    for entry in entries:
        match = _SAMPLE_RE.match(entry.name)
        if match and entry.is_file():
            found.append((int(match.group(1)), int(match.group(2)), entry))
    if step is not None:
        found = [item for item in found if item[0] <= step]
    if not found:
        return None
    best_step = max(item[0] for item in found)
    return min((item for item in found if item[0] == best_step), key=lambda item: item[1])[2]


def lookup_own_training(
    model_path: Path,
    folders: list[Path],
    creator: str,
    base_names: dict[str, str],
) -> FallbackResult | None:
    """Describe a LoRA the user trained themselves, using the training output folders."""
    if not folders:
        return None
    header = safetensors_header(Path(model_path))
    meta = header.get("__metadata__") if header else None
    if not isinstance(meta, dict):
        return None

    software = _json_dict(meta.get("software"))
    training_info = _json_dict(meta.get("training_info"))
    is_training = (software is not None and software.get("name") == "ai-toolkit") or "training_info" in meta
    if not is_training:
        return None

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        name = meta.get("ss_output_name")
    if not _safe_folder_name(name):
        return None

    run_dir = None
    for folder in folders:
        try:
            candidate = Path(folder) / name
            if candidate.is_dir():
                run_dir = candidate
                break
        except OSError:
            continue
    if run_dir is None:
        return None

    step = _step_of(training_info)
    code = meta.get("ss_base_model_version")
    base_model = base_names.get(code, "") if isinstance(code, str) else ""
    return FallbackResult(
        source="ai-toolkit",
        url="",
        title=name.replace("_", " "),
        creator=creator,
        version_name=f"step {step}" if step is not None else "",
        base_model=base_model,
        image_path=pick_sample(run_dir / "samples", step),
        final=True,
    )
