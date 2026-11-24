from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_updater.archive import (
    ARCHIVE_RESULTS_FILENAME,
    ArchiveListingError,
    archive_reference_path,
    archive_type_folders,
    list_archive_entries,
    load_archive_items,
    load_archive_results,
    load_archive_summary,
    overlaps_model_root,
    save_archive_results,
)
from comfy_updater.config_store import ConfigStore
from comfy_updater.routes import _drop_refused_archive_folder, _normalize_config_payload
from comfy_updater.sidecar import read_data_file


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class ArchiveListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_a_folder_that_cannot_be_read_fails_the_listing(self) -> None:
        archive = self.tmp / "Z"
        _write(archive / "Lora" / "a.civitai.info", json.dumps({"modelId": 1, "id": 10}))
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)

        def failing_walk(top, onerror=None, **_kwargs):
            onerror(OSError("The network name is no longer available"))
            return iter(())

        with mock.patch("comfy_updater.archive.os.walk", failing_walk):
            with self.assertRaises(ArchiveListingError):
                list_archive_entries(archive, {"lora": [d_lora]})

    def test_entries_come_only_from_identifying_info_files(self) -> None:
        archive = self.tmp / "Z"
        _write(archive / "Lora" / "sdxl" / "a.civitai.info", json.dumps({"modelId": 1, "id": 10}))
        _write(archive / "Lora" / "sdxl" / "b.civitai.info", json.dumps({"modelId": None, "id": 11}))
        _write(archive / "Lora" / "sdxl" / "c.safetensors")
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)

        entries, counts = list_archive_entries(archive, {"lora": [d_lora]})

        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("lora", entry["modelType"])
        self.assertTrue(entry["archived"])
        self.assertTrue(entry["metadataOnly"])
        self.assertEqual(archive / "Lora" / "sdxl" / "a.civitai.info", entry["infoPath"])
        self.assertEqual(archive / "Lora" / "sdxl" / "a.safetensors", entry["filePath"])
        # The info read here is what the check uses: it never reads the drive again.
        self.assertEqual({"modelId": 1, "id": 10}, entry["info"])
        self.assertEqual({"models": 1, "withoutInfo": 1, "typeFolders": 1}, counts)

    def test_an_existing_weights_file_is_the_file_path(self) -> None:
        archive = self.tmp / "Z"
        _write(archive / "Lora" / "a.civitai.info", json.dumps({"modelId": 1, "id": 10}))
        weights = _write(archive / "Lora" / "a.ckpt")
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)

        entries, counts = list_archive_entries(archive, {"lora": [d_lora]})

        self.assertEqual(weights, entries[0]["filePath"])
        self.assertEqual(0, counts["withoutInfo"])

    def test_type_folders_match_by_name_case_insensitively(self) -> None:
        archive = self.tmp / "Z"
        (archive / "lora").mkdir(parents=True)
        (archive / "Clip").mkdir(parents=True)
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)

        folders = archive_type_folders(archive, {"lora": [d_lora], "vae": []})

        self.assertEqual([(archive / "lora", d_lora)], folders["lora"])
        self.assertEqual([], folders.get("vae", []))
        self.assertNotIn("clip", [key.lower() for key in folders])

    def test_a_missing_archive_folder_lists_nothing(self) -> None:
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)
        entries, counts = list_archive_entries(self.tmp / "gone", {"lora": [d_lora]})
        self.assertEqual([], entries)
        self.assertEqual({"models": 0, "withoutInfo": 0, "typeFolders": 0}, counts)

    def test_top_folders_that_match_no_type_are_counted_as_none(self) -> None:
        archive = self.tmp / "Z"
        _write(archive / "Stuff" / "a.civitai.info", json.dumps({"modelId": 1, "id": 10}))
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)

        entries, counts = list_archive_entries(archive, {"lora": [d_lora]})

        self.assertEqual([], entries)
        self.assertEqual(0, counts["typeFolders"])


class ArchiveReferencePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.archive = self.tmp / "Z"
        self.d_lora = self.tmp / "D" / "Lora"
        self.d_lora.mkdir(parents=True)
        (self.archive / "Lora" / "sdxl").mkdir(parents=True)
        self.archived = self.archive / "Lora" / "sdxl" / "a.civitai.info"

    def test_reference_path_keeps_the_subfolder_when_d_has_it(self) -> None:
        (self.d_lora / "sdxl").mkdir()
        result = archive_reference_path(self.archive, self.archived, "lora", {"lora": [self.d_lora]})
        self.assertEqual(self.d_lora / "sdxl" / "a.civitai.info", result)

    def test_reference_path_falls_back_to_the_type_root(self) -> None:
        result = archive_reference_path(self.archive, self.archived, "lora", {"lora": [self.d_lora]})
        self.assertEqual(self.d_lora / "a.civitai.info", result)

    def test_reference_path_none_for_an_unknown_type_folder(self) -> None:
        other = self.archive / "Clip" / "x.civitai.info"
        self.assertIsNone(archive_reference_path(self.archive, other, "lora", {"lora": [self.d_lora]}))

    def test_reference_path_needs_no_archive_on_disk(self) -> None:
        (self.d_lora / "sdxl").mkdir()
        unplugged = self.tmp / "unplugged"
        archived = unplugged / "lora" / "sdxl" / "a.civitai.info"  # the top folder matches without case

        result = archive_reference_path(unplugged, archived, "lora", {"lora": [self.d_lora]})

        self.assertFalse(unplugged.exists())
        self.assertEqual(self.d_lora / "sdxl" / "a.civitai.info", result)
        self.assertEqual(
            self.d_lora / "a.civitai.info",
            archive_reference_path(unplugged, unplugged / "Lora" / "a.civitai.info", "lora", {"lora": [self.d_lora]}),
        )

    def test_reference_path_none_outside_the_archive_or_its_type_folders(self) -> None:
        roots = {"lora": [self.d_lora]}
        self.assertIsNone(archive_reference_path(self.archive, self.tmp / "D" / "Lora" / "a.civitai.info", "lora", roots))
        self.assertIsNone(archive_reference_path(self.archive, self.archive / "a.civitai.info", "lora", roots))
        self.assertIsNone(archive_reference_path(self.archive, self.archived, "checkpoint", roots))

    def test_reference_path_stays_inside_the_d_root(self) -> None:
        escaping = self.archive / "Lora" / ".." / ".." / "x.civitai.info"
        result = archive_reference_path(self.archive, escaping, "lora", {"lora": [self.d_lora]})
        self.assertIsNone(result)


class ArchiveResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def test_results_round_trip(self) -> None:
        save_archive_results(self.data_dir, r"Z:\models", {"models": 1}, [{"id": 1}])
        self.assertTrue((self.data_dir / ARCHIVE_RESULTS_FILENAME).is_file())
        self.assertEqual([{"id": 1}], load_archive_items(self.data_dir, r"Z:\models"))
        summary = load_archive_summary(self.data_dir, r"Z:\models")
        self.assertEqual(1, summary["models"])
        self.assertTrue(summary["checkedAt"])

    def test_items_and_summary_come_from_one_read(self) -> None:
        save_archive_results(self.data_dir, r"Z:\models", {"models": 1}, [{"id": 1}])
        with mock.patch("comfy_updater.archive.read_data_file", wraps=read_data_file) as read:
            items, summary = load_archive_results(self.data_dir, r"Z:\models")
        self.assertEqual(1, read.call_count)
        self.assertEqual([{"id": 1}], items)
        self.assertEqual(1, summary["models"])
        self.assertTrue(summary["checkedAt"])
        self.assertEqual(([], {}), load_archive_results(self.data_dir, r"Y:\models"))

    def test_results_for_another_folder_are_not_loaded(self) -> None:
        save_archive_results(self.data_dir, r"Z:\models", {"models": 1}, [{"id": 1}])
        self.assertEqual([], load_archive_items(self.data_dir, r"Y:\models"))
        self.assertEqual({}, load_archive_summary(self.data_dir, r"Y:\models"))
        self.assertEqual([], load_archive_items(self.data_dir, ""))
        self.assertEqual({}, load_archive_summary(self.data_dir, ""))
        if Path("A:\\x") == Path("a:\\X"):  # Windows compares folders without case
            self.assertEqual([{"id": 1}], load_archive_items(self.data_dir, r"z:\MODELS"))

    def test_missing_unreadable_or_outdated_results_load_as_empty(self) -> None:
        self.assertEqual([], load_archive_items(self.data_dir, r"Z:\models"))
        self.assertEqual({}, load_archive_summary(self.data_dir, r"Z:\models"))
        _write(self.data_dir / ARCHIVE_RESULTS_FILENAME, "not json")
        self.assertEqual([], load_archive_items(self.data_dir, r"Z:\models"))
        _write(
            self.data_dir / ARCHIVE_RESULTS_FILENAME,
            json.dumps({"schemaVersion": 0, "archiveFolder": r"Z:\models", "items": [{"id": 1}], "summary": {}}),
        )
        self.assertEqual([], load_archive_items(self.data_dir, r"Z:\models"))
        self.assertEqual({}, load_archive_summary(self.data_dir, r"Z:\models"))


class ArchiveConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.store = ConfigStore(self.tmp / "data")

    def _save(self, payload: dict) -> list[str]:
        incoming = _normalize_config_payload(payload)
        warnings = _drop_refused_archive_folder(incoming, self.store.get().get("archiveFolder", ""))
        self.store.update(incoming)
        return warnings

    def test_default_is_no_archive(self) -> None:
        self.assertEqual("", self.store.get()["archiveFolder"])

    def test_config_accepts_an_existing_archive_folder(self) -> None:
        folder = self.tmp / "archive"
        folder.mkdir()
        self.assertEqual([], self._save({"archiveFolder": f"  {folder}  "}))
        self.assertEqual(str(folder), self.store.get()["archiveFolder"])

    def test_config_refuses_an_archive_folder_that_does_not_exist(self) -> None:
        warnings = self._save({"archiveFolder": str(self.tmp / "nope")})
        self.assertEqual(1, len(warnings))
        self.assertIn("was not saved", warnings[0])
        self.assertEqual("", self.store.get()["archiveFolder"])

    def test_an_empty_value_clears_the_archive(self) -> None:
        folder = self.tmp / "archive"
        folder.mkdir()
        self._save({"archiveFolder": str(folder)})
        self.assertEqual([], self._save({"archiveFolder": ""}))
        self.assertEqual("", self.store.get()["archiveFolder"])

    def test_a_stored_folder_that_went_missing_is_kept(self) -> None:
        gone = r"Z:\models"
        self.store.update({"archiveFolder": gone})
        self.assertEqual([], self._save({"archiveFolder": gone}))
        self.assertEqual(gone, self.store.get()["archiveFolder"])


class ArchiveOverlapTests(unittest.TestCase):
    """An archive folder that is, holds or lies inside a model folder would be written to."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.models = self.tmp / "D" / "models"
        self.d_lora = self.models / "Lora"
        (self.d_lora / "old").mkdir(parents=True)
        self.archive = self.tmp / "Z" / "models"
        self.archive.mkdir(parents=True)
        self.roots = {"lora": [str(self.d_lora)], "vae": []}

    def test_overlap_is_equal_inside_or_holding(self) -> None:
        for folder in (self.d_lora, self.d_lora / "old", self.models, Path(str(self.d_lora) + "/")):
            with self.subTest(folder=folder):
                self.assertTrue(overlaps_model_root(str(folder), self.roots))
        for folder in (self.archive, self.tmp / "D" / "models2", self.tmp / "D" / "models" / "Lor"):
            with self.subTest(folder=folder):
                self.assertFalse(overlaps_model_root(str(folder), self.roots))
        self.assertFalse(overlaps_model_root(str(self.d_lora), {}))

    def test_an_overlapping_folder_is_refused_at_save(self) -> None:
        for relation, folder in (
            ("equal", self.d_lora),
            ("inside", self.d_lora / "old"),
            ("holding", self.models),
        ):
            with self.subTest(relation=relation):
                incoming = {"archiveFolder": str(folder)}
                warnings = _drop_refused_archive_folder(incoming, "", self.roots)
                self.assertEqual(
                    [f"Archive folder {folder} was not saved: it overlaps a model folder."], warnings
                )
                self.assertNotIn("archiveFolder", incoming)

    def test_a_folder_apart_from_the_model_folders_is_saved(self) -> None:
        incoming = {"archiveFolder": str(self.archive)}
        self.assertEqual([], _drop_refused_archive_folder(incoming, "", self.roots))
        self.assertEqual(str(self.archive), incoming["archiveFolder"])


if __name__ == "__main__":
    unittest.main()
