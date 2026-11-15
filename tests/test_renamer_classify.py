from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from comfy_updater.civitai_client import CivitaiRequestError
from comfy_updater.rename_store import DecisionStore
from comfy_updater.renamer import (
    MAIN_FILE_TYPES,
    RENAME_TYPES,
    TYPE_FIT,
    ModelGroup,
    classify,
    collect_entries,
)


def write_entry(folder: Path, stem: str, model_id, version_id, sha256: str, weights: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "modelId": model_id,
        "id": version_id,
        "files": [{"id": 1, "type": "Model", "hashes": {"SHA256": sha256.lower()}}],
    }
    (folder / (stem + ".civitai.info")).write_text(json.dumps(info), encoding="utf-8")
    if weights:
        (folder / (stem + ".safetensors")).write_bytes(b"weights")
    return folder / (stem + (".safetensors" if weights else ".civitai.info"))


def live_file(file_id: int, sha256: str, file_type: str = "Model", primary: bool = False, name: str = "") -> dict:
    return {
        "id": file_id,
        "name": name or f"file{file_id}.safetensors",
        "type": file_type,
        "primary": primary,
        "hashes": {"SHA256": sha256.upper()},
    }


def live_model(model_id: int, model_type: str, versions: list[dict]) -> dict:
    return {"id": model_id, "name": f"model {model_id}", "type": model_type, "modelVersions": versions}


class FakeClient:
    def __init__(self, models: dict) -> None:
        self.models = models
        self.calls: list[str] = []

    def get_model(self, model_id, *, raise_on_error: bool = False):
        self.calls.append(str(model_id))
        value = self.models[str(model_id)]
        if isinstance(value, Exception):
            raise value
        return value


def no_progress(current, total, message):
    pass


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lora = self.root / "loras"
        self.unet = self.root / "unet"
        self.ckpt = self.root / "checkpoints"
        self.roots = {"lora": [self.lora], "unet": [self.unet], "checkpoint": [self.ckpt]}
        self.decisions = DecisionStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_classify(self, client):
        entries = collect_entries(self.roots)
        return entries, classify(entries, client, self.decisions, progress=no_progress, control=None)

    def test_constants(self) -> None:
        self.assertEqual(RENAME_TYPES, ("checkpoint", "lora", "unet", "embedding"))
        self.assertEqual(MAIN_FILE_TYPES, {"Model", "Pruned Model", "Diffusion Model"})
        self.assertEqual(TYPE_FIT["lora"], {"LORA", "LoCon", "DoRA", "LyCORIS"})
        self.assertEqual(TYPE_FIT["unet"], {"Checkpoint"})

    def test_collect_reads_weights_and_sidecar_only_entries(self) -> None:
        write_entry(self.lora, "a - b - v1.5 (sdxl)", 10, 100, "aa")
        write_entry(self.lora, "c - d (sdxl)", 11, 110, "bb", weights=False)
        (self.lora / "anon.safetensors").write_bytes(b"x")  # no sidecar: no identity
        vae = self.root / "vae"
        write_entry(vae, "some vae", 12, 120, "cc")
        roots = dict(self.roots, vae=[vae])
        entries = sorted(collect_entries(roots), key=lambda e: e.stem)
        self.assertEqual([e.stem for e in entries], ["a - b - v1.5 (sdxl)", "c - d (sdxl)"])
        first, second = entries
        self.assertEqual((first.model_id, first.version_id, first.sha256), ("10", "100", "aa"))
        self.assertTrue(first.has_weights)
        self.assertFalse(first.metadata_only)
        self.assertEqual(first.folder, self.lora)
        self.assertTrue(second.metadata_only)
        self.assertFalse(second.has_weights)
        self.assertEqual(second.path, self.lora / "c - d (sdxl).civitai.info")

    def test_lora_matching_main_file_forms_one_group_one_slot(self) -> None:
        write_entry(self.lora, "x - y (sdxl)", 1, 11, "abc")
        client = FakeClient({"1": live_model(1, "LORA", [{"id": 11, "files": [live_file(501, "ABC")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(rows, [])
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertIsInstance(group, ModelGroup)
        self.assertEqual(group.model_id, "1")
        self.assertEqual(list(group.slots_by_key), ["11"])
        placed = group.placed[0]
        self.assertEqual(placed.file["id"], 501)
        self.assertEqual(placed.file_key, "file:501")

    def test_unet_entry_matching_vae_file_is_kept_not_main_file(self) -> None:
        write_entry(self.unet, "flux2-vae", 2, 21, "def")
        client = FakeClient(
            {"2": live_model(2, "Checkpoint", [{"id": 21, "files": [live_file(1, "000"), live_file(2, "DEF", "VAE")]}])}
        )
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(groups, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["status"], row["reason"]), ("kept", "not-main-file"))
        self.assertEqual(row["newStem"], row["oldStem"])
        self.assertEqual(row["oldStem"], "flux2-vae")
        self.assertEqual(row["parts"]["creator"], "flux2-vae")
        self.assertEqual(row["fileKey"], "file:2")
        self.assertFalse(row["selected"])

    def test_lora_on_checkpoint_model_is_type_mismatch(self) -> None:
        write_entry(self.lora, "x - y (sdxl)", 3, 31, "abc")
        client = FakeClient({"3": live_model(3, "Checkpoint", [{"id": 31, "files": [live_file(1, "ABC")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(groups, [])
        self.assertEqual((rows[0]["status"], rows[0]["reason"]), ("kept", "type-mismatch"))

    def test_release_gone(self) -> None:
        write_entry(self.lora, "x - y (sdxl)", 3, 99, "abc")
        client = FakeClient({"3": live_model(3, "LORA", [{"id": 31, "files": [live_file(1, "ABC")]}])})
        _entries, (_groups, rows) = self.run_classify(client)
        self.assertEqual((rows[0]["status"], rows[0]["reason"]), ("kept", "release-gone"))

    def test_404_is_kept_and_500_is_error(self) -> None:
        write_entry(self.lora, "gone", 4, 41, "a1")
        write_entry(self.lora, "broken", 5, 51, "a2")
        client = FakeClient(
            {
                "4": CivitaiRequestError("404 Not Found. The model may have been removed from Civitai.", status=404),
                "5": CivitaiRequestError("https://civitai.red/api/v1/models/5 :: 500 Internal Server Error"),
            }
        )
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(groups, [])
        by_stem = {row["oldStem"]: row for row in rows}
        self.assertEqual((by_stem["gone"]["status"], by_stem["gone"]["reason"]), ("kept", "not-on-civitai"))
        self.assertEqual(by_stem["broken"]["status"], "error")
        self.assertIn("500 Internal Server Error", by_stem["broken"]["reason"])

    def test_typo_twins_become_duplicate_of_the_copy_with_weights(self) -> None:
        first = write_entry(self.lora, "fotomax - nikora hyperreal - 1.0 (flux2k9b)", 6, 61, "f00d")
        write_entry(self.lora, "fotomax - nikra hyperreal - v1.0 (flux2k9b)", 6, 61, "f00d", weights=False)
        client = FakeClient({"6": live_model(6, "LORA", [{"id": 61, "files": [live_file(7, "F00D")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(len(groups), 1)
        self.assertEqual([p.entry.stem for p in groups[0].placed], ["fotomax - nikora hyperreal - 1.0 (flux2k9b)"])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "duplicate")
        self.assertEqual(row["oldStem"], "fotomax - nikra hyperreal - v1.0 (flux2k9b)")
        self.assertEqual(row["survivorKey"], str(first).lower())
        self.assertNotEqual(row["key"], row["survivorKey"])

    def test_weights_copy_survives_even_when_it_sorts_later(self) -> None:
        write_entry(self.lora, "fotomax - nikora hyperreal - 1.0 (flux2k9b)", 6, 61, "f00d", weights=False)
        weights = write_entry(self.lora, "fotomax - nikra hyperreal - v1.0 (flux2k9b)", 6, 61, "f00d")
        client = FakeClient({"6": live_model(6, "LORA", [{"id": 61, "files": [live_file(7, "F00D")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual([p.entry.stem for p in groups[0].placed], ["fotomax - nikra hyperreal - v1.0 (flux2k9b)"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["oldStem"], "fotomax - nikora hyperreal - 1.0 (flux2k9b)")
        self.assertEqual(rows[0]["survivorKey"], str(weights).lower())

    def test_two_weights_copies_and_a_sidecar_only_copy_keep_a_weights_copy(self) -> None:
        write_entry(self.lora, "a copy", 6, 61, "f00d", weights=False)
        write_entry(self.lora, "b copy", 6, 61, "f00d")
        write_entry(self.lora, "c copy", 6, 61, "f00d")
        client = FakeClient({"6": live_model(6, "LORA", [{"id": 61, "files": [live_file(7, "F00D")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual([p.entry.stem for p in groups[0].placed], ["b copy"])
        self.assertEqual(sorted(row["oldStem"] for row in rows), ["a copy", "c copy"])

    def test_different_unmatched_files_of_one_version_are_not_duplicates(self) -> None:
        write_entry(self.unet, "model q8", 7, 71, "1111")
        write_entry(self.unet, "model q4", 7, 71, "2222")
        client = FakeClient({"7": live_model(7, "Checkpoint", [{"id": 71, "files": [live_file(1, "FFFF")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(rows, [])
        self.assertEqual(sorted(p.entry.stem for p in groups[0].placed), ["model q4", "model q8"])

    def test_unmatched_twins_with_the_same_hash_are_duplicates(self) -> None:
        write_entry(self.unet, "model q8", 7, 71, "1111")
        write_entry(self.unet, "model q8 typo", 7, 71, "1111", weights=False)
        client = FakeClient({"7": live_model(7, "Checkpoint", [{"id": 71, "files": [live_file(1, "FFFF")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual([p.entry.stem for p in groups[0].placed], ["model q8"])
        self.assertEqual([(row["oldStem"], row["status"]) for row in rows], [("model q8 typo", "duplicate")])

    def test_folder_registered_under_two_types_is_one_entry(self) -> None:
        shared = self.root / "diffusion"
        write_entry(shared, "x - y (flux)", 7, 71, "1111")
        self.roots = {"checkpoint": [shared], "unet": [shared]}
        client = FakeClient({"7": live_model(7, "Checkpoint", [{"id": 71, "files": [live_file(1, "1111")]}])})
        entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(len(entries), 1)
        self.assertEqual(rows, [])
        self.assertEqual(len(groups[0].placed), 1)

    def test_one_version_with_two_matched_files_has_two_slots(self) -> None:
        write_entry(self.lora, "krea2_day_grade_v2", 8, 81, "aaa1")
        write_entry(self.lora, "krea2_night_grade_v2", 8, 81, "bbb2")
        files = [
            live_file(801, "AAA1", name="krea2_day_grade_v2.safetensors", primary=True),
            live_file(802, "BBB2", name="krea2_night_grade_v2.safetensors"),
        ]
        client = FakeClient({"8": live_model(8, "LORA", [{"id": 81, "files": files}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(rows, [])
        self.assertEqual(sorted(groups[0].slots_by_key), ["81:801", "81:802"])

    def test_without_hash_uses_primary_file(self) -> None:
        write_entry(self.lora, "nohash", 9, 91, "")
        files = [live_file(901, "AA", "Training Data"), live_file(902, "BB", primary=True)]
        client = FakeClient({"9": live_model(9, "LORA", [{"id": 91, "files": files}])})
        _entries, (groups, _rows) = self.run_classify(client)
        self.assertEqual(groups[0].placed[0].file_key, "file:902")

    def test_kept_by_you(self) -> None:
        write_entry(self.lora, "x - y (sdxl)", 1, 11, "abc")
        self.decisions.set_file("file:501", keep=True)
        client = FakeClient({"1": live_model(1, "LORA", [{"id": 11, "files": [live_file(501, "ABC")]}])})
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(groups, [])
        self.assertEqual((rows[0]["status"], rows[0]["reason"]), ("kept", "kept-by-you"))

    def test_one_fetch_per_model(self) -> None:
        write_entry(self.lora, "one", 1, 11, "abc")
        write_entry(self.lora, "two", 1, 12, "abd")
        versions = [{"id": 11, "files": [live_file(1, "ABC")]}, {"id": 12, "files": [live_file(2, "ABD")]}]
        client = FakeClient({"1": live_model(1, "LORA", versions)})
        _entries, (groups, _rows) = self.run_classify(client)
        self.assertEqual(client.calls, ["1"])
        self.assertEqual(sorted(groups[0].slots_by_key), ["11", "12"])

    def test_rows_record_the_identity_of_their_own_file(self) -> None:
        weights = write_entry(self.lora, "gone", 4, 41, "a1")
        info_only = write_entry(self.lora, "info", 5, 51, "a2", weights=False)
        client = FakeClient(
            {
                "4": CivitaiRequestError("404 Not Found.", status=404),
                "5": CivitaiRequestError("404 Not Found.", status=404),
            }
        )
        _entries, (_groups, rows) = self.run_classify(client)
        by_stem = {row["oldStem"]: row for row in rows}
        for stem, path in (("gone", weights), ("info", info_only)):
            stat = path.stat()
            self.assertEqual(stat.st_size, by_stem[stem]["fileSize"])
            self.assertEqual(stat.st_mtime_ns, by_stem[stem]["fileMtimeNs"])

    def test_malformed_live_data_is_an_error_row_for_that_model_only(self) -> None:
        write_entry(self.lora, "odd type", 6, 61, "b1")
        write_entry(self.lora, "odd versions", 7, 71, "b2")
        write_entry(self.lora, "fine", 8, 81, "b3")
        odd_type = live_model(6, "LORA", [{"id": 61, "files": [live_file(1, "B1")]}])
        odd_type["type"] = ["LORA"]  # unhashable
        odd_versions = live_model(7, "LORA", [])
        odd_versions["modelVersions"] = 5  # not iterable
        client = FakeClient(
            {"6": odd_type, "7": odd_versions, "8": live_model(8, "LORA", [{"id": 81, "files": [live_file(3, "B3")]}])}
        )
        _entries, (groups, rows) = self.run_classify(client)
        self.assertEqual(["8"], [group.model_id for group in groups])
        by_stem = {row["oldStem"]: row for row in rows}
        self.assertEqual("error", by_stem["odd type"]["status"])
        self.assertEqual("error", by_stem["odd versions"]["status"])
        self.assertTrue(by_stem["odd type"]["reason"])

    def test_cancel_in_the_first_fetches_does_not_seed_base_codes(self) -> None:
        from comfy_updater.rename_store import BaseCodeStore
        from comfy_updater.renamer import run_dry_run

        write_entry(self.lora, "a - b (sdxl)", 1, 11, "abc")
        write_entry(self.lora, "c - d (pony)", 2, 21, "abd")
        models = {
            "1": live_model(1, "LORA", [{"id": 11, "baseModel": "SDXL 1.0", "files": [live_file(1, "ABC")]}]),
            "2": live_model(2, "LORA", [{"id": 21, "baseModel": "Pony", "files": [live_file(2, "ABD")]}]),
        }

        class StopAfterFirst:
            def __init__(self):
                self.checks = 0

            def wait_if_paused(self):
                pass

            def is_cancelled(self):
                self.checks += 1
                return self.checks > 1

        base_codes = BaseCodeStore(self.root / "codes")

        class NoGenerator:
            def generate(self, messages):  # pragma: no cover - stopped before naming
                raise AssertionError("no naming after a stop")

        from comfy_updater.rename_store import AnswerCache

        run_dry_run(
            self.roots, FakeClient(models), NoGenerator(), decisions=self.decisions, base_codes=base_codes,
            cache=AnswerCache(self.root / "codes"), clip_name="q", progress=no_progress, emit=lambda row: None,
            control=StopAfterFirst(),
        )
        self.assertEqual({}, base_codes.all())
        again = BaseCodeStore(self.root / "codes")
        # Not marked seeded: the next full dry run still seeds from every stem.
        again.seed([("Pony", "c - d (pony)")])
        self.assertEqual({"Pony": "pony"}, again.all())


class RequestErrorStatusTests(unittest.TestCase):
    def test_status_defaults_to_none_and_message_unchanged(self) -> None:
        self.assertIsNone(CivitaiRequestError("boom").status)
        error = CivitaiRequestError("404 Not Found.", status=404)
        self.assertEqual(error.status, 404)
        self.assertEqual(str(error), "404 Not Found.")


if __name__ == "__main__":
    unittest.main()
