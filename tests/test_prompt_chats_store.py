"""The saved chats: two pages that save the one chat file in any order lose nothing."""

import asyncio
import importlib.util
import json
import threading
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAY = 24 * 3600 * 1000
NOW = 1_800_000_000_000


def _load():
    spec = importlib.util.spec_from_file_location(
        "warppipe_prompt_writer_chats_under_test", PROJECT_ROOT / "prompt_writer.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pw = _load()


def chat(chat_id, rev=None, **fields):
    out = {"id": chat_id, "title": chat_id, "versions": [], **fields}
    if rev is not None:
        out["rev"] = rev
    return out


def store(*chats, deleted=None):
    out = {"version": 1, "chats": list(chats)}
    if deleted is not None:
        out["deleted"] = deleted
    return out


def ids(merged):
    return [c["id"] for c in merged["chats"]]


def test_a_chat_only_one_side_has_is_kept():
    merged = pw.merge_chat_stores(store(chat("a", 5)), store(chat("b", 6)), NOW)
    assert ids(merged) == ["a", "b"]
    assert merged["deleted"] == {}


def test_the_newer_copy_of_a_chat_wins_whichever_side_it_is_on():
    old, new = chat("a", 5, title="old"), chat("a", 9, title="new")
    assert pw.merge_chat_stores(store(old), store(new), NOW)["chats"] == [new]
    assert pw.merge_chat_stores(store(new), store(old), NOW)["chats"] == [new]


def test_a_tie_keeps_the_copy_on_disk():
    disk, page = chat("a", 5, title="disk"), chat("a", 5, title="page")
    assert pw.merge_chat_stores(store(disk), store(page), NOW)["chats"] == [disk]


def test_a_file_from_before_revisions_merges():
    # Old files have no rev and no tombstones: every chat counts as the oldest.
    legacy = store(chat("a"), chat("b"))
    merged = pw.merge_chat_stores(legacy, store(chat("a", 3, title="edited")), NOW)
    assert merged["chats"] == [chat("a", 3, title="edited"), chat("b")]


def test_a_deleted_chat_is_not_brought_back_by_a_stale_copy():
    page = store(chat("a", NOW - 9))  # the other tab still holds the chat, unchanged
    disk = store(deleted={"a": NOW - 5})
    merged = pw.merge_chat_stores(disk, page, NOW)
    assert merged["chats"] == []
    assert merged["deleted"] == {"a": NOW - 5}


def test_a_chat_changed_after_it_was_deleted_elsewhere_stays():
    merged = pw.merge_chat_stores(store(deleted={"a": 7}), store(chat("a", 8)), NOW)
    assert ids(merged) == ["a"]
    assert merged["deleted"] == {}


def test_old_tombstones_are_dropped():
    deleted = {"old": NOW - 61 * DAY, "recent": NOW - 59 * DAY}
    merged = pw.merge_chat_stores(store(deleted=deleted), store(), NOW)
    assert merged["deleted"] == {"recent": NOW - 59 * DAY}


def test_fields_this_version_does_not_know_are_kept():
    future = chat("a", 5, somethingNew={"x": 1})
    assert pw.merge_chat_stores(store(), store(future), NOW)["chats"] == [future]


def test_junk_chats_and_tombstones_are_left_out():
    junk = store("x", {"id": ""}, {"no": "id"}, chat("a", True), deleted={"b": "x", "": 3})
    merged = pw.merge_chat_stores(store(), junk, NOW)
    assert merged["chats"] == [chat("a", True)]
    assert merged["deleted"] == {}


# --- the file ---------------------------------------------------------------


def test_two_pages_that_saved_in_turn_lose_nothing(tmp_path):
    path = tmp_path / "default" / pw.CHATS_FILE_NAME
    path.parent.mkdir()
    path.write_text(json.dumps(store(chat("a"))), encoding="utf-8")
    # Both tabs loaded {a}; one adds b, the other adds c and deletes a.
    pw.save_chats(path, store(chat("a"), chat("b", 10)), NOW)
    out = pw.save_chats(path, store(chat("c", 11), deleted={"a": 11}), NOW)
    assert ids(out) == ["b", "c"]
    assert json.loads(path.read_text(encoding="utf-8")) == out
    # Only the file is left: no temporary file behind it.
    assert [p.name for p in path.parent.iterdir()] == [pw.CHATS_FILE_NAME]


def test_the_first_save_makes_the_file(tmp_path):
    path = tmp_path / "default" / pw.CHATS_FILE_NAME
    out = pw.save_chats(path, store(chat("a", 1)), NOW)
    assert out == {"version": 1, "chats": [chat("a", 1)], "deleted": {}}
    assert json.loads(path.read_text(encoding="utf-8")) == out


@pytest.mark.parametrize("text", ["{not json", json.dumps({"version": 2, "chats": []}), "[]"])
def test_a_file_this_version_cannot_read_is_never_written_over(tmp_path, text):
    path = tmp_path / pw.CHATS_FILE_NAME
    path.write_text(text, encoding="utf-8")
    with pytest.raises(pw.PromptWriterError, match="left as it is"):
        pw.save_chats(path, store(chat("a", 1)), NOW)
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("body", [None, [], {}, {"version": 2, "chats": []}, {"version": 1}])
def test_a_page_must_send_a_chat_store(tmp_path, body):
    with pytest.raises(pw.PromptWriterError):
        pw.save_chats(tmp_path / pw.CHATS_FILE_NAME, body, NOW)


def test_saves_at_the_same_time_all_land(tmp_path):
    path = tmp_path / pw.CHATS_FILE_NAME
    threads = [
        threading.Thread(target=pw.save_chats, args=(path, store(chat(f"c{i}", i + 1)), NOW))
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(ids(saved)) == sorted(f"c{i}" for i in range(8))


# --- the route --------------------------------------------------------------


class _Routes:
    def __init__(self):
        self.handlers = {}

    def _add(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def get(self, path):
        return self._add("GET", path)

    def post(self, path):
        return self._add("POST", path)


def _route(tmp_path, chats_path):
    routes = _Routes()
    pw.register(
        routes,
        web,
        user_dir=str(tmp_path),
        settings_loader=lambda request: {},
        image_path=lambda name: None,
        guard=lambda handler: handler,
        chats_path=chats_path,
    )
    return routes.handlers[("POST", "/warp-studio/llm/chats")]


def _post(handler, body):
    async def run():
        request = make_mocked_request(
            "POST", "/warp-studio/llm/chats", headers={"Content-Type": "application/json"}
        )

        async def read_json():
            return body

        request.json = read_json
        return await handler(request)

    response = asyncio.run(run())
    return json.loads(response.body)


def test_the_route_merges_and_answers_with_the_merged_chats(tmp_path):
    path = tmp_path / pw.CHATS_FILE_NAME
    path.write_text(json.dumps(store(chat("a", 2))), encoding="utf-8")
    handler = _route(tmp_path, lambda request: str(path))
    out = _post(handler, store(chat("b", 3)))
    assert ids(out["store"]) == ["a", "b"]
    assert json.loads(path.read_text(encoding="utf-8")) == out["store"]


def test_the_route_says_why_it_could_not_save(tmp_path):
    def unknown_user(request):
        raise KeyError("Unknown user: x")

    assert "error" in _post(_route(tmp_path, unknown_user), store())
    assert "error" in _post(_route(tmp_path, lambda request: None), store())
    assert "error" in _post(_route(tmp_path, lambda request: str(tmp_path / "f.json")), [])
