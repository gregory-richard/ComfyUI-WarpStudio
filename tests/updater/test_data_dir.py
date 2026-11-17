from __future__ import annotations

import sys
import types
from pathlib import Path

from comfy_updater.plugin import data_dir_for


def test_data_dir_is_in_the_user_folder(tmp_path, monkeypatch):
    fake = types.ModuleType("folder_paths")
    fake.get_user_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", fake)
    assert data_dir_for(Path("/somewhere/root")) == tmp_path / "warp-studio" / "updater"


def test_data_dir_falls_back_to_the_package(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "folder_paths", None)  # makes the import fail
    assert data_dir_for(tmp_path) == tmp_path / ".civitai_updater"
