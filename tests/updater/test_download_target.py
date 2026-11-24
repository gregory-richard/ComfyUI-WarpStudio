from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comfy_updater import download_target
from comfy_updater.download_target import (
    Target,
    ai_stem,
    base_folder,
    choose_target,
    inside_roots,
    stem_taken,
)
from comfy_updater.model_download import DownloadError
from comfy_updater.naming_check import needs_review
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore

ANSWER = "title: glow\noriginal:\nversion 1: v2"


def glow_file() -> dict:
    return {
        "id": 2020,
        "name": "glowV2.safetensors",
        "type": "Model",
        "primary": True,
        "hashes": {"SHA256": "AB12"},
    }


def glow_model() -> dict:
    return {
        "id": 101,
        "type": "LORA",
        "name": "Glow",
        "creator": {"username": "maker"},
        "modelVersions": [
            {"id": 202, "name": "v2", "baseModel": "Illustrious", "files": [glow_file()]},
            {
                "id": 201,
                "name": "v1",
                "baseModel": "Pony",
                "files": [
                    {
                        "id": 2010,
                        "name": "glowV1.safetensors",
                        "type": "Model",
                        "primary": True,
                        "hashes": {"SHA256": "CD34"},
                    }
                ],
            },
        ],
    }


class FakeClient:
    def __init__(self, models: dict) -> None:
        self.models = {str(k): v for k, v in models.items()}
        self.calls: list = []

    def get_model(self, model_id, *, raise_on_error=False):
        self.calls.append(str(model_id))
        value = self.models[str(model_id)]
        if isinstance(value, Exception):
            raise value
        return value


class FakeGenerator:
    """Records every batch; answers every question with one answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list = []
        self.max_lengths: list = []

    def generate(self, messages, max_lengths=None):
        self.calls.append(list(messages))
        self.max_lengths.append(max_lengths)
        return [self.answer for _message in messages]


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


class DownloadTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "loras"
        self.pony = self.root / "pony"
        self.ill = self.root / "ill"
        for i in range(3):
            touch(self.pony / f"maker - a{i} - v1 (pony).safetensors")
            touch(self.ill / f"maker - b{i} - v1 (ill).safetensors")
        touch(self.root / "GR" / "maker - c0 - v1 (ill).safetensors")
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.cache = AnswerCache(self.data)
        self.reference = self.pony / "maker - a0 - v1 (pony).safetensors"

    def choose(
        self, file=None, *, reference_folder=None, named=None, fallback_code="ill"
    ) -> Target:
        return choose_target(
            file if file is not None else glow_file(),
            reference_folder=reference_folder if reference_folder is not None else self.pony,
            type_roots=[self.root],
            named=named,
            fallback_code=fallback_code,
        )

    def name(self, generator, *, model_type="lora", stop=lambda: False):
        model = glow_model()
        return ai_stem(
            "101",
            model["modelVersions"][0],
            glow_file(),
            model_type=model_type,
            reference=self.reference,
            client=FakeClient({101: model}),
            generator=generator,
            decisions=self.decisions,
            base_codes=self.base_codes,
            cache=self.cache,
            clip_name="qwen3.safetensors",
            should_stop=stop,
        )

    # --- base_folder ---------------------------------------------------------

    def test_base_folder_is_the_home_beside_the_reference_folder(self) -> None:
        self.assertEqual(base_folder([self.root], self.pony, "ill"), self.ill)

    def test_base_folder_below_a_root_reference(self) -> None:
        self.assertEqual(base_folder([self.root], self.root, "ill"), self.ill)

    def test_a_base_without_a_home_has_no_folder(self) -> None:
        self.assertIsNone(base_folder([self.root], self.pony, "zit"))

    def test_a_weights_file_and_its_info_count_once(self) -> None:
        # Two pony files, each with its info: not the three files a home needs.
        folder = self.tmp / "ckpt" / "pony"
        for i in range(2):
            touch(folder / f"maker - d{i} - v1 (pony).safetensors")
            touch(folder / f"maker - d{i} - v1 (pony).civitai.info")
        self.assertIsNone(base_folder([self.tmp / "ckpt"], folder, "pony"))

    def test_nested_roots_count_a_folder_once(self) -> None:
        folder = self.tmp / "ckpt" / "pony"
        for i in range(2):
            touch(folder / f"maker - d{i} - v1 (pony).safetensors")
        self.assertIsNone(base_folder([self.tmp / "ckpt", folder], folder, "pony"))

    # --- stem_taken and inside_roots -----------------------------------------

    def test_stem_taken_by_weights_info_or_preview_in_any_case(self) -> None:
        folder = self.tmp / "taken"
        touch(folder / "GLOW.CIVITAI.INFO")
        self.assertTrue(stem_taken(folder, "glow"))
        other = self.tmp / "preview"
        touch(other / "glow.preview.png")
        self.assertTrue(stem_taken(other, "glow"))
        weights = self.tmp / "weights"
        touch(weights / "Glow.SafeTensors")
        self.assertTrue(stem_taken(weights, "glow"))
        part = self.tmp / "part"
        touch(part / "glow.safetensors.part")
        self.assertFalse(stem_taken(part, "glow"))

    def test_inside_roots(self) -> None:
        self.assertTrue(inside_roots(self.pony, [self.root]))
        self.assertTrue(inside_roots(self.root, [self.root]))
        self.assertFalse(inside_roots(self.tmp / "checkpoints", [self.root]))
        self.assertFalse(inside_roots(self.root / "pony" / ".." / ".." / "x", [self.root]))

    # --- choose_target -------------------------------------------------------

    def test_the_ai_name_goes_to_the_bases_folder(self) -> None:
        target = self.choose(named=("maker - glow - v2 (ill)", "ill"), fallback_code="pony")
        self.assertEqual(target, Target(self.ill, "maker - glow - v2 (ill)", ".safetensors", True))
        self.assertEqual(target.path, self.ill / "maker - glow - v2 (ill).safetensors")

    def test_without_a_name_civitais_name_goes_to_the_bases_folder(self) -> None:
        self.assertEqual(self.choose(), Target(self.ill, "glowV2", ".safetensors", False))

    def test_a_base_without_a_home_gets_its_own_folder_beside_a_sorted_one(self) -> None:
        # The old version's folder is named after its base code: one folder per base.
        self.assertEqual(self.choose(fallback_code="zit").folder, self.root / "zit")
        target = self.choose(named=("maker - glow - v2 (illx)", "illx"), fallback_code="")
        self.assertEqual(target, Target(self.root / "illx", "maker - glow - v2 (illx)", ".safetensors", True))
        self.assertFalse((self.root / "illx").exists())

    def test_a_base_without_a_home_stays_beside_the_old_version_in_an_unsorted_folder(self) -> None:
        self.assertEqual(self.choose(reference_folder=self.root / "GR", fallback_code="zit").folder, self.root / "GR")
        self.assertEqual(self.choose(reference_folder=self.root, fallback_code="zit").folder, self.root)

    def test_a_taken_ai_name_falls_back_to_civitais_name(self) -> None:
        touch(self.ill / "maker - glow - v2 (ill).civitai.info")
        target = self.choose(named=("maker - glow - v2 (ill)", "ill"))
        self.assertEqual(target, Target(self.ill, "glowV2", ".safetensors", False))

    def test_civitais_name_taken_too_is_refused(self) -> None:
        touch(self.ill / "maker - glow - v2 (ill).safetensors")
        touch(self.ill / "glowv2.preview.png")
        with self.assertRaises(DownloadError) as caught:
            self.choose(named=("maker - glow - v2 (ill)", "ill"))
        self.assertEqual(str(caught.exception), "A file named glowV2.safetensors is already there.")

    def test_a_civitai_file_name_with_a_path_is_refused(self) -> None:
        for name in (
            "../glowV2.safetensors",
            "sub\\glowV2.safetensors",
            "glowV2.exe",
            "glow:V2.safetensors",
        ):
            with self.subTest(name=name):
                file = dict(glow_file(), name=name)
                with self.assertRaises(DownloadError) as caught:
                    self.choose(file, named=("maker - glow - v2 (ill)", "ill"))
                self.assertEqual(
                    str(caught.exception), f"Civitai's file name can't be used: {name}"
                )

    def test_a_reference_outside_the_roots_is_refused(self) -> None:
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        with self.assertRaises(DownloadError) as caught:
            self.choose(reference_folder=outside)
        self.assertEqual(str(caught.exception), "That folder isn't one of your model folders.")

    def test_the_suffix_is_kept_as_civitai_wrote_it(self) -> None:
        target = self.choose(dict(glow_file(), name="glowV2.SafeTensors"))
        self.assertEqual(target, Target(self.ill, "glowV2", ".SafeTensors", False))

    # --- ai_stem -------------------------------------------------------------

    def test_ai_stem_names_one_release_as_a_dry_run_would(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        generator = FakeGenerator(ANSWER)
        self.assertEqual(self.name(generator), ("maker - glow - v2 (ill)", "ill", ""))
        self.assertEqual(len(generator.calls), 1)

    def test_ai_stem_names_a_new_base_with_the_ais_code(self) -> None:
        generator = FakeGenerator(ANSWER + "\nbase Illustrious: illx")
        self.assertEqual(self.name(generator), ("maker - glow - v2 (illx)", "illx", "Illustrious"))
        # Saved only once the download uses the name.
        self.assertIsNone(self.base_codes.code("Illustrious"))

    def test_ai_stem_gives_none_for_a_new_base_without_a_code(self) -> None:
        self.assertIsNone(self.name(FakeGenerator(ANSWER)))

    def test_ai_stem_gives_none_when_the_name_needs_review(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        generator = FakeGenerator("title: shiny glow\noriginal:\nversion 1: v2")
        with mock.patch.object(download_target, "needs_review", wraps=needs_review) as check:
            self.assertIsNone(self.name(generator))
        self.assertIn("invented:shiny", check.call_args[0][0])

    def test_ai_stem_gives_none_for_a_type_the_renamer_skips(self) -> None:
        generator = FakeGenerator(ANSWER)
        self.assertIsNone(self.name(generator, model_type="vae"))
        self.assertEqual(generator.calls, [])

    def test_ai_stem_gives_none_once_stopped(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        generator = FakeGenerator(ANSWER)
        self.assertIsNone(self.name(generator, stop=lambda: True))
        self.assertEqual(generator.calls, [])


if __name__ == "__main__":
    unittest.main()
