from __future__ import annotations

import errno
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests
from PIL import Image

from comfy_updater.fallback_http import (
    FallbackHttp,
    _refuse_redirect_off_list,
    is_allowed_url,
    save_preview_png,
)
from comfy_updater.fallback_types import SourceUnavailable, TooLarge


def _jpeg_bytes(color: str = "blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="JPEG")
    return buf.getvalue()


# The Windows-only branch of save_preview_png relies on Windows' rename, which raises
# FileExistsError when the target exists. POSIX rename silently replaces the target (the code
# never takes that branch there), so off Windows the tests stand in for Windows' rename.
_REAL_WINDOWS_RENAME = os.name == "nt"


def _windows_rename(src, dst):
    """os.rename with Windows semantics on any platform: never replaces an existing target."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "Cannot create a file when that file already exists", str(dst))
    os.replace(src, dst)


def _resp(status=200, json_data=None, links=None, chunks=None, json_error=False):
    resp = MagicMock()
    resp.status_code = status
    resp.links = links or {}
    if json_error:
        chunks = [b"{not json"]
    elif json_data is not None:
        chunks = [json.dumps(json_data).encode()]
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
            ("https://evil.example\\@huggingface.co/x", False),
            ("https://huggingface.co\\@evil.example/x", False),
            ("https://user:pw@huggingface.co/x", False),
            ("https://user@huggingface.co/x", False),
        ):
            self.assertEqual(is_allowed_url(url), ok, url)

    def test_redirect_off_the_list_is_refused(self):
        resp = MagicMock(is_redirect=True, url="https://huggingface.co/x", headers={"Location": "https://example.com/y"})
        with self.assertRaises(requests.TooManyRedirects):
            _refuse_redirect_off_list(resp)
        resp.headers = {"Location": "https://cdn-lfs.hf.co/y"}
        self.assertIs(_refuse_redirect_off_list(resp), resp)


class HardeningTests(unittest.TestCase):
    def test_malformed_redirect_location_is_refused(self):
        resp = MagicMock(is_redirect=True, url="https://huggingface.co/x", headers={"Location": "https://[bad/"})
        with self.assertRaises(requests.TooManyRedirects):
            _refuse_redirect_off_list(resp)

    def test_deeply_nested_json_raises_unavailable(self):
        session = MagicMock()
        session.get.return_value = _resp(chunks=[b"[" * 200_000])
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=session).get_json("https://huggingface.co/api/x")


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

    def test_oversized_json_raises_unavailable(self):
        self.session.get.return_value = _resp(chunks=[b"[" + b"1," * 6_000_000, b"1]"])
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
        pages = http.get_json_pages("https://huggingface.co/api/p1", max_pages=3)
        self.assertEqual(pages, [1, 1, 1])
        self.assertTrue(pages.truncated)  # a next page was left unread

    def test_pages_read_to_the_end_are_not_truncated(self):
        self.session.get.side_effect = [
            _resp(json_data=[1], links={"next": {"url": "https://huggingface.co/api/p2"}}),
            _resp(json_data=[2], links={}),
        ]
        pages = FallbackHttp(session=self.session).get_json_pages("https://huggingface.co/api/p1", max_pages=2)
        self.assertEqual(pages, [1, 2])
        self.assertFalse(pages.truncated)

    def test_page_that_is_not_a_list_raises_unavailable(self):
        for body in ({"error": "Invalid credentials"}, "junk", 5):
            with self.subTest(body=body):
                self.session.get.side_effect = [
                    _resp(json_data=[1], links={"next": {"url": "https://huggingface.co/api/p2"}}),
                    _resp(json_data=body, links={}),
                ]
                with self.assertRaises(SourceUnavailable) as ctx:
                    FallbackHttp(session=self.session).get_json_pages("https://huggingface.co/api/p1")
                self.assertIn("unexpected listing", str(ctx.exception))

    def test_pages_refuse_next_link_off_the_list(self):
        self.session.get.return_value = _resp(json_data=[1], links={"next": {"url": "https://example.com/p2"}})
        with self.assertRaises(SourceUnavailable):
            FallbackHttp(session=self.session).get_json_pages("https://huggingface.co/api/p1")

    def test_non_200_raises_unavailable(self):
        for status in (500, 404):
            self.session.get.return_value = _resp(status=status)
            with self.assertRaises(SourceUnavailable):
                FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")

    def test_non_200_sets_status(self):
        self.session.get.return_value = _resp(status=404)
        with self.assertRaises(SourceUnavailable) as ctx:
            FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")
        self.assertEqual(ctx.exception.status, 404)
        self.session.get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(SourceUnavailable) as ctx:
            FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")
        self.assertIsNone(ctx.exception.status)

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

    def test_oversized_bodies_raise_too_large(self):
        self.assertTrue(issubclass(TooLarge, SourceUnavailable))
        self.session.get.return_value = _resp(chunks=[b"a" * 6, b"b" * 6])
        with self.assertRaises(TooLarge):
            FallbackHttp(session=self.session).download("https://huggingface.co/a", max_bytes=8)
        self.session.get.return_value = _resp(chunks=[b"a" * 6, b"b" * 6])
        with self.assertRaises(TooLarge):
            FallbackHttp(session=self.session).get_text("https://huggingface.co/a", max_bytes=8)
        self.session.get.return_value = _resp(chunks=[b"[" + b"1," * 6_000_000, b"1]"])
        with self.assertRaises(TooLarge):
            FallbackHttp(session=self.session).get_json("https://huggingface.co/api/x")

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

    def test_save_preview_png_scales_a_big_image_down_to_2048(self):
        buf = io.BytesIO()
        Image.new("RGB", (3000, 1000), "blue").save(buf, format="PNG")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            self.assertTrue(save_preview_png(buf.getvalue(), target))
            with Image.open(target) as im:
                width, height = im.size
        self.assertEqual(width, 2048)
        self.assertAlmostEqual(height, 683, delta=1)

    def test_save_preview_png_keeps_a_small_image_size(self):
        buf = io.BytesIO()
        Image.new("RGB", (2048, 900), "blue").save(buf, format="JPEG")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            self.assertTrue(save_preview_png(buf.getvalue(), target))
            with Image.open(target) as im:
                self.assertEqual(im.size, (2048, 900))

    def test_save_preview_png_refuses_too_many_pixels_before_decoding(self):
        buf = io.BytesIO()
        Image.new("1", (5000, 5000)).save(buf, format="PNG")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            with patch("comfy_updater.fallback_http.MAX_PREVIEW_PIXELS", 10_000_000):
                self.assertFalse(save_preview_png(buf.getvalue(), target))
            self.assertEqual(os.listdir(d), [])
            self.assertTrue(save_preview_png(buf.getvalue(), target))

    def test_save_preview_png_refuses_a_format_off_the_list(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(buf, format="BMP")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            self.assertFalse(save_preview_png(buf.getvalue(), target))
            self.assertEqual(os.listdir(d), [])

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
            # The non-Windows branch, on every platform (on Windows it would otherwise rename).
            with patch("comfy_updater.fallback_http.os.link", side_effect=OSError(errno.EPERM, "no links")), \
                    patch("comfy_updater.fallback_http._is_windows", return_value=False):
                self.assertTrue(save_preview_png(_jpeg_bytes(), target))
                with Image.open(target) as im:
                    self.assertEqual(im.format, "PNG")
                self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["m.preview.png"])
                before = target.read_bytes()
                self.assertFalse(save_preview_png(_jpeg_bytes(color="red"), target))
                self.assertEqual(target.read_bytes(), before)

    def test_fallback_write_failing_on_flush_leaves_no_target(self):
        class FailingWriter:
            def __init__(self, path):
                self._fh = open(path, "xb")

            def write(self, data):
                return self._fh.write(data[:10])

            def flush(self):
                raise OSError(errno.ENOSPC, "disk full")

            def close(self):
                self._fh.close()

        real_open = open
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"

            def fake_open(path, mode="r", *a, **k):
                if str(path) == str(target) and mode == "xb":
                    return FailingWriter(path)
                return real_open(path, mode, *a, **k)

            with patch("comfy_updater.fallback_http.os.link", side_effect=OSError(errno.EPERM, "no links")),                     patch("comfy_updater.fallback_http._is_windows", return_value=False),                     patch("builtins.open", fake_open):
                self.assertFalse(save_preview_png(_jpeg_bytes(), target))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_windows_fallback_renames_atomically_and_never_overwrites(self):
        # On Windows the real rename is checked; elsewhere a stand-in with Windows' semantics.
        rename = Mock(wraps=os.rename if _REAL_WINDOWS_RENAME else _windows_rename)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "m.preview.png"
            with patch("comfy_updater.fallback_http.os.link", side_effect=OSError(errno.EPERM, "no links")), \
                    patch("comfy_updater.fallback_http._is_windows", return_value=True), \
                    patch("comfy_updater.fallback_http.os.rename", rename):
                self.assertTrue(save_preview_png(_jpeg_bytes(), target))
                with Image.open(target) as im:
                    self.assertEqual(im.format, "PNG")
                self.assertEqual([p.name for p in Path(d).iterdir()], ["m.preview.png"])
                before = target.read_bytes()
                self.assertFalse(save_preview_png(_jpeg_bytes(color="red"), target))
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual([p.name for p in Path(d).iterdir()], ["m.preview.png"])
            # Both saves went through the rename branch.
            self.assertEqual(rename.call_count, 2)


if __name__ == "__main__":
    unittest.main()
