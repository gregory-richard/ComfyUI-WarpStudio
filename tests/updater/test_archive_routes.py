"""Check with archive: the routes that run it, keep its results apart, and use them."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from comfy_updater.archive import ARCHIVE_RESULTS_FILENAME, folder_key, save_archive_results
from comfy_updater.config_store import ConfigStore
from comfy_updater.constants import CACHE_SCHEMA_VERSION
from comfy_updater.download_queue import DownloadQueue
from comfy_updater.jobs import JobManager, JobRecord
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, RenameJournal
from comfy_updater.routes import (
    _drop_refused_archive_folder,
    _normalize_job_payload,
    _register_check_routes,
    _register_model_action_routes,
    register_routes,
    updates_request,
)
from comfy_updater.sidecar import read_data_file, write_json

UNREACHABLE = "The archive folder can't be reached."
NO_MATCHING_FOLDER = "This archived model has no matching folder on this drive."
CHECK_ROUTE = "/warp-studio/updater/jobs/check-updates"
LAST_CHECK_ROUTE = "/warp-studio/updater/last-check"
STATS_ROUTE = "/warp-studio/updater/model-stats"


def _join_job_threads(timeout: float = 5.0) -> None:
    """Wait for every job thread a JobManager started (they are named after the job)."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name.startswith("civitai-updater-"):
            thread.join(timeout)
            if thread.is_alive():
                raise AssertionError(f"{thread.name} did not finish")


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


def _remote(version_id: str, date: str) -> dict:
    return {
        "versionId": version_id,
        "versionName": f"v{version_id}",
        "versionDate": date,
        "paid": "",
        "paidUntil": "",
    }


class _FakeService:
    """Stands in for UpdaterService: the check and the archive pass hand back set items."""

    def __init__(self, job_manager: JobManager) -> None:
        self.job_manager = job_manager
        self.d_items: list = []
        self.archive_items: list = []
        self.files: list = []
        self.current_paths: set = set()
        self.effective_roots: dict = {}
        self.archive_roots: list = []
        self.seeds_after_sync = None  # the job's items once the D walk was synced
        self.stop_during = ""

    def run_check_updates(self, payload, progress, item_callback=None, control=None, files_callback=None):
        if files_callback:
            files_callback(self.files)
        self.seeds_after_sync = [dict(item) for item in self.job_manager.get(control.job_id).items]
        for item in self.d_items:
            item_callback(dict(item))
        if self.stop_during == "check":
            control.cancel()
        summary = {"mode": "check", "total": len(self.d_items), "modelTypes": payload["modelTypes"]}
        return summary, [dict(item) for item in self.d_items]

    def run_archive_check(self, payload, progress, item_callback=None, control=None, *, archive_root):
        self.archive_roots.append(archive_root)
        for item in self.archive_items:
            item_callback(dict(item))
        if self.stop_during == "archive":
            control.cancel()
        summary = {
            "mode": "archive",
            "total": len(self.archive_items),
            "resolved": len(self.archive_items),
            "withUpdates": 1,
            "notFound": 0,
            "errors": 0,
            "withoutInfo": 2,
        }
        return summary, [dict(item) for item in self.archive_items]

    def inspect_current_files(self):
        return set(self.current_paths), []

    def get_effective_roots(self, model_types=None, include_custom_paths=True):
        return self.effective_roots


class _ArchiveRoutesCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.d_lora = self.root / "D" / "Lora"
        self.d_lora.mkdir(parents=True)
        self.archive = self.root / "Z"
        (self.archive / "Lora" / "sdxl").mkdir(parents=True)
        self.d_path = str(self.d_lora / "glow.safetensors")
        self.z_path = str(self.archive / "Lora" / "sdxl" / "old.civitai.info")
        self.config_store = ConfigStore(self.data_dir)
        self.config_store.update({"archiveFolder": str(self.archive)})
        self.jobs = JobManager()
        self.service = _FakeService(self.jobs)
        self.routes = _Routes()
        _register_check_routes(self.routes, self.config_store, self.service, self.jobs)
        self.addCleanup(_join_job_threads)

    # --- items and files ---

    def d_item(self, **fields) -> dict:
        item = {
            "modelPath": self.d_path,
            "filePath": self.d_path,
            "modelType": "lora",
            "metadataOnly": False,
            "modelId": "101",
            "localVersionId": "201",
            "status": "ok",
            "remoteVersions": [_remote("201", "2026-01-01T00:00:00.000Z")],
            "modelStats": {"downloads": 500, "likes": 50, "comments": 5},
        }
        item.update(fields)
        return item

    def z_item(self, **fields) -> dict:
        item = {
            "modelPath": self.z_path,
            "filePath": str(self.archive / "Lora" / "sdxl" / "old.safetensors"),
            "modelType": "lora",
            "metadataOnly": True,
            "archived": True,
            "modelId": "301",
            "localVersionId": "401",
            "status": "ok",
            "hasUpdate": True,
            "remoteVersions": [
                _remote("402", "2026-03-01T00:00:00.000Z"),
                _remote("401", "2026-01-01T00:00:00.000Z"),
            ],
            "modelStats": {"downloads": 9000, "likes": 900, "comments": 90},
        }
        item.update(fields)
        return item

    def write_last_check(self, items: list) -> None:
        write_json(
            self.data_dir / "last_check.json",
            {
                "schemaVersion": CACHE_SCHEMA_VERSION,
                "checkedAt": "2026-11-20T10:00:00+00:00",
                "summary": {"mode": "check", "total": len(items)},
                "items": items,
            },
        )

    def save_archive(self, items: list) -> dict:
        summary = {"mode": "archive", "total": len(items), "withUpdates": 7, "withoutInfo": 0}
        save_archive_results(self.data_dir, str(self.archive), summary, items)
        return read_data_file(self.data_dir / ARCHIVE_RESULTS_FILENAME)

    def last_check(self) -> dict:
        return read_data_file(self.data_dir / "last_check.json")

    def archive_file(self) -> bytes:
        return (self.data_dir / ARCHIVE_RESULTS_FILENAME).read_bytes()

    # --- calling the routes ---

    def call(self, method: str, path: str, payload=None, guarded: bool = True):
        handler = self.routes.handlers[(method, path)]
        response = asyncio.run(handler(_Request(method, payload, guarded=guarded)))
        body = json.loads(response.text)
        # No path in any answer, however it is spelled.
        text = "\n".join(_strings(body))
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(self.root.name, text)
        return response.status, body

    def check(self, **payload) -> JobRecord:
        status, body = self.call("POST", CHECK_ROUTE, payload)
        self.assertEqual(200, status, body)
        _join_job_threads()
        return self.jobs.get(body["jobId"])

    @staticmethod
    def paths(items: list) -> list:
        return sorted(str(item.get("modelPath")) for item in items)


class CheckWithArchiveTests(_ArchiveRoutesCase):
    def test_every_route_is_guarded(self) -> None:
        self.assertEqual(
            {("POST", CHECK_ROUTE), ("GET", LAST_CHECK_ROUTE), ("GET", STATS_ROUTE)},
            set(self.routes.handlers),
        )
        for (method, path), _handler in self.routes.handlers.items():
            with self.subTest(path=path, method=method):
                status, body = self.call(method, path, {}, guarded=False)
                self.assertEqual(403, status)
                self.assertIn("X-Civitai-Updater", body["error"])

    def test_check_with_archive_saves_archive_results_apart(self) -> None:
        self.write_last_check([self.d_item()])
        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item()]

        job = self.check(includeArchive=True)

        self.assertEqual([self.archive], self.service.archive_roots)
        self.assertEqual("completed", job.status)
        last = self.last_check()
        self.assertEqual([self.d_path], self.paths(last["items"]))
        self.assertFalse(any(item.get("archived") for item in last["items"]))
        self.assertNotIn("archive", last["summary"])
        self.assertNotIn("archiveError", last["summary"])
        saved = read_data_file(self.data_dir / ARCHIVE_RESULTS_FILENAME)
        self.assertEqual(str(self.archive), saved["archiveFolder"])
        self.assertEqual([self.z_path], self.paths(saved["items"]))
        self.assertEqual("archive", saved["summary"]["mode"])
        self.assertEqual(self.paths([self.d_item(), self.z_item()]), self.paths(job.items))
        self.assertFalse(any(item.get("_seeded") for item in job.items))
        self.assertEqual(
            {"checkedAt": saved["checkedAt"], **saved["summary"]}, job.summary["archive"]
        )
        self.assertNotIn("archiveError", job.summary)

    def test_a_check_of_some_types_keeps_the_other_archived_types(self) -> None:
        checkpoint_path = str(self.archive / "Checkpoints" / "base.civitai.info")
        checkpoint = self.z_item(modelPath=checkpoint_path, modelType="checkpoint", modelId="501")
        self.save_archive([self.z_item(), checkpoint])
        self.service.archive_items = [self.z_item(localVersionId="402", hasUpdate=False, status="not_found")]

        job = self.check(modelTypes=["lora"], includeArchive=True)

        saved = read_data_file(self.data_dir / ARCHIVE_RESULTS_FILENAME)
        self.assertEqual(sorted([self.z_path, checkpoint_path]), self.paths(saved["items"]))
        lora = next(item for item in saved["items"] if item["modelType"] == "lora")
        self.assertEqual("402", lora["localVersionId"])
        # The counts describe both types; withoutInfo is the pass's own (files, not results).
        summary = saved["summary"]
        self.assertEqual(
            (2, 1, 1, 1, 0, 2),
            (
                summary["total"],
                summary["resolved"],
                summary["withUpdates"],
                summary["notFound"],
                summary["errors"],
                summary["withoutInfo"],
            ),
        )
        self.assertEqual({"checkedAt": saved["checkedAt"], **summary}, job.summary["archive"])
        self.assertEqual(sorted([self.z_path, checkpoint_path]), self.paths(job.items))

        # A check of every type replaces them all: the checkpoint is no longer in the archive.
        job = self.check(includeArchive=True)

        saved = read_data_file(self.data_dir / ARCHIVE_RESULTS_FILENAME)
        self.assertEqual([self.z_path], self.paths(saved["items"]))
        self.assertEqual(1, saved["summary"]["total"])
        self.assertEqual([self.z_path], self.paths(job.items))

    def test_unreachable_archive_keeps_the_previous_results(self) -> None:
        self.write_last_check([self.d_item()])
        saved = self.save_archive([self.z_item()])
        before = self.archive_file()
        shutil.rmtree(self.archive)  # the drive is unplugged
        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item(localVersionId="402")]

        job = self.check(includeArchive=True)

        self.assertEqual([], self.service.archive_roots)
        self.assertEqual("completed", job.status)
        self.assertEqual(UNREACHABLE, job.summary["archiveError"])
        self.assertEqual(before, self.archive_file())
        self.assertEqual(saved["checkedAt"], job.summary["archive"]["checkedAt"])
        self.assertEqual(7, job.summary["archive"]["withUpdates"])
        self.assertEqual(self.paths([self.d_item(), self.z_item()]), self.paths(job.items))
        archived = [item for item in job.items if item.get("archived")]
        self.assertEqual("401", archived[0]["localVersionId"])
        # The normal part still finished and was saved, without archive fields.
        self.assertEqual([self.d_path], self.paths(self.last_check()["items"]))
        self.assertNotIn("archiveError", self.last_check()["summary"])

    def test_a_plain_check_keeps_the_archived_rows(self) -> None:
        self.write_last_check([self.d_item()])
        saved = self.save_archive([self.z_item()])
        before = self.archive_file()
        self.service.files = [{"path": Path(self.d_path), "modelType": "lora"}]
        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item(localVersionId="402")]

        job = self.check()

        self.assertEqual([], self.service.archive_roots)
        # Seeded with the saved archive rows, and syncing with D kept them.
        seeds = {item["modelPath"]: item for item in self.service.seeds_after_sync}
        self.assertIn(self.z_path, seeds)
        self.assertTrue(seeds[self.z_path]["_seeded"])
        self.assertTrue(seeds[self.z_path]["archived"])
        self.assertEqual("completed", job.status)
        self.assertEqual(self.paths([self.d_item(), self.z_item()]), self.paths(job.items))
        archived = [item for item in job.items if item.get("archived")]
        self.assertEqual("401", archived[0]["localVersionId"])
        self.assertNotIn("_seeded", archived[0])
        self.assertEqual(saved["checkedAt"], job.summary["archive"]["checkedAt"])
        self.assertNotIn("archiveError", job.summary)
        self.assertEqual(before, self.archive_file())
        last = self.last_check()
        self.assertEqual([self.d_path], self.paths(last["items"]))
        self.assertNotIn("archive", last["summary"])

    def test_fresh_archive_rows_replace_their_seeds(self) -> None:
        self.save_archive([self.z_item()])
        self.service.archive_items = [self.z_item(localVersionId="402")]
        seen: list = []
        service_check = self.service.run_archive_check

        def archive_pass(*args, **kwargs):
            result = service_check(*args, **kwargs)
            control = args[3]
            seen.extend(dict(item) for item in self.jobs.get(control.job_id).items)
            return result

        self.service.run_archive_check = archive_pass

        job = self.check(includeArchive=True)

        # One row per archived file while the pass streams, the fresh one.
        self.assertEqual([self.z_path], self.paths(seen))
        self.assertEqual("402", seen[0]["localVersionId"])
        self.assertNotIn("_seeded", seen[0])
        self.assertEqual([self.z_path], self.paths(job.items))

    def test_a_stopped_archive_pass_keeps_the_saved_results(self) -> None:
        self.write_last_check([self.d_item()])
        later_path = str(self.archive / "Lora" / "later.civitai.info")
        self.save_archive([self.z_item(), self.z_item(modelPath=later_path, modelId="302")])
        before = self.archive_file()
        self.service.d_items = [self.d_item(localVersionId="202")]
        # Stopped after the first archived row: the second one was never re-checked.
        self.service.archive_items = [self.z_item(localVersionId="402")]
        self.service.stop_during = "archive"

        job = self.check(includeArchive=True)

        self.assertEqual("cancelled", job.status)
        self.assertEqual(before, self.archive_file())
        # The normal part had finished: its results were saved.
        self.assertEqual("202", self.last_check()["items"][0]["localVersionId"])
        # Like a stopped check, the job keeps what it gathered: the fresh rows and the seeds not yet replaced.
        self.assertEqual(sorted([self.d_path, self.z_path, later_path]), self.paths(job.items))
        rows = {item["modelPath"]: item for item in job.items}
        self.assertEqual(("202", None), (rows[self.d_path]["localVersionId"], rows[self.d_path].get("_seeded")))
        self.assertEqual(("402", None), (rows[self.z_path]["localVersionId"], rows[self.z_path].get("_seeded")))
        self.assertEqual(("401", True), (rows[later_path]["localVersionId"], rows[later_path].get("_seeded")))

    def test_a_stopped_check_skips_the_archive_pass(self) -> None:
        self.save_archive([self.z_item()])
        before = self.archive_file()
        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item(localVersionId="402")]
        self.service.stop_during = "check"

        job = self.check(includeArchive=True)

        self.assertEqual("cancelled", job.status)
        self.assertEqual([], self.service.archive_roots)
        self.assertEqual(before, self.archive_file())

    def test_without_an_archive_folder_checks_behave_as_before(self) -> None:
        self.save_archive([self.z_item()])
        self.config_store.update({"archiveFolder": ""})
        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item()]

        job = self.check(includeArchive=True)

        self.assertEqual([], self.service.archive_roots)
        self.assertEqual([self.d_path], self.paths(job.items))
        self.assertNotIn("archive", job.summary)
        self.assertNotIn("archiveError", job.summary)

    def test_include_archive_needs_a_literal_true(self) -> None:
        self.assertTrue(_normalize_job_payload({"includeArchive": True})["includeArchive"])
        for value in ("true", 1, "yes", None, [True]):
            with self.subTest(value=value):
                self.assertFalse(_normalize_job_payload({"includeArchive": value})["includeArchive"])
        self.assertFalse(_normalize_job_payload({})["includeArchive"])

        self.service.d_items = [self.d_item()]
        self.service.archive_items = [self.z_item()]
        self.check(includeArchive="true")
        self.assertEqual([], self.service.archive_roots)


class LastCheckAndStatsTests(_ArchiveRoutesCase):
    def test_last_check_shows_archived_rows_and_the_archive_summary(self) -> None:
        self.write_last_check([self.d_item()])
        saved = self.save_archive([self.z_item()])
        self.service.current_paths = {self.d_path.lower()}

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertEqual(200, status)
        data = body["data"]
        self.assertEqual({"checkedAt": saved["checkedAt"], **saved["summary"]}, data["archive"])
        # The archive is not a change on D.
        self.assertFalse(data["filesChanged"])
        self.assertEqual((0, 0), (data["filesAdded"], data["filesRemoved"]))
        self.assertNotIn("archive", data["summary"])
        cached = self.jobs.get(data["jobId"])
        self.assertEqual(self.paths([self.d_item(), self.z_item()]), self.paths(cached.items))

    def test_last_check_without_an_archive_folder(self) -> None:
        self.write_last_check([self.d_item()])
        self.save_archive([self.z_item()])
        self.config_store.update({"archiveFolder": ""})
        self.service.current_paths = {self.d_path.lower()}

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertEqual(200, status)
        self.assertIsNone(body["data"]["archive"])
        self.assertEqual([self.d_path], self.paths(self.jobs.get(body["data"]["jobId"]).items))

    def test_archive_results_alone_are_not_a_last_check(self) -> None:
        self.save_archive([self.z_item()])

        status, body = self.call("GET", LAST_CHECK_ROUTE)

        self.assertEqual(200, status)
        self.assertIsNone(body["data"])

    def test_model_stats_route_merges_d_and_archive(self) -> None:
        self.write_last_check(
            [
                self.d_item(),
                # Checked before the numbers were kept: the archive's count.
                self.d_item(modelPath=str(self.d_lora / "plain.safetensors"), modelId="102", modelStats=None),
            ]
        )
        no_stats = self.z_item(modelPath=str(self.archive / "Lora" / "x.civitai.info"), modelId="302")
        del no_stats["modelStats"]
        self.save_archive(
            [
                self.z_item(modelId="101"),  # also on D: D's numbers win
                self.z_item(modelId="102", modelStats={"downloads": 2, "likes": 1, "comments": 0}),
                self.z_item(modelId="301"),
                no_stats,
            ]
        )

        status, body = self.call("GET", STATS_ROUTE)

        self.assertEqual(200, status)
        self.assertEqual(
            {
                "stats": {
                    "101": {"downloads": 500, "likes": 50, "comments": 5},
                    "102": {"downloads": 2, "likes": 1, "comments": 0},
                    "301": {"downloads": 9000, "likes": 900, "comments": 90},
                }
            },
            body,
        )

    def test_model_stats_without_any_results(self) -> None:
        self.assertEqual((200, {"stats": {}}), self.call("GET", STATS_ROUTE))


class ArchivedDownloadTests(_ArchiveRoutesCase):
    def setUp(self) -> None:
        super().setUp()
        (self.d_lora / "sdxl").mkdir()
        self.service.effective_roots = {"lora": [str(self.d_lora)]}
        self.queue = _register_model_action_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(self.data_dir),
            BaseCodeStore(self.data_dir),
            AnswerCache(self.data_dir),
            RenameJournal(self.data_dir),
            updater_service=self.service,
        )
        self.jobs.restore_job(
            "check-updates", "check-1", startedAt="", finishedAt="", summary={}, items=[self.z_item()]
        )
        self.requests: list = []

        def fake_download(request, *, report, should_stop, ctx):
            self.requests.append(request)

        patches = [
            mock.patch("comfy_updater.routes.download_one", fake_download),
            mock.patch("comfy_updater.routes.send2trash_available", return_value=True),
            mock.patch("comfy_updater.routes.list_naming_models", return_value=[]),
            mock.patch("comfy_updater.routes.has_text_generation", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(_join_job_threads)

    def post_download(self, **fields):
        payload = {
            "source": "updates",
            "jobId": "check-1",
            "modelId": "301",
            "versionId": "402",
            "modelPath": self.z_path,
        }
        payload.update(fields)
        return self.call("POST", "/warp-studio/updater/downloads", payload)

    def test_download_from_an_archived_row_targets_d(self) -> None:
        status, body = self.post_download()
        self.assertEqual(200, status, body)
        _join_job_threads()

        self.assertEqual(1, len(self.requests))
        reference = Path(self.requests[0].reference_path)
        self.assertEqual(self.d_lora / "sdxl" / "old.civitai.info", reference)
        self.assertFalse(reference.is_relative_to(self.archive))
        self.assertEqual("", self.requests[0].replace_path)

    def test_download_from_an_archived_row_works_with_the_archive_unplugged(self) -> None:
        shutil.rmtree(self.archive)

        status, body = self.post_download()
        self.assertEqual(200, status, body)
        _join_job_threads()

        self.assertEqual(
            [self.d_lora / "sdxl" / "old.civitai.info"],
            [Path(request.reference_path) for request in self.requests],
        )

    def test_register_routes_gives_downloads_the_archive_reference(self) -> None:
        routes = _Routes()
        server = SimpleNamespace(instance=SimpleNamespace(routes=routes))
        with mock.patch("comfy_updater.routes.PromptServer", server), mock.patch(
            "comfy_updater.routes._ROUTES_REGISTERED", False
        ):
            register_routes(
                self.config_store,
                self.service,
                self.jobs,
                None,
                decisions=DecisionStore(self.data_dir),
                base_codes=BaseCodeStore(self.data_dir),
                answer_cache=AnswerCache(self.data_dir),
                journal=RenameJournal(self.data_dir),
            )
        payload = {
            "source": "updates",
            "jobId": "check-1",
            "modelId": "301",
            "versionId": "402",
            "modelPath": self.z_path,
        }

        handler = routes.handlers[("POST", "/warp-studio/updater/downloads")]
        response = asyncio.run(handler(_Request("POST", payload)))
        self.assertEqual(200, response.status, response.text)
        _join_job_threads()

        self.assertEqual(
            [self.d_lora / "sdxl" / "old.civitai.info"],
            [Path(request.reference_path) for request in self.requests],
        )

    def test_replace_of_an_archived_row_is_refused(self) -> None:
        status, body = self.post_download(replace=True)

        self.assertEqual(400, status)
        self.assertEqual("Only a version on this drive can be replaced.", body["error"])
        self.assertEqual([], self.queue.snapshot())

    def test_an_archived_row_without_a_matching_folder_is_refused(self) -> None:
        self.service.effective_roots = {"lora": [str(self.root / "D" / "Loras")]}

        status, body = self.post_download()

        self.assertEqual((400, {"error": NO_MATCHING_FOLDER}), (status, body))
        self.assertEqual([], self.requests)

    def test_updates_request_needs_an_archive_reference_for_archived_rows(self) -> None:
        job = JobRecord(id="check-1", type="check-updates", status="completed", items=[self.z_item()])
        payload = {"modelId": "301", "versionId": "402", "modelPath": self.z_path, "replace": False}

        self.assertEqual(
            (400, NO_MATCHING_FOLDER),
            updates_request(payload, job, can_trash=True, queue=DownloadQueue()),
        )
        self.assertEqual(
            (400, NO_MATCHING_FOLDER),
            updates_request(
                payload, job, can_trash=True, queue=DownloadQueue(), archive_reference=lambda item: None
            ),
        )
        made = updates_request(
            payload,
            job,
            can_trash=True,
            queue=DownloadQueue(),
            archive_reference=lambda item: self.d_lora / "old.civitai.info",
        )
        self.assertEqual(str(self.d_lora / "old.civitai.info"), made.reference_path)


class RegisteredRoutesTests(unittest.TestCase):
    def test_register_routes_adds_the_check_routes_and_the_config_answers_carry_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "Z"
            archive.mkdir()
            store = ConfigStore(Path(tmp) / "data")
            jobs = JobManager()
            routes = _Routes()
            server = SimpleNamespace(instance=SimpleNamespace(routes=routes))
            with mock.patch("comfy_updater.routes.PromptServer", server), mock.patch(
                "comfy_updater.routes._ROUTES_REGISTERED", False
            ):
                register_routes(store, _FakeService(jobs), jobs, None)

            for key in (("POST", CHECK_ROUTE), ("GET", LAST_CHECK_ROUTE), ("GET", STATS_ROUTE)):
                self.assertIn(key, routes.handlers)
            save = routes.handlers[("POST", "/warp-studio/updater/config")]
            body = json.loads(asyncio.run(save(_Request("POST", {"archiveFolder": str(archive)}))).text)
            self.assertEqual((str(archive), []), (body["config"]["archiveFolder"], body["pathWarnings"]))
            load = routes.handlers[("GET", "/warp-studio/updater/config")]
            body = json.loads(asyncio.run(load(_Request("GET"))).text)
            self.assertEqual(str(archive), body["config"]["archiveFolder"])


class ArchiveFolderSettingTests(unittest.TestCase):
    def test_a_trailing_separator_leaves_the_folder_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stored = str(Path(tmp) / "unplugged" / "models")  # not there: the drive is off
            incoming = {"archiveFolder": stored + os.sep}
            self.assertEqual([], _drop_refused_archive_folder(incoming, stored))
            self.assertEqual(stored + os.sep, incoming["archiveFolder"])

            other = {"archiveFolder": str(Path(tmp) / "unplugged" / "other")}
            self.assertEqual(1, len(_drop_refused_archive_folder(other, stored)))
            self.assertNotIn("archiveFolder", other)

        if os.name == "nt":
            self.assertEqual([], _drop_refused_archive_folder({"archiveFolder": "Z:\\models\\"}, "Z:\\models"))
            self.assertEqual([], _drop_refused_archive_folder({"archiveFolder": "z:\\Models"}, "Z:\\models"))

    def test_no_stored_folder_compares_with_nothing(self) -> None:
        # "." would otherwise share the empty value's key and skip the check.
        self.assertEqual(1, len(_drop_refused_archive_folder({"archiveFolder": "."}, "")))

    def test_the_folder_key_ignores_a_trailing_separator(self) -> None:
        folder = os.path.join("archive", "models")
        self.assertEqual(folder_key(folder), folder_key(folder + os.sep))


if __name__ == "__main__":
    unittest.main()
