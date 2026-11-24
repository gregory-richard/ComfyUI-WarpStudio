from __future__ import annotations

import io
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urljoin, urlsplit

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
        self.session = requests.Session()
        # Caches the model payload, None for a silent miss, or the error that
        # a strict lookup raised, so several files of one model share a call.
        self._model_cache: dict[str, dict | None | CivitaiRequestError] = {}
        self.default_headers = {
            "User-Agent": USER_AGENT,
        }
        api_key = (api_key or "").strip()
        if api_key:
            self.default_headers["Authorization"] = f"Bearer {api_key}"

    def get_version_by_hash(self, sha256_hash: str) -> dict | None:
        return self._get_json(f"{VERSION_BY_HASH_URL}/{sha256_hash}")

    def get_model(self, model_id: int | str, *, raise_on_error: bool = False) -> dict | None:
        """Fetch a model page payload.

        With ``raise_on_error`` a request that fails after retries, or that
        Civitai refuses (401/403) or cannot find (404), raises
        ``CivitaiRequestError`` instead of returning None, so callers can tell
        a missing model from an empty one.
        """
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

    def _get_json(self, url: str, *, raise_on_error: bool = False) -> dict | None:
        last_error = None
        for attempt in range(self.max_retries + 1):
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

            if self.should_stop and self.should_stop():
                break
            if attempt < self.max_retries:
                time.sleep(_retry_delay_seconds(attempt))

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


def _retry_delay_seconds(attempt: int) -> float:
    return min(10.0, 0.5 * (2**attempt))


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

