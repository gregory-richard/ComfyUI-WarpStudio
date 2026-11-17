"""Preview fallback: other sources for files Civitai can't serve.

For a file still without a ``.preview.png`` the configured sources are asked in
order (own ai-toolkit trainings, Hugging Face, civarchive); the first answer wins.
A hit writes the preview, never over an existing one, and fills only the empty
fields of the info file. An info file that identifies a Civitai model keeps every
field. Hashes no online source knew, or whose online answer gave no usable picture,
are remembered for 30 days.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .fallback_aitoolkit import base_names_from_codes, lookup_own_training
from .fallback_civarchive import lookup_civarchive
from .fallback_hf import HuggingFaceIndex
from .fallback_http import is_allowed_url, save_preview_png
from .fallback_types import FallbackResult, IndexIncomplete, SourceUnavailable
from .sidecar import read_json, sidecar_identifies_model, stored_sha256, write_json

MISS_DAYS = 30
MAX_FAILURES = 3  # in a row; then the source is skipped for the rest of the scan
MAX_IMAGE_BYTES = 20_000_000
MAX_TEXT = 200  # title, creator, version, base
MAX_DESCRIPTION = 5_000
MAX_WORDS = 20
MAX_WORD = 100

MISSES_FILENAME = "preview_fallback.json"
HF_INDEX_FILENAME = "hf_index.json"
SOURCE_TAG = "comfy-civitai-updater"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CIVITAI_ID = re.compile(r"[1-9][0-9]*")
# What became of a hit's picture.
_WRITTEN, _UNUSABLE, _NOT_WRITTEN = "written", "unusable", "not written"


@dataclass
class FallbackOutcome:
    source: str
    preview_written: bool
    info_written: bool


class MissStore:
    """Hashes the online sources have nothing more for, with when and under which sources they were asked.

    Saved after each change as ``{"misses": {sha: {"checkedAt": iso, "sources": fingerprint}}}``.
    A corrupt or wrong-shaped file reads as empty and is replaced on the next change.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw = read_json(self.path)
        misses = raw.get("misses") if isinstance(raw, dict) else None
        self._misses: dict[str, dict] = {}
        if isinstance(misses, dict):
            for sha, entry in misses.items():
                if isinstance(entry, dict):
                    self._misses[_normal_sha(sha)] = entry

    def is_fresh(self, sha256: str, fingerprint: str, now: datetime) -> bool:
        """True when this hash was a miss under the same sources less than 30 days ago."""
        entry = self._misses.get(_normal_sha(sha256))
        if entry is None or entry.get("sources") != fingerprint:
            return False
        try:
            age = now - datetime.fromisoformat(entry.get("checkedAt"))
        except (TypeError, ValueError):  # missing, malformed, or without a time zone
            return False
        return timedelta(0) <= age < timedelta(days=MISS_DAYS)

    def record(self, sha256: str, fingerprint: str, now: datetime) -> None:
        self._misses[_normal_sha(sha256)] = {"checkedAt": now.isoformat(), "sources": fingerprint}
        self._save()

    def forget(self, sha256: str) -> None:
        if self._misses.pop(_normal_sha(sha256), None) is not None:
            self._save()

    def _save(self) -> None:
        try:
            write_json(self.path, {"misses": self._misses}, compact=True)
        except OSError:
            pass  # a lost miss only means the sources are asked again next scan


def apply_to_info(
    info: dict | None, result: FallbackResult, sha256: str, file_name: str, now_iso: str
) -> dict | None:
    """The info file with this result filled in where its fields are empty, or None when nothing changes.

    An info file that identifies a Civitai model, or records another file's hash, is never changed.
    ``info`` itself is left as it is.
    """
    if sidecar_identifies_model(info):
        return None
    sha = _normal_sha(sha256)
    stored = stored_sha256(info)
    if sha and stored and stored != sha:
        return None
    new = copy.deepcopy(info) if isinstance(info, dict) else _stub(file_name)

    _fill_text(new, "name", result.version_name, MAX_TEXT, placeholder=file_name)
    _fill_text(new, "baseModel", result.base_model, MAX_TEXT)
    words = _capped_words(result.trained_words)
    if words and not _filled_list(new.get("trainedWords")):
        new["trainedWords"] = words
    _fill_text(new, "description", result.description, MAX_DESCRIPTION)

    model = _child(new, "model")
    _fill_text(model, "name", result.title, MAX_TEXT)
    if not isinstance(model.get("nsfw"), bool):
        model["nsfw"] = bool(result.nsfw)
    if _cap(result.creator, MAX_TEXT):
        _fill_text(_child(new, "creator"), "username", result.creator, MAX_TEXT)

    image_url = _allowed(result.image_url)
    if image_url and not _filled_list(new.get("images")):
        new["images"] = [{"url": image_url, "type": "image", "nsfwLevel": result.image_nsfw_level}]

    model_id, version_id = _civitai_id(result.civitai_model_id), _civitai_id(result.civitai_version_id)
    if model_id and version_id and not new.get("modelId") and not new.get("id"):
        new["modelId"], new["id"] = model_id, version_id

    if sha and not stored_sha256(new):
        _child(_first_file(new), "hashes")["SHA256"] = sha

    extensions = _child(new, "extensions")
    _fill_text(extensions, "source", SOURCE_TAG, MAX_TEXT)
    if not isinstance(extensions.get("fallback"), dict):
        extensions["fallback"] = {"source": result.source, "url": _allowed(result.url), "foundAt": now_iso}

    return None if new == info else new


class PreviewFallback:
    """One scan's fallback: the configured sources, their failure counts and the miss list."""

    def __init__(
        self,
        config: dict,
        data_dir: Path,
        http,
        base_codes: dict[str, str],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._config = config if isinstance(config, dict) else {}
        self.http = http
        self._now = now
        data_dir = Path(data_dir)
        self._folders = [Path(folder) for folder in _strings(self._config.get("fallbackAiToolkitFolders"))]
        creator = self._config.get("fallbackOwnCreator")
        self._creator = creator.strip() if isinstance(creator, str) else ""
        self._base_names = base_names_from_codes(base_codes or {})
        hf_entries = _strings(self._config.get("fallbackHuggingFace"))
        self._hf = HuggingFaceIndex(hf_entries, http, data_dir / HF_INDEX_FILENAME) if hf_entries else None
        self._civarchive = bool(self._config.get("fallbackCivarchive"))
        # Which online sources a miss was asked under; a change asks it again.
        self._fingerprint = "|".join(
            (["civarchive"] if self._civarchive else []) + sorted(entry.lower() for entry in hf_entries)
        )
        self._misses = MissStore(data_dir / MISSES_FILENAME)
        self._failures = {"huggingface": 0, "civarchive": 0}

    @staticmethod
    def is_enabled(config: dict) -> bool:
        """True when any source is configured."""
        config = config if isinstance(config, dict) else {}
        return (
            bool(config.get("fallbackCivarchive"))
            or bool(_strings(config.get("fallbackHuggingFace")))
            or bool(_strings(config.get("fallbackAiToolkitFolders")))
        )

    @property
    def enabled(self) -> bool:
        return self.is_enabled(self._config)

    def run(
        self,
        *,
        model_path: Path,
        info_path: Path,
        preview_path: Path,
        sha256: str,
        refetch: bool,
        on_source: Callable[[str], None] | None = None,
    ) -> FallbackOutcome | None:
        """Ask the sources about one file. None when it has a preview, or no source asked had an answer.

        ``on_source`` gets each source's key just before that source is asked.
        """
        model_path, info_path, preview_path = Path(model_path), Path(info_path), Path(preview_path)
        if preview_path.exists():
            return None
        sha = _normal_sha(sha256)
        if not _SHA256.fullmatch(sha):
            sha = ""  # only a real SHA256 ever leaves the machine; without one, online sources are skipped
        own = self._ask_own(model_path, on_source)
        if own is not None:
            result, all_answered = own, False  # own trainings are local: never remembered
        else:
            result, all_answered = self._ask_online(sha, refetch, on_source)
        # Remembering needs every online source asked to have answered: a failure is no miss.
        if result is None:
            if all_answered:
                self._misses.record(sha, self._fingerprint, self._now())
            return None
        picture = self._write_preview(result, preview_path)
        info_written = self._write_info(result, info_path, sha, model_path.name)
        if all_answered and picture == _UNUSABLE:
            # This answer can give no picture: skip the online sources like a miss.
            self._misses.record(sha, self._fingerprint, self._now())
        elif sha:
            self._misses.forget(sha)
        return FallbackOutcome(source=result.source, preview_written=picture == _WRITTEN, info_written=info_written)

    def _ask_own(self, model_path: Path, on_source) -> FallbackResult | None:
        """Own trainings need no hash, and nobody else has them."""
        if not self._folders:
            return None
        _announce(on_source, "ai-toolkit")
        return lookup_own_training(model_path, self._folders, self._creator, self._base_names)

    def _ask_online(self, sha: str, refetch: bool, on_source) -> tuple[FallbackResult | None, bool]:
        """The first online answer, and whether every source asked up to it answered."""
        online = self._online_sources()
        if not sha or not online:
            return None, False
        if not refetch and self._misses.is_fresh(sha, self._fingerprint, self._now()):
            return None, False
        all_answered = True
        for key, ask in online:
            if self._failures[key] >= MAX_FAILURES:
                all_answered = False
                continue
            _announce(on_source, key)
            try:
                result = ask(sha)
            except IndexIncomplete:
                # Costs no requests and its hits still count: neither a failure nor an answer.
                all_answered = False
                continue
            except SourceUnavailable:
                self._failures[key] += 1
                all_answered = False
                continue
            self._failures[key] = 0
            if result is not None:
                return result, all_answered
        return None, all_answered

    def _online_sources(self) -> list[tuple[str, Callable[[str], FallbackResult | None]]]:
        sources = []
        if self._hf is not None:
            sources.append(("huggingface", self._hf.find))
        if self._civarchive:
            sources.append(("civarchive", lambda sha: lookup_civarchive(sha, self.http)))
        return sources

    def _write_preview(self, result: FallbackResult, preview_path: Path) -> str:
        """``_WRITTEN``, ``_UNUSABLE`` (no picture can come from this answer) or ``_NOT_WRITTEN``."""
        if result.image_path is not None:
            data = _read_capped(Path(result.image_path))
            if data is None:
                return _NOT_WRITTEN
        elif result.image_url and is_allowed_url(result.image_url):
            try:
                data = self.http.download(result.image_url, max_bytes=MAX_IMAGE_BYTES)
            except SourceUnavailable:
                return _NOT_WRITTEN  # may work next scan
        else:
            # A source that couldn't look a picture up may find one next scan.
            return _NOT_WRITTEN if result.image_lookup_failed else _UNUSABLE
        if save_preview_png(data, preview_path):
            return _WRITTEN
        # save_preview_png never replaces a preview; one written since the check in run is no fault of the data.
        return _NOT_WRITTEN if preview_path.exists() else _UNUSABLE

    def _write_info(self, result: FallbackResult, info_path: Path, sha: str, file_name: str) -> bool:
        info = read_json(info_path)
        if info is None and info_path.exists():
            return False  # an info file that can't be read is never replaced
        new = apply_to_info(info, result, sha, file_name, self._now().isoformat())
        if new is None:
            return False
        write_json(info_path, new)
        return True


def _announce(on_source, key: str) -> None:
    if on_source is not None:
        on_source(key)


def _normal_sha(value) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _strings(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _read_capped(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= MAX_IMAGE_BYTES else None


def _stub(file_name: str) -> dict:
    """The same stub a scan writes for a file Civitai doesn't know."""
    return {
        "id": "",
        "modelId": "",
        "name": file_name,
        "files": [{"hashes": {"SHA256": ""}}],
        "extensions": {"source": SOURCE_TAG},
    }


def _cap(value, limit: int) -> str:
    return value.strip()[:limit].rstrip() if isinstance(value, str) else ""


def _capped_words(words) -> list[str]:
    if not isinstance(words, list):
        return []
    return [word for word in (_cap(word, MAX_WORD) for word in words) if word][:MAX_WORDS]


def _filled_list(value) -> bool:
    return isinstance(value, list) and bool(value)


def _fill_text(target: dict, key: str, value, limit: int, placeholder: str = "") -> None:
    """Set ``target[key]`` when it is empty (or still the placeholder) and there is a value."""
    text = _cap(value, limit)
    current = target.get(key)
    if text and (not isinstance(current, str) or not current.strip() or current == placeholder):
        target[key] = text


def _child(parent: dict, key: str) -> dict:
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def _first_file(info: dict) -> dict:
    files = info.get("files")
    if not isinstance(files, list):
        files = info["files"] = []
    if not files:
        files.append({})
    elif not isinstance(files[0], dict):
        files[0] = {}
    return files[0]


def _allowed(url) -> str:
    """An outside link is stored only when it is on the fallback allow-list."""
    return url if isinstance(url, str) and url and is_allowed_url(url) else ""


def _civitai_id(value) -> int | None:
    """Civitai ids are positive integers, stored as numbers like Civitai's own info files."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).strip()
    return int(text) if _CIVITAI_ID.fullmatch(text) else None
