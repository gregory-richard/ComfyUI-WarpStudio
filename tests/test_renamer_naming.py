from __future__ import annotations

import json
from pathlib import Path
import re
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

from comfy_updater.civitai_client import CivitaiRequestError
from comfy_updater.comfy_queue import NamingModelError
from comfy_updater.naming_ai import GROUP_INTRO
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


def write_entry(folder: Path, stem: str, model_id, version_id, sha256: str, weights=True, base=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    info = {"modelId": model_id, "id": version_id, "files": [{"id": 1, "type": "Model", "hashes": {"SHA256": sha256}}]}
    if base is not None:
        info["baseModel"] = base
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


GROUP_HEADER = re.compile(r"^Model (\d+):\n", re.MULTILINE)


class FakeClient:
    def __init__(self, models: dict) -> None:
        self.models = {str(k): v for k, v in models.items()}

    def get_model(self, model_id, *, raise_on_error=False):
        value = self.models[str(model_id)]
        if isinstance(value, Exception):
            raise value
        return value


class FakeGenerator:
    """Records every batch; answers from a script per model title found in the message."""

    def __init__(self, script: dict, fail_on_call: int = 0) -> None:
        self.script = {key: list(value) if isinstance(value, list) else [value] for key, value in script.items()}
        self.calls: list = []
        self.max_lengths: list = []
        self.fail_on_call = fail_on_call

    def generate(self, messages, max_lengths=None):
        self.calls.append(list(messages))
        self.max_lengths.append(max_lengths)
        if self.fail_on_call and len(self.calls) == self.fail_on_call:
            raise NamingModelError("ComfyUI failed in TextGenerateV2: out of memory")
        answers = []
        for message in messages:
            if message.startswith(GROUP_INTRO):
                message = message[len(GROUP_INTRO):].lstrip("\n")  # the worked example is not a question
            sections = GROUP_HEADER.split(message)
            if len(sections) == 1:
                answers.append(self.answer(message))
                continue
            # A grouped question: answer every "Model N:" section under the same header.
            numbers, bodies = sections[1::2], sections[2::2]
            answers.append("\n\n".join(f"Model {n}:\n{self.answer(body)}" for n, body in zip(numbers, bodies)))
        return answers

    def answer(self, message):
        for key, queue in self.script.items():
            if f"Model title: {key}\n" in message:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return ""

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
        self.assertFalse(row["metadataOnly"])
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
            self.dry_run(models, generator, batch_size=1, group_size=1)
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
        self.assertTrue(self.row(rows, "aura v4")["metadataOnly"])

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
        # Seeded from the local sidecars: also from entries that are not renamed (here: gone from Civitai).
        write_entry(self.lora, "maker - glow - v1 (ill)", 600, 6001, "gg", base="Illustrious")
        write_entry(self.lora, "maker - glow - v2", 600, 6002, "gi")
        write_entry(self.lora, "other - gone - v1 (pny)", 601, 6011, "gh", base="Pony")
        models = {
            600: live_model(600, "Glow", "maker", "LORA", [
                live_version(6001, "v1", "Illustrious", [live_file(1, "gg")]),
                live_version(6002, "v2", "Pony", [live_file(2, "gi")]),
            ]),
            601: CivitaiRequestError("404 Not Found.", status=404),
        }
        generator = FakeGenerator({"Glow": "title: glow\noriginal:\nversion 1: v1\nversion 2: v2"})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(base_codes.code("Illustrious"), "ill")
        self.assertEqual(base_codes.code("Pony"), "pny")
        self.assertNotIn("New base models", generator.messages[0])
        self.assertEqual(self.row(rows, "maker - glow - v1")["status"], "unchanged")
        self.assertEqual(self.row(rows, "maker - glow - v2")["newStem"], "maker - glow - v2 (pny)")

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
        _summary, rows = self.dry_run(models, generator, batch_size=2, group_size=1)
        self.assertEqual([len(batch) for batch in generator.calls], [2, 2])
        self.assertIn("Your last answer had these problems", generator.calls[1][0])
        self.assertIn("Model title: Model 2\n", generator.calls[1][1])
        self.assertEqual(self.row(rows, "m0")["newStem"], "maker - model 0 - v1 (krea2)")

    def three_models(self, count: int = 3) -> dict:
        for n in range(count):
            write_entry(self.lora, f"m{n}", 1000 + n, 10000 + n, f"k{n}")
        return {
            1000 + n: live_model(1000 + n, f"Glow {n}", "maker", "LORA",
                                 [live_version(10000 + n, "v1", "Krea 2", [live_file(n + 1, f"k{n}")])])
            for n in range(count)
        }

    def glow_script(self, count: int = 3) -> dict:
        return {f"Glow {n}": f"title: glow {n}\noriginal:\nversion 1: v1" for n in range(count)}

    def test_group_size_puts_several_models_in_one_call(self) -> None:
        models = self.three_models()
        generator = FakeGenerator(self.glow_script())
        _summary, rows = self.dry_run(models, generator, group_size=3)
        self.assertEqual([len(batch) for batch in generator.calls], [1])
        message = generator.calls[0][0]
        for number in (1, 2, 3):
            self.assertIn(f"Model {number}:\nCreator: maker\n", message)
        # The answer room grows with the models in the call.
        self.assertEqual(generator.max_lengths, [[3 * renamer.ANSWER_TOKENS_PER_MODEL]])
        for n in range(3):
            row = self.row(rows, f"m{n}")
            self.assertEqual(row["newStem"], f"maker - glow {n} - v1 (krea2)")
            self.assertEqual(row["flags"], [])

    def test_group_size_splits_into_calls_and_keeps_the_batch(self) -> None:
        models = self.three_models(5)
        generator = FakeGenerator(self.glow_script(5))
        self.dry_run(models, generator, group_size=2, batch_size=8)
        # One queued job holding three calls: 2 + 2 + 1 models; a call of one model has no header.
        self.assertEqual([len(batch) for batch in generator.calls], [3])
        self.assertNotIn("Model 1:", generator.calls[0][2])
        self.assertEqual(generator.max_lengths[0][2], renamer.ANSWER_TOKENS_PER_MODEL)

    def test_grouped_cache_is_per_model_whatever_the_group_size(self) -> None:
        models = self.three_models()
        self.dry_run(models, FakeGenerator(self.glow_script()), group_size=3)
        self.assertEqual(len(self.cache._answers), 3)
        # Asked one by one, every model is a cache hit.
        generator = FakeGenerator(self.glow_script())
        self.emitted = []
        _summary, rows = self.dry_run(models, generator, group_size=1)
        self.assertEqual(generator.calls, [])
        self.assertEqual(self.row(rows, "m1")["newStem"], "maker - glow 1 - v1 (krea2)")

    def test_grouped_model_that_fails_its_check_is_retried_alone(self) -> None:
        models = self.three_models()
        script = self.glow_script()
        script["Glow 1"] = ["title: glow 1 extra\noriginal:\nversion 1: v1", "title: glow 1\noriginal:\nversion 1: v1"]
        generator = FakeGenerator(script)
        _summary, rows = self.dry_run(models, generator, group_size=3)
        self.assertEqual([len(batch) for batch in generator.calls], [1, 1])
        retry = generator.calls[1][0]
        self.assertNotIn("Model 1:", retry)
        self.assertIn("Model title: Glow 1\n", retry)
        self.assertIn("Your last answer had these problems", retry)
        self.assertNotIn("Glow 0", retry)
        self.assertEqual(generator.max_lengths[1], [renamer.ANSWER_TOKENS_PER_MODEL])
        self.assertEqual(self.row(rows, "m1")["newStem"], "maker - glow 1 - v1 (krea2)")
        self.assertEqual(len(self.cache._answers), 3)

    def test_grouped_answer_missing_a_section_retries_that_model_alone(self) -> None:
        models = self.three_models()
        generator = FakeGenerator(self.glow_script())
        real = generator.generate

        def drop_model_two(messages, max_lengths=None):
            texts = real(messages, max_lengths)
            return [re.sub(r"Model 2:\n[^\n]*\n[^\n]*\n[^\n]*", "", text) for text in texts]

        generator.generate = drop_model_two
        _summary, rows = self.dry_run(models, generator, group_size=3)
        self.assertEqual(len(generator.calls), 2)
        self.assertIn("Model title: Glow 1\n", generator.calls[1][0])
        self.assertNotIn("Glow 2", generator.calls[1][0])
        self.assertEqual(self.row(rows, "m1")["flags"], [])

    # --- base words and version format ------------------------------------

    def glide(self, creator="brightfern", stem="brightfern - deep glide - v1 (anima)", description="") -> dict:
        self.base_codes.set("Anima", "anima")
        write_entry(self.lora, stem, 900, 9001, "g9")
        return {900: live_model(900, "Deep Glide", creator, "LORA", [
            live_version(9001, "v1.0 Anima", "Anima", [live_file(1, "g9")]),
        ], description=description)}

    def test_base_word_and_version_format_get_one_retry_then_flags(self) -> None:
        models = self.glide()
        answer = "title: deep glide\noriginal:\nversion 1: v1.0 anima"
        generator = FakeGenerator({"Deep Glide": [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        retry = generator.messages[1]
        self.assertIn('"anima" is a base model name; leave it out of the title and version.', retry)
        self.assertIn('write "v1", not "v1.0".', retry)
        row = self.row(rows, "brightfern")
        self.assertEqual(row["newStem"], "brightfern - deep glide - v1.0 anima (anima)")
        self.assertIn("base-word:anima", row["flags"])
        self.assertIn("version-format:v1.0", row["flags"])
        self.assertFalse(row["selected"])
        self.assertEqual(self.cache._answers, {})

    def test_answer_without_base_words_is_cached_and_unflagged(self) -> None:
        models = self.glide()
        generator = FakeGenerator({"Deep Glide": ["title: deep glide\noriginal:\nversion 1: v1.0 anima",
                                                  "title: deep glide\noriginal:\nversion 1: v1"]})
        _summary, rows = self.dry_run(models, generator)
        row = self.row(rows, "brightfern")
        self.assertEqual(row["flags"], [])
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(len(self.cache._answers), 1)

    def test_xl_is_a_base_word_on_an_sdxl_family_base(self) -> None:
        write_entry(self.lora, "maker - glow xl - v2 (pony)", 910, 9101, "x9")
        self.base_codes.set("Pony", "pony")
        models = {910: live_model(910, "Glow XL", "maker", "LORA", [live_version(9101, "v2", "Pony", [live_file(1, "x9")])])}
        answer = "title: glow xl\noriginal:\nversion 1: v2"
        _summary, rows = self.dry_run(models, FakeGenerator({"Glow XL": [answer, answer]}))
        self.assertIn("base-word:xl", self.row(rows, "maker")["flags"])

    def test_cached_answer_failing_the_base_check_is_asked_again(self) -> None:
        models = self.glide()
        answer = "title: deep glide\noriginal:\nversion 1: v1 anima"
        # Cached while the base check did not exist yet.
        with mock.patch("comfy_updater.renamer.name_check_flags", return_value=[]):
            self.dry_run(models, FakeGenerator({"Deep Glide": answer}))
        self.assertEqual(len(self.cache._answers), 1)
        generator = FakeGenerator({"Deep Glide": "title: deep glide\noriginal:\nversion 1: v1"})
        self.emitted = []
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 1)
        self.assertEqual(self.row(rows, "brightfern")["flags"], [])

    # --- creator fallback ---------------------------------------------------

    def test_no_live_creator_takes_the_creator_from_the_file_name(self) -> None:
        models = self.glide(creator=None)
        _summary, rows = self.dry_run(models, FakeGenerator({"Deep Glide": "title: deep glide\noriginal:\nversion 1: v1"}))
        row = self.row(rows, "brightfern")
        self.assertEqual(row["parts"]["creator"], "brightfern")
        self.assertIn("creator-from-file-name", row["flags"])
        self.assertNotIn("empty:creator", row["flags"])
        self.assertFalse(row["selected"])

    def test_no_creator_anywhere_stays_empty(self) -> None:
        models = self.glide(creator=None, stem="deep glide v1")
        _summary, rows = self.dry_run(models, FakeGenerator({"Deep Glide": "title: deep glide\noriginal:\nversion 1: v1"}))
        row = self.row(rows, "deep glide")
        self.assertEqual(row["parts"]["creator"], "")
        self.assertIn("empty:creator", row["flags"])
        self.assertNotIn("creator-from-file-name", row["flags"])
        self.assertFalse(row["selected"])

    def test_decision_or_original_beats_the_file_name_creator(self) -> None:
        models = self.glide(creator=None, description="<p>Original by fernwick.</p>")
        answer = "title: deep glide\noriginal: fernwick\nversion 1: v1"
        _summary, rows = self.dry_run(models, FakeGenerator({"Deep Glide": answer}))
        row = self.row(rows, "brightfern")
        self.assertEqual(row["parts"]["creator"], "fernwick")
        self.assertIn("maker-from-description", row["flags"])
        self.assertNotIn("creator-from-file-name", row["flags"])

        self.decisions.set_model(900, creator="someone")
        self.emitted = []
        _summary, rows = self.dry_run(models, FakeGenerator({"Deep Glide": answer}))
        row = self.row(rows, "brightfern")
        self.assertEqual(row["parts"]["creator"], "someone")
        self.assertEqual(row["flags"], [])
        self.assertTrue(row["selected"])

    # --- uploader and maker-unknown ------------------------------------------

    def official(self, description="") -> dict:
        write_entry(self.unet, "glowbase-4b", 820, 8201, "o1")
        return {820: live_model(820, "Glowbase 4B", "siteofficial", "Checkpoint", [
            live_version(8201, "4B", "Krea 2", [live_file(1, "o1", name="glowbase-4b.safetensors")]),
        ], description=description)}

    def test_rows_carry_the_uploader(self) -> None:
        models = self.lumegrid()
        _summary, rows = self.dry_run(models, FakeGenerator({LUMEGRID: "title: lumegrid\noriginal:\nversion 1: zest"}))
        self.assertEqual(self.row(rows, "tidemint")["uploader"], "tidemint")

    def test_official_file_without_a_maker_is_flagged_maker_unknown(self) -> None:
        models = self.official()
        _summary, rows = self.dry_run(models, FakeGenerator({"Glowbase 4B": "title: glowbase\noriginal:\nversion 1: 4b"}))
        row = self.row(rows, "glowbase")
        self.assertEqual(row["parts"]["creator"], "siteofficial")
        self.assertIn("official-file", row["flags"])
        self.assertIn("maker-unknown", row["flags"])
        self.assertFalse(row["selected"])

    def test_official_file_with_a_maker_from_a_link_is_not_maker_unknown(self) -> None:
        models = self.official('<p>Originally Posted; <a href="https://huggingface.co/quillworks-lab/Glowbase-4B">here</a></p>')
        generator = FakeGenerator({"Glowbase 4B": "title: glowbase\noriginal: quillworks lab\nversion 1: 4b"})
        _summary, rows = self.dry_run(models, generator)
        self.assertIn("Links: huggingface.co/quillworks-lab", generator.messages[0])
        self.assertEqual(len(generator.messages), 1)
        row = self.row(rows, "glowbase")
        self.assertEqual(row["parts"]["creator"], "quillworks lab")
        self.assertIn("maker-from-description", row["flags"])
        self.assertNotIn("maker-not-in-description", row["flags"])
        self.assertNotIn("maker-unknown", row["flags"])

    def test_official_file_gets_link_owners_without_a_credit_sentence(self) -> None:
        models = self.official('<p>4B weights: <a href="https://huggingface.co/quillworks-lab/Glowbase-4B">here</a></p>')
        generator = FakeGenerator({"Glowbase 4B": "title: glowbase\noriginal: quillworks lab\nversion 1: 4b"})
        _summary, rows = self.dry_run(models, generator)
        self.assertIn("Credit lines (only for the original line): Links: huggingface.co/quillworks-lab", generator.messages[0])
        self.assertEqual(len(generator.messages), 1)
        self.assertNotIn("maker-not-in-description", self.row(rows, "glowbase")["flags"])

    def test_lora_linking_only_its_tools_gets_no_link_owners(self) -> None:
        models = self.lumegrid()
        models[100]["description"] = '<p>Made with <a href="https://github.com/kohya-trainer/sd-scripts">this</a>.</p>'
        generator = FakeGenerator({LUMEGRID: "title: lumegrid\noriginal:\nversion 1: zest"})
        self.dry_run(models, generator)
        self.assertIn("Credit lines (only for the original line): none", generator.messages[0])

    def test_decided_creator_is_not_maker_unknown(self) -> None:
        models = self.official()
        self.decisions.set_model(820, creator="siteofficial")
        _summary, rows = self.dry_run(models, FakeGenerator({"Glowbase 4B": "title: glowbase\noriginal:\nversion 1: 4b"}))
        self.assertNotIn("maker-unknown", self.row(rows, "glowbase")["flags"])

    # --- version clash within one model --------------------------------------

    def high_low(self, folder=None) -> dict:
        folder = folder or self.lora
        write_entry(folder, "hl high", 830, 8301, "h1")
        write_entry(folder, "hl low", 830, 8302, "h2")
        return {830: live_model(830, "Pinewave", "maker", "LORA", [
            live_version(8301, "V1 - High", "Krea 2", [live_file(1, "h1")]),
            live_version(8302, "V1 - Low", "Krea 2", [live_file(2, "h2")]),
        ])}

    def test_version_clash_gets_one_retry_that_resolves_it(self) -> None:
        models = self.high_low()
        generator = FakeGenerator({"Pinewave": [
            "title: pinewave\noriginal:\nversion 1: v1\nversion 2: v1",
            "title: pinewave\noriginal:\nversion 1: v1 high\nversion 2: v1 low",
        ]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        self.assertIn(
            'Versions "V1 - High" and "V1 - Low" both became "v1"; keep the words that tell them apart.',
            generator.messages[1],
        )
        self.assertEqual(self.row(rows, "hl high")["newStem"], "maker - pinewave - v1 high (krea2)")
        self.assertEqual(self.row(rows, "hl low")["flags"], [])
        self.assertEqual(len(self.cache._answers), 1)

    def test_version_clash_still_there_after_the_retry_is_flagged(self) -> None:
        # In two folders, so it is not a folder collision: the clash check alone must catch it.
        models = self.high_low()
        write_entry(self.root / "loras2", "hl low", 830, 8302, "h2")
        (self.lora / "hl low.safetensors").unlink()
        (self.lora / "hl low.civitai.info").unlink()
        self.roots["lora"].append(self.root / "loras2")
        answer = "title: pinewave\noriginal:\nversion 1: v1\nversion 2: v1"
        generator = FakeGenerator({"Pinewave": [answer, answer]})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 2)
        for stem in ("hl high", "hl low"):
            row = self.row(rows, stem)
            self.assertIn("version-clash", row["flags"])
            self.assertNotIn("collision", row["flags"])
            self.assertFalse(row["selected"])
        self.assertEqual(self.cache._answers, {})

    def test_versions_told_apart_by_precision_do_not_clash(self) -> None:
        write_entry(self.unet, "pw fp8", 840, 8401, "p1", weights=False)
        write_entry(self.unet, "pw bf16", 840, 8402, "p2", weights=False)
        models = {840: live_model(840, "Pinewave", "maker", "Checkpoint", [
            live_version(8401, "fp8", "Krea 2", [live_file(1, "p1", fp="fp8")]),
            live_version(8402, "bf16", "Krea 2", [live_file(2, "p2", fp="bf16")]),
        ])}
        generator = FakeGenerator({"Pinewave": "title: pinewave\noriginal:\nversion 1:\nversion 2:"})
        _summary, rows = self.dry_run(models, generator)
        self.assertEqual(len(generator.messages), 1)
        self.assertEqual(self.row(rows, "pw fp8")["newStem"], "maker - pinewave - fp8 (krea2)")
        self.assertNotIn("version-clash", self.row(rows, "pw bf16")["flags"])

    # --- title clash across models -------------------------------------------

    def towels(self, same_folder=False) -> dict:
        write_entry(self.lora, "bolt - towel - v1 (krea2)", 850, 8501, "t1")
        write_entry(self.lora if same_folder else self.root / "ill", "bolt - towel 2 - v1 (krea2)", 851, 8511, "t2")
        if not same_folder:
            self.roots["lora"].append(self.root / "ill")
        return {
            850: live_model(850, "Towel", "bolt", "LORA", [live_version(8501, "v1", "Krea 2", [live_file(1, "t1")])]),
            851: live_model(851, "Towel 2", "bolt", "LORA", [live_version(8511, "v1", "Krea 2", [live_file(2, "t2")])]),
        }

    def test_title_clash_across_folders_re_asks_both_once_and_resolves(self) -> None:
        models = self.towels()
        same = "title: towel\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Towel": [same, same], "Towel 2": [same, "title: towel 2\noriginal:\nversion 1: v1"]})
        summary, rows = self.dry_run(models, generator, group_size=1)
        retries = [m for m in generator.messages if "Your last answer had these problems" in m]
        self.assertEqual(len(retries), 2)
        towel_retry = next(m for m in retries if "Model title: Towel\n" in m)
        towel2_retry = next(m for m in retries if "Model title: Towel 2\n" in m)
        self.assertIn('Another model by this creator, "Towel 2", also became "towel"; keep what sets this model apart.',
                      towel_retry)
        self.assertIn('Another model by this creator, "Towel", also became "towel"', towel2_retry)
        self.assertEqual(len(generator.messages), 4)
        first = self.row(rows, "bolt - towel - v1")
        second = self.row(rows, "bolt - towel 2")
        self.assertEqual(first["status"], "unchanged")
        self.assertEqual(second["newStem"], "bolt - towel 2 - v1 (krea2)")
        self.assertEqual(first["flags"], [])
        self.assertEqual(second["flags"], [])
        # The emitted rows are the replaced ones: one row per entry, no stale copy.
        self.assertEqual(self.emitted, rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(summary["flagged"], 0)
        # Both answers are cached once the clash is gone.
        self.assertEqual(len(self.cache._answers), 2)

    def test_title_clash_still_there_is_flagged_on_both_and_not_cached(self) -> None:
        models = self.towels()
        same = "title: towel\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Towel": same, "Towel 2": same})
        summary, rows = self.dry_run(models, generator, group_size=1)
        self.assertEqual(len(generator.messages), 4)  # each asked, then re-asked once
        for stem in ("bolt - towel - v1", "bolt - towel 2"):
            row = self.row(rows, stem)
            self.assertIn("title-clash", row["flags"])
            self.assertFalse(row["selected"])
        self.assertEqual(summary["flagged"], 2)
        self.assertEqual(self.cache._answers, {})

    def test_per_base_twins_are_not_a_title_clash(self) -> None:
        # One idea released per base: the base codes already keep the names apart.
        models = self.towels()
        models[851]["modelVersions"][0]["baseModel"] = "SDXL 1.0"
        same = "title: towel\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Towel": same, "Towel 2": same})
        summary, rows = self.dry_run(models, generator, group_size=1)
        self.assertEqual(len(generator.messages), 2)
        self.assertEqual(self.row(rows, "bolt - towel 2")["newStem"], "bolt - towel - v1 (sdxl)")
        for stem in ("bolt - towel - v1", "bolt - towel 2"):
            self.assertNotIn("title-clash", self.row(rows, stem)["flags"])
        self.assertEqual(summary["flagged"], 0)
        self.assertEqual(len(self.cache._answers), 2)

    def test_decided_title_is_not_re_asked(self) -> None:
        models = self.towels()
        self.decisions.set_model(850, title="towel")
        same = "title: towel\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Towel": same, "Towel 2": [same, "title: towel 2\noriginal:\nversion 1: v1"]})
        _summary, rows = self.dry_run(models, generator, group_size=1)
        retries = [m for m in generator.messages if "Your last answer had these problems" in m]
        self.assertEqual(len(retries), 1)
        self.assertIn("Model title: Towel 2\n", retries[0])
        self.assertEqual(self.row(rows, "bolt - towel 2")["newStem"], "bolt - towel 2 - v1 (krea2)")
        self.assertEqual(self.row(rows, "bolt - towel - v1")["flags"], [])

    def test_resolved_title_clash_clears_the_folder_collision(self) -> None:
        models = self.towels(same_folder=True)
        same = "title: towel\noriginal:\nversion 1: v1"
        generator = FakeGenerator({"Towel": same, "Towel 2": [same, "title: towel 2\noriginal:\nversion 1: v1"]})
        _summary, rows = self.dry_run(models, generator, group_size=1)
        first = self.row(rows, "bolt - towel - v1")
        second = self.row(rows, "bolt - towel 2")
        self.assertEqual(first["flags"], [])
        self.assertEqual(second["flags"], [])
        self.assertFalse(first["selected"])  # unchanged
        self.assertEqual(second["status"], "unchanged")

    def test_unresolved_clash_is_asked_afresh_on_the_next_run(self) -> None:
        models = self.towels()
        same = "title: towel\noriginal:\nversion 1: v1"
        self.dry_run(models, FakeGenerator({"Towel": same, "Towel 2": same}), group_size=1)
        generator = FakeGenerator({"Towel": same, "Towel 2": "title: towel 2\noriginal:\nversion 1: v1"})
        self.emitted = []
        _summary, rows = self.dry_run(models, generator, group_size=1)
        self.assertEqual(self.row(rows, "bolt - towel 2")["flags"], [])
        self.assertEqual(self.row(rows, "bolt - towel - v1")["flags"], [])


class GatedClient(FakeClient):
    """Fetches in order; before fetching ``gate_model`` it waits (bounded) for ``gate``."""

    def __init__(self, models: dict, gate_model=None, gate=None, delay: float = 0.0) -> None:
        super().__init__(models)
        self.gate_model = str(gate_model) if gate_model is not None else None
        self.gate = gate
        self.delay = delay
        self.fetched: list = []
        self.finished = threading.Event()

    def get_model(self, model_id, *, raise_on_error=False):
        if str(model_id) == self.gate_model:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        self.fetched.append(str(model_id))
        if len(self.fetched) == len(self.models):
            self.finished.set()
        return super().get_model(model_id, raise_on_error=raise_on_error)


class Control:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def wait_if_paused(self) -> None:
        pass

    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


class PipelineTests(unittest.TestCase):
    """The dry run names models while it is still fetching the rest from Civitai."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.lora = self.root / "loras"
        self.roots = {"lora": [self.lora]}
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.base_codes.set("Krea 2", "krea2")
        self.cache = AnswerCache(self.data)
        self.emitted: list = []
        self.progress: list = []

    def models(self, count: int) -> dict:
        for n in range(count):
            write_entry(self.lora, f"m{n}", 1000 + n, 10000 + n, f"k{n}")
        return {
            1000 + n: live_model(1000 + n, f"Glow {n}", "maker", "LORA",
                                 [live_version(10000 + n, "v1", "Krea 2", [live_file(n + 1, f"k{n}")])])
            for n in range(count)
        }

    def script(self, count: int) -> dict:
        return {f"Glow {n}": f"title: glow {n}\noriginal:\nversion 1: v1" for n in range(count)}

    def run_bounded(self, client, generator, control=None, **kwargs):
        """run_dry_run on a thread, so a hang fails the test instead of blocking it."""
        result: dict = {}

        def body():
            try:
                result["value"] = run_dry_run(
                    self.roots, client, generator, decisions=self.decisions, base_codes=self.base_codes,
                    cache=self.cache, clip_name="q", progress=lambda c, t, m: self.progress.append((c, t, m)),
                    emit=self.emitted.append, control=control, **kwargs,
                )
            except BaseException as exc:  # noqa: BLE001
                result["error"] = exc

        thread = threading.Thread(target=body, daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive(), "the dry run hung")
        if "error" in result:
            raise result["error"]
        return result["value"]

    def test_naming_starts_before_fetching_ends(self) -> None:
        models = self.models(4)
        named = threading.Event()
        client = GatedClient(models, gate_model=1003, gate=named)
        generator = FakeGenerator(self.script(4))
        seen_finished = []
        real = generator.generate

        def generate(messages, max_lengths=None):
            seen_finished.append(client.finished.is_set())
            named.set()
            return real(messages, max_lengths)

        generator.generate = generate
        summary, rows = self.run_bounded(client, generator, group_size=1)
        self.assertEqual(seen_finished[0], False)
        self.assertEqual(summary["change"], 4)
        self.assertEqual(sorted(r["oldStem"] for r in rows), ["m0", "m1", "m2", "m3"])
        self.assertEqual(self.emitted, rows)
        self.assertEqual(self.progress[-1][:2], (4, 4))
        self.assertIn("fetched 4 of 4", self.progress[-1][2])

    def test_finished_rows_are_emitted_while_the_ai_is_busy(self) -> None:
        models = self.models(1)
        write_entry(self.lora, "zgone", 2000, 20000, "zz")  # listed, so fetched, after m0
        models[2000] = CivitaiRequestError("404 Not Found.", status=404)
        asked = threading.Event()
        gone_emitted = threading.Event()
        self.emitted = EventList(gone_emitted, lambda row: row["oldStem"] == "zgone")
        client = GatedClient(models, gate_model=2000, gate=asked)
        generator = FakeGenerator(self.script(1))
        real = generator.generate
        fetched_when_asked = []

        def generate(messages, max_lengths=None):
            fetched_when_asked.append(list(client.fetched))
            asked.set()
            gone_emitted.wait(5)  # the AI is busy until the fetch side has emitted its row
            return real(messages, max_lengths)

        generator.generate = generate
        summary, rows = self.run_bounded(client, generator, group_size=1)
        self.assertEqual(fetched_when_asked[0], ["1000"])
        stems = [row["oldStem"] for row in self.emitted]
        self.assertEqual(stems, ["zgone", "m0"])
        self.assertEqual(summary["kept"], 1)
        self.assertEqual(summary["change"], 1)

    def test_kept_entry_decided_after_a_row_still_flags_exists(self) -> None:
        # "m0" is named to "maker - glow 0 - v1 (krea2)"; that stem already belongs to an
        # entry Civitai no longer has, which stays on disk under its name.
        models = self.models(1)
        write_entry(self.lora, "maker - glow 0 - v1 (krea2)", 2000, 20000, "zz")
        models[2000] = CivitaiRequestError("404 Not Found.", status=404)
        for gate_first in (False, True):
            with self.subTest(gate_first=gate_first):
                self.cache = AnswerCache(self.root / f"cache{gate_first}")
                self.emitted = []
                named = threading.Event()
                client = GatedClient(models, gate_model=2000 if gate_first else None, gate=named)
                generator = FakeGenerator(self.script(1))
                real = generator.generate

                def generate(messages, max_lengths=None, real=real, named=named):
                    texts = real(messages, max_lengths)
                    named.set()
                    return texts

                generator.generate = generate
                _summary, rows = self.run_bounded(client, generator, group_size=1)
                row = [r for r in rows if r["oldStem"] == "m0"][0]
                self.assertIn("exists", row["flags"])
                self.assertFalse(row["selected"])

    def test_cancel_stops_fetching_and_naming(self) -> None:
        models = self.models(20)
        control = Control()
        client = GatedClient(models, delay=0.02)
        generator = FakeGenerator(self.script(20))
        real = generator.generate

        def generate(messages, max_lengths=None):
            control.cancelled.set()
            return real(messages, max_lengths)

        generator.generate = generate
        summary, rows = self.run_bounded(client, generator, control=control, group_size=1)
        self.assertEqual(len(generator.calls), 1)
        self.assertLess(len(client.fetched), 20)
        count = len(self.emitted)
        progress_count = len(self.progress)
        time.sleep(0.1)
        self.assertEqual(len(self.emitted), count, "rows emitted after the run returned")
        self.assertEqual(len(self.progress), progress_count, "progress reported after the run returned")
        # The summary counts exactly the rows returned.
        self.assertEqual(summary["change"] + summary["unchanged"] + summary["kept"], len(rows))
        self.assertEqual(rows, self.emitted)

    def test_fetch_side_progress_never_lands_after_the_run_returned(self) -> None:
        models = self.models(3)
        control = Control()
        returned = threading.Event()
        late: list = []

        def progress(current, total, message):
            if threading.current_thread().name == "renamer-fetch" and "fetched 1 of" in message:
                control.cancelled.set()
                returned.wait(2)  # the fetch side is slow to report; the run is cancelled meanwhile
            if returned.is_set():
                late.append(message)

        run_dry_run(
            self.roots, GatedClient(models), FakeGenerator(self.script(3)), decisions=self.decisions,
            base_codes=self.base_codes, cache=self.cache, clip_name="q", progress=progress,
            emit=self.emitted.append, control=control, group_size=3,
        )
        returned.set()
        time.sleep(0.2)
        self.assertEqual(late, [])

    def test_cancelled_summary_is_taken_after_the_fetch_side_stopped(self) -> None:
        models = self.models(3)
        models[1001] = CivitaiRequestError("404 Not Found.", status=404)  # a kept row, emitted by the fetch side
        control = Control()
        release = threading.Event()

        class CancelInFlight(GatedClient):
            """The job is cancelled while model 1001 is being fetched; the fetch ends after the summary."""

            def get_model(self, model_id, *, raise_on_error=False):
                if str(model_id) == "1001":
                    control.cancelled.set()
                return super().get_model(model_id, raise_on_error=raise_on_error)

        client = CancelInFlight(models, gate_model=1001, gate=release)

        def cancel_then_release(current, total, message):
            self.progress.append((current, total, message))

        original = renamer._summary

        def slow_summary(entries, rows):
            result = original(entries, rows)
            release.set()  # the fetch side may now finish its model: it must not add a row after the summary
            time.sleep(0.2)
            return result

        with mock.patch.object(renamer, "_summary", side_effect=slow_summary):
            summary, rows = run_dry_run(
                self.roots, client, FakeGenerator(self.script(3)), decisions=self.decisions,
                base_codes=self.base_codes, cache=self.cache, clip_name="q", progress=cancel_then_release,
                emit=self.emitted.append, control=control, group_size=3,
            )
        time.sleep(0.2)
        self.assertEqual(sum(summary[k] for k in ("change", "unchanged", "kept", "duplicate", "error")), len(rows))
        self.assertEqual(rows, self.emitted)

    def test_cancel_before_the_start_does_not_seed_or_fetch(self) -> None:
        models = self.models(2)
        control = Control()
        control.cancelled.set()
        base_codes = BaseCodeStore(self.root / "fresh")
        self.base_codes = base_codes
        client = GatedClient(models)
        self.run_bounded(client, FakeGenerator({}), control=control)
        self.assertEqual(client.fetched, [])
        self.assertEqual(base_codes.all(), {})

    def test_fetch_thread_error_fails_the_run(self) -> None:
        models = self.models(3)
        client = GatedClient(models)
        real_get = client.get_model

        def get_model(model_id, *, raise_on_error=False):
            if str(model_id) == "1001":
                raise RuntimeError("socket exploded")
            return real_get(model_id, raise_on_error=raise_on_error)

        client.get_model = get_model
        with self.assertRaises(RuntimeError) as caught:
            self.run_bounded(client, FakeGenerator(self.script(3)), group_size=1)
        self.assertIn("socket exploded", str(caught.exception))

    def test_naming_model_error_stops_both_sides_and_keeps_emitted_rows(self) -> None:
        models = self.models(20)
        write_entry(self.lora, "gone", 2000, 20000, "zz")
        models[2000] = CivitaiRequestError("404 Not Found.", status=404)
        client = GatedClient(models, delay=0.01)
        generator = FakeGenerator(self.script(20), fail_on_call=2)
        with self.assertRaises(NamingModelError):
            self.run_bounded(client, generator, group_size=1, batch_size=1)
        named = [r for r in self.emitted if r["status"] in ("change", "unchanged")]
        self.assertEqual(len(named), 1)
        count = len(self.emitted)
        time.sleep(0.15)
        self.assertEqual(len(self.emitted), count, "rows emitted after the run failed")
        self.assertLess(len(client.fetched), 21)


class EventList(list):
    """A list that sets ``event`` once an appended item matches."""

    def __init__(self, event, match) -> None:
        super().__init__()
        self.event = event
        self.match = match

    def append(self, item) -> None:
        super().append(item)
        if self.match(item):
            self.event.set()


if __name__ == "__main__":
    unittest.main()
