from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
import threading

from .constants import SUPPORTED_MODEL_TYPES
from .sidecar import quarantine_corrupt_file, write_json


DEFAULT_CONFIG = {
    "apiKey": "",
    "cacheTtlMinutes": 240,
    "requestTimeoutSeconds": 30,
    "maxRetries": 4,
    "requestDelayMs": 120,
    "useComfyPaths": True,
    "useExtraModelPaths": True,
    "useCustomPaths": True,
    "treatSidecarsAsInstalled": True,
    "matureMode": "show",
    # The renamer's AI: a file in ComfyUI's text_encoders folders, named as
    # CLIPLoader's clip_name (relative path, subfolders included).
    "namingModel": "",
    # Preview fallback sources, for files Civitai cannot serve.
    "fallbackCivarchive": False,
    "fallbackHuggingFace": ["malcolmrey"],
    "fallbackAiToolkitFolders": [],
    "fallbackOwnCreator": "",
    "customPaths": {model_type: [] for model_type in SUPPORTED_MODEL_TYPES},
}

_HF_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][\w.-]*(/[\w.-]+)?$")


class ConfigStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_path = self.data_dir / "config.json"
        self._config = deepcopy(DEFAULT_CONFIG)
        self._writable = True
        # Job threads (the naming-model download) and the server loop both save settings.
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.config_path.is_file():
            self._save()
            return

        try:
            # Bytes let json detect UTF-8 with or without a BOM, UTF-16 and UTF-32.
            loaded = json.loads(self.config_path.read_bytes())
        except OSError as exc:
            # Unreadable, not corrupt: keep defaults in memory and never write
            # them back, or the panel's first settings sync would overwrite the
            # API key and custom paths.
            self._writable = False
            print(
                f"Civitai updater: could not read {self.config_path} ({exc}); using defaults for this session. "
                "Settings changes will not be saved until ComfyUI is restarted."
            )
            return
        except ValueError as exc:
            backup = quarantine_corrupt_file(self.config_path)
            print(
                f"Civitai updater: {self.config_path} is not valid JSON ({exc}). "
                f"It was moved to {backup} and default settings were written."
            )
            loaded = {}

        self._config = self._merged(loaded)
        self._save()

    def _merged(self, incoming: dict) -> dict:
        merged = deepcopy(DEFAULT_CONFIG)

        if not isinstance(incoming, dict):
            return merged

        for key in (
            "apiKey",
            "cacheTtlMinutes",
            "requestTimeoutSeconds",
            "maxRetries",
            "requestDelayMs",
            "useComfyPaths",
            "useExtraModelPaths",
            "useCustomPaths",
            "treatSidecarsAsInstalled",
            "matureMode",
            "namingModel",
            "fallbackCivarchive",
            "fallbackOwnCreator",
        ):
            if key in incoming:
                merged[key] = incoming[key]

        custom_paths = incoming.get("customPaths", {})
        if isinstance(custom_paths, dict):
            for model_type in SUPPORTED_MODEL_TYPES:
                val = custom_paths.get(model_type, [])
                if isinstance(val, list):
                    merged["customPaths"][model_type] = [
                        str(Path(path))
                        for path in val
                        if isinstance(path, str) and path.strip()
                    ]

        merged["cacheTtlMinutes"] = _int_in_range(
            merged["cacheTtlMinutes"], default=240, minimum=0, maximum=10080
        )
        merged["requestTimeoutSeconds"] = _int_in_range(
            merged["requestTimeoutSeconds"], default=30, minimum=5, maximum=300
        )
        merged["maxRetries"] = _int_in_range(
            merged["maxRetries"], default=4, minimum=0, maximum=10
        )
        merged["requestDelayMs"] = _int_in_range(
            merged["requestDelayMs"], default=120, minimum=0, maximum=3000
        )

        if not isinstance(merged["apiKey"], str):
            merged["apiKey"] = ""
        merged["useComfyPaths"] = bool(merged["useComfyPaths"])
        merged["useExtraModelPaths"] = bool(merged["useExtraModelPaths"])
        merged["useCustomPaths"] = bool(merged["useCustomPaths"])
        merged["treatSidecarsAsInstalled"] = bool(merged["treatSidecarsAsInstalled"])
        if merged["matureMode"] not in ("show", "blur", "hide"):
            merged["matureMode"] = "show"
        if not isinstance(merged["namingModel"], str):
            merged["namingModel"] = ""
        merged["namingModel"] = merged["namingModel"].strip()

        merged["fallbackCivarchive"] = bool(merged["fallbackCivarchive"])
        hf_repos = incoming.get("fallbackHuggingFace")
        if isinstance(hf_repos, list):
            merged["fallbackHuggingFace"] = _clean_hf_repos(hf_repos)
        folders = incoming.get("fallbackAiToolkitFolders")
        if isinstance(folders, list):
            merged["fallbackAiToolkitFolders"] = [
                str(Path(folder.strip()))
                for folder in folders
                if isinstance(folder, str) and folder.strip()
            ]
        if not isinstance(merged["fallbackOwnCreator"], str):
            merged["fallbackOwnCreator"] = ""
        merged["fallbackOwnCreator"] = merged["fallbackOwnCreator"].strip()[:100]

        return merged

    def _save(self) -> None:
        if self._writable:
            write_json(self.config_path, self._config)

    def get(self) -> dict:
        with self._lock:
            return deepcopy(self._config)

    def get_public(self) -> dict:
        cfg = self.get()
        cfg["hasApiKey"] = bool(cfg.get("apiKey", "").strip())
        cfg["apiKey"] = ""
        return cfg

    def update(self, incoming: dict) -> dict:
        incoming = incoming or {}
        with self._lock:
            merged = self._merged({**self._config, **incoming})
            if "customPaths" in incoming and isinstance(incoming["customPaths"], dict):
                current_custom = deepcopy(self._config.get("customPaths", {}))
                normalized_custom = self._merged({"customPaths": incoming["customPaths"]})["customPaths"]
                for model_type in SUPPORTED_MODEL_TYPES:
                    if model_type in incoming["customPaths"]:
                        current_custom[model_type] = normalized_custom[model_type]
                merged["customPaths"] = current_custom
            self._config = merged
            self._save()
            return self.get()


def _clean_hf_repos(values: list) -> list[str]:
    """Keep valid Hugging Face repo or author names, first spelling wins, case-insensitive dedupe."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not _HF_REPO_PATTERN.match(name) or name.lower() in seen:
            continue
        seen.add(name.lower())
        cleaned.append(name)
    return cleaned


def _int_in_range(value, default: int, minimum: int, maximum: int) -> int:
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, as_int))
