"""The Gallery and Prompt Writer routes: who may call them, and which files they may name."""

import asyncio
import os
import sys
import types

import pytest
from aiohttp.test_utils import make_mocked_request

HOST = "127.0.0.1:8188"
PANEL = {"Host": HOST, "X-Civitai-Updater": "1"}
# The one route an <img> calls, which therefore cannot ask for the header.
UNGUARDED = {("GET", "/warp-studio/lora/thumbnail")}


class _Routes:
    """Records handlers the way aiohttp's RouteTableDef registers them."""

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


class _UserManager:
    def get_request_user_filepath(self, request, name):
        raise KeyError(name)


def _register(warp_pipe, monkeypatch, tmp_path):
    routes = _Routes()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(routes=routes, user_manager=_UserManager())
    )
    monkeypatch.setitem(sys.modules, "server", server)
    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_user_directory = lambda: str(tmp_path)
    folder_paths.get_filename_list = lambda kind: []
    folder_paths.get_full_path = lambda kind, name: None
    folder_paths.get_folder_paths = lambda kind: [str(tmp_path / kind)]
    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    warp_pipe._register_routes()
    return routes.handlers


def _call(handler, method, path, headers):
    request = make_mocked_request(method, path, headers=headers)
    return asyncio.run(handler(request))


def test_every_json_route_refuses_a_request_without_the_panel_header(warp_pipe, monkeypatch, tmp_path):
    handlers = _register(warp_pipe, monkeypatch, tmp_path)
    llm = {key for key in handlers if key[1].startswith("/warp-studio/llm/")}
    assert len(llm) == 7, "the Prompt Writer routes were not registered"

    for (method, path), handler in handlers.items():
        if (method, path) in UNGUARDED:
            continue
        # What another web page can send: no custom header, a text/plain body.
        response = _call(handler, method, path, {"Host": HOST, "Content-Type": "text/plain"})
        assert response.status == 403, f"{method} {path} answered without the header"


def test_a_cross_site_request_is_refused_even_with_the_header(warp_pipe, monkeypatch, tmp_path):
    handlers = _register(warp_pipe, monkeypatch, tmp_path)
    handler = handlers[("POST", "/warp-studio/llm/key")]

    response = _call(
        handler,
        "POST",
        "/warp-studio/llm/key",
        {**PANEL, "Content-Type": "application/json", "Origin": "https://example.com"},
    )

    assert response.status == 403
    assert not (tmp_path / "warp-studio" / "llm_keys.json").exists()


def test_the_panel_still_reaches_the_routes(warp_pipe, monkeypatch, tmp_path):
    handlers = _register(warp_pipe, monkeypatch, tmp_path)

    loras = _call(handlers[("GET", "/warp-studio/loras")], "GET", "/warp-studio/loras", PANEL)
    prompts = _call(
        handlers[("GET", "/warp-studio/llm/system_prompts")], "GET", "/warp-studio/llm/system_prompts", PANEL
    )

    assert loras.status == 200
    assert prompts.status == 200


def test_only_the_thumbnail_route_goes_without_the_header(warp_pipe, monkeypatch, tmp_path):
    handlers = _register(warp_pipe, monkeypatch, tmp_path)

    response = _call(
        handlers[("GET", "/warp-studio/lora/thumbnail")],
        "GET",
        "/warp-studio/lora/thumbnail?name=none.safetensors&kind=loras",
        {"Host": HOST},
    )

    assert response.status == 404  # looked up, found nothing: not refused


def _naive_folders(monkeypatch, root):
    """A folder_paths whose lookup joins blindly, so only our own checks keep names inside."""
    module = types.ModuleType("folder_paths")
    module.get_full_path = lambda kind, name: os.path.join(str(root), name)
    module.get_folder_paths = lambda kind: [str(root)]
    monkeypatch.setitem(sys.modules, "folder_paths", module)


@pytest.mark.parametrize(
    "name",
    [
        "../secret.safetensors",
        "sub/../../secret.safetensors",
        "sub\\..\\..\\secret.safetensors",
        "/etc/passwd",
        "\\\\server\\share\\x.safetensors",
        "C:/Windows/win.ini",
        "C:x.safetensors",
        "bad\0name.safetensors",
        "",
        None,
    ],
)
def test_a_name_that_leaves_the_model_folder_resolves_to_nothing(warp_pipe, monkeypatch, tmp_path, name):
    _naive_folders(monkeypatch, tmp_path / "loras")

    assert warp_pipe._resolve_model_path("loras", name) is None
    assert warp_pipe.find_model_path(name) is None


def test_a_name_inside_the_folder_still_resolves(warp_pipe, monkeypatch, tmp_path):
    root = tmp_path / "loras"
    _naive_folders(monkeypatch, root)

    found = warp_pipe._resolve_model_path("loras", "sdxl/maker - glow - v1..2 (sdxl).safetensors")

    assert found == os.path.join(str(root), "sdxl/maker - glow - v1..2 (sdxl).safetensors")


def test_a_lookup_that_answers_outside_the_folders_is_refused(warp_pipe, monkeypatch, tmp_path):
    module = types.ModuleType("folder_paths")
    module.get_full_path = lambda kind, name: str(tmp_path / "elsewhere" / name)
    module.get_folder_paths = lambda kind: [str(tmp_path / "loras")]
    monkeypatch.setitem(sys.modules, "folder_paths", module)

    assert warp_pipe._resolve_model_path("loras", "x.safetensors") is None


def test_a_thumbnail_is_never_served_from_outside_the_model_folders(warp_pipe, monkeypatch, tmp_path):
    outside = tmp_path / "secret.preview.png"
    outside.write_bytes(b"png")
    (tmp_path / "secret.safetensors").write_bytes(b"w")
    _naive_folders(monkeypatch, tmp_path / "loras")

    assert warp_pipe.thumbnail_source("../secret.safetensors", "loras") is None
