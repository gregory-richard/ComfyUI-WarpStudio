import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "Warp",
    "Unwarp",
    "Warp Provider",
    "FD Scheduler Adapter",
    "Dead End",
    "Warp Send",
    "Warp Compare",
    "Save Image Civitai",
    "Warp Lora Prompt",
    "CivitaiUpdaterStatus",
}


def test_v1_exports_every_node(package_loader):
    package = package_loader()
    assert set(package.NODE_CLASS_MAPPINGS) == EXPECTED
    assert set(package.NODE_DISPLAY_NAME_MAPPINGS) == EXPECTED
    assert package.WEB_DIRECTORY == "./web"


def test_v3_lists_every_node(package_loader):
    package = package_loader(enable_v3=True)
    assert not hasattr(package, "NODE_CLASS_MAPPINGS")
    extension = asyncio.run(package.comfy_entrypoint())
    nodes = asyncio.run(extension.get_node_list())
    assert {node.GET_SCHEMA().node_id for node in nodes} == EXPECTED


def test_v3_also_turns_on_with_the_new_variable(package_loader, monkeypatch):
    monkeypatch.setenv("WARPSTUDIO_ENABLE_V3", "1")
    package = package_loader()
    assert not hasattr(package, "NODE_CLASS_MAPPINGS")
    assert hasattr(package, "comfy_entrypoint")


def test_pyproject_identity():
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "warp-studio"' in text and 'version = "1.0.0"' in text
    assert 'DisplayName = "Warp Studio"' in text
    assert "simplifies model management, streamlines workflows, and makes prompting easier" in text


def test_the_package_ships_the_updater_prompt():
    """naming_ai.py reads prompts/naming_system.txt at runtime; a wheel without
    it breaks AI naming."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"warp_studio.comfy_updater" = ["prompts/*.txt"]' in text
    assert (PROJECT_ROOT / "comfy_updater" / "prompts" / "naming_system.txt").is_file()


def _fake_folder_paths(monkeypatch, tmp_path, folders):
    import sys
    import types

    custom = tmp_path / "custom_nodes"
    custom.mkdir()
    for folder, name, version in folders:
        path = custom / folder
        path.mkdir()
        (path / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
        )
    user = tmp_path / "user"
    user.mkdir()
    fake = types.ModuleType("folder_paths")
    fake.get_folder_paths = lambda kind: [str(custom)]
    fake.get_user_directory = lambda: str(user)
    monkeypatch.setitem(sys.modules, "folder_paths", fake)
    return custom, user


def test_package_stands_down_when_old_warppipe_present(package_loader, monkeypatch, tmp_path):
    import sys

    _, user = _fake_folder_paths(monkeypatch, tmp_path, [("warppipe", "warppipe", "5.0.0")])
    package = package_loader()
    assert package.NODE_CLASS_MAPPINGS == {}
    assert package.NODE_DISPLAY_NAME_MAPPINGS == {}
    assert package.WEB_DIRECTORY == "./standdown"
    assert f"{package.__name__}.warp_pipe" not in sys.modules
    assert not (user / "warp-studio" / "migrated.json").exists()


def test_package_loads_when_detection_raises(package_loader, monkeypatch, tmp_path):
    import pathlib

    _fake_folder_paths(monkeypatch, tmp_path, [("warppipe", "warppipe", "5.0.0")])
    real = pathlib.Path.iterdir

    def boom(self):
        if self.name == "custom_nodes":
            raise PermissionError("nope")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", boom)
    package = package_loader()
    assert set(package.NODE_CLASS_MAPPINGS) == EXPECTED
