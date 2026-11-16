import unittest
from pathlib import Path

from comfy_updater.naming_ai import Slot
from comfy_updater.naming_check import name_check_flags, type_words
from comfy_updater.renamer import _telling_hint


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


if __name__ == "__main__":
    unittest.main()
