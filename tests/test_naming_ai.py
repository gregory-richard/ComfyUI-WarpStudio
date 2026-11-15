from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from comfy_updater import naming_ai
from comfy_updater.naming_ai import (
    ModelAnswer,
    ModelInput,
    Slot,
    build_message,
    credit_lines,
    description_excerpt,
    parse_answer,
    retry_message,
    without_precision,
)


def two_slot_model() -> ModelInput:
    return ModelInput(
        model_id="1001",
        creator="tidemint",
        title="Lumegrid (Krea 2 / Z-Image / Qwen) - Realism LoRA",
        description="<p>Realism LoRA &amp; more.</p>",
        slots=[
            Slot(key="1", version_id="11", file_id="111", version_name="Krea 2 Zest Preview FIX", base_model="Krea 2"),
            Slot(
                key="2",
                version_id="12",
                file_id="121",
                version_name="Krea 2 Mellow V1",
                base_model="Krea 3",
                file_name="lumegrid_mellow.safetensors",
            ),
        ],
        new_bases=["Krea 3"],
    )


class NamingAiTests(unittest.TestCase):
    def test_build_message_exact_format(self):
        expected = (
            "Creator: tidemint\n"
            "Model title: Lumegrid (Krea 2 / Z-Image / Qwen) - Realism LoRA\n"
            "Versions:\n"
            "1. Krea 2 Zest Preview FIX [base: Krea 2]\n"
            "2. Krea 2 Mellow V1 [base: Krea 3] [file: lumegrid_mellow.safetensors]\n"
            "New base models: Krea 3\n"
            "Credit lines (only for the original line): none"
        )
        self.assertEqual(build_message(two_slot_model()), expected)

    def test_build_message_omits_new_bases_line_when_empty(self):
        mi = two_slot_model()
        mi.new_bases = []
        self.assertNotIn("New base models", build_message(mi))
        lines = build_message(mi).split("\n")
        self.assertTrue(lines[-2].endswith("[file: lumegrid_mellow.safetensors]"))
        self.assertTrue(lines[-1].startswith("Credit lines"))

    def test_build_message_shows_only_the_credit_lines(self):
        mi = two_slot_model()
        mi.description = (
            "<p>Trigger: lumegrid. Version 5 is out!</p>"
            "<p>Not my model. Original model at <a>someone/thing</a>.</p>"
        )
        message = build_message(mi)
        self.assertTrue(message.endswith(
            "Credit lines (only for the original line): Not my model. Original model at someone/thing ."
        ))
        self.assertNotIn("Trigger", message)
        self.assertNotIn("Version 5", message)

    def test_build_message_plain_characters_and_no_precision_words(self):
        mi = two_slot_model()
        mi.title = "0745 Dress\uff081.5 and pony\uff09"
        mi.slots[0].version_name = "v1.0\uff08pony\uff09_fp16"
        mi.slots[1].file_name = "lumegrid_mellow_BF16.safetensors"
        mi.slots.append(Slot(key="3", version_id="13", file_id="131", version_name="bf16", base_model="Krea 2"))
        lines = build_message(mi).split("\n")
        self.assertEqual(lines[1], "Model title: 0745 Dress(1.5 and pony)")
        self.assertEqual(lines[3], "1. v1.0(pony) [base: Krea 2]")
        self.assertEqual(lines[4], "2. Krea 2 Mellow V1 [base: Krea 3] [file: lumegrid_mellow.safetensors]")
        self.assertEqual(lines[5], "3. [base: Krea 2]")

    def test_without_precision(self):
        cases = {
            "krea2_raw_int8_convrot": "krea2_raw",
            "11.0 (BF16 + FP8)": "11.0",
            "ZiTX_11.0_FP8.safetensors": "ZiTX_11.0.safetensors",
            "v4.0 INT8 Expanded": "v4.0 Expanded",
            "fp8_e4m3fn (legacy)": "(legacy)",
            "Wan22_fp8_scaled": "Wan22",
            "Krea 2 - fp8": "Krea 2",
            "a - b_fp8": "a - b",
            "zo-mix_fp16": "zo-mix",
            "model-q4_k_m.gguf": "model.gguf",
            "bf16": "",
            "x_Q8_0": "x",
            "fp8 scaled": "",
            # A hyphen without spaces stays one; a dot stays one dot.
            "x-fp8-y": "x-y",
            "name.fp16.x": "name.x",
            # Untouched: no precision word, one only inside a longer word, or a
            # word that is also a real name word.
            "Q8 Turbo": "Q8 Turbo",
            "Q3 Release": "Q3 Release",
            "Scaled Down": "Scaled Down",
            "Leggy - MOSS": "Leggy - MOSS",
            "Upscaled v2": "Upscaled v2",
            "lumegrid_zesty_4820519.safetensors": "lumegrid_zesty_4820519.safetensors",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(without_precision(name), expected)

    def test_credit_lines_keeps_only_credit_sentences(self):
        html_text = (
            "<p>Great model for portraits. Trigger: abc.</p>"
            "<ul><li>Original model by <b>maker</b>.</li><li>Steps: 8</li></ul>"
            "<p>I am not the creator, just uploading it. Full credit goes to Some One.</p>"
            "<p>Original model by  maker .</p>"
        )
        self.assertEqual(
            credit_lines(html_text),
            "Original model by maker . I am not the creator, just uploading it. Full credit goes to Some One.",
        )

    def test_credit_lines_empty_and_limited(self):
        self.assertEqual(credit_lines(""), "")
        self.assertEqual(credit_lines("<p>Trigger: abc. Steps: 8.</p>"), "")
        long = "<p>" + " ".join("Original by maker number %d." % i for i in range(100)) + "</p>"
        self.assertLessEqual(len(credit_lines(long)), 600)
        # Whole sentences only.
        self.assertEqual(credit_lines(long, limit=60), "Original by maker number 0. Original by maker number 1.")
        # A first sentence longer than the limit is cut after a whole word.
        self.assertEqual(credit_lines("<p>Original by Thornquill and friends.</p>", limit=20), "Original by")

    def test_credit_lines_skips_weak_cues(self):
        html_text = "<p>I originally made this for fun. The official workflow is on github, says the author.</p>"
        self.assertEqual(credit_lines(html_text), "")
        self.assertEqual(credit_lines("<p>Model Developer: Some Lab.</p>"), "Model Developer: Some Lab.")

    def test_description_excerpt_strips_tags_and_entities(self):
        self.assertEqual(
            description_excerpt("<p>Original model at <a>fernwick/QSD</a>&nbsp;.</p>"),
            "Original model at fernwick/QSD .",
        )

    def test_description_excerpt_collapses_whitespace_and_limits(self):
        self.assertEqual(description_excerpt("<p>a\n\n  b</p><p>c</p>"), "a b c")
        self.assertEqual(description_excerpt("x" * 2000), "x" * 1500)
        self.assertEqual(description_excerpt("abcdef", limit=3), "abc")
        self.assertEqual(description_excerpt(""), "")

    def test_parse_answer_reads_every_part(self):
        answer = parse_answer(
            "title: lumegrid realism\noriginal:\nversion 1: zest preview fix\nversion 2: v1 mellow",
            two_slot_model(),
        )
        self.assertIsInstance(answer, ModelAnswer)
        self.assertEqual(answer.title, "lumegrid realism")
        self.assertEqual(answer.original, "")
        self.assertEqual(answer.versions, {"1": "zest preview fix", "2": "v1 mellow"})
        self.assertFalse(answer.unparsed)

    def test_parse_answer_ignores_prose_and_think_remnants(self):
        text = (
            "<think>\n\n</think>\n\n"
            "Here is the cleaned name:\n"
            "Title: lumegrid realism\n"
            "Original: fernwick\n"
            "Version 1: zest preview fix\n"
            "VERSION 2: v1 mellow\n"
            "base Krea 3: krea3\n"
            "Hope this helps!"
        )
        answer = parse_answer(text, two_slot_model())
        self.assertEqual(answer.title, "lumegrid realism")
        self.assertEqual(answer.original, "fernwick")
        self.assertEqual(answer.versions, {"1": "zest preview fix", "2": "v1 mellow"})
        self.assertEqual(answer.bases, {"Krea 3": "krea3"})
        self.assertFalse(answer.unparsed)

    def test_parse_answer_drops_thinking_before_stray_close_tag(self):
        text = (
            "Let me think. Draft:\n"
            "title: lumegrid realism lora\n"
            "version 1: krea 2 zest\n"
            "</think>\n"
            "title: lumegrid realism\n"
            "version 1: zest preview fix\n"
        )
        answer = parse_answer(text, two_slot_model())
        self.assertEqual(answer.title, "lumegrid realism")
        self.assertEqual(answer.versions, {"1": "zest preview fix"})

    def test_parse_answer_accepts_markdown_wrapping(self):
        bold = parse_answer("**title:** lumegrid realism\n**Version 1**: zest preview fix", two_slot_model())
        self.assertFalse(bold.unparsed)
        self.assertEqual(bold.title, "lumegrid realism")
        self.assertEqual(bold.versions, {"1": "zest preview fix"})
        bullets = parse_answer("- title: lumegrid realism\n* original:\n- version 2: v1 mellow", two_slot_model())
        self.assertEqual(bullets.title, "lumegrid realism")
        self.assertEqual(bullets.original, "")
        self.assertEqual(bullets.versions, {"2": "v1 mellow"})
        fenced = parse_answer(
            "```text\ntitle: lumegrid realism\nversion 1: zest preview fix\nbase Krea 3: krea3\n```",
            two_slot_model(),
        )
        self.assertEqual(fenced.title, "lumegrid realism")
        self.assertEqual(fenced.versions, {"1": "zest preview fix"})
        self.assertEqual(fenced.bases, {"Krea 3": "krea3"})
        heading = parse_answer("## Title: `lumegrid realism`", two_slot_model())
        self.assertEqual(heading.title, "lumegrid realism")

    def test_parse_answer_missing_version_line(self):
        answer = parse_answer("title: lumegrid realism\noriginal:\nversion 1: zest preview fix", two_slot_model())
        self.assertNotIn("2", answer.versions)
        self.assertEqual(answer.versions, {"1": "zest preview fix"})
        self.assertFalse(answer.unparsed)

    def test_parse_answer_empty_version_is_kept_as_empty(self):
        answer = parse_answer("title: lumegrid realism\nversion 1:\nversion 2: v1", two_slot_model())
        self.assertEqual(answer.versions, {"1": "", "2": "v1"})

    def test_parse_answer_ignores_unknown_slots_and_bases(self):
        answer = parse_answer(
            "title: lumegrid realism\nversion 3: v9\nbase Krea 9: krea9\nbase krea 3: krea3",
            two_slot_model(),
        )
        self.assertEqual(answer.versions, {})
        self.assertEqual(answer.bases, {"Krea 3": "krea3"})

    def test_parse_answer_refusal_is_unparsed(self):
        answer = parse_answer("I can't help with that.", two_slot_model())
        self.assertTrue(answer.unparsed)
        self.assertEqual(answer.title, "")
        self.assertEqual(answer.versions, {})

    def test_retry_message_ends_with_problems(self):
        mi = two_slot_model()
        message = retry_message(mi, ['"v4" is not a version number in the source'])
        self.assertTrue(message.startswith(build_message(mi)))
        self.assertTrue(
            message.endswith('- "v4" is not a version number in the source\nAnswer again in the same format.')
        )

    def test_system_prompt_and_version(self):
        path = Path(naming_ai.__file__).parent / "prompts" / "naming_system.txt"
        self.assertEqual(naming_ai.SYSTEM_PROMPT, path.read_text(encoding="utf-8"))
        self.assertEqual(
            naming_ai.PROMPT_VERSION,
            hashlib.sha256(naming_ai.SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12],
        )
        self.assertRegex(naming_ai.PROMPT_VERSION, r"^[0-9a-f]{12}$")
        self.assertIn("version 1:", naming_ai.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
