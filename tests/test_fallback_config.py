from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from comfy_updater.config_store import ConfigStore


class FallbackConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_defaults(self) -> None:
        cfg = ConfigStore(self.data).get()
        self.assertIs(cfg["fallbackCivarchive"], False)
        self.assertEqual(cfg["fallbackHuggingFace"], ["malcolmrey"])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [])
        self.assertEqual(cfg["fallbackOwnCreator"], "")

    def test_update_cleans_values(self) -> None:
        store = ConfigStore(self.data)
        cfg = store.update({
            "fallbackCivarchive": 1,
            "fallbackHuggingFace": [" someone ", "example/repo", "Example/Repo", "bad name", "a/b/c", "", 5],
            "fallbackAiToolkitFolders": ["  C:/x/output ", "", 3],
            "fallbackOwnCreator": "  me  ",
        })
        self.assertIs(cfg["fallbackCivarchive"], True)
        self.assertEqual(cfg["fallbackHuggingFace"], ["someone", "example/repo"])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [str(Path("C:/x/output"))])
        self.assertEqual(cfg["fallbackOwnCreator"], "me")

    def test_update_caps_creator_at_100_characters(self) -> None:
        cfg = ConfigStore(self.data).update({"fallbackOwnCreator": "x" * 150})
        self.assertEqual(cfg["fallbackOwnCreator"], "x" * 100)

    def test_empty_hf_list_stays_empty(self) -> None:
        self.assertEqual(ConfigStore(self.data).update({"fallbackHuggingFace": []})["fallbackHuggingFace"], [])

    def test_saved_config_without_new_keys_gets_defaults(self) -> None:
        (self.data / "config.json").write_text(json.dumps({"apiKey": ""}), encoding="utf-8")
        cfg = ConfigStore(self.data).get()
        self.assertEqual(cfg["fallbackHuggingFace"], ["malcolmrey"])
        self.assertIs(cfg["fallbackCivarchive"], False)
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [])
        self.assertEqual(cfg["fallbackOwnCreator"], "")


if __name__ == "__main__":
    unittest.main()
