"""The job slot held by the file-changing routes that are not jobs, and the saved check's writers."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from comfy_updater import check_records, jobs as jobs_module
from comfy_updater import routes as routes_module
from comfy_updater.config_store import ConfigStore
from comfy_updater.download_queue import MODEL_DOWNLOAD_JOB
from comfy_updater.jobs import JobManager
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, RenameJournal
from comfy_updater.routes import (
    JOB_RUNNING_ERROR,
    JOURNAL_PENDING_ERROR,
    _register_check_routes,
    _register_model_action_routes,
    _register_rename_routes,
    in_held_thread,
)

from test_archive_routes import _FakeService, _join_job_threads, _Request, _Routes

DOWNLOAD_BUSY = routes_module.DOWNLOAD_BUSY_ERROR


def _hold_job(manager: JobManager, job_type: str, case: unittest.TestCase):
    release = threading.Event()
    job = manager.start(job_type, lambda progress, item, control: release.wait(5) or ({}, []))
    case.addCleanup(release.set)
    return job, release


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    event = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        event.wait(0.01)
    return predicate()


class JobManagerHoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = JobManager()
        self.addCleanup(_join_job_threads)

    def test_a_hold_is_refused_while_a_job_runs_and_no_job_starts_while_held(self) -> None:
        _job, release = _hold_job(self.jobs, "scan", self)
        self.assertIsNone(self.jobs.hold("delete"))
        release.set()
        _join_job_threads()

        hold = self.jobs.hold("delete")
        self.assertIsNotNone(hold)
        self.assertEqual("delete", self.jobs.held_by())
        self.assertIsNone(self.jobs.start("scan", lambda *_: ({}, [])))
        # One hold at a time: two tabs' recoveries, or a delete and a recovery, never overlap.
        self.assertIsNone(self.jobs.hold("rename-recovery"))
        hold.release()
        hold.release()  # twice is harmless
        self.assertIsNone(self.jobs.held_by())
        self.assertIsNotNone(self.jobs.start("scan", lambda *_: ({}, [])))

    def test_a_hold_is_refused_while_a_job_is_reserved(self) -> None:
        self.jobs.reserve(MODEL_DOWNLOAD_JOB)
        self.assertIsNone(self.jobs.hold("delete"))
        self.jobs.release_reservation()
        self.assertIsNotNone(self.jobs.hold("delete"))

    def test_beside_lets_a_hold_run_next_to_that_job_type_only(self) -> None:
        _hold_job(self.jobs, MODEL_DOWNLOAD_JOB, self)
        self.assertIsNone(self.jobs.hold("rename-recovery"))
        hold = self.jobs.hold("delete", beside=(MODEL_DOWNLOAD_JOB,))
        self.assertIsNotNone(hold)
        hold.release()

    def test_work_keeps_the_slot_held_after_release_until_it_ends(self) -> None:
        hold = self.jobs.hold("rename-recovery")
        hold.begin_work()
        hold.release()
        self.assertEqual("rename-recovery", self.jobs.held_by())
        self.assertIsNone(self.jobs.start("scan", lambda *_: ({}, [])))
        hold.end_work()
        self.assertIsNone(self.jobs.held_by())

    def test_freeing_the_slot_tells_the_finish_listeners(self) -> None:
        seen = []
        self.jobs.add_finish_listener(lambda record: seen.append((record.type, self.jobs.held_by())))
        with self.jobs.hold("delete"):
            self.assertEqual([], seen)
        # Called once the slot is free, so a waiting download can start.
        self.assertEqual([("delete", None)], seen)


class HeldThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = JobManager()
        self.addCleanup(_join_job_threads)

    def test_a_cancelled_handler_leaves_the_slot_held_until_its_thread_ends(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        started = threading.Event()

        def work():
            started.set()
            gate.wait(5)
            return "done"

        async def handler(hold):
            try:
                return await in_held_thread(hold, work)
            finally:
                hold.release()

        async def main():
            hold = self.jobs.hold("rename-recovery")
            task = asyncio.ensure_future(handler(hold))
            await asyncio.sleep(0)
            self.assertTrue(await asyncio.get_running_loop().run_in_executor(None, started.wait, 5))
            task.cancel()  # aiohttp does this when the client goes away
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(main())
        # The files are still being changed: nothing else may start yet.
        self.assertEqual("rename-recovery", self.jobs.held_by())
        self.assertIsNone(self.jobs.start("check-updates", lambda *_: ({}, [])))
        gate.set()
        self.assertTrue(_wait_until(lambda: self.jobs.held_by() is None))

    def test_an_error_in_the_work_reaches_the_handler_and_frees_the_slot(self) -> None:
        def work():
            raise OSError("locked")

        async def main():
            hold = self.jobs.hold("delete")
            try:
                with self.assertRaises(OSError):
                    await in_held_thread(hold, work)
            finally:
                hold.release()

        asyncio.run(main())
        self.assertIsNone(self.jobs.held_by())


class _RouteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.config_store = ConfigStore(self.data_dir)
        self.jobs = JobManager()
        self.journal = RenameJournal(self.data_dir)
        self.routes = _Routes()
        self.addCleanup(_join_job_threads)

    def call(self, method: str, path: str, payload=None):
        handler = self.routes.handlers[(method, path)]
        response = asyncio.run(handler(_Request(method, payload)))
        return response.status, json.loads(response.text)

    def interrupt_a_rename(self) -> None:
        self.journal.begin([{"kind": "rename", "from": str(self.root / "a"), "to": str(self.root / "b"), "entry": "a"}])


class CheckWithAPendingJournalTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = _FakeService(self.jobs)
        _register_check_routes(self.routes, self.config_store, self.service, self.jobs, journal=self.journal)

    def test_a_check_is_refused_while_a_rename_waits_for_finish_or_undo(self) -> None:
        self.interrupt_a_rename()
        status, body = self.call("POST", "/warp-studio/updater/jobs/check-updates", {})
        self.assertEqual(409, status)
        self.assertEqual(JOURNAL_PENDING_ERROR, body["error"])
        self.assertTrue(body["journal"]["pending"])
        self.assertIsNone(self.jobs.get_active())

    def test_a_check_is_refused_as_busy_while_a_recovery_holds_the_slot(self) -> None:
        self.interrupt_a_rename()
        hold = self.jobs.hold(routes_module.RENAME_RECOVERY_HOLD)
        self.addCleanup(hold.release)
        status, body = self.call("POST", "/warp-studio/updater/jobs/check-updates", {})
        self.assertEqual(409, status)
        self.assertEqual(JOB_RUNNING_ERROR, body["error"])

    def test_the_check_saves_last_check_through_the_one_writer(self) -> None:
        with mock.patch("comfy_updater.routes.save_last_check", wraps=check_records.save_last_check) as save:
            status, body = self.call("POST", "/warp-studio/updater/jobs/check-updates", {})
            self.assertEqual(200, status, body)
            _join_job_threads()
        self.assertEqual(1, save.call_count)
        self.assertTrue((self.data_dir / "last_check.json").is_file())


class ScanWithAPendingJournalTests(_RouteCase):
    def test_a_scan_is_refused_while_a_rename_waits_for_finish_or_undo(self) -> None:
        from types import SimpleNamespace

        server = SimpleNamespace(instance=SimpleNamespace(routes=self.routes))
        with mock.patch("comfy_updater.routes.PromptServer", server), mock.patch(
            "comfy_updater.routes._ROUTES_REGISTERED", False
        ):
            routes_module.register_routes(
                self.config_store,
                _FakeService(self.jobs),
                self.jobs,
                None,
                decisions=DecisionStore(self.data_dir),
                base_codes=BaseCodeStore(self.data_dir),
                answer_cache=AnswerCache(self.data_dir),
                journal=self.journal,
            )
        self.interrupt_a_rename()
        status, body = self.call("POST", "/warp-studio/updater/jobs/scan", {})
        self.assertEqual(409, status)
        self.assertEqual(JOURNAL_PENDING_ERROR, body["error"])
        self.assertIsNone(self.jobs.get_active())


class RecoveryHoldsTheSlotTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        _register_rename_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(self.data_dir),
            BaseCodeStore(self.data_dir),
            AnswerCache(self.data_dir),
            self.journal,
        )

    def test_recovery_is_busy_while_a_job_runs(self) -> None:
        _hold_job(self.jobs, "check-updates", self)
        status, body = self.call("POST", "/warp-studio/updater/rename/recover", {"action": "undo"})
        self.assertEqual((409, JOB_RUNNING_ERROR), (status, body["error"]))

    def test_no_job_starts_while_a_recovery_moves_files(self) -> None:
        self.interrupt_a_rename()
        seen = {}
        real_recover = routes_module.recover

        def recover(journal, action, **kwargs):
            seen["held"] = self.jobs.held_by()
            seen["start"] = self.jobs.start("check-updates", lambda *_: ({}, []))
            # A second tab's recovery meanwhile is refused.
            seen["second"] = self.jobs.hold(routes_module.RENAME_RECOVERY_HOLD)
            return real_recover(journal, action, **kwargs)

        with mock.patch("comfy_updater.routes.recover", recover):
            status, body = self.call("POST", "/warp-studio/updater/rename/recover", {"action": "undo"})
        self.assertEqual(200, status, body)
        self.assertEqual(routes_module.RENAME_RECOVERY_HOLD, seen["held"])
        self.assertIsNone(seen["start"])
        self.assertIsNone(seen["second"])
        self.assertIsNone(self.jobs.held_by())

    def test_a_failed_recovery_frees_the_slot(self) -> None:
        with mock.patch("comfy_updater.routes.recover", side_effect=OSError("locked")):
            status, _body = self.call("POST", "/warp-studio/updater/rename/recover", {"action": "finish"})
        self.assertEqual(500, status)
        self.assertIsNone(self.jobs.held_by())


class DecisionAgainstApplyTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        _register_rename_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(self.data_dir),
            BaseCodeStore(self.data_dir),
            AnswerCache(self.data_dir),
            self.journal,
        )
        folder = self.root / "loras"
        folder.mkdir()
        self.row = {
            "key": str(folder / "old.safetensors").lower(),
            "modelId": "1",
            "versionId": "10",
            "fileKey": "file:10",
            "modelPath": str(folder / "old.safetensors"),
            "folder": str(folder),
            "oldStem": "old",
            "newStem": "maker_title_v1",
            "parts": {"creator": "maker", "title": "title", "version": "v1", "precision": "", "base": "sdxl"},
            "changed": [],
            "flags": [],
            "status": "change",
            "reason": "",
            "selected": True,
            "baseModel": "SDXL 1.0",
        }
        self.jobs.restore_job(
            routes_module.RENAME_DRY_RUN_JOB, "dry-1", startedAt="", finishedAt="", summary={}, items=[self.row]
        )

    def test_apply_from_another_tab_is_busy_while_a_decision_edit_reads_the_disk(self) -> None:
        answers = {}
        real_stems = routes_module._disk_stems

        def disk_stems(folder):
            # The edit is mid-way (it awaits this listing): the other tab clicks Apply.
            answers["apply"] = asyncio.run(
                self.routes.handlers[("POST", "/warp-studio/updater/jobs/rename-apply")](
                    _Request("POST", {"jobId": "dry-1", "keys": [self.row["key"]]})
                )
            )
            return real_stems(folder)

        with mock.patch("comfy_updater.routes._disk_stems", disk_stems):
            status, body = self.call(
                "POST", "/warp-studio/updater/rename/decision", {"jobId": "dry-1", "modelId": "1", "title": "Other"}
            )
        self.assertEqual(200, status, body)
        apply = answers["apply"]
        self.assertEqual(409, apply.status)
        self.assertEqual(JOB_RUNNING_ERROR, json.loads(apply.text)["error"])
        self.assertIsNone(self.jobs.get_active())
        self.assertIsNone(self.jobs.held_by())


class DeleteHoldsTheSlotTests(_RouteCase):
    def setUp(self) -> None:
        super().setUp()
        _register_model_action_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(self.data_dir),
            BaseCodeStore(self.data_dir),
            AnswerCache(self.data_dir),
            self.journal,
        )
        self.weights = self.root / "glow.safetensors"
        self.weights.write_bytes(b"x")
        item = {"modelPath": str(self.weights), "filePath": str(self.weights), "modelId": "1", "localVersionId": "2"}
        self.jobs.restore_job("check-updates", "check-1", startedAt="", finishedAt="", summary={}, items=[item])
        patch = mock.patch("comfy_updater.routes.send2trash_available", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)

    def delete(self, trash):
        with mock.patch("comfy_updater.routes.send_to_trash", trash):
            return self.call("POST", "/warp-studio/updater/delete-file", {"jobId": "check-1", "path": str(self.weights)})

    def test_no_job_starts_while_a_file_goes_to_the_bin(self) -> None:
        seen = {}

        def trash(path: str) -> None:
            seen["held"] = self.jobs.held_by()
            seen["start"] = self.jobs.start("check-updates", lambda *_: ({}, []))
            os.remove(path)

        status, body = self.delete(trash)
        self.assertEqual(200, status, body)
        self.assertEqual(routes_module.DELETE_HOLD, seen["held"])
        self.assertIsNone(seen["start"])
        self.assertIsNone(self.jobs.held_by())

    def test_a_delete_is_busy_while_another_holds_the_slot(self) -> None:
        hold = self.jobs.hold(routes_module.RENAME_RECOVERY_HOLD)
        self.addCleanup(hold.release)
        status, body = self.delete(os.remove)
        self.assertEqual((409, DOWNLOAD_BUSY), (status, body["error"]))
        self.assertTrue(self.weights.exists())

    def test_a_delete_still_runs_beside_a_download(self) -> None:
        _hold_job(self.jobs, MODEL_DOWNLOAD_JOB, self)
        status, body = self.delete(os.remove)
        self.assertEqual(200, status, body)
        self.assertFalse(self.weights.exists())


class GetItemsCopiesTests(unittest.TestCase):
    def test_the_grouping_reads_copies_taken_under_the_lock(self) -> None:
        jobs = JobManager()
        item = {"modelPath": "a.safetensors", "modelId": "1", "localVersionId": "2"}
        jobs.restore_job("check-updates", "check-1", startedAt="", finishedAt="", summary={}, items=[item])
        seen = []
        real = jobs_module._group_items_by_model

        def group(items, **kwargs):
            seen.extend(items)
            return real(items, **kwargs)

        with mock.patch("comfy_updater.jobs._group_items_by_model", group):
            jobs.get_items("check-1")
        self.assertTrue(seen)
        # A delete marks the held dict while the page is grouped: the grouping must not see it change.
        self.assertTrue(all(copy is not item for copy in seen))
        self.assertEqual(item, seen[0])


class LastCheckWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def test_each_write_has_a_temporary_file_of_its_own_in_the_same_folder(self) -> None:
        temps = []
        real_replace = os.replace

        def replace(src, dst):
            temps.append(Path(src))
            return real_replace(src, dst)

        with mock.patch("comfy_updater.check_records.os.replace", replace):
            check_records.save_last_check(self.data_dir, {"items": []})
            check_records.save_last_check(self.data_dir, {"items": [1]})
        self.assertEqual(2, len(set(temps)))
        self.assertTrue(all(temp.parent == self.data_dir for temp in temps))
        self.assertEqual({"items": [1]}, json.loads((self.data_dir / "last_check.json").read_text(encoding="utf-8")))
        self.assertEqual(["last_check.json"], sorted(path.name for path in self.data_dir.iterdir()))

    def test_writers_wait_for_the_write_lock(self) -> None:
        done = threading.Event()
        writers = [
            lambda: check_records.save_last_check(self.data_dir, {"items": []}),
            lambda: check_records.rewrite_saved_paths(self.data_dir, {"a": "b"}),
        ]
        for writer in writers:
            done.clear()
            with check_records._WRITE_LOCK:
                thread = threading.Thread(target=lambda w=writer: (w(), done.set()))
                thread.start()
                self.assertFalse(done.wait(0.2))
            thread.join(5)
            self.assertTrue(done.is_set())

    def test_rewrite_saved_paths_points_the_saved_check_at_the_new_names(self) -> None:
        check_records.save_last_check(self.data_dir, {"items": [{"modelPath": "old.safetensors"}]})
        check_records.rewrite_saved_paths(self.data_dir, {"old.safetensors": "new.safetensors"})
        saved = json.loads((self.data_dir / "last_check.json").read_text(encoding="utf-8"))
        self.assertEqual("new.safetensors", saved["items"][0]["modelPath"])


if __name__ == "__main__":
    unittest.main()
