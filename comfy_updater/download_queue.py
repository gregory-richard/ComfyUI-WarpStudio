from __future__ import annotations

from dataclasses import dataclass, field
import os
import threading
import uuid

from .model_download import DownloadStopped

MODEL_DOWNLOAD_JOB = "model-download"
KEEP_FINISHED = 50
MB = 1024 * 1024

_ACTIVE_STATES = ("waiting", "downloading")
_ERROR_LIMIT = 300


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


@dataclass
class DownloadRequest:
    key: str
    source: str
    model_id: str
    version_id: str
    model_type: str
    name: str = ""
    reference_path: str = ""
    replace_path: str = ""
    check_item: dict = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    state: str = "waiting"
    written: int = 0
    total: int = 0
    file_name: str = ""
    error: str = ""
    # The type of the job this request waits for, while it waits; else "".
    waiting_for: str = ""
    warnings: list = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "source": self.source,
            "modelId": self.model_id,
            "versionId": self.version_id,
            "name": self.name,
            "replace": bool(self.replace_path),
            "state": self.state,
            "waitingFor": self.waiting_for,
            "written": self.written,
            "total": self.total,
            "fileName": self.file_name,
            "error": self.error,
            "warnings": list(self.warnings),
        }


class DownloadQueue:
    """In-memory download requests, drained by one job. Every change is under one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: list[DownloadRequest] = []
        # True from the submit that needs a job until take_next finds nothing
        # left (or the queue is stopped or abandoned), so a runner always exists
        # for a waiting request.
        self._busy = False
        self._listener_claimed = False

    def claim_listener(self) -> bool:
        """True for the first caller only: one finish listener serves this queue."""
        with self._lock:
            if self._listener_claimed:
                return False
            self._listener_claimed = True
            return True

    def wait_for(self, job_type: str) -> None:
        """Mark every waiting request as waiting for a job of this type."""
        with self._lock:
            for request in self._requests:
                if request.state == "waiting":
                    request.waiting_for = job_type

    def submit(self, request: DownloadRequest) -> tuple[DownloadRequest, bool]:
        with self._lock:
            for existing in self._requests:
                if existing.key == request.key and existing.state in _ACTIVE_STATES:
                    return existing, False
            request.state = "waiting"
            self._requests.append(request)
            needs_job = not self._busy
            self._busy = True
            return request, needs_job

    def take_next(self) -> DownloadRequest | None:
        with self._lock:
            for request in self._requests:
                if request.state == "waiting":
                    request.state = "downloading"
                    request.waiting_for = ""
                    return request
            self._busy = False
            return None

    def progress(self, request: DownloadRequest, written: int, total: int) -> None:
        with self._lock:
            request.written = max(0, int(written))
            request.total = max(0, int(total))

    def finish(self, request: DownloadRequest, state: str, error: str = "") -> None:
        with self._lock:
            request.state = state
            request.error = error
            self._prune_locked()

    def stop(self, request_id: str) -> DownloadRequest | None:
        with self._lock:
            for request in self._requests:
                if request.id != request_id:
                    continue
                if request.state == "waiting":
                    request.state = "stopped"
                    request.waiting_for = ""
                    if not any(r.state in _ACTIVE_STATES for r in self._requests):
                        # No runner exists for requests waiting on another job: none is left.
                        self._busy = False
                    self._prune_locked()
                elif request.state == "downloading":
                    request.stop_event.set()
                return request
            return None

    def stop_all(self) -> list[DownloadRequest]:
        """Stop every waiting request and the running one; the queue goes idle.

        Returns the waiting requests that were stopped.
        """
        with self._lock:
            stopped = []
            for request in self._requests:
                if request.state == "waiting":
                    request.state = "stopped"
                    request.waiting_for = ""
                    stopped.append(request)
                elif request.state == "downloading":
                    request.stop_event.set()
            self._busy = False
            self._prune_locked()
            return stopped

    def abandon(self, request: DownloadRequest, error: str) -> None:
        """Fail the request whose job never started, and every request waiting behind it.

        No runner exists when this is called, so a waiting request would never run.
        """
        with self._lock:
            for held in self._requests:
                if held is request or held.state == "waiting":
                    held.state = "error"
                    held.waiting_for = ""
                    held.error = error
            self._busy = False
            self._prune_locked()

    def abandon_waiting(self, error: str) -> None:
        """Fail every waiting request: no runner will come for them."""
        with self._lock:
            for held in self._requests:
                if held.state == "waiting":
                    held.state = "error"
                    held.waiting_for = ""
                    held.error = error
            self._busy = False
            self._prune_locked()

    def holds(self, path: str) -> bool:
        if not path:
            return False
        wanted = _norm(path)
        with self._lock:
            return any(
                request.state in _ACTIVE_STATES
                and request.replace_path
                and _norm(request.replace_path) == wanted
                for request in self._requests
            )

    def waiting(self) -> int:
        with self._lock:
            return sum(1 for request in self._requests if request.state == "waiting")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [request.as_dict() for request in self._requests]

    def forget_finished(self, key: str) -> None:
        """Drop the finished records of ``key``: the file they brought was deleted since.

        A card reads a finished record as "Downloaded"; the release is new again
        and must offer Download. A waiting or running request stays.
        """
        with self._lock:
            self._requests = [
                r for r in self._requests if r.key != key or r.state in _ACTIVE_STATES
            ]

    def _prune_locked(self) -> None:
        finished = [r for r in self._requests if r.state not in _ACTIVE_STATES]
        drop = len(finished) - KEEP_FINISHED
        if drop > 0:
            dropped = {id(r) for r in finished[:drop]}
            self._requests = [r for r in self._requests if id(r) not in dropped]


def run_queue(queue: DownloadQueue, work, *, progress, control) -> tuple[dict, list]:
    """The model-download job's runner: drain the queue one request at a time.

    ``work(request, report, should_stop)`` downloads one request and raises to
    fail. It never waits on a pause: a paused stream would time out.
    """
    counts = {"downloaded": 0, "failed": 0, "stopped": 0}
    try:
        while True:
            request = queue.take_next()
            if request is None:
                break

            def report(written: int, total: int, message: str, _request=request) -> None:
                queue.progress(_request, written, total)
                waiting = queue.waiting()
                suffix = f" · {waiting} waiting" if waiting else ""
                progress(written // MB, total // MB, f"{message}{suffix}")

            def should_stop(_request=request) -> bool:
                return control.is_cancelled() or _request.stop_event.is_set()

            try:
                work(request, report, should_stop)
            except DownloadStopped:
                queue.finish(request, "stopped")
                counts["stopped"] += 1
            except Exception as exc:  # noqa: BLE001
                queue.finish(request, "error", str(exc)[:_ERROR_LIMIT])
                counts["failed"] += 1
            else:
                queue.finish(request, "done")
                counts["downloaded"] += 1

            if control.is_cancelled():
                counts["stopped"] += len(queue.stop_all())
                break
    except BaseException:
        queue.stop_all()
        raise
    return counts, []
