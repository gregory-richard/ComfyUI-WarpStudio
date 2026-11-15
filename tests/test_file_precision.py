from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from comfy_updater.file_precision import read_precision, safetensors_header


def write_safetensors(path: Path, tensors: Dict[str, Tuple[str, Sequence[int]]]) -> dict:
    header: dict = {}
    for name, (dtype, shape) in tensors.items():
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [0, 0]}
    raw = json.dumps(header).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(struct.pack("<Q", len(raw)))
        handle.write(raw)
        handle.write(b"\x00" * 16)
    return header


def write_gguf(
    path: Path,
    tensors: List[Tuple[str, Sequence[int], int]],
    n_kv: int = 0,
    kv_bytes: bytes = b"",
) -> None:
    with open(path, "wb") as handle:
        handle.write(b"GGUF")
        handle.write(struct.pack("<I", 3))
        handle.write(struct.pack("<Q", len(tensors)))
        handle.write(struct.pack("<Q", n_kv))
        handle.write(kv_bytes)
        for name, dims, ggml_type in tensors:
            encoded = name.encode("utf-8")
            handle.write(struct.pack("<Q", len(encoded)))
            handle.write(encoded)
            handle.write(struct.pack("<I", len(dims)))
            for dim in dims:
                handle.write(struct.pack("<Q", dim))
            handle.write(struct.pack("<I", ggml_type))
            handle.write(struct.pack("<Q", 0))


def gguf_string(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


class FilePrecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_int8_dominates_by_bytes(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"w1": ("I8", [4096, 4096]), "s1": ("F32", [4096])})
        self.assertEqual("int8", read_precision(path))

    def test_bf16(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"w": ("BF16", [64, 64])})
        self.assertEqual("bf16", read_precision(path))

    def test_fp16(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"w": ("F16", [64, 64])})
        self.assertEqual("fp16", read_precision(path))

    def test_fp8_e4m3(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"w": ("F8_E4M3", [64, 64])})
        self.assertEqual("fp8", read_precision(path))

    def test_nvfp4(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"w": ("U8", [64, 32]), "w.weight_scale_2": ("F32", [1])})
        self.assertEqual("nvfp4", read_precision(path))

        plain = self.dir / "plain.safetensors"
        write_safetensors(plain, {"w": ("U8", [64, 32])})
        self.assertEqual("", read_precision(plain))

    def test_only_1d_tensors_is_unknown(self) -> None:
        path = self.dir / "m.safetensors"
        write_safetensors(path, {"a": ("F32", [4096]), "b": ("BF16", [16])})
        self.assertEqual("", read_precision(path))

    def test_truncated_file_is_unknown(self) -> None:
        path = self.dir / "m.safetensors"
        path.write_bytes(os.urandom(5))
        self.assertEqual("", read_precision(path))

    def test_gguf_q8_0(self) -> None:
        path = self.dir / "m.gguf"
        write_gguf(path, [("a", [64, 64], 8), ("b", [32, 32], 8)])
        self.assertEqual("q8_0", read_precision(path))

        half = self.dir / "h.gguf"
        write_gguf(half, [("a", [64, 64], 1), ("b", [32, 32], 1)])
        self.assertEqual("fp16", read_precision(half))

    def test_safetensors_header_returns_parsed_json(self) -> None:
        path = self.dir / "m.safetensors"
        written = write_safetensors(path, {"w": ("BF16", [8, 8])})
        self.assertEqual(written, safetensors_header(path))

        bad = self.dir / "bad.safetensors"
        bad.write_bytes(struct.pack("<Q", 4) + b"nope")
        self.assertIsNone(safetensors_header(bad))
        self.assertIsNone(safetensors_header(self.dir / "missing.safetensors"))

    def test_gguf_skips_real_shaped_metadata(self) -> None:
        kv = gguf_string("general.architecture") + struct.pack("<I", 8) + gguf_string("flux")
        kv += gguf_string("general.quantization_version") + struct.pack("<I", 4) + struct.pack("<I", 2)
        kv += gguf_string("tokenizer.tokens") + struct.pack("<I", 9) + struct.pack("<I", 8)
        kv += struct.pack("<Q", 3) + gguf_string("a") + gguf_string("bc") + gguf_string("def")
        path = self.dir / "m.gguf"
        write_gguf(path, [("a", [64, 64], 8), ("b", [32, 32], 8)], n_kv=3, kv_bytes=kv)
        self.assertEqual("q8_0", read_precision(path))

    def test_deeply_nested_gguf_array_is_unknown(self) -> None:
        levels = 5000
        kv = gguf_string("evil") + struct.pack("<I", 9)
        kv += (struct.pack("<I", 9) + struct.pack("<Q", 1)) * levels
        kv += struct.pack("<I", 4) + struct.pack("<Q", 0)
        path = self.dir / "m.gguf"
        write_gguf(path, [("a", [64, 64], 8)], n_kv=1, kv_bytes=kv)
        self.assertEqual("", read_precision(path))

    def test_deeply_nested_safetensors_header_is_unknown(self) -> None:
        raw = b"[" * 100_000 + b"]" * 100_000
        path = self.dir / "m.safetensors"
        path.write_bytes(struct.pack("<Q", len(raw)) + raw)
        self.assertEqual("", read_precision(path))
        self.assertIsNone(safetensors_header(path))


if __name__ == "__main__":
    unittest.main()
