from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from multidict import CIMultiDict

from comfy_updater import civitai_client
from comfy_updater.civitai_client import CivitaiClient, _refuse_foreign_redirect
from comfy_updater.path_resolver import custom_path_problem, resolve_model_roots
from comfy_updater.request_guard import request_rejection
from comfy_updater.routes import _drop_refused_custom_paths, _guarded

HOST = "127.0.0.1:8188"
PANEL = {"X-Civitai-Updater": "1"}
JSON_POST = {**PANEL, "Content-Type": "application/json"}


def _headers(**values) -> CIMultiDict:
    return CIMultiDict(values)


class RequestGuardTests(unittest.TestCase):
    def test_panel_requests_pass(self) -> None:
        self.assertIsNone(request_rejection("GET", _headers(**PANEL), HOST))
        self.assertIsNone(
            request_rejection(
                "POST",
                _headers(**JSON_POST, Origin="http://127.0.0.1:8188", **{"Sec-Fetch-Site": "same-origin"}),
                HOST,
            )
        )
        self.assertIsNone(
            request_rejection("POST", _headers(**{**JSON_POST, "Content-Type": "application/json; charset=utf-8"}), HOST)
        )

    def test_header_names_are_case_insensitive(self) -> None:
        self.assertIsNone(request_rejection("GET", _headers(**{"x-civitai-updater": "1"}), HOST))

    def test_missing_guard_header_is_refused(self) -> None:
        self.assertIsNotNone(request_rejection("GET", _headers(), HOST))
        self.assertIsNotNone(request_rejection("POST", _headers(**{"Content-Type": "application/json"}), HOST))

    def test_cross_site_requests_are_refused(self) -> None:
        for origin in ("https://evil.example", "http://127.0.0.1:9999", "null", "file://"):
            self.assertIsNotNone(request_rejection("POST", _headers(**JSON_POST, Origin=origin), HOST), origin)
        self.assertIsNotNone(
            request_rejection("GET", _headers(**PANEL, **{"Sec-Fetch-Site": "cross-site"}), HOST)
        )

    def test_simple_request_body_types_are_refused(self) -> None:
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            headers = _headers(**{**PANEL, "Content-Type": content_type})
            self.assertIsNotNone(request_rejection("POST", headers, HOST), content_type)


class GuardedHandlerTests(unittest.TestCase):
    def _call(self, headers: dict):
        calls = []

        async def handler(request):
            calls.append(request)
            return "handled"

        request = MagicMock(method="POST", host=HOST, headers=_headers(**headers))
        return asyncio.run(_guarded(handler)(request)), calls

    def test_refused_request_never_reaches_the_handler(self) -> None:
        response, calls = self._call({"Content-Type": "text/plain"})
        self.assertEqual([], calls)
        self.assertEqual(403, response.status)

    def test_panel_request_reaches_the_handler(self) -> None:
        response, calls = self._call(JSON_POST)
        self.assertEqual("handled", response)
        self.assertEqual(1, len(calls))


class CustomPathTests(unittest.TestCase):
    def test_existing_absolute_folder_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(custom_path_problem(tmpdir))

    def test_unusable_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            a_file = Path(tmpdir) / "model.safetensors"
            a_file.write_bytes(b"x")
            for path in ("", "relative/models", str(Path(tmpdir) / "missing"), str(a_file), Path(tmpdir).anchor):
                self.assertIsNotNone(custom_path_problem(path), path)

    def test_only_new_paths_are_checked_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gone = str(Path(tmpdir) / "unplugged-drive")

            incoming = {"customPaths": {"lora": [tmpdir, gone]}}
            self.assertEqual([], _drop_refused_custom_paths(incoming, {"lora": [gone]}))
            self.assertEqual([tmpdir, gone], incoming["customPaths"]["lora"])

            incoming = {"customPaths": {"lora": [tmpdir, gone]}}
            warnings = _drop_refused_custom_paths(incoming, {"lora": []})
            self.assertEqual([tmpdir], incoming["customPaths"]["lora"])
            self.assertEqual(1, len(warnings))
            self.assertIn("unplugged-drive", warnings[0])

    def test_scan_skips_stored_paths_that_are_not_valid_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "useComfyPaths": False,
                "useExtraModelPaths": False,
                "useCustomPaths": True,
                "customPaths": {"lora": [tmpdir, Path(tmpdir).anchor, "relative"]},
            }
            roots = resolve_model_roots(config, ["lora"], include_custom_paths=True)
            self.assertEqual([Path(tmpdir).resolve()], roots["lora"])


class PreviewDownloadHostTests(unittest.TestCase):
    def test_previews_are_fetched_only_from_civitai(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=0)
            client.session = MagicMock()
            target = Path(tmpdir) / "preview.png"
            for url in (
                "http://127.0.0.1:8188/api/view",
                "https://example.com/x.png",
                "http://image.civitai.com/x.png",
                "file:///etc/passwd",
            ):
                self.assertFalse(client.download_image_as_png(url, target), url)
                self.assertFalse(client.download_video_first_frame_as_png(url, target), url)
            client.session.get.assert_not_called()

    def test_redirect_must_stay_on_civitai(self) -> None:
        def redirect(location: str) -> MagicMock:
            response = MagicMock()
            response.is_redirect = True
            response.url = "https://image.civitai.com/a/b.jpeg"
            response.headers = {"Location": location}
            return response

        _refuse_foreign_redirect(redirect("https://blobs-b2.civitai.com/file/x"))
        for location in ("http://127.0.0.1/admin", "https://evil.example/x", "http://blobs-b2.civitai.com/x"):
            with self.assertRaises(requests.TooManyRedirects, msg=location):
                _refuse_foreign_redirect(redirect(location))


class FfmpegInputTests(unittest.TestCase):
    def test_ffmpeg_reads_only_local_video_containers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video = Path(tmpdir) / "v.mp4"
            with patch.object(civitai_client.subprocess, "run") as run:
                run.return_value = MagicMock(returncode=1)
                civitai_client._extract_first_frame("ffmpeg", video, Path(tmpdir) / "f.png", Path(tmpdir) / "p.png")
            args = run.call_args.args[0]
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual("file", args[args.index("-protocol_whitelist") + 1])
            self.assertIn("-format_whitelist", args)
            self.assertLess(args.index("-format_whitelist"), args.index("-i"))


if __name__ == "__main__":
    unittest.main()
