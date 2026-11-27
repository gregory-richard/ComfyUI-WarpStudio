from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import test_archive_routes as archive_tests
from multidict import MultiDict
from comfy_updater.config_store import _int_in_range
from comfy_updater.path_resolver import normalize_model_types
from comfy_updater.routes import register_routes
from comfy_updater.constants import SUPPORTED_MODEL_TYPES
from comfy_updater.sidecar import write_json

CHECK_ROUTE = archive_tests.CHECK_ROUTE
LAST_CHECK_ROUTE = archive_tests.LAST_CHECK_ROUTE


class _QueryRequest(archive_tests._Request):
    def __init__(self, method, match_info=None):
        super().__init__(method, match_info=match_info)
        self.query = MultiDict()


class ProgressFileTests(archive_tests._ArchiveRoutesCase):
    def progress_path(self):
        return self.data_dir / "progress.json"

    def test_failed_check_removes_progress_file(self) -> None:
        seen = []

        def failing_check(payload, progress, item_callback=None, control=None, files_callback=None, client=None):
            # The handler writes progress.json just after the job starts.
            deadline = time.time() + 5
            while not self.progress_path().exists() and time.time() < deadline:
                time.sleep(0.01)
            seen.append(self.progress_path().exists())
            raise OSError("disk full")

        self.service.run_check_updates = failing_check
        job = self.check()
        self.assertEqual("failed", job.status)
        self.assertEqual([True], seen)
        self.assertFalse(self.progress_path().exists())

    def _hold_job(self, job_type: str):
        release = threading.Event()
        job = self.jobs.start(job_type, lambda progress, item, control: release.wait(5) or ({}, []))
        self.addCleanup(release.set)
        return job

    def test_other_running_job_is_not_reported_as_a_check(self) -> None:
        job = self._hold_job("scan")
        write_json(self.progress_path(), {"jobId": "gone-check", "startedAt": "", "progress": 0, "total": 0})

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertEqual(200, status)
        self.assertFalse((body.get("data") or {}).get("inProgress"))
        self.assertFalse(self.progress_path().exists())
        self.assertIsNotNone(job)

    def test_other_check_job_id_is_not_reported(self) -> None:
        self._hold_job("check-updates")
        write_json(self.progress_path(), {"jobId": "older-check", "startedAt": "", "progress": 0, "total": 0})

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertFalse((body.get("data") or {}).get("inProgress"))

    def test_running_check_is_reported_and_keeps_its_progress_file(self) -> None:
        job = self._hold_job("check-updates")
        write_json(self.progress_path(), {"jobId": job.id, "startedAt": "", "progress": 0, "total": 0})

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertEqual(200, status)
        self.assertTrue(body["data"]["inProgress"])
        self.assertEqual(job.id, body["data"]["jobId"])
        self.assertTrue(self.progress_path().exists())


class JobItemsOffLoopTests(archive_tests._ArchiveRoutesCase):
    def test_items_are_built_off_the_event_loop(self) -> None:
        routes = archive_tests._Routes()
        server = SimpleNamespace(instance=SimpleNamespace(routes=routes))
        with mock.patch("comfy_updater.routes.PromptServer", server), mock.patch(
            "comfy_updater.routes._ROUTES_REGISTERED", False
        ):
            register_routes(self.config_store, self.service, self.jobs, None)
        job = self.jobs.start("scan", lambda progress, item, control: ({}, []))
        archive_tests._join_job_threads()

        threads = []
        real = self.jobs.get_items

        def spy(*args, **kwargs):
            threads.append(threading.current_thread())
            return real(*args, **kwargs)

        handler = routes.handlers[("GET", "/warp-studio/updater/jobs/{job_id}/items")]
        request = _QueryRequest("GET", match_info={"job_id": job.id})
        with mock.patch.object(self.jobs, "get_items", spy):
            response = asyncio.run(handler(request))

        self.assertEqual(200, response.status, response.text)
        self.assertEqual(job.id, json.loads(response.text)["jobId"])
        self.assertEqual(1, len(threads))
        self.assertIsNot(threading.main_thread(), threads[0])


class InputGuardTests(unittest.TestCase):
    def test_model_types_that_are_not_a_list_mean_all_types(self) -> None:
        for raw in (5, "lora", {"a": 1}, 1.5, True):
            self.assertEqual(list(SUPPORTED_MODEL_TYPES), normalize_model_types(raw), raw)

    def test_model_type_entries_that_are_not_strings_are_skipped(self) -> None:
        self.assertEqual(list(SUPPORTED_MODEL_TYPES), normalize_model_types([1, None, [], {}]))
        self.assertEqual(["lora"], normalize_model_types([1, " LoRA ", None]))

    def test_int_in_range_survives_infinity_and_nan(self) -> None:
        self.assertEqual(30, _int_in_range(float("inf"), 30, 1, 120))
        self.assertEqual(30, _int_in_range(float("-inf"), 30, 1, 120))
        self.assertEqual(30, _int_in_range(float("nan"), 30, 1, 120))
        self.assertEqual(30, _int_in_range(json.loads("1e999"), 30, 1, 120))
        self.assertEqual(120, _int_in_range(500, 30, 1, 120))


if __name__ == "__main__":
    unittest.main()
