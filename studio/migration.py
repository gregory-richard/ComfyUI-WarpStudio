"""One-time copy of WarpPipe's and Civitai Updater's data to Warp Studio's names.

Everything here copies, never moves; never overwrites an existing target and
never deletes. A failed copy is recorded in the report and the marker is not
written, so the next start tries again.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("WarpStudio")

SETTING_PREFIXES = (("WarpPipe.", "WarpStudio."), ("CivitaiUpdater.", "WarpStudio.Updater."))
MARKER = Path("warp-studio") / "migrated.json"

_STUDIO_DIR = "warp-studio"
_USER_FILES = (
    ("warppipe_llm_keys.json", "llm_keys.json"),
    ("warppipe_llm_log.jsonl", "llm_log.jsonl"),
    ("warppipe_hashes.json", "hashes.json"),
)
_OLD_CHATS = "warppipe_prompt_chats.json"
_NEW_CHATS = "warp_studio_prompt_chats.json"
_SETTINGS = "comfy.settings.json"
_OLD_DATA_DIR = ".civitai_updater"


@dataclass
class MigrationReport:
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _project_name(pyproject: Path) -> str | None:
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    table = re.search(r"^\[project\][ \t]*\r?\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not table:
        return None
    name = re.search(r"^name[ \t]*=[ \t]*[\"']([^\"']+)[\"']", table.group(1), re.M)
    return name.group(1).strip().lower() if name else None


def _newest_mtime(folder: Path) -> float:
    newest = 0.0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _candidate_children(custom_node_dir: Path) -> list[Path]:
    children: list[Path] = []
    try:
        for child in custom_node_dir.iterdir():
            if not child.is_dir():
                continue
            if child.name == ".disabled":
                children.extend(sub for sub in child.iterdir() if sub.is_dir())
            else:
                children.append(child)
    except OSError:
        pass
    return children


def find_old_updater_data(custom_node_dirs: list[Path], own_root: Path) -> Path | None:
    """The `.civitai_updater` folder of an installed Civitai Updater, if any."""
    try:
        own = own_root.resolve()
    except OSError:
        own = own_root
    found: list[Path] = []
    for directory in custom_node_dirs:
        for child in _candidate_children(Path(directory)):
            try:
                if child.resolve() == own:
                    continue
                if not (child / "comfy_updater" / "plugin.py").is_file():
                    continue
                if _project_name(child / "pyproject.toml") == "warp-studio":
                    continue
                data = child / _OLD_DATA_DIR
                if data.is_dir():
                    found.append(data)
            except OSError:
                continue
    if not found:
        return None
    return max(found, key=_newest_mtime)


def _copy_file(source: Path, target: Path) -> None:
    """Copy through a temp name so a failure never leaves a partial target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".migrating")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _has_files(folder: Path) -> bool:
    return any(path.is_file() for path in folder.rglob("*"))


def _copy_one(source: Path, target: Path, report: MigrationReport) -> None:
    if not source.is_file():
        return
    if target.exists():
        report.skipped.append(str(target))
        return
    try:
        _copy_file(source, target)
    except Exception as exc:
        report.errors.append(f"{source}: {exc}")
        logger.warning("Could not copy %s: %s", source, exc)
        return
    report.copied.append(str(target))
    logger.info("Copied %s -> %s", source, target)


def _copy_updater_data(source: Path, target: Path, report: MigrationReport) -> None:
    pending = target.with_name(target.name + ".pending")
    if target.exists() and _has_files(target):
        if not pending.exists():
            report.skipped.append(str(target))
            return
        # An earlier copy failed and Warp Studio then wrote fresh defaults here.
        # Keep them aside (never delete) and carry the old data over.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        aside = target.with_name(f"{target.name}.before-migration-{stamp}")
        counter = 1
        while aside.exists():
            counter += 1
            aside = target.with_name(f"{target.name}.before-migration-{stamp}-{counter}")
        try:
            os.replace(target, aside)
        except Exception as exc:
            report.errors.append(f"{target}: {exc}")
            logger.warning("Could not move %s aside: %s", target, exc)
            return
        logger.info("Moved %s aside to %s", target, aside)
    try:
        pending.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        pending.write_text(stamp + chr(10) + str(source) + chr(10), encoding="utf-8")
    except Exception as exc:
        report.errors.append(f"{pending}: {exc}")
        logger.warning("Could not write %s: %s", pending, exc)
        return
    # Copy into a staging folder first: a failure part-way leaves no half-filled
    # target behind, so the next start can try again from scratch.
    staging = target.with_name(target.name + ".migrating")
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_dir():
                (staging / relative).mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, staging / relative)
        if target.exists():
            shutil.rmtree(target)  # only ever an existing folder without files
        os.replace(staging, target)
        pending.unlink()
    except Exception as exc:
        report.errors.append(f"{source}: {exc}")
        logger.warning("Could not copy %s: %s", source, exc)
        shutil.rmtree(staging, ignore_errors=True)
        return
    report.copied.append(str(target))
    logger.info("Copied %s -> %s", source, target)


def _migrate_settings(settings_file: Path, report: MigrationReport) -> None:
    if not settings_file.is_file():
        return
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("not a JSON object")
    except Exception as exc:
        report.errors.append(f"{settings_file}: {exc}")
        logger.warning("Could not read %s: %s", settings_file, exc)
        return
    additions = {}
    for key, value in settings.items():
        if not isinstance(key, str):
            continue
        for old, new in SETTING_PREFIXES:
            if key.startswith(old):
                new_key = new + key[len(old) :]
                if new_key not in settings:
                    additions[new_key] = value
                break
    if not additions:
        return
    temp = settings_file.with_name(settings_file.name + ".migrating")
    try:
        merged = {**settings, **additions}
        temp.write_text(json.dumps(merged, indent=4), encoding="utf-8")
        os.replace(temp, settings_file)
    except Exception as exc:
        report.errors.append(f"{settings_file}: {exc}")
        logger.warning("Could not write %s: %s", settings_file, exc)
        try:
            temp.unlink()
        except OSError:
            pass
        return
    report.copied.append(f"{settings_file} ({len(additions)} settings)")
    logger.info("Added %d Warp Studio setting ids to %s", len(additions), settings_file)


def migrate(user_dir: Path, custom_node_dirs: list[Path], own_root: Path) -> MigrationReport | None:
    """Copy old data to the new names once. Returns None when already done."""
    user_dir = Path(user_dir)
    marker = user_dir / MARKER
    if marker.exists():
        return None
    report = MigrationReport()
    studio = user_dir / _STUDIO_DIR

    for old_name, new_name in _USER_FILES:
        _copy_one(user_dir / old_name, studio / new_name, report)

    old_updater = find_old_updater_data(list(custom_node_dirs), Path(own_root))
    if old_updater is not None:
        _copy_updater_data(old_updater, studio / "updater", report)

    try:
        users = sorted(
            child
            for child in user_dir.iterdir()
            if child.is_dir() and child.name != _STUDIO_DIR and not child.name.startswith("__")
        )
    except OSError as exc:
        users = []
        report.errors.append(f"{user_dir}: {exc}")
    for user in users:
        _copy_one(user / _OLD_CHATS, user / _NEW_CHATS, report)
        _migrate_settings(user / _SETTINGS, report)

    if not report.errors:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            temp = marker.with_name(marker.name + ".tmp")
            temp.write_text(
                json.dumps({"migrated_at": time.time(), "report": asdict(report)}, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, marker)
        except Exception as exc:
            report.errors.append(f"{marker}: {exc}")
            logger.warning("Could not write %s: %s", marker, exc)
    return report
