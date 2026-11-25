from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from comfy_updater.download_target import AiName, NamedRelease
from comfy_updater.naming_scheme import NameParts
from comfy_updater.release_names import RELEASE_NAMES_FILENAME, ReleaseNameStore
from comfy_updater.rename_store import BaseCodeStore


def glow(code: str = "ill", *, base_model: str = "Illustrious", new_base: str = "", precision: str = "") -> NamedRelease:
    return NamedRelease(NameParts("moss", "glow", "v2", precision, code), base_model, new_base)


class ReleaseNameStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.base_codes = BaseCodeStore(self.data)
        self.store = ReleaseNameStore(self.data, self.base_codes)

    def test_names_survive_a_new_store(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        self.store.put("101", {"202": glow(), "203": glow()})
        again = ReleaseNameStore(self.data, self.base_codes)
        self.assertTrue(again.has("202"))
        self.assertEqual(AiName("moss - glow - v2 (ill)", "ill", ""), again.get("202"))
        saved = json.loads((self.data / RELEASE_NAMES_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual("101", saved["names"]["202"]["modelId"])
        self.assertEqual("Illustrious", saved["names"]["202"]["baseModel"])

    def test_labels_give_the_rows_text_and_the_whole_stem(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        self.store.put("101", {"202": glow()})
        self.assertEqual({"202": {"stem": "moss - glow - v2 (ill)", "label": "v2 (ill)"}}, self.store.labels())

    def test_precision_is_part_of_the_label(self) -> None:
        self.base_codes.set("SDXL 1.0", "sdxl")
        named = NamedRelease(NameParts("moss", "glow", "v2", "fp16", "sdxl"), "SDXL 1.0", "")
        self.store.put("101", {"202": named})
        self.assertEqual("v2 fp16 (sdxl)", self.store.labels()["202"]["label"])

    def test_a_code_learned_later_wins_over_the_stored_one(self) -> None:
        self.store.put("101", {"202": NamedRelease(NameParts("moss", "glow", "v2", "", "illx"), "Illustrious X", "Illustrious X")})
        self.assertEqual(AiName("moss - glow - v2 (illx)", "illx", "Illustrious X"), self.store.get("202"))
        self.base_codes.set("Illustrious X", "ilx")
        self.assertEqual(AiName("moss - glow - v2 (ilx)", "ilx", ""), self.store.get("202"))
        self.assertEqual("v2 (ilx)", self.store.labels()["202"]["label"])

    def test_an_unknown_release_has_no_name(self) -> None:
        self.assertIsNone(self.store.get("999"))
        self.assertFalse(self.store.has("999"))

    def test_putting_again_replaces_a_releases_name(self) -> None:
        self.base_codes.set("Illustrious", "ill")
        self.store.put("101", {"202": glow()})
        self.store.put("101", {"202": NamedRelease(NameParts("moss", "glow lite", "v2", "", "ill"), "Illustrious", "")})
        self.assertEqual("moss - glow lite - v2 (ill)", self.store.get("202").stem)

    def test_a_corrupt_file_is_moved_aside_and_the_store_starts_empty(self) -> None:
        (self.data / RELEASE_NAMES_FILENAME).write_text("{not json", encoding="utf-8")
        store = ReleaseNameStore(self.data, self.base_codes)
        self.assertFalse(store.has("202"))
        self.assertEqual({}, store.labels())
        self.assertTrue(any(p.name.startswith(RELEASE_NAMES_FILENAME) and p.name != RELEASE_NAMES_FILENAME for p in self.data.iterdir()))


if __name__ == "__main__":
    unittest.main()
