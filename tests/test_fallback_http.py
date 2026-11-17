from __future__ import annotations

import errno
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

from comfy_updater.fallback_http import (
    FallbackHttp,
    _refuse_redirect_off_list,
    is_allowed_url,
    save_preview_png,
)
from comfy_updater.fallback_types import SourceUnavailable


def _jpeg_bytes(color: str = "blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="JPEG")
    return buf.getvalue()


def _resp(status=200, json_data=None, links=None, chunks=None, json_error=False):
    resp = MagicMock()
    resp.status_code = status
    resp.links = links or {}
    if json_error:
        resp.json.side_effect = ValueError("bad json")
    else:
        resp.json.return_value = json_data
    resp.iter_content.return_value = iter(chunks or [])
    return resp


class AllowListTests(unittest.TestCase):
    def test_allow_list(self):
        for url, ok in (
            ("https://huggingface.co/api/models/x", True),
            ("https://cdn-lfs-us-1.hf.co/a", True),
            ("https://cas-bridge.xethub.hf.co/a", True),
            ("https://civarchive.com/sha256/ab", True),
            ("https://c.genur.art/u_small.webp", True),
            ("http://huggingface.co/x", False),
            ("https://huggingface.co.evil.com/x", False),
            ("https://127.0.0.1/x", False),
            ("https://civitai.com/x", False),
        ):
            self.assertEqual(is_allowed_url(url), ok, url)

    def test_redirect_off_the_list_is_refused(self):
        resp = MagicMock(is_redirect=True, url="https://huggingface.co/x", headers={"Location": "https://example.com/y"})
        with self.assertRaises(requests.TooManyRedirects):
            _refuse_redirect_off_list(resp)
        resp.headers = {"Location": "https://cdn-lfs.hf.co/y"}
        self.assertIs(_refuse_redirect_off_list(resp), resp)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.slept: list[float] = []

    def test_refuses_url_off_the_list_without_a_request(self):
        http = FallbackHttp(session=self.session)
        with self.assertRaises(SourceUnavailable):
            http.get_json("https://example.com/x")
        self.session.get.assert_not_called()
        self.assertEqual(http.request_count, 0)

    def test_get_json(self):
        self.session.get.return_value = _resp(json_data={"a": 1})
        http = FallbackHttp(session=self.session)
        self.assertEqual(http.get_json("https://huggingface.co/api/x"), {"a": 1})
        self.assertEqual(http.request_count, 1)

    def test_invalid_json_raises_unavailable(self):
        self.session.get.return_value = _resp(json_error=True)
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")

    def test_request_exception_raises_unavailable(self):
        self.session.get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")

    def test_pages_follow_link_next(self):
        self.session.get.side_effect = [
            _resp(json_data=[1, 2], links={"next": {"url": "https://huggingface.co/api/p2"}}),
            _resp(json_data=[3], links={}),
        ]
        http = FallbackHttp(session=self.session)
        self.assertEqual(http.get_json_pages("https://huggingface.co/api/p1"), [1, 2, 3])
        self.assertEqual(self.session.get.call_args_list[1].args[0], "https://huggingface.co/api/p2")
        self.assertEqual(http.request_count, 2)

    def test_pages_stop_at_max_pages(self):
        self.session.get.side_effect = lambda *a, **k: _resp(
            json_data=[1], links={"next": {"url": "https://huggingface.co/api/n"}}
        )
        http = FallbackHttp(session=self.session)
        self.assertEqual(http.get_json_pages("https://huggingface.co/api/p1", max_pages=3), [1, 1, 1])

    def test_pages_refuse_next_link_off_the_list(self):
        self.session.get.return_value = _resp(json_data=[1], links={"next": {"url": "https://example.com/p2"}})
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).get_json_pages("https://huggingface.co/api/p1")

    def test_non_200_raises_unavailable(self):
        for status in (500, 404):
            self.session.get.return_value = _resp(status=status)
            with self.assertRaises(SourceUnavailable):
                FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")

    def test_civarchive_requests_are_paced(self):
        self.session.get.side_effect = lambda *a, **k: _resp(chunks=[b"ok"])
        times = iter([100.0, 100.2])
        http = FallbackHttp(session=self.session, sleep=self.slept.append, clock=lambda: next(times))
        http.get_text("https://civarchive.com/sha256/aa")
        http.get_text("https://civarchive.com/sha256/bb")
        self.assertAlmostEqual(sum(self.slept), 1.3, places=2)

    def test_other_hosts_are_not_paced(self):
        self.session.get.side_effect = lambda *a, **k: _resp(chunks=[b"ok"])
        http = FallbackHttp(session=self.session, sleep=self.slept.append, clock=lambda: 100.0)
        http.get_text("https://c.genur.art/a.webp")
        http.get_text("https://c.genur.art/b.webp")
        http.get_text("https://huggingface.co/a")
        http.get_text("https://huggingface.co/b")
        self.assertEqual(self.slept, [])

    def test_get_text_and_download(self):
        self.session.get.side_effect = lambda *a, **k: _resp(chunks=[b"he", b"llo"])
        http = FallbackHttp(session=self.session)
        self.assertEqual(http.get_text("https://huggingface.co/a"), "hello")
        self.assertEqual(http.download("https://huggingface.co/a"), b"hello")

    def test_oversized_download_raises(self):
        self.session.get.return_value = _resp(chunks=[b"a" * 6, b"b" * 6])
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).download("https://huggingface.co/a", max_bytes=8)

    def test_oversized_text_raises(self):
        self.session.get.return_value = _resp(chunks=[b"a" * 6, b"b" * 6])
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).get_text("https://huggingface.co/a", max_bytes=8)

    def test_stop_is_checked_before_each_request(self):
        http = FallbackHttp(session=self.session, should_stop=lambda: True)
        with self.assertRaises(SourceUnavailable):
            http.get_json("https://huggingface.co/api/x")
        self.session.get.assert_not_called()
        self.assertEqual(http.request_count, 0)


class SavePreviewTests(unittest.TestCase):
    def test_save_preview_png_converts_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            self.assertTrue(save_preview_png(_jpeg_bytes(), target))
            with Image.open(target) as im:
                self.assertEqual(im.format, "PNG")
            before = target.read_bytes()
            self.assertFalse(save_preview_png(_jpeg_bytes(color="red"), target))
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["m.preview.png"])

    def test_save_preview_png_rejects_non_image(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            self.assertFalse(save_preview_png(b"<html>nope</html>", target))
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_save_preview_png_missing_folder_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(save_preview_png(_jpeg_bytes(), Path(d) / "gone" / "m.preview.png"))

    def test_save_preview_png_without_hard_links_uses_exclusive_create(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            with patch("comfy_updater.fallback_http.os.link", side_effect=OSError(errno.EPERM, "no links")):
                self.assertTrue(save_preview_png(_jpeg_bytes(), target))
                with Image.open(target) as im:
                    self.assertEqual(im.format, "PNG")
                self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["m.preview.png"])
                before = target.read_bytes()
                self.assertFalse(save_preview_png(_jpeg_bytes(color="red"), target))
                self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
