from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from comfy_updater.check_records import (
    check_item_for,
    downloaded_item,
    edit_check_items,
    update_check_records,
)
from comfy_updater.constants import CACHE_SCHEMA_VERSION
from comfy_updater.jobs import JobManager


def remote_versions() -> list[dict]:
    return [
        {"versionId": "202", "versionName": "v2", "versionDate": "2026-02-01T00:00:00.000Z"},
        {"versionId": "201", "versionName": "v1", "versionDate": "2026-01-01T00:00:00.000Z"},
    ]


def v1_item(path: Path) -> dict:
    return {
        "modelPath": str(path),
        "filePath": str(path),
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


def v2_info() -> dict:
    return {
        "id": 202,
        "modelId": 101,
        "name": "v2",
        "baseModel": "Illustrious",
        "publishedAt": "2026-02-01T00:00:00.000Z",
        "model": {"name": "Glow", "type": "LORA", "nsfw": False},
        "images": [{"type": "image", "url": "https://image.civitai.red/glow-v2.jpeg"}],
        "files": [{"name": "glowV2.safetensors", "primary": True, "hashes": {"SHA256": "AB12"}}],
    }


class CheckRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "loras"
        self.v1 = self.root / "pony" / "maker - glow - v1 (pony).safetensors"
        self.v2 = self.root / "ill" / "glowV2.safetensors"
        self.v2_info = self.root / "ill" / "glowV2.civitai.info"
        self.v2_info.parent.mkdir(parents=True)
        self.v2_info.write_text(json.dumps(v2_info()), encoding="utf-8")
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.manager = JobManager()

    def restore(
        self, items: list[dict], job_id: str = "j1", when: str = "2026-03-01T00:00:00+00:00"
    ):
        return self.manager.restore_job(
            "check-updates",
            job_id,
            startedAt=when,
            finishedAt=when,
            summary={"total": len(items), "withUpdates": 1},
            items=items,
        )

    def save_check(self, items: list[dict], schema: int = CACHE_SCHEMA_VERSION) -> Path:
        path = self.data / "last_check.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": schema,
                    "checkedAt": "2026-03-01T00:00:00+00:00",
                    "summary": {"total": len(items), "withUpdates": 1},
                    "items": items,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_edit_check_items_removes_by_model_or_file_path_and_adds(self) -> None:
        info = self.root / "pony" / "maker - c - v1 (pony).civitai.info"
        items = [
            v1_item(self.v1),
            {"modelPath": str(info), "filePath": str(self.root / "pony" / "c.safetensors")},
            {"modelPath": str(self.root / "kept.safetensors")},
            {"modelPath": str(self.v2), "localVersionId": "old"},
        ]
        # Another spelling of the same paths still matches.
        weights_spelled = os.path.join(str(self.root), "pony", ".", self.v1.name)
        new = {"modelPath": str(self.v2), "localVersionId": "202"}
        extra = {"modelPath": str(self.root / "extra.safetensors")}

        changed = edit_check_items(
            items,
            remove=[weights_spelled, str(self.root / "pony" / "c.safetensors")],
            add=[new, extra],
        )

        self.assertTrue(changed)
        self.assertEqual(
            [item["modelPath"] for item in items],
            [
                str(self.root / "kept.safetensors"),
                str(self.v2),
                str(self.root / "extra.safetensors"),
            ],
        )
        self.assertEqual(items[1]["localVersionId"], "202")

    def test_edit_check_items_reports_no_change(self) -> None:
        items = [v1_item(self.v1)]
        self.assertFalse(edit_check_items(items, remove=[str(self.v2)], add=[]))
        self.assertEqual(items, [v1_item(self.v1)])

    def test_downloaded_item_carries_the_models_releases(self) -> None:
        source = v1_item(self.v1)

        item = downloaded_item(source, weights=self.v2, info_path=self.v2_info, model_type="lora")

        self.assertEqual(item["modelPath"], str(self.v2))
        self.assertEqual(item["filePath"], str(self.v2))
        self.assertEqual(item["localVersionId"], "202")
        self.assertEqual(item["modelId"], "101")
        self.assertIs(item["metadataOnly"], False)
        self.assertEqual(item["remoteVersions"], remote_versions())
        self.assertIsNot(item["remoteVersions"], source["remoteVersions"])
        self.assertEqual(item["creatorName"], "maker")
        self.assertEqual(item["modelUrl"], "https://civitai.red/models/101")
        self.assertIs(item["hasLocalPreview"], False)
        self.assertEqual(item["fallbackSource"], "")
        self.assertTrue(item["lastCheckedAt"])

    def test_downloaded_item_sees_the_new_preview(self) -> None:
        (self.root / "ill" / "glowV2.preview.png").write_bytes(b"png")
        item = downloaded_item({}, weights=self.v2, info_path=self.v2_info, model_type="lora")
        self.assertIs(item["hasLocalPreview"], True)
        self.assertEqual(item["remoteVersions"], [])

    def test_downloaded_item_takes_nothing_from_another_model(self) -> None:
        source = dict(v1_item(self.v1), modelId="999", creatorName="studio-k")
        item = downloaded_item(source, weights=self.v2, info_path=self.v2_info, model_type="lora")
        self.assertEqual(item["remoteVersions"], [])
        self.assertNotEqual(item.get("creatorName"), "studio-k")

    def test_downloaded_item_is_none_without_an_identifying_info(self) -> None:
        self.v2_info.write_text(json.dumps({"name": "no ids"}), encoding="utf-8")
        self.assertIsNone(
            downloaded_item({}, weights=self.v2, info_path=self.v2_info, model_type="lora")
        )
        missing = self.root / "ill" / "gone.civitai.info"
        self.assertIsNone(
            downloaded_item({}, weights=self.v2, info_path=missing, model_type="lora")
        )

    def test_update_check_records_edits_held_jobs_and_the_saved_check(self) -> None:
        job = self.restore([v1_item(self.v1)])
        cached = self.manager.load_cached_check({"items": [v1_item(self.v1)], "summary": {}})
        saved = self.save_check([v1_item(self.v1)])
        new = downloaded_item(
            v1_item(self.v1), weights=self.v2, info_path=self.v2_info, model_type="lora"
        )

        update_check_records(self.manager, self.data, remove=[str(self.v1)], add=[new])

        self.assertEqual(job.items, [new])
        self.assertEqual(cached.items, [new])
        self.assertIsNot(job.items[0], cached.items[0])
        payload = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"], [new])
        self.assertEqual(payload["schemaVersion"], CACHE_SCHEMA_VERSION)
        self.assertEqual(payload["summary"]["withUpdates"], 0)
        self.assertEqual(payload["summary"]["total"], 1)

    def test_an_outdated_saved_check_is_left_alone(self) -> None:
        saved = self.save_check([v1_item(self.v1)], schema=CACHE_SCHEMA_VERSION - 1)
        before = saved.read_bytes()
        update_check_records(self.manager, self.data, remove=[str(self.v1)], add=[])
        self.assertEqual(saved.read_bytes(), before)

    def test_no_saved_check_is_not_created(self) -> None:
        update_check_records(self.manager, self.data, remove=[str(self.v1)], add=[])
        self.assertFalse((self.data / "last_check.json").exists())

    def test_check_item_for_copies_from_the_newest_check_job(self) -> None:
        older = dict(v1_item(self.v1), localVersionName="older")
        self.restore([older], job_id="old", when="2026-01-01T00:00:00+00:00")
        newest = self.restore([v1_item(self.v1)], job_id="new")

        found = check_item_for(self.manager, os.path.join(str(self.v1.parent), ".", self.v1.name))

        self.assertEqual(found, v1_item(self.v1))
        found["remoteVersions"].clear()
        self.assertEqual(newest.items[0]["remoteVersions"], remote_versions())
        self.assertIsNone(check_item_for(self.manager, str(self.v2)))

    def test_check_item_for_without_a_check_job(self) -> None:
        self.assertIsNone(check_item_for(self.manager, str(self.v1)))

    def test_the_records_do_not_share_the_added_item(self) -> None:
        job = self.restore([])
        new = {"modelPath": str(self.v2), "remoteVersions": remote_versions()}
        original = copy.deepcopy(new)
        update_check_records(self.manager, self.data, remove=[], add=[new])
        job.items[0]["remoteVersions"].clear()
        self.assertEqual(new, original)


if __name__ == "__main__":
    unittest.main()
