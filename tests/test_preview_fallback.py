from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from comfy_updater import preview_fallback, renamer
from comfy_updater.fallback_types import FallbackResult, SourceUnavailable
from comfy_updater.preview_fallback import (
    FallbackOutcome,
    MissStore,
    PreviewFallback,
    apply_to_info,
)
from comfy_updater.sidecar import info_sidecar_path, preview_sidecar_path, read_json, write_json

SHA = "ab" * 32
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HF_IMAGE = "https://huggingface.co/example/repo/resolve/main/a.png"


def image_bytes(fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, format=fmt)
    return buf.getvalue()


def sha_for(n: int) -> str:
    return f"{n:02x}" * 32


def hf_result(**over) -> FallbackResult:
    values = dict(
        source="huggingface", url="https://huggingface.co/example/repo", title="Cool", creator="someone",
        version_name="v1", base_model="Krea 2", image_url=HF_IMAGE,
    )
    values.update(over)
    return FallbackResult(**values)


def civ_result(**over) -> FallbackResult:
    values = dict(
        source="civarchive", url="https://civarchive.com/sha256/" + SHA, title="Cool Style", creator="someone",
        version_name="v2", base_model="Krea 2", description="A style.", trained_words=["coolstyle"], nsfw=True,
        image_url="https://c.genur.art/a_small.webp", image_nsfw_level=2,
        civitai_model_id="77", civitai_version_id="88",
    )
    values.update(over)
    return FallbackResult(**values)


def own_result(**over) -> FallbackResult:
    values = dict(
        source="ai-toolkit", url="", title="my concept", creator="someone", version_name="step 10",
        base_model="Krea 2", final=True,
    )
    values.update(over)
    return FallbackResult(**values)


def stub(name: str = "x.safetensors", sha: str = SHA) -> dict:
    return {
        "id": "",
        "modelId": "",
        "name": name,
        "files": [{"hashes": {"SHA256": sha}}],
        "extensions": {"source": "comfy-civitai-updater"},
    }


class FakeSources:
    """Stands in for the three sources; every ask is logged in order."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.answers: dict[str, list] = {"ai-toolkit": [], "huggingface": [], "civarchive": []}
        self.args: dict[str, tuple] = {}
        self.hf_init: tuple | None = None

    def queue(self, source: str, *answers) -> None:
        self.answers[source].extend(answers)

    def _answer(self, source: str):
        self.log.append(source)
        pending = self.answers[source]
        answer = pending.pop(0) if pending else None
        if isinstance(answer, Exception):
            raise answer
        return answer

    def own(self, model_path, folders, creator, base_names):
        self.args["ai-toolkit"] = (model_path, folders, creator, base_names)
        return self._answer("ai-toolkit")

    def civarchive(self, sha256, http):
        self.args["civarchive"] = (sha256, http)
        return self._answer("civarchive")

    def hf_index(self, entries, http, cache_path):
        self.hf_init = (entries, http, cache_path)
        sources = self

        class _Index:
            def find(self, sha256):
                sources.args["huggingface"] = (sha256,)
                return sources._answer("huggingface")

        return _Index()


class FakeHttp:
    def __init__(self) -> None:
        self.data = image_bytes()
        self.error: Exception | None = None
        self.before_return = None
        self.calls: list[tuple[str, int]] = []

    def download(self, url, max_bytes=20_000_000):
        self.calls.append((url, max_bytes))
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return self.data


class PreviewFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.lib = root / "lib"
        self.lib.mkdir()
        self.out = root / "out"
        self.out.mkdir()
        self.data = root / "data"
        self.sources = FakeSources()
        for name, fake in (
            ("lookup_own_training", self.sources.own),
            ("HuggingFaceIndex", self.sources.hf_index),
            ("lookup_civarchive", self.sources.civarchive),
        ):
            patcher = patch.object(preview_fallback, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.http = FakeHttp()
        self.model = self.model_file("x")
        self.info_path = info_sidecar_path(self.model)
        self.preview_path = preview_sidecar_path(self.model)

    # Helpers

    def model_file(self, stem: str) -> Path:
        path = self.lib / f"{stem}.safetensors"
        path.write_bytes(b"weights")
        return path

    def config(self, **over) -> dict:
        cfg = {
            "fallbackCivarchive": True,
            "fallbackHuggingFace": ["someone"],
            "fallbackAiToolkitFolders": [],
            "fallbackOwnCreator": "",
        }
        cfg.update(over)
        return cfg

    def fallback(self, now: datetime = NOW, **over) -> PreviewFallback:
        return PreviewFallback(self.config(**over), self.data, self.http, {"Krea 2": "krea2"}, now=lambda: now)

    def run_on(self, fb, model: Path | None = None, sha: str = SHA, refetch: bool = False, on_source=None):
        model = model or self.model
        return fb.run(
            model_path=model,
            info_path=info_sidecar_path(model),
            preview_path=preview_sidecar_path(model),
            sha256=sha,
            refetch=refetch,
            on_source=on_source,
        )

    def misses(self) -> dict:
        return (read_json(self.data / "preview_fallback.json") or {}).get("misses", {})

    # The chain

    def test_existing_preview_short_circuits(self):
        self.preview_path.write_bytes(b"old picture")
        write_json(self.info_path, stub())
        before = self.info_path.read_bytes()
        self.sources.queue("huggingface", hf_result())

        self.assertIsNone(self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)])))
        self.assertEqual(self.sources.log, [])
        self.assertEqual(self.info_path.read_bytes(), before)
        self.assertEqual(self.preview_path.read_bytes(), b"old picture")

    def test_order_and_first_hit_wins(self):
        self.sources.queue("huggingface", hf_result())
        self.sources.queue("civarchive", civ_result())

        outcome = self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)]))
        self.assertEqual(self.sources.log, ["ai-toolkit", "huggingface"])
        self.assertEqual(outcome.source, "huggingface")

    def test_final_own_training_stops_chain_without_image(self):
        self.sources.queue("ai-toolkit", own_result())

        outcome = self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)]))
        self.assertEqual(self.sources.log, ["ai-toolkit"])
        self.assertEqual(outcome, FallbackOutcome(source="ai-toolkit", preview_written=False, info_written=True))
        self.assertFalse(self.preview_path.exists())
        info = read_json(self.info_path)
        self.assertEqual(info["model"]["name"], "my concept")
        self.assertEqual(info["extensions"]["fallback"]["source"], "ai-toolkit")
        self.assertNotIn("images", info)

    def test_own_training_gets_folders_creator_and_base_names(self):
        self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)], fallbackOwnCreator="someone"))
        self.assertEqual(
            self.sources.args["ai-toolkit"], (self.model, [self.out], "someone", {"krea2": "Krea 2"})
        )

    def test_hit_without_image_still_wins_the_chain(self):
        self.sources.queue("huggingface", hf_result(image_url=""), hf_result(image_url=""))

        outcome = self.run_on(self.fallback())
        self.assertEqual(self.sources.log, ["huggingface"])
        self.assertEqual(outcome, FallbackOutcome(source="huggingface", preview_written=False, info_written=True))
        self.assertEqual(read_json(self.info_path)["model"]["name"], "Cool")
        self.assertEqual(self.http.calls, [])

        # The next scan still has no preview, so it asks again; the info is already filled.
        self.sources.log.clear()
        outcome = self.run_on(self.fallback())
        self.assertEqual(self.sources.log, ["huggingface"])
        self.assertFalse(outcome.info_written)
        self.assertEqual(self.misses(), {})

    def test_progress_names_each_source_before_it_is_asked(self):
        self.sources.queue("civarchive", civ_result())
        fb = self.fallback(fallbackAiToolkitFolders=[str(self.out)])

        self.run_on(fb, on_source=lambda key: self.sources.log.append("announce " + key))
        self.assertEqual(
            self.sources.log,
            [
                "announce ai-toolkit", "ai-toolkit",
                "announce huggingface", "huggingface",
                "announce civarchive", "civarchive",
            ],
        )

    def test_sources_share_one_http_and_get_only_the_hash(self):
        self.run_on(self.fallback(), sha=SHA.upper())
        entries, http, cache_path = self.sources.hf_init
        self.assertEqual(entries, ["someone"])
        self.assertIs(http, self.http)
        self.assertEqual(cache_path, self.data / "hf_index.json")
        self.assertEqual(self.sources.args["huggingface"], (SHA,))
        self.assertEqual(self.sources.args["civarchive"], (SHA, self.http))

    # What a hit writes

    def test_hit_writes_png_and_fills_stub(self):
        write_json(self.info_path, stub())
        self.sources.queue("huggingface", hf_result())

        outcome = self.run_on(self.fallback())
        self.assertEqual(outcome, FallbackOutcome(source="huggingface", preview_written=True, info_written=True))
        self.assertEqual(self.http.calls, [(HF_IMAGE, 20_000_000)])
        self.assertTrue(self.preview_path.read_bytes().startswith(PNG_SIGNATURE))
        info = read_json(self.info_path)
        self.assertEqual(info["model"], {"name": "Cool", "nsfw": False})
        self.assertEqual(info["creator"], {"username": "someone"})
        self.assertEqual(info["name"], "v1")
        self.assertEqual(info["extensions"]["fallback"]["source"], "huggingface")
        self.assertEqual(info["files"][0]["hashes"]["SHA256"], SHA)
        self.assertEqual(info["id"], "")  # no Civitai ids from Hugging Face
        self.assertEqual(
            info,
            {
                "id": "",
                "modelId": "",
                "name": "v1",
                "files": [{"hashes": {"SHA256": SHA}}],
                "extensions": {
                    "source": "comfy-civitai-updater",
                    "fallback": {
                        "source": "huggingface",
                        "url": "https://huggingface.co/example/repo",
                        "foundAt": NOW.isoformat(),
                    },
                },
                "baseModel": "Krea 2",
                "model": {"name": "Cool", "nsfw": False},
                "creator": {"username": "someone"},
                "images": [{"url": HF_IMAGE, "type": "image", "nsfwLevel": 0}],
            },
        )

    def test_no_info_file_gets_a_stub_shaped_file(self):
        self.sources.queue("huggingface", hf_result(version_name=""))

        self.run_on(self.fallback())
        info = read_json(self.info_path)
        self.assertEqual((info["id"], info["modelId"], info["name"]), ("", "", "x.safetensors"))
        self.assertEqual(info["files"], [{"hashes": {"SHA256": SHA}}])
        self.assertEqual(info["extensions"]["source"], "comfy-civitai-updater")

    def test_civarchive_civitai_hit_writes_ids(self):
        write_json(self.info_path, stub())
        self.sources.queue("civarchive", civ_result())

        outcome = self.run_on(self.fallback())
        self.assertEqual(outcome.source, "civarchive")
        info = read_json(self.info_path)
        self.assertEqual((info["modelId"], info["id"]), (77, 88))
        self.assertEqual(info["description"], "A style.")
        self.assertEqual(info["trainedWords"], ["coolstyle"])
        self.assertEqual(info["model"], {"name": "Cool Style", "nsfw": True})
        self.assertEqual(info["images"], [{"url": "https://c.genur.art/a_small.webp", "type": "image", "nsfwLevel": 2}])

    def test_identified_sidecar_gets_preview_only(self):
        identified = {"id": 88, "modelId": 77, "name": "Real", "model": {"name": "Real Model"}}
        self.info_path.write_text(json.dumps(identified, indent=2), encoding="utf-8")
        before = self.info_path.read_bytes()
        self.sources.queue("civarchive", civ_result())

        outcome = self.run_on(self.fallback())
        self.assertEqual(outcome, FallbackOutcome(source="civarchive", preview_written=True, info_written=False))
        self.assertEqual(self.info_path.read_bytes(), before)
        self.assertTrue(self.preview_path.read_bytes().startswith(PNG_SIGNATURE))

    def test_unreadable_info_file_is_not_replaced(self):
        self.info_path.write_bytes(b"{broken")
        self.sources.queue("huggingface", hf_result())

        outcome = self.run_on(self.fallback())
        self.assertEqual(outcome, FallbackOutcome(source="huggingface", preview_written=True, info_written=False))
        self.assertEqual(self.info_path.read_bytes(), b"{broken")

    def test_info_for_another_hash_is_left_alone(self):
        write_json(self.info_path, stub(sha="cd" * 32))
        before = self.info_path.read_bytes()
        self.sources.queue("huggingface", hf_result())

        outcome = self.run_on(self.fallback())
        self.assertFalse(outcome.info_written)
        self.assertEqual(self.info_path.read_bytes(), before)

    def test_never_overwrites_preview_created_meanwhile(self):
        self.http.before_return = lambda: self.preview_path.write_bytes(b"their picture")
        self.sources.queue("huggingface", hf_result())

        outcome = self.run_on(self.fallback())
        self.assertFalse(outcome.preview_written)
        self.assertTrue(outcome.info_written)
        self.assertEqual(self.preview_path.read_bytes(), b"their picture")

    def test_download_failure_still_writes_info(self):
        self.http.error = SourceUnavailable("HTTP 500", status=500)
        self.sources.queue("huggingface", hf_result())

        outcome = self.run_on(self.fallback())
        self.assertEqual(outcome, FallbackOutcome(source="huggingface", preview_written=False, info_written=True))
        self.assertFalse(self.preview_path.exists())
        self.assertEqual(read_json(self.info_path)["model"]["name"], "Cool")

    def test_image_url_off_the_allow_list_is_not_stored(self):
        self.sources.queue("huggingface", hf_result(image_url="https://example.com/a.png"))

        outcome = self.run_on(self.fallback())
        self.assertFalse(outcome.preview_written)
        self.assertEqual(self.http.calls, [])
        self.assertNotIn("images", read_json(self.info_path))

    def test_own_training_sample_becomes_the_preview(self):
        sample = self.out / "sample.jpg"
        sample.write_bytes(image_bytes("JPEG"))
        self.sources.queue("ai-toolkit", own_result(image_path=sample))

        outcome = self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)]))
        self.assertEqual(outcome, FallbackOutcome(source="ai-toolkit", preview_written=True, info_written=True))
        self.assertTrue(self.preview_path.read_bytes().startswith(PNG_SIGNATURE))
        self.assertEqual(self.http.calls, [])
        self.assertNotIn("images", read_json(self.info_path))

    def test_oversized_or_unreadable_sample_writes_info_only(self):
        big = self.out / "big.png"
        big.write_bytes(image_bytes("PNG"))
        with open(big, "r+b") as handle:
            handle.truncate(20_000_001)  # a real PNG, padded past the cap
        for stem, sample in (("big", big), ("gone", self.out / "missing.png")):
            model = self.model_file(stem)
            self.sources.queue("ai-toolkit", own_result(image_path=sample))

            outcome = self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)]), model=model)
            self.assertEqual(outcome, FallbackOutcome(source="ai-toolkit", preview_written=False, info_written=True))
            self.assertFalse(preview_sidecar_path(model).exists())

    def test_fallback_stub_is_not_a_renamer_entry(self):
        write_json(self.info_path, stub())
        self.sources.queue("huggingface", hf_result())
        self.run_on(self.fallback())

        known = self.model_file("known")
        write_json(info_sidecar_path(known), {"id": 88, "modelId": 77, "name": "v1"})
        paths = [entry.path for entry in renamer.collect_entries({"lora": [self.lib]})]
        self.assertEqual(paths, [known])

    def test_text_is_capped(self):
        self.sources.queue(
            "huggingface",
            hf_result(
                title="x" * 500, creator="c" * 500, version_name="v" * 500, base_model="b" * 500,
                trained_words=["w" * 300] * 30, description="d" * 9_000,
            ),
        )

        self.run_on(self.fallback())
        info = read_json(self.info_path)
        self.assertEqual(info["model"]["name"], "x" * 200)
        self.assertEqual(info["creator"]["username"], "c" * 200)
        self.assertEqual(info["name"], "v" * 200)
        self.assertEqual(info["baseModel"], "b" * 200)
        self.assertEqual(info["trainedWords"], ["w" * 100] * 20)
        self.assertEqual(info["description"], "d" * 5_000)

    # Misses

    def test_miss_recorded_and_skipped_then_refetch_asks_again(self):
        self.assertIsNone(self.run_on(self.fallback()))
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])
        self.assertEqual(self.misses(), {SHA: {"checkedAt": NOW.isoformat(), "sources": "civarchive|someone"}})

        self.sources.log.clear()
        self.assertIsNone(self.run_on(self.fallback()))  # a later scan
        self.assertEqual(self.sources.log, [])

        self.assertIsNone(self.run_on(self.fallback(), refetch=True))
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])

    def test_hit_forgets_an_earlier_miss(self):
        self.run_on(self.fallback())
        self.assertIn(SHA, self.misses())

        self.sources.queue("civarchive", civ_result())
        self.assertIsNotNone(self.run_on(self.fallback(), refetch=True))
        self.assertEqual(self.misses(), {})

    def test_unavailable_source_is_not_a_miss(self):
        self.sources.queue("civarchive", SourceUnavailable("timeout"))
        self.assertIsNone(self.run_on(self.fallback()))
        self.assertEqual(self.misses(), {})

        self.sources.log.clear()
        self.run_on(self.fallback())
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])

    def test_changed_sources_retry_a_miss(self):
        self.run_on(self.fallback(fallbackHuggingFace=[]))
        self.assertEqual(self.misses()[SHA]["sources"], "civarchive")

        self.sources.log.clear()
        self.run_on(self.fallback(fallbackHuggingFace=["x/y"]))
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])
        self.assertEqual(self.misses()[SHA]["sources"], "civarchive|x/y")

    def test_miss_expires_after_30_days(self):
        self.run_on(self.fallback())

        self.sources.log.clear()
        self.run_on(self.fallback(now=NOW + timedelta(days=29)))
        self.assertEqual(self.sources.log, [])

        self.run_on(self.fallback(now=NOW + timedelta(days=31)))
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])

    def test_no_hash_skips_online_sources(self):
        for sha in ("", "not-a-hash"):
            self.sources.log.clear()
            self.assertIsNone(self.run_on(self.fallback(fallbackAiToolkitFolders=[str(self.out)]), sha=sha))
            self.assertEqual(self.sources.log, ["ai-toolkit"])
        self.assertFalse((self.data / "preview_fallback.json").exists())

    def test_source_dropped_after_three_failures_in_a_row(self):
        self.sources.queue("civarchive", *[SourceUnavailable("HTTP 503", status=503)] * 3)
        fb = self.fallback()
        for n in (1, 2, 3):
            self.run_on(fb, model=self.model_file(f"f{n}"), sha=sha_for(n))

        self.sources.log.clear()
        self.run_on(
            fb, model=self.model_file("f4"), sha=sha_for(4),
            on_source=lambda key: self.sources.log.append("announce " + key),
        )
        self.assertEqual(self.sources.log, ["announce huggingface", "huggingface"])
        self.assertFalse((self.data / "preview_fallback.json").exists())

    def test_failure_count_resets_on_an_answer(self):
        down = SourceUnavailable("timeout")
        self.sources.queue("civarchive", down, down, None, down, down)
        fb = self.fallback()
        for n in range(1, 6):
            self.run_on(fb, model=self.model_file(f"f{n}"), sha=sha_for(n))

        self.sources.log.clear()
        self.run_on(fb, model=self.model_file("f6"), sha=sha_for(6))
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])

    def test_corrupt_miss_file_is_ignored(self):
        self.data.mkdir()
        (self.data / "preview_fallback.json").write_text("{x", encoding="utf-8")

        self.run_on(self.fallback())
        self.assertEqual(self.sources.log, ["huggingface", "civarchive"])
        self.assertEqual(self.misses(), {SHA: {"checkedAt": NOW.isoformat(), "sources": "civarchive|someone"}})

    # Enabled

    def test_is_enabled_when_any_source_is_configured(self):
        off = {"fallbackCivarchive": False, "fallbackHuggingFace": [], "fallbackAiToolkitFolders": []}
        self.assertFalse(PreviewFallback.is_enabled(off))
        self.assertFalse(PreviewFallback.is_enabled({}))
        self.assertFalse(PreviewFallback.is_enabled({**off, "fallbackHuggingFace": ["  "]}))
        for key, value in (
            ("fallbackCivarchive", True),
            ("fallbackHuggingFace", ["someone"]),
            ("fallbackAiToolkitFolders", [str(self.out)]),
        ):
            config = {**off, key: value}
            self.assertTrue(PreviewFallback.is_enabled(config), key)
            fb = PreviewFallback(config, self.data, self.http, {}, now=lambda: NOW)
            self.assertTrue(fb.enabled, key)
        self.assertFalse(PreviewFallback(off, self.data, self.http, {}, now=lambda: NOW).enabled)


class MissStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "preview_fallback.json"

    def test_wrong_shapes_and_bad_dates_are_not_fresh(self):
        for text in (
            "[1, 2]",
            '{"misses": []}',
            json.dumps({"misses": {SHA: "x"}}),
            json.dumps({"misses": {SHA: {"sources": "civarchive"}}}),
            json.dumps({"misses": {SHA: {"checkedAt": "yesterday", "sources": "civarchive"}}}),
            json.dumps({"misses": {SHA: {"checkedAt": 5, "sources": "civarchive"}}}),
            json.dumps({"misses": {SHA: {"checkedAt": "2026-11-17T00:00:00", "sources": "civarchive"}}}),
            json.dumps({"misses": {SHA: {"checkedAt": (NOW + timedelta(days=2)).isoformat(), "sources": "civarchive"}}}),
            json.dumps({"misses": {SHA: {"checkedAt": NOW.isoformat(), "sources": ["civarchive"]}}}),
        ):
            self.path.write_text(text, encoding="utf-8")
            self.assertFalse(MissStore(self.path).is_fresh(SHA, "civarchive", NOW), text)

    def test_record_rewrites_a_bad_file_and_forget_removes(self):
        self.path.write_text('{"misses": []}', encoding="utf-8")
        store = MissStore(self.path)
        store.record(SHA.upper(), "civarchive", NOW)
        self.assertEqual(
            read_json(self.path), {"misses": {SHA: {"checkedAt": NOW.isoformat(), "sources": "civarchive"}}}
        )
        self.assertTrue(MissStore(self.path).is_fresh(SHA, "civarchive", NOW + timedelta(days=1)))

        store.forget(SHA)
        self.assertEqual(read_json(self.path), {"misses": {}})


class ApplyToInfoTests(unittest.TestCase):
    def test_does_not_change_its_input_and_returns_none_when_nothing_changes(self):
        info = stub()
        before = copy.deepcopy(info)
        new = apply_to_info(info, hf_result(), SHA, "x.safetensors", NOW.isoformat())
        self.assertEqual(info, before)
        new["files"][0]["hashes"]["SHA256"] = "changed"
        new["extensions"]["source"] = "changed"
        self.assertEqual(info, before)

        again = apply_to_info(info, hf_result(), SHA, "x.safetensors", NOW.isoformat())
        self.assertIsNone(apply_to_info(again, hf_result(), SHA, "x.safetensors", "later"))

    def test_identified_info_is_never_changed(self):
        info = {"id": 88, "modelId": 77}
        self.assertIsNone(apply_to_info(info, civ_result(), SHA, "x.safetensors", NOW.isoformat()))

    def test_only_empty_fields_are_filled(self):
        info = {
            **stub(),
            "name": "Mine",
            "baseModel": "Other",
            "trainedWords": ["mine"],
            "model": {"name": "Kept", "nsfw": False},
            "creator": {"username": "me"},
            "images": [{"url": "https://huggingface.co/kept.png", "type": "image", "nsfwLevel": 1}],
        }
        new = apply_to_info(info, civ_result(civitai_model_id="", civitai_version_id=""), SHA, "x.safetensors", "t")
        for key in ("name", "baseModel", "trainedWords", "model", "creator", "images", "id", "modelId"):
            self.assertEqual(new[key], info[key], key)
        self.assertEqual(new["description"], "A style.")
        self.assertEqual(new["extensions"]["fallback"]["source"], "civarchive")

    def test_ids_need_both_and_must_be_civitai_ids(self):
        for model_id, version_id in (("77", ""), ("", "88"), ("abc", "88"), ("77", "8.8")):
            new = apply_to_info(
                stub(), civ_result(civitai_model_id=model_id, civitai_version_id=version_id), SHA, "x.safetensors", "t"
            )
            self.assertEqual((new["modelId"], new["id"]), ("", ""), (model_id, version_id))


if __name__ == "__main__":
    unittest.main()
