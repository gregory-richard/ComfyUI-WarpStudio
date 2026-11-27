from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, DryRunStore, RenameJournal


class RenameStoreTests(unittest.TestCase):
    def test_decisions_survive_reload_and_ask_again_clears_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = DecisionStore(data_dir)
            store.set_model("7", title="sheer clothes", creator="naughty_tokens", locked=True)
            store.set_version("70", "v2")
            store.set_file("file:700", version="v2 identity", keep=True)

            reloaded = DecisionStore(data_dir)
            self.assertEqual(
                {"title": "sheer clothes", "creator": "naughty_tokens", "locked": True},
                reloaded.model("7"),
            )
            self.assertEqual("v2", reloaded.version("70"))
            self.assertEqual({"version": "v2 identity", "keep": True}, reloaded.file("file:700"))

            reloaded.ask_again("7", ["70"], ["file:700"])
            self.assertEqual({}, reloaded.model("7"))
            self.assertIsNone(reloaded.version("70"))
            self.assertEqual({}, reloaded.file("file:700"))
            again = DecisionStore(data_dir)
            self.assertEqual({}, again.model("7"))
            self.assertIsNone(again.version("70"))
            self.assertEqual({}, again.file("file:700"))

    def test_set_model_leaves_unnamed_fields_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DecisionStore(Path(tmpdir))
            store.set_model("7", title="a")
            store.set_model("7", creator="b")
            self.assertEqual({"title": "a", "creator": "b"}, store.model("7"))

    def test_lock_applied_records_locked_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DecisionStore(Path(tmpdir))
            store.lock_applied(
                "7",
                "sheer clothes",
                "naughty_tokens",
                {"70": "v2", "71": "v3"},
                {"file:700": "v2 identity"},
            )
            self.assertEqual(
                {"title": "sheer clothes", "creator": "naughty_tokens", "locked": True},
                store.model("7"),
            )
            self.assertEqual("v2", store.version("70"))
            self.assertEqual("v3", store.version("71"))
            self.assertEqual({"version": "v2 identity"}, store.file("file:700"))

    def test_empty_locked_versions_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = DecisionStore(Path(tmpdir))
            store.lock_applied("8", "nora wrap midi dress", "silkthread", {"80": ""}, {"file:800": ""})
            reloaded = DecisionStore(Path(tmpdir))
            self.assertEqual("", reloaded.version("80"))
            self.assertEqual({"version": ""}, reloaded.file("file:800"))

    def test_base_codes_seed_once_from_most_common_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            store = BaseCodeStore(data_dir)
            store.seed(
                [
                    ("Krea 2", "a - b (krea2)"),
                    ("Krea 2", "c - d (krea2)"),
                    ("Krea 2", "e - f (kr2)"),
                    ("Pony", "no tag"),
                ]
            )
            self.assertEqual({"Krea 2": "krea2"}, store.all())
            self.assertEqual("krea2", store.code("Krea 2"))
            self.assertIsNone(store.code("Pony"))

            store.seed([("Pony", "x - y (pony)")])
            self.assertEqual({"Krea 2": "krea2"}, store.all())
            reloaded = BaseCodeStore(data_dir)
            reloaded.seed([("Pony", "x - y (pony)")])
            self.assertEqual({"Krea 2": "krea2"}, reloaded.all())

            reloaded.set("ZImageTurbo", "zit")
            self.assertEqual("zit", BaseCodeStore(data_dir).code("ZImageTurbo"))

    def test_answer_cache_key_is_order_independent_and_drop_model_is_scoped(self) -> None:
        self.assertEqual(AnswerCache.key("5", {"a": 1, "b": 2}), AnswerCache.key("5", {"b": 2, "a": 1}))
        self.assertNotEqual(AnswerCache.key("5", {"a": 1}), AnswerCache.key("5", {"a": 2}))
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            cache = AnswerCache(data_dir)
            five = AnswerCache.key("5", {"a": 1})
            fifty = AnswerCache.key("50", {"a": 1})
            cache.put(five, {"title": "five"})
            cache.put(fifty, {"title": "fifty"})
            self.assertEqual({"title": "five"}, AnswerCache(data_dir).get(five))

            cache.drop_model("5")
            self.assertIsNone(cache.get(five))
            self.assertEqual({"title": "fifty"}, cache.get(fifty))
            self.assertIsNone(AnswerCache(data_dir).get(five))

            cache.drop(fifty)
            cache.drop(fifty)  # dropping what is not there is fine
            self.assertIsNone(AnswerCache(data_dir).get(fifty))

    def test_journal_tracks_steps_across_instances_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            journal = RenameJournal(data_dir)
            self.assertIsNone(journal.pending())
            run_id = journal.begin(
                [
                    {"kind": "rename", "from": "a.safetensors", "to": "b.safetensors", "entry": "e1"},
                    {"kind": "trash", "from": "c.safetensors", "to": None, "entry": "e2"},
                ]
            )
            journal.mark_done(0)

            pending = RenameJournal(data_dir).pending()
            self.assertIsNotNone(pending)
            self.assertEqual(run_id, pending["runId"])
            self.assertTrue(pending["startedAt"])
            self.assertIs(True, pending["steps"][0]["done"])
            self.assertIs(False, pending["steps"][1]["done"])

            journal.mark_done(1)
            self.assertIsNone(journal.pending())
            journal.clear()
            self.assertIsNone(journal.pending())
            self.assertIsNone(RenameJournal(data_dir).pending())

    def test_journal_mark_done_many_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            journal = RenameJournal(data_dir)
            journal.begin([{"kind": "rename", "from": f"a{n}", "to": f"b{n}", "entry": "e"} for n in range(3)])
            writes = []
            real_write = journal._write
            journal._write = lambda payload, **kwargs: (writes.append(1), real_write(payload, **kwargs))
            journal.mark_done_many([0, 2])
            self.assertEqual(1, len(writes))
            pending = RenameJournal(data_dir).pending()
            self.assertEqual([True, False, True], [step["done"] for step in pending["steps"]])
            journal.mark_done_many([1])
            self.assertIsNone(RenameJournal(data_dir).pending())

    def test_journal_keep_only_keeps_the_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            journal = RenameJournal(data_dir)
            run_id = journal.begin([{"kind": "rename", "from": f"a{n}", "to": f"b{n}", "entry": f"e{n}"} for n in range(3)])
            journal.mark_done_many([0, 1, 2])
            journal.keep_only([1])
            pending = RenameJournal(data_dir).pending()
            self.assertEqual(run_id, pending["runId"])
            self.assertEqual([("a1", False)], [(step["from"], step["done"]) for step in pending["steps"]])

    def test_journal_is_compact_and_synced_before_the_first_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            journal = RenameJournal(data_dir)
            with mock.patch("os.fsync") as fsync:
                journal.begin([{"kind": "rename", "from": f"a{n}", "to": f"b{n}", "entry": f"e{n}"} for n in range(3)])
            self.assertTrue(fsync.called)
            text = (data_dir / "rename_journal.json").read_text(encoding="utf-8")
            self.assertNotIn("\n", text)
            self.assertNotIn(": ", text)
            journal.mark_done(0)
            self.assertNotIn("\n", (data_dir / "rename_journal.json").read_text(encoding="utf-8"))

    def test_journal_keeps_delete_and_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            RenameJournal(data_dir).begin([
                {"kind": "trash", "from": "a.safetensors", "to": "", "entry": "a", "delete": True},
                {"kind": "rename", "from": "b.safetensors", "to": "c.safetensors", "entry": "b", "suffix": ".safetensors"},
                {"kind": "rename", "from": "d.safetensors", "to": "e.safetensors", "entry": "d"},
            ])
            steps = RenameJournal(data_dir).pending()["steps"]
            self.assertIs(True, steps[0]["delete"])
            self.assertEqual(".safetensors", steps[1]["suffix"])
            # Absent fields stay absent.
            self.assertEqual({"kind", "from", "to", "entry", "done"}, set(steps[2]))

    def test_marking_steps_done_is_synced(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = RenameJournal(Path(tmpdir))
            journal.begin([{"kind": "rename", "from": f"a{n}", "to": f"b{n}", "entry": f"e{n}"} for n in range(3)])
            with mock.patch("os.fsync") as fsync:
                journal.mark_done(0)
            self.assertEqual(1, fsync.call_count)
            with mock.patch("os.fsync") as fsync:
                journal.mark_done_many([1, 2])
            self.assertEqual(1, fsync.call_count)

    def test_other_stores_keep_their_indented_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            DecisionStore(data_dir).set_model("7", title="t")
            self.assertIn("\n  ", (data_dir / "rename_decisions.json").read_text(encoding="utf-8"))

    def test_corrupt_decisions_file_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            path = data_dir / "rename_decisions.json"
            path.write_text("{not json", encoding="utf-8")
            with redirect_stdout(StringIO()):
                store = DecisionStore(data_dir)
            self.assertEqual({}, store.model("7"))
            self.assertEqual(1, len(list(data_dir.glob("rename_decisions.json.corrupt-*"))))
            self.assertEqual(
                {"models": {}, "versions": {}, "files": {}},
                json.loads(path.read_text(encoding="utf-8")),
            )


def _snapshot(job_id="run-1", rows=None, finished="2026-11-14T10:00:00+00:00"):
    return {
        "jobId": job_id,
        "type": "rename-dry-run",
        "status": "completed",
        "startedAt": "2026-11-14T09:00:00+00:00",
        "finishedAt": finished,
        "progress": 2,
        "total": 2,
        "summary": {"change": 1},
        "items": rows if rows is not None else [{"key": "a", "newStem": "A"}, {"key": "b", "newStem": "B"}],
    }


class DryRunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_saved_dry_run_survives_a_reload(self) -> None:
        DryRunStore(self.data_dir).save(_snapshot())
        loaded = DryRunStore(self.data_dir).load()
        self.assertEqual("run-1", loaded["jobId"])
        self.assertEqual("2026-11-14T10:00:00+00:00", loaded["finishedAt"])
        self.assertEqual({"change": 1}, loaded["summary"])
        self.assertEqual(["A", "B"], [row["newStem"] for row in loaded["rows"]])
        self.assertFalse(loaded["applied"])
        self.assertEqual("run-1", DryRunStore(self.data_dir).job_id())

    def test_nothing_saved_loads_none(self) -> None:
        store = DryRunStore(self.data_dir)
        self.assertIsNone(store.load())
        self.assertIsNone(store.job_id())
        self.assertFalse((self.data_dir / "last_dry_run.json").exists())

    def test_applied_flag_round_trips_and_a_new_run_resets_it(self) -> None:
        store = DryRunStore(self.data_dir)
        store.save(_snapshot())
        store.mark_applied()
        self.assertTrue(DryRunStore(self.data_dir).load()["applied"])
        store.save(_snapshot("run-2"))
        loaded = DryRunStore(self.data_dir).load()
        self.assertEqual("run-2", loaded["jobId"])
        self.assertFalse(loaded["applied"])

    def test_row_updates_follow_the_revision_and_the_job(self) -> None:
        store = DryRunStore(self.data_dir)
        store.save(_snapshot())
        older = store.next_revision()
        newer = store.next_revision()
        store.update_rows("run-1", [{"key": "a", "newStem": "new"}], newer)
        # A write that lost the race to the thread pool must not undo the newer one.
        store.update_rows("run-1", [{"key": "a", "newStem": "old"}], older)
        # Another (older) dry run's edits never replace the saved one.
        store.update_rows("run-0", [{"key": "z"}], store.next_revision())
        self.assertEqual([{"key": "a", "newStem": "new"}], DryRunStore(self.data_dir).load()["rows"])

    def test_corrupt_file_is_quarantined_and_ignored(self) -> None:
        path = self.data_dir / "last_dry_run.json"
        path.write_text("{not json", encoding="utf-8")
        with redirect_stdout(StringIO()):
            store = DryRunStore(self.data_dir)
        self.assertIsNone(store.load())
        self.assertEqual(1, len(list(self.data_dir.glob("last_dry_run.json.corrupt-*"))))
        store.save(_snapshot())
        self.assertEqual("run-1", DryRunStore(self.data_dir).load()["jobId"])

    def test_a_file_of_the_wrong_shape_is_ignored(self) -> None:
        path = self.data_dir / "last_dry_run.json"
        path.write_text(json.dumps({"jobId": "", "rows": "nope"}), encoding="utf-8")
        self.assertIsNone(DryRunStore(self.data_dir).load())
        path.write_text(json.dumps({"jobId": "x", "rows": [{"key": "a"}, 5]}), encoding="utf-8")
        loaded = DryRunStore(self.data_dir).load()
        self.assertEqual([{"key": "a"}], loaded["rows"])
        self.assertEqual({}, loaded["summary"])

    def test_the_applied_flag_is_synced_to_disk(self) -> None:
        store = DryRunStore(self.data_dir)
        store.save(_snapshot())
        with mock.patch("comfy_updater.sidecar.os.fsync") as fsync:
            self.assertIsNone(store.mark_applied())
        self.assertTrue(fsync.called)
        self.assertTrue(DryRunStore(self.data_dir).load()["applied"])

    def test_a_failed_applied_write_deletes_the_saved_run(self) -> None:
        store = DryRunStore(self.data_dir)
        store.save(_snapshot())
        with mock.patch("comfy_updater.rename_store.write_json", side_effect=OSError("disk full")), \
                redirect_stdout(StringIO()) as out:
            warning = store.mark_applied()
        self.assertIn("disk full", warning)
        self.assertIn("deleted", warning)
        self.assertIn(warning, out.getvalue())
        # A missing saved run is safe; a stale one that could be applied again is not.
        self.assertFalse((self.data_dir / "last_dry_run.json").exists())
        self.assertIsNone(store.job_id())
        self.assertIsNone(DryRunStore(self.data_dir).load())

    def test_a_failed_applied_write_that_cannot_delete_says_so(self) -> None:
        store = DryRunStore(self.data_dir)
        store.save(_snapshot())
        with mock.patch("comfy_updater.rename_store.write_json", side_effect=OSError("disk full")), \
                mock.patch.object(Path, "unlink", side_effect=OSError("locked")), \
                redirect_stdout(StringIO()):
            warning = store.mark_applied()
        self.assertIn("locked", warning)
        self.assertIn("by hand", warning)

    def _blocking_write(self):
        import threading

        entered, release = threading.Event(), threading.Event()
        real = __import__("comfy_updater.rename_store", fromlist=["write_json"]).write_json

        def slow(path, payload, **kwargs):
            entered.set()
            release.wait(timeout=5)
            real(path, payload, **kwargs)

        return entered, release, slow

    def test_reads_do_not_wait_for_a_write_in_progress(self) -> None:
        import threading

        store = DryRunStore(self.data_dir)
        entered, release, slow = self._blocking_write()
        with mock.patch("comfy_updater.rename_store.write_json", side_effect=slow):
            writer = threading.Thread(target=store.save, args=(_snapshot(),))
            writer.start()
            self.assertTrue(entered.wait(timeout=5))
            results: list = []
            reader = threading.Thread(target=lambda: results.append((store.job_id(), store.next_revision())))
            reader.start()
            reader.join(timeout=1)
            self.assertFalse(reader.is_alive(), "job_id()/next_revision() waited for the disk write")
            release.set()
            writer.join(timeout=5)
        self.assertEqual("run-1", results[0][0])

    def test_the_last_state_reaches_the_disk_when_writes_overlap(self) -> None:
        import threading

        store = DryRunStore(self.data_dir)
        entered, release, slow = self._blocking_write()
        with mock.patch("comfy_updater.rename_store.write_json", side_effect=slow):
            writer = threading.Thread(target=store.save, args=(_snapshot(),))
            writer.start()
            self.assertTrue(entered.wait(timeout=5))
            editor = threading.Thread(
                target=store.update_rows, args=("run-1", [{"key": "a", "newStem": "edited"}], store.next_revision())
            )
            editor.start()
            release.set()
            writer.join(timeout=5)
            editor.join(timeout=5)
        self.assertEqual([{"key": "a", "newStem": "edited"}], DryRunStore(self.data_dir).load()["rows"])

    def test_the_file_is_written_compact(self) -> None:
        DryRunStore(self.data_dir).save(_snapshot())
        text = (self.data_dir / "last_dry_run.json").read_text(encoding="utf-8")
        self.assertNotIn("\n", text)


if __name__ == "__main__":
    unittest.main()
