"""Letter case in file identity: two files on a case-sensitive volume, one where case does not count.

The flag is switched per test. A case-sensitive folder on Windows is made with
``fsutil file setCaseSensitiveInfo`` (skipped where that is not available); the
default Windows folder then stands in for an NTFS drive mounted on Linux, where
the platform says case counts and the drive says it does not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from comfy_updater import path_identity
from comfy_updater.archive import list_archive_entries
from comfy_updater.download_target import stem_taken
from comfy_updater.model_files import missing_loras, stem_files
from comfy_updater.model_moves import taken_name
from comfy_updater.path_identity import path_key, same_path
from comfy_updater.path_resolver import list_model_files
from comfy_updater.rename_apply import TARGET_EXISTS, apply_steps, assign_siblings, plan, recover
from comfy_updater.rename_folders import recheck_folder_flags
from comfy_updater.rename_store import RenameJournal
from comfy_updater.renamer import _disk_stems, _FolderCheck, path_file_key

SIDECARS = (".safetensors", ".civitai.info", ".preview.png")
INFO = '{"modelId": 1, "id": 2}'


def _make(folder: Path, stem: str, suffixes=SIDECARS) -> None:
    for suffix in suffixes:
        (folder / (stem + suffix)).write_text(stem + suffix, encoding="utf-8")


def _row(folder: Path, old: str, new: str, **extra) -> dict:
    row = {
        "key": path_key(str(folder / (old + ".safetensors"))),
        "modelPath": str(folder / (old + ".safetensors")),
        "folder": str(folder),
        "oldStem": old,
        "newStem": new,
        "status": "change",
    }
    row.update(extra)
    return row


def _two_spellings_are_two_files(folder: Path) -> bool:
    (folder / "probe").write_text("a", encoding="utf-8")
    try:
        (folder / "PROBE").write_text("b", encoding="utf-8")
        return len(os.listdir(folder)) == 2
    finally:
        for name in os.listdir(folder):
            os.remove(folder / name)


@pytest.fixture
def sensitive(tmp_path, monkeypatch) -> Path:
    """A case-sensitive folder, with the platform flag saying so."""
    folder = tmp_path / "loras"
    folder.mkdir()
    if sys.platform == "win32":
        done = subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", str(folder), "enable"], capture_output=True, check=False
        )
        if done.returncode != 0:
            pytest.skip("this Windows can't make a case-sensitive folder")
    if not _two_spellings_are_two_files(folder):
        pytest.skip("no case-sensitive folder here")
    monkeypatch.setattr(path_identity, "CASE_SENSITIVE", True)
    return folder


@pytest.fixture
def ntfs_on_linux(tmp_path, monkeypatch) -> Path:
    """A case-insensitive folder while the platform flag says case counts."""
    folder = tmp_path / "loras"
    folder.mkdir()
    if _two_spellings_are_two_files(folder):
        pytest.skip("no case-insensitive folder here")
    monkeypatch.setattr(path_identity, "CASE_SENSITIVE", True)
    return folder


# --- the key ---------------------------------------------------------------


def test_path_key_is_lower_where_case_does_not_count(monkeypatch) -> None:
    monkeypatch.setattr(path_identity, "CASE_SENSITIVE", False)
    assert path_key(r"D:\Loras\Foo.safetensors") == r"d:\loras\foo.safetensors"
    assert path_key("Straße/Foo") == "Straße/Foo".lower()  # exactly lower(), not casefold()
    assert same_path("a/Foo", "A/foo")


def test_path_key_keeps_case_where_it_counts(monkeypatch) -> None:
    monkeypatch.setattr(path_identity, "CASE_SENSITIVE", True)
    assert path_key("/models/Foo.safetensors") == "/models/Foo.safetensors"
    assert not same_path("a/Foo", "a/foo")
    assert same_path("a/./Foo", "a/Foo")


def test_the_platform_decides_the_default() -> None:
    assert path_identity.CASE_SENSITIVE == (sys.platform not in ("win32", "darwin"))


# --- two files of one name in two cases ---------------------------------------


def test_siblings_of_foo_leave_Foo_alone(sensitive) -> None:
    _make(sensitive, "foo")
    _make(sensitive, "Foo")
    siblings = assign_siblings(sensitive)
    assert {p.name for p in siblings["foo"]} == {"foo" + s for s in SIDECARS}
    assert {p.name for p in siblings["Foo"]} == {"Foo" + s for s in SIDECARS}
    # What a delete, an archive move or a replaced version sends to the Recycle Bin.
    assert {p.name for p in stem_files(sensitive / "foo.safetensors")} == {"foo" + s for s in SIDECARS}


def test_renaming_foo_leaves_Foo_and_its_sidecars_alone(sensitive, tmp_path) -> None:
    _make(sensitive, "foo")
    _make(sensitive, "Foo")
    row = _row(sensitive, "foo", "bar")
    steps, skipped = plan([row], {row["key"]}, set())
    assert skipped == []
    result = apply_steps(steps, RenameJournal(tmp_path / "data"), trash=_no_trash)
    assert result["done"] == [row["key"]]
    assert set(os.listdir(sensitive)) == {"bar" + s for s in SIDECARS} | {"Foo" + s for s in SIDECARS}
    assert (sensitive / "Foo.safetensors").read_text(encoding="utf-8") == "Foo.safetensors"


def test_deleting_foo_leaves_Foo_alone(sensitive, tmp_path) -> None:
    _make(sensitive, "foo")
    _make(sensitive, "Foo")
    row = _row(sensitive, "foo", "foo")
    steps, skipped = plan([row], set(), set(), {row["key"]})
    assert skipped == []
    assert {Path(step["from"]).name for step in steps} == {"foo" + s for s in SIDECARS}
    apply_steps(steps, RenameJournal(tmp_path / "data"), trash=os.remove)
    assert set(os.listdir(sensitive)) == {"Foo" + s for s in SIDECARS}


def test_removing_a_duplicate_leaves_the_other_spelling_alone(sensitive, tmp_path) -> None:
    for stem in ("copy", "Copy", "keep"):
        _make(sensitive, stem)
        (sensitive / (stem + ".safetensors")).write_bytes(b"same weights")
    survivor = _row(sensitive, "keep", "keep")
    dup = _row(sensitive, "copy", "copy", status="duplicate", survivorKey=survivor["key"])
    steps, _skipped = plan([survivor, dup], set(), {dup["key"]})
    assert {Path(step["from"]).name for step in steps} == {"copy" + s for s in SIDECARS}


def test_two_files_in_two_cases_are_two_rows(sensitive) -> None:
    (sensitive / "foo.safetensors").write_text("a", encoding="utf-8")
    (sensitive / "Foo.safetensors").write_text("b", encoding="utf-8")
    (sensitive / "FOO.civitai.info").write_text(INFO, encoding="utf-8")
    files = list_model_files({"lora": [sensitive]}, include_sidecar_only=True)
    assert sorted(Path(item["path"]).name for item in files) == ["FOO.civitai.info", "Foo.safetensors", "foo.safetensors"]
    assert path_file_key(sensitive / "foo.safetensors") != path_file_key(sensitive / "Foo.safetensors")
    assert _disk_stems(sensitive) == {"foo", "Foo", "FOO"}


def test_an_info_of_another_spelling_has_no_weights(sensitive) -> None:
    (sensitive / "foo.safetensors").write_text("a", encoding="utf-8")
    (sensitive / "Foo.civitai.info").write_text(INFO, encoding="utf-8")
    assert [entry["id"] for entry in missing_loras([sensitive])] == ["Foo.safetensors"]


def test_the_archive_lists_each_spelling_on_its_own(sensitive) -> None:
    archive = sensitive / "loras"
    archive.mkdir()
    (archive / "foo.safetensors").write_text("a", encoding="utf-8")
    (archive / "Foo.safetensors").write_text("b", encoding="utf-8")
    (archive / "foo.civitai.info").write_text(INFO, encoding="utf-8")
    entries, summary = list_archive_entries(sensitive, {"lora": [Path("D:/models/loras")]})
    assert [Path(entry["filePath"]).name for entry in entries] == ["foo.safetensors"]
    assert summary["withoutInfo"] == 1  # Foo.safetensors


def test_a_new_name_differing_only_in_case_is_free(sensitive) -> None:
    _make(sensitive, "foo")
    assert not stem_taken(sensitive, "Foo")
    assert stem_taken(sensitive, "foo")
    assert taken_name([sensitive / "Foo.safetensors"], sensitive) == ""
    check = _FolderCheck([])
    row = {"folder": str(sensitive), "newStem": "Foo", "flags": [], "selected": True}
    check.place(row)
    assert "exists" not in row["flags"]


def test_two_new_names_differing_only_in_case_do_not_collide(sensitive) -> None:
    rows = [
        {"folder": str(sensitive), "oldStem": old, "newStem": new, "status": "change", "flags": []}
        for old, new in (("a", "Foo"), ("b", "foo"))
    ]
    recheck_folder_flags(rows, {path_key(str(sensitive))}, {path_key(str(sensitive)): set()})
    assert [row["flags"] for row in rows] == [[], []]


def test_a_rename_onto_another_spelling_of_a_file_that_is_there_works(sensitive, tmp_path) -> None:
    _make(sensitive, "a")
    _make(sensitive, "foo")
    row = _row(sensitive, "a", "Foo")
    steps, _skipped = plan([row], {row["key"]}, set())
    result = apply_steps(steps, RenameJournal(tmp_path / "data"), trash=_no_trash)
    assert result["done"] == [row["key"]]
    assert set(os.listdir(sensitive)) == {"Foo" + s for s in SIDECARS} | {"foo" + s for s in SIDECARS}


def test_a_case_only_rename_onto_a_second_file_is_refused(sensitive, tmp_path) -> None:
    _make(sensitive, "foo")
    _make(sensitive, "Foo")
    row = _row(sensitive, "foo", "Foo")
    steps, _skipped = plan([row], {row["key"]}, set())
    result = apply_steps(steps, RenameJournal(tmp_path / "data"), trash=_no_trash)
    assert result["done"] == []
    assert [s["reason"] for s in result["skipped"]] == [TARGET_EXISTS]
    for stem in ("foo", "Foo"):
        assert (sensitive / (stem + ".safetensors")).read_text(encoding="utf-8") == stem + ".safetensors"


def test_finish_does_not_take_a_second_file_for_a_case_only_rename_done(sensitive, tmp_path) -> None:
    _make(sensitive, "foo", (".safetensors",))
    _make(sensitive, "Foo", (".safetensors",))
    journal = RenameJournal(tmp_path / "data")
    journal.begin(
        [{"kind": "rename", "from": str(sensitive / "foo.safetensors"), "to": str(sensitive / "Foo.safetensors"),
          "entry": "foo", "suffix": ".safetensors"}]
    )
    result = recover(RenameJournal(tmp_path / "data"), "finish")
    assert result["done"] == []
    assert [s["reason"] for s in result["skipped"]] == [TARGET_EXISTS]
    assert RenameJournal(tmp_path / "data").pending() is not None  # foo was never renamed
    for stem in ("foo", "Foo"):
        assert (sensitive / (stem + ".safetensors")).read_text(encoding="utf-8") == stem + ".safetensors"


# --- a case-insensitive drive on a case-sensitive platform --------------------


def test_the_drive_refuses_a_target_the_key_calls_free(ntfs_on_linux, tmp_path) -> None:
    _make(ntfs_on_linux, "a")
    _make(ntfs_on_linux, "foo")
    row = _row(ntfs_on_linux, "a", "Foo")
    steps, _skipped = plan([row], {row["key"]}, set())
    result = apply_steps(steps, RenameJournal(tmp_path / "data"), trash=_no_trash)
    assert result["done"] == []
    assert [s["reason"] for s in result["skipped"]] == [TARGET_EXISTS]
    assert (ntfs_on_linux / "foo.safetensors").read_text(encoding="utf-8") == "foo.safetensors"


def test_a_case_only_rename_still_works_on_such_a_drive(ntfs_on_linux, tmp_path) -> None:
    _make(ntfs_on_linux, "Foo")
    row = _row(ntfs_on_linux, "Foo", "foo")
    steps, _skipped = plan([row], {row["key"]}, set())
    result = apply_steps(steps, RenameJournal(tmp_path / "data"), trash=_no_trash)
    assert result["done"] == [row["key"]]
    assert set(os.listdir(ntfs_on_linux)) == {"foo" + s for s in SIDECARS}


def test_a_download_name_is_taken_when_the_drive_says_so(ntfs_on_linux) -> None:
    (ntfs_on_linux / "Foo.civitai.info").write_text(INFO, encoding="utf-8")
    # Its info file would be written over Foo.civitai.info.
    assert stem_taken(ntfs_on_linux, "foo")


def _no_trash(path):  # pragma: no cover - rename-only tests never remove a file
    raise AssertionError(f"unexpected trash of {path}")
