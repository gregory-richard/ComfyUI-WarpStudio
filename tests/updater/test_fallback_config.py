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


class FallbackSettingsRouteTests(unittest.TestCase):
    """The panel saves settings through the config route's payload filter."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def save(self, payload: dict) -> dict:
        from comfy_updater.routes import _normalize_config_payload

        return self.store.update(_normalize_config_payload(payload))

    def test_panel_payload_saves_all_four_settings(self) -> None:
        cfg = self.save({
            "fallbackCivarchive": True,
            "fallbackHuggingFace": ["malcolmrey", "owner/repo"],
            "fallbackAiToolkitFolders": ["C:/runs/output"],
            "fallbackOwnCreator": " me ",
        })
        self.assertIs(cfg["fallbackCivarchive"], True)
        self.assertEqual(cfg["fallbackHuggingFace"], ["malcolmrey", "owner/repo"])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [str(Path("C:/runs/output"))])
        self.assertEqual(cfg["fallbackOwnCreator"], "me")

    def test_text_values_are_split_like_path_settings(self) -> None:
        cfg = self.save({"fallbackHuggingFace": "malcolmrey; owner/repo\n", "fallbackAiToolkitFolders": "C:/a;C:/b"})
        self.assertEqual(cfg["fallbackHuggingFace"], ["malcolmrey", "owner/repo"])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [str(Path("C:/a")), str(Path("C:/b"))])

    def test_emptied_settings_are_saved_empty(self) -> None:
        self.save({"fallbackCivarchive": True, "fallbackHuggingFace": ["x"], "fallbackOwnCreator": "me"})
        cfg = self.save({
            "fallbackCivarchive": False,
            "fallbackHuggingFace": [],
            "fallbackAiToolkitFolders": [],
            "fallbackOwnCreator": "",
        })
        self.assertIs(cfg["fallbackCivarchive"], False)
        self.assertEqual(cfg["fallbackHuggingFace"], [])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [])
        self.assertEqual(cfg["fallbackOwnCreator"], "")

    def test_payload_without_fallback_keys_keeps_them(self) -> None:
        self.save({"fallbackCivarchive": True, "fallbackHuggingFace": ["owner/repo"], "fallbackOwnCreator": "me"})
        cfg = self.save({"maxRetries": 3})
        self.assertIs(cfg["fallbackCivarchive"], True)
        self.assertEqual(cfg["fallbackHuggingFace"], ["owner/repo"])
        self.assertEqual(cfg["fallbackOwnCreator"], "me")

    def test_odd_types_do_not_break_the_save(self) -> None:
        cfg = self.save({"fallbackHuggingFace": 5, "fallbackAiToolkitFolders": None, "fallbackOwnCreator": 7})
        self.assertEqual(cfg["fallbackHuggingFace"], [])
        self.assertEqual(cfg["fallbackAiToolkitFolders"], [])
        self.assertEqual(cfg["fallbackOwnCreator"], "")


if __name__ == "__main__":
    unittest.main()
