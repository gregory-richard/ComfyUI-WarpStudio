"""Reject requests to Warp Studio's HTTP routes that did not come from its own pages.

ComfyUI's server has no authentication, so any web page the user visits can
send requests to it. The checks below stop those cross-site requests before a
route reads its body or touches the disk:

* every request must carry ``X-Civitai-Updater: 1``. A browser sends a custom
  header cross-origin only after a CORS preflight, which ComfyUI does not grant
  unless it runs with ``--enable-cors-header``;
* while ComfyUI listens only on this computer, the request must be addressed to
  ``localhost``, an IP address or a host the user trusts. A page on
  ``attacker.example`` can re-resolve its own name to 127.0.0.1 (DNS
  rebinding); its requests are then same-origin and pass every other check, but
  they still name ``attacker.example`` in ``Host``;
* when the browser names the request's origin (``Origin``) or reports it as
  cross-site (``Sec-Fetch-Site``), it must be the ComfyUI page itself;
* a POST body must be sent as ``application/json``. aiohttp's ``request.json()``
  ignores the content type, so without this a ``text/plain`` "simple" request
  would be parsed as JSON.
"""

from __future__ import annotations

import functools
import ipaddress
import os
from typing import Optional
from urllib.parse import urlsplit

GUARD_HEADER = "X-Civitai-Updater"
GUARD_VALUE = "1"
TRUSTED_HOSTS_ENV = "WARP_STUDIO_TRUSTED_HOSTS"

# Stands for "ask ComfyUI" in request_rejection, so None can mean "not known".
_FROM_COMFY = object()
_comfy_listen_cache: Optional[str] = None


def request_rejection(
    method: str,
    headers,
    host: str,
    *,
    listen=_FROM_COMFY,
    trusted_hosts: Optional[str] = None,
) -> str | None:
    """Return why the request is refused, or None when it may proceed.

    ``headers`` is any case-insensitive mapping (aiohttp's ``request.headers``);
    ``host`` is the ``Host`` the request was sent to (``request.host``).
    ``listen`` is ComfyUI's ``--listen`` value (None when unknown); by default it
    is read from ComfyUI. ``trusted_hosts`` is the raw value of
    ``WARP_STUDIO_TRUSTED_HOSTS``; by default it is read from the environment.
    """
    if headers.get(GUARD_HEADER) != GUARD_VALUE:
        return f"Missing {GUARD_HEADER} header."

    if listen is _FROM_COMFY:
        listen = _comfy_listen()
    if trusted_hosts is None:
        trusted_hosts = os.environ.get(TRUSTED_HOSTS_ENV, "")
    if listens_only_on_loopback(listen):
        refused = host_rejection(host, parse_trusted_hosts(trusted_hosts))
        if refused:
            return refused

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


def guarded(handler):
    """Wrap an aiohttp route handler so a request ``request_rejection`` refuses gets a 403.

    Every JSON route of the package uses it: the updater's, the Gallery's and the
    Prompt Writer's. The thumbnail route cannot, because an ``<img>`` sends no header.
    """

    @functools.wraps(handler)
    async def wrapper(request):
        reason = request_rejection(request.method, request.headers, request.host)
        if reason:
            from aiohttp import web

            return web.json_response({"error": reason}, status=403)
        return await handler(request)

    return wrapper


def listens_only_on_loopback(listen: Optional[str]) -> Optional[bool]:
    """Whether every address in a ``--listen`` value is this computer's own, or None if unknown.

    An address that is not an IP literal (a hostname) counts as not loopback:
    the host check is skipped rather than locking out a setup it cannot judge.
    """
    if listen is None:
        return None
    addresses = [part.strip().strip("[]") for part in listen.split(",") if part.strip()]
    if not addresses:
        return None
    for address in addresses:
        if address.lower() == "localhost":
            continue
        try:
            if not ipaddress.ip_address(address).is_loopback:
                return False
        except ValueError:
            return False
    return True


def parse_trusted_hosts(value: Optional[str]) -> frozenset:
    """The host names in a ``WARP_STUDIO_TRUSTED_HOSTS`` value, compared the way ``Host`` is."""
    names = (_host_name(part) for part in (value or "").split(","))
    return frozenset(name for name in names if name)


def host_rejection(host: Optional[str], trusted: frozenset = frozenset()) -> str | None:
    """Why a request addressed to ``host`` is refused on a loopback-only ComfyUI, or None.

    DNS rebinding needs a DNS name the attacker controls, so an IP literal and
    ``localhost`` (which browsers never look up in DNS) are always fine.
    """
    name = _host_name(host)
    if not name:
        return (
            "Warp Studio refused this request because it did not say which address "
            "it was sent to. Open ComfyUI at http://127.0.0.1 or http://localhost."
        )
    if name == "localhost" or name.endswith(".localhost") or _is_ip_literal(name) or name in trusted:
        return None
    shown = name[:253]
    return (
        f'Warp Studio refused this request because it was sent to "{shown}", which is '
        "not this computer's own address (a web page can use such a name to reach "
        "ComfyUI behind your back). If you open ComfyUI through a tunnel or proxy at "
        f"that address on purpose, set the environment variable {TRUSTED_HOSTS_ENV}={shown} "
        "before starting ComfyUI, then restart it."
    )


def _host_name(host: Optional[str]) -> str:
    """The name or address in a ``Host`` value: lower case, without port, brackets or trailing dot."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return ""
        rest = host[end + 1 :]
        if rest and not rest.startswith(":"):
            return ""
        # Only an IPv6 address may be bracketed; anything else is not a usable host.
        inner = host[1:end]
        return inner if _is_ip_literal(inner) and ":" in inner else ""
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    if host.endswith("."):
        host = host[:-1]
    return host


def _is_ip_literal(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _comfy_listen() -> Optional[str]:
    """ComfyUI's ``--listen`` value, or None when ComfyUI is not importable (as in tests).

    Imported here, not at module level, so importing this module never imports
    ComfyUI. Parsed like comfy_queue.comfy_base_url: a comma-separated list.
    """
    global _comfy_listen_cache
    if _comfy_listen_cache is not None:
        return _comfy_listen_cache
    try:
        from comfy.cli_args import args  # type: ignore
    except Exception:
        return None
    listen = getattr(args, "listen", None)
    if isinstance(listen, str):
        _comfy_listen_cache = listen
        return listen
    return None


def _same_origin(origin: str, host: str) -> bool:
    origin = origin.strip()
    if not origin or origin.lower() == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return parts.netloc.lower() == (host or "").strip().lower()
