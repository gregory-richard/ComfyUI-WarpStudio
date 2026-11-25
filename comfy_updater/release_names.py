"""The names the AI gave to the releases of a model, kept per version.

The check names every release of a model once; a download later uses the name
it finds here, and the panel shows the label. A base code learned after the name
was made wins over the code stored with it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .download_target import AiName, NamedRelease
from .naming_scheme import NameParts, assemble
from .rename_store import _as_dict, _clean, _JsonStore

RELEASE_NAMES_FILENAME = "release_names.json"
_FIELDS = ("creator", "title", "version", "precision", "base")


class ReleaseNameStore(_JsonStore):
    def __init__(self, data_dir: Path, base_codes) -> None:
        super().__init__(data_dir / RELEASE_NAMES_FILENAME, "release names")
        self._base_codes = base_codes
        self._names: dict[str, dict] = {}
        for version_id, record in _as_dict(_as_dict(self._read()).get("names")).items():
            cleaned = _record(record)
            if cleaned:
                self._names[_clean(version_id)] = cleaned

    def _save(self) -> None:
        self._write({"names": dict(sorted(self._names.items()))})

    def has(self, version_id: str | int) -> bool:
        with self._lock:
            return _clean(version_id) in self._names

    def put(self, model_id: str | int, names: dict[str, NamedRelease]) -> None:
        """Keep these releases' names (one write); a release named before is renamed."""
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            for version_id, named in names.items():
                self._names[_clean(version_id)] = {
                    "modelId": _clean(model_id),
                    "creator": named.parts.creator,
                    "title": named.parts.title,
                    "version": named.parts.version,
                    "precision": named.parts.precision,
                    "base": named.parts.base,
                    "baseModel": named.base_model,
                    "namedAt": stamp,
                }
            self._save()

    def get(self, version_id: str | int) -> Optional[AiName]:
        with self._lock:
            record = self._names.get(_clean(version_id))
        if record is None:
            return None
        parts, new_base = self._resolve(record)
        return AiName(assemble(parts), parts.base, new_base)

    def labels(self) -> dict[str, dict]:
        """{versionId: {"stem", "label"}}: the whole name, and its version, precision and base."""
        with self._lock:
            records = dict(self._names)
        labels: dict[str, dict] = {}
        for version_id, record in records.items():
            parts, _new_base = self._resolve(record)
            label = " ".join(part for part in (parts.version, parts.precision) if part)
            if parts.base:
                label += f" ({parts.base})"
            labels[version_id] = {"stem": assemble(parts), "label": label.strip()}
        return labels

    def _resolve(self, record: dict) -> tuple[NameParts, str]:
        """The parts with the base table's code when it has one now, and the new base (else "")."""
        parts = NameParts(**{field: record[field] for field in _FIELDS})
        base_model = record["baseModel"]
        code = self._base_codes.code(base_model) if base_model else None
        if code:
            parts.base = code
            return parts, ""
        return parts, base_model


def _record(record) -> dict:
    record = _as_dict(record)
    if not all(isinstance(record.get(field), str) for field in _FIELDS):
        return {}
    cleaned = {field: record[field] for field in _FIELDS}
    for field in ("modelId", "baseModel", "namedAt"):
        value = record.get(field)
        cleaned[field] = value if isinstance(value, str) else ""
    return cleaned
