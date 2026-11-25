from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

from comfy_updater import model_actions
from comfy_updater.civitai_client import CivitaiClient, CivitaiRequestError
from comfy_updater.constants import CACHE_SCHEMA_VERSION, MODEL_VERSION_BY_ID_URL
from comfy_updater.download_queue import DownloadRequest
from comfy_updater.download_target import AiName, NamedRelease
from comfy_updater.jobs import JobManager
from comfy_updater.model_actions import (
    EARLY_ACCESS_ERROR,
    DownloadContext,
    delete_model,
    download_one,
    info_payload,
    release_is_paid,
)
from comfy_updater.model_download import DownloadError, DownloadStopped
from comfy_updater.naming_scheme import NameParts
from comfy_updater.release_names import ReleaseNameStore
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore

CIVITAI = "https://civitai.red/api/download/models/202"
STORAGE = "https://files.example-storage.net/glowV2.safetensors?sig=1"
V1_CIVITAI = "https://civitai.red/api/download/models/201"
V1_STORAGE = "https://files.example-storage.net/glowV1.safetensors?sig=1"
BODY = b"new weights"
HASH = hashlib.sha256(BODY).hexdigest()
V1_BODY = b"old weights"
V1_HASH = hashlib.sha256(V1_BODY).hexdigest()
PNG = b"\x89PNG\r\n\x1a\nfake"
ANSWER = "title: glow\noriginal:\nversion 1: v2"
V1_STEM = "maker - glow - v1 (pony)"
V1_SUFFIXES = (".safetensors", ".civitai.info", ".preview.png", ".json")
HASH_MESSAGE = "The downloaded file doesn't match Civitai's SHA-256. It was deleted."


def held(items: list) -> list:
    """The rows of files still there: a removed file's row stays, marked deleted, until the next check."""
    return [item for item in items if not item.get("deleted")]



# --- the fake session of Task 1 ------------------------------------------------


class FakeResponse:
    def __init__(self, status, headers=None, chunks=(), error_after=None, url="", payload=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self.chunks = list(chunks)
        self.error_after = error_after
        self.url = url
        self.reason = "Reason"
        self.closed = False
        self.payload = payload

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

    def json(self):
        return self.payload

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


def _ok(body: bytes) -> FakeResponse:
    return FakeResponse(200, {"Content-Length": str(len(body))}, [body])


def _redirect(to: str) -> FakeResponse:
    return FakeResponse(307, {"Location": to})


# --- Civitai's data --------------------------------------------------------------


def v2_file(sha: str = HASH) -> dict:
    return {
        "id": 2020,
        "name": "glowV2.safetensors",
        "type": "Model",
        "primary": True,
        "sizeKB": len(BODY) / 1024,
        "hashes": {"SHA256": sha.upper()},
        "downloadUrl": CIVITAI,
    }


def v1_file() -> dict:
    return {
        "id": 2010,
        "name": "glowV1.safetensors",
        "type": "Model",
        "primary": True,
        "sizeKB": len(V1_BODY) / 1024,
        "hashes": {"SHA256": V1_HASH.upper()},
        "downloadUrl": V1_CIVITAI,
    }


def version_202(**extra) -> dict:
    return {
        "id": 202,
        "modelId": 101,
        "name": "v2",
        "baseModel": "Illustrious",
        "publishedAt": "2026-02-01T00:00:00.000Z",
        "model": {"name": "Glow", "type": "LORA", "nsfw": False},
        "images": [{"type": "image", "url": "https://image.civitai.red/glow-v2.jpeg"}],
        "files": [v2_file()],
        **extra,
    }


def version_201(files=None) -> dict:
    return {
        "id": 201,
        "modelId": 101,
        "name": "v1",
        "baseModel": "Pony",
        "publishedAt": "2026-01-01T00:00:00.000Z",
        "model": {"name": "Glow", "type": "LORA", "nsfw": False},
        "images": [{"type": "image", "url": "https://image.civitai.red/glow-v1.jpeg"}],
        "files": files if files is not None else [v1_file()],
    }


def glow_model() -> dict:
    return {
        "id": 101,
        "type": "LORA",
        "name": "Glow",
        "creator": {"username": "maker"},
        "modelVersions": [version_202(), version_201()],
    }


def remote_versions() -> list[dict]:
    return [
        {"versionId": "202", "versionName": "v2", "versionDate": "2026-02-01T00:00:00.000Z"},
        {"versionId": "201", "versionName": "v1", "versionDate": "2026-01-01T00:00:00.000Z"},
    ]


class FakeClient:
    def __init__(self, versions: dict, models: dict, session: FakeSession) -> None:
        self.versions = {str(k): v for k, v in versions.items()}
        self.models = {str(k): v for k, v in models.items()}
        self.session = session
        self.images: list = []
        self.version_calls: list = []

    def get_version(self, version_id, *, raise_on_error=False):
        self.version_calls.append((str(version_id), raise_on_error))
        value = self.versions.get(str(version_id))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def get_model(self, model_id, *, raise_on_error=False):
        value = self.models.get(str(model_id))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def download_image_as_png(self, url, path):
        self.images.append((url, Path(path)))
        Path(path).write_bytes(PNG)
        return True


class FakeGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list = []

    def generate(self, messages, max_lengths=None):
        self.calls.append(list(messages))
        return [self.answer for _message in messages]


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class ModelActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "loras"
        self.pony = self.root / "pony"
        self.ill = self.root / "ill"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

        self.v1 = touch(self.pony / f"{V1_STEM}.safetensors", V1_BODY)
        touch(self.pony / f"{V1_STEM}.civitai.info", json.dumps(version_201()).encode())
        touch(self.pony / f"{V1_STEM}.preview.png", PNG)
        touch(self.pony / f"{V1_STEM}.json", b"{}")
        for i in range(2):
            touch(self.pony / f"maker - a{i} - v1 (pony).safetensors")
        for i in range(3):
            touch(self.ill / f"maker - b{i} - v1 (ill).safetensors")
        self.v1_bytes = {s: (self.pony / (V1_STEM + s)).read_bytes() for s in V1_SUFFIXES}

        self.data = self.tmp / "data"
        self.data.mkdir()
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.base_codes.set("Pony", "pony")
        self.base_codes.set("Illustrious", "ill")
        self.cache = AnswerCache(self.data)

        self.session = FakeSession(
            {
                CIVITAI: _redirect(STORAGE),
                STORAGE: _ok(BODY),
                V1_CIVITAI: _redirect(V1_STORAGE),
                V1_STORAGE: _ok(V1_BODY),
            }
        )
        self.client = FakeClient(
            {202: version_202(), 201: version_201()}, {101: glow_model()}, self.session
        )

        self.v1_item = {
            "modelPath": str(self.v1),
            "filePath": str(self.v1),
            "modelType": "lora",
            "metadataOnly": False,
            "modelId": "101",
            "modelName": "Glow",
            "baseModel": "Pony",
            "status": "ok",
            "hasUpdate": True,
            "localVersionId": "201",
            "localVersionName": "v1",
            "localVersionDate": "2026-01-01T00:00:00.000Z",
            "creatorName": "maker",
            "modelUrl": "https://civitai.red/models/101",
            "nsfw": False,
            "remoteVersions": remote_versions(),
        }
        self.manager = JobManager()
        self.job = self.manager.restore_job(
            "check-updates",
            "j1",
            startedAt="2026-03-01T00:00:00+00:00",
            finishedAt="2026-03-01T00:00:00+00:00",
            summary={"total": 1, "withUpdates": 1},
            items=[copy.deepcopy(self.v1_item)],
        )
        self.saved = self.data / "last_check.json"
        self.saved.write_text(
            json.dumps(
                {
                    "schemaVersion": CACHE_SCHEMA_VERSION,
                    "checkedAt": "2026-03-01T00:00:00+00:00",
                    "summary": {"total": 1, "withUpdates": 1},
                    "items": [self.v1_item],
                }
            ),
            encoding="utf-8",
        )

        self.trashed: list = []
        self.on_trash = None
        self.reports: list = []
        self.generator = None

    # --- helpers ---------------------------------------------------------------

    def trash(self, path: str) -> None:
        if self.on_trash is not None:
            self.on_trash(Path(path))
        self.trashed.append(Path(path).name)
        os.replace(path, self.bin / Path(path).name)

    def ctx(self, **overrides) -> DownloadContext:
        fields = dict(
            config={"apiKey": "secret", "requestTimeoutSeconds": 30},
            data_dir=self.data,
            job_manager=self.manager,
            decisions=self.decisions,
            base_codes=self.base_codes,
            answer_cache=self.cache,
            make_client=lambda should_stop: self.client,
            make_generator=(lambda should_stop: self.generator) if self.generator else None,
            clip_name="qwen3.safetensors",
            roots_for=lambda model_type: [self.root],
            lora_roots=lambda: [self.root],
            trash=self.trash,
        )
        fields.update(overrides)
        return DownloadContext(**fields)

    def updates_request(self, *, replace: bool = False) -> DownloadRequest:
        return DownloadRequest(
            key="version:202",
            source="updates",
            model_id="101",
            version_id="202",
            model_type="lora",
            reference_path=str(self.v1),
            replace_path=str(self.v1) if replace else "",
            check_item=copy.deepcopy(self.v1_item),
        )

    def gallery_request(self) -> DownloadRequest:
        name = os.path.join("pony", f"{V1_STEM}.safetensors")
        return DownloadRequest(
            key=f"gallery:{name}",
            source="gallery",
            model_id="",
            version_id="",
            model_type="lora",
            name=name,
        )

    def run_one(self, request, *, should_stop=lambda: False, **overrides) -> DownloadRequest:
        def report(written, total, message):
            self.reports.append((written, total, message))

        download_one(request, report=report, should_stop=should_stop, ctx=self.ctx(**overrides))
        return request

    def saved_items(self) -> list[dict]:
        return json.loads(self.saved.read_text(encoding="utf-8"))["items"]

    def part_files(self) -> list[Path]:
        return list(self.tmp.rglob("*.part"))

    def assert_v1_intact(self) -> None:
        for suffix, data in self.v1_bytes.items():
            self.assertEqual((self.pony / (V1_STEM + suffix)).read_bytes(), data, suffix)

    # --- Updates ---------------------------------------------------------------

    def test_an_updates_download_lands_in_the_bases_folder_with_its_sidecars(self) -> None:
        request = self.run_one(self.updates_request())

        weights = self.ill / "glowV2.safetensors"
        self.assertEqual(weights.read_bytes(), BODY)
        info = json.loads((self.ill / "glowV2.civitai.info").read_text(encoding="utf-8"))
        self.assertEqual(info["files"][0]["hashes"]["SHA256"].lower(), HASH)
        self.assertEqual(info["extensions"]["source"], "comfy-civitai-updater")
        self.assertTrue(info["extensions"]["updatedAt"])
        self.assertEqual((self.ill / "glowV2.preview.png").read_bytes(), PNG)
        self.assertEqual(self.part_files(), [])
        self.assertEqual(request.file_name, "glowV2.safetensors")
        self.assertEqual(request.warnings, [])
        self.assert_v1_intact()
        self.assertEqual(self.trashed, [])

        self.assertEqual(self.client.version_calls, [("202", True)])
        self.assertEqual(self.session.calls[0][1].get("Authorization"), "Bearer secret")
        self.assertNotIn("Authorization", self.session.calls[1][1])
        self.assertEqual(self.session.calls[0][2]["timeout"], (30, 30))
        messages = [message for _w, _t, message in self.reports]
        self.assertEqual(messages[0], "Asking Civitai for the release")
        self.assertIn("Downloading glowV2.safetensors: 0 of 0 MB", messages)
        self.assertEqual(self.reports[-1][:2], (len(BODY), len(BODY)))

        # No Replace: the old version's record stays and the new one joins it.
        self.assertEqual(
            [item["modelPath"] for item in self.job.items], [str(self.v1), str(weights)]
        )
        self.assertEqual(
            [item["modelPath"] for item in self.saved_items()], [str(self.v1), str(weights)]
        )

    def test_an_updates_download_takes_the_ai_name(self) -> None:
        self.generator = FakeGenerator(ANSWER)

        request = self.run_one(self.updates_request())

        weights = self.ill / "maker - glow - v2 (ill).safetensors"
        self.assertEqual(weights.read_bytes(), BODY)
        self.assertTrue((self.ill / "maker - glow - v2 (ill).civitai.info").is_file())
        self.assertTrue((self.ill / "maker - glow - v2 (ill).preview.png").is_file())
        self.assertEqual(request.file_name, weights.name)
        self.assertEqual(len(self.generator.calls), 1)
        self.assertIn(
            "Naming glowV2.safetensors with the AI", [message for _w, _t, message in self.reports]
        )

    def test_a_new_base_takes_the_ais_code_its_own_folder_and_says_so(self) -> None:
        other = self.tmp / "other-data"
        other.mkdir()
        base_codes = BaseCodeStore(other)
        base_codes.set("Pony", "pony")
        self.generator = FakeGenerator(ANSWER + "\nbase Illustrious: illx")

        request = self.run_one(self.updates_request(), base_codes=base_codes)

        weights = self.root / "illx" / "maker - glow - v2 (illx).safetensors"
        self.assertEqual(weights.read_bytes(), BODY)
        self.assertTrue((self.root / "illx" / "maker - glow - v2 (illx).civitai.info").is_file())
        self.assertEqual(request.file_name, weights.name)
        self.assertEqual(request.warnings, ["New base model Illustrious: its files get the code illx."])
        self.assertEqual(base_codes.code("Illustrious"), "illx")
        self.assertEqual([item["modelPath"] for item in self.job.items], [str(self.v1), str(weights)])

    def test_a_failed_download_leaves_no_new_base_folder_and_no_code(self) -> None:
        other = self.tmp / "other-data"
        other.mkdir()
        base_codes = BaseCodeStore(other)
        base_codes.set("Pony", "pony")
        self.generator = FakeGenerator(ANSWER + "\nbase Illustrious: illx")
        self.session.routes[STORAGE] = _ok(b"wrong weights")

        with self.assertRaises(DownloadError):
            self.run_one(self.updates_request(), base_codes=base_codes)

        self.assertFalse((self.root / "illx").exists())
        self.assertIsNone(base_codes.code("Illustrious"))

    def test_a_stored_name_names_the_download_without_the_ai(self) -> None:
        self.generator = FakeGenerator(ANSWER)
        names = ReleaseNameStore(self.data, self.base_codes)
        names.put("101", {"202": NamedRelease(NameParts("moss", "glow lite", "v2", "", "ill"), "Illustrious", "")})

        request = self.run_one(self.updates_request(), release_names=names)

        weights = self.ill / "moss - glow lite - v2 (ill).safetensors"
        self.assertTrue(weights.is_file())
        self.assertEqual(request.file_name, weights.name)
        self.assertEqual([], self.generator.calls)

    def test_a_stored_name_on_a_new_base_learns_its_code_on_download(self) -> None:
        other = self.tmp / "other-data"
        other.mkdir()
        base_codes = BaseCodeStore(other)
        base_codes.set("Pony", "pony")
        names = ReleaseNameStore(other, base_codes)
        names.put("101", {"202": NamedRelease(NameParts("moss", "glow", "v2", "", "illx"), "Illustrious", "Illustrious")})

        request = self.run_one(self.updates_request(), base_codes=base_codes, release_names=names)

        self.assertTrue((self.root / "illx" / "moss - glow - v2 (illx).safetensors").is_file())
        self.assertEqual("illx", base_codes.code("Illustrious"))
        self.assertEqual(request.warnings, ["New base model Illustrious: its files get the code illx."])

    def test_a_taken_stored_name_falls_back_to_civitais_name(self) -> None:
        names = ReleaseNameStore(self.data, self.base_codes)
        names.put("101", {"202": NamedRelease(NameParts("moss", "glow", "v2", "", "ill"), "Illustrious", "")})
        (self.ill / "moss - glow - v2 (ill).civitai.info").write_bytes(b"{}")

        request = self.run_one(self.updates_request(), release_names=names)

        self.assertEqual(request.file_name, "glowV2.safetensors")
        self.assertTrue((self.ill / "glowV2.safetensors").is_file())

    def test_a_release_without_a_stored_name_is_named_by_the_ai(self) -> None:
        self.generator = FakeGenerator(ANSWER)
        names = ReleaseNameStore(self.data, self.base_codes)

        self.run_one(self.updates_request(), release_names=names)

        self.assertEqual(1, len(self.generator.calls))
        self.assertTrue((self.ill / "maker - glow - v2 (ill).safetensors").is_file())

    def test_a_naming_failure_keeps_civitais_name(self) -> None:
        self.generator = FakeGenerator(ANSWER)
        self.client.models["101"] = CivitaiRequestError("503 Service Unavailable")

        with mock.patch("builtins.print") as printed:
            request = self.run_one(self.updates_request())

        self.assertEqual(request.file_name, "glowV2.safetensors")
        self.assertTrue((self.ill / "glowV2.safetensors").is_file())
        self.assertIn("503 Service Unavailable", str(printed.call_args_list))

    def test_replace_sends_the_old_files_to_the_recycle_bin_after_the_new_file_is_in_place(
        self,
    ) -> None:
        weights = self.ill / "glowV2.safetensors"

        def check(path: Path) -> None:
            self.assertEqual(weights.read_bytes(), BODY)
            self.assertTrue((self.ill / "glowV2.civitai.info").is_file())
            self.assertEqual(self.part_files(), [])

        self.on_trash = check

        request = self.run_one(self.updates_request(replace=True))

        self.assertEqual(sorted(self.trashed), sorted(V1_STEM + s for s in V1_SUFFIXES))
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), sorted(self.trashed))
        self.assertEqual(request.warnings, [])
        self.assertIn(
            "Sending the old version to the Recycle Bin",
            [message for _w, _t, message in self.reports],
        )
        for items in (held(self.job.items), held(self.saved_items())):
            self.assertEqual([item["modelPath"] for item in items], [str(weights)])
            self.assertEqual(items[0]["localVersionId"], "202")
            self.assertEqual(items[0]["remoteVersions"], remote_versions())
        self.assertEqual([str(self.v1)], [i["modelPath"] for i in self.job.items if i.get("deleted")])
        summary = json.loads(self.saved.read_text(encoding="utf-8"))["summary"]
        self.assertEqual(summary["withUpdates"], 0)

    def test_a_failed_download_leaves_the_old_version_alone(self) -> None:
        self.session.routes[STORAGE] = _ok(b"wrong weights")
        saved_before = self.saved.read_bytes()

        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.updates_request(replace=True))

        self.assertEqual(str(caught.exception), HASH_MESSAGE)
        self.assertEqual(self.trashed, [])
        self.assert_v1_intact()
        self.assertEqual(
            sorted(p.name for p in self.ill.iterdir()),
            [f"maker - b{i} - v1 (ill).safetensors" for i in range(3)],
        )
        self.assertEqual(self.part_files(), [])
        self.assertEqual(self.job.items, [self.v1_item])
        self.assertEqual(self.saved.read_bytes(), saved_before)

    def test_stop_leaves_the_old_version_and_no_part_file(self) -> None:
        self.session.routes[STORAGE] = FakeResponse(
            200, {"Content-Length": str(len(BODY))}, [b"new ", b"weights"]
        )

        def should_stop() -> bool:
            # Stop once the first chunk is written.
            return any(message.startswith("Downloading") for _w, _t, message in self.reports)

        with self.assertRaises(DownloadStopped):
            self.run_one(self.updates_request(replace=True), should_stop=should_stop)

        self.assertEqual(self.trashed, [])
        self.assert_v1_intact()
        self.assertFalse((self.ill / "glowV2.safetensors").exists())
        self.assertEqual(self.part_files(), [])
        self.assertEqual(self.job.items, [self.v1_item])

    def test_a_stop_while_asking_civitai_is_a_stop(self) -> None:
        self.client.versions["202"] = CivitaiRequestError("https://civitai.red :: timed out")
        with self.assertRaises(DownloadStopped):
            self.run_one(self.updates_request(), should_stop=lambda: True)
        self.assertEqual(self.session.calls, [])

    def test_a_release_now_in_early_access_is_refused(self) -> None:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.client.versions["202"] = version_202(
            paidAccess={"permanent": False, "endsAt": tomorrow}
        )

        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.updates_request(replace=True))

        self.assertEqual(str(caught.exception), EARLY_ACCESS_ERROR)
        self.assertEqual(self.session.calls, [])
        self.assert_v1_intact()

    def test_a_release_civitai_no_longer_has_is_refused(self) -> None:
        for value in (CivitaiRequestError("404 Not Found", status=404), None):
            with self.subTest(value=value):
                self.client.versions["202"] = value
                with self.assertRaises(DownloadError) as caught:
                    self.run_one(self.updates_request())
                self.assertEqual(str(caught.exception), "Civitai no longer has this release.")
        self.client.versions["202"] = CivitaiRequestError(
            "401 Unauthorized. Civitai rejected the API key.", status=401
        )
        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.updates_request())
        self.assertEqual(str(caught.exception), "401 Unauthorized. Civitai rejected the API key.")
        self.assertEqual(self.session.calls, [])

    def test_a_release_without_a_file_or_a_hash_is_refused_before_naming(self) -> None:
        self.generator = FakeGenerator(ANSWER)
        self.client.versions["202"] = version_202(files=[])
        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.updates_request())
        self.assertEqual(str(caught.exception), "Civitai lists no model file for this release.")

        self.client.versions["202"] = version_202(files=[dict(v2_file(), hashes={})])
        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.updates_request())
        self.assertEqual(
            str(caught.exception), "Civitai lists no SHA-256 for this file, so it can't be checked."
        )
        self.assertEqual(self.generator.calls, [])
        self.assertEqual(self.session.calls, [])

    def test_a_replace_never_trashes_the_new_files(self) -> None:
        new_stem = f"{V1_STEM}.v2"
        with mock.patch.object(model_actions, "ai_stem", return_value=AiName(new_stem, "pony", "")):
            self.generator = FakeGenerator(ANSWER)
            request = self.run_one(self.updates_request(replace=True))

        self.assertEqual(request.file_name, f"{new_stem}.safetensors")
        self.assertEqual(sorted(self.trashed), sorted(V1_STEM + s for s in V1_SUFFIXES))
        self.assertEqual((self.pony / f"{new_stem}.safetensors").read_bytes(), BODY)
        self.assertTrue((self.pony / f"{new_stem}.civitai.info").is_file())
        self.assertTrue((self.pony / f"{new_stem}.preview.png").is_file())

    def test_the_new_files_are_kept_even_when_listed_with_the_old_ones(self) -> None:
        weights = self.ill / "glowV2.safetensors"
        listed = [
            self.v1,
            weights,
            self.ill / "glowV2.civitai.info",
            self.ill / "glowV2.preview.png",
        ]
        with mock.patch.object(model_actions, "stem_files", return_value=listed):
            self.run_one(self.updates_request(replace=True))
        self.assertEqual(self.trashed, [self.v1.name])
        self.assertEqual(weights.read_bytes(), BODY)

    def test_a_trash_failure_is_a_warning_not_a_failure(self) -> None:
        def refuse_weights(path: Path) -> None:
            if path.suffix == ".safetensors":
                raise OSError("in use")

        self.on_trash = refuse_weights

        request = self.run_one(self.updates_request(replace=True))

        self.assertEqual(
            request.warnings,
            [f"Could not send {V1_STEM}.safetensors to the Recycle Bin: in use"],
        )
        # The weights stayed, so their sidecars stay with them.
        self.assertEqual(self.trashed, [])
        self.assert_v1_intact()
        self.assertEqual((self.ill / "glowV2.safetensors").read_bytes(), BODY)
        # The old weights are still there, so their record stays beside the new one.
        both = [str(self.v1), str(self.ill / "glowV2.safetensors")]
        self.assertEqual([item["modelPath"] for item in self.saved_items()], both)
        self.assertEqual([item["modelPath"] for item in self.job.items], both)

    def test_weights_the_recycle_bin_left_in_place_keep_their_sidecars(self) -> None:
        calls = []

        request = self.run_one(self.updates_request(replace=True), trash=calls.append)

        self.assertEqual(calls, [str(self.v1)])
        self.assertEqual(
            request.warnings,
            [f"Could not send {V1_STEM}.safetensors to the Recycle Bin: it is still there"],
        )
        self.assert_v1_intact()
        both = [str(self.v1), str(self.ill / "glowV2.safetensors")]
        self.assertEqual([item["modelPath"] for item in self.saved_items()], both)

    def test_a_failed_info_write_keeps_the_old_version(self) -> None:
        with mock.patch.object(
            model_actions, "write_json", side_effect=OSError(28, "No space left")
        ):
            request = self.run_one(self.updates_request(replace=True))

        self.assertEqual(self.trashed, [])
        self.assert_v1_intact()
        self.assertEqual((self.ill / "glowV2.safetensors").read_bytes(), BODY)
        self.assertEqual(
            request.warnings,
            [
                "Could not write glowV2.civitai.info: No space left",
                "The old version was kept because the new version's info file could not be"
                " written.",
            ],
        )
        self.assertNotIn(
            "Sending the old version to the Recycle Bin",
            [message for _w, _t, message in self.reports],
        )
        # The new file has no info yet, so it gets no record until the next check.
        self.assertEqual([item["modelPath"] for item in self.saved_items()], [str(self.v1)])
        self.assertEqual(self.job.items, [self.v1_item])

    def test_old_weights_already_gone_is_a_warning(self) -> None:
        self.v1.unlink()
        request = self.run_one(self.updates_request(replace=True))
        self.assertEqual(request.warnings, ["The old version's file was already gone."])
        self.assertEqual(self.trashed, [])
        self.assertEqual(
            [item["modelPath"] for item in held(self.saved_items())],
            [str(self.ill / "glowV2.safetensors")],
        )

    def test_a_taken_name_is_refused(self) -> None:
        with mock.patch.object(model_actions, "download_to", side_effect=FileExistsError("x")):
            with self.assertRaises(DownloadError) as caught:
                self.run_one(self.updates_request(replace=True))
        self.assertEqual(str(caught.exception), "A file named glowV2.safetensors is already there.")
        self.assertEqual(self.trashed, [])

    def test_an_unreadable_folder_is_a_friendly_error(self) -> None:
        error = PermissionError(13, "Access is denied", str(self.ill))
        with mock.patch("comfy_updater.download_target.stem_taken", side_effect=error):
            with self.assertRaises(DownloadError) as caught:
                self.run_one(self.updates_request())
        self.assertNotIn(str(self.tmp), str(caught.exception))
        self.assertIn("Access is denied", str(caught.exception))

    def test_an_info_write_failure_is_a_warning(self) -> None:
        with mock.patch.object(
            model_actions, "write_json", side_effect=OSError(28, "No space left")
        ):
            request = self.run_one(self.updates_request())
        self.assertEqual(request.warnings, ["Could not write glowV2.civitai.info: No space left"])
        self.assertEqual((self.ill / "glowV2.safetensors").read_bytes(), BODY)
        # Without its info the new file gets no record until the next check.
        self.assertEqual([item["modelPath"] for item in self.saved_items()], [str(self.v1)])

    def test_a_records_failure_after_the_replace_is_a_warning(self) -> None:
        error = OSError(13, "Access is denied", str(self.saved))
        with mock.patch.object(model_actions, "update_check_records", side_effect=error):
            with mock.patch("builtins.print"):
                request = self.run_one(self.updates_request(replace=True))
        self.assertEqual(request.warnings, ["Could not update the check results: Access is denied"])
        self.assertEqual((self.ill / "glowV2.safetensors").read_bytes(), BODY)
        self.assertEqual(len(self.trashed), 4)

    def test_a_replace_outside_the_roots_is_refused(self) -> None:
        outside = touch(self.tmp / "elsewhere" / "x.safetensors")
        request = self.updates_request(replace=True)
        request.replace_path = str(outside)
        with self.assertRaises(DownloadError) as caught:
            self.run_one(request)
        self.assertEqual(str(caught.exception), "That folder isn't one of your model folders.")
        self.assertTrue(outside.is_file())
        self.assertEqual(self.session.calls, [])

    # --- delete ----------------------------------------------------------------

    def glow_files(self) -> Path:
        folder = self.root / "glow-folder"
        for suffix in (".safetensors", ".civitai.info", ".preview.png", ".json"):
            touch(folder / f"glow{suffix}")
        touch(folder / "glow.v2.safetensors")
        return folder / "glow.safetensors"

    def test_delete_sends_the_weights_and_every_sibling_to_the_recycle_bin(self) -> None:
        weights = self.glow_files()

        result = delete_model(
            weights, trash=self.trash, job_manager=self.manager, data_dir=self.data
        )

        self.assertEqual(self.trashed[0], "glow.safetensors")
        self.assertEqual(
            sorted(self.trashed),
            ["glow.civitai.info", "glow.json", "glow.preview.png", "glow.safetensors"],
        )
        self.assertEqual(result, {"removed": self.trashed, "warnings": []})
        self.assertEqual([p.name for p in weights.parent.iterdir()], ["glow.v2.safetensors"])

    def test_delete_keeps_the_sidecars_when_the_weights_cannot_go(self) -> None:
        weights = self.glow_files()

        def refuse(path: Path) -> None:
            raise OSError("in use")

        self.on_trash = refuse
        with self.assertRaises(DownloadError) as caught:
            delete_model(weights, trash=self.trash, job_manager=self.manager, data_dir=self.data)
        self.assertEqual(
            str(caught.exception), "Could not send glow.safetensors to the Recycle Bin: in use"
        )
        self.assertEqual(self.trashed, [])
        self.assertEqual(len(list(weights.parent.iterdir())), 5)

        # A trash that raises nothing but leaves the weights is the same refusal.
        with self.assertRaises(DownloadError) as caught:
            delete_model(
                weights, trash=lambda path: None, job_manager=self.manager, data_dir=self.data
            )
        self.assertIn("it is still there", str(caught.exception))
        self.assertEqual(len(list(weights.parent.iterdir())), 5)
        self.assertEqual(len(self.saved_items()), 1)

    def test_a_sidecar_that_cannot_go_is_a_warning(self) -> None:
        weights = self.glow_files()

        def refuse_info(path: Path) -> None:
            if path.name.endswith(".civitai.info"):
                raise OSError("in use")

        self.on_trash = refuse_info
        result = delete_model(
            weights, trash=self.trash, job_manager=self.manager, data_dir=self.data
        )

        self.assertEqual(
            sorted(result["removed"]), ["glow.json", "glow.preview.png", "glow.safetensors"]
        )
        self.assertEqual(
            result["warnings"], ["Could not send glow.civitai.info to the Recycle Bin: in use"]
        )

    def test_delete_takes_the_file_out_of_the_check_results(self) -> None:
        weights = self.glow_files()
        glow_item = dict(self.v1_item, modelPath=str(weights), filePath=str(weights))
        self.job.items.append(copy.deepcopy(glow_item))
        payload = json.loads(self.saved.read_text(encoding="utf-8"))
        payload["items"].append(glow_item)
        self.saved.write_text(json.dumps(payload), encoding="utf-8")

        delete_model(weights, trash=self.trash, job_manager=self.manager, data_dir=self.data)

        self.assertEqual([item["modelPath"] for item in held(self.job.items)], [str(self.v1)])
        self.assertEqual([item["modelPath"] for item in held(self.saved_items())], [str(self.v1)])
        # The deleted file's row stays, marked, so its card says Deleted until the next check.
        self.assertEqual(
            [item["modelPath"] for item in self.job.items if item.get("deleted")], [str(weights)]
        )

    # --- Gallery ---------------------------------------------------------------

    def make_info_only(self, info: dict | None = None) -> Path:
        self.v1.unlink()
        info_path = self.pony / f"{V1_STEM}.civitai.info"
        info_path.write_text(
            json.dumps(info if info is not None else version_201()), encoding="utf-8"
        )
        item = dict(
            self.v1_item,
            modelPath=str(info_path),
            filePath=str(self.v1),
            metadataOnly=True,
        )
        self.job.items[:] = [copy.deepcopy(item)]
        payload = json.loads(self.saved.read_text(encoding="utf-8"))
        payload["items"] = [item]
        self.saved.write_text(json.dumps(payload), encoding="utf-8")
        return info_path

    def test_a_gallery_download_puts_the_weights_beside_the_info_file(self) -> None:
        info_path = self.make_info_only()
        info_bytes = info_path.read_bytes()

        request = self.run_one(self.gallery_request())

        self.assertEqual(self.v1.read_bytes(), V1_BODY)
        self.assertEqual(request.file_name, self.v1.name)
        self.assertEqual(info_path.read_bytes(), info_bytes)
        self.assertEqual(self.client.version_calls, [("201", True)])
        self.assertEqual(self.session.calls[0][0], V1_CIVITAI)
        # Its preview is there already.
        self.assertEqual(self.client.images, [])
        self.assertEqual(self.part_files(), [])
        for items in (held(self.job.items), held(self.saved_items())):
            self.assertEqual([item["modelPath"] for item in items], [str(self.v1)])
            self.assertIs(items[0]["metadataOnly"], False)
            self.assertEqual(items[0]["localVersionId"], "201")

    def test_a_gallery_download_fetches_a_missing_preview(self) -> None:
        self.make_info_only()
        (self.pony / f"{V1_STEM}.preview.png").unlink()
        self.run_one(self.gallery_request())
        self.assertEqual(
            self.client.images,
            [("https://image.civitai.red/glow-v1.jpeg", self.pony / f"{V1_STEM}.preview.png")],
        )

    def test_a_gallery_download_takes_the_file_its_info_describes(self) -> None:
        fp16_url = "https://civitai.red/api/download/models/201?fp=fp16"
        fp32_url = "https://civitai.red/api/download/models/201?fp=fp32"
        fp16 = dict(
            v1_file(),
            id=2011,
            name="glowV1_fp16.safetensors",
            hashes={"SHA256": "AB12"},
            downloadUrl=fp16_url,
        )
        fp32 = dict(
            v1_file(), id=2012, name="glowV1_fp32.safetensors", primary=False, downloadUrl=fp32_url
        )
        self.client.versions["201"] = version_201(files=[fp16, fp32])
        self.session.routes[fp16_url] = _redirect(V1_STORAGE)
        self.session.routes[fp32_url] = _redirect(V1_STORAGE)
        self.make_info_only(version_201(files=[dict(fp32, primary=True)]))

        self.run_one(self.gallery_request())

        self.assertEqual(self.session.calls[0][0], fp32_url)
        self.assertEqual(self.v1.read_bytes(), V1_BODY)

    def test_a_gallery_file_civitai_no_longer_has_is_refused(self) -> None:
        self.make_info_only()
        self.client.versions["201"] = version_201(
            files=[dict(v1_file(), hashes={"SHA256": "AB12"})]
        )
        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.gallery_request())
        self.assertEqual(str(caught.exception), "Civitai no longer has this file.")
        self.assertEqual(self.session.calls, [])

    def test_a_gallery_download_whose_weights_are_back_is_refused(self) -> None:
        with self.assertRaises(DownloadError) as caught:
            self.run_one(self.gallery_request())
        self.assertEqual(
            str(caught.exception), "Its info file is gone, or its weights are already there."
        )
        self.assertEqual(self.client.version_calls, [])

    # --- helpers of the module -------------------------------------------------

    def test_release_is_paid(self) -> None:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(release_is_paid({"paidAccess": {"permanent": True}}))
        self.assertTrue(release_is_paid({"paidAccess": {"permanent": False, "endsAt": tomorrow}}))
        self.assertFalse(release_is_paid({"paidAccess": {"permanent": False, "endsAt": yesterday}}))
        self.assertFalse(release_is_paid(version_202()))
        # A check item's version already says so.
        self.assertTrue(release_is_paid({"paid": "early", "paidUntil": tomorrow}))
        self.assertFalse(release_is_paid({"paid": "early", "paidUntil": yesterday}))
        self.assertTrue(release_is_paid({"paid": "permanent", "paidUntil": ""}))
        self.assertFalse(release_is_paid({"paid": "", "paidUntil": ""}))

    def test_info_payload_puts_the_downloaded_file_first(self) -> None:
        fp16 = dict(v2_file(), id=1, name="glow_fp16.safetensors")
        fp32 = dict(v2_file(), id=2, name="glow_fp32.safetensors", primary=False)
        version = version_202(files=[fp16, fp32])

        payload = info_payload(version, version["files"][1])

        self.assertEqual([f["id"] for f in payload["files"]], [2, 1])
        self.assertEqual([f["id"] for f in version["files"]], [1, 2])
        self.assertEqual(payload["extensions"]["source"], "comfy-civitai-updater")
        self.assertIn("updatedAt", payload["extensions"])
        self.assertNotIn("extensions", version)
        self.assertEqual(payload["id"], 202)

    def test_get_version_can_raise(self) -> None:
        client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=0)
        url = f"{MODEL_VERSION_BY_ID_URL}/202"
        client.session = FakeSession({url: FakeResponse(404)})
        with self.assertRaises(CivitaiRequestError) as caught:
            client.get_version(202, raise_on_error=True)
        self.assertEqual(caught.exception.status, 404)
        client.session = FakeSession({url: FakeResponse(404)})
        self.assertIsNone(client.get_version(202))


if __name__ == "__main__":
    unittest.main()
