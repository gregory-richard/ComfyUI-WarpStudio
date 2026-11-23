from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from comfy_updater.jobs import JobRecord
from comfy_updater.routes import local_preview_response
from comfy_updater.sidecar import preview_path_for

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake image data"


class _Request:
    def __init__(self, payload, remote="127.0.0.1"):
        self._payload = payload
        self.remote = remote

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class PreviewPathForTests(unittest.TestCase):
    def test_weights_file(self) -> None:
        self.assertEqual(Path("d/x.preview.png"), preview_path_for(Path("d/x.safetensors")))

    def test_info_sidecar(self) -> None:
        self.assertEqual(Path("d/x.preview.png"), preview_path_for(Path("d/x.civitai.info")))

    def test_info_sidecar_suffix_in_any_case(self) -> None:
        # The lister matches the suffix case-insensitively; so must this.
        # Names compared as text: Windows paths compare without case.
        for info, preview in (("X.Civitai.Info", "X.preview.png"), ("x.v2.CIVITAI.INFO", "x.v2.preview.png")):
            with self.subTest(info=info):
                path = preview_path_for(Path("d") / info)
                self.assertEqual((Path("d"), preview), (path.parent, path.name))


class LocalPreviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.model = self.folder / "someone.safetensors"
        self.model.write_bytes(b"weights")
        self.preview = self.folder / "someone.preview.png"
        self.preview.write_bytes(PNG_BYTES)
        self.job = JobRecord(
            id="j", type="check-updates", status="completed", items=[{"modelPath": str(self.model)}]
        )

    def respond(self, payload, remote="127.0.0.1"):
        get_job = lambda job_id: self.job if job_id == "j" else None  # noqa: E731
        return asyncio.run(local_preview_response(_Request(payload, remote), get_job))

    def call(self, payload, remote="127.0.0.1"):
        response = self.respond(payload, remote)
        return response.status, response.body, response.content_type

    def error(self, payload):
        response = self.respond(payload)
        return response.status, json.loads(response.text)

    def test_serves_listed_files_preview(self) -> None:
        status, body, ctype = self.call({"jobId": "j", "path": str(self.model)})
        self.assertEqual((200, "image/png"), (status, ctype))
        self.assertEqual(PNG_BYTES, body)

    def test_cache_control_no_cache(self) -> None:
        response = self.respond({"jobId": "j", "path": str(self.model)})
        self.assertEqual("no-cache", response.headers["Cache-Control"])

    def test_metadata_only_entry_uses_info_preview_name(self) -> None:
        info = self.folder / "someone.civitai.info"
        self.job.items = [{"modelPath": str(info)}]
        status, body, _ = self.call({"jobId": "j", "path": str(info)})
        self.assertEqual((200, PNG_BYTES), (status, body))

    def test_an_info_only_row_is_found_by_its_weights_path(self) -> None:
        info = self.folder / "someone.civitai.info"
        self.job.items = [{"modelPath": str(info), "filePath": str(self.folder / "someone.safetensors")}]
        status, body, _ = self.call({"jobId": "j", "path": str(self.folder / "someone.safetensors")})
        self.assertEqual((200, PNG_BYTES), (status, body))

    def test_nothing_is_served_for_an_archived_row(self) -> None:
        # Only the archive's .civitai.info files are read, never its previews.
        info = self.folder / "someone.civitai.info"
        self.job.items = [{"modelPath": str(info), "filePath": str(self.model), "archived": True}]
        for path in (info, self.model):
            with self.subTest(path=path.name):
                status, body = self.error({"jobId": "j", "path": str(path)})
                self.assertEqual((404, {"error": "This file has no local preview."}), (status, body))

    def test_serves_remote_peer_too(self) -> None:
        status, body, _ = self.call({"jobId": "j", "path": str(self.model)}, remote="192.168.1.5")
        self.assertEqual((200, PNG_BYTES), (status, body))

    def test_refuses_unlisted_path(self) -> None:
        other = self.folder / "other.safetensors"
        (self.folder / "other.preview.png").write_bytes(PNG_BYTES)
        status, _ = self.error({"jobId": "j", "path": str(other)})
        self.assertEqual(400, status)

    def test_refuses_a_preview_that_is_not_png(self) -> None:
        self.preview.write_bytes(b"GIF89a" + b"x" * 20)
        response = self.respond({"jobId": "j", "path": str(self.model)})
        self.assertEqual(415, response.status)
        self.assertNotIn(b"GIF89a", response.body)

    def test_refuses_an_oversized_preview(self) -> None:
        self.preview.write_bytes(PNG_BYTES + b"\0" * (20 * 1024 * 1024))
        status, _ = self.error({"jobId": "j", "path": str(self.model)})
        self.assertEqual(413, status)

    def test_serves_a_preview_exactly_at_the_cap(self) -> None:
        data = PNG_BYTES + b"\0" * (20 * 1024 * 1024 - len(PNG_BYTES))
        self.preview.write_bytes(data)
        status, body, _ = self.call({"jobId": "j", "path": str(self.model)})
        self.assertEqual((200, len(data)), (status, len(body)))

    def test_unknown_job(self) -> None:
        status, _ = self.error({"jobId": "nope", "path": str(self.model)})
        self.assertEqual(404, status)

    def test_missing_preview(self) -> None:
        self.preview.unlink()
        status, body = self.error({"jobId": "j", "path": str(self.model)})
        self.assertEqual(404, status)
        self.assertNotIn(str(self.folder), body["error"])

    def test_never_serves_other_files(self) -> None:
        info = self.folder / "someone.civitai.info"
        info.write_text("secret", encoding="utf-8")
        self.job.items = [{"modelPath": str(info)}]
        status, body, ctype = self.call({"jobId": "j", "path": str(info)})
        self.assertEqual((200, "image/png", PNG_BYTES), (status, ctype, body))

    def test_bad_bodies_are_400(self) -> None:
        for payload in (
            ValueError("bad json"),
            [],
            {},
            {"jobId": "j"},
            {"jobId": "j", "path": ""},
            {"jobId": "  ", "path": str(self.model)},
            {"jobId": "j", "path": 5},
        ):
            with self.subTest(payload=repr(payload)):
                status, _ = self.error(payload)
                self.assertEqual(400, status)

    def test_errors_do_not_echo_the_path(self) -> None:
        other = self.folder / "private-name.safetensors"
        for payload in (
            {"jobId": "j", "path": str(other)},
            {"jobId": "nope", "path": str(other)},
        ):
            _, body = self.error(payload)
            self.assertNotIn("private-name", body["error"])
            self.assertNotIn(str(self.folder), body["error"])


if __name__ == "__main__":
    unittest.main()
