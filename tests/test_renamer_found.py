"""Files Civitai can't serve whose info the preview fallback filled in are named like any other.

The names and examples here are made up.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from comfy_updater.civitai_client import CivitaiRequestError
from comfy_updater.naming_check import glued_names, invented_words, needs_review
from comfy_updater.rename_store import AnswerCache, BaseCodeStore, DecisionStore
from comfy_updater.renamer import collect_entries, run_dry_run
from comfy_updater.routes import reassemble_rows

from test_renamer_naming import FakeClient, FakeGenerator, live_file, live_model, live_version, no_progress

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def write_found(
    folder: Path,
    stem: str,
    *,
    source: str = "huggingface",
    title: str = "mirahollow",
    creator: str = "quillmoth",
    version: str = "v1",
    base: str = "Krea 2",
    sha256: str = SHA_A,
    url: str = "https://huggingface.co/quillmoth/krea2/blob/main/krea2_mirahollow_v1.safetensors",
    model_id="",
    version_id="",
    description: str = "",
    weights: bool = True,
    fallback: bool = True,
) -> Path:
    """A stub info file as the preview fallback leaves it."""
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "id": version_id,
        "modelId": model_id,
        "name": version,
        "baseModel": base,
        "files": [{"hashes": {"SHA256": sha256}}],
        "model": {"name": title, "nsfw": False},
        "creator": {"username": creator},
        "extensions": {"source": "comfy-civitai-updater"},
    }
    if description:
        info["description"] = description
    if fallback:
        info["extensions"]["fallback"] = {"source": source, "url": url, "foundAt": "2026-11-16T10:00:00+00:00"}
    (folder / (stem + ".civitai.info")).write_text(json.dumps(info), encoding="utf-8")
    path = folder / (stem + ".safetensors")
    if weights:
        path.write_bytes(b"weights")
        return path
    return folder / (stem + ".civitai.info")


class NoCivitai(FakeClient):
    """Fails the test if anything is fetched from Civitai."""

    def __init__(self, models: dict | None = None) -> None:
        super().__init__(models or {})
        self.asked: list = []

    def get_model(self, model_id, *, raise_on_error=False):
        self.asked.append(str(model_id))
        return super().get_model(model_id, raise_on_error=raise_on_error)


def gone() -> CivitaiRequestError:
    return CivitaiRequestError("404 Not Found", status=404)


class CollectFoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lora = Path(self._tmp.name) / "loras"
        self.roots = {"lora": [self.lora]}

    def test_a_filled_stub_without_civitai_ids_is_listed_with_its_own_identity(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        [entry] = collect_entries(self.roots)
        self.assertEqual(entry.found, {"source": "huggingface", "url": "https://huggingface.co/quillmoth/krea2/blob/main/krea2_mirahollow_v1.safetensors"})
        self.assertFalse(entry.model_id.isdigit())
        self.assertEqual(entry.version_id, "sha256:" + SHA_A)
        self.assertEqual(entry.sha256, SHA_A)

    def test_a_stub_no_source_filled_in_is_still_not_listed(self) -> None:
        write_found(self.lora, "anon", fallback=False)
        self.assertEqual(collect_entries(self.roots), [])

    def test_a_filled_stub_without_a_title_is_not_listed(self) -> None:
        write_found(self.lora, "anon", title="")
        self.assertEqual(collect_entries(self.roots), [])

    def test_files_of_one_creator_and_title_share_a_model(self) -> None:
        write_found(self.lora / "krea2", "a", sha256=SHA_A)
        write_found(self.lora / "zit", "b", sha256=SHA_B, base="ZImageTurbo")
        write_found(self.lora / "krea2", "c", sha256=SHA_C, title="otherperson")
        entries = {entry.stem: entry for entry in collect_entries(self.roots)}
        self.assertEqual(entries["a"].model_id, entries["b"].model_id)
        self.assertNotEqual(entries["a"].model_id, entries["c"].model_id)

    def test_a_stub_with_civitai_ids_keeps_them_and_says_where_it_was_found(self) -> None:
        write_found(self.lora, "x", source="civarchive", model_id=501, version_id=5011, url="https://civarchive.com/sha256/" + SHA_A)
        [entry] = collect_entries(self.roots)
        self.assertEqual((entry.model_id, entry.version_id), ("501", "5011"))
        self.assertEqual(entry.found["source"], "civarchive")


class NameFoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data = root / "data"
        self.data.mkdir()
        self.lora = root / "loras"
        self.unet = root / "unet"
        self.roots = {"lora": [self.lora], "unet": [self.unet]}
        self.decisions = DecisionStore(self.data)
        self.base_codes = BaseCodeStore(self.data)
        self.base_codes.set("Krea 2", "krea2")
        self.base_codes.set("ZImageTurbo", "zit")
        self.cache = AnswerCache(self.data)

    def dry_run(self, generator, client=None):
        client = client if client is not None else NoCivitai()
        summary, rows = run_dry_run(
            self.roots, client, generator, decisions=self.decisions, base_codes=self.base_codes,
            cache=self.cache, clip_name="qwen3.safetensors", progress=no_progress, emit=lambda row: None,
            control=None,
        )
        return client, rows

    def row(self, rows, stem: str) -> dict:
        [row] = [row for row in rows if row["oldStem"] == stem]
        return row

    def test_the_naming_model_names_it_from_the_found_info_without_asking_civitai(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        generator = FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1"})
        client, rows = self.dry_run(generator)
        self.assertEqual(client.asked, [])
        message = generator.messages[0]
        self.assertIn("Creator: quillmoth\n", message)
        self.assertIn("Model title: mirahollow\n", message)
        self.assertIn("1. v1 [base: Krea 2]", message)
        row = self.row(rows, "quill - mira hollow (krea2)")
        self.assertEqual(row["status"], "change")
        self.assertEqual(row["newStem"], "quillmoth - mira hollow - v1 (krea2)")
        self.assertEqual(row["liveTitle"], "mirahollow")
        self.assertEqual(row["uploader"], "quillmoth")

    def test_the_row_says_where_the_info_came_from_and_starts_unticked(self) -> None:
        url = "https://huggingface.co/quillmoth/krea2/blob/main/krea2_mirahollow_v1.safetensors"
        write_found(self.lora, "quill - mira hollow (krea2)", url=url)
        _client, rows = self.dry_run(FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1"}))
        row = self.row(rows, "quill - mira hollow (krea2)")
        self.assertEqual(row["flags"], ["found:huggingface"])
        self.assertEqual(row["foundUrl"], url)
        self.assertTrue(needs_review(row["flags"]))
        self.assertFalse(row["selected"])

    def test_a_word_not_in_the_found_info_is_still_invented(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        generator = FakeGenerator({"mirahollow": "title: mira hollows\noriginal:\nversion 1: v1"})
        _client, rows = self.dry_run(generator)
        self.assertEqual(len(generator.messages), 2)  # asked once more, then flagged
        self.assertIn("invented:hollows", self.row(rows, "quill - mira hollow (krea2)")["flags"])

    def test_one_creator_and_title_on_two_bases_is_one_model(self) -> None:
        write_found(self.lora / "krea2", "quill - mira hollow (krea2)", sha256=SHA_A)
        write_found(self.lora / "zit", "quill - mira hollow (zit)", sha256=SHA_B, base="ZImageTurbo")
        generator = FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1\nversion 2: v1"})
        _client, rows = self.dry_run(generator)
        self.assertEqual(len(generator.messages), 1)
        self.assertIn("2. v1 [base: ZImageTurbo]", generator.messages[0])
        self.assertEqual(self.row(rows, "quill - mira hollow (zit)")["newStem"], "quillmoth - mira hollow - v1 (zit)")

    def test_a_removed_civitai_model_is_named_from_its_found_info(self) -> None:
        write_found(
            self.lora, "keep - old name (krea2)", source="civarchive", model_id=501, version_id=5011,
            title="Velvet Hush (Krea 2)", creator="dawnwright", version="Krea 2 - v2.0",
            url="https://civarchive.com/sha256/" + SHA_A, description="<p>Made by me.</p>",
        )
        generator = FakeGenerator({"Velvet Hush (Krea 2)": "title: velvet hush\noriginal:\nversion 1: v2"})
        client, rows = self.dry_run(generator, NoCivitai({"501": gone()}))
        self.assertEqual(client.asked, ["501"])
        row = self.row(rows, "keep - old name (krea2)")
        self.assertEqual(row["newStem"], "dawnwright - velvet hush - v2 (krea2)")
        self.assertEqual(row["flags"], ["found:civarchive"])
        self.assertEqual(row["modelId"], "501")

    def test_files_of_a_removed_model_without_found_info_keep_their_names(self) -> None:
        write_found(self.lora, "found one (krea2)", source="civarchive", model_id=501, version_id=5011, title="Velvet Hush")
        write_found(self.lora, "plain one (krea2)", model_id=501, version_id=5012, sha256=SHA_B, fallback=False)
        generator = FakeGenerator({"Velvet Hush": "title: velvet hush\noriginal:\nversion 1: v1"})
        _client, rows = self.dry_run(generator, NoCivitai({"501": gone()}))
        plain = self.row(rows, "plain one (krea2)")
        self.assertEqual((plain["status"], plain["reason"]), ("kept", "not-on-civitai"))
        self.assertEqual(self.row(rows, "found one (krea2)")["status"], "change")

    def test_a_live_civitai_model_is_named_from_civitai_not_the_found_info(self) -> None:
        write_found(self.lora, "x (krea2)", source="civarchive", model_id=501, version_id=5011, title="Old Title")
        live = live_model(501, "Live Title", "dawnwright", "LORA", [live_version(5011, "v3", "Krea 2", [live_file(9, SHA_A)])])
        generator = FakeGenerator({"Live Title": "title: live title\noriginal:\nversion 1: v3"})
        _client, rows = self.dry_run(generator, NoCivitai({"501": live}))
        row = self.row(rows, "x (krea2)")
        self.assertEqual(row["newStem"], "dawnwright - live title - v3 (krea2)")
        self.assertEqual(row["flags"], [])
        self.assertNotIn("foundUrl", row)

    def test_a_version_name_that_is_only_the_file_name_is_no_version_name(self) -> None:
        # The fallback leaves the stub's placeholder (the file's own name) when its source has no version name.
        write_found(self.lora, "quill - mira hollow (krea2)", version="quill - mira hollow (krea2).safetensors")
        generator = FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1:"})
        _client, rows = self.dry_run(generator)
        self.assertIn("1. [base: Krea 2]", generator.messages[0])
        self.assertEqual(self.row(rows, "quill - mira hollow (krea2)")["newStem"], "quillmoth - mira hollow (krea2)")

    def test_no_live_type_to_check_against_the_folder(self) -> None:
        write_found(self.unet, "mira base", title="Mira Base", version="9B")
        generator = FakeGenerator({"Mira Base": "title: mira base\noriginal:\nversion 1: 9b"})
        _client, rows = self.dry_run(generator)
        row = self.row(rows, "mira base")
        self.assertEqual(row["status"], "change")
        self.assertEqual(row["modelType"], "unet")

    def test_no_file_not_in_release_hint(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        _client, rows = self.dry_run(FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1"}))
        self.assertNotIn("file-not-in-release", self.row(rows, "quill - mira hollow (krea2)")["flags"])

    def test_two_copies_of_one_found_file_in_a_folder_are_duplicates(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        write_found(self.lora, "mirahollow copy")
        _client, rows = self.dry_run(FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1"}))
        self.assertEqual(self.row(rows, "mirahollow copy")["status"], "duplicate")
        self.assertEqual(self.row(rows, "quill - mira hollow (krea2)")["status"], "change")

    def test_an_edit_keeps_the_source_line(self) -> None:
        write_found(self.lora, "quill - mira hollow (krea2)")
        _client, rows = self.dry_run(FakeGenerator({"mirahollow": "title: mira hollow\noriginal:\nversion 1: v1"}))
        row = self.row(rows, "quill - mira hollow (krea2)")
        self.decisions.set_model(row["modelId"], title="mira hollow portrait")
        reassemble_rows(rows, self.decisions, self.base_codes, {}, model_id=row["modelId"])
        self.assertEqual(row["parts"]["title"], "mira hollow portrait")
        self.assertIn("found:huggingface", row["flags"])
        self.assertFalse(row["selected"])


class SplitNameTests(unittest.TestCase):
    """Titles found elsewhere often run a name together ("mirahollow"); the AI may write it apart, and the checks let that through."""

    def test_splitting_a_run_together_name_is_not_invented(self) -> None:
        self.assertEqual(invented_words("mirahollow v1", ["mira hollow", "v1"]), [])

    def test_splitting_a_run_together_name_is_not_a_glued_name(self) -> None:
        self.assertEqual(glued_names("mirahollow", "mira hollow", frozenset({"krea2", "krea"})), [])


if __name__ == "__main__":
    unittest.main()
