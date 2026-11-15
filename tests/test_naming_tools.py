"""The pure helpers of the naming regression tools (no network, no ComfyUI)."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load("naming_regression")
builder = _load("build_naming_regression")


def case(number: int, slot_keys=("1",), expect_keys=("1",), **extra) -> dict:
    slots = [{"key": k, "version_id": "9", "file_id": "", "version_name": "v1", "base_model": "Pony",
              "file_name": ""} for k in slot_keys]
    return dict({"case": number,
                 "input": {"model_id": "1", "creator": "c", "title": "t", "description": "", "slots": slots},
                 "expect": {"title": "t", "original": "", "versions": {k: "v1" for k in expect_keys}}}, **extra)


def live_model(files) -> dict:
    return {"id": 5, "name": "Live Title", "creator": {"username": "uploader"}, "description": "<p>d</p>",
            "modelVersions": [{"id": 9, "name": "v2.0", "baseModel": "Pony", "files": files}]}


class RunnerHelperTests(unittest.TestCase):
    def test_select_cases_refuses_unknown_and_empty_selections(self):
        cases = [case(1), case(2)]
        self.assertEqual([c["case"] for c in runner.select_cases(cases, [2])], [2])
        self.assertEqual(len(runner.select_cases(cases, None)), 2)
        with self.assertRaises(runner.BadData):
            runner.select_cases(cases, [3])
        with self.assertRaises(runner.BadData):
            runner.select_cases([], None)

    def test_check_case_refuses_mismatched_keys_and_todo(self):
        runner.check_case(case(1, ("1", "2"), ("1", "2")))
        with self.assertRaises(runner.BadData):
            runner.check_case(case(1, ("1", "2"), ("1",)))
        with self.assertRaises(runner.BadData):
            runner.check_case(case(1, todo=True))

    def test_same_maker(self):
        self.assertTrue(runner._same_maker("Krea.ai", "krea ai"))
        self.assertTrue(runner._same_maker("", ""))
        self.assertTrue(runner._same_maker("  ", ""))
        self.assertFalse(runner._same_maker("-", ""))
        self.assertFalse(runner._same_maker("", "fernwick"))
        self.assertFalse(runner._same_maker("someone", "fernwick"))

    def test_missing_data_file_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as out:
            code = runner.main(["--clip", "x", "--data", str(Path(folder) / "none.json")])
        self.assertEqual(code, runner.EXIT_BAD_INPUT)
        self.assertIn("REFUSED", out.getvalue())


class BuilderHelperTests(unittest.TestCase):
    def test_build_input_one_main_file_is_one_slot(self):
        model = live_model([{"id": 1, "type": "Model", "name": "a.safetensors"},
                            {"id": 2, "type": "Training Data", "name": "data.zip"}])
        built = builder.build_input(model, "9")
        self.assertEqual(built["title"], "Live Title")
        self.assertEqual(built["creator"], "uploader")
        self.assertEqual(built["slots"], [{"version_id": "9", "version_name": "v2.0", "base_model": "Pony",
                                           "key": "1", "file_id": "1", "file_name": ""}])

    def test_build_input_several_main_files(self):
        model = live_model([{"id": 1, "type": "Model", "name": "a_high.safetensors"},
                            {"id": 2, "type": "Pruned Model", "name": "a_low.safetensors"}])
        slots = builder.build_input(model, "9")["slots"]
        self.assertEqual([(s["key"], s["file_name"]) for s in slots],
                         [("1", "a_high.safetensors"), ("2", "a_low.safetensors")])
        single = builder.build_input(model, "9", per_file=False)["slots"]
        self.assertEqual([(s["key"], s["file_name"]) for s in single], [("1", "")])

    def test_build_input_missing_version(self):
        with self.assertRaises(builder.NotServed):
            builder.build_input(live_model([]), "10")

    def test_changes(self):
        old = case(1)["input"]
        new = json.loads(json.dumps(old))
        self.assertEqual(builder.changes(old, new), [])
        new["title"] = "New"
        new["slots"][0]["version_name"] = "v2"
        found = builder.changes(old, new)
        self.assertEqual(len(found), 2)
        self.assertIn("'t' -> 'New'", found[0])

    def test_parse_add(self):
        self.assertEqual(builder.parse_add("12:34"), ("12", "34"))
        for bad in ("12", "a:1", "1:", ":1", "1:2:3"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                builder.parse_add(bad)


if __name__ == "__main__":
    unittest.main()
