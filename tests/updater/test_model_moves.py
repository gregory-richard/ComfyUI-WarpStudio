"""Archive and Unarchive: a version's files are copied, checked and only then sent to the Recycle Bin."""

from __future__ import annotations

import asyncio
import copy
import errno
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from comfy_updater import model_moves
from comfy_updater import routes as routes_module
from comfy_updater.archive import ARCHIVE_RESULTS_FILENAME, save_archive_results
from comfy_updater.config_store import ConfigStore
from comfy_updater.constants import CACHE_SCHEMA_VERSION
from comfy_updater.download_queue import MODEL_DOWNLOAD_JOB, DownloadRequest
from comfy_updater.jobs import JobManager, move_key
from comfy_updater.model_download import DownloadError, DownloadStopped
from comfy_updater.model_moves import (
    ARCHIVE_CHANGED_ERROR,
    BOTH_COPIES_WARNING,
    HASH_MISMATCH_ERROR,
    INFO_ONLY_MOVE_ERROR,
    MOVE_BUSY_ERROR,
    MOVE_SOURCES,
    NAME_TAKEN_ERROR,
    NO_ARCHIVE_FOLDER_ERROR,
    NO_DRIVE_FOLDER_ERROR,
    NO_TRASH_MOVE_ERROR,
    NOT_IN_ARCHIVE_ERROR,
    NOT_IN_TYPE_FOLDER_ERROR,
    NOT_ON_DRIVE_ERROR,
    SIDECAR_COPY_WARNING,
    UNIDENTIFIED_MOVE_ERROR,
    MoveContext,
    archive_target,
    copy_verified,
    info_hashes,
    move_one,
    moved_item,
    unarchive_target,
)
from comfy_updater.rename_apply import send_to_trash
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore, RenameJournal
from comfy_updater.routes import (
    ARCHIVE_OVERLAP_ERROR,
    ARCHIVE_UNREACHABLE_ERROR,
    FILE_GONE_ERROR,
    JOURNAL_PENDING_ERROR,
    UNLISTED_FILE_ERROR,
    _register_model_action_routes,
    recheck,
)
from comfy_updater.sidecar import read_data_file, write_json

from test_archive_routes import _join_job_threads, _Request, _Routes, _strings

BODY = bytes(range(256)) * 40  # 10 KB of weights
HASH = hashlib.sha256(BODY).hexdigest()
PNG = b"\x89PNG\r\n\x1a\nfake"
LOCKED = "The process cannot access the file because it is being used by another process"
DROPPED = "The specified network name is no longer available"
MOVES_ROUTE = "/warp-studio/updater/moves"


def info_payload(sha=HASH) -> dict:
    file = {"name": "x.safetensors", "primary": True, "type": "Model"}
    if sha is not None:
        file["hashes"] = {"SHA256": sha.upper(), "AutoV2": "ABCDEF0123"}
    return {"id": 201, "modelId": 101, "name": "v1", "baseModel": "Flux.1 D", "files": [file]}


class _BrokenWriter:
    """A file opened for writing whose writes fail after ``after`` of them, as a drive that drops out."""

    def __init__(self, handle, after: int) -> None:
        self._handle = handle
        self._left = after

    def write(self, data):
        if self._left <= 0:
            raise OSError(errno.EIO, DROPPED)
        self._left -= 1
        return self._handle.write(data)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self._handle.close()
        return False


def _breaking_open(predicate, after: int = 1):
    """Patch model_moves' ``open`` so a file written there that ``predicate`` names breaks."""
    real_open = open

    def fake_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if ("w" in mode or "x" in mode) and predicate(Path(file)):
            return _BrokenWriter(handle, after)
        return handle

    return mock.patch("comfy_updater.model_moves.open", fake_open, create=True)


class _MoveCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.d = self.tmp / "D" / "Lora"
        self.d.mkdir(parents=True)
        self.dk = self.d / "krea2"
        self.z = self.tmp / "Z" / "models"
        self.z.mkdir(parents=True)
        self.zk = self.z / "Lora" / "krea2"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.trashed: list = []
        self.trash_paths: list = []
        self.jobs = JobManager()
        self.ctx = MoveContext(self.jobs, self.data, str(self.z), trash=self.trash)

    # --- files and records ---

    def trash(self, path: str) -> None:
        self.trash_paths.append(path)
        self.trashed.append(Path(path).name)
        os.replace(path, self.bin / Path(path).name)

    def lay(self, folder: Path, info=None) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        weights = folder / "x.safetensors"
        weights.write_bytes(BODY)
        (folder / "x.civitai.info").write_text(json.dumps(info or info_payload()), encoding="utf-8")
        (folder / "x.preview.png").write_bytes(PNG)
        (folder / "x.json").write_text('{"activation text": "x"}', encoding="utf-8")
        return weights

    def d_item(self, **fields) -> dict:
        weights = str(self.dk / "x.safetensors")
        item = {
            "modelPath": weights,
            "filePath": weights,
            "modelType": "lora",
            "metadataOnly": False,
            "modelId": "101",
            "modelName": "X",
            "localVersionId": "201",
            "localVersionName": "v1",
            "baseModel": "Flux.1 D",
            "status": "ok",
            "hasLocalPreview": True,
            "remoteVersions": [
                {"versionId": "202", "versionName": "v2", "versionDate": "2026-03-01T00:00:00.000Z"},
                {"versionId": "201", "versionName": "v1", "versionDate": "2026-01-01T00:00:00.000Z"},
            ],
        }
        item.update(fields)
        return item

    def z_item(self, folder: Path = None, **fields) -> dict:
        folder = folder or self.zk
        item = self.d_item(
            modelPath=str(folder / "x.civitai.info"),
            filePath=str(folder / "x.safetensors"),
            metadataOnly=True,
            archived=True,
            hasLocalPreview=False,
        )
        item.update(fields)
        return item

    def other_z_item(self) -> dict:
        return self.d_item(
            modelPath=str(self.z / "Lora" / "sdxl" / "old.civitai.info"),
            filePath=str(self.z / "Lora" / "sdxl" / "old.safetensors"),
            modelId="301",
            localVersionId="401",
            metadataOnly=True,
            archived=True,
        )

    def hold(self, d_items: list, z_items: list) -> None:
        """A restored check job of both drives, last_check.json of D and archive_check.json of Z."""
        self.jobs.restore_job(
            "check-updates",
            "check-1",
            startedAt="",
            finishedAt="",
            summary={},
            items=copy.deepcopy(d_items + z_items),
        )
        write_json(
            self.data / "last_check.json",
            {
                "schemaVersion": CACHE_SCHEMA_VERSION,
                "checkedAt": "2026-11-25T10:00:00+00:00",
                "summary": {"mode": "check", "total": len(d_items)},
                "items": copy.deepcopy(d_items),
            },
        )
        save_archive_results(
            self.data, str(self.z), {"mode": "archive", "total": len(z_items)}, copy.deepcopy(z_items)
        )

    def job_items(self) -> list:
        return self.jobs.get("check-1").items

    def last_check_items(self) -> list:
        return read_data_file(self.data / "last_check.json")["items"]

    def archive_items(self) -> list:
        return read_data_file(self.data / ARCHIVE_RESULTS_FILENAME)["items"]

    @staticmethod
    def by_path(items: list, path: Path) -> dict:
        found = [item for item in items if item.get("modelPath") == str(path)]
        assert len(found) == 1, found
        return found[0]

    def everything(self) -> dict:
        """Every file and folder under the temp folder, with each file's bytes."""
        return {
            str(path.relative_to(self.tmp)): (path.read_bytes() if path.is_file() else None)
            for path in sorted(self.tmp.rglob("*"))
        }

    # --- requests ---

    def archive_request(self, item: dict = None) -> DownloadRequest:
        item = item or self.d_item()
        return DownloadRequest(
            key=move_key(item["modelPath"]),
            source="archive",
            model_id="101",
            version_id="201",
            model_type="lora",
            move_from=item["filePath"],
            move_to=str(self.zk / "x.safetensors"),
            check_item=copy.deepcopy(item),
        )

    def unarchive_request(self, item: dict = None, target: Path = None) -> DownloadRequest:
        item = item or self.z_item()
        return DownloadRequest(
            key=move_key(item["modelPath"]),
            source="unarchive",
            model_id="101",
            version_id="201",
            model_type="lora",
            move_from=item["filePath"],
            move_to=str(target or self.dk / "x.safetensors"),
            check_item=copy.deepcopy(item),
        )

    def move(self, request: DownloadRequest, should_stop=lambda: False) -> list:
        reports: list = []
        move_one(
            request,
            report=lambda written, total, message: reports.append((written, total, message)),
            should_stop=should_stop,
            ctx=self.ctx,
        )
        return reports

    def assert_untouched(self, before: dict, weights: Path) -> None:
        self.assertEqual(before, self.everything())
        self.assertEqual(BODY, weights.read_bytes())
        self.assertEqual([], self.trashed)
        self.assertEqual([], list(self.tmp.rglob("*.part")))


class TargetTests(_MoveCase):
    def test_archive_target_keeps_the_subfolders_under_the_type_folder(self) -> None:
        d, z = self.d, self.z
        self.assertEqual(
            z / "Lora" / "krea2" / "x.safetensors",
            archive_target(d / "krea2" / "x.safetensors", z, [d]),
        )
        self.assertEqual(z / "Lora" / "x.safetensors", archive_target(d / "x.safetensors", z, [d]))

    def test_archive_target_uses_the_archives_own_spelling_of_the_type_folder(self) -> None:
        (self.z / "lora").mkdir()
        target = archive_target(self.dk / "x.safetensors", self.z, [self.d])
        self.assertEqual("lora", target.parent.parent.name)
        self.assertEqual(self.z / "lora" / "krea2" / "x.safetensors", target)

    def test_archive_target_takes_the_deepest_model_folder(self) -> None:
        inner = self.d / "Flux"
        target = archive_target(inner / "moss" / "x.safetensors", self.z, [self.d, inner])
        self.assertEqual(self.z / "Flux" / "moss" / "x.safetensors", target)

    def test_archive_target_is_none_outside_every_model_folder(self) -> None:
        self.assertIsNone(archive_target(self.tmp / "elsewhere" / "x.safetensors", self.z, [self.d]))
        self.assertIsNone(archive_target(self.dk / "x.safetensors", self.z, []))
        # A folder whose name only starts like the root's is not inside it.
        self.assertIsNone(
            archive_target(self.tmp / "D" / "Lora2" / "x.safetensors", self.z, [self.d])
        )

    def test_unarchive_target_keeps_the_subfolders_even_when_missing_on_this_drive(self) -> None:
        self.assertFalse(self.dk.exists())
        self.assertEqual(
            self.dk / "moss" / "x.safetensors",
            unarchive_target(self.zk / "moss" / "x.safetensors", self.z, [self.d]),
        )
        # The archive's own spelling of the type folder still finds the root.
        self.assertEqual(
            self.d / "x.safetensors",
            unarchive_target(self.z / "LORA" / "x.safetensors", self.z, [self.d]),
        )

    def test_unarchive_target_is_none_for_a_type_folder_this_drive_lacks(self) -> None:
        z = self.z
        self.assertIsNone(unarchive_target(z / "Embeddings" / "x.safetensors", z, [self.d]))
        self.assertIsNone(unarchive_target(self.tmp / "elsewhere" / "x.safetensors", z, [self.d]))
        self.assertIsNone(unarchive_target(z / "x.safetensors", z, [self.d]))
        self.assertIsNone(unarchive_target(Path(str(z) + "/Lora/../../x.safetensors"), z, [self.d]))

    def test_info_hashes_reads_every_files_sha256(self) -> None:
        info = self.tmp / "x.civitai.info"
        payload = info_payload()
        payload["files"].append({"name": "x-fp16.safetensors", "hashes": {"SHA256": " ABC "}})
        payload["files"].append({"name": "x.yaml"})
        info.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual({HASH, "abc"}, info_hashes(info))
        self.assertEqual(set(), info_hashes(None))
        self.assertEqual(set(), info_hashes(self.tmp / "missing.civitai.info"))

    def test_the_moves_speak_the_routes_words(self) -> None:
        self.assertEqual(("archive", "unarchive"), MOVE_SOURCES)
        self.assertEqual(routes_module.FILE_GONE_ERROR, model_moves.FILE_GONE_ERROR)
        self.assertEqual(
            routes_module.ARCHIVE_UNREACHABLE_ERROR, model_moves.ARCHIVE_UNREACHABLE_ERROR
        )


class MoveTests(_MoveCase):
    def test_archive_copies_checks_and_then_bins_the_originals(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [self.other_z_item()])
        request = self.archive_request()

        self.move(request)

        zk = self.zk
        self.assertEqual(BODY, (zk / "x.safetensors").read_bytes())
        self.assertEqual(PNG, (zk / "x.preview.png").read_bytes())
        self.assertEqual(json.dumps(info_payload()), (zk / "x.civitai.info").read_text("utf-8"))
        self.assertTrue((zk / "x.json").is_file())
        self.assertEqual(["x.safetensors", "x.civitai.info", "x.json", "x.preview.png"], self.trashed)
        self.assertFalse(weights.exists())
        self.assertEqual([], list(self.tmp.rglob("*.part")))
        self.assertEqual([], request.warnings)
        self.assertEqual("x.safetensors", request.file_name)

        for label, items in (("job", self.job_items()), ("last check", self.last_check_items())):
            with self.subTest(label):
                gone = self.by_path(items, weights)
                self.assertIs(True, gone["deleted"])
                self.assertIs(True, gone["moved"])
        self.assertEqual([], [item for item in self.last_check_items() if item.get("archived")])
        for label, items in (("job", self.job_items()), ("archive check", self.archive_items())):
            with self.subTest(label):
                here = self.by_path(items, zk / "x.civitai.info")
                self.assertIs(True, here["archived"])
                self.assertIs(True, here["metadataOnly"])
                self.assertEqual(str(zk / "x.safetensors"), here["filePath"])
                self.assertIs(False, here["hasLocalPreview"])
                self.assertTrue(here["movedAt"])
                self.assertNotIn("deleted", here)
                self.assertNotIn("moved", here)
                self.assertEqual("201", here["localVersionId"])
                self.assertEqual(self.d_item()["remoteVersions"], here["remoteVersions"])
        # The archive's other results stay.
        self.by_path(self.archive_items(), self.z / "Lora" / "sdxl" / "old.civitai.info")

    def test_unarchive_brings_a_version_back_to_its_folder(self) -> None:
        source = self.lay(self.zk)
        self.hold([], [self.z_item(), self.other_z_item()])
        self.assertFalse(self.dk.exists())

        self.move(self.unarchive_request())

        dk = self.dk
        self.assertEqual(BODY, (dk / "x.safetensors").read_bytes())
        for name in ("x.civitai.info", "x.preview.png", "x.json"):
            self.assertTrue((dk / name).is_file(), name)
        self.assertFalse(source.exists())
        self.assertEqual(["x.safetensors", "x.civitai.info", "x.json", "x.preview.png"], self.trashed)
        for label, items in (("job", self.job_items()), ("last check", self.last_check_items())):
            with self.subTest(label):
                here = self.by_path(items, dk / "x.safetensors")
                self.assertNotIn("archived", here)
                self.assertIs(False, here["metadataOnly"])
                self.assertEqual(str(dk / "x.safetensors"), here["filePath"])
                self.assertIs(True, here["hasLocalPreview"])
                self.assertTrue(here["movedAt"])
        for label, items in (("job", self.job_items()), ("archive check", self.archive_items())):
            with self.subTest(label):
                gone = self.by_path(items, self.zk / "x.civitai.info")
                self.assertIs(True, gone["deleted"])
                self.assertIs(True, gone["moved"])

    def test_a_taken_name_refuses_the_move_and_touches_nothing(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        self.zk.mkdir(parents=True)
        (self.zk / "x.civitai.info").write_text("theirs", encoding="utf-8")
        before = self.everything()

        with self.assertRaises(DownloadError) as caught:
            self.move(self.archive_request())

        self.assertEqual(NAME_TAKEN_ERROR.format(name="x.civitai.info"), str(caught.exception))
        self.assert_untouched(before, weights)

    def test_a_taken_name_is_found_whatever_its_case(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        self.zk.mkdir(parents=True)
        (self.zk / "X.JSON").write_text("theirs", encoding="utf-8")
        before = self.everything()

        with self.assertRaises(DownloadError) as caught:
            self.move(self.archive_request())

        self.assertEqual(NAME_TAKEN_ERROR.format(name="x.json"), str(caught.exception))
        self.assert_untouched(before, weights)

    def test_a_copy_that_does_not_match_the_info_hash_is_removed(self) -> None:
        weights = self.lay(self.dk, info_payload(sha="0" * 64))
        self.hold([self.d_item()], [])
        before = self.everything()

        with self.assertRaises(DownloadError) as caught:
            self.move(self.archive_request())

        self.assertEqual(HASH_MISMATCH_ERROR, str(caught.exception))
        self.assertFalse((self.z / "Lora" / "krea2").exists())
        self.assertFalse((self.z / "Lora").exists())  # the type folder the move made goes too
        self.assert_untouched(before, weights)

    def test_without_a_stored_hash_the_copy_is_checked_against_the_source(self) -> None:
        weights = self.lay(self.dk, info_payload(sha=None))
        self.hold([self.d_item()], [])

        self.move(self.archive_request())

        self.assertEqual(BODY, (self.zk / "x.safetensors").read_bytes())
        self.assertFalse(weights.exists())

    def test_a_copy_that_reads_back_differently_is_removed(self) -> None:
        for label, info in (("stored hash", info_payload()), ("no stored hash", info_payload(sha=None))):
            with self.subTest(label):
                weights = self.lay(self.dk, info)
                self.hold([self.d_item()], [])
                before = self.everything()
                with mock.patch.object(model_moves, "sha256_file", return_value="f" * 64):
                    with self.assertRaises(DownloadError) as caught:
                        self.move(self.archive_request())
                self.assertEqual(HASH_MISMATCH_ERROR, str(caught.exception))
                self.assert_untouched(before, weights)

    def test_stop_during_the_copy_leaves_the_original_and_no_part(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        before = self.everything()
        asked = []

        def should_stop() -> bool:
            asked.append(1)
            return len(asked) > 3

        reports = []
        with mock.patch.object(model_moves, "COPY_CHUNK", 1024):
            with self.assertRaises(DownloadStopped):
                move_one(
                    self.archive_request(),
                    report=lambda *args: reports.append(args),
                    should_stop=should_stop,
                    ctx=self.ctx,
                )

        # It stopped partway through the copy, before any check.
        self.assertTrue(reports)
        self.assertLess(reports[-1][0], len(BODY))
        self.assertTrue(all(message.startswith("Copying") for _w, _t, message in reports))
        self.assertFalse((self.z / "Lora").exists())
        self.assert_untouched(before, weights)

    def test_stop_while_checking_the_copy_leaves_the_original_and_no_part(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        before = self.everything()
        checking = []

        def report(written, total, message):
            if message.startswith("Checking"):
                checking.append(message)

        with self.assertRaises(DownloadStopped):
            move_one(
                self.archive_request(), report=report, should_stop=lambda: bool(checking), ctx=self.ctx
            )

        self.assertEqual(["Checking x.safetensors"], checking)
        self.assert_untouched(before, weights)

    def test_a_copy_that_fails_midway_leaves_the_original_and_no_part(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        before = self.everything()

        with mock.patch.object(model_moves, "COPY_CHUNK", 1024):
            with _breaking_open(lambda path: path.name.endswith(".part"), after=1):
                with self.assertRaises(DownloadError) as caught:
                    self.move(self.archive_request())

        self.assertTrue(str(caught.exception).startswith("Could not copy x.safetensors:"))
        self.assertIn(DROPPED, str(caught.exception))
        self.assertNotIn(str(self.tmp), str(caught.exception))
        self.assertFalse((self.z / "Lora").exists())
        self.assert_untouched(before, weights)

    def test_a_copy_never_overwrites_a_file_or_a_part_left_behind(self) -> None:
        weights = self.lay(self.dk)
        self.zk.mkdir(parents=True)
        for name in ("x.safetensors", "x.safetensors.part"):
            with self.subTest(name):
                there = self.zk / name
                there.write_bytes(b"theirs")
                with self.assertRaises(DownloadError) as caught:
                    copy_verified(
                        weights,
                        self.zk / "x.safetensors",
                        {HASH},
                        report=lambda *args: None,
                        should_stop=lambda: False,
                    )
                self.assertEqual(NAME_TAKEN_ERROR.format(name=name), str(caught.exception))
                self.assertEqual(b"theirs", there.read_bytes())
                there.unlink()
        self.assertEqual(BODY, weights.read_bytes())

    def test_a_refused_original_keeps_both_copies_and_says_so(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        attempts = []

        def locked(path: str) -> None:
            attempts.append(Path(path).name)
            raise PermissionError(errno.EACCES, LOCKED)

        self.ctx.trash = locked
        request = self.archive_request()

        self.move(request)

        self.assertTrue(
            request.warnings[0].startswith(
                "Both copies are kept: x.safetensors could not go to the Recycle Bin"
            ),
            request.warnings,
        )
        self.assertEqual(
            [BOTH_COPIES_WARNING.format(name="x.safetensors", reason=LOCKED)], request.warnings
        )
        self.assertEqual(["x.safetensors"], attempts)  # the sidecars were never tried
        for name in ("x.safetensors", "x.civitai.info", "x.preview.png", "x.json"):
            self.assertTrue((self.dk / name).is_file(), name)
            self.assertTrue((self.zk / name).is_file(), name)
        self.assertEqual(BODY, weights.read_bytes())
        for label, items in (("job", self.job_items()), ("last check", self.last_check_items())):
            with self.subTest(label):
                self.assertNotIn("deleted", self.by_path(items, weights))
        for label, items in (("job", self.job_items()), ("archive check", self.archive_items())):
            with self.subTest(label):
                self.assertTrue(self.by_path(items, self.zk / "x.civitai.info")["archived"])

    def test_weights_the_recycle_bin_left_in_place_keep_both_copies(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        self.ctx.trash = lambda path: None
        request = self.archive_request()

        self.move(request)

        self.assertEqual(
            [BOTH_COPIES_WARNING.format(name="x.safetensors", reason="it is still there")],
            request.warnings,
        )
        self.assertTrue((self.dk / "x.civitai.info").is_file())
        self.assertNotIn("deleted", self.by_path(self.job_items(), weights))

    def test_a_sidecar_that_cannot_be_copied_keeps_both_copies(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        request = self.archive_request()

        with _breaking_open(lambda path: path.name == "x.json", after=0):
            self.move(request)

        self.assertEqual(
            [SIDECAR_COPY_WARNING.format(name="x.json", reason=DROPPED)], request.warnings
        )
        self.assertTrue(request.warnings[0].startswith("Both copies are kept: x.json could not be copied"))
        self.assertEqual([], self.trashed)
        for name in ("x.safetensors", "x.civitai.info", "x.preview.png", "x.json"):
            self.assertTrue((self.dk / name).is_file(), name)
        for name in ("x.safetensors", "x.civitai.info", "x.preview.png"):
            self.assertTrue((self.zk / name).is_file(), name)
        self.assertFalse((self.zk / "x.json").exists())  # its half copy is not left behind
        self.assertNotIn("deleted", self.by_path(self.job_items(), weights))
        self.assertTrue(self.by_path(self.archive_items(), self.zk / "x.civitai.info")["archived"])

    def test_the_ring_fills_while_copying_and_spins_while_checking(self) -> None:
        self.lay(self.dk)
        self.hold([self.d_item()], [])

        with mock.patch.object(model_moves, "COPY_CHUNK", 4096):
            reports = self.move(self.archive_request())

        size = len(BODY)
        self.assertEqual(
            [
                (4096, size, "Copying x.safetensors: 0 of 0 MB"),
                (8192, size, "Copying x.safetensors: 0 of 0 MB"),
                (size, size, "Copying x.safetensors: 0 of 0 MB"),
                (size, size, "Checking x.safetensors"),
            ],
            reports,
        )
        self.assertEqual((len(BODY), len(BODY), "Checking x.safetensors"), reports[-1])

    def test_paths_reach_the_recycle_bin_normalised(self) -> None:
        self.lay(self.dk)
        self.hold([self.d_item()], [])
        request = self.archive_request()
        request.move_from = str(self.dk).replace("\\", "/") + "/../krea2/x.safetensors"

        self.move(request)

        self.assertEqual(4, len(self.trash_paths))
        for path in self.trash_paths:
            self.assertEqual(os.path.normpath(path), path)
            self.assertNotIn("..", Path(path).parts)
            if os.name == "nt":
                self.assertNotIn("/", path)

    def test_a_move_after_the_archive_folder_changed_is_refused(self) -> None:
        weights = self.lay(self.dk)
        self.lay(self.zk / "moss")
        self.hold([self.d_item()], [self.z_item(self.zk / "moss")])
        elsewhere = self.tmp / "Y"
        elsewhere.mkdir()
        self.ctx.archive_folder = str(elsewhere)
        before = self.everything()

        for request in (
            self.archive_request(),
            self.unarchive_request(self.z_item(self.zk / "moss"), self.dk / "moss" / "x.safetensors"),
        ):
            with self.subTest(request.source):
                with self.assertRaises(DownloadError) as caught:
                    self.move(request)
                self.assertEqual(ARCHIVE_CHANGED_ERROR, str(caught.exception))
        self.assert_untouched(before, weights)

    def test_a_move_whose_archive_is_gone_or_unset_is_refused(self) -> None:
        weights = self.lay(self.dk)
        self.hold([self.d_item()], [])
        before = self.everything()
        for folder, message in (
            ("", NO_ARCHIVE_FOLDER_ERROR),
            (str(self.tmp / "unplugged"), model_moves.ARCHIVE_UNREACHABLE_ERROR),
        ):
            with self.subTest(message):
                self.ctx.archive_folder = folder
                with self.assertRaises(DownloadError) as caught:
                    self.move(self.archive_request())
                self.assertEqual(message, str(caught.exception))
        self.assert_untouched(before, weights)

    def test_a_move_whose_file_is_gone_is_refused(self) -> None:
        self.hold([self.d_item()], [])
        with self.assertRaises(DownloadError) as caught:
            self.move(self.archive_request())
        self.assertEqual(model_moves.FILE_GONE_ERROR, str(caught.exception))
        self.assertFalse((self.z / "Lora").exists())

    def test_an_unarchive_never_makes_this_drives_model_folder(self) -> None:
        self.lay(self.zk)
        self.hold([], [self.z_item()])
        missing_root = self.tmp / "E" / "Lora"
        request = self.unarchive_request(target=missing_root / "krea2" / "x.safetensors")
        before = self.everything()

        with self.assertRaises(DownloadError) as caught:
            self.move(request)

        self.assertEqual(NO_DRIVE_FOLDER_ERROR.format(folder="Lora"), str(caught.exception))
        self.assertFalse((self.tmp / "E").exists())
        self.assertEqual(before, self.everything())

    def test_moved_item_describes_the_file_in_its_new_place(self) -> None:
        self.lay(self.dk)
        item = self.d_item(deleted=True, moved=True)

        archived = moved_item(
            item,
            weights=self.zk / "x.safetensors",
            info_path=self.zk / "x.civitai.info",
            to_archive=True,
        )
        self.assertEqual(str(self.zk / "x.civitai.info"), archived["modelPath"])
        self.assertTrue(archived["archived"] and archived["metadataOnly"] and archived["movedAt"])
        self.assertNotIn("deleted", archived)
        self.assertNotIn("moved", archived)
        self.assertTrue(item["deleted"])  # the row given is not changed
        without_info = moved_item(
            item, weights=self.zk / "x.safetensors", info_path=None, to_archive=True
        )
        self.assertEqual(str(self.zk / "x.safetensors"), without_info["modelPath"])

        back = moved_item(
            self.z_item(), weights=self.dk / "x.safetensors", info_path=None, to_archive=False
        )
        self.assertNotIn("archived", back)
        self.assertEqual(str(self.dk / "x.safetensors"), back["modelPath"])
        self.assertIs(True, back["hasLocalPreview"])


class _Service:
    def __init__(self, roots: dict) -> None:
        self.roots = roots

    def get_effective_roots(self, model_types=None, include_custom_paths=True):
        return {
            name: list(paths)
            for name, paths in self.roots.items()
            if not model_types or name in model_types
        }


class MoveRouteTests(_MoveCase):
    def setUp(self) -> None:
        super().setUp()
        self.weights = self.lay(self.dk)
        self.config_store = ConfigStore(self.data)
        self.config_store.update({"archiveFolder": str(self.z)})
        self.service = _Service({"lora": [str(self.d)]})
        self.journal = RenameJournal(self.data)
        self.routes = _Routes()
        self.queue = _register_model_action_routes(
            self.routes,
            self.config_store,
            self.jobs,
            DecisionStore(self.data),
            BaseCodeStore(self.data),
            AnswerCache(self.data),
            self.journal,
            updater_service=self.service,
        )
        self.hold([self.d_item()], [self.other_z_item()])
        self.release = threading.Event()
        self.moves: list = []

        def fake_move(request, *, report, should_stop, ctx):
            self.moves.append((request, ctx))
            self.release.wait(5)

        self.can_trash = True
        patches = [
            mock.patch("comfy_updater.routes.move_one", fake_move),
            mock.patch(
                "comfy_updater.routes.send2trash_available", side_effect=lambda: self.can_trash
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(_join_job_threads)
        self.addCleanup(self.release.set)

    def call(self, payload):
        handler = self.routes.handlers[("POST", MOVES_ROUTE)]
        response = asyncio.run(handler(_Request("POST", payload)))
        body = json.loads(response.text)
        # No path in any answer, however it is spelled.
        text = "\n".join(_strings(body))
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        return response.status, body

    def post_move(self, **fields):
        payload = {"jobId": "check-1", "path": str(self.weights), "to": "archive"}
        payload.update(fields)
        return self.call(payload)

    def test_a_move_is_queued_under_the_rows_move_key(self) -> None:
        status, body = self.post_move()

        self.assertEqual(200, status, body)
        record = body["request"]
        self.assertEqual(move_key(str(self.weights)), record["key"])
        self.assertEqual("move", record["kind"])
        self.assertEqual("archive", record["source"])
        self.assertEqual("x.safetensors", record["fileName"])
        self.assertEqual(MODEL_DOWNLOAD_JOB, self.jobs.get(body["jobId"]).type)

        # A second click on the row finds its own move already queued.
        again_status, again = self.post_move()
        self.assertEqual(200, again_status, again)
        self.assertEqual(record["id"], again["request"]["id"])

        self.release.set()
        _join_job_threads()
        self.assertEqual(1, len(self.moves))
        request, ctx = self.moves[0]
        self.assertEqual(str(self.weights), request.move_from)
        self.assertEqual(str(self.zk / "x.safetensors"), request.move_to)
        self.assertEqual(self.d_item(), request.check_item)
        self.assertIsInstance(ctx, MoveContext)
        self.assertIs(self.jobs, ctx.job_manager)
        self.assertEqual(self.data, ctx.data_dir)
        self.assertEqual(str(self.z), ctx.archive_folder)
        self.assertIs(send_to_trash, ctx.trash)

    def test_an_unarchive_is_queued_with_its_place_on_this_drive(self) -> None:
        moss = self.z / "Lora" / "moss"
        self.lay(moss)
        self.hold([self.d_item()], [self.z_item(moss)])

        status, body = self.post_move(path=str(moss / "x.civitai.info"), to="drive")

        self.assertEqual(200, status, body)
        self.assertEqual(move_key(str(moss / "x.civitai.info")), body["request"]["key"])
        self.assertEqual("unarchive", body["request"]["source"])
        self.release.set()
        _join_job_threads()
        request, _ctx = self.moves[0]
        self.assertEqual(str(moss / "x.safetensors"), request.move_from)
        self.assertEqual(str(self.d / "moss" / "x.safetensors"), request.move_to)
        self.assertFalse((self.d / "moss").exists())  # made by the move, not by the route

    def test_a_move_needs_a_job_a_path_and_a_place(self) -> None:
        for payload in (
            {},
            {"jobId": "check-1", "to": "archive"},
            {"path": str(self.weights), "to": "archive"},
            {"jobId": "check-1", "path": str(self.weights)},
            {"jobId": "check-1", "path": str(self.weights), "to": "trash"},
        ):
            with self.subTest(payload=sorted(payload)):
                status, _body = self.call(payload)
                self.assertEqual(400, status)
        self.assertEqual([], self.queue.snapshot())

    def test_a_move_is_refused_with_its_reason(self) -> None:
        other = self.lay(self.tmp / "elsewhere")
        embeddings = self.z / "Embeddings" / "faces"
        self.lay(embeddings)
        gone = self.dk / "gone.safetensors"
        info = self.dk / "x.civitai.info"
        z_info = str(self.zk / "x.civitai.info")

        def taken():
            self.zk.mkdir(parents=True)
            (self.zk / "x.civitai.info").write_text("theirs", encoding="utf-8")

        def replacing():
            self.queue.submit(
                DownloadRequest(
                    key="version:202",
                    source="updates",
                    model_id="101",
                    version_id="202",
                    model_type="lora",
                    replace_path=str(self.weights),
                )
            )

        def moving_there():
            self.queue.submit(
                DownloadRequest(
                    key=move_key(str(other)),
                    source="archive",
                    model_id="101",
                    version_id="201",
                    model_type="lora",
                    move_from=str(other),
                    move_to=str(self.zk / "x.safetensors"),
                )
            )

        cases = [
            ("no check job", {"payload": {"jobId": "nope"}}, (404, "job not found")),
            (
                "unlisted file",
                {"payload": {"path": str(self.dk / "y.safetensors")}},
                (404, UNLISTED_FILE_ERROR),
            ),
            ("no send2trash", {"can_trash": False}, (409, NO_TRASH_MOVE_ERROR)),
            ("no folder", {"folder": ""}, (409, NO_ARCHIVE_FOLDER_ERROR)),
            ("overlap", {"folder": str(self.dk)}, (409, ARCHIVE_OVERLAP_ERROR)),
            ("unreachable", {"folder": str(self.tmp / "unplugged")}, (409, ARCHIVE_UNREACHABLE_ERROR)),
            ("drive row to the drive", {"payload": {"to": "drive"}}, (400, NOT_IN_ARCHIVE_ERROR)),
            (
                "archived row to the archive",
                {"items": [self.z_item()], "payload": {"path": z_info}},
                (400, NOT_ON_DRIVE_ERROR),
            ),
            (
                "info-only drive row",
                {
                    "items": [self.d_item(modelPath=str(info), metadataOnly=True)],
                    "payload": {"path": str(info)},
                },
                (400, INFO_ONLY_MOVE_ERROR),
            ),
            ("no model id", {"items": [self.d_item(modelId="")]}, (400, UNIDENTIFIED_MOVE_ERROR)),
            (
                "no version id",
                {"items": [self.d_item(localVersionId="")]},
                (400, UNIDENTIFIED_MOVE_ERROR),
            ),
            (
                "no info file to archive by",
                {"before": lambda: info.unlink(), "after": lambda: self.lay(self.dk)},
                (400, UNIDENTIFIED_MOVE_ERROR),
            ),
            (
                "weights gone",
                {
                    "items": [self.d_item(modelPath=str(gone), filePath=str(gone))],
                    "payload": {"path": str(gone)},
                },
                (404, FILE_GONE_ERROR),
            ),
            (
                "a row already moved",
                {"items": [self.d_item(deleted=True, moved=True)]},
                (404, FILE_GONE_ERROR),
            ),
            (
                "in no model folder",
                {
                    "items": [self.d_item(modelPath=str(other), filePath=str(other))],
                    "payload": {"path": str(other)},
                },
                (400, NOT_IN_TYPE_FOLDER_ERROR),
            ),
            (
                "no drive folder",
                {
                    "items": [self.z_item(embeddings)],
                    "payload": {"path": str(embeddings / "x.civitai.info"), "to": "drive"},
                },
                (400, NO_DRIVE_FOLDER_ERROR.format(folder="Embeddings")),
            ),
            (
                "a taken name",
                {"before": taken, "after": lambda: (self.zk / "x.civitai.info").unlink()},
                (409, NAME_TAKEN_ERROR.format(name="x.civitai.info")),
            ),
            ("being replaced", {"before": replacing}, (409, MOVE_BUSY_ERROR)),
            ("another move to its place", {"before": moving_there}, (409, MOVE_BUSY_ERROR)),
            (
                "an interrupted rename",
                {"journal": {"runId": "r", "steps": [{"done": False}]}},
                (409, JOURNAL_PENDING_ERROR),
            ),
        ]
        for label, case, expected in cases:
            with self.subTest(label):
                if "items" in case:
                    self.hold(case["items"], [self.other_z_item()])
                if "folder" in case:
                    self.config_store.update({"archiveFolder": case["folder"]})
                self.can_trash = case.get("can_trash", True)
                if "before" in case:
                    case["before"]()
                try:
                    with mock.patch.object(
                        self.journal, "pending", return_value=case.get("journal")
                    ):
                        status, body = self.post_move(**case.get("payload", {}))
                    self.assertEqual(expected, (status, body.get("error")))
                finally:
                    if "after" in case:
                        case["after"]()
                    self.queue.stop_all()
                    self.config_store.update({"archiveFolder": str(self.z)})
                    self.hold([self.d_item()], [self.other_z_item()])
        self.assertEqual([], self.moves)
        self.assertEqual(
            [],
            [
                record
                for record in self.queue.snapshot()
                if record["key"] == move_key(str(self.weights))
            ],
        )
        self.assertEqual(BODY, self.weights.read_bytes())

    def test_recheck_refuses_a_move_whose_file_is_gone(self) -> None:
        request = DownloadRequest(
            key=move_key(str(self.weights)),
            source="archive",
            model_id="101",
            version_id="201",
            model_type="lora",
            move_from=str(self.weights),
            move_to=str(self.zk / "x.safetensors"),
        )
        self.assertEqual("", recheck(request, journal_pending=False))
        self.weights.unlink()
        self.assertEqual(FILE_GONE_ERROR, recheck(request, journal_pending=False))


if __name__ == "__main__":
    unittest.main()
