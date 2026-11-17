from __future__ import annotations

import io
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from .constants import USER_AGENT
from .fallback_types import SourceUnavailable

_ALLOWED_HOSTS = ("huggingface.co", "civarchive.com", "c.genur.art")
_ALLOWED_SUFFIXES = (".hf.co",)
_PACED_HOST = "civarchive.com"
_PACE_SECONDS = 1.5
_MAX_JSON_BYTES = 10_000_000
# Longest side of a saved preview: keeps it well under the local-preview route's size cap.
MAX_PREVIEW_SIDE = 2048


def is_allowed_url(url: str) -> bool:
    """True for https URLs on the fallback allow-list (Hugging Face and civarchive hosts)."""
    if "\\" in url:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.username is not None or parts.password is not None:
            return False
        # requests uses urllib3's parser; both parsers must agree on the host.
        if (parse_url(url).host or "").lower() != host:
            return False
    except (ValueError, LocationParseError):
        return False
    if parts.scheme != "https" or not host:
        return False
    return host in _ALLOWED_HOSTS or any(host.endswith(suffix) for suffix in _ALLOWED_SUFFIXES)


def _refuse_redirect_off_list(response, *args, **kwargs):
    """Stop a fetch that redirects anywhere off the allow-list."""
    if response.is_redirect:
        try:
            target = urljoin(response.url, response.headers.get("Location", ""))
            allowed = is_allowed_url(target)
        except ValueError:
            target, allowed = "an invalid address", False
        if not allowed:
            response.close()
            raise requests.TooManyRedirects(f"Refused redirect to {target}")
    return response


class JsonPages(list):
    """The items of a paged listing; ``truncated`` says a next page was left unread."""

    truncated = False


class FallbackHttp:
    """Fetch client limited to the allow-list, one try per call, paced for civarchive."""

    def __init__(self, timeout_seconds: int = 30, should_stop=None, session=None,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self.timeout_seconds = timeout_seconds
        self.should_stop = should_stop
        self.sleep = sleep
        self.clock = clock
        self.request_count = 0
        self._last_request: dict[str, float] = {}
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
        self.session = session

    def _open(self, url: str):
        """Check the URL and stop flag, pace, send one request; return a 200 response or raise."""
        if not is_allowed_url(url):
            raise SourceUnavailable(f"URL not allowed: {url}")
        if self.should_stop is not None and self.should_stop():
            raise SourceUnavailable("stopped")
        host = (urlsplit(url).hostname or "").lower()
        if host == _PACED_HOST:
            now = self.clock()
            last = self._last_request.get(host)
            wait = 0.0
            if last is not None:
                wait = max(0.0, _PACE_SECONDS - (now - last))
                if wait > 0:
                    self.sleep(wait)
            self._last_request[host] = now + wait
        self.request_count += 1
        try:
            response = self.session.get(
                url,
                timeout=self.timeout_seconds,
                stream=True,
                hooks={"response": _refuse_redirect_off_list},
            )
        except requests.RequestException as exc:
            raise SourceUnavailable(str(exc)) from exc
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise SourceUnavailable(f"HTTP {status} from {host}", status=status)
        return response

    def _json_page(self, url: str):
        data, links = self._read_with_links(url, _MAX_JSON_BYTES)
        try:
            return json.loads(data.decode("utf-8")), links
        except (ValueError, RecursionError) as exc:  # includes UnicodeDecodeError
            raise SourceUnavailable("invalid JSON") from exc

    def get_json(self, url: str) -> Any:
        return self._json_page(url)[0]

    def get_json_pages(self, url: str, max_pages: int = 100) -> JsonPages:
        """The items of a paged list endpoint, following ``Link: rel="next"`` for up to ``max_pages`` pages.

        A page that isn't a list raises: a listing read as empty would pass for a complete one.
        """
        items = JsonPages()
        next_url: str | None = url
        for _ in range(max_pages):
            if not next_url:
                break
            body, links = self._json_page(next_url)
            if not isinstance(body, list):
                raise SourceUnavailable("unexpected listing")
            items.extend(body)
            next_url = (links.get("next") or {}).get("url")
        items.truncated = bool(next_url)
        return items

    def _read_with_links(self, url: str, max_bytes: int):
        response = self._open(url)
        try:
            data = bytearray()
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise SourceUnavailable("response too large")
            except requests.RequestException as exc:
                raise SourceUnavailable(str(exc)) from exc
            return bytes(data), response.links or {}
        finally:
            response.close()

    def _read(self, url: str, max_bytes: int) -> bytes:
        return self._read_with_links(url, max_bytes)[0]

    def get_text(self, url: str, max_bytes: int = 5_000_000) -> str:
        return self._read(url, max_bytes).decode("utf-8", errors="replace")

    def download(self, url: str, max_bytes: int = 20_000_000) -> bytes:
        return self._read(url, max_bytes)


def _is_windows() -> bool:
    return os.name == "nt"


def save_preview_png(data: bytes, target: Path) -> bool:
    """Decode image bytes and write them as a PNG at ``target``, never replacing an existing file.

    An image longer than ``MAX_PREVIEW_SIDE`` on a side is scaled down to it, keeping its shape.
    Returns False (never raises) when the data is not an image, the target
    already exists, or the target's folder is missing.
    """
    if Image is None:
        return False
    target = Path(target)
    if not target.parent.is_dir():
        return False
    tmp_path = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            if max(image.size) > MAX_PREVIEW_SIDE:
                image.thumbnail((MAX_PREVIEW_SIDE, MAX_PREVIEW_SIDE), Image.LANCZOS)
            image.save(tmp_path, format="PNG")
        try:
            os.link(tmp_path, target)
            return True
        except FileExistsError:
            return False
        except OSError:
            pass
        # Hard links unsupported here.
        if _is_windows():
            # Atomic; raises FileExistsError when the target exists.
            os.rename(tmp_path, target)
            return True
        # Exclusive create never overwrites; remove a partial target on any failure.
        out = open(target, "xb")
        try:
            out.write(tmp_path.read_bytes())
            out.flush()
            out.close()
        except BaseException:
            try:
                out.close()
            except Exception:  # noqa: BLE001
                pass
            target.unlink(missing_ok=True)
            raise
        return True
    except FileExistsError:
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
