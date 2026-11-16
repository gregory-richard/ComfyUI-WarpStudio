import os
import tempfile
import unittest
from pathlib import Path

from comfy_updater.naming_check import base_conflict
from comfy_updater.rename_apply import plan
from comfy_updater.rename_folders import home_folders, plan_moves, recheck_folder_flags

ROOT = os.path.join("D:" + os.sep, "models", "loras")


def folder(name: str) -> str:
    return os.path.join(ROOT, name)


def row(name: str, where: str, base: str, *, status: str = "change", old_base: str = "", flags=None) -> dict:
    old = f"maker - {name} ({old_base or base})"
    new = f"maker - {name} ({base})"
    return {
        "key": os.path.join(folder(where), old + ".safetensors").lower(),
        "modelPath": os.path.join(folder(where), old + ".safetensors"),
        "folder": folder(where),
        "oldStem": old,
        "newStem": new if status == "change" else old,
        "parts": {"creator": "maker", "title": name, "version": "", "precision": "", "base": base},
        "changed": ["base"] if old != new else [],
        "flags": list(flags or []),
        "status": status if old != new or status != "change" else "unchanged",
        "selected": status == "change" and not flags,
    }


def library(*extra) -> list:
    rows = [row(f"pony {n}", "pony", "pony", status="unchanged") for n in range(4)]
    rows += [row(f"ill {n}", "ill", "ill", status="unchanged") for n in range(4)]
    return rows + list(extra)


class HomeFolderTests(unittest.TestCase):
    def test_each_base_has_the_folder_holding_most_of_its_files(self) -> None:
        homes = home_folders(library())
        parent = os.path.normcase(os.path.normpath(ROOT))
        self.assertEqual(folder("pony"), homes[(parent, "pony")])
        self.assertEqual(folder("ill"), homes[(parent, "ill")])

    def test_a_few_files_make_no_home(self) -> None:
        rows = library(row("a", "mixed", "sdxl", status="unchanged"), row("b", "mixed", "sdxl", status="unchanged"))
        self.assertNotIn("sdxl", {code for (_parent, code) in home_folders(rows)})


class PlanMovesTests(unittest.TestCase):
    def test_a_pony_file_in_the_ill_folder_moves_to_pony(self) -> None:
        stray = row("stray", "ill", "pony", old_base="ill")
        rows = library(stray)
        plan_moves(rows)
        self.assertEqual(folder("pony"), stray["newFolder"])
        self.assertIn("folder", stray["changed"])

    def test_a_file_that_only_moves_becomes_a_change(self) -> None:
        stray = row("stray", "pony", "ill", status="unchanged")
        rows = library(stray)
        plan_moves(rows)
        self.assertEqual("change", stray["status"])
        self.assertEqual(folder("ill"), stray["newFolder"])
        self.assertTrue(stray["selected"])

    def test_files_outside_base_folders_stay(self) -> None:
        own = row("training", "GR", "pony")
        rows = library(own)
        plan_moves(rows)
        self.assertNotIn("newFolder", own)

    def test_no_home_no_move(self) -> None:
        odd = row("odd", "ill", "chroma", old_base="ill")
        rows = library(odd)
        plan_moves(rows)
        self.assertNotIn("newFolder", odd)

    def test_a_doubtful_base_stays(self) -> None:
        doubtful = row("doubt", "ill", "pony", old_base="ill", flags=["base-conflict:krea2"])
        rows = library(doubtful)
        plan_moves(rows)
        self.assertNotIn("newFolder", doubtful)

    def test_planning_again_drops_an_old_move(self) -> None:
        stray = row("stray", "pony", "ill", status="unchanged")
        rows = library(stray)
        plan_moves(rows)
        stray["parts"]["base"] = "pony"
        plan_moves(rows)
        self.assertNotIn("newFolder", stray)
        self.assertEqual("unchanged", stray["status"])
        self.assertNotIn("folder", stray["changed"])


class FolderFlagTests(unittest.TestCase):
    def test_a_name_taken_in_the_target_folder_is_flagged(self) -> None:
        stray = row("stray", "ill", "pony", old_base="ill")
        rows = library(stray)
        plan_moves(rows)
        listing = {folder("pony").lower(): {"maker - stray (pony)"}, folder("ill").lower(): {"maker - stray (ill)"}}
        recheck_folder_flags(rows, {folder("pony").lower(), folder("ill").lower()}, listing)
        self.assertIn("exists", stray["flags"])
        self.assertFalse(stray["selected"])

    def test_two_files_moving_onto_one_name_collide(self) -> None:
        a = row("same", "ill", "pony", old_base="ill")
        b = dict(row("same", "ill", "pony", old_base="ill"), key="other", oldStem="maker - same copy (ill)")
        rows = library(a, b)
        plan_moves(rows)
        recheck_folder_flags(rows, {folder("pony").lower(), folder("ill").lower()}, {})
        self.assertIn("collision", a["flags"])
        self.assertIn("collision", b["flags"])


class BaseConflictTests(unittest.TestCase):
    CODES = {
        "Flux.1 D": "flux1d", "Krea 2": "krea2", "ZImageBase": "zib", "ZImageTurbo": "zit",
        "Wan Video 2.2 I2V-A14B": "wan2.2 14b i2v", "Wan Video 2.2 T2V-A14B": "wan2.2 14b t2v",
        "Pony": "pony", "Illustrious": "ill", "NoobAI": "pony",
    }

    def test_a_version_named_for_another_base(self) -> None:
        self.assertEqual("krea2", base_conflict("krea2 v1.0", "Flux.1 D", self.CODES))
        self.assertEqual("t2v", base_conflict("Wan2.2 T2V high", "Wan Video 2.2 I2V-A14B", self.CODES))
        self.assertEqual("zit", base_conflict("ZIT v1.0", "ZImageBase", self.CODES))

    def test_names_that_fit_their_base(self) -> None:
        for version, base in (
            ("ZIT and ZIB v2.0", "ZImageBase"),
            ("ZIT - V2", "ZImageTurbo"),
            ("Wan2.2 I2V Highnoise", "Wan Video 2.2 I2V-A14B"),
            ("v1.0", "Pony"),
            ("v1.2 FLUX", "Flux.1 D"),
            ("Illustrious2+NoobAI v.4", "Illustrious"),
            ("", "Pony"),
        ):
            self.assertEqual("", base_conflict(version, base, self.CODES), version)


class ApplyMoveTests(unittest.TestCase):
    def test_a_moving_file_and_its_sidecars_go_to_the_new_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ill, pony = Path(tmp) / "ill", Path(tmp) / "pony"
            ill.mkdir()
            pony.mkdir()
            stem = "maker - stray (pony)"
            for suffix in (".safetensors", ".civitai.info", ".preview.png"):
                (ill / (stem + suffix)).write_text("x", encoding="utf-8")
            own = ill / (stem + ".safetensors")
            moving = {
                "key": str(own).lower(), "modelPath": str(own), "folder": str(ill), "newFolder": str(pony),
                "oldStem": stem, "newStem": stem, "status": "change", "flags": [],
            }
            steps, skipped = plan([moving], {moving["key"]}, set())
            self.assertEqual([], skipped)
            self.assertEqual(
                sorted(str(pony / (stem + s)) for s in (".safetensors", ".civitai.info", ".preview.png")),
                sorted(step["to"] for step in steps),
            )


if __name__ == "__main__":
    unittest.main()
