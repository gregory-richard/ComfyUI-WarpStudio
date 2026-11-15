from __future__ import annotations

import unittest

from comfy_updater.naming_check import invented_words, maker_in_description

OK = [
    ("Some Model version1.0", ["some model", "v1"]),
    ("Some Model Krea-2_v1", ["some model", "v1"]),
    ("Misty Glen Mix (ZIB/ZID) V4.0 Distilled 12STEPS", ["misty glen mix", "v4 distilled 12steps"]),
    ("Zo-mix - Style LORA Zo-mix α (Anima)", ["zo-mix", "alpha"]),
    ("Krea2 - CrispRealism v4.0", ["crisp realism", "v4"]),
    ("Z-Image Turbo Nightfall 11.0 (BF16 + FP8)", ["nightfall", "v11"]),
    ("Cargo shorts Hadley's 602 v0.3", ["cargo shorts hadleys 602", "v0.3"]),
    ("H3 Juno Max beta6", ["juno max", "beta6"]),
]


class NamingCheckTests(unittest.TestCase):
    def test_allowed_rewrites_are_not_flagged(self) -> None:
        for source, parts in OK:
            with self.subTest(source=source):
                self.assertEqual([], invented_words(source, parts))

    def test_invented_version_is_flagged(self) -> None:
        source = "BlueForge | 蓝炉 | Jul 19 '26 | Current - ZiB[SWIFT]⚡️Distilled ZIB Distilled⚡️DPO Nova"
        self.assertEqual(["v1"], invented_words(source, ["blueforge", "v1"]))

    def test_number_glued_to_letters_is_not_a_version(self) -> None:
        source = "QSD Speed LoRA [SDXL, Pony, Illustrious] qsd_sdxl_4step_lora_fp16"
        self.assertEqual(["v4"], invented_words(source, ["qsd speed", "v4"]))

    def test_number_glued_to_letters_in_front_is_not_a_version(self) -> None:
        self.assertEqual(["v2"], invented_words("Krea2 - CrispRealism", ["crisp realism", "v2"]))
        source = "QSD Speed LoRA [SDXL, Pony, Illustrious] qsd_sdxl_4step_lora_fp16"
        self.assertEqual(["v2"], invented_words(source, ["qsd speed", "v2"]))

    def test_v_prefix_glued_to_a_word_still_counts(self) -> None:
        self.assertEqual([], invented_words("SDXLv2 Model", ["model", "v2"]))

    def test_dot_after_version_prefix_still_counts(self) -> None:
        self.assertEqual([], invented_words("Squirting Krea 2 v.3", ["squirting", "v3"]))
        self.assertEqual([], invented_words("Model ver. 2", ["model", "v2"]))
        self.assertEqual([], invented_words("Model Version.1.0", ["model", "v1"]))

    def test_longer_number_does_not_shorten_into_a_version(self) -> None:
        self.assertEqual(["v1"], invented_words("SDXL v1.5b", ["sdxl", "v1"]))

    def test_non_latin_word_is_flagged(self) -> None:
        self.assertEqual(["红潮"], invented_words("Some Model", ["some model", "红潮"]))

    def test_ordinary_invented_word_is_flagged(self) -> None:
        self.assertEqual(["turbo"], invented_words("Some Model", ["some model", "turbo"]))

    def test_words_from_file_names_count(self) -> None:
        source = "Color Grade v2 krea2_night_grade_v2.safetensors"
        self.assertEqual([], invented_words(source, ["color grade", "v2 night"]))

    def test_maker_in_description(self) -> None:
        self.assertIs(True, maker_in_description("fernwick", "Original model at Huggingface fernwick/QSD ."))
        self.assertIs(False, maker_in_description("bfl", "by Black Forest Labs"))
        self.assertIs(True, maker_in_description("black forest labs", "by Black Forest Labs"))


if __name__ == "__main__":
    unittest.main()
