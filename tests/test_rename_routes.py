from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from comfy_updater.config_store import ConfigStore
from unittest import mock

from comfy_updater.jobs import JobManager, JobRecord
from comfy_updater.rename_store import BaseCodeStore, DecisionStore, DryRunStore, RenameJournal, RenameLog
from comfy_updater.routes import (
    NAMING_MODEL_FILENAME,
    NAMING_MODEL_URL,
    RENAME_REASON_NO_MODEL,
    RENAME_REASON_NO_TEXTGEN,
    STALE_DRY_RUN_ERROR,
    _decision_problem,
    _existing_naming_model,
    _normalize_apply_payload,
    _normalize_config_payload,
    _normalize_decision_payload,
    _short_error,
    RENAME_DRY_RUN_JOB,
    ExclusiveSlot,
    apply_followups,
    download_naming_model,
    dry_run_rows_writer,
    rename_rows_payload,
    restore_last_dry_run,
    save_finished_dry_run,
    mark_dry_runs_stale,
    run_apply,
    update_check_job_paths,
    reassemble_rows,
    recovered_path_mapping,
    rename_job_problem,
    rename_status,
    reveal_command,
    reveal_response,
)


def _row(folder, old, new, *, model_id="1", version_id="10", file_key=None, parts=None, flags=None, status=None, **extra):
    parts = parts or {"creator": "maker", "title": "title", "version": "v1", "precision": "", "base": "sdxl"}
    row = {
        "key": str(Path(folder) / (old + ".safetensors")).lower(),
        "modelId": model_id,
        "versionId": version_id,
        "fileKey": file_key or f"file:{version_id}",
        "modelPath": str(Path(folder) / (old + ".safetensors")),
        "folder": str(folder),
        "oldStem": old,
        "newStem": new,
        "parts": dict(parts),
        "changed": [],
        "flags": list(flags or []),
        "status": status or ("unchanged" if old == new else "change"),
        "reason": "",
        "selected": True,
        "baseModel": "SDXL 1.0",
        "liveTitle": "Title",
    }
    row.update(extra)
    return row


class RenameRouteHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.folder = self.root / "loras"
        self.folder.mkdir()
        self.decisions = DecisionStore(self.data_dir)
        self.base_codes = BaseCodeStore(self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- status ---------------------------------------------------------

    def test_status_without_generate_text(self) -> None:
        status = rename_status({"namingModel": "q.safetensors"}, False, ["q.safetensors"], RenameJournal(self.data_dir))
        self.assertFalse(status["available"])
        self.assertEqual(
            "This ComfyUI has no Generate Text node. Update ComfyUI to use the renamer.", status["reason"]
        )
        self.assertEqual(RENAME_REASON_NO_TEXTGEN, status["reason"])

    def test_status_without_naming_model(self) -> None:
        status = rename_status({"namingModel": ""}, True, ["q.safetensors"], RenameJournal(self.data_dir))
        self.assertFalse(status["available"])
        self.assertEqual("Choose a naming model in the settings, or download one.", status["reason"])
        self.assertEqual(RENAME_REASON_NO_MODEL, status["reason"])
        self.assertEqual(["q.safetensors"], status["namingModels"])

    def test_status_with_a_missing_naming_model(self) -> None:
        status = rename_status({"namingModel": "llm\\gone.safetensors"}, True, ["q.safetensors"], RenameJournal(self.data_dir))
        self.assertFalse(status["available"])
        self.assertEqual("The naming model llm\\gone.safetensors is not in your text encoder folders.", status["reason"])

    def test_status_available_with_journal_and_base_codes(self) -> None:
        journal = RenameJournal(self.data_dir)
        journal.begin([{"kind": "rename", "from": "a", "to": "b", "entry": "a"}, {"kind": "rename", "from": "c", "to": "d", "entry": "c"}])
        journal.mark_done(0)
        self.base_codes.set("SDXL 1.0", "sdxl")
        status = rename_status(
            {"namingModel": "llm\\q.safetensors"}, True, ["llm\\q.safetensors"], journal, base_codes=self.base_codes
        )
        self.assertTrue(status["available"])
        self.assertEqual("", status["reason"])
        self.assertEqual("llm\\q.safetensors", status["namingModel"])
        self.assertEqual({"pending": True, "steps": 1}, status["journal"])
        self.assertEqual({"SDXL 1.0": "sdxl"}, status["baseCodes"])

    def test_status_matches_the_model_regardless_of_slash_and_case(self) -> None:
        status = rename_status({"namingModel": "LLM/Q.safetensors"}, True, ["llm\\q.safetensors"], RenameJournal(self.data_dir))
        self.assertTrue(status["available"])
        self.assertEqual({"pending": False, "steps": 0}, status["journal"])

    # --- payloads -------------------------------------------------------

    def test_normalize_decision_payload(self) -> None:
        payload = _normalize_decision_payload(
            {
                "jobId": "job",
                "modelId": "1",
                "versionId": 10,
                "fileKey": "file:5",
                "title": " New Title ",
                "creator": None,
                "version": "",
                "keep": False,
                "askAgain": "yes",
                "evil": "x",
            }
        )
        self.assertEqual(
            {"jobId": "job", "modelId": "1", "fileKey": "file:5", "title": " New Title ", "version": "", "keep": False},
            payload,
        )

    def test_normalize_decision_payload_drops_empty_ids(self) -> None:
        self.assertEqual({"askAgain": True}, _normalize_decision_payload({"modelId": " ", "jobId": 3, "askAgain": True}))

    def test_normalize_apply_payload_dedupes(self) -> None:
        payload = _normalize_apply_payload(
            {"jobId": "job", "keys": ["a", "b", "a", 3, ""], "removeDuplicates": ["d", "d"], "other": 1}
        )
        self.assertEqual({"jobId": "job", "keys": ["a", "b"], "removeDuplicates": ["d"]}, payload)
        self.assertEqual({"jobId": "", "keys": [], "removeDuplicates": []}, _normalize_apply_payload({"keys": "a"}))

    def test_config_payload_takes_naming_model(self) -> None:
        self.assertEqual({"namingModel": "llm\\q.safetensors"}, _normalize_config_payload({"namingModel": "llm\\q.safetensors"}))
        self.assertEqual({"namingModel": ""}, _normalize_config_payload({"namingModel": 5}))

    def test_config_default_naming_model(self) -> None:
        store = ConfigStore(self.data_dir)
        self.assertEqual("", store.get()["namingModel"])
        self.assertEqual("q.safetensors", store.update({"namingModel": "q.safetensors"})["namingModel"])
        self.assertEqual("", store.update({"namingModel": ["x"]})["namingModel"])
        self.assertEqual("", ConfigStore(self.root / "other").get_public()["namingModel"])

    def test_short_error(self) -> None:
        self.assertEqual("short", _short_error("short"))
        long = "x" * 1000
        self.assertLessEqual(len(_short_error(long)), 300)
        self.assertTrue(_short_error(long).endswith("…"))

    # --- reassembling ---------------------------------------------------

    def test_title_decision_applies_to_every_row_and_recomputes_collision(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)", version_id="10")
        b = _row(
            self.folder,
            "old b",
            "maker - title - v2 (sdxl)",
            version_id="11",
            parts={"creator": "maker", "title": "title", "version": "v2", "precision": "", "base": "sdxl"},
        )
        other = _row(
            self.folder,
            "old c",
            "maker - better - v1 (sdxl)",
            model_id="2",
            version_id="20",
            parts={"creator": "maker", "title": "better", "version": "v1", "precision": "", "base": "sdxl"},
        )
        rows = [a, b, other]
        self.decisions.set_model("1", title="Better")
        listing = {str(self.folder).lower(): {"old a", "old b", "old c"}}
        result = reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1")
        # The other model's row changed too (collision), so it comes back with the model's rows.
        self.assertEqual([a, b, other], result)
        self.assertEqual("maker - better - v1 (sdxl)", a["newStem"])
        self.assertEqual("maker - better - v2 (sdxl)", b["newStem"])
        self.assertEqual("better", a["parts"]["title"])
        self.assertIn("title", a["changed"])
        # a now collides with the other model's row; both are flagged and deselected.
        self.assertIn("collision", a["flags"])
        self.assertIn("collision", other["flags"])
        self.assertFalse(a["selected"])
        self.assertFalse(other["selected"])
        self.assertNotIn("collision", b["flags"])
        # The title is now the other model's too.
        self.assertEqual(["title-clash"], b["flags"])
        self.assertFalse(b["selected"])

        # A version decision moves a away again: the collision goes on both rows.
        self.decisions.set_version("10", "v3")
        self.assertEqual([a, b, other], reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1"))
        self.assertEqual("maker - better - v3 (sdxl)", a["newStem"])
        self.assertNotIn("collision", a["flags"])
        self.assertNotIn("collision", other["flags"])
        self.assertEqual(["title-clash"], a["flags"])
        self.assertFalse(a["selected"])
        # Nothing changed for the other row this time: only the model's rows come back.
        self.decisions.set_version("10", "v4")
        self.assertEqual([a, b], reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1"))

    def test_a_moving_row_stays_a_change_and_keep_cancels_the_move(self) -> None:
        target = self.root / "pony"
        moving = _row(self.folder, "maker - title - v1 (sdxl)", "maker - title - v1 (sdxl)")
        moving["newFolder"] = str(target)
        rows = [moving]
        listing = {str(self.folder).lower(): {"maker - title - v1 (sdxl)"}, str(target).lower(): set()}
        reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1")
        # The name stays, the folder changes: still something to apply.
        self.assertEqual("change", moving["status"])
        self.assertIn("folder", moving["changed"])
        self.assertEqual(str(target), moving["newFolder"])
        # Keeping the current name keeps the file where it is too; undoing it brings the move back.
        self.decisions.set_file(moving["fileKey"], keep=True)
        reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1")
        self.assertEqual("kept", moving["status"])
        self.assertNotIn("newFolder", moving)
        self.decisions.set_file(moving["fileKey"], keep=False)
        reassemble_rows(rows, self.decisions, self.base_codes, listing, model_id="1")
        self.assertEqual("change", moving["status"])
        self.assertEqual(str(target), moving["newFolder"])

    def test_rows_of_other_folders_are_not_returned(self) -> None:
        elsewhere = self.root / "other"
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        far = _row(elsewhere, "old z", "maker - other - v1 (sdxl)", model_id="2", flags=["collision"], selected=False,
                   parts={"creator": "maker", "title": "other", "version": "v1", "precision": "", "base": "sdxl"})
        self.decisions.set_model("1", title="Better")
        self.assertEqual([a], reassemble_rows([a, far], self.decisions, self.base_codes, {}, model_id="1"))
        self.assertEqual(["collision"], far["flags"])

    # --- title and version clashes ----------------------------------------

    def towel_rows(self):
        elsewhere = self.root / "ill"
        towel = _row(self.folder, "old towel", "bolt - towel - v1 (sdxl)", model_id="1", version_id="10",
                     flags=["title-clash"], selected=False, liveTitle="Towel", uploader="bolt",
                     parts={"creator": "bolt", "title": "towel", "version": "v1", "precision": "", "base": "sdxl"})
        towel2 = _row(elsewhere, "old towel 2", "bolt - towel - v1 (sdxl)", model_id="2", version_id="20",
                      flags=["title-clash"], selected=False, liveTitle="Towel 2", uploader="bolt",
                      parts={"creator": "bolt", "title": "towel", "version": "v1", "precision": "", "base": "sdxl"})
        return towel, towel2

    def test_title_edit_that_ends_a_title_clash_clears_it_on_both_models(self) -> None:
        towel, towel2 = self.towel_rows()
        rows = [towel, towel2]
        self.decisions.set_model("2", title="towel 2")
        result = reassemble_rows(rows, self.decisions, self.base_codes, {}, model_id="2")
        # The other model's row is in another folder, but its flags changed: it comes back too.
        self.assertEqual([towel, towel2], result)
        self.assertEqual([], towel["flags"])
        self.assertTrue(towel["selected"])
        self.assertEqual([], towel2["flags"])
        self.assertEqual("bolt - towel 2 - v1 (sdxl)", towel2["newStem"])
        # The uploader and live title stay on the rows.
        self.assertEqual("bolt", towel2["uploader"])
        self.assertEqual("Towel 2", towel2["liveTitle"])

    def test_title_edit_that_makes_a_title_clash_flags_both_models(self) -> None:
        towel, towel2 = self.towel_rows()
        towel2["parts"]["title"] = "towel 2"
        towel2["newStem"] = "bolt - towel 2 - v1 (sdxl)"
        towel["flags"], towel2["flags"] = [], []
        self.decisions.set_model("2", title="towel")
        reassemble_rows([towel, towel2], self.decisions, self.base_codes, {}, model_id="2")
        self.assertEqual(["title-clash"], towel["flags"])
        self.assertFalse(towel["selected"])
        self.assertEqual(["title-clash"], towel2["flags"])
        # Both titles the user's own: their choice, no clash.
        self.decisions.set_model("1", title="towel")
        reassemble_rows([towel, towel2], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual([], towel["flags"])
        self.assertEqual([], towel2["flags"])

    def test_same_title_under_different_bases_is_no_title_clash(self) -> None:
        towel, towel2 = self.towel_rows()
        towel2["parts"]["base"] = "ill"
        towel2["newStem"] = "bolt - towel - v1 (ill)"
        towel2["baseModel"] = "Illustrious"
        reassemble_rows([towel, towel2], self.decisions, self.base_codes, {}, model_id="2")
        self.assertEqual([], towel["flags"])
        self.assertTrue(towel["selected"])
        self.assertEqual([], towel2["flags"])

    def test_version_edit_that_ends_a_version_clash_clears_it(self) -> None:
        high = _row(self.folder, "hl high", "maker - title - v1 (sdxl)", version_id="10", flags=["version-clash"])
        low = _row(self.root / "low", "hl low", "maker - title - v1 (sdxl)", version_id="11", flags=["version-clash"],
                   file_key="file:11")
        self.decisions.set_version("11", "v1 low")
        reassemble_rows([high, low], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual([], high["flags"])
        self.assertTrue(high["selected"])
        self.assertEqual([], low["flags"])
        self.decisions.set_version("11", "v1")
        reassemble_rows([high, low], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(["version-clash"], high["flags"])
        self.assertEqual(["version-clash"], low["flags"])
        self.assertFalse(low["selected"])

    def test_decided_creator_drops_maker_unknown(self) -> None:
        a = _row(self.folder, "glowbase-4b", "siteofficial - glowbase - 4b (sdxl)",
                 flags=["official-file", "maker-unknown"], selected=False)
        self.decisions.set_model("1", creator="quillworks")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual([], a["flags"])

    # --- whether a dry run may be edited or applied ---------------------

    def test_rename_job_problem(self) -> None:
        dry = JobRecord(id="dry", type="rename-dry-run", status="completed")
        apply_job = JobRecord(id="apply", type="rename-apply", status="running")
        self.assertIsNone(rename_job_problem(dry, active_job=None, applied=set()))
        self.assertEqual(404, rename_job_problem(None, active_job=None, applied=set())[0])
        self.assertEqual(404, rename_job_problem(apply_job, active_job=None, applied=set())[0])
        self.assertEqual((409, STALE_DRY_RUN_ERROR), rename_job_problem(dry, active_job=None, applied={"dry"}))
        self.assertEqual("Run a new dry run.", STALE_DRY_RUN_ERROR.split(". ", 1)[1])
        self.assertEqual((409, "Wait for the dry run to finish."), rename_job_problem(dry, active_job=dry, applied=set()))
        self.assertEqual((409, "A job is already running."), rename_job_problem(dry, active_job=apply_job, applied=set()))
        # A consumed dry run stays refused after its apply finished.
        self.assertEqual(409, rename_job_problem(dry, active_job=apply_job, applied={"dry"})[0])

    def test_decision_ids_must_belong_to_the_model(self) -> None:
        rows = [_row(self.folder, "old a", "x - y (sdxl)", version_id="10", file_key="file:7")]
        self.assertEqual("", _decision_problem({"fileKey": "file:7", "keep": True}, rows))
        self.assertEqual("", _decision_problem({"versionId": "10", "version": "v2"}, rows))
        self.assertEqual("", _decision_problem({"title": "t"}, rows))
        self.assertIn("fileKey", _decision_problem({"fileKey": "file:99", "keep": True}, rows))
        self.assertIn("versionId", _decision_problem({"versionId": "99", "version": "v2"}, rows))
        self.assertTrue(_decision_problem({"version": "v2"}, rows))
        self.assertTrue(_decision_problem({"keep": True}, rows))
        self.assertEqual("", _decision_problem({"askAgain": True, "fileKey": "file:99"}, rows))

    def test_existing_naming_model_anywhere_in_the_folders(self) -> None:
        self.assertEqual(
            r"llm\Qwen3.5_9B_int8_convrot.safetensors",
            _existing_naming_model(["gemma.safetensors", r"llm\Qwen3.5_9B_int8_convrot.safetensors"]),
        )
        self.assertEqual(NAMING_MODEL_FILENAME, _existing_naming_model([NAMING_MODEL_FILENAME]))
        self.assertEqual("", _existing_naming_model(["qwen3.5_9b_int8_convrot.safetensors.bak", "x.safetensors"]))

    def test_config_updates_from_two_threads_keep_both(self) -> None:
        store = ConfigStore(self.data_dir)

        def save(key, values):
            for value in values:
                store.update({key: value})

        threads = [
            threading.Thread(target=save, args=("namingModel", [f"m{i}" for i in range(40)])),
            threading.Thread(target=save, args=("requestDelayMs", list(range(40)))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual("m39", store.get()["namingModel"])
        self.assertEqual(39, store.get()["requestDelayMs"])

    def test_exists_flag_for_a_file_outside_the_rename_set(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        self.decisions.set_model("1", title="taken")
        listing = {str(self.folder).lower(): {"old a", "maker - taken - v1 (sdxl)"}}
        reassemble_rows([a], self.decisions, self.base_codes, listing, model_id="1")
        self.assertIn("exists", a["flags"])
        self.assertFalse(a["selected"])

    def test_unchanged_after_a_decision(self) -> None:
        a = _row(self.folder, "maker - title - v1 (sdxl)", "maker - title - v2 (sdxl)", flags=["invented:v2"],
                 parts={"creator": "maker", "title": "title", "version": "v2", "precision": "", "base": "sdxl"})
        self.decisions.set_version("10", "V1")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual("unchanged", a["status"])
        self.assertEqual([], a["flags"])
        self.assertFalse(a["selected"])

    def test_decided_parts_drop_their_ai_flags(self) -> None:
        a = _row(
            self.folder,
            "old a",
            "someone - fancy title - v4 (sdxl)",
            flags=["maker-from-description", "maker-not-in-description", "invented:fancy", "invented:v4", "official-file"],
            parts={"creator": "someone", "title": "fancy title", "version": "v4", "precision": "", "base": "sdxl"},
        )
        self.decisions.set_model("1", title="Title", creator="maker")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        # The version is still the AI's, so its invented word stays.
        self.assertEqual(["invented:v4"], a["flags"])
        self.assertEqual("maker - title - v4 (sdxl)", a["newStem"])

    def test_bad_decision_is_flagged(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        self.decisions.set_model("1", title="a - b")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertIn("separator:title", a["flags"])
        self.assertFalse(a["selected"])

    def test_editing_a_base_word_out_makes_the_row_selectable(self) -> None:
        a = _row(self.folder, "old a", "maker - glow sdxl - v1.0 (sdxl)", flags=["base-word:sdxl", "version-format:v1.0"],
                 parts={"creator": "maker", "title": "glow sdxl", "version": "v1.0", "precision": "", "base": "sdxl"},
                 selected=False)
        self.decisions.set_model("1", title="Glow")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(["version-format:v1.0"], a["flags"])
        self.assertFalse(a["selected"])
        self.decisions.set_version("10", "v1")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual("maker - glow - v1 (sdxl)", a["newStem"])
        self.assertEqual([], a["flags"])
        self.assertTrue(a["selected"])

    def test_editing_a_base_word_in_adds_the_flag(self) -> None:
        a = _row(self.folder, "old a", "maker - glow - v2 (sdxl)",
                 parts={"creator": "maker", "title": "glow", "version": "v2", "precision": "", "base": "sdxl"})
        self.base_codes.set("Krea 2", "krea2")
        self.decisions.set_model("1", title="Glow XL")
        self.decisions.set_version("10", "v2 krea2")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        # "xl" counts on an SDXL base; "krea2" is a code from the table.
        self.assertEqual(["base-word:xl", "base-word:krea2"], a["flags"])
        self.assertFalse(a["selected"])

    def test_base_word_vocabulary_includes_the_models_other_bases(self) -> None:
        a = _row(self.folder, "old a", "maker - glow - v2 (sdxl)", modelBaseModels=["SDXL 1.0", "Glimmer 3"],
                 parts={"creator": "maker", "title": "glow", "version": "v2", "precision": "", "base": "sdxl"})
        self.decisions.set_version("10", "v2 glimmer")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(["base-word:glimmer"], a["flags"])

    def test_creator_from_file_name_stays_until_the_creator_is_decided(self) -> None:
        a = _row(self.folder, "maker - old - v1 (sdxl)", "maker - title - v1 (sdxl)", flags=["creator-from-file-name"],
                 selected=False)
        self.decisions.set_model("1", title="Title")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(["creator-from-file-name"], a["flags"])
        self.assertFalse(a["selected"])
        self.decisions.set_model("1", title="Title", creator="maker")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual([], a["flags"])
        self.assertTrue(a["selected"])

    def test_base_code_from_the_table_replaces_a_new_code(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxlx)", flags=["new-base-code"],
                 parts={"creator": "maker", "title": "title", "version": "v1", "precision": "", "base": "sdxlx"})
        self.base_codes.set("SDXL 1.0", "sdxl")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual("maker - title - v1 (sdxl)", a["newStem"])
        self.assertEqual([], a["flags"])

    def test_per_file_version_decision_wins(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)", file_key="file:7")
        self.decisions.set_version("10", "v9")
        self.decisions.set_file("file:7", version="v1 identity")
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual("maker - title - v1 identity (sdxl)", a["newStem"])

    def test_keep_and_unkeep(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)", file_key="file:7")
        self.decisions.set_file("file:7", keep=True)
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(("kept", "kept-by-you"), (a["status"], a["reason"]))
        self.assertEqual("old a", a["newStem"])
        self.assertFalse(a["selected"])
        self.decisions.set_file("file:7", keep=False)
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(("change", ""), (a["status"], a["reason"]))
        self.assertEqual("maker - title - v1 (sdxl)", a["newStem"])
        self.assertTrue(a["selected"])

    def test_unkeep_of_a_row_kept_before_the_run(self) -> None:
        a = _row(self.folder, "old a", "old a", status="kept", reason="kept-by-you", selected=False,
                 parts={"creator": "old a", "title": "", "version": "", "precision": "", "base": ""})
        reassemble_rows([a], self.decisions, self.base_codes, {}, model_id="1")
        self.assertEqual(("kept", "dry-run-again"), (a["status"], a["reason"]))
        self.assertFalse(a["selected"])

    def test_other_statuses_are_left_alone(self) -> None:
        dup = _row(self.folder, "old d", "old d", status="duplicate", reason="duplicate", selected=False)
        before = dict(dup)
        self.decisions.set_model("1", title="Other")
        self.assertEqual([dup], reassemble_rows([dup], self.decisions, self.base_codes, {}, model_id="1"))
        self.assertEqual(before, dup)

    # --- after apply ----------------------------------------------------

    def test_apply_followups(self) -> None:
        old_info = self.folder / "old a.civitai.info"
        new_info = self.folder / "maker - title - v1 (sdxl).civitai.info"
        new_info.write_text(json.dumps({"id": 10, "model": {"name": "Old"}}), encoding="utf-8")
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)", flags=["new-base-code"])
        b = _row(self.folder, "old b", "maker - title - v1 b (sdxl)", version_id="11", file_key="file:8",
                 parts={"creator": "maker", "title": "title", "version": "v1 b", "precision": "", "base": "sdxl"})
        c = _row(self.folder, "old c", "maker - title - v1 c (sdxl)", version_id="11", file_key="file:9",
                 parts={"creator": "maker", "title": "title", "version": "v1 c", "precision": "", "base": "sdxl"})
        skipped = _row(self.folder, "old s", "x - y (sdxl)", model_id="3", version_id="30")
        dup = _row(self.folder, "old d", "old d", status="duplicate", survivorKey=a["key"])
        last_check = {"items": [{"modelPath": a["modelPath"]}, {"modelPath": str(self.folder / "old d.safetensors")}]}
        (self.data_dir / "last_check.json").write_text(json.dumps(last_check), encoding="utf-8")
        steps = [
            {"kind": "trash", "from": str(self.folder / "old d.safetensors"), "to": a["modelPath"], "entry": dup["key"]},
            {"kind": "rename", "from": a["modelPath"], "to": str(self.folder / (a["newStem"] + ".safetensors")), "entry": a["key"]},
            {"kind": "rename", "from": str(old_info), "to": str(new_info), "entry": a["key"]},
            {"kind": "rename", "from": b["modelPath"], "to": str(self.folder / (b["newStem"] + ".safetensors")), "entry": b["key"]},
            {"kind": "rename", "from": skipped["modelPath"], "to": "elsewhere", "entry": skipped["key"]},
        ]
        result = {"done": [dup["key"], a["key"], b["key"]], "skipped": [{"entry": skipped["key"], "path": "p", "reason": "r"}]}
        summary = apply_followups(
            [a, b, c, skipped, dup], steps, result, data_dir=self.data_dir, decisions=self.decisions, base_codes=self.base_codes
        )
        self.assertEqual({"renamed": 2, "removed": 1, "skipped": result["skipped"], "warnings": []}, summary)
        saved = json.loads((self.data_dir / "last_check.json").read_text(encoding="utf-8"))
        # The trash step's "to" (the survivor) is not a rename.
        self.assertEqual(
            [str(self.folder / (a["newStem"] + ".safetensors")), str(self.folder / "old d.safetensors")],
            [item["modelPath"] for item in saved["items"]],
        )
        self.assertEqual("Title", json.loads(new_info.read_text(encoding="utf-8"))["model"]["name"])
        self.assertEqual({"title": "title", "creator": "maker", "locked": True}, self.decisions.model("1"))
        self.assertEqual("v1", self.decisions.version("10"))
        # Version 11 has two files with different versions: remembered per file.
        self.assertIsNone(self.decisions.version("11"))
        self.assertEqual("v1 b", self.decisions.file("file:8").get("version"))
        self.assertEqual({}, self.decisions.file("file:9"))
        self.assertEqual({}, self.decisions.model("3"))
        self.assertEqual("sdxl", self.base_codes.code("SDXL 1.0"))

    def test_apply_followups_skip_title_refresh_without_live_title(self) -> None:
        new_info = self.folder / "maker - title - v1 (sdxl).civitai.info"
        new_info.write_text(json.dumps({"model": {"name": "Old"}}), encoding="utf-8")
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        del a["liveTitle"]
        steps = [{"kind": "rename", "from": str(self.folder / "old a.civitai.info"), "to": str(new_info), "entry": a["key"]}]
        apply_followups([a], steps, {"done": [a["key"]], "skipped": []}, data_dir=self.data_dir,
                        decisions=self.decisions, base_codes=self.base_codes)
        self.assertEqual("Old", json.loads(new_info.read_text(encoding="utf-8"))["model"]["name"])

    # --- final-review fixes ----------------------------------------------

    def _manager_with(self, *records) -> JobManager:
        manager = JobManager()
        for record in records:
            manager._jobs[record.id] = record
        return manager

    def test_every_dry_run_goes_stale_on_apply_or_recover(self) -> None:
        old = JobRecord(id="old", type=RENAME_DRY_RUN_JOB, status="completed")
        newer = JobRecord(id="newer", type=RENAME_DRY_RUN_JOB, status="completed")
        check = JobRecord(id="check", type="check-updates", status="completed")
        applied: set = set()
        mark_dry_runs_stale(self._manager_with(old, newer, check), applied)
        self.assertEqual({"old", "newer"}, applied)
        for job in (old, newer):
            self.assertEqual((409, STALE_DRY_RUN_ERROR), rename_job_problem(job, active_job=None, applied=applied))

    # --- the last dry run across restarts --------------------------------

    def _finished_dry_run(self, rows, job_id="run-1"):
        return {
            "jobId": job_id, "type": RENAME_DRY_RUN_JOB, "status": "completed",
            "startedAt": "2026-11-14T09:00:00+00:00", "finishedAt": "2026-11-14T10:00:00+00:00",
            "progress": len(rows), "total": len(rows), "summary": {"change": len(rows)}, "items": rows,
        }

    def test_restore_registers_the_saved_dry_run_under_its_id(self) -> None:
        rows = [_row(self.folder, "old a", "maker - title - v1 (sdxl)")]
        save_finished_dry_run(DryRunStore(self.data_dir))(self._finished_dry_run(rows))

        manager, applied = JobManager(), set()
        job = restore_last_dry_run(manager, DryRunStore(self.data_dir), applied)
        self.assertIs(job, manager.get("run-1"))
        self.assertEqual(RENAME_DRY_RUN_JOB, job.type)
        self.assertEqual("completed", job.status)
        self.assertEqual(rows, job.items)
        self.assertEqual(set(), applied)
        self.assertIsNone(rename_job_problem(job, active_job=None, applied=applied))
        payload = rename_rows_payload(job, applied)
        self.assertEqual("2026-11-14T10:00:00+00:00", payload["restoredFrom"])
        self.assertFalse(payload["applied"])
        self.assertEqual(rows, payload["rows"])
        self.assertEqual({"change": 1}, payload["summary"])

    def test_nothing_to_restore(self) -> None:
        manager = JobManager()
        self.assertIsNone(restore_last_dry_run(manager, DryRunStore(self.data_dir), set()))
        self.assertEqual([], manager.job_ids(RENAME_DRY_RUN_JOB))

    def test_a_dry_run_of_this_session_is_not_marked_restored(self) -> None:
        job = JobRecord(id="live", type=RENAME_DRY_RUN_JOB, status="completed", items=[{"key": "a"}])
        payload = rename_rows_payload(job, set())
        self.assertIsNone(payload["restoredFrom"])
        self.assertEqual("completed", payload["status"])

    def test_only_a_completed_dry_run_is_saved(self) -> None:
        store = DryRunStore(self.data_dir)
        hook = save_finished_dry_run(store)
        hook(dict(self._finished_dry_run([{"key": "a"}]), status="cancelled"))
        self.assertIsNone(store.load())
        hook(self._finished_dry_run([{"key": "a"}]))
        self.assertEqual("run-1", store.job_id())

    def test_a_restored_applied_dry_run_stays_read_only(self) -> None:
        store = DryRunStore(self.data_dir)
        save_finished_dry_run(store)(self._finished_dry_run([{"key": "a"}]))
        manager, applied = JobManager(), set()
        restore_last_dry_run(manager, store, applied)
        mark_dry_runs_stale(manager, applied)
        self.assertIsNone(store.mark_applied())  # saved: no warning for the panel

        manager, applied = JobManager(), set()  # restart
        job = restore_last_dry_run(manager, DryRunStore(self.data_dir), applied)
        self.assertEqual({"run-1"}, applied)
        self.assertTrue(rename_rows_payload(job, applied)["applied"])
        self.assertEqual((409, STALE_DRY_RUN_ERROR), rename_job_problem(job, active_job=None, applied=applied))

    def test_an_edit_survives_a_restart(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        save_finished_dry_run(DryRunStore(self.data_dir))(self._finished_dry_run([a]))

        store = DryRunStore(self.data_dir)  # first restart
        manager = JobManager()
        job = restore_last_dry_run(manager, store, set())
        self.decisions.set_model("1", title="Better")
        reassemble_rows(job.items, self.decisions, self.base_codes, {str(self.folder).lower(): {"old a"}}, model_id="1")
        write = dry_run_rows_writer(store, job.id, job.items)
        # The writer holds a copy: rows edited after it was made are not what it writes.
        job.items[0]["newStem"] = "edited later"
        write()

        manager = JobManager()  # second restart
        job = restore_last_dry_run(manager, DryRunStore(self.data_dir), set())
        self.assertEqual("maker - better - v1 (sdxl)", job.items[0]["newStem"])
        self.assertEqual("better", job.items[0]["parts"]["title"])

    def test_edits_of_an_older_dry_run_do_not_replace_the_saved_one(self) -> None:
        store = DryRunStore(self.data_dir)
        save_finished_dry_run(store)(self._finished_dry_run([{"key": "new"}], job_id="run-2"))
        dry_run_rows_writer(store, "run-1", [{"key": "old"}])()
        self.assertEqual([{"key": "new"}], DryRunStore(self.data_dir).load()["rows"])

    def test_check_jobs_in_memory_get_the_new_paths(self) -> None:
        old, new = str(self.folder / "Old.safetensors"), str(self.folder / "new.safetensors")
        cached = JobRecord(id="cached", type="check-updates", status="completed",
                           items=[{"modelPath": old.lower(), "localVersions": [{"filePath": old}], "modelName": old}])
        session = JobRecord(id="s", type="check-updates", status="completed", items=[{"modelPath": old}])
        dry = JobRecord(id="d", type=RENAME_DRY_RUN_JOB, status="completed", items=[{"modelPath": old}])
        manager = self._manager_with(cached, session, dry)
        update_check_job_paths(manager, {old: new})
        self.assertEqual(new, cached.items[0]["modelPath"])
        self.assertEqual(new, cached.items[0]["localVersions"][0]["filePath"])
        self.assertEqual(old, cached.items[0]["modelName"])
        self.assertEqual(new, session.items[0]["modelPath"])
        self.assertEqual(old, dry.items[0]["modelPath"])

    def test_apply_followups_update_the_check_jobs(self) -> None:
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        target = str(self.folder / (a["newStem"] + ".safetensors"))
        check = JobRecord(id="cached", type="check-updates", status="completed", items=[{"modelPath": a["modelPath"]}])
        steps = [{"kind": "rename", "from": a["modelPath"], "to": target, "entry": a["key"]}]
        apply_followups([a], steps, {"done": [a["key"]], "skipped": []}, data_dir=self.data_dir,
                        decisions=self.decisions, base_codes=self.base_codes, check_jobs=self._manager_with(check))
        self.assertEqual(target, check.items[0]["modelPath"])

    def test_run_apply_reports_planning_skips(self) -> None:
        (self.folder / "old a.safetensors").write_bytes(b"w")
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        gone = _row(self.folder, "gone", "maker - gone - v1 (sdxl)", model_id="2", version_id="20")
        summary = run_apply(
            [a, gone], {a["key"], gone["key"]}, set(), RenameJournal(self.data_dir), data_dir=self.data_dir,
            decisions=self.decisions, base_codes=self.base_codes, progress=lambda *args: None, control=None,
        )
        self.assertEqual(1, summary["renamed"])
        self.assertEqual([gone["key"]], [item["entry"] for item in summary["skipped"]])
        self.assertTrue((self.folder / (a["newStem"] + ".safetensors")).exists())

    def test_run_apply_writes_the_rename_log(self) -> None:
        (self.folder / "old a.safetensors").write_bytes(b"w")
        a = _row(self.folder, "old a", "maker - title - v1 (sdxl)")
        run_apply(
            [a], {a["key"]}, set(), RenameJournal(self.data_dir), data_dir=self.data_dir,
            decisions=self.decisions, base_codes=self.base_codes, progress=lambda *args: None, control=None,
            log=RenameLog(self.data_dir), dry_run_job_id="dry-7",
        )
        lines = [json.loads(line) for line in (self.data_dir / "rename_log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            [("rename", "dry-7", str(self.folder / "old a.safetensors"), str(self.folder / (a["newStem"] + ".safetensors")))],
            [(line["kind"], line["dryRunJobId"], line["from"], line["to"]) for line in lines],
        )

    def test_exclusive_slot_lets_one_recovery_run_at_a_time(self) -> None:
        import asyncio

        slot = ExclusiveSlot()
        order = []

        async def recovery(name, gate):
            if not slot.acquire():
                order.append(f"{name} refused")
                return
            try:
                order.append(f"{name} started")
                await gate.wait()
            finally:
                slot.release()

        async def main():
            gate = asyncio.Event()
            first = asyncio.ensure_future(recovery("first", gate))
            await asyncio.sleep(0)
            await recovery("second", gate)
            gate.set()
            await first
            # Free again once the first recovery ends.
            self.assertTrue(slot.acquire())
            slot.release()

        asyncio.run(main())
        self.assertEqual(["first started", "second refused"], order)

    def test_status_says_whether_duplicates_can_be_removed(self) -> None:
        journal = RenameJournal(self.data_dir)
        status = rename_status({"namingModel": ""}, True, [], journal)
        self.assertTrue(status["canRemoveDuplicates"])
        with mock.patch.dict("sys.modules", {"send2trash": None}):
            status = rename_status({"namingModel": ""}, True, [], journal)
        self.assertFalse(status["canRemoveDuplicates"])

    def test_recovered_path_mapping(self) -> None:
        pending = {"runId": "r", "steps": [
            {"kind": "trash", "from": "d", "to": "a", "entry": "d", "done": False},
            {"kind": "rename", "from": "a", "to": "a2", "entry": "a", "done": True},
            {"kind": "rename", "from": "b", "to": "b2", "entry": "b", "done": False},
        ]}
        self.assertEqual({"a": "a2"}, recovered_path_mapping(pending, {"done": ["a", "d"], "skipped": []}, "finish"))
        # "a" was done before the interruption, so recovery does not list it; it still moved.
        self.assertEqual({"a": "a2"}, recovered_path_mapping(pending, {"done": [], "skipped": []}, "finish"))
        self.assertEqual({}, recovered_path_mapping(pending, {"done": ["a"], "skipped": []}, "undo"))
        self.assertEqual({}, recovered_path_mapping(None, {"done": ["a"], "skipped": []}, "finish"))


class _Response:
    def __init__(self, chunks, status=200, length=None) -> None:
        self.chunks = chunks
        self.status_code = status
        self.ok = status < 400
        self.reason = "Not Found" if status == 404 else "OK"
        total = sum(len(c) for c in chunks) if length is None else length
        self.headers = {"Content-Length": str(total)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=None):
        yield from self.chunks


class _Control:
    def __init__(self, cancel_after=None) -> None:
        self.cancel_after = cancel_after
        self.checks = 0

    def wait_if_paused(self) -> None:
        pass

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.cancel_after is not None and self.checks > self.cancel_after


class NamingModelDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "text_encoders"
        self.progress = []
        self.requests = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _get(self, response):
        def get(url, **kwargs):
            self.requests.append((url, kwargs))
            return response

        return get

    def test_downloads_through_a_part_file(self) -> None:
        name = download_naming_model(
            self.folder,
            http_get=self._get(_Response([b"ab", b"cd"])),
            progress=lambda *args: self.progress.append(args),
            control=_Control(),
        )
        self.assertEqual(NAMING_MODEL_FILENAME, name)
        self.assertEqual(b"abcd", (self.folder / NAMING_MODEL_FILENAME).read_bytes())
        self.assertFalse((self.folder / (NAMING_MODEL_FILENAME + ".part")).exists())
        self.assertEqual(NAMING_MODEL_URL, self.requests[0][0])
        self.assertTrue(self.requests[0][1]["stream"])
        self.assertTrue(self.progress)

    def test_refuses_an_existing_file(self) -> None:
        self.folder.mkdir()
        (self.folder / NAMING_MODEL_FILENAME).write_bytes(b"mine")
        with self.assertRaises(FileExistsError):
            download_naming_model(self.folder, http_get=self._get(_Response([b"x"])), progress=lambda *a: None,
                                  control=_Control())
        self.assertEqual([], self.requests)
        self.assertEqual(b"mine", (self.folder / NAMING_MODEL_FILENAME).read_bytes())

    def test_short_download_is_discarded(self) -> None:
        with self.assertRaises(OSError):
            download_naming_model(self.folder, http_get=self._get(_Response([b"ab"], length=10)),
                                  progress=lambda *a: None, control=_Control())
        self.assertEqual([], list(self.folder.iterdir()))

    def test_http_error(self) -> None:
        with self.assertRaises(OSError):
            download_naming_model(self.folder, http_get=self._get(_Response([], status=404)),
                                  progress=lambda *a: None, control=_Control())
        self.assertEqual([], list(self.folder.iterdir()))

    def test_download_never_pauses(self) -> None:
        class NoPause(_Control):
            def wait_if_paused(self) -> None:
                raise AssertionError("a paused stream times out and loses the download")

        name = download_naming_model(self.folder, http_get=self._get(_Response([b"a", b"b"])),
                                     progress=lambda *a: None, control=NoPause())
        self.assertEqual(NAMING_MODEL_FILENAME, name)

    def test_stop_removes_the_part_file(self) -> None:
        name = download_naming_model(self.folder, http_get=self._get(_Response([b"a", b"b", b"c"])),
                                     progress=lambda *a: None, control=_Control(cancel_after=1))
        self.assertEqual("", name)
        self.assertEqual([], list(self.folder.iterdir()))


if __name__ == "__main__":
    unittest.main()


class _RevealRequest:
    def __init__(self, payload, remote="127.0.0.1"):
        self._payload = payload
        self.remote = remote

    async def json(self):
        return self._payload


class RevealRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name) / "loras"
        self.folder.mkdir()
        self.keep = self.folder / "keep.civitai.info"
        self.copy = self.folder / "copy.civitai.info"
        for path in (self.keep, self.copy):
            path.write_text("{}", encoding="utf-8")
        survivor = {"key": str(self.keep).lower(), "modelPath": str(self.keep), "status": "unchanged"}
        dup = {
            "key": str(self.copy).lower(), "modelPath": str(self.copy), "status": "duplicate",
            "survivorKey": str(self.keep).lower(), "survivorPath": str(self.keep),
        }
        self.job = JobRecord(id="dry", type=RENAME_DRY_RUN_JOB, status="completed", items=[survivor, dup])
        self.launched: list = []

    def reveal(self, payload, remote="127.0.0.1", platform="win32"):
        import asyncio

        get_job = lambda job_id: self.job if job_id == "dry" else None  # noqa: E731
        response = asyncio.run(
            reveal_response(_RevealRequest(payload, remote), get_job, launch=self.launched.append, platform=platform)
        )
        return response.status, json.loads(response.text)

    def test_reveals_a_row_of_the_job(self) -> None:
        status, body = self.reveal({"jobId": "dry", "key": str(self.copy).lower()})
        self.assertEqual(200, status, body)
        self.assertEqual([["explorer", "/select,", str(self.copy)]], self.launched)

    def test_reveals_the_survivor_of_a_duplicate_row(self) -> None:
        status, _body = self.reveal({"jobId": "dry", "key": str(self.copy).lower(), "survivor": True})
        self.assertEqual(200, status)
        self.assertEqual([["explorer", "/select,", str(self.keep)]], self.launched)

    def test_a_path_in_the_request_is_never_used(self) -> None:
        elsewhere = Path(self._tmp.name) / "secret.txt"
        elsewhere.write_text("x", encoding="utf-8")
        status, _body = self.reveal({"jobId": "dry", "key": str(elsewhere).lower(), "path": str(elsewhere)})
        self.assertEqual(400, status)
        status, _body = self.reveal({"jobId": "dry", "key": str(self.keep).lower(), "survivor": True})
        self.assertEqual(400, status)  # not a duplicate row: it has no survivor
        self.assertEqual([], self.launched)

    def test_reveals_a_listed_path(self) -> None:
        status, body = self.reveal({"jobId": "dry", "path": str(self.keep)})
        self.assertEqual(200, status, body)
        self.assertEqual([["explorer", "/select,", str(self.keep)]], self.launched)

    def test_reveals_a_checked_file_of_an_update_check(self) -> None:
        import asyncio

        model = self.folder / "model.safetensors"
        model.write_text("x", encoding="utf-8")
        check = JobRecord(id="check", type="check-updates", status="completed", items=[{"modelPath": str(model)}])
        response = asyncio.run(
            reveal_response(
                _RevealRequest({"jobId": "check", "path": str(model)}),
                lambda job_id: check if job_id == "check" else None, launch=self.launched.append, platform="win32",
            )
        )
        self.assertEqual(200, response.status)
        self.assertEqual([["explorer", "/select,", str(model)]], self.launched)

    def test_unknown_job_and_missing_fields(self) -> None:
        self.assertEqual(404, self.reveal({"jobId": "other", "key": str(self.copy).lower()})[0])
        self.assertEqual(400, self.reveal({"jobId": "dry"})[0])
        self.assertEqual([], self.launched)

    def test_only_from_the_same_machine(self) -> None:
        for remote in ("::1", "::ffff:127.0.0.1", "127.0.0.2"):
            self.assertEqual(200, self.reveal({"jobId": "dry", "key": str(self.copy).lower()}, remote=remote)[0], remote)
        for remote in ("192.168.1.20", None, "not an address"):
            status, body = self.reveal({"jobId": "dry", "key": str(self.copy).lower()}, remote=remote)
            self.assertEqual(403, status)
            self.assertEqual({"error": "Show in folder works only on the computer ComfyUI runs on."}, body)
        self.assertEqual(3, len(self.launched))

    def test_missing_file(self) -> None:
        self.copy.unlink()
        status, body = self.reveal({"jobId": "dry", "key": str(self.copy).lower()})
        self.assertEqual(404, status)
        self.assertIn("copy.civitai.info", body["error"])
        self.assertEqual([], self.launched)

    def test_commands_per_platform(self) -> None:
        path = self.folder / "a b.safetensors"
        self.assertEqual(["explorer", "/select,", str(path)], reveal_command(path, "win32"))
        self.assertEqual(["open", "-R", str(path)], reveal_command(path, "darwin"))
        self.assertEqual(["xdg-open", str(self.folder)], reveal_command(path, "linux"))

    def test_launch_failure_is_reported(self) -> None:
        import asyncio

        def fail(command):
            raise OSError("no explorer")

        response = asyncio.run(
            reveal_response(
                _RevealRequest({"jobId": "dry", "key": str(self.copy).lower()}),
                lambda job_id: self.job, launch=fail, platform="win32",
            )
        )
        self.assertEqual(500, response.status)
        self.assertIn("no explorer", json.loads(response.text)["error"])
