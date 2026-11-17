"""Run the renamer's AI through ComfyUI's own queue.

The renamer never calls model code itself: ComfyUI's model management is not
thread-safe. Instead it posts a small API-format graph of core nodes
(CLIPLoader -> TextGenerateV2 -> PreviewAny) to ComfyUI's `/prompt` and reads
the generated text back from `/history/<prompt_id>`. ComfyUI loads and unloads
the model as usual and the user's own jobs interleave with the renamer's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import time
from typing import Callable, Optional
import urllib.error
import urllib.request

CLIENT_ID = "civitai-updater-renamer"
_WILDCARD_HOSTS = {"0.0.0.0", "::"}
_HEADER_LIMIT = 100 * 1024 * 1024
_REQUEST_TIMEOUT = 30

# A tensor name that only the language models Generate Text can run carry.
_NAMING_MODEL_KEYS = (
    re.compile(r"^model\.language_model\.layers\.0\.linear_attn\.A_log$"),  # Qwen3.5
    re.compile(r"^(model\.)?(language_model\.)?layers\.0\.self_attn\.q_norm\.weight$"),  # Qwen3, Qwen3-VL
    re.compile(r"^model\.layers\.0\.post_feedforward_layernorm\.weight$"),  # Gemma 3/4
)


class NamingModelError(RuntimeError):
    """ComfyUI rejected, failed or never finished a Generate Text job."""


def comfy_base_url(listen: Optional[str] = None, port: Optional[int] = None, tls: Optional[bool] = None) -> str:
    """Where this ComfyUI answers, from its own command-line arguments."""
    if listen is None or port is None or tls is None:
        try:
            from comfy.cli_args import args  # type: ignore
        except ImportError:
            args = None
        if listen is None:
            listen = getattr(args, "listen", None)
        if port is None:
            port = getattr(args, "port", None)
        if tls is None:
            tls = bool(getattr(args, "tls_keyfile", None))
    hosts = [h.strip() for h in (listen or "127.0.0.1").split(",") if h.strip()]
    host = hosts[0] if hosts else "127.0.0.1"
    if host in _WILDCARD_HOSTS or any(h in _WILDCARD_HOSTS for h in hosts):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    scheme = "https" if tls else "http"
    return f"{scheme}://{host}:{port or 8188}"


def build_graph(
    clip_name: str,
    system_prompt: str,
    messages: list[str],
    max_length: int = 160,
    max_lengths: Optional[list[int]] = None,
) -> dict:
    """One CLIPLoader and system prompt shared by a Generate Text + Preview pair per message.

    ``max_lengths`` gives each message its own answer length (a grouped question
    answers several models); otherwise every message gets ``max_length``.
    """
    graph = {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip_name, "type": "stable_diffusion"}},
        "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": system_prompt}},
    }
    for i, message in enumerate(messages):
        graph[str(10 + i)] = {"class_type": "TextGenerateV2", "inputs": {
            "clip": ["1", 0],
            "prompt": message,
            "max_length": max_lengths[i] if max_lengths and i < len(max_lengths) else max_length,
            "sampling_mode": "off",
            "thinking": False,
            "use_default_template": True,
            "mtp": "auto",
            "system_prompt": ["2", 0],
        }}
        graph[str(5000 + i)] = {"class_type": "PreviewAny", "inputs": {"source": [str(10 + i), 0]}}
    return graph


class ComfyTextGenerator:
    def __init__(
        self,
        base_url: str,
        clip_name: str,
        system_prompt: str,
        *,
        opener=urllib.request.urlopen,
        poll_seconds: float = 1.0,
        timeout_seconds: float = 900,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.clip_name = clip_name
        self.system_prompt = system_prompt
        self.opener = opener
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.should_stop = should_stop

    def generate(self, messages: list[str], max_lengths: Optional[list[int]] = None) -> list[str]:
        """Queue one job for all messages and return the answers in message order."""
        if not messages:
            return []
        graph = build_graph(self.clip_name, self.system_prompt, messages, max_lengths=max_lengths)
        prompt_id = self._queue(graph)
        entry = self._wait(prompt_id)
        outputs = entry.get("outputs") or {}
        texts = []
        for i in range(len(messages)):
            text = (outputs.get(str(5000 + i)) or {}).get("text") or [""]
            texts.append(str(text[0]))
        return texts

    def _queue(self, graph: dict) -> str:
        body = json.dumps({"prompt": graph, "client_id": CLIENT_ID}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/prompt", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self.opener(request, timeout=_REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise NamingModelError(_rejection_message(exc)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise NamingModelError(f"ComfyUI could not be reached at {self.base_url}: {exc}") from exc
        prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
        if not prompt_id:
            raise NamingModelError("ComfyUI did not return a prompt id.")
        return str(prompt_id)

    def _wait(self, prompt_id: str) -> dict:
        """Poll the history until the job ends.

        The timeout counts from when the job leaves ComfyUI's pending queue, so a
        long job of the user's ahead of it does not fail the dry run. A stop
        takes the job back out of the queue (or interrupts it, if it is running).
        """
        deadline: Optional[float] = None
        url = f"{self.base_url}/history/{prompt_id}"
        while True:
            if self.should_stop is not None and self.should_stop():
                self._withdraw(prompt_id)
                raise NamingModelError("stopped")
            try:
                with self.opener(url, timeout=_REQUEST_TIMEOUT) as response:
                    history = json.loads(response.read())
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise NamingModelError(f"ComfyUI history could not be read: {exc}") from exc
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            status = (entry or {}).get("status") or {}
            if status.get("completed") is not None:
                if status.get("status_str") == "error":
                    raise NamingModelError(_execution_message(status))
                return entry
            if deadline is None and self._queue_state(prompt_id) != "pending":
                deadline = time.monotonic() + self.timeout_seconds
            if deadline is not None and time.monotonic() >= deadline:
                raise NamingModelError(f"ComfyUI did not finish the naming job within {self.timeout_seconds:g} s.")
            time.sleep(self.poll_seconds)

    def _queue_state(self, prompt_id: str) -> str:
        """"pending", "running" or "" (not queued, or the queue could not be read)."""
        try:
            with self.opener(self.base_url + "/queue", timeout=_REQUEST_TIMEOUT) as response:
                queue = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return ""
        if not isinstance(queue, dict):
            return ""
        for state, key in (("running", "queue_running"), ("pending", "queue_pending")):
            for item in queue.get(key) or []:
                if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
                    return state
        return ""

    def _withdraw(self, prompt_id: str) -> None:
        """Delete the job if it still waits; interrupt it only if it is the one running."""
        state = self._queue_state(prompt_id)
        if state == "pending":
            self._post("/queue", {"delete": [prompt_id]})
        elif state == "running":
            # The prompt id makes a current ComfyUI interrupt only this job.
            self._post("/interrupt", {"prompt_id": prompt_id})

    def _post(self, path: str, payload: dict) -> None:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=_REQUEST_TIMEOUT) as response:
                response.read()
        except (urllib.error.URLError, OSError, ValueError):
            pass  # Stopping goes on; the job then just runs to its end in ComfyUI.


def _rejection_message(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read())
    except (OSError, ValueError):
        payload = None
    parts = []
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            parts.extend(str(error[k]) for k in ("message", "details") if error.get(k))
        elif error:
            parts.append(str(error))
        node_errors = payload.get("node_errors")
        if isinstance(node_errors, dict):
            for node in node_errors.values():
                for item in (node or {}).get("errors") or []:
                    if isinstance(item, dict):
                        parts.extend(str(item[k]) for k in ("message", "details") if item.get(k))
    detail = "; ".join(dict.fromkeys(parts)) or exc.reason or "no details"
    return f"ComfyUI rejected the naming job (HTTP {exc.code}): {detail}"


def _execution_message(status: dict) -> str:
    for message in status.get("messages") or []:
        if isinstance(message, (list, tuple)) and len(message) >= 2 and isinstance(message[1], dict):
            if message[0] == "execution_error":
                info = message[1]
                node = info.get("node_type") or "a node"
                return f"ComfyUI failed in {node}: {info.get('exception_message') or 'unknown error'}"
            if message[0] == "execution_interrupted":
                return "The naming job was cancelled in ComfyUI."
    return "ComfyUI failed the naming job."


def has_text_generation() -> bool:
    """Whether this ComfyUI has the core Generate Text node (False outside ComfyUI)."""
    try:
        import nodes  # type: ignore
    except ImportError:
        return False
    return "TextGenerateV2" in getattr(nodes, "NODE_CLASS_MAPPINGS", {})


def list_naming_models(folders: Optional[list[Path]] = None) -> list[str]:
    """Text-encoder file names whose weights are a language model Generate Text can run."""
    if folders is None:
        try:
            import folder_paths  # type: ignore
        except ImportError:
            return []
        folders = [Path(p) for p in folder_paths.get_folder_paths("text_encoders")]
    names = set()
    for folder in folders:
        # Walk like ComfyUI's folder_paths: follow symlinks and name files by
        # their native relative path, which is what CLIPLoader validates against.
        for dirpath, _subdirs, filenames in os.walk(folder, followlinks=True):
            for file_name in filenames:
                if not file_name.lower().endswith(".safetensors"):
                    continue
                path = os.path.join(dirpath, file_name)
                header = _read_header(Path(path))
                if header and any(p.match(key) for key in header for p in _NAMING_MODEL_KEYS):
                    names.add(os.path.relpath(path, folder))
    return sorted(names)


def _read_header(path: Path) -> Optional[dict]:
    """The JSON header of a safetensors file; never reads tensor data."""
    try:
        with open(path, "rb") as fh:
            prefix = fh.read(8)
            if len(prefix) != 8:
                return None
            (length,) = struct.unpack("<Q", prefix)
            if length == 0 or length > _HEADER_LIMIT:
                return None
            raw = fh.read(length)
        if len(raw) != length:
            return None
        header = json.loads(raw)
    except (OSError, ValueError):
        return None
    return header if isinstance(header, dict) else None
