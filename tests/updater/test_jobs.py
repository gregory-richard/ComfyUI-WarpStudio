from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from comfy_updater.archived_updates import ArchivedUpdateStore
from comfy_updater.jobs import JobManager, JobRecord, card_version_order
from comfy_updater.updater_service import _normalize_remote_versions


class FakeCivitaiClient:
    def version_page_url(self, model_id, version_id, nsfw=False):  # noqa: ARG002
        return f"https://example.com/models/{model_id}?modelVersionId={version_id}"


def make_item(
    *,
    model_id: str,
    model_name: str,
    model_type: str,
    local_version_id: str,
    local_version_name: str,
    local_version_date: str,
    base_model: str,
    model_path: str,
    remote_versions: list[dict],
    nsfw: bool = False,
    metadata_only: bool = False,
) -> dict:
    primary = remote_versions[0] if remote_versions else {}
    return {
        "modelId": model_id,
        "modelName": model_name,
        "modelType": model_type,
        "creatorName": "artist",
        "modelUrl": f"https://civitai.com/models/{model_id}",
        "modelPath": model_path,
        "metadataOnly": metadata_only,
        "baseModel": base_model,
        "localVersionId": local_version_id,
        "localVersionName": local_version_name,
        "localVersionDate": local_version_date,
        "localPreviewUrl": "",
        "localPreviewType": "image",
        "remoteVersions": remote_versions,
        "latestVersionId": primary.get("versionId", ""),
        "latestVersionName": primary.get("versionName", ""),
        "latestBaseModel": primary.get("baseModel", ""),
        "latestVersionDate": primary.get("versionDate", ""),
        "previewUrl": primary.get("previewUrl", ""),
        "previewType": primary.get("previewType", "image"),
        "versionUrl": primary.get("versionUrl", ""),
        "downloadUrl": primary.get("downloadUrl", ""),
        "hasUpdate": bool(remote_versions),
        "status": "ok",
        "nsfw": nsfw,
    }


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.archive_store = ArchivedUpdateStore(Path(self.tmpdir.name))
        self.manager = JobManager(self.archive_store)
        self.job = JobRecord(id="job-1", type="check-updates", status="completed")
        self.manager._jobs[self.job.id] = self.job

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_remote_version_normalization_preserves_availability(self) -> None:
        versions = _normalize_remote_versions(
            FakeCivitaiClient(),
            "m1",
            [
                {
                    "id": "v1",
                    "name": "Paid Window",
                    "publishedAt": "2026-03-07T00:00:00Z",
                    "baseModel": "SDXL 1.0",
                    "availability": "EarlyAccess",
                    "downloadUrl": "https://example.com/download/v1",
                }
            ],
        )

        self.assertEqual("EarlyAccess", versions[0]["availability"])
        self.assertEqual("early", versions[0]["paid"])

    def test_grouping_uses_newest_local_date_and_archive_partition(self) -> None:
        remote_versions = [
            {
                "versionId": "v-new-2",
                "versionName": "Newest",
                "versionDate": "2026-03-07T00:00:00Z",
                "baseModel": "SDXL 1.0",
                "previewUrl": "",
                "previewType": "image",
                "versionUrl": "https://example.com/v-new-2",
                "downloadUrl": "https://example.com/d-new-2",
                "availability": "EarlyAccess",
            },
            {
                "versionId": "v-new-1",
                "versionName": "Newer",
                "versionDate": "2026-03-06T00:00:00Z",
                "baseModel": "SDXL 1.0",
                "previewUrl": "",
                "previewType": "image",
                "versionUrl": "https://example.com/v-new-1",
                "downloadUrl": "https://example.com/d-new-1",
                "availability": "Public",
            },
            {
                "versionId": "v-old",
                "versionName": "Older",
                "versionDate": "2026-03-04T00:00:00Z",
                "baseModel": "SDXL 1.0",
                "previewUrl": "",
                "previewType": "image",
                "versionUrl": "https://example.com/v-old",
                "downloadUrl": "https://example.com/d-old",
            },
        ]
        self.job.items = [
            make_item(
                model_id="m1",
                model_name="Model One",
                model_type="checkpoint",
                local_version_id="v-local-a",
                local_version_name="Photo 1",
                local_version_date="2026-03-01T00:00:00Z",
                base_model="SDXL 1.0",
                model_path="C:\\models\\one-a.civitai.info",
                remote_versions=remote_versions,
                metadata_only=True,
            ),
            make_item(
                model_id="m1",
                model_name="Model One",
                model_type="checkpoint",
                local_version_id="v-local-b",
                local_version_name="Photo 2",
                local_version_date="2026-03-05T00:00:00Z",
                base_model="SDXL 1.0",
                model_path="C:\\models\\one-b.safetensors",
                remote_versions=remote_versions,
            ),
        ]
        self.archive_store.archive("m1", ["v-new-1"])

        result = self.manager.get_items("job-1", mode="updates", show_hidden=True)
        self.assertEqual(1, result["total"])
        grouped = result["items"][0]
        self.assertEqual("2026-03-05T00:00:00Z", grouped["newestLocalVersionDate"])
        self.assertEqual(["v-new-2"], [entry["versionId"] for entry in grouped["newVersions"]])
        self.assertEqual(["v-new-1"], [entry["versionId"] for entry in grouped["hiddenNewVersions"]])
        self.assertEqual("EarlyAccess", grouped["newVersions"][0]["availability"])
        self.assertEqual("EarlyAccess", grouped["latestAvailability"])
        self.assertTrue(grouped["localVersions"][1]["metadataOnly"])
        # Cached items predating filePath still copy the weights, not the sidecar.
        self.assertEqual("C:\\models\\one-a.civitai.info", grouped["localVersions"][1]["modelPath"])
        self.assertEqual("C:\\models\\one-a.safetensors", grouped["localVersions"][1]["filePath"])
        self.assertEqual("C:\\models\\one-b.safetensors", grouped["localVersions"][0]["filePath"])
        self.assertNotIn("v-old", [entry["versionId"] for entry in grouped["newVersions"]])

        summary = self.manager.summarize_check_items({"mode": "check", "total": 2}, self.job.items)
        self.assertEqual(1, summary["withUpdates"])
        self.assertEqual(1, summary["hiddenUpdates"])

    def _fallback_item(self, **extra) -> dict:
        item = make_item(
            model_id="m1",
            model_name="Model One",
            model_type="lora",
            local_version_id="v1",
            local_version_name="v1",
            local_version_date="2026-03-01T00:00:00Z",
            base_model="SDXL 1.0",
            model_path="C:\\models\\one.safetensors",
            remote_versions=[],
        )
        item.update(extra)
        return item

    def test_local_versions_carry_fallback_fields(self) -> None:
        self.job.items = [self._fallback_item(status="error", fallbackSource="huggingface", hasLocalPreview=True)]
        grouped = self.manager.get_items("job-1", mode="issues")["items"][0]
        v = grouped["localVersions"][0]
        self.assertEqual(("huggingface", True), (v["fallbackSource"], v["hasLocalPreview"]))

    def test_ungrouped_local_versions_carry_fallback_fields(self) -> None:
        self.job.items = [
            self._fallback_item(modelId="", modelUrl="", status="not_found", fallbackSource="civarchive", hasLocalPreview=True)
        ]
        grouped = self.manager.get_items("job-1", mode="issues")["items"][0]
        self.assertEqual("", grouped["modelId"])
        v = grouped["localVersions"][0]
        self.assertEqual(("civarchive", True), (v["fallbackSource"], v["hasLocalPreview"]))

    def test_fallback_fields_default_when_the_item_lacks_them(self) -> None:
        self.job.items = [self._fallback_item(status="error")]
        v = self.manager.get_items("job-1", mode="issues")["items"][0]["localVersions"][0]
        self.assertEqual(("", False), (v["fallbackSource"], v["hasLocalPreview"]))

    def test_multi_select_filters_apply_to_grouped_results(self) -> None:
        self.job.items = [
            make_item(
                model_id="m1",
                model_name="Checkpoint One",
                model_type="checkpoint",
                local_version_id="c1",
                local_version_name="c1",
                local_version_date="2026-03-01T00:00:00Z",
                base_model="SDXL 1.0",
                model_path="C:\\models\\c1.safetensors",
                remote_versions=[{
                    "versionId": "c2",
                    "versionName": "c2",
                    "versionDate": "2026-03-02T00:00:00Z",
                    "baseModel": "SDXL 1.0",
                    "previewUrl": "",
                    "previewType": "image",
                    "versionUrl": "",
                    "downloadUrl": "",
                }],
            ),
            make_item(
                model_id="m2",
                model_name="Lora One",
                model_type="lora",
                local_version_id="l1",
                local_version_name="l1",
                local_version_date="2026-03-01T00:00:00Z",
                base_model="Flux.1 D",
                model_path="C:\\models\\l1.safetensors",
                remote_versions=[{
                    "versionId": "l2",
                    "versionName": "l2",
                    "versionDate": "2026-03-03T00:00:00Z",
                    "baseModel": "Flux.1 D",
                    "previewUrl": "",
                    "previewType": "image",
                    "versionUrl": "",
                    "downloadUrl": "",
                }],
            ),
        ]

        result = self.manager.get_items(
            "job-1",
            mode="updates",
            model_types=["lora"],
            base_models=["Flux.1 D"],
        )
        self.assertEqual(1, result["total"])
        self.assertEqual("m2", result["items"][0]["modelId"])
        self.assertEqual(["checkpoint", "lora"], result["facets"]["modelTypes"])
        self.assertEqual(["Flux.1 D", "SDXL 1.0"], result["facets"]["baseModels"])
        # The menus serve every view: Issues offers the same options as Updates.
        issues = self.manager.get_items("job-1", mode="issues")
        self.assertEqual(result["facets"], issues["facets"])

    def test_start_refuses_second_concurrent_job(self) -> None:
        import threading

        release = threading.Event()
        started = threading.Event()

        def runner(progress, item_cb, control):  # noqa: ARG001
            started.set()
            release.wait(timeout=5)
            return {}, []

        first = self.manager.start("scan", runner)
        self.assertIsNotNone(first)
        self.assertTrue(started.wait(timeout=5))

        second = self.manager.start("scan", runner)
        self.assertIsNone(second)

        release.set()

    def test_seeded_items_are_replaced_when_fresh_results_arrive(self) -> None:
        import threading
        import time as time_module

        seeded = [
            {"modelPath": "C:\\models\\a.safetensors", "modelId": "m1", "_seeded": True},
            {"modelPath": "C:\\models\\b.safetensors", "modelId": "m2", "_seeded": True},
        ]
        fresh_a = {"modelPath": "C:\\models\\A.safetensors", "modelId": "m1", "status": "ok"}
        emitted = threading.Event()
        release = threading.Event()

        def runner(progress, item_cb, control):  # noqa: ARG001
            item_cb(fresh_a)
            emitted.set()
            release.wait(timeout=5)
            return {}, [fresh_a]

        job = self.manager.start("check-updates", runner, seed_items=seeded)
        self.assertIsNotNone(job)
        self.assertTrue(emitted.wait(timeout=5))

        with self.manager._lock:
            paths = [item["modelPath"] for item in job.items]
        # The fresh item replaces its seeded counterpart (case-insensitive),
        # while untouched seeds keep showing.
        self.assertIn("C:\\models\\A.safetensors", paths)
        self.assertNotIn("C:\\models\\a.safetensors", paths)
        self.assertIn("C:\\models\\b.safetensors", paths)

        release.set()
        for _ in range(100):
            if job.status == "completed":
                break
            time_module.sleep(0.05)
        self.assertEqual("completed", job.status)
        # Completion keeps only the fresh results.
        self.assertEqual([fresh_a], job.items)

    def test_cancel_keeps_unreplaced_seeded_items(self) -> None:
        import threading
        import time as time_module

        seeded = [
            {"modelPath": "C:\\models\\a.safetensors", "modelId": "m1", "_seeded": True},
            {"modelPath": "C:\\models\\b.safetensors", "modelId": "m2", "_seeded": True},
        ]
        fresh_a = {"modelPath": "C:\\models\\a.safetensors", "modelId": "m1", "status": "ok"}
        emitted = threading.Event()
        release = threading.Event()

        def runner(progress, item_cb, control):  # noqa: ARG001
            item_cb(fresh_a)
            emitted.set()
            release.wait(timeout=5)
            return {"partial": True}, [fresh_a]

        job = self.manager.start("check-updates", runner, seed_items=seeded)
        self.assertIsNotNone(job)
        self.assertTrue(emitted.wait(timeout=5))

        self.manager.cancel(job.id)
        release.set()
        for _ in range(100):
            if job.status == "cancelled":
                break
            time_module.sleep(0.05)
        self.assertEqual("cancelled", job.status)

        # The unreplaced seed survives the cancel; the fresh result stays.
        paths = sorted(item["modelPath"] for item in job.items)
        self.assertEqual(
            ["C:\\models\\a.safetensors", "C:\\models\\b.safetensors"],
            paths,
        )
        fresh = [item for item in job.items if not item.get("_seeded")]
        self.assertEqual([fresh_a], fresh)

    def test_sync_seeds_with_disk_hides_releases_downloaded_since_last_check(self) -> None:
        # The last check saw only the Krea file and offered the H3 release; the
        # H3 file was downloaded afterwards, and an old file was deleted.
        h3 = {
            "versionId": "h3", "versionName": "H3", "versionDate": "2026-09-14T00:00:00Z",
            "baseModel": "MiniMax H3", "previewUrl": "", "previewType": "image",
            "versionUrl": "", "downloadUrl": "",
        }
        krea_seed = make_item(
            model_id="m1", model_name="Face", model_type="lora",
            local_version_id="krea", local_version_name="Krea",
            local_version_date="2026-06-30T00:00:00Z", base_model="Krea 2",
            model_path="C:\\models\\krea\\face.safetensors", remote_versions=[h3],
        )
        gone_seed = make_item(
            model_id="m2", model_name="Gone", model_type="lora",
            local_version_id="old", local_version_name="Old",
            local_version_date="2026-01-01T00:00:00Z", base_model="Krea 2",
            model_path="C:\\models\\krea\\gone.safetensors", remote_versions=[],
        )
        fresh = make_item(
            model_id="m3", model_name="Fresh", model_type="lora",
            local_version_id="f", local_version_name="F",
            local_version_date="2026-01-01T00:00:00Z", base_model="Krea 2",
            model_path="C:\\models\\krea\\fresh.safetensors", remote_versions=[],
        )
        # A checkpoint is not part of this lora-only check, so its absence from
        # the discovered files says nothing about whether it still exists.
        other_type_seed = make_item(
            model_id="m4", model_name="Ckpt", model_type="checkpoint",
            local_version_id="c", local_version_name="C",
            local_version_date="2026-01-01T00:00:00Z", base_model="SDXL 1.0",
            model_path="C:\\models\\ckpt.safetensors", remote_versions=[],
        )
        for seed in (krea_seed, gone_seed, other_type_seed):
            seed["_seeded"] = True
        self.job.status = "running"
        self.job.items = [krea_seed, gone_seed, fresh, other_type_seed]
        self.job.seededPaths = {
            seed["modelPath"].lower() for seed in (krea_seed, gone_seed, other_type_seed)
        }

        before = self.manager.get_items("job-1", mode="updates")
        self.assertEqual(["m1"], [card["modelId"] for card in before["items"]])

        h3_seed = {
            "modelPath": "C:\\models\\h3\\face.safetensors",
            "modelType": "lora",
            "modelId": "m1",
            "baseModel": "MiniMax H3",
            "localVersionId": "h3",
            "localVersionName": "H3",
            "localVersionDate": "2026-09-14T00:00:00Z",
            "remoteVersions": [],
            "status": "ok",
            "_seeded": True,
        }
        # A seed for a path that already has a fresh result must not be added.
        duplicate = dict(h3_seed, modelPath="C:\\models\\krea\\FRESH.safetensors")
        current = {
            "c:\\models\\krea\\face.safetensors",
            "c:\\models\\krea\\fresh.safetensors",
            "c:\\models\\h3\\face.safetensors",
        }
        self.manager.sync_seeds_with_disk("job-1", ["lora"], current, [h3_seed, duplicate])

        paths = [item["modelPath"] for item in self.job.items]
        self.assertEqual(
            ["C:\\models\\krea\\face.safetensors", "C:\\models\\krea\\fresh.safetensors",
             "C:\\models\\ckpt.safetensors", "C:\\models\\h3\\face.safetensors"],
            paths,
        )
        # The new seed is replaceable once its file is re-checked.
        self.assertEqual(
            {"c:\\models\\krea\\face.safetensors", "c:\\models\\ckpt.safetensors",
             "c:\\models\\h3\\face.safetensors"},
            self.job.seededPaths,
        )
        after = self.manager.get_items("job-1", mode="updates")
        self.assertEqual([], after["items"])

    def test_sync_seeds_with_disk_ignores_unknown_jobs(self) -> None:
        self.manager.sync_seeds_with_disk("missing", ["lora"], set(), [{"modelPath": "C:\\a.safetensors"}])
        self.assertNotIn("missing", self.manager._jobs)

    def test_finished_jobs_are_pruned(self) -> None:
        from comfy_updater import jobs as jobs_module

        for index in range(jobs_module._MAX_FINISHED_JOBS + 3):
            record = JobRecord(
                id=f"finished-{index}",
                type="check-updates",
                status="completed",
                finishedAt=f"2026-01-{index + 1:02d}T00:00:00Z",
            )
            self.manager._jobs[record.id] = record

        def runner(progress, item_cb, control):  # noqa: ARG001
            return {}, []

        job = self.manager.start("scan", runner)
        self.assertIsNotNone(job)

        # Only the newest _MAX_FINISHED_JOBS finished jobs survive the prune.
        remaining = [job_id for job_id in self.manager._jobs if job_id.startswith("finished-")]
        self.assertEqual(
            [f"finished-{index}" for index in range(3, 8)],
            sorted(remaining),
        )
        self.assertNotIn("job-1", self.manager._jobs)

    def _newer(self, version_id: str = "v2", **extra) -> dict:
        return {"versionId": version_id, "versionName": version_id, "versionDate": "2026-04-01T00:00:00Z",
                "baseModel": "SDXL 1.0", **extra}

    def _item(self, model_id: str, path: str, remote_versions: list[dict]) -> dict:
        return make_item(
            model_id=model_id, model_name=model_id, model_type="lora", local_version_id=f"{model_id}-v1",
            local_version_name="v1", local_version_date="2026-01-01T00:00:00Z", base_model="SDXL 1.0",
            model_path=path, remote_versions=remote_versions,
        )

    def test_issues_view_lists_failed_models_and_counts_both_views(self) -> None:
        removed = self._item("m-gone", "C:\\models\\gone.safetensors", [])
        removed.update(status="error", error="404 Not Found. The model may have been removed from Civitai.")
        unknown = {"modelPath": "C:\\models\\merge.safetensors", "modelType": "lora", "status": "not_found", "modelId": ""}
        current = self._item("m-ok", "C:\\models\\ok.safetensors", [self._newer()])
        self.job.items = [removed, unknown, current]

        issues = self.manager.get_items("job-1", mode="issues")

        self.assertEqual(2, issues["total"])
        self.assertEqual({"updates": 1, "issues": 2}, issues["modeCounts"])
        rows = {card["localVersions"][0]["modelPath"]: card["localVersions"][0] for card in issues["items"]}
        self.assertEqual("error", rows["C:\\models\\gone.safetensors"]["status"])
        self.assertIn("404", rows["C:\\models\\gone.safetensors"]["error"])
        self.assertEqual("not_found", rows["C:\\models\\merge.safetensors"]["status"])
        updates = self.manager.get_items("job-1", mode="updates")
        self.assertEqual(["m-ok"], [card["modelId"] for card in updates["items"]])

    def test_only_cards_awaiting_their_recheck_are_provisional(self) -> None:
        self.job.status = "running"
        seeded = dict(self._item("m1", "C:\\models\\a.safetensors", [self._newer()]), _seeded=True)
        rechecked = self._item("m2", "C:\\models\\b.safetensors", [self._newer()])
        self.job.items = [seeded, rechecked]

        cards = {card["modelId"]: card for card in self.manager.get_items("job-1", mode="updates")["items"]}

        self.assertTrue(cards["m1"]["isProvisional"])
        self.assertFalse(cards["m2"]["isProvisional"])

    def _wait_finished(self, job) -> None:
        import time

        deadline = time.time() + 5
        while job.status not in ("completed", "failed", "cancelled") and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn(job.status, ("completed", "failed", "cancelled"))

    def test_on_complete_gets_a_snapshot_of_a_completed_job_only(self) -> None:
        import threading

        seen: list = []
        done = threading.Event()

        def hook(snapshot):
            seen.append(snapshot)
            done.set()

        job = self.manager.start("rename-dry-run", lambda p, e, c: ({"change": 1}, [{"key": "a"}]), on_complete=hook)
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(job.id, seen[0]["jobId"])
        self.assertEqual("completed", seen[0]["status"])
        self.assertEqual([{"key": "a"}], seen[0]["items"])
        self.assertEqual({"change": 1}, seen[0]["summary"])
        self.assertTrue(seen[0]["finishedAt"])
        # A snapshot: later edits to the job's rows do not reach it.
        job.items[0]["key"] = "edited"
        self.assertEqual("a", seen[0]["items"][0]["key"])

        def failing(p, e, c):
            raise RuntimeError("boom")

        failed = self.manager.start("rename-dry-run", failing, on_complete=hook)
        self._wait_finished(failed)

        def cancelled_run(p, e, control):
            control.cancel()
            return {}, []

        cancelled = self.manager.start("rename-dry-run", cancelled_run, on_complete=hook)
        self._wait_finished(cancelled)
        self.assertEqual(1, len(seen))

    def test_the_job_stays_active_until_on_complete_returns(self) -> None:
        import threading

        during: list = []
        done = threading.Event()

        def hook(snapshot):
            job = self.manager.get(snapshot["jobId"])
            # Edits and applies are refused while the saved copy is written, so none is lost.
            during.append((job.status, self.manager.get_active() is job))
            done.set()

        job = self.manager.start("rename-dry-run", lambda p, e, c: ({}, [{"key": "a"}]), on_complete=hook)
        self.assertTrue(done.wait(timeout=5))
        self._wait_finished(job)
        self.assertEqual([("running", True)], during)
        self.assertEqual("completed", job.status)
        self.assertEqual("Completed", job.message)
        self.assertEqual([{"key": "a"}], job.items)

    def test_pause_while_saving_is_ignored(self) -> None:
        import threading

        paused: list = []
        done = threading.Event()

        def hook(snapshot):
            # The work is finished; a pause here has nothing left to pause.
            paused.append(self.manager.pause(snapshot["jobId"]).status)
            done.set()

        job = self.manager.start("rename-dry-run", lambda p, e, c: ({}, [{"key": "a"}]), on_complete=hook)
        self.assertTrue(done.wait(timeout=5))
        self._wait_finished(job)
        self.assertEqual(["running"], paused)
        self.assertEqual("completed", job.status)
        self.assertEqual("Completed", job.message)

    def test_a_failing_on_complete_hook_does_not_fail_the_job(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        def hook(snapshot):
            raise OSError("disk full")

        with redirect_stdout(StringIO()):
            job = self.manager.start("rename-dry-run", lambda p, e, c: ({}, [{"key": "a"}]), on_complete=hook)
            self._wait_finished(job)
            import time

            time.sleep(0.05)
        self.assertEqual("completed", job.status)

    def test_restore_job_registers_a_finished_job_under_its_old_id(self) -> None:
        record = self.manager.restore_job(
            "rename-dry-run",
            "run-1",
            startedAt="2026-11-14T09:00:00+00:00",
            finishedAt="2026-11-14T10:00:00+00:00",
            summary={"change": 1},
            items=[{"key": "a"}, {"key": "b"}],
        )
        self.assertIs(record, self.manager.get("run-1"))
        self.assertEqual("completed", record.status)
        self.assertEqual("rename-dry-run", record.type)
        self.assertEqual("2026-11-14T10:00:00+00:00", record.restoredFrom)
        self.assertEqual(2, record.progress)
        self.assertEqual(2, record.total)
        self.assertEqual({"change": 1}, record.summary)
        self.assertIsNone(self.manager.get_active())
        self.assertIsNone(JobRecord(id="x", type="scan").restoredFrom)

    def test_pruning_keeps_the_newest_dry_run_and_the_newest_completed_one(self) -> None:
        from comfy_updater import jobs as jobs_module

        self.manager._jobs = {
            "dry-done": JobRecord(id="dry-done", type="rename-dry-run", status="completed",
                                  finishedAt="2026-01-01T00:00:00Z"),
            "dry-cancelled": JobRecord(id="dry-cancelled", type="rename-dry-run", status="cancelled",
                                       finishedAt="2026-01-02T00:00:00Z"),
        }
        for index in range(jobs_module._MAX_FINISHED_JOBS + 2):
            record = JobRecord(id=f"scan-{index}", type="scan", status="completed",
                               finishedAt=f"2026-02-{index + 1:02d}T00:00:00Z")
            self.manager._jobs[record.id] = record

        self.assertIsNotNone(self.manager.start("scan", lambda progress, item_cb, control: ({}, [])))

        # The saved (restorable) dry run and the one the panel shows both stay.
        self.assertIn("dry-done", self.manager._jobs)
        self.assertIn("dry-cancelled", self.manager._jobs)
        self.assertNotIn("scan-0", self.manager._jobs)

    def test_pruning_keeps_the_newest_check(self) -> None:
        from comfy_updater import jobs as jobs_module

        self.manager._jobs = {"check": JobRecord(id="check", type="check-updates", status="completed",
                                                 finishedAt="2026-01-01T00:00:00Z")}
        for index in range(jobs_module._MAX_FINISHED_JOBS + 2):
            record = JobRecord(id=f"scan-{index}", type="scan", status="completed",
                               finishedAt=f"2026-02-{index + 1:02d}T00:00:00Z")
            self.manager._jobs[record.id] = record

        self.assertIsNotNone(self.manager.start("scan", lambda progress, item_cb, control: ({}, [])))

        # The panel is still showing that check, however many scans ran since.
        self.assertIn("check", self.manager._jobs)
        self.assertNotIn("scan-0", self.manager._jobs)

    def test_paid_access_is_read_and_a_closed_early_window_is_dropped(self) -> None:
        versions = _normalize_remote_versions(
            FakeCivitaiClient(),
            "m1",
            [
                {"id": 1, "name": "always", "publishedAt": "2026-04-03T00:00:00Z",
                 "paidAccess": {"permanent": True, "endsAt": None}},
                {"id": 2, "name": "early", "publishedAt": "2026-04-02T00:00:00Z",
                 "paidAccess": {"permanent": False, "endsAt": "2999-01-01T00:00:00Z"}},
                {"id": 3, "name": "free", "publishedAt": "2026-04-01T00:00:00Z", "paidAccess": None},
            ],
        )
        by_id = {version["versionId"]: version for version in versions}
        self.assertEqual(("permanent", ""), (by_id["1"]["paid"], by_id["1"]["paidUntil"]))
        self.assertEqual(("early", "2999-01-01T00:00:00Z"), (by_id["2"]["paid"], by_id["2"]["paidUntil"]))
        self.assertEqual("", by_id["3"]["paid"])

        # Results are cached; a window that closed since the check is free now.
        closed = dict(by_id["2"], versionId="4", versionDate="2026-04-04T00:00:00Z", paidUntil="2020-01-01T00:00:00Z")
        self.job.items = [self._item("m1", "C:\\models\\a.safetensors", [by_id["1"], by_id["2"], by_id["3"], closed])]

        card = self.manager.get_items("job-1", mode="updates")["items"][0]

        paid = {version["versionId"]: (version["paid"], version["paidUntil"]) for version in card["newVersions"]}
        self.assertEqual(
            {"1": ("permanent", ""), "2": ("early", "2999-01-01T00:00:00Z"), "3": ("", ""), "4": ("", "")},
            paid,
        )

    def test_grouping_aggregates_nsfw_flag(self) -> None:
        # Test grouped items
        self.job.items = [
            make_item(
                model_id="m_nsfw",
                model_name="NSFW Model",
                model_type="checkpoint",
                local_version_id="v1",
                local_version_name="v1",
                local_version_date="2026-03-01T00:00:00Z",
                base_model="SDXL 1.0",
                model_path="C:\\models\\nsfw-1.safetensors",
                remote_versions=[],
                nsfw=True,
            ),
            make_item(
                model_id="m_nsfw",
                model_name="NSFW Model",
                model_type="checkpoint",
                local_version_id="v2",
                local_version_name="v2",
                local_version_date="2026-03-02T00:00:00Z",
                base_model="SDXL 1.0",
                model_path="C:\\models\\nsfw-2.safetensors",
                remote_versions=[],
                nsfw=False,
            ),
        ]
        result = self.manager.get_items("job-1")
        self.assertEqual(1, result["total"])
        self.assertTrue(result["items"][0]["nsfw"])

        # Test ungrouped items (no modelId/modelUrl)
        self.job.items = [
            {
                "modelPath": "C:\\models\\unknown.safetensors",
                "modelType": "checkpoint",
                "nsfw": True,
                "localVersionId": "v1",
                "localVersionName": "v1",
                "localVersionDate": "2026-03-01T00:00:00Z",
                "baseModel": "SDXL 1.0",
                "localPreviewUrl": "",
                "localPreviewType": "image",
            }
        ]
        result = self.manager.get_items("job-1")
        self.assertEqual(1, result["total"])
        self.assertTrue(result["items"][0]["nsfw"])


    def test_card_lists_its_versions_newest_first_in_one_order(self) -> None:
        remote_versions = [
            {"versionId": "v-new-2", "versionName": "Newest", "versionDate": "2026-03-07T00:00:00Z", "baseModel": "SDXL 1.0"},
            {"versionId": "v-new-1", "versionName": "Newer", "versionDate": "2026-03-06T00:00:00Z", "baseModel": "SDXL 1.0"},
            {"versionId": "v-old", "versionName": "Older", "versionDate": "2026-03-04T00:00:00Z", "baseModel": "SDXL 1.0"},
        ]
        common = dict(model_id="m1", model_name="Model One", model_type="checkpoint",
                      base_model="SDXL 1.0", remote_versions=remote_versions)
        self.job.items = [
            make_item(local_version_id="v-local-a", local_version_name="Photo 1",
                      local_version_date="2026-03-01T00:00:00Z", model_path="C:\\models\\a.safetensors", **common),
            make_item(local_version_id="v-local-b", local_version_name="Photo 2",
                      local_version_date="2026-03-05T00:00:00Z", model_path="C:\\models\\b.safetensors", **common),
        ]
        self.archive_store.archive("m1", ["v-new-1"])

        card = self.manager.get_items("job-1", mode="updates", show_hidden=True)["items"][0]

        # The newest release sits on top, the oldest saved file at the bottom.
        self.assertEqual([["new", 0], ["hidden", 0], ["saved", 0], ["saved", 1]], card["versionOrder"])
        lists = {"new": card["newVersions"], "hidden": card["hiddenNewVersions"], "saved": card["localVersions"]}
        self.assertEqual(
            ["Newest", "Newer", "Photo 2", "Photo 1"],
            [lists[kind][index]["versionName"] for kind, index in card["versionOrder"]],
        )

    def test_a_file_civitai_never_identified_has_its_one_row(self) -> None:
        self.job.items = [{"modelPath": "C:\\models\\mystery.safetensors", "modelType": "lora", "status": "not_found"}]

        card = self.manager.get_items("job-1", mode="issues")["items"][0]

        self.assertEqual([["saved", 0]], card["versionOrder"])


class CardVersionOrderTests(unittest.TestCase):
    def test_saved_and_new_releases_interleave_by_date(self) -> None:
        saved = [{"publishedAt": "2024-08-01T00:00:00Z"}, {"publishedAt": "2024-01-01T00:00:00Z"}]
        new = [{"versionDate": "2025-02-01T00:00:00Z"}, {"versionDate": "2024-06-01T00:00:00Z"}]
        hidden = [{"versionDate": "2025-01-01T00:00:00Z"}]

        self.assertEqual(
            [["new", 0], ["hidden", 0], ["saved", 0], ["new", 1], ["saved", 1]],
            card_version_order(saved, new, hidden),
        )

    def test_undated_files_go_last_and_keep_their_order(self) -> None:
        saved = [{"publishedAt": "2024-01-01T00:00:00Z"}, {"publishedAt": ""}, {}]
        new = [{"versionDate": "2025-01-01T00:00:00Z"}]

        self.assertEqual(
            [["new", 0], ["saved", 0], ["saved", 1], ["saved", 2]],
            card_version_order(saved, new, []),
        )

    def test_a_release_on_the_same_day_as_a_saved_file_goes_above_it(self) -> None:
        saved = [{"publishedAt": "2024-05-01T00:00:00Z"}]
        new = [{"versionDate": "2024-05-01T00:00:00Z"}]

        self.assertEqual([["new", 0], ["saved", 0]], card_version_order(saved, new, []))


class FinishListenerTests(unittest.TestCase):
    def _run(self, runner, cancel: bool = False):
        manager = JobManager()
        seen: list = []
        done = threading.Event()

        def listener(record):
            seen.append((record.type, record.status))
            done.set()

        manager.add_finish_listener(listener)
        started = threading.Event()

        def wrapped(progress, emit, control):
            started.set()
            return runner(control)

        job = manager.start("scan", wrapped)
        if cancel:
            self.assertTrue(started.wait(5))
            manager.cancel(job.id)
        self.assertTrue(done.wait(5))
        return seen, manager.get(job.id)

    def test_finish_listeners_run_once_per_job(self) -> None:
        def ok(control):
            return {}, []

        def bad(control):
            raise RuntimeError("boom")

        def slow(control):
            end = time.monotonic() + 5
            while not control.is_cancelled() and time.monotonic() < end:
                time.sleep(0.01)
            return {}, []

        for label, runner, cancel, status in (
            ("completed", ok, False, "completed"),
            ("failed", bad, False, "failed"),
            ("cancelled", slow, True, "cancelled"),
        ):
            with self.subTest(label):
                seen, record = self._run(runner, cancel)
                self.assertEqual([("scan", status)], seen)
                self.assertEqual(status, record.status)

    def test_a_failing_listener_does_not_stop_the_others(self) -> None:
        manager = JobManager()
        calls: list = []
        done = threading.Event()

        def failing(record):
            raise RuntimeError("x")

        def working(record):
            calls.append(record.id)
            done.set()

        manager.add_finish_listener(failing)
        manager.add_finish_listener(working)
        job = manager.start("scan", lambda progress, emit, control: ({}, []))
        self.assertTrue(done.wait(5))
        self.assertEqual([job.id], calls)


def _wait_idle(manager) -> None:
    end = time.monotonic() + 5
    while manager.get_active() is not None and time.monotonic() < end:
        time.sleep(0.005)


class ReservationTests(unittest.TestCase):
    def test_a_reservation_refuses_other_jobs_until_the_reserved_one_starts(self) -> None:
        manager = JobManager()
        manager.reserve("model-download")
        self.assertIsNone(manager.start("check-updates", lambda p, e, c: ({}, [])))
        self.assertIsNotNone(manager.start("model-download", lambda p, e, c: ({}, [])))
        _wait_idle(manager)
        # The reservation ended with the reserved start.
        self.assertIsNotNone(manager.start("check-updates", lambda p, e, c: ({}, [])))
        _wait_idle(manager)

    def test_release_reservation_lets_any_job_start(self) -> None:
        manager = JobManager()
        manager.reserve("model-download")
        manager.release_reservation()
        self.assertIsNotNone(manager.start("scan", lambda p, e, c: ({}, [])))
        _wait_idle(manager)


if __name__ == "__main__":
    unittest.main()
