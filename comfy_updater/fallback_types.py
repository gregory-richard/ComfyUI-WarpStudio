from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class SourceUnavailable(Exception):
    """A fallback source could not answer (network error, non-200, refused redirect, oversized body).

    ``status`` is the HTTP status of a non-200 answer, else None.
    """

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TooLarge(SourceUnavailable):
    """A response body went past the size cap asked for; asking again would fail the same way."""


class IndexIncomplete(SourceUnavailable):
    """A hash index built only in part (request limit, a failed listing, a stop) lacks this hash.

    It sends no requests after its build, so asking again costs nothing: unlike other
    failures it never counts toward dropping the source. It is still no "doesn't know".
    """


@dataclass
class FallbackResult:
    source: str
    url: str
    title: str = ""
    creator: str = ""
    version_name: str = ""
    base_model: str = ""
    description: str = ""
    trained_words: list[str] = field(default_factory=list)
    nsfw: bool = False
    image_url: str = ""
    image_path: Path | None = None
    image_nsfw_level: int = 0
    # No picture because the source couldn't look one up this time (not because it has none).
    image_lookup_failed: bool = False
    civitai_model_id: str = ""
    civitai_version_id: str = ""
    final: bool = False
