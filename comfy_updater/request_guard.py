"""Reject requests to the updater's HTTP routes that did not come from its own panel.

ComfyUI's server has no authentication, so any web page the user visits can
send requests to it. The checks below stop those cross-site requests before a
route reads its body or touches the disk:

* every request must carry ``X-Civitai-Updater: 1``. A browser sends a custom
  header cross-origin only after a CORS preflight, which ComfyUI does not grant
  unless it runs with ``--enable-cors-header``;
* when the browser names the request's origin (``Origin``) or reports it as
  cross-site (``Sec-Fetch-Site``), it must be the ComfyUI page itself;
* a POST body must be sent as ``application/json``. aiohttp's ``request.json()``
  ignores the content type, so without this a ``text/plain`` "simple" request
  would be parsed as JSON.
"""

from __future__ import annotations

from urllib.parse import urlsplit

GUARD_HEADER = "X-Civitai-Updater"
GUARD_VALUE = "1"


def request_rejection(method: str, headers, host: str) -> str | None:
    """Return why the request is refused, or None when it may proceed.

    ``headers`` is any case-insensitive mapping (aiohttp's ``request.headers``);
    ``host`` is the ``Host`` the request was sent to (``request.host``).
    """
    if headers.get(GUARD_HEADER) != GUARD_VALUE:
        return f"Missing {GUARD_HEADER} header."

    fetch_site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site == "cross-site":
        return "Cross-site requests are not allowed."

    origin = headers.get("Origin")
    if origin is not None and not _same_origin(origin, host):
        return "Cross-origin requests are not allowed."

    if method.upper() == "POST":
        content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return "Request body must be application/json."

    return None


def _same_origin(origin: str, host: str) -> bool:
    origin = origin.strip()
    if not origin or origin.lower() == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return parts.netloc.lower() == (host or "").strip().lower()
