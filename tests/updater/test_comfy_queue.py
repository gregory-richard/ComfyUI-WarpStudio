from __future__ import annotations

import io
import json
import os
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path

from comfy_updater.comfy_queue import (
    ComfyTextGenerator,
    NamingModelError,
    build_graph,
    comfy_base_url,
    has_text_generation,
    list_naming_models,
)

BASE = "http://127.0.0.1:8188"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    """Records requests and answers `/prompt` and `/history/<id>` from canned JSON."""

    def __init__(self, histories, prompt_status=200, prompt_body=None, queues=None):
        self.requests = []
        self.histories = list(histories)
        self.queues = list(queues or [{"queue_running": [], "queue_pending": []}])
        self.prompt_status = prompt_status
        self.prompt_body = prompt_body if prompt_body is not None else {"prompt_id": "pid-1", "number": 1}

    def __call__(self, request, timeout=None):  # noqa: ARG002
        url = request.full_url if hasattr(request, "full_url") else request
        self.requests.append(request)
        if url == BASE + "/prompt":
            body = json.dumps(self.prompt_body).encode()
            if self.prompt_status != 200:
                raise urllib.error.HTTPError(url, self.prompt_status, "Bad Request", {}, io.BytesIO(body))
            return FakeResponse(body)
        if url == BASE + "/queue" and getattr(request, "data", None) is None:
            payload = self.queues.pop(0) if len(self.queues) > 1 else self.queues[0]
            return FakeResponse(json.dumps(payload).encode())
        if url in (BASE + "/queue", BASE + "/interrupt"):
            return FakeResponse(b"{}")
        if url == BASE + "/history/pid-1":
            payload = self.histories.pop(0) if len(self.histories) > 1 else self.histories[0]
            return FakeResponse(json.dumps(payload).encode())
        raise AssertionError(f"unexpected url {url}")

    def urls(self):
        return [r.full_url if hasattr(r, "full_url") else r for r in self.requests]

    def posts(self):
        """(url, JSON body) of every POST but the prompt itself."""
        return [
            (r.full_url, json.loads(r.data) if r.data else None)
            for r in self.requests
            if hasattr(r, "full_url") and r.data is not None and r.full_url != BASE + "/prompt"
        ]


PENDING = {"queue_running": [[1, "someone-else", {}, {}, []]], "queue_pending": [[2, "pid-1", {}, {}, []]]}
RUNNING = {"queue_running": [[2, "pid-1", {}, {}, []]], "queue_pending": []}
OTHER_RUNNING = {"queue_running": [[3, "someone-else", {}, {}, []]], "queue_pending": []}


def finished(outputs, status_str="success", messages=None):
    return {"pid-1": {
        "outputs": outputs,
        "status": {"status_str": status_str, "completed": status_str == "success", "messages": messages or []},
    }}


def write_header(path: Path, keys):
    header = {k: {"dtype": "BF16", "shape": [4, 4], "data_offsets": [0, 32]} for k in keys}
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * 32)


class ComfyQueueTests(unittest.TestCase):
    def test_build_graph_wires_one_generator_and_preview_per_message(self):
        graph = build_graph("q.safetensors", "SYS", ["a", "b"])
        self.assertEqual(len(graph), 6)
        self.assertEqual(graph["1"], {"class_type": "CLIPLoader",
                                      "inputs": {"clip_name": "q.safetensors", "type": "stable_diffusion"}})
        self.assertEqual(graph["2"]["inputs"]["value"], "SYS")
        self.assertEqual(graph["11"]["inputs"]["prompt"], "b")
        self.assertEqual(graph["11"]["inputs"]["sampling_mode"], "off")
        self.assertEqual(graph["11"]["inputs"]["system_prompt"], ["2", 0])
        self.assertEqual(graph["10"]["inputs"]["max_length"], 160)
        self.assertEqual(graph["5001"], {"class_type": "PreviewAny", "inputs": {"source": ["11", 0]}})

    def test_build_graph_takes_a_max_length_per_message(self):
        graph = build_graph("q.safetensors", "SYS", ["a", "b"], max_lengths=[160, 480])
        self.assertEqual(graph["10"]["inputs"]["max_length"], 160)
        self.assertEqual(graph["11"]["inputs"]["max_length"], 480)

    def test_generate_passes_max_lengths_to_the_graph(self):
        opener = FakeOpener([{}, finished({"5000": {"text": ["x"]}, "5001": {"text": ["y"]}})])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0)
        gen.generate(["a", "b"], max_lengths=[160, 320])
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["prompt"]["11"]["inputs"]["max_length"], 320)

    def test_generate_posts_once_polls_history_and_returns_texts_in_order(self):
        opener = FakeOpener([
            {},
            finished({"5001": {"text": ["title: y"]}, "5000": {"text": ["title: x"]}}),
        ])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0)
        self.assertEqual(gen.generate(["a", "b"]), ["title: x", "title: y"])
        urls = opener.urls()
        self.assertEqual(urls.count(BASE + "/prompt"), 1)
        self.assertEqual([u for u in urls[1:] if u != BASE + "/queue"], [BASE + "/history/pid-1"] * 2)
        post = opener.requests[0]
        self.assertEqual(post.get_header("Content-type"), "application/json")
        self.assertIsNone(post.get_header("Origin"))
        body = json.loads(post.data)
        self.assertEqual(body["client_id"], "civitai-updater-renamer")
        self.assertEqual(body["prompt"]["10"]["inputs"]["prompt"], "a")

    def test_rejected_prompt_raises_with_comfy_message(self):
        opener = FakeOpener([{}], prompt_status=400,
                            prompt_body={"error": {"message": "Value not in list: clip_name"}, "node_errors": {}})
        gen = ComfyTextGenerator(BASE, "missing.safetensors", "SYS", opener=opener, poll_seconds=0)
        with self.assertRaises(NamingModelError) as ctx:
            gen.generate(["a"])
        self.assertIn("clip_name", str(ctx.exception))

    def test_execution_error_raises_with_exception_message(self):
        messages = [["execution_start", {}],
                    ["execution_error", {"node_type": "TextGenerateV2", "exception_message": "CUDA out of memory"}]]
        opener = FakeOpener([finished({}, status_str="error", messages=messages)])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0)
        with self.assertRaises(NamingModelError) as ctx:
            gen.generate(["a"])
        self.assertIn("CUDA out of memory", str(ctx.exception))

    def test_should_stop_ends_polling(self):
        opener = FakeOpener([{}])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0,
                                 should_stop=lambda: True)
        with self.assertRaises(NamingModelError) as ctx:
            gen.generate(["a"])
        self.assertEqual(str(ctx.exception), "stopped")

    def test_timeout_ends_polling(self):
        opener = FakeOpener([{}])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0,
                                 timeout_seconds=0)
        with self.assertRaises(NamingModelError):
            gen.generate(["a"])

    def test_waiting_behind_the_users_jobs_does_not_count_against_the_timeout(self):
        opener = FakeOpener(
            [{}, {}, {}, finished({"5000": {"text": ["title: x"]}})], queues=[PENDING, PENDING, PENDING, RUNNING]
        )
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0, timeout_seconds=0)
        self.assertEqual(gen.generate(["a"]), ["title: x"])

    def test_timeout_counts_once_the_job_runs(self):
        opener = FakeOpener([{}], queues=[RUNNING])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0, timeout_seconds=0)
        with self.assertRaises(NamingModelError) as ctx:
            gen.generate(["a"])
        self.assertIn("did not finish", str(ctx.exception))

    def test_unreadable_queue_starts_the_timeout(self):
        class NoQueue(FakeOpener):
            def __call__(self, request, timeout=None):
                url = request.full_url if hasattr(request, "full_url") else request
                if url == BASE + "/queue":
                    raise urllib.error.URLError("no queue endpoint")
                return super().__call__(request, timeout)

        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=NoQueue([{}]), poll_seconds=0, timeout_seconds=0)
        with self.assertRaises(NamingModelError):
            gen.generate(["a"])

    def _stop_after_queueing(self, queue):
        opener = FakeOpener([{}], queues=[queue])
        gen = ComfyTextGenerator(BASE, "q.safetensors", "SYS", opener=opener, poll_seconds=0, should_stop=lambda: True)
        with self.assertRaises(NamingModelError) as ctx:
            gen.generate(["a"])
        self.assertEqual(str(ctx.exception), "stopped")
        return opener.posts()

    def test_stop_deletes_our_pending_prompt(self):
        self.assertEqual([(BASE + "/queue", {"delete": ["pid-1"]})], self._stop_after_queueing(PENDING))

    def test_stop_interrupts_only_our_running_prompt(self):
        self.assertEqual([(BASE + "/interrupt", {"prompt_id": "pid-1"})], self._stop_after_queueing(RUNNING))

    def test_stop_leaves_the_users_running_prompt_alone(self):
        self.assertEqual([], self._stop_after_queueing(OTHER_RUNNING))

    def test_comfy_base_url(self):
        self.assertEqual(comfy_base_url("0.0.0.0,::", 8188, False), "http://127.0.0.1:8188")
        self.assertEqual(comfy_base_url("192.168.1.5", 8000, True), "https://192.168.1.5:8000")
        self.assertEqual(comfy_base_url("::1", 8188, False), "http://[::1]:8188")

    def test_comfy_base_url_falls_back_outside_comfyui(self):
        self.assertEqual(comfy_base_url(), "http://127.0.0.1:8188")

    def test_has_text_generation_is_false_outside_comfyui(self):
        self.assertFalse(has_text_generation())

    def test_list_naming_models_keeps_supported_text_encoders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_header(root / "z_qwen35.safetensors", ["model.language_model.layers.0.linear_attn.A_log"])
            write_header(root / "a_gemma.safetensors", ["model.layers.0.post_feedforward_layernorm.weight"])
            write_header(root / "vae.safetensors", ["decoder.conv_in.weight"])
            (root / "broken.safetensors").write_bytes(b"\x01")
            self.assertEqual(list_naming_models([root]), ["a_gemma.safetensors", "z_qwen35.safetensors"])

    def test_list_naming_models_matches_qwen3_q_norm_with_or_without_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_header(root / "qwen3.safetensors", ["model.layers.0.self_attn.q_norm.weight"])
            write_header(root / "qwen3vl.safetensors", ["model.language_model.layers.0.self_attn.q_norm.weight"])
            write_header(root / "bare.safetensors", ["layers.0.self_attn.q_norm.weight"])
            write_header(root / "other.safetensors", ["vision.layers.0.self_attn.q_norm.weight"])
            self.assertEqual(list_naming_models([root]),
                             ["bare.safetensors", "qwen3.safetensors", "qwen3vl.safetensors"])

    def test_list_naming_models_names_subfolder_files_like_comfyui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "llm").mkdir()
            write_header(root / "llm" / "qwen35.safetensors", ["model.language_model.layers.0.linear_attn.A_log"])
            self.assertEqual(list_naming_models([root, root / "missing"]),
                             [os.path.join("llm", "qwen35.safetensors")])


if __name__ == "__main__":
    unittest.main()
