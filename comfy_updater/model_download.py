"""Stream one model file from a Civitai download link into place.

The file is written to ``<target>.part`` while it is hashed, checked against
Civitai's SHA-256 and only then renamed to its name. Any failure or Stop
deletes the ``.part``. An existing file is never overwritten.

The API key goes only to Civitai hosts over https. Civitai answers a model
download with a redirect to its storage host, which is followed without the key
as long as it is a public https address.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import requests

from .civitai_client import _is_civitai_https_url
from .constants import USER_AGENT
from .renamer import _match_file

API_KEY_MESSAGE = "Civitai needs an API key for this download: Settings → Warp Studio → Civitai Network → API Key"
HASH_MESSAGE = "The downloaded file doesn't match Civitai's SHA-256. It was deleted."
EARLY_STOP_MESSAGE = "The download stopped early. Try again."
NO_SHA_MESSAGE = "Civitai lists no SHA-256 for this file, so it can't be checked."
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class DownloadError(RuntimeError):
    """A download that failed. The text is what the card shows."""


class DownloadStopped(Exception):
    """The user stopped the download."""


def pick_file(version: dict, sha256: str = "") -> Optional[dict]:
    """The file of a version that a hash names, else the primary (or first main) file."""
    return _match_file(version, sha256)


def file_sha256(file: dict) -> str:
    """A file's SHA-256, lowercased, or an empty string when Civitai lists none."""
    hashes = file.get("hashes") if isinstance(file, dict) and isinstance(file.get("hashes"), dict) else {}
    value = hashes.get("SHA256") or hashes.get("sha256") or ""
    return value.strip().lower() if isinstance(value, str) else ""


def is_public_https(url: str) -> bool:
    """An https URL whose host is not localhost or a local, private or reserved IP address."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme != "https" or not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def open_download(session, url: str, *, api_key: str, timeout: float, max_redirects: int = 5):
    """Follow a download link hop by hop and return the final streaming response."""
    if not _is_civitai_https_url(url):
        raise DownloadError("Refused a download link that isn't Civitai's.")
    hops = 0
    while True:
        on_civitai = _is_civitai_https_url(url)
        headers = {"User-Agent": USER_AGENT}
        if on_civitai and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(timeout, timeout),
            allow_redirects=False,
        )
        try:
            status = response.status_code
            if status in _REDIRECT_STATUSES and response.headers.get("Location"):
                hops += 1
                if hops > max_redirects:
                    raise DownloadError("Too many redirects.")
                url = urljoin(url, response.headers["Location"])
                if not _is_civitai_https_url(url) and not is_public_https(url):
                    raise DownloadError(f"Refused a redirect to {urlsplit(url).hostname or 'that address'}.")
                response.close()
                continue
            if 200 <= status < 300:
                content_type = str(response.headers.get("Content-Type", "")).lower()
                if on_civitai and content_type.startswith("text/html"):
                    raise DownloadError(API_KEY_MESSAGE)
                return response
            if on_civitai and status in (401, 403):
                raise DownloadError(API_KEY_MESSAGE)
            raise DownloadError(f"The download failed (HTTP {status} {getattr(response, 'reason', '')}).")
        except BaseException:
            response.close()
            raise


def download_to(
    target: Path,
    url: str,
    sha256: str,
    *,
    session,
    api_key: str,
    timeout: float,
    should_stop: Callable[[], bool],
    on_progress: Callable[[int, int], None],
    expected_size: int = 0,
    chunk_size: int = 1024 * 1024,
) -> None:
    """Download one file to ``target``, through a ``.part`` file checked against ``sha256``."""
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"A file named {target.name} is already there.")
    if not sha256:
        raise DownloadError(NO_SHA_MESSAGE)
    wanted = sha256.strip().lower()
    part = target.with_name(target.name + ".part")
    response = None
    handle = None
    try:
        response = open_download(session, url, api_key=api_key, timeout=timeout)
        length = _content_length(response)
        total = length or max(int(expected_size or 0), 0)
        digest = hashlib.sha256()
        written = 0
        handle = open(part, "wb")
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            handle.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            on_progress(written, total)
            if should_stop():
                raise DownloadStopped()
        handle.close()
        handle = None
        response.close()
        response = None
        if length and written != length:
            raise DownloadError(EARLY_STOP_MESSAGE)
        if digest.hexdigest() != wanted:
            raise DownloadError(HASH_MESSAGE)
        if target.exists():
            raise FileExistsError(f"A file named {target.name} is already there.")
        os.rename(part, target)
    except requests.RequestException as exc:
        _cleanup(handle, response, part)
        raise DownloadError(f"The download failed: {exc}"[:300]) from exc
    except BaseException:
        _cleanup(handle, response, part)
        raise


def _content_length(response) -> int:
    try:
        return max(int(response.headers.get("Content-Length") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _cleanup(handle, response, part: Path) -> None:
    """Close the file and the response first (Windows can't unlink an open file), then drop the part."""
    for item in (handle, response):
        if item is not None:
            try:
                item.close()
            except Exception:
                pass
    try:
        part.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
