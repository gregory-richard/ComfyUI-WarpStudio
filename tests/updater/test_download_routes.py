from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from comfy_updater import model_files
from comfy_updater.config_store import ConfigStore
from comfy_updater.download_queue import (
    MODEL_DOWNLOAD_JOB,
    DownloadQueue,
    DownloadRequest,
    run_queue,
)
from comfy_updater.jobs import JobManager, JobRecord
from comfy_updater.model_actions import EARLY_ACCESS_ERROR
from comfy_updater.naming_ai import SYSTEM_PROMPT
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, RenameJournal
from comfy_updater.routes import (
    _PAUSE_ERRORS,
    DOWNLOAD_BUSY_ERROR,
    DOWNLOAD_PAUSE_ERROR,
    JOURNAL_PENDING_ERROR,
    MODEL_DOWNLOAD_PAUSE_ERROR,
    NAMING_MODEL_DOWNLOAD_JOB,
    REPLACE_HELD_ERROR,
    _normalize_download_payload,
    _register_model_action_routes,
    _unpausable,
    download_context,
    delete_problem,
    download_problem,
    gallery_request,
    submit_download,
    updates_request,
)


def _join_job_threads(timeout: float = 5.0) -> None:
    """Wait for every job thread a JobManager started (they are named after the job)."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name.startswith("civitai-updater-"):
            thread.join(timeout)
            if thread.is_alive():
                raise AssertionError(f"{thread.name} did not finish")


def _blocking_runner(event: threading.Event):
    def runner(progress, emit, control):
        event.wait(5)
        return {}, []

    return runner


class _CountingJobManager(JobManager):
    """A real JobManager that remembers every start it was asked for, and the ones it granted."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list = []
        self.started: list = []

    def start(self, job_type, runner, seed_items=None, on_complete=None):
        self.asked.append(job_type)
        job = super().start(job_type, runner, seed_items=seed_items, on_complete=on_complete)
        if job is not None:
            self.started.append(job.id)
        return job


def _request(version_id: str) -> DownloadRequest:
    return DownloadRequest(
        key=f"version:{version_id}",
        source="updates",
        model_id="101",
        version_id=version_id,
        model_type="lora",
    )


def _remote(version_id: str, paid: str = "", paid_until: str = "") -> dict:
    return {
        "versionId": version_id,
        "versionName": f"v{version_id[-1]}",
        "versionDate": f"2026-0{version_id[-1]}-01T00:00:00.000Z",
        "paid": paid,
        "paidUntil": paid_until,
    }


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


class _Dirs(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.folder = self.root / "loras"
        self.folder.mkdir()
        self.v1 = str(self.folder / "glow.safetensors")

    def item(self, **fields) -> dict:
        item = {
            "modelPath": self.v1,
            "filePath": self.v1,
            "modelType": "lora",
            "metadataOnly": False,
            "modelId": "101",
            "localVersionId": "201",
            "status": "ok",
            "remoteVersions": [_remote("203"), _remote("202"), _remote("201")],
        }
        item.update(fields)
        return item

    def check_job(self, items, job_id: str = "check-1") -> JobRecord:
        return JobRecord(id=job_id, type="check-updates", status="completed", items=items)

    def payload(self, **fields) -> dict:
        payload = {
            "source": "updates",
            "jobId": "check-1",
            "modelId": "101",
            "versionId": "202",
            "modelPath": self.v1,
            "name": "",
            "replace": False,
        }
        payload.update(fields)
        return _normalize_download_payload(payload)


class DownloadHelperTests(_Dirs):
    def tearDown(self) -> None:
        _join_job_threads()

    def test_a_download_waits_for_another_kind_of_job(self) -> None:
        self.assertEqual("Another job is running. Try again when it's done.", DOWNLOAD_BUSY_ERROR)
        manager = JobManager()
        release = threading.Event()
        try:
            manager.start("check-updates", _blocking_runner(release))
            active = manager.get_active()
            self.assertEqual(
                (409, DOWNLOAD_BUSY_ERROR), download_problem(active, journal_pending=False)
            )
            # A rename apply holds the journal while it runs: the job is the reason.
            self.assertEqual(
                (409, DOWNLOAD_BUSY_ERROR), download_problem(active, journal_pending=True)
            )
        finally:
            release.set()
            _join_job_threads()
        self.assertEqual((409, JOURNAL_PENDING_ERROR), download_problem(None, journal_pending=True))
        self.assertIsNone(download_problem(None, journal_pending=False))

        release = threading.Event()
        try:
            manager.start(MODEL_DOWNLOAD_JOB, _blocking_runner(release))
            self.assertIsNone(download_problem(manager.get_active(), journal_pending=False))
        finally:
            release.set()

    def test_an_updates_request_is_checked_against_the_check_results(self) -> None:
        queue = DownloadQueue()
        held = DownloadRequest(
            key="version:203",
            source="updates",
            model_id="101",
            version_id="203",
            model_type="lora",
            replace_path=self.v1,
        )
        cases = [
            ("no job", None, self.payload(), True, (404, "job not found")),
            (
                "not a check",
                JobRecord(id="check-1", type="rename-dry-run", items=[self.item()]),
                self.payload(),
                True,
                (404, "job not found"),
            ),
            (
                "another file",
                self.check_job([self.item()]),
                self.payload(modelPath=str(self.folder / "glowV2.safetensors")),
                True,
                (400, "That file is not in these results."),
            ),
            (
                "another model",
                self.check_job([self.item()]),
                self.payload(modelId="102"),
                True,
                (400, "That file is not in these results."),
            ),
            (
                "no path",
                self.check_job([self.item()]),
                self.payload(modelPath=""),
                True,
                (400, "That file is not in these results."),
            ),
            (
                "another release",
                self.check_job([self.item()]),
                self.payload(versionId="999"),
                True,
                (400, "That release is not one of this model's."),
            ),
            (
                "paid for good",
                self.check_job([self.item(remoteVersions=[_remote("202", "permanent")])]),
                self.payload(),
                True,
                (409, EARLY_ACCESS_ERROR),
            ),
            (
                "early access",
                self.check_job(
                    [self.item(remoteVersions=[_remote("202", "early", _iso(timedelta(days=1)))])]
                ),
                self.payload(),
                True,
                (409, EARLY_ACCESS_ERROR),
            ),
            (
                "replace of metadata only",
                self.check_job([self.item(metadataOnly=True)]),
                self.payload(replace=True),
                True,
                (400, "Only a version on this drive can be replaced."),
            ),
            (
                "replace without the recycle bin",
                self.check_job([self.item()]),
                self.payload(replace=True),
                False,
                (409, "Replace needs the send2trash package."),
            ),
        ]
        for name, job, payload, can_trash, expected in cases:
            with self.subTest(name):
                self.assertEqual(
                    expected, updates_request(payload, job, can_trash=can_trash, queue=queue)
                )

        queue.submit(held)
        with self.subTest("held by another replace"):
            self.assertEqual(
                (409, REPLACE_HELD_ERROR),
                updates_request(
                    self.payload(replace=True),
                    self.check_job([self.item()]),
                    can_trash=True,
                    queue=queue,
                ),
            )

        item = self.item()
        job = self.check_job([self.item(modelPath=str(self.folder / "other.safetensors")), item])
        # The path as the panel sent it may be spelled differently; the item's own path is used.
        sent = os.path.join(str(self.folder), ".", "glow.safetensors")
        made = updates_request(
            self.payload(modelPath=sent), job, can_trash=False, queue=DownloadQueue()
        )
        self.assertIsInstance(made, DownloadRequest)
        self.assertEqual("version:202", made.key)
        self.assertEqual("updates", made.source)
        self.assertEqual("101", made.model_id)
        self.assertEqual("202", made.version_id)
        self.assertEqual("lora", made.model_type)
        self.assertEqual("", made.name)
        self.assertEqual(self.v1, made.reference_path)
        self.assertEqual("", made.replace_path)
        self.assertEqual(item, made.check_item)
        self.assertIsNot(item, made.check_item)
        self.assertIsNot(item["remoteVersions"], made.check_item["remoteVersions"])
        self.assertEqual("waiting", made.state)

        replace = updates_request(
            self.payload(replace=True), job, can_trash=True, queue=DownloadQueue()
        )
        self.assertEqual(self.v1, replace.replace_path)
        self.assertEqual(self.v1, replace.reference_path)

        metadata_only = self.item(
            metadataOnly=True, modelPath=str(self.folder / "glow.civitai.info")
        )
        made = updates_request(
            self.payload(modelPath=metadata_only["modelPath"]),
            self.check_job([metadata_only]),
            can_trash=True,
            queue=DownloadQueue(),
        )
        self.assertIsInstance(made, DownloadRequest)
        self.assertEqual("", made.replace_path)

    def test_an_early_access_window_that_ended_allows_the_download(self) -> None:
        ended = self.item(remoteVersions=[_remote("202", "early", _iso(timedelta(days=-1)))])
        made = updates_request(
            self.payload(), self.check_job([ended]), can_trash=True, queue=DownloadQueue()
        )
        self.assertIsInstance(made, DownloadRequest)
        self.assertEqual("version:202", made.key)

    def test_a_second_replace_of_the_same_file_is_refused(self) -> None:
        queue = DownloadQueue()
        job = self.check_job([self.item()])
        first = updates_request(
            self.payload(versionId="202", replace=True), job, can_trash=True, queue=queue
        )
        self.assertIsInstance(first, DownloadRequest)
        _held, needs_job = queue.submit(first)
        self.assertTrue(needs_job)

        self.assertEqual(
            (409, REPLACE_HELD_ERROR),
            updates_request(
                self.payload(versionId="203", replace=True), job, can_trash=True, queue=queue
            ),
        )
        self.assertEqual("That version is already being replaced.", REPLACE_HELD_ERROR)
        download = updates_request(self.payload(versionId="203"), job, can_trash=True, queue=queue)
        self.assertIsInstance(download, DownloadRequest)
        self.assertEqual("", download.replace_path)

        # Once the first replace is over, the file can be replaced again.
        queue.finish(queue.take_next(), "error", "failed")
        again = updates_request(
            self.payload(versionId="203", replace=True), job, can_trash=True, queue=queue
        )
        self.assertIsInstance(again, DownloadRequest)

    def test_a_gallery_request_names_a_missing_lora(self) -> None:
        other_root = self.root / "more-loras"
        other_root.mkdir()
        sub = self.folder / "studio-k"
        sub.mkdir()
        info_path = sub / "glow.civitai.info"
        info_path.write_text(
            json.dumps({"id": 202, "modelId": 101, "name": "v2"}), encoding="utf-8"
        )
        roots = [other_root, self.folder]
        name = os.path.join("studio-k", "glow.safetensors")

        self.assertEqual(
            (400, "The name of a LoRA is required."),
            gallery_request(
                _normalize_download_payload({"source": "gallery", "name": "  "}), roots
            ),
        )
        made = gallery_request(
            _normalize_download_payload({"source": "gallery", "name": f" {name} "}), roots
        )
        self.assertIsInstance(made, DownloadRequest)
        self.assertEqual(f"gallery:{name}", made.key)
        self.assertEqual("gallery", made.source)
        self.assertEqual("101", made.model_id)
        self.assertEqual("202", made.version_id)
        self.assertEqual("lora", made.model_type)
        self.assertEqual(name, made.name)
        self.assertEqual(str(info_path), made.reference_path)
        self.assertEqual("", made.replace_path)

        gone = (404, "Its info file is gone, or its weights are already there.")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "glow.civitai.info").write_text(
            json.dumps({"id": 202, "modelId": 101}), encoding="utf-8"
        )
        escaping = os.path.join("..", "elsewhere", "glow.safetensors")
        self.assertEqual(
            gone, gallery_request(_normalize_download_payload({"name": escaping}), roots)
        )
        (sub / "glow.safetensors").write_bytes(b"weights")
        self.assertEqual(gone, gallery_request(_normalize_download_payload({"name": name}), roots))

    def test_the_first_click_starts_the_job_and_the_next_ones_queue(self) -> None:
        queue = DownloadQueue()
        manager = _CountingJobManager()
        release = threading.Event()
        ran: list = []

        def work(request, report, should_stop):
            release.wait(5)
            ran.append(request.key)

        def runner(progress, emit, control):
            return run_queue(queue, work, progress=progress, control=control)

        try:
            first, first_id = asyncio.run(submit_download(queue, manager, _request("202"), runner))
            second, second_id = asyncio.run(
                submit_download(queue, manager, _request("203"), runner)
            )
            again, again_id = asyncio.run(submit_download(queue, manager, _request("203"), runner))
        finally:
            release.set()
            _join_job_threads()
        self.assertIsNotNone(first_id)
        self.assertEqual(first_id, second_id)
        self.assertEqual(first_id, again_id)
        self.assertIs(second, again)
        self.assertEqual([MODEL_DOWNLOAD_JOB], manager.asked)
        self.assertEqual([first_id], manager.started)
        self.assertEqual(["version:202", "version:203"], ran)
        self.assertEqual(("done", "done"), (first.state, second.state))
        self.assertEqual(
            {"downloaded": 2, "failed": 0, "stopped": 0}, manager.get(first_id).summary
        )

    def test_a_click_as_the_last_download_finishes_starts_a_new_job(self) -> None:
        queue = DownloadQueue()
        manager = _CountingJobManager()
        drained = threading.Event()
        closing = threading.Event()
        ran: list = []

        def work(request, report, should_stop):
            ran.append(request.key)

        def runner(progress, emit, control):
            result = run_queue(queue, work, progress=progress, control=control)
            drained.set()
            # The job's closing tail: the queue is idle, JobManager still says running.
            closing.wait(5)
            return result

        timer = threading.Timer(0.1, closing.set)
        try:
            _first, first_id = asyncio.run(submit_download(queue, manager, _request("202"), runner))
            self.assertTrue(drained.wait(5))
            self.assertEqual("running", manager.get(first_id).status)
            timer.start()
            second, second_id = asyncio.run(
                submit_download(queue, manager, _request("203"), runner)
            )
        finally:
            closing.set()
            timer.cancel()
            timer.join(5)
            _join_job_threads()
        self.assertIsNotNone(second_id)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual([first_id, second_id], manager.started)
        self.assertGreater(len(manager.asked), 2)  # refused while the first job was closing
        self.assertEqual(["version:202", "version:203"], ran)
        self.assertEqual("done", second.state)
        self.assertEqual("completed", manager.get(second_id).status)

    def test_a_click_while_another_kind_of_job_runs_is_abandoned(self) -> None:
        queue = DownloadQueue()
        manager = _CountingJobManager()
        release = threading.Event()
        try:
            manager.start("check-updates", _blocking_runner(release))
            started = time.monotonic()
            request, job_id = asyncio.run(
                submit_download(
                    queue, manager, _request("202"), _blocking_runner(release), delay=1.0
                )
            )
            elapsed = time.monotonic() - started
        finally:
            release.set()
            _join_job_threads()
        self.assertIsNone(job_id)
        self.assertEqual(("error", DOWNLOAD_BUSY_ERROR), (request.state, request.error))
        self.assertLess(elapsed, 0.5)  # no waiting: only a closing download job is waited for
        self.assertEqual(["check-updates", MODEL_DOWNLOAD_JOB], manager.asked)
        _next, needs_job = queue.submit(_request("203"))
        self.assertTrue(needs_job)

    def test_a_request_waiting_behind_an_abandoned_retry_is_failed_too(self) -> None:
        queue = DownloadQueue()

        class _Manager:
            def __init__(self):
                self.starts = 0
                self.active = JobRecord(id="closing", type=MODEL_DOWNLOAD_JOB)

            def start(self, job_type, runner, **kwargs):
                self.starts += 1
                return None

            def get_active(self):
                return self.active

        manager = _Manager()
        outcome: list = []

        def first_click():
            outcome.append(
                asyncio.run(
                    submit_download(queue, manager, _request("202"), None, attempts=500, delay=0.01)
                )
            )

        thread = threading.Thread(target=first_click)
        thread.start()
        try:
            end = time.monotonic() + 5
            while manager.starts < 2 and time.monotonic() < end:
                time.sleep(0.005)
            self.assertGreaterEqual(manager.starts, 2)  # the first click is retrying
            second, _second_id = asyncio.run(submit_download(queue, manager, _request("203"), None))
            manager.active = JobRecord(id="other", type="check-updates")
        finally:
            thread.join(5)
        self.assertFalse(thread.is_alive())
        first, first_id = outcome[0]
        self.assertIsNone(first_id)
        self.assertEqual(("error", DOWNLOAD_BUSY_ERROR), (first.state, first.error))
        self.assertEqual(("error", DOWNLOAD_BUSY_ERROR), (second.state, second.error))
        _next, needs_job = queue.submit(_request("204"))
        self.assertTrue(needs_job)

    def test_downloads_cannot_be_paused(self) -> None:
        self.assertTrue(_unpausable(JobRecord(id="a", type=MODEL_DOWNLOAD_JOB)))
        self.assertTrue(_unpausable(JobRecord(id="b", type=NAMING_MODEL_DOWNLOAD_JOB)))
        self.assertFalse(_unpausable(JobRecord(id="c", type="scan")))
        self.assertFalse(_unpausable(None))
        self.assertEqual(
            "Downloads cannot be paused. Stop cancels them.", MODEL_DOWNLOAD_PAUSE_ERROR
        )
        self.assertEqual(MODEL_DOWNLOAD_PAUSE_ERROR, _PAUSE_ERRORS[MODEL_DOWNLOAD_JOB])
        self.assertEqual(DOWNLOAD_PAUSE_ERROR, _PAUSE_ERRORS[NAMING_MODEL_DOWNLOAD_JOB])

    def test_normalize_download_payload(self) -> None:
        self.assertEqual(
            {
                "source": "updates",
                "jobId": "check-1",
                "modelId": "101",
                "versionId": "202",
                "modelPath": "C:/loras/glow.safetensors",
                "name": "glow.safetensors",
                "replace": False,
            },
            _normalize_download_payload(
                {
                    "source": " updates ",
                    "jobId": " check-1 ",
                    "modelId": " 101",
                    "versionId": "202 ",
                    "modelPath": "  C:/loras/glow.safetensors ",
                    "name": "\tglow.safetensors\n",
                    "replace": "yes",
                    "extra": "dropped",
                }
            ),
        )
        self.assertTrue(_normalize_download_payload({"replace": True})["replace"])
        self.assertFalse(_normalize_download_payload({"replace": 1})["replace"])
        normalized = _normalize_download_payload(
            {"source": "elsewhere", "modelId": 101, "jobId": None}
        )
        self.assertEqual(
            ("", "", ""), (normalized["source"], normalized["modelId"], normalized["jobId"])
        )
        self.assertEqual("gallery", _normalize_download_payload({"source": "gallery"})["source"])
        self.assertEqual("", _normalize_download_payload({})["name"])

    def test_the_download_context_builds_fresh_clients_and_the_naming_model(self) -> None:
        data_dir = self.root / "data"
        data_dir.mkdir()
        config_store = ConfigStore(data_dir)
        config_store.update(
            {
                "apiKey": "test-key",
                "requestTimeoutSeconds": 12,
                "maxRetries": 2,
                "namingModel": "llm\\q.safetensors",
            }
        )
        journal = RenameJournal(data_dir)
        stores = (DecisionStore(data_dir), BaseCodeStore(data_dir), AnswerCache(data_dir))
        stop = lambda: False  # noqa: E731

        with (
            mock.patch("comfy_updater.routes.has_text_generation", return_value=False),
            mock.patch(
                "comfy_updater.routes.list_naming_models", return_value=["llm/Q.safetensors"]
            ),
        ):
            ctx = download_context(config_store, "jobs", *stores, journal)
        self.assertIsNone(ctx.make_generator)
        self.assertEqual(data_dir, ctx.data_dir)
        self.assertEqual("jobs", ctx.job_manager)
        self.assertIs(stores[0], ctx.decisions)
        self.assertIs(stores[1], ctx.base_codes)
        self.assertIs(stores[2], ctx.answer_cache)
        self.assertIs(model_files.lora_roots, ctx.lora_roots)
        self.assertEqual("test-key", ctx.config["apiKey"])
        client = ctx.make_client(stop)
        self.assertIsNot(client, ctx.make_client(stop))  # no release data kept between downloads
        self.assertEqual((12, 2), (client.timeout_seconds, client.max_retries))
        self.assertIs(stop, client.should_stop)

        lora_folders = {"lora": [self.folder]}
        with (
            mock.patch("comfy_updater.routes.has_text_generation", return_value=True),
            mock.patch(
                "comfy_updater.routes.list_naming_models", return_value=["llm/Q.safetensors"]
            ),
            mock.patch(
                "comfy_updater.routes.resolve_model_roots", return_value=lora_folders
            ) as roots,
        ):
            ctx = download_context(config_store, "jobs", *stores, journal)
            self.assertEqual([self.folder], ctx.roots_for("lora"))
            roots.assert_called_once_with(ctx.config, ["lora"], include_custom_paths=True)
            roots.return_value = {}
            self.assertEqual([], ctx.roots_for("checkpoint"))
        self.assertEqual("llm/Q.safetensors", ctx.clip_name)
        generator = ctx.make_generator(stop)
        self.assertEqual("llm/Q.safetensors", generator.clip_name)
        self.assertEqual(SYSTEM_PROMPT, generator.system_prompt)
        self.assertIs(stop, generator.should_stop)


# --- the routes ------------------------------------------------------------------


def _strings(value) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in _strings(key) + _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


class _Routes:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def _add(self, method: str, path: str):
        def register(handler):
            self.handlers[(method, path)] = handler
            return handler

        return register

    def get(self, path: str):
        return self._add("GET", path)

    def post(self, path: str):
        return self._add("POST", path)


class _Request:
    def __init__(self, method: str, payload=None, match_info=None, guarded: bool = True) -> None:
        self.method = method
        self.headers = {"Content-Type": "application/json"}
        if guarded:
            self.headers["X-Civitai-Updater"] = "1"
        self.host = "127.0.0.1:8188"
        self.match_info = match_info or {}
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload


class DownloadRouteTests(_Dirs):
    def setUp(self) -> None:
        super().setUp()
        data_dir = self.root / "data"
        data_dir.mkdir()
        self.config_store = ConfigStore(data_dir)
        self.jobs = JobManager()
        self.journal = RenameJournal(data_dir)
        self.routes = _Routes()
        self.queue = _register_model_action_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(data_dir),
            BaseCodeStore(data_dir),
            AnswerCache(data_dir),
            self.journal,
        )
        self.jobs.restore_job(
            "check-updates", "check-1", startedAt="", finishedAt="", summary={}, items=[self.item()]
        )
        self.release = threading.Event()
        self.contexts: list = []

        def fake_download(request, *, report, should_stop, ctx):
            self.contexts.append(ctx)
            self.release.wait(5)

        patches = [
            mock.patch("comfy_updater.routes.download_one", fake_download),
            mock.patch("comfy_updater.routes.send2trash_available", return_value=True),
            mock.patch("comfy_updater.routes.list_naming_models", return_value=[]),
            mock.patch("comfy_updater.routes.has_text_generation", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # Cleanups run last first: the jobs end before the fakes are taken away.
        self.addCleanup(_join_job_threads)
        self.addCleanup(self.release.set)

    def call(self, method: str, path: str, payload=None, match_info=None, guarded: bool = True):
        handler = self.routes.handlers[(method, path)]
        response = asyncio.run(handler(_Request(method, payload, match_info, guarded)))
        body = json.loads(response.text)
        text = "\n".join(_strings(body))
        # No path in any answer, however it is spelled.
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(self.root.name, text)
        return response.status, body

    def post_download(self, **fields):
        payload = {
            "source": "updates",
            "jobId": "check-1",
            "modelId": "101",
            "versionId": "202",
            "modelPath": self.v1,
        }
        payload.update(fields)
        return self.call("POST", "/warp-studio/updater/downloads", payload)

    def stop(self, request_id: str):
        return self.call(
            "POST",
            "/warp-studio/updater/downloads/{request_id}/stop",
            match_info={"request_id": request_id},
        )

    def test_every_route_is_guarded(self) -> None:
        self.assertEqual(
            {
                ("GET", "/warp-studio/updater/downloads"),
                ("POST", "/warp-studio/updater/downloads"),
                ("POST", "/warp-studio/updater/downloads/{request_id}/stop"),
                ("POST", "/warp-studio/updater/delete-model"),
            },
            set(self.routes.handlers),
        )
        for (method, path), _handler in self.routes.handlers.items():
            with self.subTest(path=path, method=method):
                status, body = self.call(method, path, {}, {"request_id": "x"}, guarded=False)
                self.assertEqual(403, status)
                self.assertIn("X-Civitai-Updater", body["error"])

    def test_the_routes_queue_follow_and_stop_downloads(self) -> None:
        self.assertEqual(
            (200, {"jobId": None, "requests": [], "canTrash": True}),
            self.call("GET", "/warp-studio/updater/downloads"),
        )
        status, body = self.post_download(replace=True)
        self.assertEqual(200, status, body)
        job_id = body["jobId"]
        self.assertEqual(MODEL_DOWNLOAD_JOB, self.jobs.get(job_id).type)
        first = body["request"]
        self.assertEqual(
            ("version:202", "updates", "101", "202"),
            (first["key"], first["source"], first["modelId"], first["versionId"]),
        )
        self.assertTrue(first["replace"])

        status, body = self.post_download(versionId="203")
        self.assertEqual(200, status, body)
        self.assertEqual(job_id, body["jobId"])
        second = body["request"]
        self.assertEqual("waiting", second["state"])

        status, body = self.call("GET", "/warp-studio/updater/downloads")
        self.assertEqual(200, status)
        self.assertEqual(job_id, body["jobId"])
        self.assertEqual(
            ["version:202", "version:203"], [record["key"] for record in body["requests"]]
        )

        status, body = self.stop(second["id"])
        self.assertEqual(200, status)
        self.assertEqual("stopped", body["request"]["state"])
        self.assertEqual((404, {"error": "download not found"}), self.stop("nope"))

        self.release.set()
        _join_job_threads()
        status, body = self.call("GET", "/warp-studio/updater/downloads")
        self.assertIsNone(body["jobId"])
        self.assertEqual(["done", "stopped"], [record["state"] for record in body["requests"]])
        self.assertEqual(1, len(self.contexts))  # the stopped one never ran
        self.assertEqual(self.config_store.data_dir, self.contexts[0].data_dir)
        self.assertEqual(
            {"downloaded": 1, "failed": 0, "stopped": 0}, self.jobs.get(job_id).summary
        )

    def test_delete_problem(self) -> None:
        queue = DownloadQueue()
        weights = Path(self.v1)
        other_job = JobRecord(id="x", type="scan", status="running")
        download_job = JobRecord(id="d", type=MODEL_DOWNLOAD_JOB, status="running")
        queue.submit(
            DownloadRequest(
                key="version:202",
                source="updates",
                model_id="101",
                version_id="202",
                model_type="lora",
                replace_path=self.v1,
            )
        )
        no_bin = (409, "Delete needs the send2trash package.")
        held = (409, "That file is being replaced. Try again when it's done.")
        gone = (404, "That LoRA is not there any more.")
        cases = [
            ("no recycle bin", (other_job, queue, None, False), no_bin),
            ("other job", (other_job, queue, weights, True), (409, DOWNLOAD_BUSY_ERROR)),
            ("no weights", (download_job, queue, None, True), gone),
            ("held", (download_job, queue, weights, True), held),
            ("another file", (download_job, queue, self.folder / "b.safetensors", True), None),
            ("idle", (None, DownloadQueue(), weights, True), None),
        ]
        for label, (job, held_queue, path, can_trash), expected in cases:
            with self.subTest(label):
                self.assertEqual(
                    expected,
                    delete_problem(job, queue=held_queue, weights=path, can_trash=can_trash),
                )

    def test_the_download_route_answers_the_helpers_refusals(self) -> None:
        self.assertEqual(
            (400, {"error": 'source must be "updates" or "gallery"'}),
            self.post_download(source="elsewhere"),
        )
        self.assertEqual((404, {"error": "job not found"}), self.post_download(jobId="gone"))
        self.assertEqual(
            (400, {"error": "That release is not one of this model's."}),
            self.post_download(versionId="999"),
        )
        with mock.patch("comfy_updater.routes.lora_roots", return_value=[self.folder]):
            self.assertEqual(
                (404, {"error": "Its info file is gone, or its weights are already there."}),
                self.post_download(source="gallery", name="glow.safetensors"),
            )
        try:
            self.jobs.start("check-updates", _blocking_runner(self.release))
            self.assertEqual((409, {"error": DOWNLOAD_BUSY_ERROR}), self.post_download())
        finally:
            self.release.set()
            _join_job_threads()
        self.journal.begin([{"kind": "rename", "from": "a", "to": "b", "entry": "a"}])
        self.assertEqual((409, {"error": JOURNAL_PENDING_ERROR}), self.post_download())
        self.assertEqual([], self.queue.snapshot())

    def test_a_gallery_download_is_queued_through_the_route(self) -> None:
        info = self.folder / "glow.civitai.info"
        info.write_text(json.dumps({"id": 202, "modelId": 101}), encoding="utf-8")
        with mock.patch("comfy_updater.routes.lora_roots", return_value=[self.folder]):
            status, body = self.post_download(source="gallery", name="glow.safetensors")
        self.assertEqual(200, status, body)
        self.assertEqual("gallery:glow.safetensors", body["request"]["key"])
        self.assertEqual("glow.safetensors", body["request"]["name"])
        self.assertEqual(MODEL_DOWNLOAD_JOB, self.jobs.get(body["jobId"]).type)

    def delete(self, name: str):
        bin_folder = self.root / "bin"
        bin_folder.mkdir(exist_ok=True)

        def fake_trash(path: str) -> None:
            os.replace(path, bin_folder / Path(path).name)

        with mock.patch("comfy_updater.routes.lora_roots", return_value=[self.folder]):
            with mock.patch("comfy_updater.routes.send_to_trash", fake_trash):
                return self.call("POST", "/warp-studio/updater/delete-model", {"name": name})

    def test_the_delete_route_sends_a_lora_to_the_recycle_bin(self) -> None:
        sub = self.folder / "studio-k"
        sub.mkdir()
        for suffix in (".safetensors", ".civitai.info", ".preview.png"):
            (sub / f"glow{suffix}").write_bytes(b"x")
        name = os.path.join("studio-k", "glow.safetensors")

        status, body = self.delete(name)

        self.assertEqual(200, status, body)
        self.assertEqual("glow.safetensors", body["removed"][0])
        self.assertEqual(
            ["glow.civitai.info", "glow.preview.png", "glow.safetensors"], sorted(body["removed"])
        )
        self.assertEqual([], body["warnings"])
        self.assertEqual([], list(sub.iterdir()))

    def test_the_delete_route_refuses_what_is_not_a_lora_in_the_folders(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "glow.safetensors").write_bytes(b"x")
        (self.folder / "glow.civitai.info").write_bytes(b"{}")
        gone = (404, {"error": "That LoRA is not there any more."})
        for name in (
            "",
            "missing.safetensors",
            os.path.join("..", "elsewhere", "glow.safetensors"),
            str(elsewhere / "glow.safetensors"),
            "glow.civitai.info",
        ):
            with self.subTest(name=name):
                self.assertEqual(gone, self.delete(name))
        self.assertTrue((elsewhere / "glow.safetensors").exists())
        self.assertTrue((self.folder / "glow.civitai.info").exists())

    def test_the_delete_route_answers_a_refused_trash_with_500(self) -> None:
        (self.folder / "glow.safetensors").write_bytes(b"x")

        def refuse(path: str) -> None:
            raise OSError("in use")

        with mock.patch("comfy_updater.routes.lora_roots", return_value=[self.folder]):
            with mock.patch("comfy_updater.routes.send_to_trash", refuse):
                status, body = self.call(
                    "POST", "/warp-studio/updater/delete-model", {"name": "glow.safetensors"}
                )
        self.assertEqual(
            (500, {"error": "Could not send glow.safetensors to the Recycle Bin: in use"}),
            (status, body),
        )
        self.assertTrue((self.folder / "glow.safetensors").exists())

    def test_the_delete_route_waits_for_another_kind_of_job(self) -> None:
        (self.folder / "glow.safetensors").write_bytes(b"x")
        try:
            self.jobs.start("scan", _blocking_runner(self.release))
            self.assertEqual((409, {"error": DOWNLOAD_BUSY_ERROR}), self.delete("glow.safetensors"))
        finally:
            self.release.set()
            _join_job_threads()
        self.assertTrue((self.folder / "glow.safetensors").exists())

    def test_a_refused_start_answers_409_with_the_requests_error(self) -> None:
        with mock.patch("comfy_updater.routes.download_problem", return_value=None):
            try:
                self.jobs.start("scan", _blocking_runner(self.release))
                status, body = self.post_download()
            finally:
                self.release.set()
                _join_job_threads()
        self.assertEqual((409, {"error": DOWNLOAD_BUSY_ERROR}), (status, body))
        self.assertEqual(["error"], [record["state"] for record in self.queue.snapshot()])


if __name__ == "__main__":
    unittest.main()
