from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, RenameJournal


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
            journal._write = lambda payload: (writes.append(1), real_write(payload))
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


if __name__ == "__main__":
    unittest.main()
