"""The check's last phase: the AI names the releases the check found to be new.

A download later takes the name from here instead of asking, and the card shows
it as the label. Each model is named in one AI call and its names are stored at
once, so a stop keeps every model done so far. The phase never writes the base
code table: a base the AI gave a code to is learned for this phase only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .civitai_client import CivitaiRequestError
from .comfy_queue import NamingModelError
from .download_target import ai_stems
from .model_download import pick_file
from .renamer import RENAME_TYPES, _find_version

# A naming model's rejection lists every file ComfyUI has; the summary keeps the start.
_ERROR_LIMIT = 300
# These often read "model - version (base)" as creator "model" and title "version".
_THREE_PART_TYPES = ("checkpoint", "unet")


@dataclass
class NamingDeps:
    """The stores the naming phase works with."""

    decisions: object
    base_codes: object
    answer_cache: object
    journal: object
    release_names: object


@dataclass
class NamingTask:
    """One model's new releases to name, beside a file of the user's."""

    model_id: str
    model_type: str
    version_ids: list[str]
    reference: Path
    # The user's (creator, title) for the model, kept as they are.
    own: tuple[str, str] | None


def naming_tasks(
    cards: list[dict],
    *,
    named: Callable[[str], bool],
    archive_reference: Callable[[str, str], Optional[Path]],
) -> list[NamingTask]:
    """The models with new releases that have no name yet, from the cards of the Updates tab."""
    tasks: list[NamingTask] = []
    for card in cards:
        model_id = str(card.get("modelId") or "")
        model_type = str(card.get("modelType") or "")
        if not model_id or model_type not in RENAME_TYPES:
            continue
        new = [version for version in card.get("newVersions") or [] if isinstance(version, dict)]
        new = sorted(new, key=lambda version: str(version.get("versionDate") or ""), reverse=True)
        version_ids = [
            str(version.get("versionId") or "")
            for version in new
            if version.get("versionId") and not named(str(version["versionId"]))
        ]
        if not version_ids:
            continue
        reference = _reference(card.get("localVersions") or [], model_type, archive_reference)
        if reference is None:
            continue
        tasks.append(NamingTask(model_id, model_type, version_ids, reference, _own(card, model_type)))
    return tasks


def _reference(local_versions: list, model_type: str, archive_reference) -> Optional[Path]:
    held = [row for row in local_versions if isinstance(row, dict) and not row.get("deleted")]
    for row in held:
        if not row.get("archived") and row.get("modelPath"):
            return Path(row["modelPath"])
    for row in held:
        if row.get("modelPath"):
            return archive_reference(row["modelPath"], model_type)
    return None


def _own(card: dict, model_type: str) -> tuple[str, str] | None:
    own = card.get("ownName")
    if not isinstance(own, dict) or not own.get("creator") or not own.get("title"):
        return None
    if model_type in _THREE_PART_TYPES and not own.get("version"):
        return None
    return str(own["creator"]), str(own["title"])


class PhaseControl:
    """A job's control for the naming: it stops on a cancel and on a stop of the phase alone."""

    def __init__(self, control) -> None:
        self._control = control

    def is_cancelled(self) -> bool:
        return bool(self._control.is_cancelled() or self._control.phase_stopped())

    def wait_if_paused(self) -> None:
        self._control.wait_if_paused()


class PhaseCodes:
    """The base code table with the codes this phase learned laid over it; never writes it."""

    def __init__(self, base_codes) -> None:
        self._base_codes = base_codes
        self._learned: dict[str, str] = {}

    def code(self, base_model: str) -> Optional[str]:
        return self._learned.get(base_model) or self._base_codes.code(base_model)

    def all(self) -> dict[str, str]:
        return {**self._base_codes.all(), **self._learned}

    def learn(self, base_model: str, code: str) -> None:
        if base_model and code:
            self._learned.setdefault(base_model, code)


def run_naming(
    tasks: list[NamingTask],
    *,
    client,
    generator,
    decisions,
    base_codes,
    cache,
    clip_name: str,
    store,
    progress: Callable[[int, int, str], None],
    control,
) -> dict:
    """Name every task's releases, one AI call per model. Returns ``{"named", "total", "error"}``.

    ``total`` and ``named`` count releases. A model Civitai no longer has, or that fails, is
    skipped; a naming-model error ends the phase (silently when it was stopped).
    """
    codes = PhaseCodes(base_codes)
    total = sum(len(task.version_ids) for task in tasks)
    done = 0
    named = 0
    error = ""
    for task in tasks:
        if control.is_cancelled():
            break
        progress(done, total, f"Naming {done} of {total}")
        try:
            names = _name_task(
                task, client, generator, decisions, codes, cache, clip_name, control
            )
        except NamingModelError as exc:
            if not control.is_cancelled():
                error = str(exc)[:_ERROR_LIMIT]
            break
        except CivitaiRequestError:
            names = {}
        except Exception as exc:  # noqa: BLE001 - one model failing must not end the phase
            print(f"Civitai updater: could not name model {task.model_id} ({exc}).")
            names = {}
        if names:
            store.put(task.model_id, names)
            for release in names.values():
                if release.new_base:
                    codes.learn(release.new_base, release.parts.base)
            named += len(names)
        done += len(task.version_ids)
    progress(done, total, f"Naming {done} of {total}")
    return {"named": named, "total": total, "error": error}


def _name_task(task, client, generator, decisions, codes, cache, clip_name, control) -> dict:
    model = client.get_model(task.model_id, raise_on_error=True)
    if not isinstance(model, dict):
        return {}
    releases = []
    for version_id in task.version_ids:
        version = _find_version(model, version_id)
        file = pick_file(version) if version else None
        if version and file:
            releases.append((version, file))
    return ai_stems(
        task.model_id,
        model,
        releases,
        model_type=task.model_type,
        reference=task.reference,
        own=task.own,
        generator=generator,
        decisions=decisions,
        base_codes=codes,
        cache=cache,
        clip_name=clip_name,
        should_stop=control.is_cancelled,
    )
