import pytest

from studio.legacy import OldPackage, find_active_old_packages, stand_down_message


def make(root, folder, name, version):
    path = root / folder
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
    )
    return path


@pytest.fixture
def dirs(tmp_path):
    custom = tmp_path / "custom_nodes"
    own = custom / "warpstudio"
    own.mkdir(parents=True)
    return custom, own


def test_nothing_installed(dirs):
    custom, own = dirs
    assert find_active_old_packages([custom], own) == []


def test_warppipe_500_is_active(dirs):
    custom, own = dirs
    path = make(custom, "warppipe", "warppipe", "5.0.0")
    assert find_active_old_packages([custom], own) == [OldPackage("warppipe", "5.0.0", path)]


def test_folder_name_doesnt_matter(dirs):
    custom, own = dirs
    make(custom, "ComfyUI-WarpPipe", "warppipe", "4.1.1")
    found = find_active_old_packages([custom], own)
    assert [p.version for p in found] == ["4.1.1"]


def test_goodbye_versions_step_aside(dirs):
    custom, own = dirs
    make(custom, "a", "warppipe", "5.1.0")
    make(custom, "b", "civitai-updater", "1.10.0")
    make(custom, "c", "civitai-updater", "1.10.2")
    assert find_active_old_packages([custom], own) == []


def test_disabled_ones_dont_count(dirs):
    custom, own = dirs
    make(custom / ".disabled", "warppipe", "warppipe", "5.0.0")
    make(custom, "civitai-updater.disabled", "civitai-updater", "1.9.2")
    assert find_active_old_packages([custom], own) == []


def test_unreadable_version_counts_as_old(dirs):
    custom, own = dirs
    make(custom, "warppipe", "warppipe", "dev")
    assert len(find_active_old_packages([custom], own)) == 1


def test_own_folder_through_a_link_is_skipped(dirs):
    custom, own = dirs
    (own / "pyproject.toml").write_text(
        '[project]\nname = "warppipe"\nversion = "5.0.0"\n', encoding="utf-8"
    )
    try:
        (custom / "linked").symlink_to(own, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")
    assert find_active_old_packages([custom], own) == []


def test_message_one(tmp_path):
    msg = stand_down_message([OldPackage("warppipe", "5.0.0", tmp_path)])
    assert msg == (
        "Warp Studio is installed but switched off, because WarpPipe 5.0.0 is still "
        "installed. Update or remove it in the Manager, then restart."
    )


def test_message_two(tmp_path):
    msg = stand_down_message(
        [
            OldPackage("warppipe", "5.0.0", tmp_path),
            OldPackage("civitai-updater", "1.9.2", tmp_path),
        ]
    )
    assert msg == (
        "Warp Studio is installed but switched off, because WarpPipe 5.0.0 and "
        "Civitai Updater 1.9.2 are still installed. Update or remove them in the "
        "Manager, then restart."
    )
