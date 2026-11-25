from __future__ import annotations

import os
import unittest

from comfy_updater.jobs import JobManager, move_key

from test_grouping import item

ROOT = "C:\\models\\loras\\"
DRIVE = "Z:\\archive\\loras\\"


class FakeNames:
    def __init__(self, labels: dict | None = None) -> None:
        self._labels = labels or {}
        self.calls = 0

    def labels(self) -> dict:
        self.calls += 1
        return self._labels


def file_item(model_id, stem, date, *, folder=ROOT, version_id=None, ext=".safetensors", **extra):
    """One local file of a model; ``folder`` and ``ext`` set where and what it is."""
    row = item(model_id, "Glow", "lora", "Illustrious", local_date=date)
    row["creatorName"] = "maker"
    row["localVersionId"] = version_id or f"{model_id}-{date[:7]}"
    row["modelPath"] = folder + stem + ext
    row.update(extra)
    return row


def build(items, names=None, **kwargs):
    manager = JobManager(release_names=names)
    manager.restore_job(
        "check-updates", "j", startedAt="2025-01-01", finishedAt="2025-01-02", summary={}, items=items
    )
    page = manager.get_items("j", limit=50, **kwargs)
    return page, page["items"]


class CardNameTests(unittest.TestCase):
    def two_files(self, **second):
        newer = file_item("m1", "moss - glow lite - v2 (ill)", "2025-06-01T00:00:00Z")
        newer.update(second)
        older = file_item("m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z")
        return [older, newer]

    def test_the_card_takes_creator_and_title_from_the_newest_file_that_parses(self) -> None:
        _, cards = build(self.two_files())
        card = cards[0]
        self.assertEqual(("moss", "glow lite"), (card["creator"], card["title"]))
        self.assertEqual({"creator": "moss", "title": "glow lite", "version": "v2"}, card["ownName"])

    def test_a_deleted_file_does_not_name_the_card(self) -> None:
        _, cards = build(self.two_files(deleted=True))
        self.assertEqual(("moss", "glow"), (cards[0]["creator"], cards[0]["title"]))

    def test_a_file_on_the_archive_drive_names_the_card(self) -> None:
        items = self.two_files(
            archived=True, modelPath=DRIVE + "moss - glow lite - v2 (ill).civitai.info", metadataOnly=True
        )
        _, cards = build(items)
        self.assertEqual("glow lite", cards[0]["title"])

    def test_without_a_file_that_parses_the_card_keeps_civitais_names(self) -> None:
        _, cards = build([file_item("m1", "glowV2", "2025-01-01T00:00:00Z")])
        card = cards[0]
        self.assertEqual(("maker", "Glow", None), (card["creator"], card["title"], card["ownName"]))

    def test_a_row_shows_its_files_version_and_base(self) -> None:
        items = [
            file_item("m1", "moss - glow - xxl (moss)", "2025-06-01T00:00:00Z"),
            file_item("m1", "glowV2", "2025-01-01T00:00:00Z"),
        ]
        _, cards = build(items)
        card = cards[0]
        self.assertEqual(["xxl (moss)", "glowV2"], [v["rowName"] for v in card["localVersions"]])
        self.assertEqual("moss - glow - xxl (moss).safetensors", card["localVersions"][0]["fileName"])

    def test_the_file_name_is_the_weights_file_even_for_an_info_only_entry(self) -> None:
        row = file_item(
            "m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", ext=".civitai.info", metadataOnly=True
        )
        _, cards = build([row])
        version = cards[0]["localVersions"][0]
        self.assertEqual("moss - glow - v1 (ill).safetensors", version["fileName"])
        self.assertEqual("v1 (ill)", version["rowName"])

    def test_releases_carry_their_stored_names(self) -> None:
        row = file_item("m1", "glowV2", "2025-01-01T00:00:00Z")
        row["remoteVersions"] = [
            {"versionId": "202", "versionName": "v3", "versionDate": "2025-08-01T00:00:00Z"},
            {"versionId": "203", "versionName": "v4", "versionDate": "2025-09-01T00:00:00Z"},
            {"versionId": "204", "versionName": "v5", "versionDate": "2025-10-01T00:00:00Z"},
        ]
        names = FakeNames({
            "202": {"stem": "moss - glow - v3 (ill)", "label": "v3 (ill)"},
            "204": {"stem": "moss - glow - v5 (ill)", "label": "v5 (ill)"},
        })
        manager = JobManager(release_names=names)
        manager.restore_job("check-updates", "j", startedAt="a", finishedAt="b", summary={}, items=[row])
        # 204 is hidden through the Not interested list.
        manager.archive_store = _Hidden({"m1": {"204"}})
        card = manager.get_items("j", limit=50)["items"][0]
        by_id = {v["versionId"]: v for v in card["newVersions"]}
        self.assertEqual(("v3 (ill)", "moss - glow - v3 (ill)"), (by_id["202"]["rowName"], by_id["202"]["fileStem"]))
        self.assertEqual(("", ""), (by_id["203"]["rowName"], by_id["203"]["fileStem"]))
        hidden = card["hiddenNewVersions"][0]
        self.assertEqual(("v5 (ill)", "moss - glow - v5 (ill)"), (hidden["rowName"], hidden["fileStem"]))
        self.assertEqual(1, names.calls)

    def test_the_name_sort_follows_the_shown_title(self) -> None:
        first = file_item("m1", "x - alpha - v1 (ill)", "2025-01-01T00:00:00Z")
        first["modelName"] = "Zeta"
        second = file_item("m2", "y - zulu - v1 (ill)", "2025-01-01T00:00:00Z")
        second["modelName"] = "Alpha"
        for sort, expected in (("name", ["alpha", "zulu"]), ("name-desc", ["zulu", "alpha"])):
            page, _ = build([second, first], sort=sort)
            self.assertEqual(expected, [c["title"] for c in page["items"]])
        page, _ = build([second, first], sort="behind")
        self.assertEqual(["alpha", "zulu"], [c["title"] for c in page["items"]])

    def test_local_versions_carry_a_move_key_without_their_path(self) -> None:
        one = file_item("m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z")
        two = file_item("m1", "moss - glow - v2 (ill)", "2025-06-01T00:00:00Z")
        _, cards = build([one, two])
        rows = {v["modelPath"]: v for v in cards[0]["localVersions"]}
        row = rows[one["modelPath"]]
        self.assertEqual(move_key(one["modelPath"]), row["moveKey"])
        self.assertNotIn("models", row["moveKey"])
        self.assertTrue(row["moveKey"].startswith("move:"))
        self.assertNotEqual(move_key(one["modelPath"]), move_key(two["modelPath"]))

    def test_the_move_key_is_stable_and_short(self) -> None:
        path = "C:\\models\\a.safetensors"
        self.assertEqual(move_key(path), move_key(path))
        self.assertEqual(len("move:") + 16, len(move_key(path)))

    def test_a_moved_row_leaves_its_card_and_keeps_an_issue_card_listed(self) -> None:
        z_path = DRIVE + "moss - glow - v1 (ill).civitai.info"
        moved = file_item(
            "m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", status="error", deleted=True, moved=True
        )
        copy = file_item(
            "m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", archived=True, modelPath=z_path,
            metadataOnly=True, version_id=moved["localVersionId"],
        )
        page, cards = build([moved, copy], mode="issues")
        card = cards[0]
        self.assertEqual([z_path], [v["modelPath"] for v in card["localVersions"]])
        self.assertEqual([["saved", 0]], card["versionOrder"])
        self.assertEqual(1, card["deletedIssues"])
        self.assertEqual(1, page["total"])

    def test_a_file_moved_in_is_not_downloaded(self) -> None:
        row = file_item("m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", lastCheckedAt="2025-03-01T00:00:00Z")
        manager = JobManager()
        manager.restore_job(
            "check-updates", "j", startedAt="2025-01-01T00:00:00Z", finishedAt="2025-02-01T00:00:00Z",
            summary={}, items=[row],
        )
        card = manager.get_items("j", limit=50)["items"][0]
        self.assertTrue(card["localVersions"][0]["downloaded"])
        row["movedAt"] = "2025-03-01T00:00:00Z"
        card = manager.get_items("j", limit=50)["items"][0]
        self.assertFalse(card["localVersions"][0]["downloaded"])

    def test_a_deleted_archived_copy_makes_no_duplicate(self) -> None:
        drive = file_item("m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", version_id="v1")
        gone = file_item(
            "m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", archived=True, deleted=True,
            folder=DRIVE, version_id="v1",
        )
        _, cards = build([drive, gone])
        d_row = next(v for v in cards[0]["localVersions"] if not v["archived"])
        self.assertNotIn("alsoArchived", d_row)

    def test_the_card_carries_its_models_stats(self) -> None:
        row = file_item("m1", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", modelStats={"downloadCount": 5})
        other = file_item("m2", "moss - other - v1 (ill)", "2025-01-01T00:00:00Z")
        _, cards = build([row, other], sort="name")
        by_id = {c["modelId"]: c for c in cards}
        self.assertEqual({"downloadCount": 5}, by_id["m1"]["modelStats"])
        self.assertEqual({}, by_id["m2"]["modelStats"])

    def test_an_unidentified_file_gets_the_same_fields(self) -> None:
        row = file_item("", "moss - glow - v1 (ill)", "2025-01-01T00:00:00Z", status="not_found")
        row["modelId"] = ""
        row["modelUrl"] = ""
        row["modelStats"] = {"downloadCount": 1}
        _, cards = build([row])
        card = cards[0]
        self.assertEqual(("moss", "glow"), (card["creator"], card["title"]))
        self.assertEqual({"creator": "moss", "title": "glow", "version": "v1"}, card["ownName"])
        self.assertEqual({"downloadCount": 1}, card["modelStats"])
        version = card["localVersions"][0]
        self.assertEqual("v1 (ill)", version["rowName"])
        self.assertEqual("moss - glow - v1 (ill).safetensors", version["fileName"])
        self.assertEqual(move_key(row["modelPath"]), version["moveKey"])


class _Hidden:
    def __init__(self, hidden: dict) -> None:
        self._hidden = hidden

    def snapshot(self) -> dict:
        return self._hidden


if __name__ == "__main__":
    unittest.main()
