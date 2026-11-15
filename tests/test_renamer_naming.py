from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from comfy_updater.comfy_queue import NamingModelError
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore
from comfy_updater import renamer
from comfy_updater.renamer import classify, collect_entries, name_groups, run_dry_run


def write_safetensors(path: Path, tensors: dict) -> None:
    header = {name: {"dtype": dtype, "shape": list(shape), "data_offsets": [0, 0]} for name, (dtype, shape) in tensors.items()}
    raw = json.dumps(header).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(raw)))
        handle.write(raw)
        handle.write(b"\x00" * 16)


def write_entry(folder: Path, stem: str, model_id, version_id, sha256: str, weights=True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    info = {"modelId": model_id, "id": version_id, "files": [{"id": 1, "type": "Model", "hashes": {"SHA256": sha256}}]}
    (folder / (stem + ".civitai.info")).write_text(json.dumps(info), encoding="utf-8")
    path = folder / (stem + ".safetensors")
    if weights is True:
        path.write_bytes(b"weights")
    elif callable(weights):
        weights(path)
    return path


def live_model(model_id, name, creator, model_type, versions, description="") -> dict:
    return {
        "id": model_id,
        "name": name,
        "type": model_type,
        "creator": {"username": creator},
        "description": description,
        "modelVersions": versions,
    }


def live_version(version_id, name, base, files) -> dict:
    return {"id": version_id, "name": name, "baseModel": base, "files": files}


def live_file(file_id, sha256, name="", file_type="Model", fp=None) -> dict:
    item = {"id": file_id, "name": name or f"file{file_id}.safetensors", "type": file_type, "hashes": {"SHA256": sha256.upper()}}
    if fp:
        item["metadata"] = {"fp": fp}
    return item


class FakeClient:
    def __init__(self, models: dict) -> None:
        self.models = {str(k): v for k, v in models.items()}

    def get_model(self, model_id, *, raise_on_error=False):
        return self.models[str(model_id)]


class FakeGenerator:
    """Records every batch; answers from a script per model title found in the message."""

    def __init__(self, script: dict, fail_on_call: int = 0) -> None:
        self.script = {key: list(value) if isinstance(value, list) else [value] for key, value in script.items()}
        self.calls: list = []
        self.fail_on_call = fail_on_call

    def generate(self, messages):
        self.calls.append(list(messages))
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            raise NamingModelError("ComfyUI failed in TextGenerateV2: out of memory")
        answers = []
        for message in messages:
            for key, queue in self.script.items():
                if f"Model title: {key}\n" in message:
                    answers.append(queue.pop(0) if len(queue) > 1 else queue[0])
                    break
            else:
                answers.append("")
        return answers

    @property
    def messages(self):
        return [m for batch in self.calls for m in batch]


def no_progress(current, total, message):
    pass


LUMEGRID = "Lumegrid (Krea 2 / Z-Image / Qwen) - Realism LoRA"
QSD = "QSD Speed LoRA [SDXL, Pony, Illustrious]"
AURA = "Aura By Calm Ridge Krea2"


class NamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.lora = self.root / "loras"
        self.unet = self.root / "unet"
        self.roots = {"lora": [self.lora], "unet": [self.unet]}
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.base_codes.set("Krea 2", "krea2")
        self.base_codes.set("SDXL 1.0", "sdxl")
        self.cache = AnswerCache(self.data)
        self.emitted: list = []

    def dry_run(self, models: dict, generator, **kwargs):
        return run_dry_run(
            self.roots,
            FakeClient(models),
            generator,
            decisions=self.decisions,
            base_codes=self.base_codes,
            cache=self.cache,
            clip_name="qwen3.safetensors",
            progress=no_progress,
            emit=self.emitted.append,
            control=None,
            **kwargs,
        )

    def lumegrid(self) -> dict:
        write_entry(self.lora, "tidemint - lumegrid (krea2)", 100, 1001, "aa")
        return {
            100: live_model(100, LUMEGRID, "tidemint", "LORA", [
                live_version(1001, "Krea 2 Zest Preview FIX", "Krea 2", [live_file(5001, "aa")]),
            ]),
        }

    def row(self, rows, stem_start: str) -> dict:
        matches = [r for r in rows if r["oldStem"].startswith(stem_start)]
        self.assertEqual(len(matches), 1, rows)
        return matches[0]

    def test_lumegrid_row(self) -> None:
        models = self.lumegrid()
        generator = FakeGenerator({LUMEGRID: "title: lumegrid realism\noriginal:\nversion 1: zest preview fix"})
        summary, rows = self.dry_run(models, generator)
        row = self.row(rows, "tidemint - lumegrid")
        self.assertEqual(row["newStem"], "tidemint - lumegrid realism - zest preview fix (krea2)")
        self.assertEqual(row["status"], "change")
        self.assertEqual(row["flags"], [])
        self.assertTrue(row["selected"])
        self.assertEqual(row["parts"], {
            "creator": "tidemint", "title": "lumegrid realism", "version": "zest preview fix",
            "precision": "", "base": "krea2",
        })
        self.assertEqual(row["changed"], ["title", "version"])
        self.assertEqual(row["fileKey"], "file:5001")
        # Carried for re-assembly after an edit and for the sidecar title after apply.
        self.assertEqual(row["baseModel"], "Krea 2")
        self.assertEqual(row["liveTitle"], LUMEGRID)
        self.assertEqual(row["key"], str(self.lora / "tidemint - lumegrid (krea2).safetensors").lower())
        self.assertEqual(summary["entries"], 1)
        self.assertEqual(summary["change"], 1)
        self.assertEqual(summary["flagged"], 0)
        self.assertEqual(self.emitted, rows)
        message = generator.messages[0]
        self.assertIn("Creator: tidemint\n", message)
        self.assertIn("1. Krea 2 Zest Preview FIX [base: Krea 2]", message)
        self.assertNotIn("New base models", message)

    def qsd(self) -> dict:
        write_entry(self.lora, "qsd 4step fp16 (sdxl)", 200, 2001, "bb")
        return {
            200: live_model(200, QSD, "7qX", "LORA", [
                live_version(2001, "qsd_sdxl_4step_lora_fp16", "SDXL 1.0", [live_file(6001, "bb")]),
            ], description="<p>Re-upload. <b>Original</b> model by fernwick, see the paper.</p>"),
        }

    def test_invented_version_gets_one_retry_then_flag(self) -> None:
        models = self.qsd()
        answer = "title: qsd speed\noriginal:\nversion 1: v4"
        generator = FakeGenerator({QSD: [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(generator.messages), 2)
        self.assertIn("\"v4\"", generator.messages[1])
        self.assertNotIn("\"v4\"", generator.messages[0])
        row = self.row(rows, "qsd")
        self.assertEqual(row["newStem"], "7qx - qsd speed - v4 (sdxl)")
        self.assertIn("invented:v4", row["flags"])
        self.assertFalse(row["selected"])
        self.assertEqual(self.cache._answers, {})

    def test_retry_that_fixes_the_answer_is_cached(self) -> None:
        models = self.qsd()
        generator = FakeGenerator({QSD: ["title: qsd speed\noriginal:\nversion 1: v4",
                                         "title: qsd speed\noriginal:\nversion 1: 4step"]})
        _summary, rows = self.dry_run(models, generator)
        row = self.row(rows, "qsd")
        self.assertEqual(row["newStem"], "7qx - qsd speed - 4step (sdxl)")
        self.assertEqual(row["flags"], [])
        self.assertTrue(row["selected"])
        self.assertEqual(len(self.cache._answers), 1)

    def test_empty_output_is_unparsed_retried_and_never_cached(self) -> None:
        models = self.qsd()
        generator = FakeGenerator({})  # every answer ""
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        row = self.row(rows, "qsd")
        self.assertIn("unparsed", row["flags"])
        self.assertFalse(row["selected"])
        self.assertEqual(self.cache._answers, {})

    def test_maker_from_description_until_confirmed(self) -> None:
        models = self.qsd()
        answer = "title: qsd speed\noriginal: fernwick\nversion 1: 4step"
        _summary, rows = self.dry_run(models, FakeGenerator({QSD: answer}))
        row = self.row(rows, "qsd")
        self.assertEqual(row["parts"]["creator"], "fernwick")
        self.assertEqual(row["newStem"], "fernwick - qsd speed - 4step (sdxl)")
        self.assertIn("maker-from-description", row["flags"])
        self.assertFalse(row["selected"])

        self.decisions.set_model(200, creator="fernwick")
        self.emitted = []
        _summary, rows = self.dry_run(models, FakeGenerator({QSD: answer}))
        row = self.row(rows, "qsd")
        self.assertEqual(row["parts"]["creator"], "fernwick")
        self.assertNotIn("maker-from-description", row["flags"])
        self.assertTrue(row["selected"])

    def test_maker_not_in_description_is_retried_and_flagged(self) -> None:
        models = self.qsd()
        answer = "title: qsd speed\noriginal: stabilityai\nversion 1: 4step"
        generator = FakeGenerator({QSD: [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        self.assertIn("\"stabilityai\"", generator.messages[1])
        row = self.row(rows, "qsd")
        self.assertIn("maker-not-in-description", row["flags"])

    def test_maker_check_reads_the_credit_lines_past_the_excerpt(self) -> None:
        models = self.qsd()
        models[200]["description"] = "<p>" + "filler words " * 400 + "</p><p>Original by fernwick.</p>"
        answer = "title: qsd speed\noriginal: fernwick\nversion 1: 4step"
        generator = FakeGenerator({QSD: answer})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 1)
        self.assertIn("Credit lines (only for the original line): Original by fernwick.", generator.messages[0])
        self.assertNotIn("maker-not-in-description", self.row(rows, "qsd")["flags"])

    def test_maker_only_outside_the_credit_lines_is_retried_and_flagged(self) -> None:
        models = self.qsd()
        # "fernwick" is in the description, but not in a sentence the AI saw.
        models[200]["description"] = "<p>Thanks to fernwick for the paper.</p><p>Re-upload, see the paper.</p>"
        answer = "title: qsd speed\noriginal: fernwick\nversion 1: 4step"
        generator = FakeGenerator({QSD: [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        self.assertIn("maker-not-in-description", self.row(rows, "qsd")["flags"])

    def test_decision_title_wins_and_locked_matching_stem_is_unchanged(self) -> None:
        models = self.lumegrid()
        answer = "title: lumegrid realism\noriginal:\nversion 1: zest preview fix"
        self.decisions.set_model(100, title="lumegrid")
        _summary, rows = self.dry_run(models, FakeGenerator({LUMEGRID: answer}))
        row = self.row(rows, "tidemint")
        self.assertEqual(row["newStem"], "tidemint - lumegrid - zest preview fix (krea2)")
        self.assertEqual(row["changed"], ["version"])

        self.decisions.lock_applied(100, "lumegrid", "tidemint", {"1001": ""}, {})
        # A ComfyUI restart: the lock, including its empty version, must survive the reload.
        self.decisions = DecisionStore(self.data)
        self.emitted = []
        summary, rows = self.dry_run(models, FakeGenerator({LUMEGRID: answer}))
        row = self.row(rows, "tidemint")
        self.assertEqual(row["newStem"], "tidemint - lumegrid (krea2)")
        self.assertEqual(row["status"], "unchanged")
        self.assertFalse(row["selected"])
        self.assertEqual(row["changed"], [])
        self.assertEqual(summary["unchanged"], 1)

    def test_cache_hit_skips_the_ai(self) -> None:
        models = self.lumegrid()
        answer = "title: lumegrid realism\noriginal:\nversion 1: zest preview fix"
        self.dry_run(models, FakeGenerator({LUMEGRID: answer}))
        generator = FakeGenerator({LUMEGRID: answer})
        self.emitted = []
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(generator.calls, [])
        self.assertEqual(self.row(rows, "tidemint")["newStem"], "tidemint - lumegrid realism - zest preview fix (krea2)")

    def test_cache_key_includes_the_naming_model(self) -> None:
        models = self.lumegrid()
        answer = "title: lumegrid realism\noriginal:\nversion 1: zest preview fix"
        self.dry_run(models, FakeGenerator({LUMEGRID: answer}))
        generator = FakeGenerator({LUMEGRID: answer})
        groups, _rows = classify(collect_entries(self.roots), FakeClient(models), self.decisions, progress=no_progress)
        name_groups(groups, generator, decisions=self.decisions, base_codes=self.base_codes, cache=self.cache,
                    clip_name="gemma.safetensors", progress=no_progress, emit=self.emitted.append, control=None)
        self.assertEqual(len(generator.calls), 1)

    def test_two_groups_with_the_same_stem_collide(self) -> None:
        write_entry(self.lora, "a one", 300, 3001, "c1")
        write_entry(self.lora, "a two", 301, 3011, "c2")
        models = {
            300: live_model(300, "Same Thing", "maker", "LORA", [live_version(3001, "v1", "Krea 2", [live_file(1, "c1")])]),
            301: live_model(301, "Same Thing!", "maker", "LORA", [live_version(3011, "v1", "Krea 2", [live_file(2, "c2")])]),
        }
        answer = "title: same thing\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Same Thing": answer, "Same Thing!": answer})
        summary, rows = self.dry_run(models, generator, batch_size=1)
        for stem in ("a one", "a two"):
            row = self.row(rows, stem)
            self.assertEqual(row["newStem"], "maker - same thing - v1 (krea2)")
            self.assertIn("collision", row["flags"])
            self.assertFalse(row["selected"])
        self.assertEqual(summary["flagged"], 2)
        # The earlier group's row was emitted before the collision was known; it is updated in place.
        self.assertTrue(all("collision" in r["flags"] for r in self.emitted))

    def test_existing_file_outside_the_rename_set(self) -> None:
        models = self.lumegrid()
        (self.lora / "tidemint - lumegrid realism - zest preview fix (krea2).safetensors").write_bytes(b"x")
        answer = "title: lumegrid realism\noriginal:\nversion 1: zest preview fix"
        _summary, rows = self.dry_run(models, FakeGenerator({LUMEGRID: answer}))
        row = self.row(rows, "tidemint - lumegrid (krea2)")
        self.assertIn("exists", row["flags"])
        self.assertFalse(row["selected"])

    def test_case_only_change_is_not_a_collision(self) -> None:
        write_entry(self.lora, "Maker - BustlineXL - v1 (krea2)", 400, 4001, "dd")
        models = {400: live_model(400, "BustlineXL", "Maker", "LORA", [live_version(4001, "v1", "Krea 2", [live_file(1, "dd")])])}
        _summary, rows = self.dry_run(models, FakeGenerator({"BustlineXL": "title: bustlinexl\noriginal:\nversion 1: v1"}))
        row = self.row(rows, "Maker")
        self.assertEqual(row["newStem"], "maker - bustlinexl - v1 (krea2)")
        self.assertEqual(row["status"], "change")
        self.assertEqual(row["flags"], [])
        self.assertTrue(row["selected"])

    def test_naming_model_error_stops_the_run_after_emitted_rows(self) -> None:
        models = self.lumegrid()
        models.update(self.qsd())
        generator = FakeGenerator(
            {LUMEGRID: "title: lumegrid realism\noriginal:\nversion 1: zest preview fix",
             QSD: "title: qsd speed\noriginal:\nversion 1: 4step"},
            fail_on_call=2,
        )
        with self.assertRaises(NamingModelError) as caught:
            self.dry_run(models, generator, batch_size=1)
        self.assertIn("out of memory", str(caught.exception))
        first_title = LUMEGRID if f"Model title: {LUMEGRID}\n" in generator.calls[0][0] else QSD
        first_id, first_stem, other_id = (100, "tidemint", 200) if first_title == LUMEGRID else (200, "qsd", 100)
        named = [r for r in self.emitted if r["status"] in ("change", "unchanged")]
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0]["modelId"], str(first_id))
        self.assertTrue(named[0]["oldStem"].startswith(first_stem))
        cached = AnswerCache(self.data)._answers
        self.assertTrue(any(key.startswith(f"{first_id}:") for key in cached), cached)
        self.assertFalse(any(key.startswith(f"{other_id}:") for key in cached), cached)

    def aura(self, folder: Path, model_type: str, header_dtype="I8") -> dict:
        write_entry(folder, "aura v4", 500, 5001, "ee",
                    weights=lambda p: write_safetensors(p, {"w": (header_dtype, [4096, 4096]), "s": ("F32", [4096])}))
        return {500: live_model(500, AURA, "calm_ridge", model_type, [
            live_version(5001, "v4.0 INT8 Expanded", "Krea 2", [live_file(1, "ee", fp="bf16")]),
        ])}

    def test_unet_gets_precision_from_its_header(self) -> None:
        models = self.aura(self.unet, "Checkpoint")
        answer = "title: aura\noriginal:\nversion 1: v4 expanded"
        _summary, rows = self.dry_run(models, FakeGenerator({AURA: answer}))
        row = self.row(rows, "aura v4")
        self.assertEqual(row["newStem"], "calm_ridge - aura - v4 expanded int8 (krea2)")
        self.assertEqual(row["parts"]["precision"], "int8")

    def test_lora_gets_no_precision(self) -> None:
        models = self.aura(self.lora, "LORA")
        answer = "title: aura\noriginal:\nversion 1: v4 expanded"
        _summary, rows = self.dry_run(models, FakeGenerator({AURA: answer}))
        self.assertEqual(self.row(rows, "aura v4")["newStem"], "calm_ridge - aura - v4 expanded (krea2)")

    def test_metadata_only_unet_uses_civitai_fp_for_a_single_main_file(self) -> None:
        write_entry(self.unet, "aura v4", 500, 5001, "ee", weights=False)
        models = {500: live_model(500, AURA, "calm_ridge", "Checkpoint", [
            live_version(5001, "v4.0 INT8 Expanded", "Krea 2", [live_file(1, "ee", fp="fp8"), live_file(2, "ff", file_type="VAE")]),
        ])}
        answer = "title: aura\noriginal:\nversion 1: v4 expanded"
        _summary, rows = self.dry_run(models, FakeGenerator({AURA: answer}))
        self.assertEqual(self.row(rows, "aura v4")["newStem"], "calm_ridge - aura - v4 expanded fp8 (krea2)")

    def test_metadata_only_unet_with_two_main_files_gets_no_precision(self) -> None:
        write_entry(self.unet, "aura v4", 500, 5001, "ee", weights=False)
        models = {500: live_model(500, AURA, "calm_ridge", "Checkpoint", [
            live_version(5001, "v4.0 INT8 Expanded", "Krea 2", [live_file(1, "ee", fp="fp8"), live_file(2, "ff", fp="bf16")]),
        ])}
        answer = "title: aura\noriginal:\nversion 1: v4 expanded"
        _summary, rows = self.dry_run(models, FakeGenerator({AURA: answer}))
        self.assertEqual(self.row(rows, "aura v4")["newStem"], "calm_ridge - aura - v4 expanded (krea2)")

    def test_new_base_code_from_the_answer(self) -> None:
        write_entry(self.lora, "x", 600, 6001, "gg")
        models = {600: live_model(600, "Glow", "maker", "LORA", [live_version(6001, "v1", "Flux.2 Klein 9B", [live_file(1, "gg")])])}
        generator = FakeGenerator({"Glow": "title: glow\noriginal:\nversion 1: v1\nbase Flux.2 Klein 9B: flux2k9b"})
        _summary, rows = self.dry_run(models, generator)
        self.assertIn("New base models: Flux.2 Klein 9B", generator.messages[0])
        row = self.row(rows, "x")
        self.assertEqual(row["newStem"], "maker - glow - v1 (flux2k9b)")
        self.assertIn("new-base-code", row["flags"])
        self.assertIsNone(self.base_codes.code("Flux.2 Klein 9B"))

    def test_seed_runs_before_naming(self) -> None:
        base_codes = BaseCodeStore(self.root / "fresh")
        self.base_codes = base_codes
        write_entry(self.lora, "maker - glow - v1 (ill)", 600, 6001, "gg")
        models = {600: live_model(600, "Glow", "maker", "LORA", [live_version(6001, "v1", "Illustrious", [live_file(1, "gg")])])}
        generator = FakeGenerator({"Glow": "title: glow\noriginal:\nversion 1: v1"})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(base_codes.code("Illustrious"), "ill")
        self.assertNotIn("New base models", generator.messages[0])
        self.assertEqual(self.row(rows, "maker")["status"], "unchanged")

    def test_unsafe_parts_are_flagged(self) -> None:
        write_entry(self.lora, "x", 700, 7001, "hh")
        models = {700: live_model(700, "Glow \U0001F525 Up", "maker", "LORA", [live_version(7001, "v1", "Krea 2", [live_file(1, "hh")])])}
        answer = "title: glow \U0001F525 up -\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Glow \U0001F525 Up": [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        flags = self.row(rows, "x")["flags"]
        self.assertIn("non-ascii", flags)
        self.assertIn("separator", flags)

    def test_official_file_and_file_not_in_release(self) -> None:
        write_entry(self.unet, "Flux1-Dev-FP8", 800, 8001, "ii")
        write_entry(self.lora, "stale", 801, 8011, "zz")
        models = {
            800: live_model(800, "FLUX.1 dev", "bflmirror", "Checkpoint", [
                live_version(8001, "fp8", "Krea 2", [live_file(1, "ii", name="flux1-dev-fp8.safetensors")])]),
            801: live_model(801, "Stale", "maker", "LORA", [live_version(8011, "v1", "Krea 2", [live_file(2, "other")])]),
        }
        generator = FakeGenerator({"FLUX.1 dev": "title: flux.1 dev\noriginal:\nversion 1:",
                                   "Stale": "title: stale\noriginal:\nversion 1: v1"})
        _summary, rows = self.dry_run(models, generator)
        self.assertIn("official-file", self.row(rows, "Flux1")["flags"])
        stale = self.row(rows, "stale")
        self.assertIn("file-not-in-release", stale["flags"])
        self.assertTrue(stale["fileKey"].startswith("path:"))

    def test_odd_model_data_becomes_error_rows(self) -> None:
        models = self.lumegrid()
        write_entry(self.unet, "odd", 900, 9001, "jj")
        models[900] = live_model(900, "Odd", "maker", "Checkpoint", [live_version(9001, "v1", "Krea 2", [live_file(1, "jj")])])
        generator = FakeGenerator({LUMEGRID: "title: lumegrid realism\noriginal:\nversion 1: zest preview fix",
                                   "Odd": "title: odd\noriginal:\nversion 1: v1"})
        # Only the checkpoint/unet entry reads its precision; make that blow up.
        with mock.patch.object(renamer, "read_precision", side_effect=RuntimeError("unexpected data")):
            summary, rows = self.dry_run(models, generator)
        self.assertEqual(self.row(rows, "odd")["reason"], "unexpected data")
        self.assertEqual(self.row(rows, "odd")["status"], "error")
        self.assertEqual(self.row(rows, "tidemint")["status"], "change")
        self.assertEqual(summary["error"], 1)

    def test_batches_hold_batch_size_messages_and_retries_go_next(self) -> None:
        for n in range(3):
            write_entry(self.lora, f"m{n}", 1000 + n, 10000 + n, f"k{n}")
        models = {
            1000 + n: live_model(1000 + n, f"Model {n}", "maker", "LORA",
                                 [live_version(10000 + n, "v1", "Krea 2", [live_file(n + 1, f"k{n}")])])
            for n in range(3)
        }
        generator = FakeGenerator({
            "Model 0": ["title: model 0 extra\noriginal:\nversion 1: v1", "title: model 0\noriginal:\nversion 1: v1"],
            "Model 1": "title: model 1\noriginal:\nversion 1: v1",
            "Model 2": "title: model 2\noriginal:\nversion 1: v1",
        })
        _summary, rows = self.dry_run(models, generator, batch_size=2)
        self.assertEqual([len(batch) for batch in generator.calls], [2, 2])
        self.assertIn("Your last answer had these problems", generator.calls[1][0])
        self.assertIn("Model title: Model 2\n", generator.calls[1][1])
        self.assertEqual(self.row(rows, "m0")["newStem"], "maker - model 0 - v1 (krea2)")


if __name__ == "__main__":
    unittest.main()
