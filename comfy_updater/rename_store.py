"""Persistence for the model renamer: remembered decisions, base codes, AI answers, rename journal,
last dry run, rename log."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Optional
import uuid

from .sidecar import quarantine_corrupt_file, write_json

DECISIONS_FILENAME = "rename_decisions.json"
BASE_CODES_FILENAME = "base_codes.json"
NAMING_CACHE_FILENAME = "naming_cache.json"
RENAME_JOURNAL_FILENAME = "rename_journal.json"
LAST_DRY_RUN_FILENAME = "last_dry_run.json"
RENAME_LOG_FILENAME = "rename_log.jsonl"

_TRAILING_TAG = re.compile(r"\(([^()]*)\)\s*$")


class _JsonStore:
    """Same load rules as ArchivedUpdateStore: corrupt files are moved aside, unreadable ones go read-only."""

    def __init__(self, path: Path, what: str) -> None:
        self.path = path
        self._what = what
        self._lock = threading.Lock()
        self._writable = True

    def _read(self) -> Optional[dict]:
        """The parsed file, or None when it is missing, corrupt (quarantined) or unreadable."""
        if not self.path.is_file():
            return None
        try:
            # Bytes let json detect UTF-8 with or without a BOM, UTF-16 and UTF-32.
            payload = json.loads(self.path.read_bytes())
        except OSError as exc:
            # Saving would replace the unread file with this session's empty data.
            self._writable = False
            print(
                f"Civitai updater: could not read {self.path} ({exc}); {self._what} are unavailable "
                "and changes to them will not be saved until ComfyUI is restarted."
            )
            return None
        except ValueError as exc:
            backup = quarantine_corrupt_file(self.path)
            print(
                f"Civitai updater: {self.path} is not valid JSON ({exc}). "
                f"It was moved to {backup}; starting empty."
            )
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, payload: dict) -> None:
        if self._writable:
            write_json(self.path, payload)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


class DecisionStore(_JsonStore):
    """Naming choices the user made or applied; they win over the AI."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir / DECISIONS_FILENAME, "remembered rename decisions")
        payload = _as_dict(self._read())
        self._models: dict[str, dict] = {}
        for model_id, record in _as_dict(payload.get("models")).items():
            cleaned = _model_record(record)
            if cleaned:
                self._models[str(model_id)] = cleaned
        self._versions: dict[str, dict] = {}
        for version_id, record in _as_dict(payload.get("versions")).items():
            version = _as_dict(record).get("version")
            if isinstance(version, str):  # "" is a valid decision: no version part
                self._versions[str(version_id)] = {"version": version}
        self._files: dict[str, dict] = {}
        for file_key, record in _as_dict(payload.get("files")).items():
            cleaned = _file_record(record)
            if cleaned:
                self._files[str(file_key)] = cleaned
        self._save()

    def _save(self) -> None:
        self._write(
            {
                "models": {key: record for key, record in sorted(self._models.items()) if record},
                "versions": dict(sorted(self._versions.items())),
                "files": {key: record for key, record in sorted(self._files.items()) if record},
            }
        )

    def model(self, model_id: str | int) -> dict:
        with self._lock:
            return dict(self._models.get(_clean(model_id), {}))

    def set_model(
        self,
        model_id: str | int,
        *,
        title: Optional[str] = None,
        creator: Optional[str] = None,
        locked: Optional[bool] = None,
    ) -> None:
        key = _clean(model_id)
        if not key:
            return
        with self._lock:
            record = dict(self._models.get(key, {}))
            if title is not None:
                record["title"] = title
            if creator is not None:
                record["creator"] = creator
            if locked is not None:
                record["locked"] = bool(locked)
            self._models[key] = record
            self._save()

    def version(self, version_id: str | int) -> Optional[str]:
        with self._lock:
            return self._versions.get(_clean(version_id), {}).get("version")

    def set_version(self, version_id: str | int, version: str) -> None:
        key = _clean(version_id)
        if not key:
            return
        with self._lock:
            self._versions[key] = {"version": version}
            self._save()

    def file(self, file_key: str) -> dict:
        with self._lock:
            return dict(self._files.get(_clean(file_key), {}))

    def set_file(self, file_key: str, *, version: Optional[str] = None, keep: Optional[bool] = None) -> None:
        key = _clean(file_key)
        if not key:
            return
        with self._lock:
            record = dict(self._files.get(key, {}))
            if version is not None:
                record["version"] = version
            if keep is not None:
                record["keep"] = bool(keep)
            self._files[key] = record
            self._save()

    def lock_applied(
        self,
        model_id: str | int,
        title: str,
        creator: str,
        versions: dict[str, str],
        files: dict[str, str],
    ) -> None:
        """Record the names a rename just applied so later runs and downloads reuse them."""
        key = _clean(model_id)
        if not key:
            return
        with self._lock:
            self._models[key] = {"title": title, "creator": creator, "locked": True}
            for version_id, version in versions.items():
                self._versions[_clean(version_id)] = {"version": version}
            for file_key, version in files.items():
                record = dict(self._files.get(_clean(file_key), {}))
                record["version"] = version
                self._files[_clean(file_key)] = record
            self._save()

    def ask_again(self, model_id: str | int, version_ids: list[str], file_keys: list[str]) -> None:
        """Forget a model's decisions and lock so the AI derives its names afresh."""
        with self._lock:
            self._models.pop(_clean(model_id), None)
            for version_id in version_ids:
                self._versions.pop(_clean(version_id), None)
            for file_key in file_keys:
                self._files.pop(_clean(file_key), None)
            self._save()


def _model_record(record: Any) -> dict:
    record = _as_dict(record)
    cleaned: dict = {}
    for field in ("title", "creator"):
        if isinstance(record.get(field), str):
            cleaned[field] = record[field]
    if isinstance(record.get("locked"), bool):
        cleaned["locked"] = record["locked"]
    return cleaned


def _file_record(record: Any) -> dict:
    record = _as_dict(record)
    cleaned: dict = {}
    if isinstance(record.get("version"), str):
        cleaned["version"] = record["version"]
    if isinstance(record.get("keep"), bool):
        cleaned["keep"] = record["keep"]
    return cleaned


class BaseCodeStore(_JsonStore):
    """One fixed short code per Civitai baseModel, e.g. "Krea 2" -> "krea2"."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir / BASE_CODES_FILENAME, "base-model codes")
        payload = _as_dict(self._read())
        self._codes: dict[str, str] = {
            str(base): code
            for base, code in _as_dict(payload.get("codes")).items()
            if isinstance(code, str) and code
        }
        self._seeded = payload.get("seeded") is True
        self._save()

    def _save(self) -> None:
        self._write({"codes": dict(sorted(self._codes.items())), "seeded": self._seeded})

    def code(self, base_model: str) -> Optional[str]:
        with self._lock:
            return self._codes.get(base_model)

    def set(self, base_model: str, code: str) -> None:
        with self._lock:
            self._codes[base_model] = code
            self._save()

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._codes)

    def seed(self, pairs: list[tuple[str, str]]) -> None:
        """Once only: take each base's most common trailing "(tag)" from the current file stems."""
        with self._lock:
            if self._seeded:
                return
            counts: dict[str, Counter] = {}
            for base_model, stem in pairs:
                match = _TRAILING_TAG.search(stem or "")
                tag = match.group(1).strip() if match else ""
                if base_model and tag:
                    counts.setdefault(base_model, Counter())[tag] += 1
            for base_model, counter in counts.items():
                # Ties go to the tag seen first.
                self._codes.setdefault(base_model, counter.most_common(1)[0][0])
            self._seeded = True
            self._save()


class AnswerCache(_JsonStore):
    """AI naming answers keyed by model id and a hash of the AI's inputs."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir / NAMING_CACHE_FILENAME, "cached naming answers")
        payload = _as_dict(self._read())
        self._answers: dict[str, dict] = {
            str(key): answer
            for key, answer in _as_dict(payload.get("answers")).items()
            if isinstance(answer, dict)
        }
        self._save()

    def _save(self) -> None:
        self._write({"answers": dict(sorted(self._answers.items()))})

    @staticmethod
    def key(model_id: str | int, payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"{_clean(model_id)}:{digest}"

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            answer = self._answers.get(key)
            return dict(answer) if answer is not None else None

    def put(self, key: str, answer: dict) -> None:
        with self._lock:
            self._answers[key] = dict(answer)
            self._save()

    def drop(self, key: str) -> None:
        with self._lock:
            if self._answers.pop(key, None) is not None:
                self._save()

    def drop_model(self, model_id: str | int) -> None:
        prefix = f"{_clean(model_id)}:"
        with self._lock:
            self._answers = {key: answer for key, answer in self._answers.items() if not key.startswith(prefix)}
            self._save()


def _journal_step(step: dict) -> dict:
    """The fields recovery reads. A deletion's ``delete`` (no kept copy to compare
    with) and a rename's ``suffix`` (its temporary name) go along when present."""
    saved = {"kind": step.get("kind"), "from": step.get("from"), "to": step.get("to"), "entry": step.get("entry")}
    for name in ("delete", "suffix"):
        if name in step:
            saved[name] = step[name]
    saved["done"] = False
    return saved


class RenameJournal(_JsonStore):
    """Every planned step of a rename run, written before the first one, for crash recovery.

    Written compact (thousands of steps), and synced to disk when a run begins so
    a power loss cannot leave an empty journal behind renamed files.
    """

    def _write(self, payload: dict, *, durable: bool = False) -> None:
        if self._writable:
            write_json(self.path, payload, compact=True, durable=durable)

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir / RENAME_JOURNAL_FILENAME, "the rename journal")
        payload = self._read()
        self._data: Optional[dict] = None
        if payload is not None and isinstance(payload.get("steps"), list):
            steps = [dict(step, done=step.get("done") is True) for step in payload["steps"] if isinstance(step, dict)]
            self._data = {
                "runId": str(payload.get("runId") or ""),
                "startedAt": str(payload.get("startedAt") or ""),
                "dryRunJobId": str(payload.get("dryRunJobId") or ""),
                "steps": steps,
            }

    def begin(self, steps: list[dict], dry_run_job_id: str = "") -> str:
        with self._lock:
            if not self._writable:
                # Renaming without a journal would leave no way to finish or undo a crashed run.
                raise OSError(f"{self.path} could not be read, so a rename run cannot be journaled")
            run_id = uuid.uuid4().hex
            self._data = {
                "runId": run_id,
                "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                # For the rename log, when "finish" completes the run after a restart.
                "dryRunJobId": str(dry_run_job_id or ""),
                "steps": [_journal_step(step) for step in steps],
            }
            self._write(self._data, durable=True)
            return run_id

    def mark_done(self, index: int) -> None:
        with self._lock:
            if self._data is None:
                raise IndexError("no rename run is journaled")
            self._data["steps"][index]["done"] = True
            # Synced: a journal cut short by a power loss is set aside as corrupt, stranding the run.
            self._write(self._data, durable=True)

    def mark_done_many(self, indices: list[int], *, applied: bool = False, applied_to: Iterable[int] = ()) -> None:
        """Mark several steps done with one write (a whole entry at once).

        ``applied``: the entry went through, so its files are under their new names
        (a removal's file is in the Recycle Bin). ``applied_to``: only these of the
        steps did. "Done" alone also covers an entry given up and put back, or a
        removal given up with its file still there; undo tells them apart.
        """
        went_through = set(applied_to)
        with self._lock:
            if self._data is None:
                raise IndexError("no rename run is journaled")
            for index in indices:
                self._data["steps"][index]["done"] = True
                if applied or index in went_through:
                    self._data["steps"][index]["applied"] = True
            if indices:
                self._write(self._data, durable=True)  # one sync per entry, as in mark_done

    def keep_only(self, indices: list[int], unapplied: Iterable[int] = ()) -> None:
        """Drop every step but these (left open), keeping the run id; one write.

        Steps in ``unapplied`` were put back under their old names: they lose ``applied``.
        """
        with self._lock:
            if self._data is None:
                raise IndexError("no rename run is journaled")
            steps = self._data["steps"]
            back = set(unapplied)
            kept = []
            for index in indices:
                step = dict(steps[index], done=False)
                if index in back:
                    step.pop("applied", None)
                kept.append(step)
            self._data["steps"] = kept
            self._write(self._data, durable=True)

    def pending(self) -> Optional[dict]:
        with self._lock:
            if self._data is None or all(step["done"] for step in self._data["steps"]):
                return None
            return json.loads(json.dumps(self._data))

    def clear(self) -> None:
        with self._lock:
            self._data = None
            if self._writable:
                self.path.unlink(missing_ok=True)


class RenameLog:
    """Every file the renamer moved, one JSON line per step, appended for good.

    The journal is cleared after each run; this keeps the record, so copies of
    the same files elsewhere (an archive disk) can be renamed to match. A line:
    ``{"time", "runId", "dryRunJobId", "kind": "rename"|"trash"|"undo", "from", "to"}``
    (a trash step's ``to`` is the kept copy's file). Writing it never fails a run.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / RENAME_LOG_FILENAME
        self._lock = threading.Lock()

    def append(self, run_id: str, dry_run_job_id: str, steps: list[dict], kind: Optional[str] = None) -> Optional[str]:
        """One line per step (``kind`` overrides the steps' own). Never raises: returns the warning
        for a failed write (printed by the caller, once per run), else None."""
        if not steps:
            return None
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = "".join(
            json.dumps(
                {
                    "time": now,
                    "runId": str(run_id or ""),
                    "dryRunJobId": str(dry_run_job_id or ""),
                    "kind": kind or step.get("kind"),
                    "from": step.get("from"),
                    "to": step.get("to"),
                },
                ensure_ascii=False,
            )
            + "\n"
            for step in steps
        )
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
        except Exception as exc:  # noqa: BLE001 - the files are moved; the log is a record only
            return f"Civitai updater: could not write the rename log {self.path} ({exc})."
        return None


class DryRunStore(_JsonStore):
    """The last completed dry run, so its list survives a ComfyUI restart.

    Kept current as decisions change its rows, and marked applied once an apply
    or a recovery moves files (it then stays read-only). Written compact: it
    holds every row of the library.

    ``_lock`` guards the state in memory and is held only briefly, so the event
    loop's reads (``job_id``, ``next_revision``) never wait on the disk.
    ``_write_lock`` serialises the writes; each writes the state as it is when
    its turn comes, so overlapping writes still leave the latest state on disk.
    Row lists handed to the store are owned by it and replaced, never mutated.
    """

    def _write(self, payload: dict, *, durable: bool = False) -> None:
        if self._writable:
            write_json(self.path, payload, compact=True, durable=durable)

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir / LAST_DRY_RUN_FILENAME, "the last dry run")
        self._write_lock = threading.Lock()
        payload = _as_dict(self._read())
        self._data: Optional[dict] = None
        job_id = payload.get("jobId")
        rows = payload.get("rows")
        if isinstance(job_id, str) and job_id and isinstance(rows, list):
            self._data = {
                "jobId": job_id,
                "startedAt": str(payload.get("startedAt") or ""),
                "finishedAt": str(payload.get("finishedAt") or ""),
                "summary": _as_dict(payload.get("summary")),
                "rows": [row for row in rows if isinstance(row, dict)],
                "applied": payload.get("applied") is True,
            }
        # Revisions order row writes that reach the thread pool out of order.
        self._issued = 0
        self._written = 0

    def _flush(self, *, durable: bool = False) -> None:
        with self._write_lock:
            with self._lock:
                if self._data is None:
                    return
                payload = dict(self._data)
            self._write(payload, durable=durable)

    def load(self) -> Optional[dict]:
        with self._lock:
            return json.loads(json.dumps(self._data)) if self._data is not None else None

    def job_id(self) -> Optional[str]:
        with self._lock:
            return self._data["jobId"] if self._data is not None else None

    def next_revision(self) -> int:
        with self._lock:
            self._issued += 1
            return self._issued

    def save(self, snapshot: dict) -> None:
        """Replace the saved dry run with this finished job (a ``JobRecord.as_dict()`` snapshot)."""
        with self._lock:
            self._issued += 1
            self._written = self._issued
            self._data = {
                "jobId": str(snapshot.get("jobId") or ""),
                "startedAt": str(snapshot.get("startedAt") or ""),
                "finishedAt": str(snapshot.get("finishedAt") or ""),
                "summary": _as_dict(snapshot.get("summary")),
                "rows": list(snapshot.get("items") or []),
                "applied": False,
            }
        self._flush()

    def update_rows(self, job_id: str, rows: list[dict], revision: int) -> None:
        """Save edited rows of the saved dry run; older revisions and other dry runs are ignored."""
        with self._lock:
            if self._data is None or self._data["jobId"] != job_id or revision <= self._written:
                return
            self._written = revision
            self._data["rows"] = rows
        self._flush()

    def mark_applied(self) -> Optional[str]:
        """Files moved: the saved rows may now name other files, so they stay read-only.

        Synced to disk. Returns None, or a warning when the flag could not be
        saved: the saved run is then deleted, since a missing run is safe and a
        stale one that could be applied again after a restart is not.
        """
        with self._lock:
            if self._data is None or self._data["applied"]:
                return None
            self._data["applied"] = True
        try:
            self._flush(durable=True)
            return None
        except OSError as exc:
            return self._drop_after_failed_write(exc)

    def _drop_after_failed_write(self, exc: OSError) -> str:
        with self._write_lock:
            with self._lock:
                self._data = None
            try:
                self.path.unlink(missing_ok=True)
                warning = (
                    f"Could not save that this dry run was applied ({exc}), so its saved copy was deleted: "
                    "after a restart it is gone instead of coming back as if it had not been applied."
                )
            except OSError as unlink_exc:
                warning = (
                    f"Could not save that this dry run was applied ({exc}) and could not delete {self.path} "
                    f"({unlink_exc}). Delete that file by hand before restarting ComfyUI, or the dry run comes "
                    "back after the restart as if it had not been applied."
                )
        print(f"Civitai updater: {warning}")
        return warning
