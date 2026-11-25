"""The check's naming phase: which new releases get a name, and the run that names them.

The names and examples here are made up.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comfy_updater.archived_updates import ArchivedUpdateStore
from comfy_updater.civitai_client import CivitaiRequestError
from comfy_updater.jobs import JobControl, JobManager
from comfy_updater.naming_phase import NamingTask, PhaseCodes, PhaseControl, naming_tasks, run_naming
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore
from comfy_updater.release_names import ReleaseNameStore

from test_download_target import FakeClient, three_release_model
from test_renamer_naming import FakeGenerator

GLOW_ANSWER = "title: glow\noriginal:\nversion 1: v3\nversion 2: v2"
FERN_ANSWER = "title: fern\noriginal:\nversion 1: v2"


def fern_model(base: str = "Illustrious") -> dict:
    def release(version_id: int, name: str) -> dict:
        return {
            "id": version_id,
            "name": name,
            "baseModel": base,
            "files": [
                {
                    "id": version_id * 10,
                    "name": f"fern{name}.safetensors",
                    "type": "Model",
                    "primary": True,
                    "hashes": {"SHA256": f"AA{version_id}"},
                }
            ],
        }

    return {
        "id": 301,
        "type": "LORA",
        "name": "Fern",
        "creator": {"username": "maker"},
        "modelVersions": [release(302, "v2"), release(301, "v1")],
    }


def card(model_id="101", model_type="lora", new=("202", "203"), local=None, own=None) -> dict:
    return {
        "modelId": model_id,
        "modelType": model_type,
        "ownName": own,
        "newVersions": [{"versionId": version_id} for version_id in new],
        "localVersions": local or [],
    }


def local_row(path, *, archived=False, deleted=False) -> dict:
    return {"modelPath": str(path), "archived": archived, "deleted": deleted}


class NamingTasksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.d_path = Path("D") / "Lora" / "moss - glow - v1 (ill).safetensors"
        self.z_path = Path("Z") / "Lora" / "moss - glow - v1 (ill).civitai.info"
        self.mirrored = Path("D") / "Lora" / "mirrored" / "moss - glow - v1 (ill).safetensors"
        self.reference_calls: list = []

    def archive_reference(self, path, model_type):
        self.reference_calls.append((path, model_type))
        return self.mirrored

    def tasks(self, cards, named=lambda _version_id: False):
        return naming_tasks(cards, named=named, archive_reference=self.archive_reference)

    def task_for(self, **fields) -> NamingTask:
        fields.setdefault("local", [local_row(self.d_path)])
        tasks = self.tasks([card(**fields)])
        self.assertEqual(1, len(tasks))
        return tasks[0]

    def test_tasks_cover_unnamed_new_releases_newest_first(self) -> None:
        cards = [card(local=[local_row(self.d_path)], own={"creator": "moss", "title": "glow", "version": ""})]
        # Listed oldest first: the task still asks for the newest first.
        cards[0]["newVersions"] = [
            {"versionId": "202", "versionDate": "2026-02-01"},
            {"versionId": "203", "versionDate": "2026-03-01"},
        ]
        tasks = self.tasks(cards)
        self.assertEqual([NamingTask("101", "lora", ["203", "202"], self.d_path, ("moss", "glow"))], tasks)

    def test_hidden_and_named_releases_are_left_out(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ArchivedUpdateStore(Path(tmp.name))
        store.archive("101", ["202"])
        item = {
            "modelId": "101",
            "modelName": "Glow",
            "modelType": "lora",
            "modelPath": str(self.d_path),
            "localVersionId": "201",
            "status": "ok",
            "remoteVersions": [
                {"versionId": "204", "versionName": "v4", "versionDate": "2026-04-01T00:00:00.000Z"},
                {"versionId": "203", "versionName": "v3", "versionDate": "2026-03-01T00:00:00.000Z"},
                {"versionId": "202", "versionName": "v2", "versionDate": "2026-02-01T00:00:00.000Z"},
                {"versionId": "201", "versionName": "v1", "versionDate": "2026-01-01T00:00:00.000Z"},
            ],
        }
        cards = JobManager(store).naming_cards([item])
        tasks = self.tasks(cards, named=lambda version_id: version_id == "204")
        self.assertEqual([["203"]], [task.version_ids for task in tasks])

    def test_a_type_the_renamer_skips_gets_no_task(self) -> None:
        self.assertEqual([], self.tasks([card(model_type="vae", local=[local_row(self.d_path)])]))

    def test_an_archive_only_model_names_beside_its_mirrored_folder(self) -> None:
        task = self.task_for(local=[local_row(self.z_path, archived=True)])
        self.assertEqual(self.mirrored, task.reference)
        self.assertEqual([(str(self.z_path), "lora")], self.reference_calls)

    def test_a_drive_row_is_preferred_over_an_archived_one(self) -> None:
        task = self.task_for(local=[local_row(self.z_path, archived=True), local_row(self.d_path)])
        self.assertEqual(self.d_path, task.reference)
        self.assertEqual([], self.reference_calls)

    def test_a_model_without_a_reference_gets_no_task(self) -> None:
        self.assertEqual([], self.tasks([card(local=[])]))
        self.assertEqual([], self.tasks([card(local=[local_row(self.d_path, deleted=True)])]))
        self.assertEqual([], naming_tasks([card(local=[local_row(self.z_path, archived=True)])], named=lambda _v: False, archive_reference=lambda _p, _t: None))

    def test_a_lora_named_creator_title_base_keeps_its_names(self) -> None:
        own = {"creator": "moss", "title": "glow", "version": ""}
        self.assertEqual(("moss", "glow"), self.task_for(own=own).own)

    def test_a_checkpoint_needs_all_three_parts_to_keep_its_names(self) -> None:
        own = {"creator": "cyberrealistic", "title": "v10", "version": ""}
        self.assertIsNone(self.task_for(model_type="checkpoint", own=own).own)
        full = {"creator": "cyberrealistic", "title": "xl", "version": "v10"}
        self.assertEqual(("cyberrealistic", "xl"), self.task_for(model_type="checkpoint", own=full).own)

    def test_a_card_without_a_model_id_gets_no_task(self) -> None:
        self.assertEqual([], self.tasks([card(model_id="", local=[local_row(self.d_path)])]))


class RunNamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.base_codes.set("Illustrious", "ill")
        self.cache = AnswerCache(self.data)
        self.store = ReleaseNameStore(self.data, self.base_codes)
        self.reference = self.tmp / "loras" / "ill" / "maker - glow - v1 (ill).safetensors"
        self.progress: list = []
        self.control = _Control()

    def tasks(self) -> list[NamingTask]:
        return [
            NamingTask("101", "lora", ["203", "202"], self.reference, None),
            NamingTask("301", "lora", ["302"], self.reference, None),
        ]

    def run_naming(self, generator, tasks=None, client=None):
        return run_naming(
            self.tasks() if tasks is None else tasks,
            client=client or FakeClient({101: three_release_model(), 301: fern_model()}),
            generator=generator,
            decisions=self.decisions,
            base_codes=self.base_codes,
            cache=self.cache,
            clip_name="qwen3.safetensors",
            store=self.store,
            progress=lambda *args: self.progress.append(args),
            control=self.control,
        )

    def test_each_model_is_named_in_one_call_and_stored_at_once(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER})
        client = FakeClient({101: three_release_model(), 301: fern_model()})
        result = self.run_naming(generator, client=client)
        self.assertEqual(2, len(generator.calls))
        self.assertEqual(["101", "301"], client.calls)
        self.assertTrue(all(self.store.has(version_id) for version_id in ("202", "203", "302")))
        self.assertEqual("maker - glow - v3 (ill)", self.store.get("203").stem)
        self.assertEqual({"named": 3, "total": 3, "error": ""}, result)
        self.assertEqual("Naming 0 of 3", self.progress[0][2])
        self.assertEqual((0, 3), self.progress[0][:2])

    def test_a_stopped_phase_keeps_the_names_done(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER})
        real_generate = generator.generate

        def generate(messages, max_lengths=None):
            answers = real_generate(messages, max_lengths)
            self.control.stopped = True  # the user stops the naming during model 1's call
            return answers

        generator.generate = generate
        result = self.run_naming(generator)
        self.assertTrue(self.store.has("202"))
        self.assertFalse(self.store.has("302"))
        self.assertEqual(1, len(generator.calls))
        self.assertEqual("", result["error"])

    def test_a_stopped_phase_reports_no_count_after_the_stop(self) -> None:
        # The job's "Stopping the naming" then stays, and the job ends "Completed".
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER})
        real_generate = generator.generate
        reported_at_stop: list = []

        def generate(messages, max_lengths=None):
            answers = real_generate(messages, max_lengths)
            self.control.stopped = True
            reported_at_stop.append(len(self.progress))
            return answers

        generator.generate = generate
        self.run_naming(generator)
        self.assertEqual([1], reported_at_stop)
        self.assertEqual([(0, 3, "Naming 0 of 3")], self.progress)

    def test_a_pause_holds_the_naming_before_each_model(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER})
        held_after: list = []
        # Where the phase waits while the check is paused: before each model's call, with
        # the releases named so far already counted, so the button says where it paused.
        self.control.wait_if_paused = lambda: held_after.append((len(generator.calls), self.progress[-1][0]))
        self.run_naming(generator)
        self.assertEqual([(0, 0), (1, 2)], held_after)

    def test_a_stop_while_paused_names_no_further_model(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER})

        def paused_then_stopped():
            if generator.calls:
                self.control.stopped = True  # Stop clicked while paused after model 1

        self.control.wait_if_paused = paused_then_stopped
        result = self.run_naming(generator)
        self.assertEqual(1, len(generator.calls))
        self.assertTrue(self.store.has("202"))
        self.assertFalse(self.store.has("302"))
        self.assertEqual("", result["error"])

    def test_a_naming_model_error_ends_the_phase_with_its_text(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER, "Fern": FERN_ANSWER}, fail_on_call=1)
        result = self.run_naming(generator)
        self.assertEqual(0, result["named"])
        self.assertIn("out of memory", result["error"])
        self.assertLessEqual(len(result["error"]), 300)
        self.assertEqual(1, len(generator.calls))

    def test_a_naming_model_error_after_a_stop_says_nothing(self) -> None:
        generator = FakeGenerator({"Glow": GLOW_ANSWER}, fail_on_call=1)
        real_generate = generator.generate

        def generate(messages, max_lengths=None):
            self.control.stopped = True
            return real_generate(messages, max_lengths)

        generator.generate = generate
        self.assertEqual("", self.run_naming(generator)["error"])

    def test_a_model_civitai_no_longer_has_is_skipped(self) -> None:
        client = FakeClient({101: CivitaiRequestError("gone", status=404), 301: fern_model()})
        generator = FakeGenerator({"Fern": FERN_ANSWER})
        result = self.run_naming(generator, client=client)
        self.assertEqual({"named": 1, "total": 3, "error": ""}, result)
        self.assertFalse(self.store.has("202"))
        self.assertTrue(self.store.has("302"))

    def test_another_failure_skips_that_model_only(self) -> None:
        client = FakeClient({101: ValueError("odd"), 301: fern_model()})
        result = self.run_naming(FakeGenerator({"Fern": FERN_ANSWER}), client=client)
        self.assertEqual((1, 3), (result["named"], result["total"]))

    def test_a_new_base_gets_one_code_across_models(self) -> None:
        glow = three_release_model()
        glow["modelVersions"] = [v for v in glow["modelVersions"] if v["id"] == 202]
        glow["modelVersions"][0]["baseModel"] = "Krea 2"
        client = FakeClient({101: glow, 301: fern_model("Krea 2")})
        generator = FakeGenerator(
            {
                "Glow": "title: glow\noriginal:\nversion 1: v2\nbase Krea 2: krea2",
                "Fern": "title: fern\noriginal:\nversion 1: v2\nbase Krea 2: krea9",
            }
        )
        tasks = [
            NamingTask("101", "lora", ["202"], self.reference, None),
            NamingTask("301", "lora", ["302"], self.reference, None),
        ]
        self.run_naming(generator, tasks=tasks, client=client)
        self.assertEqual(["(krea2)", "(krea2)"], [self.store.get(v).stem[-7:] for v in ("202", "302")])
        # The table is the user's to change: the phase never writes it.
        self.assertIsNone(self.base_codes.code("Krea 2"))


class PhaseAdaptersTests(unittest.TestCase):
    def test_the_phase_control_stops_on_a_cancel_or_a_phase_stop(self) -> None:
        control = JobControl("job")
        phase = PhaseControl(control)
        self.assertFalse(phase.is_cancelled())
        control.stop_phase()
        self.assertTrue(phase.is_cancelled())
        phase.wait_if_paused()
        other = JobControl("job")
        other.cancel()
        self.assertTrue(PhaseControl(other).is_cancelled())

    def test_phase_codes_lay_learned_codes_over_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            table = BaseCodeStore(Path(tmp))
            table.set("Pony", "pony")
            codes = PhaseCodes(table)
            codes.learn("Krea 2", "krea2")
            self.assertEqual(("pony", "krea2"), (codes.code("Pony"), codes.code("Krea 2")))
            self.assertEqual({"Pony": "pony", "Krea 2": "krea2"}, codes.all())
            self.assertIsNone(table.code("Krea 2"))


class _Control:
    def __init__(self) -> None:
        self.stopped = False

    def is_cancelled(self) -> bool:
        return self.stopped

    def wait_if_paused(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
