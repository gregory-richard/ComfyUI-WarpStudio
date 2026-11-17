import json
import shutil

import pytest

from studio import migration
from studio.migration import MARKER, find_old_updater_data, migrate

SETTINGS = {
    "WarpPipe.PromptWriter.BaseUrl": "http://x",
    "CivitaiUpdater.APIKey": "k",
    "Comfy.Foo": 1,
    "WarpPipe.CodeEditor": True,
    "WarpStudio.CodeEditor": False,
}


def make_updater(root, name="civitai-updater", config=b'{"a": 1}'):
    pkg = root / name
    (pkg / "comfy_updater").mkdir(parents=True)
    (pkg / "comfy_updater" / "plugin.py").write_text("# plugin\n")
    data = pkg / ".civitai_updater"
    data.mkdir()
    (data / "config.json").write_bytes(config)
    (data / "last_dry_run.json").write_bytes(b'{"dry": true}')
    return pkg


@pytest.fixture
def env(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    nodes = tmp_path / "custom_nodes"
    nodes.mkdir()
    own = nodes / "ComfyUI-WarpStudio"
    own.mkdir()
    return user, nodes, own


def test_copies_updater_data_folder(env):
    user, nodes, own = env
    pkg = make_updater(nodes)
    report = migrate(user, [nodes], own)
    assert report.errors == []
    target = user / "warp-studio" / "updater"
    assert (target / "config.json").read_bytes() == b'{"a": 1}'
    assert (target / "last_dry_run.json").read_bytes() == b'{"dry": true}'
    assert (pkg / ".civitai_updater" / "config.json").exists()
    assert (pkg / ".civitai_updater" / "last_dry_run.json").exists()


def test_finds_a_disabled_updater(env):
    user, nodes, own = env
    (nodes / ".disabled").mkdir()
    make_updater(nodes / ".disabled")
    migrate(user, [nodes], own)
    assert (user / "warp-studio" / "updater" / "config.json").exists()


def test_finds_a_dot_disabled_named_folder(env):
    user, nodes, own = env
    make_updater(nodes, name="civitai-updater.disabled")
    migrate(user, [nodes], own)
    assert (user / "warp-studio" / "updater" / "config.json").exists()


def test_ignores_own_folder(env):
    user, nodes, own = env
    (own / "comfy_updater").mkdir()
    (own / "comfy_updater" / "plugin.py").write_text("# own\n")
    (own / ".civitai_updater").mkdir()
    (own / ".civitai_updater" / "config.json").write_text("{}")
    assert find_old_updater_data([nodes], own) is None
    migrate(user, [nodes], own)
    assert not (user / "warp-studio" / "updater").exists()


def test_ignores_another_warp_studio_checkout(env):
    _, nodes, own = env
    other = make_updater(nodes, name="ComfyUI-WarpStudio-copy")
    (other / "pyproject.toml").write_text('[project]\nname = "warp-studio"\nversion = "1"\n')
    assert find_old_updater_data([nodes], own) is None


def test_takes_newest_when_several(env):
    import os

    _, nodes, own = env
    old = make_updater(nodes, name="a-updater")
    new = make_updater(nodes, name="b-updater")
    for f in (old / ".civitai_updater").iterdir():
        os.utime(f, (1000, 1000))
    assert find_old_updater_data([nodes], own) == new / ".civitai_updater"


def test_copies_warppipe_user_files(env):
    user, nodes, own = env
    (user / "warppipe_llm_keys.json").write_text('{"k": 1}')
    (user / "warppipe_llm_log.jsonl").write_text("{}\n")
    (user / "warppipe_hashes.json").write_text('{"h": 2}')
    migrate(user, [nodes], own)
    studio = user / "warp-studio"
    assert (studio / "llm_keys.json").read_text() == '{"k": 1}'
    assert (studio / "llm_log.jsonl").read_text() == "{}\n"
    assert (studio / "hashes.json").read_text() == '{"h": 2}'
    assert (user / "warppipe_llm_keys.json").exists()


def test_copies_chats_and_settings_per_user(env):
    user, nodes, own = env
    for name in ("default", "second"):
        (user / name).mkdir()
        (user / name / "warppipe_prompt_chats.json").write_text('{"chats": []}')
        (user / name / "comfy.settings.json").write_text(json.dumps(SETTINGS))
    migrate(user, [nodes], own)
    for name in ("default", "second"):
        settings = json.loads((user / name / "comfy.settings.json").read_text())
        assert settings["WarpStudio.PromptWriter.BaseUrl"] == "http://x"
        assert settings["WarpStudio.Updater.APIKey"] == "k"
        assert settings["WarpStudio.CodeEditor"] is False
        for key, value in SETTINGS.items():
            if key != "WarpStudio.CodeEditor":
                assert settings[key] == value
        assert (user / name / "warp_studio_prompt_chats.json").read_text() == '{"chats": []}'
        assert (user / name / "warppipe_prompt_chats.json").exists()


def test_broken_settings_file_skips_that_user(env):
    user, nodes, own = env
    (user / "default").mkdir()
    (user / "default" / "comfy.settings.json").write_text("{not json")
    (user / "second").mkdir()
    (user / "second" / "comfy.settings.json").write_text(json.dumps(SETTINGS))
    report = migrate(user, [nodes], own)
    assert (user / "default" / "comfy.settings.json").read_text() == "{not json"
    assert "WarpStudio.Updater.APIKey" in json.loads(
        (user / "second" / "comfy.settings.json").read_text()
    )
    assert any("default" in e and "comfy.settings.json" in e for e in report.errors)
    assert not (user / MARKER).exists()


def test_runs_once(env):
    user, nodes, own = env
    make_updater(nodes)
    migrate(user, [nodes], own)
    config = user / "warp-studio" / "updater" / "config.json"
    config.write_text("edited")
    assert migrate(user, [nodes], own) is None
    assert config.read_text() == "edited"


def test_never_overwrites_an_existing_target(env):
    user, nodes, own = env
    (user / "warppipe_llm_keys.json").write_text("old")
    (user / "warp-studio").mkdir()
    (user / "warp-studio" / "llm_keys.json").write_text("new")
    report = migrate(user, [nodes], own)
    assert (user / "warp-studio" / "llm_keys.json").read_text() == "new"
    assert any(s.endswith("llm_keys.json") for s in report.skipped)


def test_failed_copy_retries_next_start(env, monkeypatch):
    user, nodes, own = env
    make_updater(nodes)
    real = shutil.copy2
    calls = {"n": 0}

    def flaky(src, dst, *a, **k):
        if str(src).endswith("config.json") and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("disk hiccup")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", flaky)
    report = migrate(user, [nodes], own)
    assert report.errors
    assert not (user / MARKER).exists()
    report = migrate(user, [nodes], own)
    assert report.errors == []
    assert (user / "warp-studio" / "updater" / "config.json").read_bytes() == b'{"a": 1}'
    assert (user / "warp-studio" / "updater" / "last_dry_run.json").exists()
    assert (user / MARKER).exists()


def test_fresh_install_copies_nothing(env):
    user, nodes, own = env
    report = migrate(user, [nodes], own)
    assert report.copied == [] and report.skipped == [] and report.errors == []
    assert (user / MARKER).exists()
    assert migration.MARKER == MARKER


def test_failed_updater_copy_is_retried_after_defaults_were_written(env, monkeypatch):
    user, nodes, own = env
    make_updater(nodes)
    real = shutil.copy2
    state = {"fail": True}

    def flaky(src, dst, *a, **k):
        if state["fail"] and str(src).endswith("config.json"):
            raise OSError("disk hiccup")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", flaky)
    report = migrate(user, [nodes], own)
    studio = user / "warp-studio"
    assert report.errors
    assert (studio / "updater.pending").exists()
    assert not (user / MARKER).exists()

    state["fail"] = False
    (studio / "updater").mkdir(exist_ok=True)
    (studio / "updater" / "config.json").write_text('{"default": true}')

    report = migrate(user, [nodes], own)
    assert report.errors == []
    assert (studio / "updater" / "config.json").read_bytes() == b'{"a": 1}'
    asides = list(studio.glob("updater.before-migration-*"))
    assert len(asides) == 1
    assert (asides[0] / "config.json").read_text() == '{"default": true}'
    assert not (studio / "updater.pending").exists()
    assert (user / MARKER).exists()


def test_settings_are_written_as_ascii(env):
    user, nodes, own = env
    (user / "default").mkdir()
    values = {"WarpPipe.Name": "Łukasz", "WarpPipe.Glyph": ""}
    (user / "default" / "comfy.settings.json").write_text(json.dumps(values, indent=4))
    migrate(user, [nodes], own)
    raw = (user / "default" / "comfy.settings.json").read_bytes()
    raw.decode("ascii")
    data = json.loads(raw)
    assert data["WarpStudio.Name"] == values["WarpPipe.Name"]
    assert data["WarpStudio.Glyph"] == values["WarpPipe.Glyph"]
