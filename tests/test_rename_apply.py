from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_updater import rename_apply
from comfy_updater.rename_apply import (
    apply_steps,
    assign_siblings,
    file_identity,
    plan,
    plan_steps,
    recover,
    refresh_sidecar_title,
    update_saved_paths,
)
from comfy_updater.rename_store import RenameJournal

SIDECARS = (".safetensors", ".civitai.info", ".preview.png")


def _make(folder: Path, stem: str, suffixes=SIDECARS) -> None:
    for suffix in suffixes:
        (folder / (stem + suffix)).write_text(stem + suffix, encoding="utf-8")


def _row(folder: Path, old: str, new: str, status: str = "change", **extra) -> dict:
    row = {
        "key": str(folder / (old + ".safetensors")).lower(),
        "folder": str(folder),
        "oldStem": old,
        "newStem": new,
        "status": status,
    }
    row.update(extra)
    return row


def _same_weights(folder: Path, *stems: str) -> None:
    """Duplicate copies are byte-identical; a removal checks that."""
    for stem in stems:
        (folder / (stem + ".safetensors")).write_bytes(b"identical weights")


def _names(folder: Path) -> set:
    return set(os.listdir(folder))


class _NoTrash:
    def __call__(self, path):  # pragma: no cover - must never be called by rename-only tests
        raise AssertionError(f"unexpected trash of {path}")


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.folder = root / "loras"
        self.folder.mkdir()
        self.data_dir = root / "data"
        self.data_dir.mkdir()
        self.journal = RenameJournal(self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, rows, selected=None, remove=(), **kwargs):
        selected = {row["key"] for row in rows} if selected is None else selected
        steps, planned_skips = plan(rows, set(selected), set(remove))
        kwargs.setdefault("trash", _NoTrash())
        result = apply_steps(steps, self.journal, **kwargs)
        result["skipped"] = planned_skips + result["skipped"]
        return result

    def test_renames_weights_and_every_sibling(self) -> None:
        old = "fotomax - nikora hyperreal - 1.0 (flux2k9b)"
        new = "fotomax - nikora hyperreal - v1.5 (flux2k9b)"
        _make(self.folder, old)
        result = self._run([_row(self.folder, old, new)])
        self.assertEqual({new + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual([str(self.folder / (old + ".safetensors")).lower()], result["done"])
        self.assertEqual([], result["skipped"])
        self.assertIsNone(self.journal.pending())
        self.assertFalse((self.data_dir / "rename_journal.json").exists())

    def test_siblings_go_to_the_longest_stem(self) -> None:
        for name in ("flux.safetensors", "flux.civitai.info", "flux.1 dev.safetensors", "flux.1 dev.civitai.info"):
            (self.folder / name).write_text("x", encoding="utf-8")
        siblings = assign_siblings(self.folder)
        self.assertEqual(
            {"flux.safetensors", "flux.civitai.info"}, {p.name for p in siblings["flux"]}
        )
        self.assertEqual(
            {"flux.1 dev.safetensors", "flux.1 dev.civitai.info"}, {p.name for p in siblings["flux.1 dev"]}
        )

    def test_weights_step_comes_first(self) -> None:
        _make(self.folder, "a", (".civitai.info", ".preview.png", ".safetensors"))
        steps = plan_steps([_row(self.folder, "a", "b")], {str(self.folder / "a.safetensors").lower()}, set())
        self.assertEqual(3, len(steps))
        self.assertEqual("a.safetensors", Path(steps[0]["from"]).name)
        self.assertEqual({"rename"}, {s["kind"] for s in steps})

    def test_case_only_rename(self) -> None:
        old, new = "BustlineXL - v1 (sdxl)", "bustlinexl - v1 (sdxl)"
        _make(self.folder, old)
        result = self._run([_row(self.folder, old, new)])
        self.assertEqual({new + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual(1, len(result["done"]))

    def test_existing_target_skips_the_entry(self) -> None:
        _make(self.folder, "old")
        (self.folder / "new.preview.png").write_text("someone else's", encoding="utf-8")
        before = _names(self.folder)
        result = self._run([_row(self.folder, "old", "new")])
        self.assertEqual(before, _names(self.folder))
        self.assertEqual([], result["done"])
        self.assertEqual(1, len(result["skipped"]))
        self.assertEqual("target exists", result["skipped"][0]["reason"])
        self.assertEqual("someone else's", (self.folder / "new.preview.png").read_text(encoding="utf-8"))

    def test_locked_weights_skip_only_that_entry(self) -> None:
        _make(self.folder, "locked")
        _make(self.folder, "free")
        real_rename = os.rename

        def fake_rename(src, dst):
            if Path(src).name == "locked.safetensors":
                raise PermissionError("in use")
            return real_rename(src, dst)

        rows = [_row(self.folder, "locked", "locked2"), _row(self.folder, "free", "free2")]
        with mock.patch("os.rename", side_effect=fake_rename):
            result = self._run(rows)
        self.assertEqual(
            {"locked" + s for s in SIDECARS} | {"free2" + s for s in SIDECARS}, _names(self.folder)
        )
        self.assertEqual([rows[1]["key"]], result["done"])
        self.assertEqual(rows[0]["key"], result["skipped"][0]["entry"])

    def test_failure_on_a_later_sibling_rolls_the_entry_back(self) -> None:
        _make(self.folder, "old")
        real_rename = os.rename

        def fake_rename(src, dst):
            if Path(src).name == "old.preview.png":
                raise PermissionError("in use")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=fake_rename):
            result = self._run([_row(self.folder, "old", "new")])
        self.assertEqual({"old" + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual([], result["done"])
        self.assertEqual(1, len(result["skipped"]))

    def _crash_after_first_entry(self, rows):
        calls = []

        def progress(current, total, message):
            calls.append(current)
            raise RuntimeError("ComfyUI stopped")

        with self.assertRaises(RuntimeError):
            self._run(rows, progress=progress)
        self.assertEqual([1], calls)

    def test_crash_then_undo(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]
        self._crash_after_first_entry(rows)
        pending = RenameJournal(self.data_dir).pending()
        self.assertIsNotNone(pending)
        left = [s for s in pending["steps"] if not s["done"]]
        self.assertEqual({rows[1]["key"]}, {s["entry"] for s in left})
        self.assertEqual(3, len(left))

        result = recover(RenameJournal(self.data_dir), "undo")
        self.assertEqual({"a" + s for s in SIDECARS} | {"b" + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual([rows[0]["key"]], result["done"])
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def test_crash_then_finish(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]
        self._crash_after_first_entry(rows)
        result = recover(RenameJournal(self.data_dir), "finish")
        self.assertEqual({"a2" + s for s in SIDECARS} | {"b2" + s for s in SIDECARS}, _names(self.folder))
        self.assertIn(rows[1]["key"], result["done"])
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def test_undo_with_a_locked_file_can_be_retried(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]
        self._crash_after_first_entry(rows)
        real_rename = os.rename

        def locked(src, dst):
            if Path(src).name == "a2.preview.png":
                raise PermissionError("in use")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=locked):
            first = recover(RenameJournal(self.data_dir), "undo")
        self.assertEqual([rows[0]["key"]], [s["entry"] for s in first["skipped"]])
        pending = RenameJournal(self.data_dir).pending()
        self.assertIsNotNone(pending, "the half-undone entry must stay journaled")
        self.assertEqual({rows[0]["key"]}, {s["entry"] for s in pending["steps"]})

        second = recover(RenameJournal(self.data_dir), "undo")
        self.assertEqual([], second["skipped"])
        self.assertEqual({"a" + s for s in SIDECARS} | {"b" + s for s in SIDECARS}, _names(self.folder))
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def test_finish_with_a_locked_file_can_be_retried(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]
        self._crash_after_first_entry(rows)
        real_rename = os.rename

        def locked(src, dst):
            if Path(src).name == "b.preview.png":
                raise PermissionError("in use")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=locked):
            first = recover(RenameJournal(self.data_dir), "finish")
        self.assertEqual([rows[1]["key"]], [s["entry"] for s in first["skipped"]])
        pending = RenameJournal(self.data_dir).pending()
        self.assertIsNotNone(pending)
        self.assertEqual({rows[1]["key"]}, {s["entry"] for s in pending["steps"]})

        second = recover(RenameJournal(self.data_dir), "finish")
        self.assertEqual([rows[1]["key"]], second["done"])
        self.assertEqual({"a2" + s for s in SIDECARS} | {"b2" + s for s in SIDECARS}, _names(self.folder))
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def test_undo_of_a_removed_copy_does_not_keep_the_journal(self) -> None:
        _make(self.folder, "copy")
        _make(self.folder, "keep")
        survivor, dup = self._dup_pair("keep", "keep2", "copy")
        steps = plan_steps([survivor, dup], {survivor["key"]}, {dup["key"]})
        self.journal.begin(steps)
        self.journal.mark_done_many([i for i, s in enumerate(steps) if s["kind"] == "trash"])
        for name in ("copy" + s for s in SIDECARS):
            os.remove(self.folder / name)
        result = recover(RenameJournal(self.data_dir), "undo")
        self.assertIn(rename_apply.IN_RECYCLE_BIN, {s["reason"] for s in result["skipped"]})
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def _strand_case_only_rename(self):
        old, new = "BustlineXL - v1 (sdxl)", "bustlinexl - v1 (sdxl)"
        _make(self.folder, old, (".safetensors",))
        row = _row(self.folder, old, new)
        steps = plan_steps([row], {row["key"]}, set())
        run_id = self.journal.begin(steps)
        # Crash between the two halves of the case-only rename.
        tmp = self.folder / (new + ".cu-tmp-" + run_id[:8] + ".safetensors")
        os.rename(self.folder / (old + ".safetensors"), tmp)
        return old, new

    def test_finish_completes_a_stranded_case_only_rename(self) -> None:
        _old, new = self._strand_case_only_rename()
        recover(RenameJournal(self.data_dir), "finish")
        self.assertEqual({new + ".safetensors"}, _names(self.folder))

    def test_undo_restores_a_stranded_case_only_rename(self) -> None:
        old, _new = self._strand_case_only_rename()
        recover(RenameJournal(self.data_dir), "undo")
        self.assertEqual({old + ".safetensors"}, _names(self.folder))

    def test_two_entries_wanting_one_name_keep_the_second_unchanged(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "c"), _row(self.folder, "b", "c")]
        result = self._run(rows)
        self.assertEqual({"c" + s for s in SIDECARS} | {"b" + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual([rows[0]["key"]], result["done"])
        self.assertEqual("target exists", result["skipped"][0]["reason"])
        self.assertEqual("a.safetensors", (self.folder / "c.safetensors").read_text(encoding="utf-8"))

    def test_refuses_to_start_over_an_unfinished_run(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]
        self._crash_after_first_entry(rows)
        with self.assertRaises(RuntimeError):
            self._run([_row(self.folder, "b", "b3")])
        self.assertTrue((self.folder / "b.safetensors").exists())

    def test_cancel_stops_between_entries(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b2")]

        class Control:
            def __init__(self):
                self.checks = 0

            def wait_if_paused(self):
                pass

            def is_cancelled(self):
                self.checks += 1
                return self.checks > 1

        result = self._run(rows, control=Control())
        self.assertEqual({"a2" + s for s in SIDECARS} | {"b" + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual([rows[0]["key"]], result["done"])
        self.assertIsNone(self.journal.pending())

    def _dup_pair(self, survivor_old, survivor_new, copy_old):
        survivor = _row(self.folder, survivor_old, survivor_new)
        dup = _row(self.folder, copy_old, copy_old, status="duplicate", survivorKey=survivor["key"])
        return survivor, dup

    def test_survivor_takes_the_name_its_removed_copy_held(self) -> None:
        canonical = "fotomax - nikora hyperreal - v1 (flux)"
        _make(self.folder, "fotomax - nikra hyperreal")
        _make(self.folder, canonical)
        _same_weights(self.folder, "fotomax - nikra hyperreal", canonical)
        survivor, dup = self._dup_pair("fotomax - nikra hyperreal", canonical, canonical)

        def fake_trash(path):
            os.remove(path)

        result = self._run([survivor, dup], selected={survivor["key"]}, remove={dup["key"]}, trash=fake_trash)
        self.assertEqual({canonical + s for s in SIDECARS}, _names(self.folder))
        self.assertEqual(
            "fotomax - nikra hyperreal.civitai.info",
            (self.folder / (canonical + ".civitai.info")).read_text(encoding="utf-8"),
        )
        self.assertEqual({survivor["key"], dup["key"]}, set(result["done"]))

    def test_survivor_is_not_renamed_onto_a_copy_that_could_not_be_removed(self) -> None:
        canonical = "fotomax - nikora hyperreal - v1 (flux)"
        _make(self.folder, "fotomax - nikra hyperreal")
        _make(self.folder, canonical)
        survivor, dup = self._dup_pair("fotomax - nikra hyperreal", canonical, canonical)

        def failing_trash(path):
            raise OSError("Recycle Bin unavailable")

        before = _names(self.folder)
        result = self._run([survivor, dup], selected={survivor["key"]}, remove={dup["key"]}, trash=failing_trash)
        self.assertEqual(before, _names(self.folder))
        self.assertEqual(canonical + ".safetensors", (self.folder / (canonical + ".safetensors")).read_text(encoding="utf-8"))
        self.assertEqual([], result["done"])
        self.assertEqual({survivor["key"], dup["key"]}, {s["entry"] for s in result["skipped"]})

    def test_duplicate_removal_spares_another_models_weights(self) -> None:
        _make(self.folder, "x")
        (self.folder / "x.ckpt").write_text("other model", encoding="utf-8")
        _make(self.folder, "y")
        _same_weights(self.folder, "x", "y")
        survivor, dup = self._dup_pair("y", "y2", "x")
        trashed = []
        self._run([survivor, dup], selected={survivor["key"]}, remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([str(self.folder / "x.safetensors")], trashed)

    def test_missing_send2trash_fails_only_the_removal(self) -> None:
        _make(self.folder, "copy")
        _make(self.folder, "keep")
        _same_weights(self.folder, "copy", "keep")
        survivor, dup = self._dup_pair("keep", "keep2", "copy")
        with mock.patch.dict("sys.modules", {"send2trash": None}):
            result = self._run(
                [survivor, dup], selected={survivor["key"]}, remove={dup["key"]}, trash=rename_apply.send_to_trash
            )
        self.assertEqual([survivor["key"]], result["done"])
        self.assertIn("send2trash", result["skipped"][0]["reason"])
        self.assertTrue((self.folder / "copy.safetensors").exists())

    def test_stranded_temp_file_keeps_the_journal(self) -> None:
        old, new = "BustlineXL - v1 (sdxl)", "bustlinexl - v1 (sdxl)"
        _make(self.folder, "good", (".safetensors", ".civitai.info"))
        _make(self.folder, old, (".safetensors",))
        real_rename = os.rename

        def fake_rename(src, dst):
            if ".cu-tmp-" in Path(src).name:
                raise PermissionError("scanner holds it")
            return real_rename(src, dst)

        rows = [_row(self.folder, "good", "good2"), _row(self.folder, old, new)]
        with mock.patch("os.rename", side_effect=fake_rename):
            result = self._run(rows)
        self.assertEqual([rows[0]["key"]], result["done"])
        (left,) = _names(self.folder) - {"good2.safetensors", "good2.civitai.info"}
        self.assertIn(".cu-tmp-", left)
        self.assertIn(left, result["skipped"][0]["reason"])
        pending = RenameJournal(self.data_dir).pending()
        self.assertIsNotNone(pending)
        # Only the stuck file stays journaled; the finished entry is out of recovery's reach.
        self.assertEqual({rows[1]["key"]}, {step["entry"] for step in pending["steps"]})

        recover(RenameJournal(self.data_dir), "undo")
        self.assertEqual({old + ".safetensors", "good2.safetensors", "good2.civitai.info"}, _names(self.folder))
        self.assertIsNone(RenameJournal(self.data_dir).pending())

    def _assert_copy_kept(
        self, rows, dup, copy_files, *, remove_before_plan=(), remove_before_apply=(), select_survivor=True
    ):
        survivor = rows[0]
        for name in remove_before_plan:
            os.remove(self.folder / name)
        steps = plan_steps(rows, {survivor["key"]} if select_survivor else set(), {dup["key"]})
        for name in remove_before_apply:
            os.remove(self.folder / name)
        trashed = []
        result = apply_steps(steps, self.journal, trash=trashed.append)
        self.assertEqual([], trashed)
        reasons = {s["entry"]: s["reason"] for s in result["skipped"]}
        self.assertEqual("the copy that was to be kept is gone", reasons[dup["key"]])
        for name in copy_files:
            self.assertTrue((self.folder / name).exists(), name)

    def test_survivor_info_does_not_stand_in_for_its_missing_weights(self) -> None:
        for when in ("remove_before_plan", "remove_before_apply"):
            with self.subTest(when=when):
                for name in os.listdir(self.folder):
                    os.remove(self.folder / name)
                _make(self.folder, "copy")
                _make(self.folder, "keep")
                survivor, dup = self._dup_pair("keep", "keep2", "copy")
                self.journal.clear()
                self._assert_copy_kept(
                    [survivor, dup], dup, ["copy" + s for s in SIDECARS], **{when: ["keep.safetensors"]}
                )

    def test_shared_stem_copy_never_confirms_itself(self) -> None:
        for when in ("remove_before_plan", "remove_before_apply"):
            with self.subTest(when=when):
                for name in os.listdir(self.folder):
                    os.remove(self.folder / name)
                _make(self.folder, "keep")
                (self.folder / "keep.ckpt").write_text("copy", encoding="utf-8")
                survivor = _row(self.folder, "keep", "keep2")
                dup = {
                    "key": str(self.folder / "keep.ckpt").lower(),
                    "folder": str(self.folder),
                    "oldStem": "keep",
                    "newStem": "keep",
                    "status": "duplicate",
                    "survivorKey": survivor["key"],
                }
                self.journal.clear()
                # Survivor not selected for renaming, as in the reported case.
                self._assert_copy_kept(
                    [survivor, dup], dup, ["keep.ckpt"], select_survivor=False, **{when: ["keep.safetensors"]}
                )

    def test_duplicate_without_its_survivor_row_is_reported(self) -> None:
        _make(self.folder, "copy")
        dup = _row(self.folder, "copy", "copy", status="duplicate", survivorKey="nowhere")
        trashed = []
        result = apply_steps(plan_steps([dup], set(), {dup["key"]}), self.journal, trash=trashed.append)
        self.assertEqual([], trashed)
        self.assertEqual([dup["key"]], [s["entry"] for s in result["skipped"]])
        self.assertEqual("the copy that was to be kept is gone", result["skipped"][0]["reason"])

    def test_copy_is_kept_when_its_survivor_is_gone(self) -> None:
        _make(self.folder, "copy")
        _make(self.folder, "keep")
        survivor, dup = self._dup_pair("keep", "keep2", "copy")
        steps = plan_steps([survivor, dup], {survivor["key"]}, {dup["key"]})
        for name in os.listdir(self.folder):
            if name.startswith("keep."):
                os.remove(self.folder / name)
        trashed = []
        result = apply_steps(steps, self.journal, trash=trashed.append)
        self.assertEqual([], trashed)
        reasons = {s["entry"]: s["reason"] for s in result["skipped"]}
        self.assertEqual("the copy that was to be kept is gone", reasons[dup["key"]])
        self.assertEqual({"copy" + s for s in SIDECARS}, _names(self.folder))

    def test_duplicate_removal_trashes_each_sibling(self) -> None:
        _make(self.folder, "fotomax - nikra hyperreal")
        _make(self.folder, "fotomax - nikora hyperreal")
        _same_weights(self.folder, "fotomax - nikra hyperreal", "fotomax - nikora hyperreal")
        survivor = _row(self.folder, "fotomax - nikora hyperreal", "fotomax - nikora hyperreal - v1 (flux)")
        dup = _row(
            self.folder,
            "fotomax - nikra hyperreal",
            "fotomax - nikra hyperreal",
            status="duplicate",
            survivorKey=survivor["key"],
        )
        trashed = []
        result = self._run([survivor, dup], selected={survivor["key"]}, remove={dup["key"]}, trash=trashed.append)
        self.assertEqual(
            sorted(str(self.folder / ("fotomax - nikra hyperreal" + s)) for s in SIDECARS), sorted(trashed)
        )
        self.assertIn(dup["key"], result["done"])
        self.assertIn(survivor["key"], result["done"])

    def test_unselected_and_unchanged_rows_have_no_steps(self) -> None:
        _make(self.folder, "a")
        _make(self.folder, "b")
        rows = [_row(self.folder, "a", "a2"), _row(self.folder, "b", "b", status="unchanged")]
        self.assertEqual([], plan_steps(rows, set(), set()))
        self.assertEqual([], plan_steps(rows, {rows[1]["key"]}, set()))

    # --- removing a copy only when it is byte-identical ------------------

    def _copy_and_survivor(self, copy_weights: bytes, keep_weights: bytes, copy_suffixes=SIDECARS):
        _make(self.folder, "copy", copy_suffixes)
        _make(self.folder, "keep")
        if ".safetensors" in copy_suffixes:
            (self.folder / "copy.safetensors").write_bytes(copy_weights)
        (self.folder / "keep.safetensors").write_bytes(keep_weights)
        survivor = _row(self.folder, "keep", "keep", status="unchanged")
        dup = _row(self.folder, "copy", "copy", status="duplicate", survivorKey=survivor["key"])
        return survivor, dup

    def test_identical_copy_is_removed(self) -> None:
        survivor, dup = self._copy_and_survivor(b"same weights", b"same weights")
        trashed = []
        result = self._run([survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([dup["key"]], result["done"])
        self.assertEqual(sorted(str(self.folder / ("copy" + s)) for s in SIDECARS), sorted(trashed))

    def test_copy_of_another_size_is_not_removed_and_not_hashed(self) -> None:
        survivor, dup = self._copy_and_survivor(b"short", b"much longer weights")
        trashed = []
        with mock.patch.object(rename_apply, "sha256_file", side_effect=AssertionError("hashed")):
            result = self._run([survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([], trashed)
        self.assertEqual([], result["done"])
        self.assertEqual(rename_apply.COPY_DIFFERS, result["skipped"][0]["reason"])

    def test_copy_with_other_bytes_of_the_same_size_is_not_removed(self) -> None:
        survivor, dup = self._copy_and_survivor(b"weights-AAAA", b"weights-BBBB")
        trashed = []
        result = self._run([survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([], trashed)
        self.assertEqual(rename_apply.COPY_DIFFERS, result["skipped"][0]["reason"])
        self.assertTrue((self.folder / "copy.safetensors").exists())

    def test_unreadable_survivor_refuses_the_removal(self) -> None:
        survivor, dup = self._copy_and_survivor(b"same weights", b"same weights")
        real = rename_apply.sha256_file

        def unreadable(path, **kwargs):
            if Path(path).name == "keep.safetensors":
                raise PermissionError("in use")
            return real(path, **kwargs)

        trashed = []
        with mock.patch.object(rename_apply, "sha256_file", side_effect=unreadable):
            result = self._run([survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([], trashed)
        self.assertIn("could not be compared", result["skipped"][0]["reason"])

    def test_metadata_only_copy_is_removed_without_hashing(self) -> None:
        survivor, dup = self._copy_and_survivor(b"", b"weights", copy_suffixes=(".civitai.info", ".preview.png"))
        dup["key"] = str(self.folder / "copy.civitai.info").lower()
        trashed = []
        with mock.patch.object(rename_apply, "sha256_file", side_effect=AssertionError("hashed")):
            result = self._run([survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append)
        self.assertEqual([dup["key"]], result["done"])
        self.assertEqual(2, len(trashed))

    def test_stop_while_comparing_keeps_the_copy(self) -> None:
        survivor, dup = self._copy_and_survivor(b"same weights", b"same weights")

        class StopDuringHash:
            def __init__(self):
                self.calls = 0

            def wait_if_paused(self):
                pass

            def is_cancelled(self):
                # The check before the entry passes; the hash then sees the stop.
                self.calls += 1
                return self.calls > 1

        trashed = []
        result = self._run(
            [survivor, dup], selected=set(), remove={dup["key"]}, trash=trashed.append, control=StopDuringHash()
        )
        self.assertEqual([], trashed)
        self.assertEqual(rename_apply.COMPARE_STOPPED, result["skipped"][0]["reason"])

    def test_finish_compares_before_removing(self) -> None:
        survivor, dup = self._copy_and_survivor(b"weights-AAAA", b"weights-BBBB")
        steps = plan_steps([survivor, dup], set(), {dup["key"]})
        self.journal.begin(steps)
        trashed = []
        result = recover(RenameJournal(self.data_dir), "finish", trash=trashed.append)
        self.assertEqual([], trashed)
        self.assertEqual(rename_apply.COPY_DIFFERS, result["skipped"][0]["reason"])

    # --- identity at plan time (stale dry runs) --------------------------

    def test_own_file_renamed_outside_the_panel_skips_the_entry(self) -> None:
        _make(self.folder, "a")
        row = _row(self.folder, "a", "b", modelPath=str(self.folder / "a.safetensors"))
        os.rename(self.folder / "a.safetensors", self.folder / "elsewhere.safetensors")
        steps, skipped = plan([row], {row["key"]}, set())
        self.assertEqual([], steps)
        self.assertEqual([row["key"]], [s["entry"] for s in skipped])
        self.assertEqual(rename_apply.OWN_FILE_GONE, skipped[0]["reason"])

    def test_changed_size_since_the_dry_run_skips_the_entry(self) -> None:
        _make(self.folder, "a")
        row = _row(self.folder, "a", "b", modelPath=str(self.folder / "a.safetensors"))
        row.update(file_identity(self.folder / "a.safetensors"))
        (self.folder / "a.safetensors").write_text("a different model", encoding="utf-8")
        steps, skipped = plan([row], {row["key"]}, set())
        self.assertEqual([], steps)
        self.assertEqual(rename_apply.CHANGED_SINCE_DRY_RUN, skipped[0]["reason"])

    def test_another_model_moved_into_the_old_name_is_not_renamed(self) -> None:
        # R renamed away by one apply, P renamed into R's old name by another:
        # the old dry run's R row now names P's files.
        _make(self.folder, "r")
        r_row = _row(self.folder, "r", "r-new", modelPath=str(self.folder / "r.safetensors"))
        r_row.update(file_identity(self.folder / "r.safetensors"))
        for suffix in SIDECARS:
            os.rename(self.folder / ("r" + suffix), self.folder / ("r-new" + suffix))
        _make(self.folder, "p")
        old = os.stat(self.folder / "p.safetensors")
        os.utime(self.folder / "p.safetensors", ns=(old.st_atime_ns, old.st_mtime_ns + 5_000_000_000))
        for suffix in SIDECARS:
            os.rename(self.folder / ("p" + suffix), self.folder / ("r" + suffix))
        # Same size on purpose ("p.safetensors" vs "r.safetensors"): only the time tells them apart.
        self.assertEqual(r_row["fileSize"], os.stat(self.folder / "r.safetensors").st_size)
        before = _names(self.folder)
        result = self._run([r_row])
        self.assertEqual(before, _names(self.folder))
        self.assertEqual([], result["done"])
        self.assertEqual(rename_apply.CHANGED_SINCE_DRY_RUN, result["skipped"][0]["reason"])

    def test_unchanged_file_with_identity_is_renamed(self) -> None:
        _make(self.folder, "a")
        row = _row(self.folder, "a", "b", modelPath=str(self.folder / "a.safetensors"))
        row.update(file_identity(self.folder / "a.safetensors"))
        result = self._run([row])
        self.assertEqual([row["key"]], result["done"])
        self.assertEqual({"b" + s for s in SIDECARS}, _names(self.folder))

    def test_selected_key_without_steps_is_reported(self) -> None:
        gone = self.folder / "gone"
        row = _row(gone, "a", "b")
        steps, skipped = plan([row], {row["key"], "not-a-row"}, set())
        self.assertEqual([], steps)
        reasons = {s["entry"]: s["reason"] for s in skipped}
        self.assertEqual(rename_apply.OWN_FILE_GONE, reasons[row["key"]])
        self.assertEqual(rename_apply.NOT_A_RENAME, reasons["not-a-row"])

    def test_chosen_duplicate_with_nothing_left_is_reported(self) -> None:
        _make(self.folder, "keep")
        survivor = _row(self.folder, "keep", "keep2")
        dup = _row(self.folder, "copy", "copy", status="duplicate", survivorKey=survivor["key"])
        steps, skipped = plan([survivor, dup], set(), {dup["key"]})
        self.assertEqual([], steps)
        self.assertEqual([(dup["key"], rename_apply.OWN_FILE_GONE)], [(s["entry"], s["reason"]) for s in skipped])

    def test_shared_stem_copy_renamed_with_its_survivor_is_reported(self) -> None:
        _make(self.folder, "keep")
        (self.folder / "keep.ckpt").write_text("copy", encoding="utf-8")
        survivor = _row(self.folder, "keep", "keep2")
        dup = {
            "key": str(self.folder / "keep.ckpt").lower(),
            "folder": str(self.folder),
            "oldStem": "keep",
            "newStem": "keep",
            "status": "duplicate",
            "survivorKey": survivor["key"],
        }
        steps, skipped = plan([survivor, dup], {survivor["key"]}, {dup["key"]})
        self.assertEqual({"rename"}, {s["kind"] for s in steps})
        self.assertEqual([(dup["key"], rename_apply.SHARES_SURVIVOR_NAME)], [(s["entry"], s["reason"]) for s in skipped])
        result = self._run([survivor, dup], selected={survivor["key"]}, remove={dup["key"]})
        self.assertIn(dup["key"], [s["entry"] for s in result["skipped"]])

    def test_update_saved_paths(self) -> None:
        old = str(self.folder / "Old.safetensors")
        new = str(self.folder / "new.safetensors")
        payload = {
            "items": [
                {
                    "modelPath": old.lower(),
                    "modelName": old,
                    "localVersions": [{"filePath": old, "otherPath": "elsewhere"}],
                }
            ]
        }
        (self.data_dir / "last_check.json").write_text(json.dumps(payload), encoding="utf-8")
        update_saved_paths(self.data_dir, {old: new})
        saved = json.loads((self.data_dir / "last_check.json").read_text(encoding="utf-8"))
        item = saved["items"][0]
        self.assertEqual(new, item["modelPath"])
        self.assertEqual(new, item["localVersions"][0]["filePath"])
        self.assertEqual(old, item["modelName"])
        self.assertEqual("elsewhere", item["localVersions"][0]["otherPath"])
        self.assertFalse((self.data_dir / "progress.json").exists())

    def test_refresh_sidecar_title(self) -> None:
        info = self.folder / "a.civitai.info"
        info.write_text(json.dumps({"id": 1, "model": {"name": "old", "type": "LORA"}}), encoding="utf-8")
        refresh_sidecar_title(info, "New Title")
        saved = json.loads(info.read_text(encoding="utf-8"))
        self.assertEqual({"name": "New Title", "type": "LORA"}, saved["model"])
        self.assertEqual(1, saved["id"])


if __name__ == "__main__":
    unittest.main()
