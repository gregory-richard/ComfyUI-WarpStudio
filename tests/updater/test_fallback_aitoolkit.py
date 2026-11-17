from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from comfy_updater.fallback_aitoolkit import (
    base_names_from_codes,
    lookup_own_training,
    pick_sample,
)


def write_safetensors(path: Path, meta: dict | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {} if meta is None else {"__metadata__": meta}
    raw = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)
    return path


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def toolkit_meta(**extra) -> dict:
    meta = {
        "name": "my_concept_v2",
        "training_info": json.dumps({"step": 1500}),
        "ss_base_model_version": "krea2",
        "software": json.dumps({"name": "ai-toolkit"}),
    }
    meta.update(extra)
    return meta


class OwnTrainingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.lib = root / "lib"
        self.out = root / "out"
        self.lib.mkdir()
        self.out.mkdir()

    def _samples(self, name="my_concept_v2", files=()):
        samples = self.out / name / "samples"
        samples.mkdir(parents=True, exist_ok=True)
        for f in files:
            touch(samples / f)
        return samples

    def test_own_training_with_samples(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        samples = self._samples(files=(
            "111__000001000_0.jpg", "222__000001500_3.jpg",
            "222__000001500_1.jpg", "333__000002000_0.jpg"))
        r = lookup_own_training(model, [self.out], "me", {"krea2": "Krea 2"})
        self.assertEqual((r.source, r.title, r.version_name, r.base_model, r.creator),
                         ("ai-toolkit", "my concept v2", "step 1500", "Krea 2", "me"))
        self.assertEqual(r.image_path, samples / "222__000001500_1.jpg")
        self.assertTrue(r.final)
        self.assertEqual(r.url, "")

    def test_nearest_earlier_step_when_exact_step_has_none(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        samples = self._samples(files=("1__000001000_2.jpg", "1__000001000_0.png", "1__000002000_0.jpg"))
        r = lookup_own_training(model, [self.out], "me", {})
        self.assertEqual(r.image_path, samples / "1__000001000_0.png")

    def test_no_earlier_step_gives_no_image(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        self._samples(files=("1__000002000_0.jpg",))
        r = lookup_own_training(model, [self.out], "me", {})
        self.assertIsNone(r.image_path)
        self.assertTrue(r.final)

    def test_no_samples_still_fills_info(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        (self.out / "my_concept_v2").mkdir()
        r = lookup_own_training(model, [self.out], "", {"krea2": "Krea 2"})
        self.assertIsNone(r.image_path)
        self.assertTrue(r.final)
        self.assertEqual(r.title, "my concept v2")
        self.assertEqual(r.creator, "")

    def test_missing_step_takes_highest_sample(self):
        meta = toolkit_meta()
        del meta["training_info"]
        model = write_safetensors(self.lib / "x.safetensors", meta)
        samples = self._samples(files=("1__000001000_0.jpg", "1__000003000_1.jpg", "1__000003000_0.jpg"))
        r = lookup_own_training(model, [self.out], "me", {})
        self.assertEqual(r.version_name, "")
        self.assertEqual(r.image_path, samples / "1__000003000_0.jpg")

    def test_third_party_ai_toolkit_file_is_not_own(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        self.assertIsNone(lookup_own_training(model, [self.out], "me", {}))

    def test_no_folders_configured_is_off(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        self.assertIsNone(lookup_own_training(model, [], "me", {}))

    def test_ss_output_name_fallback_and_unknown_base(self):
        meta = toolkit_meta(ss_base_model_version="weird")
        del meta["name"]
        meta["ss_output_name"] = "abc_def"
        model = write_safetensors(self.lib / "x.safetensors", meta)
        (self.out / "abc_def").mkdir()
        r = lookup_own_training(model, [self.out], "me", {"krea2": "Krea 2"})
        self.assertEqual((r.title, r.base_model), ("abc def", ""))

    def test_not_a_training(self):
        (self.out / "my_concept_v2").mkdir()
        model = write_safetensors(self.lib / "x.safetensors", {"name": "my_concept_v2"})
        self.assertIsNone(lookup_own_training(model, [self.out], "me", {}))
        bad = self.lib / "y.safetensors"
        bad.write_bytes(b"not a model")
        self.assertIsNone(lookup_own_training(bad, [self.out], "me", {}))
        no_meta = write_safetensors(self.lib / "z.safetensors", None)
        self.assertIsNone(lookup_own_training(no_meta, [self.out], "me", {}))

    def test_only_images_directly_in_samples_count(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        samples = self._samples(files=("1__000001500_0.txt", "sub/1__000001500_0.jpg", "1__000001000_0.jpg"))
        r = lookup_own_training(model, [self.out], "me", {})
        self.assertEqual(r.image_path, samples / "1__000001000_0.jpg")

    def test_uppercase_extension_matches(self):
        model = write_safetensors(self.lib / "x.safetensors", toolkit_meta())
        samples = self._samples(files=("1__000001500_0.JPG",))
        r = lookup_own_training(model, [self.out], "me", {})
        self.assertEqual(r.image_path, samples / "1__000001500_0.JPG")

    def test_malformed_metadata_never_raises(self):
        (self.out / "my_concept_v2").mkdir()
        cases = [
            toolkit_meta(training_info="{not json"),
            toolkit_meta(training_info=json.dumps({"step": "many"})),
            toolkit_meta(training_info=json.dumps([1, 2])),
            toolkit_meta(software="{broken"),
            toolkit_meta(training_info=json.dumps({"step": True})),
            toolkit_meta(name=5),
            toolkit_meta(ss_base_model_version=7),
        ]
        for i, meta in enumerate(cases):
            model = write_safetensors(self.lib / f"m{i}.safetensors", meta)
            lookup_own_training(model, [self.out], "me", {})  # must not raise
        r = lookup_own_training(
            write_safetensors(self.lib / "ok.safetensors", toolkit_meta(training_info="{bad")),
            [self.out], "me", {})
        self.assertEqual(r.version_name, "")

    def test_software_only_without_training_info_is_a_training(self):
        meta = toolkit_meta()
        del meta["training_info"]
        (self.out / "my_concept_v2").mkdir()
        r = lookup_own_training(write_safetensors(self.lib / "x.safetensors", meta), [self.out], "me", {})
        self.assertIsNotNone(r)

    def test_name_cannot_escape_folder(self):
        (self.out.parent / "secret").mkdir()
        for bad in ("../secret", "..", "a/b", "a\\b", ".", ""):
            model = write_safetensors(self.lib / "x.safetensors", toolkit_meta(name=bad))
            self.assertIsNone(lookup_own_training(model, [self.out], "me", {}), bad)


class PickSampleTests(unittest.TestCase):
    def test_missing_dir(self):
        self.assertIsNone(pick_sample(Path("does-not-exist-xyz"), 100))

    def test_exact_and_none_step(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for n in ("1__000000100_1.jpg", "1__000000100_0.jpg", "1__000000200_0.jpg"):
                touch(d / n)
            self.assertEqual(pick_sample(d, 100), d / "1__000000100_0.jpg")
            self.assertEqual(pick_sample(d, None), d / "1__000000200_0.jpg")
            self.assertEqual(pick_sample(d, 150), d / "1__000000100_0.jpg")
            self.assertIsNone(pick_sample(d, 50))


class BaseNamesTests(unittest.TestCase):
    def test_base_names_from_codes(self):
        self.assertEqual(
            base_names_from_codes({"Krea 2": "krea2", "Krea 2 Turbo": "krea2", "Qwen": "qwen"}),
            {"krea2": "Krea 2", "qwen": "Qwen"})


if __name__ == "__main__":
    unittest.main()
