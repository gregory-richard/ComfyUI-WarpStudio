from __future__ import annotations

import unittest

from comfy_updater.naming_check import (
    base_vocabulary,
    base_words,
    invented_words,
    is_official_title,
    maker_in_description,
    name_check_flags,
    version_format,
    xl_is_base,
)

# Base-model names and codes as the base-code table and Civitai give them.
NAMES = [
    "Pony", "pony", "Illustrious", "ill", "Krea 2", "krea2", "ZImageTurbo", "zit", "ZImageBase", "zib",
    "Flux.2 Klein 9B", "flux2k9b", "Anima", "anima", "SDXL 1.0", "sdxl", "Wan Video 2.2 T2V-A14B", "wan2.2 14b t2v",
    "MiniMax H3", "mmh3", "Other", "other",
]

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
        # The owner part of a model link, as credit_lines lists it.
        self.assertIs(True, maker_in_description("quillworks lab", "Links: huggingface.co/quillworks-lab"))



class BaseWordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab = base_vocabulary(NAMES)

    def words(self, text: str, xl: bool = False):
        return base_words(text, self.vocab, xl_base=xl)

    def test_base_names_and_codes_are_flagged(self) -> None:
        self.assertEqual(["anima"], self.words("v1.0 anima"))
        self.assertEqual(["klein"], self.words("v2 klein"))
        self.assertEqual(["zib"], self.words("v2 zib exp"))
        self.assertEqual(["pony"], self.words("v2.6 pony"))
        self.assertEqual(["zit", "flux"], self.words("zit flux sitting in car"))
        self.assertEqual(["wan2.2"], self.words("wan2.2 motion"))

    def test_abbreviation_of_a_base_name(self) -> None:
        self.assertEqual(["illust"], self.words("v1.0 illust"))

    def test_glued_forms(self) -> None:
        self.assertEqual(["ponyxl"], self.words("ponyxl style"))
        self.assertEqual(["ponyv6"], self.words("ponyv6 style"))
        self.assertEqual(["flux1"], self.words("flux1 style"))

    def test_xl_only_on_an_sdxl_family_base(self) -> None:
        self.assertEqual(["xl"], self.words("all disney princess xl", xl=True))
        self.assertEqual([], self.words("all disney princess xl", xl=False))
        self.assertTrue(xl_is_base("Pony"))
        self.assertTrue(xl_is_base("SDXL 1.0"))
        self.assertTrue(xl_is_base("Illustrious"))
        self.assertTrue(xl_is_base("NoobAI"))
        self.assertFalse(xl_is_base("Krea 2"))
        self.assertFalse(xl_is_base(""))

    def test_ordinary_words_pass(self) -> None:
        for text in ("cleavagexl", "ponytail", "turbo", "animal", "animation", "illustration", "mini skirt",
                     "image base model video", "juno max", "deep penetration", "v1.5", "other"):
            with self.subTest(text=text):
                self.assertEqual([], self.words(text, xl=True))

    def test_hyphenated_pieces_are_checked(self) -> None:
        self.assertEqual(["pony"], self.words("realism-pony"))
        # A hyphenated base name is reported whole.
        self.assertEqual(["z-image"], self.words("z-image realism"))

    def test_official_title(self) -> None:
        for title in ("flux.1 dev", "z-image turbo", "krea 2 turbo", "pony", "minimax h3", "flux.2-klein-9b-fp8"):
            with self.subTest(title=title):
                self.assertTrue(is_official_title(title, self.vocab))
        for title in ("deep penetration", "all disney princess xl", "turbo", "zit flux sitting in car"):
            with self.subTest(title=title):
                self.assertFalse(is_official_title(title, self.vocab, xl_base=True))

    def test_version_format(self) -> None:
        self.assertEqual(["v1.0"], version_format("v1.0 anima"))
        self.assertEqual(["v2.0"], version_format("v2.0 exp"))
        self.assertEqual(["10.0"], version_format("10.0"))
        self.assertEqual([], version_format("v1.5"))
        self.assertEqual([], version_format("v1.0.1 v2"))

    def test_name_check_flags(self) -> None:
        self.assertEqual(
            ["base-word:anima", "version-format:v1.0"],
            name_check_flags("deep penetration", "v1.0 anima", self.vocab),
        )
        self.assertEqual(
            ["base-word:xl", "base-word:pony"],
            name_check_flags("all disney princess xl", "v2 pony", self.vocab, xl_base=True),
        )
        # An official model keeps the base name as its title; its version is still checked.
        self.assertEqual(["base-word:klein"], name_check_flags("flux.2 klein", "v1 klein", self.vocab))
        self.assertEqual([], name_check_flags("flux.1 dev", "", self.vocab))
        self.assertEqual([], name_check_flags("deep penetration", "v1", self.vocab))


if __name__ == "__main__":
    unittest.main()
