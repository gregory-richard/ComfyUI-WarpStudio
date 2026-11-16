"""Dry-run rows carry what the Rename card shows: a preview, the mature flag and the model type."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore
from comfy_updater.renamer import collect_entries, classify, run_dry_run


def write_entry(folder: Path, stem: str, model_id, version_id, sha256: str, images=None, model_nsfw=None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    info = {"modelId": model_id, "id": version_id, "files": [{"id": 1, "type": "Model", "hashes": {"SHA256": sha256}}]}
    if images is not None:
        info["images"] = images
    if model_nsfw is not None:
        info["model"] = {"nsfw": model_nsfw}
    (folder / (stem + ".civitai.info")).write_text(json.dumps(info), encoding="utf-8")
    (folder / (stem + ".safetensors")).write_bytes(b"weights")


def live_model(model_id, versions, model_type="LORA", nsfw=None) -> dict:
    model = {
        "id": model_id,
        "name": f"Model {model_id}",
        "type": model_type,
        "creator": {"username": "maker"},
        "description": "",
        "modelVersions": versions,
    }
    if nsfw is not None:
        model["nsfw"] = nsfw
    return model


def live_version(version_id, sha256, images=None) -> dict:
    return {
        "id": version_id,
        "name": "v2",
        "baseModel": "SDXL 1.0",
        "files": [{"id": version_id * 10, "name": "f.safetensors", "type": "Model", "hashes": {"SHA256": sha256.upper()}}],
        "images": images or [],
    }


class FakeClient:
    def __init__(self, models: dict) -> None:
        self.models = {str(k): v for k, v in models.items()}

    def get_model(self, model_id, *, raise_on_error=False):
        return self.models[str(model_id)]


class FakeGenerator:
    def generate(self, messages, max_lengths=None):
        return ["title: thing\noriginal:\nversion 1: v2" for _ in messages]


def no_progress(current, total, message):
    pass


IMG = [{"type": "image", "url": "https://img/local.jpeg"}]
VIDEO_ONLY = [{"type": "video", "url": "https://img/live.mp4"}]


class PreviewFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.lora = self.root / "loras"
        self.roots = {"lora": [self.lora]}
        self.data = self.root / "data"
        self.data.mkdir()
        self.decisions = DecisionStore(self.data)

    def classify_rows(self, models):
        entries = collect_entries(self.roots)
        _groups, rows = classify(entries, FakeClient(models), self.decisions, progress=no_progress, control=None)
        return {row["oldStem"]: row for row in rows}

    def test_kept_row_uses_the_info_file_preview_and_its_model_flag(self) -> None:
        write_entry(self.lora, "a", 1, 11, "aa", images=IMG, model_nsfw=True)
        rows = self.classify_rows({1: live_model(1, [live_version(12, "zz")])})  # release gone
        row = rows["a"]
        self.assertEqual(row["status"], "kept")
        self.assertEqual((row["previewUrl"], row["previewType"]), ("https://img/local.jpeg", "image"))
        self.assertTrue(row["nsfw"])
        self.assertEqual(row["modelType"], "lora")

    def test_live_version_images_when_the_info_file_has_none(self) -> None:
        write_entry(self.lora, "a", 1, 11, "aa")
        write_entry(self.lora, "b", 1, 11, "aa")  # a duplicate of "a"
        rows = self.classify_rows({1: live_model(1, [live_version(11, "aa", images=VIDEO_ONLY)], nsfw=False)})
        row = rows["b"]
        self.assertEqual(row["status"], "duplicate")
        self.assertEqual((row["previewUrl"], row["previewType"]), ("https://img/live.mp4", "video"))
        self.assertFalse(row["nsfw"])

    def test_live_flag_wins_over_the_info_file(self) -> None:
        write_entry(self.lora, "a", 1, 11, "aa", model_nsfw=False)
        rows = self.classify_rows({1: live_model(1, [live_version(11, "aa")], model_type="Checkpoint", nsfw=True)})
        row = rows["a"]
        self.assertEqual(row["reason"], "type-mismatch")
        self.assertTrue(row["nsfw"])
        self.assertEqual((row["previewUrl"], row["previewType"]), ("", "image"))

    def test_odd_images_give_no_preview(self) -> None:
        write_entry(self.lora, "a", 1, 11, "aa", images=5)
        rows = self.classify_rows({1: live_model(1, [live_version(12, "zz")])})
        self.assertEqual((rows["a"]["previewUrl"], rows["a"]["previewType"]), ("", "image"))

    def test_named_rows_carry_the_fields(self) -> None:
        write_entry(self.lora, "maker - thing - v2 (sdxl)", 1, 11, "aa", model_nsfw=True)
        base_codes = BaseCodeStore(self.data)
        base_codes.set("SDXL 1.0", "sdxl")
        _summary, rows = run_dry_run(
            self.roots,
            FakeClient({1: live_model(1, [live_version(11, "aa", images=IMG)])}),
            FakeGenerator(),
            decisions=self.decisions,
            base_codes=base_codes,
            cache=AnswerCache(self.data),
            clip_name="qwen3.safetensors",
            progress=no_progress,
            emit=lambda row: None,
            control=None,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn(row["status"], ("change", "unchanged"))
        self.assertEqual((row["previewUrl"], row["previewType"]), ("https://img/local.jpeg", "image"))
        self.assertTrue(row["nsfw"])  # the live model has no flag: the info file's counts
        self.assertEqual(row["modelType"], "lora")


if __name__ == "__main__":
    unittest.main()
