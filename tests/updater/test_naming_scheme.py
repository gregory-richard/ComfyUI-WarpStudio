from __future__ import annotations

import unittest
from pathlib import Path

from comfy_updater.naming_scheme import (
    NameParts,
    assemble,
    format_part,
    name_tail,
    own_name,
    parse_stem,
    part_problems,
    stem_problems,
    target_path,
)


class NamingSchemeTests(unittest.TestCase):
    def test_assemble_with_version(self) -> None:
        self.assertEqual(
            "tidemint - lumegrid realism - zest preview fix (krea2)",
            assemble(NameParts("tidemint", "lumegrid realism", "zest preview fix", "", "krea2")),
        )

    def test_assemble_with_version_and_precision(self) -> None:
        self.assertEqual(
            "calm_ridge - aura - v4 expanded int8 (krea2)",
            assemble(NameParts("calm_ridge", "aura", "v4 expanded", "int8", "krea2")),
        )

    def test_assemble_without_version_or_precision(self) -> None:
        self.assertEqual(
            "silkthread - nora wrap midi dress (ill)",
            assemble(NameParts("silkthread", "nora wrap midi dress", "", "", "ill")),
        )

    def test_assemble_with_precision_only(self) -> None:
        self.assertEqual(
            "dlrowolleh - solstice - int8 (krea2)",
            assemble(NameParts("dlrowolleh", "solstice", "", "int8", "krea2")),
        )

    def test_format_part_lowercases_trims_and_collapses_spaces(self) -> None:
        self.assertEqual("zest preview", format_part("  Zest   Preview "))

    def test_format_part_keeps_hyphens(self) -> None:
        self.assertEqual("zo-mix", format_part("zo-mix"))

    def test_target_path_keeps_dots_in_stem(self) -> None:
        stem = "black forest labs - flux.1 dev - fp8 (flux1d)"
        self.assertEqual(
            "black forest labs - flux.1 dev - fp8 (flux1d).safetensors",
            target_path(Path("x"), stem, ".safetensors").name,
        )
        self.assertEqual(
            "black forest labs - flux.1 dev - fp8 (flux1d).civitai.info",
            target_path(Path("x"), stem, ".civitai.info").name,
        )

    def test_parse_stem_with_version(self) -> None:
        self.assertEqual(
            NameParts("naughty_tokens", "sheer clothes", "v2.0", "", "flux1k9b"),
            parse_stem("naughty_tokens - sheer clothes - v2.0 (flux1k9b)"),
        )

    def test_parse_stem_without_version(self) -> None:
        self.assertEqual(
            NameParts("drawixa", "a1", "", "", "anima"),
            parse_stem("drawixa - a1 (anima)"),
        )

    def test_part_problems_separator(self) -> None:
        self.assertEqual(["separator:title"], part_problems(NameParts("a", "b - c", "", "", "x")))

    def test_part_problems_forbidden(self) -> None:
        self.assertEqual(["forbidden:title"], part_problems(NameParts("a", "what?", "", "", "x")))

    def test_part_problems_empty(self) -> None:
        self.assertEqual(["empty:title"], part_problems(NameParts("a", "", "", "", "x")))

    def test_stem_problems_reserved(self) -> None:
        self.assertEqual(["reserved"], stem_problems("nul"))

    def test_stem_problems_trailing(self) -> None:
        self.assertEqual(["trailing"], stem_problems("abc."))

    def test_stem_problems_clean(self) -> None:
        self.assertEqual([], stem_problems("a - b (x)"))

    def test_own_name_is_the_first_stem_with_a_creator_and_a_title(self) -> None:
        self.assertEqual(
            NameParts("moss", "glow", "xxl", "", "moss"),
            own_name(["glowV2", "moss - glow - xxl (moss)"]),
        )
        self.assertIsNone(own_name(["glowV2", "glow (ill)"]))

    def test_name_tail_is_the_version_and_base_part(self) -> None:
        self.assertEqual("xxl (moss)", name_tail("moss - glow - xxl (moss)"))
        self.assertEqual("(ill)", name_tail("moss - glow (ill)"))
        self.assertEqual("moss - glow", name_tail("moss - glow"))
        self.assertEqual("glowV2", name_tail("glowV2"))


if __name__ == "__main__":
    unittest.main()
