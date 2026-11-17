from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from comfy_updater.civitai_client import CivitaiRequestError
from comfy_updater.config_store import ConfigStore
from comfy_updater.jobs import JobControl
from comfy_updater.preview_fallback import FallbackOutcome, PreviewFallback
from comfy_updater.sidecar import info_sidecar_path, preview_sidecar_path, read_json, write_json
from comfy_updater.updater_service import UpdaterService

PNG = b"\x89PNG\r\n\x1a\n" + b"made-up picture"
HF_IMAGE = "https://huggingface.co/example/repo/resolve/main/a.png"


def sha_of(name: str) -> str:
    """A made-up, stable SHA256 per file name."""
    return (format(sum(map(ord, name)), "x") * 64)[:64]


class NoHashClient:
    """Civitai as a fake that knows no hashes; a model page lookup answers 404."""

    def __init__(self, **kwargs) -> None:  # noqa: ARG002
        pass

    def get_version_by_hash(self, sha256):  # noqa: ARG002
        return None

    def get_version(self, version_id):  # noqa: ARG002
        return None

    def get_model_versions_for_model(self, model_id):
        raise CivitaiRequestError(f"https://civitai.red/api/v1/models/{model_id} :: 404 Not Found")

    def model_page_url(self, model_id, nsfw=False):  # noqa: ARG002
        return f"https://civitai.red/models/{model_id}"

    def version_page_url(self, model_id, version_id=None, nsfw=False):  # noqa: ARG002
        return f"https://civitai.red/models/{model_id}?modelVersionId={version_id}"


class ErroringHashClient(NoHashClient):
    """Civitai failing every hash lookup."""

    def get_version_by_hash(self, sha256):  # noqa: ARG002
        raise CivitaiRequestError("https://civitai.red/api/v1/model-versions/by-hash :: 500 Internal Server Error")


class FakeFallbacks:
    """Stands in for the PreviewFallback class: building it returns this object, which logs each run.

    ``answers`` maps a file name to an outcome or an exception; ``sources`` are announced on every run.
    """

    is_enabled = staticmethod(PreviewFallback.is_enabled)

    def __init__(self, answers: dict | None = None, sources: tuple = ()) -> None:
        self.answers = answers or {}
        self.sources = sources
        self.built: list[tuple] = []
        self.calls: list[dict] = []

    def __call__(self, config, data_dir, http, base_codes):
        self.built.append((config, data_dir, http, base_codes))
        return self

    def run(self, *, model_path, info_path, preview_path, sha256, refetch, on_source=None):
        self.calls.append({
            "name": Path(model_path).name, "info": Path(info_path), "preview": Path(preview_path),
            "sha256": sha256, "refetch": refetch,
        })
        for source in self.sources:
            on_source(source)
        answer = self.answers.get(Path(model_path).name)
        if isinstance(answer, Exception):
            raise answer
        if answer is not None and answer.preview_written:
            Path(preview_path).write_bytes(PNG)
        return answer


class StopInSecondWait:
    """A job control that is stopped while waiting out a pause the second time it is asked to."""

    def __init__(self) -> None:
        self.waits = 0
        self.cancelled = False

    def wait_if_paused(self) -> None:
        self.waits += 1
        if self.waits == 2:
            self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


def fallback_stub(name: str, sha: str, **extra) -> dict:
    info = {
        "id": "",
        "modelId": "",
        "name": name,
        "baseModel": "Krea 2",
        "model": {"name": "Cool", "nsfw": True},
        "creator": {"username": "someone"},
        "images": [{"url": HF_IMAGE, "type": "image", "nsfwLevel": 1}],
        "files": [{"hashes": {"SHA256": sha}}],
        "extensions": {
            "source": "comfy-civitai-updater",
            "fallback": {"source": "huggingface", "url": "https://huggingface.co/example/repo", "foundAt": "x"},
        },
    }
    info.update(extra)
    return info


class FallbackServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.models = root / "models"
        self.models.mkdir()
        self.store = ConfigStore(root / "data")
        self.store.update({
            "useComfyPaths": False,
            "useExtraModelPaths": False,
            "requestDelayMs": 0,
            "requestTimeoutSeconds": 17,
            "customPaths": {"lora": [str(self.models)]},
        })
        self.hashes: dict[str, str] = {}
        self.http = MagicMock(name="FallbackHttp")
        self.client = NoHashClient
        self.progress: list[tuple[int, int, str]] = []

    def model(self, name: str, info: dict | None = None, preview: bytes | None = None) -> Path:
        path = self.models / name
        path.write_bytes(b"weights of " + name.encode())
        if info is not None:
            write_json(info_sidecar_path(path), info)
        if preview is not None:
            preview_sidecar_path(path).write_bytes(preview)
        return path

    def _hash(self, path, should_stop=None):  # noqa: ARG002
        return self.hashes.get(Path(path).name) or sha_of(Path(path).name)

    def run_job(self, mode: str = "scan", fallbacks: FakeFallbacks | None = None, control=None, **payload):
        service = UpdaterService(self.store)
        method = service.run_scan if mode == "scan" else service.run_check_updates
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("comfy_updater.updater_service.CivitaiClient", self.client))
            stack.enter_context(patch("comfy_updater.updater_service.FallbackHttp", self.http))
            stack.enter_context(patch("comfy_updater.updater_service.sha256_file", side_effect=self._hash))
            if fallbacks is not None:
                stack.enter_context(patch("comfy_updater.updater_service.PreviewFallback", fallbacks))
            summary, items = method(
                {"modelTypes": ["lora"], **payload},
                lambda current, total, message: self.progress.append((current, total, message)),
                control=control,
            )
        return summary, {Path(item["modelPath"]).name: item for item in items}

    # Where it runs

    def test_scan_runs_fallback_only_without_preview(self) -> None:
        self.model("a.safetensors", preview=PNG)
        b = self.model("b.safetensors")
        fallbacks = FakeFallbacks()
        control = JobControl()

        _summary, items = self.run_job(fallbacks=fallbacks, control=control)

        self.assertEqual({"not_found"}, {item["status"] for item in items.values()})
        self.assertEqual(["b.safetensors"], [call["name"] for call in fallbacks.calls])
        call = fallbacks.calls[0]
        self.assertEqual((info_sidecar_path(b), preview_sidecar_path(b)), (call["info"], call["preview"]))
        self.assertEqual((sha_of("b.safetensors"), False), (call["sha256"], call["refetch"]))
        self.http.assert_called_once_with(timeout_seconds=17, should_stop=control.is_cancelled)

    def test_refetch_never_asks_the_fallback_about_a_file_with_a_preview(self) -> None:
        a = self.model("a.safetensors", preview=PNG)
        fallbacks = FakeFallbacks(answers={
            "a.safetensors": FallbackOutcome(source="huggingface", preview_written=True, info_written=True),
        })

        _summary, items = self.run_job(fallbacks=fallbacks, refetchMetadata=True)

        self.assertEqual([], fallbacks.calls)
        self.assertEqual([], fallbacks.built)
        self.http.assert_not_called()
        self.assertEqual(PNG, preview_sidecar_path(a).read_bytes())
        self.assertTrue(items["a.safetensors"]["hasLocalPreview"])

    def test_scan_runs_fallback_for_identified_files_too(self) -> None:
        # A removed model's info file with ids but no picture: the stored hash is what is asked about.
        self.model("a.safetensors", info={"id": 88, "modelId": 77, "files": [{"hashes": {"SHA256": "AB" * 32}}]})
        fallbacks = FakeFallbacks()

        _summary, items = self.run_job(fallbacks=fallbacks, refetchMetadata=False)

        self.assertEqual("skipped", items["a.safetensors"]["status"])
        self.assertEqual(["ab" * 32], [call["sha256"] for call in fallbacks.calls])

    def test_scan_builds_the_fallback_once_with_the_data_dir_and_base_codes(self) -> None:
        write_json(self.store.data_dir / "base_codes.json", {"codes": {"Krea 2": "krea2"}, "seeded": True})
        self.model("a.safetensors")
        self.model("b.safetensors")
        fallbacks = FakeFallbacks()

        self.run_job(fallbacks=fallbacks, refetchMetadata=True)

        self.assertEqual(2, len(fallbacks.calls))
        self.assertTrue(all(call["refetch"] for call in fallbacks.calls))
        self.assertEqual(1, len(fallbacks.built))
        config, data_dir, http, base_codes = fallbacks.built[0]
        self.assertEqual(["malcolmrey"], config["fallbackHuggingFace"])
        self.assertEqual(self.store.data_dir, data_dir)
        self.assertIs(self.http.return_value, http)
        self.assertEqual({"Krea 2": "krea2"}, base_codes)
        self.http.assert_called_once()

    def test_scan_reads_base_codes_without_rewriting_them(self) -> None:
        codes_path = self.store.data_dir / "base_codes.json"
        # Compact, unsorted and with entries the renamer's store would drop: any rewrite shows.
        original = b'{"codes":{"Zeta":"z","Krea 2":"krea2","Empty":"","Number":7},"seeded":true}'
        codes_path.write_bytes(original)
        self.model("a.safetensors")
        fallbacks = FakeFallbacks()

        self.run_job(fallbacks=fallbacks)

        self.assertEqual(original, codes_path.read_bytes())
        self.assertEqual({"Zeta": "z", "Krea 2": "krea2"}, fallbacks.built[0][3])

    def test_scan_passes_no_base_codes_for_a_missing_or_corrupt_file(self) -> None:
        codes_path = self.store.data_dir / "base_codes.json"
        self.model("a.safetensors")
        for content in (None, b"{not json", b'["krea2"]', b'{"codes": ["krea2"]}'):
            with self.subTest(content=content):
                if content is None:
                    codes_path.unlink(missing_ok=True)
                else:
                    codes_path.write_bytes(content)
                fallbacks = FakeFallbacks()

                self.run_job(fallbacks=fallbacks)

                self.assertEqual({}, fallbacks.built[0][3])
                if content is None:
                    self.assertFalse(codes_path.exists())
                else:
                    self.assertEqual(content, codes_path.read_bytes())  # never moved aside or replaced

    def test_scan_summary_counts_previews_found(self) -> None:
        self.model("a.safetensors")
        self.model("b.safetensors")
        fallbacks = FakeFallbacks({
            "a.safetensors": FallbackOutcome("huggingface", preview_written=True, info_written=True),
            "b.safetensors": FallbackOutcome("civarchive", preview_written=False, info_written=True),
        })

        summary, items = self.run_job(fallbacks=fallbacks)

        self.assertEqual(1, summary["previewsFound"])
        self.assertEqual("huggingface", items["a.safetensors"]["fallbackSource"])
        self.assertEqual("civarchive", items["b.safetensors"]["fallbackSource"])
        self.assertTrue(items["a.safetensors"]["hasLocalPreview"])
        self.assertFalse(items["b.safetensors"]["hasLocalPreview"])

    def test_check_does_not_run_fallback(self) -> None:
        self.model("a.safetensors")
        fallbacks = FakeFallbacks()

        summary, items = self.run_job("check", fallbacks=fallbacks)

        self.assertEqual([], fallbacks.calls)
        self.assertEqual([], fallbacks.built)
        self.http.assert_not_called()
        self.assertNotIn("previewsFound", summary)
        item = items["a.safetensors"]
        self.assertEqual(("not_found", ""), (item["status"], item["fallbackSource"]))

    def test_every_item_reports_has_local_preview(self) -> None:
        self.model("a.safetensors", preview=PNG)
        self.model("b.safetensors")
        self.model("c.safetensors", info={"id": 88, "modelId": 77})
        identified = self.models / "d.civitai.info"  # metadata-only: info file without weights
        write_json(identified, {"id": 89, "modelId": 78})
        (self.models / "d.preview.png").write_bytes(PNG)

        for mode in ("scan", "check"):
            with self.subTest(mode=mode):
                _summary, items = self.run_job(mode, fallbacks=FakeFallbacks())
                self.assertEqual(
                    {"a.safetensors": True, "b.safetensors": False, "c.safetensors": False, "d.civitai.info": True},
                    {name: item["hasLocalPreview"] for name, item in items.items()},
                )
                self.assertEqual({""}, {item["fallbackSource"] for item in items.values()})

    def test_items_that_failed_still_report_preview_and_source(self) -> None:
        self.model("a.safetensors")

        def broken_hash(path, should_stop=None):  # noqa: ARG001
            raise OSError("locked")

        self._hash = broken_hash
        fallbacks = FakeFallbacks()
        _summary, items = self.run_job(fallbacks=fallbacks)

        item = items["a.safetensors"]
        self.assertEqual(("error", False, ""), (item["status"], item["hasLocalPreview"], item["fallbackSource"]))
        self.assertEqual([], fallbacks.calls)

    def test_scan_skips_the_fallback_when_civitai_errored(self) -> None:
        # Civitai may serve the file next time; a fallback preview now would block its own picture.
        self.model("a.safetensors")
        self.model("b.safetensors")
        fallbacks = FakeFallbacks()
        self.client = ErroringHashClient

        _summary, items = self.run_job(fallbacks=fallbacks)

        self.assertEqual({"error"}, {item["status"] for item in items.values()})
        self.assertEqual({""}, {item["fallbackSource"] for item in items.values()})
        self.assertEqual([], fallbacks.calls)
        self.assertEqual([], fallbacks.built)
        self.http.assert_not_called()

    def test_progress_names_each_source_asked(self) -> None:
        self.model("a.safetensors", preview=PNG)
        b = self.model("b.safetensors")
        fallbacks = FakeFallbacks(sources=("ai-toolkit", "huggingface", "civarchive"))

        self.run_job(fallbacks=fallbacks)

        index = next(current for current, _total, message in self.progress if message == "scan: b.safetensors")
        fallback_lines = [
            entry for entry in self.progress
            if entry[2].endswith(": " + b.name) and not entry[2].startswith("scan:")
        ]
        self.assertEqual(
            [
                (index, 2, "Looking in your ai-toolkit samples: b.safetensors"),
                (index, 2, "Asking Hugging Face for a preview: b.safetensors"),
                (index, 2, "Asking civarchive for a preview: b.safetensors"),
            ],
            fallback_lines,
        )

    def test_a_fallback_error_never_stops_the_scan(self) -> None:
        self.model("a.safetensors")
        self.model("b.safetensors")
        fallbacks = FakeFallbacks({
            "a.safetensors": OSError("disk full"),
            "b.safetensors": FallbackOutcome("huggingface", preview_written=True, info_written=True),
        })
        printed = io.StringIO()

        with contextlib.redirect_stdout(printed):
            summary, items = self.run_job(fallbacks=fallbacks)

        a, b = items["a.safetensors"], items["b.safetensors"]
        self.assertEqual(("not_found", ""), (a["status"], a["fallbackSource"]))
        self.assertEqual(("not_found", "huggingface"), (b["status"], b["fallbackSource"]))
        self.assertEqual((0, 2, 1), (summary["errors"], summary["notFound"], summary["previewsFound"]))
        log = printed.getvalue()
        self.assertIn("a.safetensors", log)
        self.assertIn("disk full", log)
        self.assertNotIn(str(self.models), log)

    def test_stop_while_paused_before_the_fallback_skips_it(self) -> None:
        self.model("a.safetensors")
        fallbacks = FakeFallbacks()
        control = StopInSecondWait()

        _summary, items = self.run_job(fallbacks=fallbacks, control=control)

        self.assertEqual(2, control.waits)  # before the Civitai step, then before the fallback
        self.assertEqual([], fallbacks.calls)
        self.assertEqual({}, items)

    def test_fallback_off_makes_no_http(self) -> None:
        self.store.update({"fallbackHuggingFace": [], "fallbackCivarchive": False, "fallbackAiToolkitFolders": []})
        self.model("a.safetensors")

        summary, items = self.run_job()

        self.http.assert_not_called()
        self.assertEqual(0, summary["previewsFound"])
        item = items["a.safetensors"]
        self.assertEqual(("not_found", "", False), (item["status"], item["fallbackSource"], item["hasLocalPreview"]))

    # Stub rewrite

    def test_rescan_keeps_fallback_fields(self) -> None:
        sha = sha_of("a.safetensors")
        a = self.model("a.safetensors", info=fallback_stub("a.safetensors", sha), preview=PNG)
        before = info_sidecar_path(a).read_bytes()

        for _ in range(2):
            _summary, items = self.run_job(fallbacks=FakeFallbacks())

        self.assertEqual(before, info_sidecar_path(a).read_bytes())
        self.assertEqual("Cool", read_json(info_sidecar_path(a))["model"]["name"])
        item = items["a.safetensors"]
        self.assertEqual(("Cool", "huggingface"), (item["modelName"], item["fallbackSource"]))

    def test_rescan_keeps_fallback_fields_without_a_stored_hash(self) -> None:
        # No recorded hash is no sign the file was replaced.
        a = self.model("a.safetensors", info=fallback_stub("v1", ""), preview=PNG)
        before = info_sidecar_path(a).read_bytes()

        _summary, items = self.run_job(fallbacks=FakeFallbacks())

        self.assertEqual(before, info_sidecar_path(a).read_bytes())
        self.assertEqual("huggingface", items["a.safetensors"]["fallbackSource"])

    def test_rescan_compares_hashes_case_insensitively(self) -> None:
        sha = sha_of("a.safetensors")
        a = self.model("a.safetensors", info=fallback_stub("a.safetensors", sha.upper()), preview=PNG)
        before = info_sidecar_path(a).read_bytes()

        self.run_job(fallbacks=FakeFallbacks(), forceRehash=True)

        self.assertEqual(before, info_sidecar_path(a).read_bytes())

    def test_rescan_with_new_hash_resets_stub(self) -> None:
        a = self.model("a.safetensors", info=fallback_stub("v1", "ab" * 32), preview=PNG)
        self.hashes["a.safetensors"] = "cd" * 32
        fallbacks = FakeFallbacks()

        _summary, items = self.run_job(fallbacks=fallbacks, forceRehash=True)

        info = read_json(info_sidecar_path(a))
        self.assertNotIn("fallback", info["extensions"])
        self.assertNotIn("model", info)
        self.assertEqual("cd" * 32, info["files"][0]["hashes"]["SHA256"])
        self.assertEqual(PNG, preview_sidecar_path(a).read_bytes())
        self.assertEqual([], fallbacks.calls)  # the preview is still there
        item = items["a.safetensors"]
        self.assertEqual(("", "", ""), (item["modelName"], item.get("creatorName", ""), item["fallbackSource"]))

    def test_rescan_with_new_hash_resets_a_fallback_file_with_civitai_ids(self) -> None:
        # A civarchive hit on a Civitai-platform model wrote ids; the file was replaced since.
        info = fallback_stub("v2", "ab" * 32, id=88, modelId=77)
        info["extensions"]["fallback"]["source"] = "civarchive"
        a = self.model("a.safetensors", info=info, preview=PNG)
        self.hashes["a.safetensors"] = "cd" * 32

        _summary, items = self.run_job(fallbacks=FakeFallbacks(), refetchMetadata=True, forceRehash=True)

        info = read_json(info_sidecar_path(a))
        self.assertNotIn("fallback", info["extensions"])
        self.assertEqual(("", ""), (info["id"], info["modelId"]))
        self.assertEqual("cd" * 32, info["files"][0]["hashes"]["SHA256"])
        self.assertEqual(PNG, preview_sidecar_path(a).read_bytes())
        item = items["a.safetensors"]
        self.assertEqual(("not_found", ""), (item["status"], item["fallbackSource"]))

        _summary, items = self.run_job("check")

        item = items["a.safetensors"]
        self.assertEqual(("not_found", "", ""), (item["status"], item.get("creatorName", ""), item["fallbackSource"]))

    def test_rescan_with_new_hash_keeps_a_civitai_info_file(self) -> None:
        # Without a fallback record a failed lookup never replaces an info file that identifies the model.
        a = self.model(
            "a.safetensors",
            info={"id": 88, "modelId": 77, "name": "v2", "files": [{"hashes": {"SHA256": "ab" * 32}}]},
            preview=PNG,
        )
        before = info_sidecar_path(a).read_bytes()
        self.hashes["a.safetensors"] = "cd" * 32

        _summary, items = self.run_job(fallbacks=FakeFallbacks(), refetchMetadata=True, forceRehash=True)

        self.assertEqual(before, info_sidecar_path(a).read_bytes())
        self.assertEqual("not_found", items["a.safetensors"]["status"])

    # Check mode and Issues

    def test_check_not_found_carries_fallback_fields(self) -> None:
        sha = sha_of("a.safetensors")
        for stored_name, version_name in (("v1", "v1"), ("a.safetensors", "")):
            with self.subTest(stored_name=stored_name):
                self.model("a.safetensors", info=fallback_stub(stored_name, sha), preview=PNG)

                _summary, items = self.run_job("check")

                item = items["a.safetensors"]
                self.assertEqual("not_found", item["status"])
                self.assertEqual((item["modelName"], item["creatorName"], item["baseModel"], item["fallbackSource"]),
                                 ("Cool", "someone", "Krea 2", "huggingface"))
                self.assertEqual(version_name, item["localVersionName"])
                self.assertTrue(item["nsfw"])
                self.assertEqual((HF_IMAGE, "image"), (item["localPreviewUrl"], item["localPreviewType"]))
                self.assertTrue(item["hasLocalPreview"])

    def test_check_not_found_without_fallback_info_is_unchanged(self) -> None:
        self.model("a.safetensors", info={"id": "", "modelId": "", "name": "a.safetensors"})

        _summary, items = self.run_job("check")

        item = items["a.safetensors"]
        self.assertEqual(
            ("", "", False, ""), (item["modelName"], item["baseModel"], item["nsfw"], item["fallbackSource"])
        )

    def test_check_error_reads_creator_and_source(self) -> None:
        info = fallback_stub("v2", sha_of("a.safetensors"), id=88, modelId=77)
        info["extensions"]["fallback"]["source"] = "civarchive"
        self.model("a.safetensors", info=info, preview=PNG)

        _summary, items = self.run_job("check")

        item = items["a.safetensors"]
        self.assertEqual("error", item["status"])
        self.assertIn("404", item["error"])
        self.assertEqual(("someone", "civarchive"), (item["creatorName"], item["fallbackSource"]))

    def test_check_error_without_creator_or_source(self) -> None:
        self.model("a.safetensors", info={"id": 88, "modelId": 77, "name": "v2"})

        _summary, items = self.run_job("check")

        item = items["a.safetensors"]
        self.assertEqual(("error", "", ""), (item["status"], item["creatorName"], item["fallbackSource"]))


if __name__ == "__main__":
    unittest.main()
