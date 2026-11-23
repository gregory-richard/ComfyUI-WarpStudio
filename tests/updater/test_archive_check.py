from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_updater import updater_service
from comfy_updater.civitai_client import CivitaiClient, CivitaiRequestError
from comfy_updater.config_store import ConfigStore
from comfy_updater.updater_service import UpdaterService


class _Response:
    ok = True
    status_code = 200
    reason = "OK"

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeModelClient(CivitaiClient):
    """The real client with only the model lookup replaced."""

    def __init__(self, model: dict | None = None, error: str = ""):
        super().__init__(api_key="", timeout_seconds=5, max_retries=0)
        self.model = model
        self.error = error
        self.downloaded: list[tuple[str, Path]] = []

    def get_model(self, model_id, *, raise_on_error=False):  # noqa: ARG002
        if self.error:
            raise CivitaiRequestError(self.error)
        return self.model

    def download_image_as_png(self, url, target_path, max_bytes=10_000_000):  # noqa: ARG002
        self.downloaded.append((url, target_path))
        return True


def _model(**stats) -> dict:
    return {
        "id": 123,
        "name": "Example Model",
        "creator": {"username": "artist"},
        "stats": stats,
        "modelVersions": [
            {
                "id": 456,
                "name": "Local Version",
                "baseModel": "SDXL 1.0",
                "publishedAt": "2026-03-05T00:00:00Z",
                "images": [{"type": "image", "url": "https://img.example/local.jpeg"}],
            },
        ],
    }


class ArchiveCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.archive = self.tmp / "Z"
        folder = self.archive / "Lora"
        folder.mkdir(parents=True)
        self.info_path = folder / "a.civitai.info"
        # No preview beside it, and an empty image list: both invite a download.
        self.info_path.write_text(
            json.dumps(
                {
                    "id": 456,
                    "modelId": 123,
                    "name": "Local Version",
                    "baseModel": "SDXL 1.0",
                    "publishedAt": "2026-03-05T00:00:00Z",
                    "model": {"name": "Example Model"},
                    "images": [],
                }
            ),
            encoding="utf-8",
        )
        (folder / "a.safetensors").write_bytes(b"weights")
        d_lora = self.tmp / "D" / "Lora"
        d_lora.mkdir(parents=True)
        self.store = ConfigStore(self.tmp / "data")
        self.store.update(
            {
                "useComfyPaths": False,
                "useExtraModelPaths": False,
                "requestDelayMs": 0,
                "customPaths": {"lora": [str(d_lora)]},
            }
        )

    def _snapshot(self) -> dict[str, tuple[int, int]]:
        return {
            str(path.relative_to(self.archive)): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(self.archive.rglob("*"))
        }

    def _run(self, client, **payload):
        messages: list[str] = []
        with patch("comfy_updater.updater_service.CivitaiClient", return_value=client):
            summary, items = UpdaterService(self.store).run_archive_check(
                {"modelTypes": ["lora"], **payload},
                lambda current, total, message: messages.append(message),
                archive_root=self.archive,
            )
        return summary, items, messages

    def test_archive_check_writes_nothing_in_the_archive(self) -> None:
        before = self._snapshot()
        client = FakeModelClient(_model())

        summary, items, _messages = self._run(client)

        self.assertEqual("ok", items[0]["status"])
        self.assertEqual(before, self._snapshot())
        self.assertEqual([], client.downloaded)
        self.assertEqual(1, summary["resolved"])

    def test_archive_items_are_marked_and_carry_stats(self) -> None:
        client = FakeModelClient(_model(downloadCount=11570, thumbsUpCount=833, commentCount=3))

        summary, items, messages = self._run(client)

        item = items[0]
        self.assertTrue(item["archived"])
        self.assertEqual({"downloads": 11570, "likes": 833, "comments": 3}, item["modelStats"])
        self.assertEqual(str(self.archive / "Lora" / "a.safetensors"), item["filePath"])
        self.assertEqual("archive", summary["mode"])
        self.assertEqual(0, summary["withoutInfo"])
        self.assertIn("archive: a.civitai.info", messages)

    def test_a_failed_archive_lookup_keeps_its_model_id(self) -> None:
        client = FakeModelClient(error="500 Internal Server Error")

        summary, items, _messages = self._run(client)

        self.assertEqual("error", items[0]["status"])
        self.assertEqual("123", items[0]["modelId"])
        self.assertTrue(items[0]["archived"])
        self.assertNotIn("modelStats", items[0])
        self.assertEqual(1, summary["errors"])

    def _unplug_after_listing(self):
        """The real listing, then the archive drive drops out before any lookup."""
        real_listing = updater_service.list_archive_entries

        def listing(archive_root, type_roots):
            listed = real_listing(archive_root, type_roots)
            shutil.rmtree(self.archive)
            return listed

        return patch("comfy_updater.updater_service.list_archive_entries", listing)

    def test_a_drive_lost_after_listing_still_checks_what_was_listed(self) -> None:
        client = FakeModelClient(_model(downloadCount=5))

        with self._unplug_after_listing():
            summary, items, _messages = self._run(client)

        self.assertFalse(self.archive.exists())
        self.assertEqual([("ok", "123", "456")], [(i["status"], i["modelId"], i["localVersionId"]) for i in items])
        self.assertEqual((1, 0), (summary["resolved"], summary["notFound"]))

    def test_an_unexpected_failure_after_the_drive_is_lost_keeps_the_model_id(self) -> None:
        client = FakeModelClient(_model())
        client.get_model = lambda model_id, *, raise_on_error=False: 1 / 0  # noqa: ARG005

        with self._unplug_after_listing():
            _summary, items, _messages = self._run(client)

        self.assertEqual([("error", "123")], [(item["status"], item["modelId"]) for item in items])

    def test_an_archive_without_model_folders_fails_and_looks_nothing_up(self) -> None:
        (self.archive / "Lora").rename(self.archive / "Stuff")
        for layout in ("renamed", "empty"):  # an unmounted mount point is an empty folder
            if layout == "empty":
                shutil.rmtree(self.archive / "Stuff")
            with self.subTest(layout=layout):
                client = FakeModelClient(error="must not be asked")

                summary, items, _messages = self._run(client)

                self.assertEqual("No model folders found in the archive folder.", summary["archiveError"])
                self.assertEqual([], items)

    def test_plain_check_items_carry_model_stats(self) -> None:
        models = self.tmp / "D" / "Lora"
        weights = models / "b.safetensors"
        weights.write_bytes(b"weights")
        (models / "b.civitai.info").write_text(
            json.dumps({"id": 456, "modelId": 123, "name": "Local Version", "images": []}),
            encoding="utf-8",
        )
        client = FakeModelClient(_model(downloadCount=7, thumbsUpCount=2, commentCount=1))

        with patch("comfy_updater.updater_service.CivitaiClient", return_value=client):
            _summary, items = UpdaterService(self.store).run_check_updates(
                {"modelTypes": ["lora"]}, lambda current, total, message: None
            )

        self.assertEqual({"downloads": 7, "likes": 2, "comments": 1}, items[0]["modelStats"])


class ModelStatsTests(unittest.TestCase):
    def test_model_stats_uses_the_cached_model(self) -> None:
        client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=0)
        requests: list[str] = []

        def get(url, **kwargs):  # noqa: ARG001
            requests.append(url)
            return _Response({"id": 5, "stats": {"downloadCount": 9, "thumbsUpCount": 4}, "modelVersions": []})

        client.session.get = get  # type: ignore[assignment]

        client.get_model_versions_for_model(5)
        stats = client.model_stats(5)

        self.assertEqual(1, len(requests))
        self.assertEqual({"downloads": 9, "likes": 4, "comments": 0}, stats)

    def test_model_stats_is_empty_for_an_errored_model(self) -> None:
        client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=0)
        client._model_cache["5"] = CivitaiRequestError("404")

        self.assertEqual({}, client.model_stats(5))


if __name__ == "__main__":
    unittest.main()
