from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import requests

from comfy_updater import model_download
from comfy_updater.model_download import (
    API_KEY_MESSAGE,
    DownloadError,
    DownloadStopped,
    download_to,
    pick_file,
)

CIVITAI = "https://civitai.red/api/download/models/202"
STORAGE = "https://files.example-storage.net/glowV2.safetensors?sig=1"
BODY = b"new weights"
HASH = hashlib.sha256(BODY).hexdigest()
HASH_MESSAGE = "The downloaded file doesn't match Civitai's SHA-256. It was deleted."
EARLY_MESSAGE = "The download stopped early. Try again."
NO_SHA_MESSAGE = "Civitai lists no SHA-256 for this file, so it can't be checked."


class FakeResponse:
    def __init__(self, status, headers=None, chunks=(), error_after=None, url=""):
        self.status_code = status
        self.headers = dict(headers or {})
        self.chunks = list(chunks)
        self.error_after = error_after
        self.url = url
        self.reason = "Reason"
        self.closed = False

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, chunk_size=1):
        for index, chunk in enumerate(self.chunks):
            if self.error_after is not None and index >= self.error_after:
                raise requests.ConnectionError("connection dropped")
            yield chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {}), kwargs))
        response = self.routes[url]
        response.url = url
        return response


class RedirectLoop:
    """Every Civitai hop answers with another redirect to Civitai."""

    def __getitem__(self, url):
        return FakeResponse(302, {"Location": CIVITAI})


def _ok(chunks=(BODY,), length=len(BODY), **extra):
    headers = {"Content-Length": str(length)} if length is not None else {}
    headers.update(extra)
    return FakeResponse(200, headers, chunks)


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.target = self.folder / "glowV2.safetensors"
        self.progress = []

    def run_download(self, session, url=CIVITAI, sha=HASH, should_stop=None, on_progress=None, **kw):
        download_to(
            self.target,
            url,
            sha,
            session=session,
            api_key=kw.pop("api_key", "secret"),
            timeout=5,
            should_stop=should_stop or (lambda: False),
            on_progress=on_progress or (lambda done, total: self.progress.append((done, total))),
            **kw,
        )

    def assert_folder_empty(self):
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_downloads_through_a_part_file_and_checks_the_hash(self):
        session = FakeSession({CIVITAI: _ok([b"new ", b"weights"])})
        self.run_download(session)
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.target.name])
        self.assertEqual(self.progress[-1], (11, 11))
        for _url, _headers, kwargs in session.calls:
            self.assertIs(kwargs["stream"], True)
            self.assertIs(kwargs["allow_redirects"], False)

    def test_a_wrong_hash_deletes_the_part_file(self):
        session = FakeSession({CIVITAI: _ok()})
        with self.assertRaises(DownloadError) as ctx:
            self.run_download(session, sha="0" * 64)
        self.assertEqual(str(ctx.exception), HASH_MESSAGE)
        self.assert_folder_empty()

    def test_a_short_download_is_discarded(self):
        session = FakeSession({CIVITAI: _ok(length=20)})
        with self.assertRaises(DownloadError) as ctx:
            self.run_download(session)
        self.assertEqual(str(ctx.exception), EARLY_MESSAGE)
        self.assert_folder_empty()

    def test_stop_deletes_the_part_file(self):
        session = FakeSession({CIVITAI: _ok([b"new ", b"weights"])})
        with self.assertRaises(DownloadStopped):
            self.run_download(session, should_stop=lambda: len(self.progress) >= 1)
        self.assert_folder_empty()

    def test_an_existing_file_is_never_overwritten(self):
        self.target.write_bytes(b"mine")
        session = FakeSession({CIVITAI: _ok()})
        with self.assertRaises(FileExistsError):
            self.run_download(session)
        self.assertEqual(session.calls, [])
        self.assertEqual(self.target.read_bytes(), b"mine")

    def test_a_file_that_appears_meanwhile_is_not_overwritten(self):
        session = FakeSession({CIVITAI: _ok()})

        def appear(done, total):
            self.target.write_bytes(b"mine")

        with self.assertRaises(FileExistsError):
            self.run_download(session, on_progress=appear)
        self.assertEqual(self.target.read_bytes(), b"mine")
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.target.name])

    def test_a_leftover_part_file_is_replaced(self):
        (self.folder / "glowV2.safetensors.part").write_bytes(b"old junk")
        session = FakeSession({CIVITAI: _ok()})
        self.run_download(session)
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.target.name])

    def test_the_key_goes_to_civitai_but_not_to_the_storage_host(self):
        session = FakeSession({CIVITAI: FakeResponse(307, {"Location": STORAGE}), STORAGE: _ok()})
        self.run_download(session)
        (_u1, first, _k1), (_u2, second, _k2) = session.calls
        self.assertEqual(first["Authorization"], "Bearer secret")
        self.assertNotIn("Authorization", second)
        self.assertIn("User-Agent", first)
        self.assertIn("User-Agent", second)

    def test_a_relative_redirect_stays_on_civitai_with_the_key(self):
        again = "https://civitai.red/api/download/models/202?file=1"
        session = FakeSession(
            {CIVITAI: FakeResponse(302, {"Location": "/api/download/models/202?file=1"}), again: _ok()}
        )
        self.run_download(session)
        self.assertEqual(session.calls[1][0], again)
        self.assertEqual(session.calls[1][1]["Authorization"], "Bearer secret")

    def test_redirects_to_plain_http_or_local_addresses_are_refused(self):
        for location in (
            "http://files.example-storage.net/x",
            "https://127.0.0.1/x",
            "https://localhost/x",
            "https://[::1]/x",
            "https://10.0.0.5/x",
            "https://192.168.1.2/x",
        ):
            with self.subTest(location=location):
                session = FakeSession({CIVITAI: FakeResponse(302, {"Location": location})})
                with self.assertRaises(DownloadError):
                    self.run_download(session)
                self.assertEqual(len(session.calls), 1)
                self.assert_folder_empty()

    def test_a_backslash_redirect_never_carries_the_key(self):
        evil = "https://evil.example\\@civitai.red/x"
        session = FakeSession(
            {CIVITAI: FakeResponse(302, {"Location": evil}), requests.Request("GET", evil).prepare().url: _ok()}
        )
        try:
            self.run_download(session)
        except DownloadError:
            pass
        for url, headers, _kwargs in session.calls[1:]:
            self.assertNotEqual(urlsplit(url).hostname, "civitai.red")
            self.assertNotIn("Authorization", headers)

    def test_userinfo_in_a_link_is_refused(self):
        session = FakeSession({})
        with self.assertRaises(DownloadError):
            self.run_download(session, url="https://evil.example\\@civitai.red/api/download/models/202")
        self.assertEqual(session.calls, [])

    def test_only_a_civitai_link_starts_a_download(self):
        session = FakeSession({})
        with self.assertRaises(DownloadError):
            self.run_download(session, url="https://example.com/x.safetensors")
        self.assertEqual(session.calls, [])

    def test_a_refusal_says_where_to_set_the_api_key(self):
        for response in (
            FakeResponse(401),
            FakeResponse(403),
            FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, [b"<html>"]),
        ):
            with self.subTest(status=response.status_code):
                session = FakeSession({CIVITAI: response})
                with self.assertRaises(DownloadError) as ctx:
                    self.run_download(session)
                self.assertEqual(str(ctx.exception), API_KEY_MESSAGE)
                self.assertTrue(response.closed)

    def test_no_hash_means_no_download(self):
        session = FakeSession({CIVITAI: _ok()})
        with self.assertRaises(DownloadError) as ctx:
            self.run_download(session, sha="")
        self.assertEqual(str(ctx.exception), NO_SHA_MESSAGE)
        self.assertEqual(session.calls, [])

    def test_too_many_redirects_stop(self):
        session = FakeSession(RedirectLoop())
        with self.assertRaises(DownloadError) as ctx:
            self.run_download(session)
        self.assertEqual(str(ctx.exception), "Too many redirects.")

    def test_a_dropped_connection_is_a_download_error(self):
        response = FakeResponse(200, {"Content-Length": "11"}, [b"new ", b"weights"], error_after=1)
        session = FakeSession({CIVITAI: response})
        with self.assertRaises(DownloadError) as ctx:
            self.run_download(session)
        self.assertTrue(str(ctx.exception).startswith("The download failed:"))
        self.assert_folder_empty()

    def test_pick_file_by_hash_else_primary(self):
        fp16 = {"name": "a.safetensors", "primary": True, "type": "Model", "hashes": {"SHA256": "AA"}}
        fp32 = {"name": "b.safetensors", "type": "Model", "hashes": {"SHA256": "BB"}}
        version = {"files": [fp16, fp32]}
        self.assertIs(pick_file(version, "bb"), fp32)
        self.assertIs(pick_file(version), fp16)
        self.assertIsNone(pick_file(version, "cc"))

    def test_every_hop_asks_for_the_raw_bytes(self):
        session = FakeSession(
            {
                CIVITAI: FakeResponse(302, {"Location": STORAGE}),
                STORAGE: _ok(),
            }
        )
        self.run_download(session)
        self.assertEqual(len(session.calls), 2)
        for _url, headers, _kwargs in session.calls:
            self.assertEqual(headers["Accept-Encoding"], "identity")

    def test_a_locked_finished_file_is_retried(self):
        real_rename = os.rename
        attempts = []

        def flaky(src, dst):
            attempts.append(src)
            if len(attempts) < 3:
                raise PermissionError(32, "in use")
            return real_rename(src, dst)

        session = FakeSession({CIVITAI: _ok()})
        with mock.patch.object(model_download, "RENAME_DELAY", 0), mock.patch.object(
            model_download.os, "rename", side_effect=flaky
        ):
            self.run_download(session)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.target.read_bytes(), BODY)
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.target.name])

    def test_a_rename_that_keeps_failing_is_an_error(self):
        session = FakeSession({CIVITAI: _ok()})
        with mock.patch.object(model_download, "RENAME_DELAY", 0), mock.patch.object(
            model_download.os, "rename", side_effect=PermissionError(32, "in use")
        ) as rename:
            with self.assertRaises(DownloadError) as ctx:
                self.run_download(session)
        self.assertEqual(rename.call_count, model_download.RENAME_TRIES)
        self.assertTrue(
            str(ctx.exception).startswith("Could not move the finished download into place: ")
        )
        self.assert_folder_empty()

    def test_the_error_text_carries_no_signed_query(self):
        class Dropping(FakeSession):
            def get(self, url, headers=None, **kwargs):
                raise requests.ConnectionError(
                    "Max retries for https://files.example-storage.net/x?sig=SECRET (reset)"
                )

        with self.assertRaises(DownloadError) as ctx:
            self.run_download(Dropping({}))
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("sig=", str(ctx.exception))
        self.assertIn("https://files.example-storage.net/x", str(ctx.exception))

    def test_the_error_text_carries_no_signed_query_after_a_bare_path(self):
        class Dropping(FakeSession):
            def get(self, url, headers=None, **kwargs):
                raise requests.ConnectionError(
                    "HTTPSConnectionPool(host='x', port=443): Max retries exceeded with url: "
                    "/file/a.safetensors?X-Amz-Credential=KEY&X-Amz-Signature=SECRET "
                    "(Caused by NameResolutionError('lookup failed'))"
                )

        with self.assertRaises(DownloadError) as ctx:
            self.run_download(Dropping({}))
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("X-Amz", str(ctx.exception))
        self.assertIn("url: /file/a.safetensors (Caused by", str(ctx.exception))
        self.assert_folder_empty()


if __name__ == "__main__":
    unittest.main()
