from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from comfy_updater import path_resolver
from comfy_updater.model_files import (
    folder_roots,
    lora_roots,
    missing_info,
    missing_loras,
    resolve_name,
    stem_files,
)

IDENTIFIED = {"modelId": 101, "id": 201, "files": [{"name": "glow.safetensors", "primary": True}]}


def _info(path: Path, payload=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(IDENTIFIED if payload is None else payload), encoding="utf-8")


class ModelFilesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "loras"
        self.root.mkdir()

    def test_missing_loras_lists_info_files_without_weights(self):
        _info(self.root / "pony" / "maker - glow - v1 (pony).civitai.info")
        (self.root / "pony" / "maker - glow - v1 (pony).preview.png").write_bytes(b"p")
        _info(self.root / "pony" / "b.civitai.info")
        (self.root / "pony" / "b.safetensors").write_bytes(b"w")
        _info(self.root / "c.civitai.info", {"name": "no ids"})

        found = missing_loras([self.root])

        self.assertEqual(
            [entry["id"] for entry in found],
            [os.path.join("pony", "maker - glow - v1 (pony).safetensors")],
        )
        self.assertEqual(found[0]["root"], self.root)
        self.assertEqual(found[0]["info"]["id"], 201)
        self.assertEqual(found[0]["infoPath"].name, "maker - glow - v1 (pony).civitai.info")

    def test_the_id_takes_the_info_files_extension(self):
        _info(
            self.root / "glow.civitai.info",
            {**IDENTIFIED, "files": [{"name": "glow.ckpt", "primary": True}]},
        )

        self.assertTrue(missing_loras([self.root])[0]["id"].endswith(".ckpt"))

    def test_weights_in_another_case_count_as_on_disk(self):
        _info(self.root / "a.civitai.info")
        (self.root / "A.SafeTensors").write_bytes(b"w")

        self.assertEqual(missing_loras([self.root]), [])

    def test_names_the_index_already_has_are_left_out(self):
        _info(self.root / "glow.civitai.info")
        _info(self.root / "other.civitai.info")

        found = missing_loras([self.root], {"glow.safetensors"})

        self.assertEqual([entry["id"] for entry in found], ["other.safetensors"])

    def test_a_part_file_does_not_count_as_weights(self):
        _info(self.root / "glow.civitai.info")
        (self.root / "glow.safetensors.part").write_bytes(b"half")

        self.assertEqual([e["id"] for e in missing_loras([self.root])], ["glow.safetensors"])
        listed = path_resolver.list_model_files({"lora": [self.root]})
        self.assertFalse([f for f in listed if str(f["path"]).endswith(".part")])

    def test_folder_roots_per_kind(self):
        import sys
        import types
        from unittest import mock

        module = types.ModuleType("folder_paths")
        module.get_folder_paths = lambda kind: [str(self.root / kind)]
        with mock.patch.dict(sys.modules, {"folder_paths": module}):
            self.assertEqual([self.root / "embeddings"], folder_roots("embeddings"))
            self.assertEqual(folder_roots("loras"), lora_roots())
            self.assertEqual([], folder_roots("vae"))

    def test_resolve_name_stays_inside_the_roots(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "glow.safetensors").write_bytes(b"w")
        (self.root.parent / "outside.safetensors").write_bytes(b"w")

        self.assertIsNone(resolve_name([self.root], "../outside.safetensors"))
        self.assertIsNone(resolve_name([self.root], str(self.root.parent / "outside.safetensors")))
        self.assertIsNone(resolve_name([self.root], "C:\\x.safetensors"))
        self.assertIsNone(resolve_name([self.root], "sub"))
        self.assertEqual(
            resolve_name([self.root], os.path.join("sub", "glow.safetensors")),
            self.root / "sub" / "glow.safetensors",
        )

    def test_missing_info_for_a_listed_id_and_none_once_the_weights_exist(self):
        _info(self.root / "glow.civitai.info")

        found = missing_info([self.root], "glow.safetensors")
        self.assertEqual(found[0], self.root / "glow.civitai.info")
        self.assertEqual(found[1]["id"], 201)
        self.assertIsNone(missing_info([self.root], "glow.txt"))
        self.assertIsNone(missing_info([self.root], "../glow.safetensors"))

        (self.root / "glow.safetensors").write_bytes(b"w")
        self.assertIsNone(missing_info([self.root], "glow.safetensors"))

    def test_stem_files_takes_every_sibling_of_the_stem(self):
        for name in ("x.safetensors", "x.civitai.info", "x.preview.png", "x.json"):
            (self.root / name).write_bytes(b"1")
        (self.root / "x.v2.safetensors").write_bytes(b"2")
        (self.root / "x.v2.civitai.info").write_bytes(b"2")

        files = stem_files(self.root / "x.safetensors")

        self.assertEqual(files[0], self.root / "x.safetensors")
        self.assertEqual(
            sorted(p.name for p in files),
            ["x.civitai.info", "x.json", "x.preview.png", "x.safetensors"],
        )
        self.assertEqual(stem_files(self.root / "gone.safetensors"), [])

    def test_stem_files_with_two_weights_of_one_stem_takes_only_the_chosen_one(self):
        for name in ("x.safetensors", "x.ckpt", "x.civitai.info"):
            (self.root / name).write_bytes(b"1")

        self.assertEqual(stem_files(self.root / "x.safetensors"), [self.root / "x.safetensors"])


if __name__ == "__main__":
    unittest.main()
