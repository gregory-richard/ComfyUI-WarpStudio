from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from .fallback_types import FallbackResult, SourceUnavailable

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
_BASE_URL = "https://civarchive.com/sha256/"
_UNEXPECTED = "unexpected civarchive page"
_BREAKS = frozenset(
    {"p", "br", "div", "li", "ul", "ol", "tr", "td", "th", "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BREAKS:
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            if self._skip:
                self._skip -= 1
        elif tag in _BREAKS:
            self._parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def _plain_text(html: Any) -> str:
    if not isinstance(html, str) or not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    return parser.text()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _level(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _words(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [w.strip() for w in value if isinstance(w, str) and w.strip()]


def _pick_image(images: Any) -> tuple[str, int]:
    if not isinstance(images, list):
        return "", 0
    best: tuple[str, int] | None = None
    for img in images:
        # Any minor flag but a plain false (``1``, ``"true"``, ``"yes"``, even ``"false"``) excludes the image.
        if not isinstance(img, dict) or img.get("type") != "image" or img.get("minor") not in (None, False):
            continue
        url = img.get("url") or img.get("image_url")
        if not isinstance(url, str) or not url.strip():
            continue
        level = _level(img.get("nsfwLevel"))
        if best is None or level < best[1]:
            best = (url.strip(), level)
    return best if best else ("", 0)


def _has_hash(version: dict, sha: str) -> bool:
    files = version.get("files")
    if not isinstance(files, list):
        return False
    return any(
        isinstance(f, dict) and isinstance(f.get("sha256"), str) and f["sha256"].strip().lower() == sha
        for f in files
    )


def parse_hash_page(html: str, sha256: str) -> FallbackResult | None:
    sha = sha256.strip().lower()
    match = _NEXT_DATA.search(html or "")
    if not match:
        raise SourceUnavailable(_UNEXPECTED)
    try:
        data = json.loads(match.group(1))
        models = data["props"]["pageProps"]["models"]
    except (ValueError, KeyError, TypeError):
        raise SourceUnavailable(_UNEXPECTED) from None
    if not isinstance(models, list):
        raise SourceUnavailable(_UNEXPECTED)

    matches: list[tuple[dict, dict]] = []
    for model in models:
        if not isinstance(model, dict):
            raise SourceUnavailable(_UNEXPECTED)
        version = model.get("version")
        if version is None:
            continue
        if not isinstance(version, dict):
            raise SourceUnavailable(_UNEXPECTED)
        if _has_hash(version, sha):
            matches.append((model, version))
    if not matches:
        return None
    model, version = next((m for m in matches if m[0].get("platform") == "civitai"), matches[0])

    model_id = version.get("civitai_model_id")
    version_id = version.get("civitai_model_version_id")
    civitai = model.get("platform") == "civitai" and model_id is not None and version_id is not None
    image_url, image_level = _pick_image(version.get("images"))

    return FallbackResult(
        source="civarchive",
        url=_BASE_URL + sha,
        title=_text(model.get("name")),
        creator=_text(model.get("username")) or _text(model.get("creator_username")),
        version_name=_text(version.get("name")),
        base_model=_text(version.get("base_model")),
        description=_plain_text(version.get("description")) or _plain_text(model.get("description")),
        trained_words=_words(version.get("trigger")),
        nsfw=bool(model.get("is_nsfw")),
        image_url=image_url,
        image_nsfw_level=image_level if image_url else 0,
        civitai_model_id=str(model_id) if civitai else "",
        civitai_version_id=str(version_id) if civitai else "",
    )


def lookup_civarchive(sha256: str, http) -> FallbackResult | None:
    sha = sha256.strip().lower()
    return parse_hash_page(http.get_text(_BASE_URL + sha), sha)
