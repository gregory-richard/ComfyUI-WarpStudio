"""Guards on the Warp Studio naming: old names must not survive in code."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Files that hold the old names on purpose (they migrate data from the old packages).
EXCLUDED = {
    PROJECT_ROOT / "studio" / "migration.py",
    PROJECT_ROOT / "studio" / "legacy.py",
    PROJECT_ROOT / "web" / "storage_migration.js",
}

OLD_ROUTES = ["/warppipe/", "/civitai-updater"]


@pytest.fixture(scope="module")
def sources():
    """(Path, text) for the root *.py, comfy_updater/*.py and web/*.js files."""
    paths = [
        *PROJECT_ROOT.glob("*.py"),
        *(PROJECT_ROOT / "comfy_updater").glob("*.py"),
        *(PROJECT_ROOT / "web").glob("*.js"),
    ]
    return [
        (path, path.read_text(encoding="utf-8")) for path in sorted(paths) if path not in EXCLUDED
    ]


def test_no_old_route_prefix(sources):
    hits = [(path.name, old) for path, text in sources for old in OLD_ROUTES if old in text]
    assert hits == []


OLD_NAMES = [
    '"WarpPipe.',
    '"CivitaiUpdater.',
    'name: "warppipe.',
    "wpw-style",
    "cu-styles",
    "window.WarpPipe",
    "civitai-updater.rename",
    "warppipe_prompt_chats",
    "warppipe_llm_",
    "warppipe_hashes",
    'category: ["WarpPipe"',
    'category: ["Civitai Updater"',
    'TAB_ID = "civitai-updater"',
    '"civitai-updater.ui"',
    "cu-rn-styles",
]


def test_no_old_unique_names(sources):
    hits = [(path.name, old) for path, text in sources for old in OLD_NAMES if old in text]
    assert hits == []


def test_workflow_properties_keep_their_names(sources):
    text = "".join(t for _, t in sources)
    for prop in ("warppipeWidgets", "warppipeFolded", "warppipeListRows"):
        assert prop in text
