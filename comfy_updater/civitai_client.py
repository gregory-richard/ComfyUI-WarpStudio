from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import io
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from .constants import (
    MODEL_BY_ID_URL,
    MODEL_PAGE_BASE_URL,
    MODEL_VERSION_BY_ID_URL,
    USER_AGENT,
    VERSION_BY_HASH_URL,
)

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional import guard
    Image = None


# Bounds on a Retry-After wait: not so short that a "0" hammers the server, not
# so long that one odd header parks a whole check for an hour.
RETRY_AFTER_MIN_SECONDS = 1.0
RETRY_AFTER_MAX_SECONDS = 120.0
# How long a cool-down sleep runs before Stop is looked at again.
_COOLDOWN_SLICE_SECONDS = 1.0

# Civitai's category is one of a model's tags; these are the tags that are one.
CATEGORY_KEYS = (
    "character",
    "style",
    "celebrity",
    "concept",
    "clothing",
    "base model",
    "poses",
    "background",
    "tool",
    "buildings",
    "vehicle",
    "objects",
    "animal",
    "action",
    "assets",
)


def _category_of(tags) -> str:
    """The first of the model's tags, in Civitai's order, that is a category; "" for none."""
    for tag in tags if isinstance(tags, list) else []:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if isinstance(name, str) and name.strip().lower() in CATEGORY_KEYS:
            return name.strip().lower()
    return ""


class CivitaiClient:
    def __init__(self, api_key: str, timeout_seconds: int, max_retries: int, should_stop=None):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        # Checked between retries, so a stopped job does not sit through the
        # whole back-off when Civitai is failing.
        self.should_stop = should_stop
        # Earliest time.monotonic() at which a request may go out. Civitai's
        # Retry-After applies to the whole client, not to the request that got
        # it, so threads sharing this client do not each hit the same 429.
        self._cooldown_until = 0.0
        self._cooldown_lock = threading.Lock()
        self.session = requests.Session()
        # Caches the model payload, None for a silent miss, or the error that
        # a strict lookup raised, so several files of one model share a call.
        self._model_cache: dict[str, dict | None | CivitaiRequestError] = {}
        # What this run learned about each creator, so a creator with several
        # removed models is asked about once. Filled by the updater service.
        self.creator_status_cache: dict[str, str] = {}
        self.default_headers = {
            "User-Agent": USER_AGENT,
        }
        api_key = (api_key or "").strip()
        if api_key:
            self.default_headers["Authorization"] = f"Bearer {api_key}"

    def get_version_by_hash(self, sha256_hash: str, *, raise_on_error: bool = False) -> dict | None:
        """Look a file up by its SHA256.

        With ``raise_on_error`` a failure raises ``CivitaiRequestError`` as for
        ``get_model``; ``status`` 404 (or 400) is the one that means Civitai
        does not know the hash.
        """
        # Hashes come from sidecars, which shared model packs also write: only
        # a real SHA256 goes into the path of a request that carries the key.
        if not isinstance(sha256_hash, str) or not _SHA256_RE.fullmatch(sha256_hash):
            return _not_found(raise_on_error, f"Not a SHA256 hash: {sha256_hash!r}")
        return self._get_json(f"{VERSION_BY_HASH_URL}/{sha256_hash}", raise_on_error=raise_on_error)

    def get_model(self, model_id: int | str, *, raise_on_error: bool = False) -> dict | None:
        """Fetch a model page payload.

        With ``raise_on_error`` a request that fails after retries, or that
        Civitai refuses (401/403) or cannot find (404), raises
        ``CivitaiRequestError`` instead of returning None, so callers can tell
        a missing model from an empty one.
        """
        if _path_id(model_id) is None:
            return _not_found(raise_on_error, f"Not a Civitai model id: {model_id!r}")
        key = str(model_id)
        if key not in self._model_cache:
            try:
                self._model_cache[key] = self._get_json(f"{MODEL_BY_ID_URL}/{model_id}", raise_on_error=raise_on_error)
            except CivitaiRequestError as exc:
                self._model_cache[key] = exc
                raise
        cached = self._model_cache[key]
        if isinstance(cached, CivitaiRequestError):
            if raise_on_error:
                raise cached
            return None
        return cached

    def get_version(self, version_id: int | str, *, raise_on_error: bool = False) -> dict | None:
        if _path_id(version_id) is None:
            return _not_found(raise_on_error, f"Not a Civitai version id: {version_id!r}")
        return self._get_json(f"{MODEL_VERSION_BY_ID_URL}/{version_id}", raise_on_error=raise_on_error)

    def get_model_versions_for_model(self, model_id: int | str) -> tuple[str, list[dict], bool]:
        model = self.get_model(model_id, raise_on_error=True)
        if not model:
            return ("", [], False)
        versions = model.get("modelVersions") or []
        creator = model.get("creator")
        creator_name = ""
        if isinstance(creator, dict):
            creator_name = creator.get("username") or ""
        is_nsfw = bool(model.get("nsfw"))
        return (creator_name, versions, is_nsfw)

    def model_stats(self, model_id: int | str) -> dict:
        """What a gallery card shows of the model; {} when it is unknown or errored.

        The counts, how many versions it has, the creator's avatar, Civitai's
        category and every base it covers. Reads the cached model, so it costs
        no request after a versions lookup.
        """
        model = self.get_model(model_id)
        if not isinstance(model, dict):
            return {}
        stats = model.get("stats")
        if not isinstance(stats, dict):
            stats = {}

        def count(key: str) -> int:
            value = stats.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        versions = [v for v in (model.get("modelVersions") or []) if isinstance(v, dict)]
        bases: list[str] = []
        for version in versions:
            base = version.get("baseModel")
            if isinstance(base, str) and base.strip() and base not in bases:
                bases.append(base)
        creator = model.get("creator")
        image = creator.get("image") if isinstance(creator, dict) else None
        return {
            "downloads": count("downloadCount"),
            "likes": count("thumbsUpCount"),
            "comments": count("commentCount"),
            "tipped": count("tippedAmountCount"),
            "versions": len(versions),
            "creatorImage": image if isinstance(image, str) and image.startswith("https://") else "",
            "category": _category_of(model.get("tags")),
            "bases": bases,
        }

    def creator_lookup(self, username: str) -> str | None:
        """Whether Civitai still has this account: "active", "gone" or "missing".

        "active": the account lists models. "gone": it answers but lists none.
        "missing": Civitai has no account by that name (HTTP 404). None when
        nothing can be claimed: a network error, a timeout, another failure
        status or an unreadable reply.
        """
        query = urlencode({"username": username, "limit": 1, "nsfw": "true"}, quote_via=quote)
        try:
            payload = self._get_json(f"{MODEL_BY_ID_URL}?{query}", raise_on_error=True)
        except CivitaiRequestError as exc:
            return "missing" if exc.status == 404 else None
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return None
        return "active" if items else "gone"

    def model_page_url(self, model_id: int | str) -> str:
        return f"{MODEL_PAGE_BASE_URL}/{model_id}"

    def version_page_url(self, model_id: int | str, version_id: int | str | None = None) -> str:
        if version_id:
            return f"{MODEL_PAGE_BASE_URL}/{model_id}?modelVersionId={version_id}"
        return self.model_page_url(model_id)

    def download_file(
        self,
        url: str,
        target_path: Path,
        max_bytes: int = 10_000_000,
        allow_truncated: bool = False,
    ) -> bool:
        """Download a file (e.g. preview image) to *target_path*. Returns True on success.

        With ``allow_truncated`` the first ``max_bytes`` are kept when the
        source is larger, instead of treating the size limit as a failure.
        """
        tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
        try:
            with self.session.get(
                url, timeout=self.timeout_seconds, headers=self._media_headers(url), stream=True,
                hooks={"response": _refuse_foreign_redirect},
            ) as response:
                if not response.ok:
                    return False
                target_path.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                truncated = False
                with tmp_path.open("wb") as fh:
                    for chunk in response.iter_content(chunk_size=8192):
                        if written + len(chunk) > max_bytes:
                            if allow_truncated:
                                fh.write(chunk[: max_bytes - written])
                            truncated = True
                            break
                        fh.write(chunk)
                        written += len(chunk)
            # Windows cannot unlink a file that is still open, so delete only
            # after both the file handle and the response are closed.
            if truncated and not allow_truncated:
                tmp_path.unlink(missing_ok=True)
                return False
            tmp_path.replace(target_path)
            return True
        except Exception:  # noqa: BLE001
            tmp_path.unlink(missing_ok=True)
            return False

    def download_image_as_png(self, url: str, target_path: Path, max_bytes: int = 10_000_000) -> bool:
        """Download an image URL and save it as a PNG file."""
        if not _is_civitai_https_url(url):
            return False
        data = self._download_bytes(url, max_bytes=max_bytes)
        if not data:
            return False

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
        try:
            if Image is None:
                if not _is_png_data(data):
                    return False
                with tmp_path.open("wb") as fh:
                    fh.write(data)
                tmp_path.replace(target_path)
                return True

            with Image.open(io.BytesIO(data)) as image:
                if image.mode not in ("RGB", "RGBA"):
                    has_alpha = "A" in image.getbands()
                    image = image.convert("RGBA" if has_alpha else "RGB")
                image.save(tmp_path, format="PNG")
            tmp_path.replace(target_path)
            return True
        except Exception:  # noqa: BLE001
            tmp_path.unlink(missing_ok=True)
            return False

    def download_video_first_frame_as_png(
        self,
        url: str,
        target_path: Path,
        max_bytes: int = 80_000_000,
        head_bytes: int = 12_000_000,
    ) -> bool:
        """Download a video URL and extract its first frame to PNG via ffmpeg.

        Preview videos on Civitai are regularly 30-60MB, so the first
        ``head_bytes`` are tried first — enough to decode frame one of a
        web-optimized MP4 — before falling back to the full download.
        """
        if not _is_civitai_https_url(url):
            return False
        ffmpeg_path = _resolve_ffmpeg()
        if not ffmpeg_path:
            return False

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_video = target_path.with_suffix(".preview_video.tmp.mp4")
        tmp_png = target_path.with_suffix(".preview_frame.tmp.png")

        try:
            if not self.download_file(url, tmp_video, max_bytes=head_bytes, allow_truncated=True):
                return False
            if _extract_first_frame(ffmpeg_path, tmp_video, tmp_png, target_path):
                return True

            # A file smaller than head_bytes was complete — retrying with a
            # full download cannot help.
            was_truncated = tmp_video.exists() and tmp_video.stat().st_size >= head_bytes
            if not was_truncated:
                return False

            # Slow path for videos whose metadata sits at the end of the file.
            if not self.download_file(url, tmp_video, max_bytes=max_bytes):
                return False
            return _extract_first_frame(ffmpeg_path, tmp_video, tmp_png, target_path)
        finally:
            tmp_video.unlink(missing_ok=True)

    def _media_headers(self, url: str) -> dict:
        """Headers for a preview download.

        Preview URLs come from sidecars, which other tools and shared model
        packs also write, so the API key goes only to Civitai over https.
        """
        if _is_civitai_https_url(url):
            return self.default_headers
        return {"User-Agent": USER_AGENT}

    def _extend_cooldown(self, seconds: float) -> None:
        """No request of this client before ``seconds`` from now; never shortens an earlier wait."""
        until = time.monotonic() + seconds
        with self._cooldown_lock:
            if until > self._cooldown_until:
                self._cooldown_until = until

    def _wait_for_cooldown(self) -> bool:
        """Sleeps out the shared cool-down; False when Stop was asked meanwhile."""
        while True:
            with self._cooldown_lock:
                remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return True
            if self.should_stop and self.should_stop():
                return False
            # Sliced so Stop is noticed within a second, and so a later 429
            # that extends the cool-down is picked up on the next pass.
            time.sleep(min(remaining, _COOLDOWN_SLICE_SECONDS))

    def _get_json(self, url: str, *, raise_on_error: bool = False) -> dict | None:
        last_error = None
        for attempt in range(self.max_retries + 1):
            # Sitting out a cool-down another request set is not an attempt.
            if not self._wait_for_cooldown():
                # Nothing was sent, which is not an answer: a caller that asked
                # for errors must not read it as "Civitai does not have this".
                last_error = last_error or "stopped while waiting out Civitai's rate limit"
                break
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers=self.default_headers,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                response = None
            else:
                if response.ok:
                    try:
                        return response.json()
                    except ValueError:
                        # An HTML page with status 200 (a Cloudflare check) is a
                        # failure, not an answer that the item does not exist.
                        if raise_on_error:
                            raise CivitaiRequestError(f"{url} :: {response.status_code} reply was not JSON")
                        return None
                if response.status_code in (401, 403):
                    if "Authorization" in self.default_headers:
                        detail = "Civitai rejected the API key. Verify it in the settings panel."
                    else:
                        detail = "Civitai denied the request. This resource may require an API key — set one in the settings panel."
                    print(f"Civitai updater: {response.status_code} {response.reason} for {url}. {detail}")
                    if raise_on_error:
                        raise CivitaiRequestError(
                            f"{response.status_code} {response.reason}. {detail}", status=response.status_code
                        )
                    return None
                if response.status_code in (400, 404):
                    if raise_on_error:
                        raise CivitaiRequestError(
                            f"{response.status_code} {response.reason}. The model may have been removed from Civitai.",
                            status=response.status_code,
                        )
                    return None
                last_error = f"{response.status_code} {response.reason}"

            told_to_wait = None
            if response is not None and (
                response.status_code == 429 or response.status_code == 503
            ):
                told_to_wait = _retry_after_seconds(response)
                if told_to_wait is None and response.status_code == 429 and attempt < self.max_retries:
                    # A 429 that names no wait still means "everyone slow down":
                    # the usual backoff becomes the shared cool-down, so the
                    # other pending lookups do not each run into it as well.
                    told_to_wait = _retry_delay_seconds(attempt)
            if told_to_wait is not None:
                # Recorded even on the last attempt: other requests of this
                # client are still to be sent and should not hit the same wall.
                self._extend_cooldown(told_to_wait)

            if self.should_stop and self.should_stop():
                break
            if attempt < self.max_retries and told_to_wait is None:
                time.sleep(_retry_delay_seconds(attempt))
            # With a Retry-After the wait is the shared cool-down, which the
            # next pass through the loop sits out before sending.

        if last_error:
            print(f"Civitai updater request failed: {url} :: {last_error}")
            if raise_on_error:
                raise CivitaiRequestError(f"{url} :: {last_error}")
        return None

    def _download_bytes(self, url: str, max_bytes: int) -> bytes | None:
        try:
            with self.session.get(
                url,
                timeout=self.timeout_seconds,
                headers=self._media_headers(url),
                stream=True,
                hooks={"response": _refuse_foreign_redirect},
            ) as response:
                if not response.ok:
                    return None
                chunks: list[bytes] = []
                written = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception:  # noqa: BLE001
            return None


def _extract_first_frame(ffmpeg_path: str, video_path: Path, tmp_png: Path, target_path: Path) -> bool:
    try:
        process = subprocess.run(  # noqa: S603
            [
                ffmpeg_path,
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                # The download is untrusted: a playlist or concat file named
                # .mp4 would otherwise make ffmpeg open other local files or
                # URLs. Only plain local reads of real video containers.
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                _FFMPEG_INPUT_FORMATS,
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(tmp_png),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if process.returncode != 0 or not tmp_png.exists() or tmp_png.stat().st_size == 0:
            tmp_png.unlink(missing_ok=True)
            return False
        tmp_png.replace(target_path)
        return True
    except Exception:  # noqa: BLE001
        tmp_png.unlink(missing_ok=True)
        return False


_FFMPEG_INPUT_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm"

_ffmpeg_cache: dict = {}


def _resolve_ffmpeg() -> str | None:
    """Locate ffmpeg once per process; warn once when it is unavailable."""
    if "path" in _ffmpeg_cache:
        return _ffmpeg_cache["path"]

    path = shutil.which("ffmpeg")
    if not path:
        try:
            # imageio-ffmpeg ships a bundled binary and is a common transitive
            # dependency in ComfyUI installs even when ffmpeg is not on PATH.
            import imageio_ffmpeg

            path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001 - any failure means "no ffmpeg available"
            path = None

    if not path:
        print(
            "Civitai updater: ffmpeg was not found, so video previews cannot be "
            "converted to PNG. Install ffmpeg (or the imageio-ffmpeg Python "
            "package) to enable video preview thumbnails."
        )
    _ffmpeg_cache["path"] = path
    return path


_CIVITAI_DOMAINS = ("civitai.red", "civitai.com")


def _is_civitai_https_url(url: str) -> bool:
    # urlsplit treats a backslash as an ordinary character, urllib3 as the end of the
    # host, so a link with user info or a backslash could send the key elsewhere.
    authority = url.split("://", 1)[1] if "://" in url else ""
    authority = re.split(r"[/?#]", authority, maxsplit=1)[0]
    if "@" in authority or "\\" in authority:
        return False
    parts = urlsplit(url)
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == domain or host.endswith(f".{domain}") for domain in _CIVITAI_DOMAINS)


def _refuse_foreign_redirect(response, *args, **kwargs):
    """Stop a preview download that redirects anywhere but Civitai over https.

    Civitai's image host redirects to its own blob storage; anything else
    (another site, a local address) is refused so a preview URL cannot be
    used to reach other servers.
    """
    if response.is_redirect:
        target = urljoin(response.url, response.headers.get("Location", ""))
        if not _is_civitai_https_url(target):
            response.close()
            raise requests.TooManyRedirects(f"Refused redirect to {target}")
    return response


_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_ID_RE = re.compile(r"[0-9]+")


def _path_id(value) -> str | None:
    """A Civitai id as a URL path segment: digits only, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and _ID_RE.fullmatch(value):
        return value
    return None


def _not_found(raise_on_error: bool, detail: str) -> None:
    """What a lookup of a value that cannot be on Civitai answers, with no request sent."""
    if raise_on_error:
        raise CivitaiRequestError(f"404 Not Found. {detail}", status=404)
    return None


def _retry_delay_seconds(attempt: int) -> float:
    return min(10.0, 0.5 * (2**attempt))


def _retry_after_seconds(response) -> float | None:
    """The wait a 429/503 asked for, clamped; None when absent or unusable.

    Retry-After is whole seconds or an HTTP date. A bad or negative value is
    ignored (the caller then backs off as usual) rather than guessed at.
    """
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d+", raw):
        seconds = float(raw)
    else:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - _wall_now()).total_seconds()
    if seconds < 0:
        return None
    return min(RETRY_AFTER_MAX_SECONDS, max(RETRY_AFTER_MIN_SECONDS, seconds))


def _wall_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_png_data(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


class CivitaiRequestError(RuntimeError):
    """Raised when a Civitai request fails after retries.

    ``status`` is the HTTP status when Civitai answered with a definite refusal
    (400/401/403/404); None for failures that were retried.
    """

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

