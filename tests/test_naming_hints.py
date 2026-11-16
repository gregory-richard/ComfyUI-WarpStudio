import unittest
from pathlib import Path

from comfy_updater.naming_ai import Slot
from comfy_updater.naming_check import base_numbers, base_vocabulary, glued_names, name_check_flags, type_words
from comfy_updater.renamer import _names_from_files, _telling_hint


class _Pending:
    def __init__(self, source: str) -> None:
        self.source = source


def slot(key: str, name: str) -> Slot:
    return Slot(key=key, version_id=key, file_id=key, version_name=name, base_model="Wan Video 2.2 T2V-A14B")


class TypeWordTests(unittest.TestCase):
    def test_lora_on_its_own_is_flagged(self) -> None:
        self.assertEqual(["lora"], type_words("realism lora v1"))
        self.assertIn("type-word:lora", name_check_flags("realism lora", "v1", frozenset()))

    def test_lora_inside_a_word_is_not(self) -> None:
        self.assertEqual([], type_words("floral explorer"))


class TellingHintTests(unittest.TestCase):
    def test_the_differing_words_of_the_version_names(self) -> None:
        pending = _Pending("source")
        hint = _telling_hint(pending, (slot("1", "2.2-Speed-T2V-0928-H"), ""), (slot("2", "2.2 Speed T2V H"), ""), frozenset())
        self.assertEqual(': "0928" only in "2.2-Speed-T2V-0928-H".', hint)
        self.assertEqual("source", pending.source)

    def test_same_version_names_point_to_the_file_names(self) -> None:
        pending = _Pending("source")
        hint = _telling_hint(
            pending,
            (slot("1", "Speed H"), "speed_v1.1_high_fp16.safetensors"),
            (slot("2", "Speed H"), "speed_0928_high.safetensors"),
            frozenset(),
        )
        self.assertIn("Their files are", hint)
        # Words taken from the file names are then not "invented".
        self.assertIn("speed_0928_high.safetensors", pending.source)

    def test_nothing_to_tell_them_apart(self) -> None:
        pending = _Pending("source")
        self.assertEqual(".", _telling_hint(pending, (slot("1", "H"), "a.safetensors"), (slot("2", "H"), "a.safetensors"), frozenset()))


class GluedNameTests(unittest.TestCase):
    VOCAB = base_vocabulary(["Krea 2", "krea2", "Pony", "pony"])

    def test_a_base_glued_into_the_models_own_name_must_stay(self) -> None:
        self.assertEqual(["KreaCore"], glued_names("KreaCore", "kore", self.VOCAB))
        self.assertEqual(["PonyRealism"], glued_names("PonyRealism", "realism", self.VOCAB))

    def test_kept_whole_or_separate_words_pass(self) -> None:
        self.assertEqual([], glued_names("KreaCore", "kreacore", self.VOCAB))
        self.assertEqual([], glued_names("Pony Realism", "realism", self.VOCAB))  # a base tag in its own word
        self.assertEqual([], glued_names("Krea2 Glow", "glow", self.VOCAB))


class BaseNumberTests(unittest.TestCase):
    def test_the_base_number_as_a_version_is_flagged(self) -> None:
        self.assertEqual(["v2.2"], base_numbers("v2.2", "Wan Video 2.2 T2V-A14B"))
        self.assertIn("base-number:v2.2", name_check_flags("speed", "v2.2", frozenset(), base_model="Wan Video 2.2 T2V-A14B"))

    def test_real_versions_pass(self) -> None:
        self.assertEqual([], base_numbers("v1.0", "SDXL 1.0"))  # x.0 tells nothing
        self.assertEqual([], base_numbers("v2", "Krea 2"))  # no dot: usually a real second version
        self.assertEqual([], base_numbers("v1.5", "Wan Video 2.2 T2V-A14B"))


def clash_row(key: str, old: str, version: str = "v2.2") -> dict:
    return {
        "key": key, "versionId": key, "oldStem": old, "status": "change",
        "newStem": f"maker - speed - {version} (wan)", "flags": ["version-clash"],
        "parts": {"creator": "maker", "title": "speed", "version": version, "precision": "", "base": "wan"},
    }


class NamesFromFilesTests(unittest.TestCase):
    def test_clashing_versions_keep_the_users_names_in_the_scheme(self) -> None:
        rows = [clash_row("1", "maker - speed - 4steps - 0928 - high (wan)"), clash_row("2", "maker - speed - 4steps - low (wan)")]
        rows[0]["flags"] += ["maker-from-description", "base-number:v2.2", "new-base-code"]
        _names_from_files(rows)
        self.assertEqual(["new-base-code"], rows[0]["flags"])
        rows[0]["flags"] = []
        self.assertEqual("maker - speed - 4steps 0928 high (wan)", rows[0]["newStem"])
        self.assertEqual("maker - speed - 4steps low (wan)", rows[1]["newStem"])
        self.assertEqual([], rows[0]["flags"])
        self.assertEqual("file", rows[0]["nameSource"])

    def test_names_that_would_still_clash_stay_flagged(self) -> None:
        rows = [clash_row("1", "maker - speed - high (wan)"), clash_row("2", "maker - speed - high (wan)")]
        _names_from_files(rows)
        self.assertEqual(["version-clash"], rows[0]["flags"])
        self.assertNotIn("nameSource", rows[0])

    def test_a_name_without_creator_and_title_stays_flagged(self) -> None:
        rows = [clash_row("1", "speed_high"), clash_row("2", "maker - speed - low (wan)")]
        _names_from_files(rows)
        self.assertEqual(["version-clash"], rows[1]["flags"])


if __name__ == "__main__":
    unittest.main()
