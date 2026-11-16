"""Read a model file's weight precision from its header, never its tensor data."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import BinaryIO, Dict, Optional

MAX_HEADER_BYTES = 100 * 1024 * 1024
# The share of weight bytes from which a file counts as its quantized variant.
QUANTIZED_SHARE = 0.25
_MAX_GGUF_COUNT = 10_000_000
_MAX_GGUF_NESTING = 8

_SAFETENSORS_DTYPE_BYTES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
    "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
    "I32": 4, "U32": 4, "F32": 4,
    "I64": 8, "U64": 8, "F64": 8,
}

_SAFETENSORS_PRECISION = {
    "I8": "int8",
    "F8_E4M3": "fp8",
    "F8_E5M2": "fp8",
    "BF16": "bf16",
    "F16": "fp16",
    "F32": "fp32",
}

_GGML_PRECISION = {
    0: "fp32",
    1: "fp16",
    30: "bf16",
    8: "q8_0",
    12: "q4_k",
    13: "q5_k",
    14: "q6_k",
}

# GGUF metadata value types with a fixed byte size (8 = string, 9 = array).
_GGUF_SCALAR_BYTES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def safetensors_header(path: Path) -> Optional[dict]:
    """Return the parsed safetensors JSON header, or None when unreadable or invalid."""
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                return None
            (length,) = struct.unpack("<Q", prefix)
            if length == 0 or length > MAX_HEADER_BYTES:
                return None
            raw = handle.read(length)
        if len(raw) != length:
            return None
        header = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, struct.error, RecursionError):
        return None
    return header if isinstance(header, dict) else None


def read_precision(path: Path) -> str:
    """Return the dominant weight precision of a model file, or "" when unknown."""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            magic = handle.read(4)
            if path.suffix.lower() == ".gguf" or magic == b"GGUF":
                if magic != b"GGUF":
                    return ""
                return _gguf_precision(handle)
    except (OSError, ValueError, struct.error, RecursionError):
        return ""
    return _safetensors_precision(path)


def _safetensors_precision(path: Path) -> str:
    header = safetensors_header(path)
    if header is None:
        return ""
    weights: Dict[str, int] = {}
    has_scale_2 = False
    try:
        for name, info in header.items():
            if name == "__metadata__" or not isinstance(info, dict):
                continue
            if name.endswith("weight_scale_2"):
                has_scale_2 = True
            shape = info.get("shape")
            dtype = info.get("dtype")
            if not isinstance(shape, list) or len(shape) < 2 or not isinstance(dtype, str):
                continue
            elements = 1
            for dim in shape:
                elements *= int(dim)
            size = elements * _SAFETENSORS_DTYPE_BYTES.get(dtype, 1)
            weights[dtype] = weights.get(dtype, 0) + size
    except (TypeError, ValueError):
        return ""
    if not weights:
        return ""
    # A quantized file keeps some parts (a text encoder, norms) in higher precision:
    # it is the quantized variant once a quarter of its weights are quantized.
    quantized = [dtype for dtype in ("I8", "F8_E4M3", "F8_E5M2") if dtype in weights]
    if has_scale_2 and "U8" in weights:
        quantized.append("U8")
    total = sum(weights.values())
    if quantized:
        main = max(quantized, key=lambda key: weights[key])
        if weights[main] >= QUANTIZED_SHARE * total:
            return "nvfp4" if main == "U8" else _SAFETENSORS_PRECISION[main]
    dominant = max(weights, key=lambda key: weights[key])
    if dominant == "U8":
        return "nvfp4" if has_scale_2 else ""
    return _SAFETENSORS_PRECISION.get(dominant, "")


def _gguf_precision(handle: BinaryIO) -> str:
    (version,) = struct.unpack("<I", _read_exact(handle, 4))
    if version < 2:
        return ""
    n_tensors = _read_count(handle)
    n_kv = _read_count(handle)
    for _ in range(n_kv):
        _read_string(handle)
        (value_type,) = struct.unpack("<I", _read_exact(handle, 4))
        _skip_gguf_value(handle, value_type)

    counts: Dict[int, int] = {}
    for _ in range(n_tensors):
        _read_string(handle)
        (n_dims,) = struct.unpack("<I", _read_exact(handle, 4))
        if n_dims > 16:
            raise ValueError("implausible tensor rank")
        elements = 1
        for _ in range(n_dims):
            (dim,) = struct.unpack("<Q", _read_exact(handle, 8))
            elements *= dim
        (ggml_type,) = struct.unpack("<I", _read_exact(handle, 4))
        _read_exact(handle, 8)  # data offset
        if n_dims >= 2:
            counts[ggml_type] = counts.get(ggml_type, 0) + elements
    if not counts:
        return ""
    dominant = max(counts, key=lambda key: counts[key])
    return _GGML_PRECISION.get(dominant, "")


def _skip_gguf_value(handle: BinaryIO, value_type: int, depth: int = 0) -> None:
    if depth > _MAX_GGUF_NESTING:
        raise ValueError("GGUF arrays nested too deeply")
    if value_type in _GGUF_SCALAR_BYTES:
        _read_exact(handle, _GGUF_SCALAR_BYTES[value_type])
    elif value_type == 8:
        _read_string(handle)
    elif value_type == 9:
        (item_type,) = struct.unpack("<I", _read_exact(handle, 4))
        count = _read_count(handle)
        if item_type in _GGUF_SCALAR_BYTES:
            _read_exact(handle, _GGUF_SCALAR_BYTES[item_type] * count)
        else:
            for _ in range(count):
                _skip_gguf_value(handle, item_type, depth + 1)
    else:
        raise ValueError("unknown GGUF value type")


def _read_count(handle: BinaryIO) -> int:
    (count,) = struct.unpack("<Q", _read_exact(handle, 8))
    if count > _MAX_GGUF_COUNT:
        raise ValueError("implausible GGUF count")
    return count


def _read_string(handle: BinaryIO) -> None:
    (length,) = struct.unpack("<Q", _read_exact(handle, 8))
    _read_exact(handle, length)


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    if size > MAX_HEADER_BYTES:
        raise ValueError("implausible GGUF field size")
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("truncated GGUF header")
    return data
