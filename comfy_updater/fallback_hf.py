"""Source 2: Hugging Face repos, matched by the file's exact SHA256.

The index is built from public repo listings (``lfs.oid`` is a file's SHA256)
and compared on this machine; nothing about the user's files is sent.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .fallback_http import is_allowed_url
from .fallback_types import FallbackResult, SourceUnavailable
from .sidecar import read_json, write_json

MAX_REPOS_PER_AUTHOR = 200
MAX_TREE_PAGES = 30
MAX_REQUESTS_PER_RUN = 300

# Repo name -> (framework key in data-hf-images.json, Civitai base name).
MALCOLMREY_REPOS: dict[str, tuple[str, str]] = {
    "krea2": ("Krea2", "Krea 2"),
    "zimage": ("ZImage", "ZImageTurbo"),
    "zbase": ("ZBase", "ZImageBase"),
    "klein9": ("Klein9", "Flux.2 Klein 9B"),
    "flux": ("Flux", "Flux.1 D"),
    "moss": ("Moss", "MOSS Image"),
    "minimaxh3": ("MinimaxH3", "MiniMax H3"),
    "qwen": ("Qwen", "Qwen"),
}

# Lowercase ``cardData.base_model`` -> Civitai base name.
HF_BASE_NAMES: dict[str, str] = {"openmoss-team/moss-image-base": "MOSS Image"}

_SITE = "https://huggingface.co"
_API = _SITE + "/api/models"
_SAMPLES_URL = _SITE + "/spaces/malcolmrey/browser/resolve/main/data-hf-images.json"
_MALCOLMREY = "malcolmrey"
_LISTING_LIMIT = 100
_LIMIT_REACHED = "Hugging Face request limit reached"
# Answers that mean the repo or author is not there: Hugging Face gives 401 for a missing or private repo.
_GONE = frozenset({401, 404})
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_IMAGE_FOLDERS = frozenset({"assets", "images", "samples", "examples"})
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MALCOLMREY_FILE = re.compile(r"[A-Za-z0-9]+_(.+?)_(v\d+(?:\.\d+)*)(?:_.*)?", re.IGNORECASE)


def parse_malcolmrey_name(filename: str) -> tuple[str, str] | None:
    """``<repo>_<slug>_<version>[_<tool>].<ext>`` -> ``(slug, version)``."""
    stem = PurePosixPath(str(filename).replace("\\", "/")).stem
    match = _MALCOLMREY_FILE.fullmatch(stem)
    return (match.group(1), match.group(2)) if match else None


def pick_repo_image(file_path: str, images: list[str]) -> str | None:
    """Same stem (same folder first), then same folder, then a usual image folder, then any image."""
    model = PurePosixPath(file_path)
    stem, folder = model.stem.lower(), str(model.parent).lower()

    def same_stem(p: PurePosixPath) -> bool:
        return p.stem.lower() == stem

    def same_folder(p: PurePosixPath) -> bool:
        return str(p.parent).lower() == folder

    tiers = (
        lambda p: same_stem(p) and same_folder(p),
        same_stem,
        same_folder,
        lambda p: any(part.lower() in _IMAGE_FOLDERS for part in p.parent.parts),
        lambda p: True,
    )
    for matches in tiers:
        for image in images:
            if matches(PurePosixPath(image)):
                return image
    return None


def _is_name(value) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def _is_repo_id(value) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return len(parts) == 2 and all(_is_name(part) for part in parts)


def _owner(repo_id: str) -> str:
    return repo_id.split("/", 1)[0]


def _malcolmrey_repo(repo_id: str) -> tuple[str, str] | None:
    owner, _, name = repo_id.partition("/")
    return MALCOLMREY_REPOS.get(name.lower()) if owner.lower() == _MALCOLMREY else None


def _spaced(text: str) -> str:
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def _sha_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _card_base(info) -> str:
    card = info.get("cardData") if isinstance(info, dict) else None
    base = card.get("base_model") if isinstance(card, dict) else None
    if isinstance(base, list):
        base = next((item for item in base if isinstance(item, str)), None)
    return HF_BASE_NAMES.get(base.strip().lower(), "") if isinstance(base, str) else ""


def _cached_repos(raw) -> dict[str, dict]:
    """The usable repos of a cache file; anything malformed counts as absent."""
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("repos"), dict):
        return {}
    repos: dict[str, dict] = {}
    for repo_id, data in raw["repos"].items():
        if not _is_repo_id(repo_id) or not isinstance(data, dict):
            continue
        sha, base, files, images = (data.get(k) for k in ("sha", "baseModel", "files", "images"))
        if not (isinstance(sha, str) and isinstance(base, str) and isinstance(files, dict) and isinstance(images, list)):
            continue
        repos[repo_id] = {
            "sha": sha,
            "baseModel": base,
            "files": {k: v for k, v in files.items() if _SHA256.fullmatch(k) and isinstance(v, str) and v},
            "images": [p for p in images if isinstance(p, str) and p],
        }
    return repos


def _tree_entry(sha: str, base: str, items: list) -> dict:
    files: dict[str, str] = {}
    images: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type", "file") != "file":
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        if path.lower().endswith(_IMAGE_SUFFIXES):
            images.append(path)
            continue
        lfs = item.get("lfs")
        oid = lfs.get("oid") if isinstance(lfs, dict) else None
        oid = oid.strip().lower() if isinstance(oid, str) else ""
        if _SHA256.fullmatch(oid):
            files.setdefault(oid, path)
    return {"sha": sha, "baseModel": base, "files": files, "images": images}


class HuggingFaceIndex:
    """Hash index over the configured authors and repos, built on the first ``find`` of a run.

    A 404 or 401 entry contributes nothing. Any other failure, a stop, or the request
    budget makes the index partial: hits still come from what was refreshed
    plus the cache, but a miss raises ``SourceUnavailable``.
    """

    def __init__(self, entries: list[str], http, cache_path: Path) -> None:
        self.http = http
        self.cache_path = Path(cache_path)
        self._entries: list[str] = []
        seen: set[str] = set()
        for entry in entries or []:
            entry = entry.strip() if isinstance(entry, str) else ""
            if entry.lower() not in seen and (_is_name(entry) or _is_repo_id(entry)):
                seen.add(entry.lower())
                self._entries.append(entry)
        self._hashes: dict[str, tuple[str, str]] | None = None
        self._repos: dict[str, dict] = {}
        self._error: str | None = None
        self._samples: dict | None = None
        self._used = 0  # requests this index sent; the client may be shared with other sources
        # Build state, reset by each _build.
        self._cached: dict[str, tuple[str, dict]] = {}  # lowercase repo id -> (repo id, cached entry)
        self._fresh: dict[str, dict] = {}
        self._settled: set[str] = set()  # lowercase repo ids refreshed or dropped this run
        self._listed: dict[str, set[str]] = {}  # lowercase author -> its completed listing

    def find(self, sha256: str) -> FallbackResult | None:
        if self._hashes is None:
            self._build()
        hit = self._hashes.get(str(sha256 or "").strip().lower())
        if hit is None:
            if self._error is not None:
                raise SourceUnavailable(self._error)
            return None
        return self._result(*hit)

    # Building

    def _build(self) -> None:
        raw = read_json(self.cache_path)
        self._cached = {repo_id.lower(): (repo_id, data) for repo_id, data in _cached_repos(raw).items()}
        self._fresh, self._settled, self._listed = {}, set(), {}
        try:
            for entry in self._entries:
                if "/" in entry:
                    self._refresh_repo(entry)
                else:
                    self._refresh_author(entry)
        except SourceUnavailable as exc:
            self._error = str(exc) or "Hugging Face unavailable"
            self._keep_unrefreshed()
        self._repos = self._fresh
        payload = {"version": 1, "repos": self._repos}
        if payload != raw:
            try:
                write_json(self.cache_path, payload, compact=True)
            except OSError:
                pass
        self._hashes = {}
        for repo_id, data in self._repos.items():
            for sha, path in data["files"].items():
                self._hashes.setdefault(sha, (repo_id, path))

    def _keep_unrefreshed(self) -> None:
        """Partial run: keep cached repos of the configured entries that this run did not settle."""
        authors = {entry.lower() for entry in self._entries if "/" not in entry}
        repos = {entry.lower() for entry in self._entries if "/" in entry}
        for key, (repo_id, data) in self._cached.items():
            if key in self._settled:
                continue
            owner = _owner(key)
            listing = self._listed.get(owner)
            if key in repos or (owner in authors and (listing is None or key in listing)):
                self._fresh[repo_id] = data

    def _remaining(self) -> int:
        return MAX_REQUESTS_PER_RUN - self._used

    def _get_json(self, url: str):
        if self._remaining() <= 0:
            raise SourceUnavailable(_LIMIT_REACHED)
        before = self.http.request_count
        try:
            return self.http.get_json(url)
        finally:
            self._used += self.http.request_count - before

    def _get_pages(self, url: str, cap: int) -> tuple[list, bool]:
        """Up to ``cap`` pages; the flag says the budget may have cut the listing short.

        Only a failed first page keeps its status: a later page failing says nothing
        about whether the repo or author exists.
        """
        allowed = min(cap, self._remaining())
        if allowed <= 0:
            raise SourceUnavailable(_LIMIT_REACHED)
        before = self.http.request_count
        try:
            items = self.http.get_json_pages(url, max_pages=allowed)
        except SourceUnavailable as exc:
            if self.http.request_count - before > 1:
                raise SourceUnavailable(str(exc)) from exc
            raise
        finally:
            self._used += self.http.request_count - before
        return items, allowed < cap and self.http.request_count - before >= allowed

    def _refresh_author(self, author: str) -> None:
        url = f"{_API}?author={author}&expand[]=sha&limit={_LISTING_LIMIT}"
        try:
            items, cut = self._get_pages(url, -(-MAX_REPOS_PER_AUTHOR // _LISTING_LIMIT))
        except SourceUnavailable as exc:
            if exc.status not in _GONE:
                raise
            items, cut = [], False
        if cut:
            raise SourceUnavailable(_LIMIT_REACHED)
        listed: list[tuple[str, str]] = []
        keys: set[str] = set()
        for item in items:
            repo_id = item.get("id") if isinstance(item, dict) else None
            if not _is_repo_id(repo_id) or repo_id.lower() in keys:
                continue
            keys.add(repo_id.lower())
            listed.append((repo_id, _sha_text(item.get("sha"))))
            if len(listed) >= MAX_REPOS_PER_AUTHOR:
                break
        self._listed[author.lower()] = keys
        for repo_id, sha in listed:
            if repo_id.lower() in self._settled or self._reuse(repo_id, sha):
                continue
            mapped = _malcolmrey_repo(repo_id)
            if mapped:
                base = mapped[1]
            else:
                info = self._repo_info(repo_id)
                if info is None:
                    continue
                base = _card_base(info)
            self._refresh_tree(repo_id, sha, base)

    def _refresh_repo(self, entry: str) -> None:
        key = entry.lower()
        if key in self._settled:
            return
        info = self._repo_info(entry)
        if info is None:
            return
        info_id = info.get("id")
        repo_id = info_id if _is_repo_id(info_id) and info_id.lower() == key else entry
        sha = _sha_text(info.get("sha"))
        if self._reuse(repo_id, sha):
            return
        mapped = _malcolmrey_repo(repo_id)
        self._refresh_tree(repo_id, sha, mapped[1] if mapped else _card_base(info))

    def _repo_info(self, repo_id: str) -> dict | None:
        """The repo's info, or None (and the repo dropped) when it is gone."""
        try:
            info = self._get_json(f"{_API}/{repo_id}?expand[]=sha&expand[]=cardData")
        except SourceUnavailable as exc:
            if exc.status not in _GONE:
                raise
            self._settled.add(repo_id.lower())
            return None
        return info if isinstance(info, dict) else {}

    def _reuse(self, repo_id: str, sha: str) -> bool:
        cached = self._cached.get(repo_id.lower())
        if not sha or cached is None or cached[1]["sha"] != sha:
            return False
        self._fresh[repo_id] = cached[1]
        self._settled.add(repo_id.lower())
        return True

    def _refresh_tree(self, repo_id: str, sha: str, base: str) -> None:
        try:
            items, cut = self._get_pages(f"{_API}/{repo_id}/tree/main?recursive=true", MAX_TREE_PAGES)
        except SourceUnavailable as exc:
            if exc.status not in _GONE:
                raise
            items, cut = [], False
        # A tree the budget cut short keeps what it listed, with no sha so the next run lists it again.
        self._fresh[repo_id] = _tree_entry("" if cut else sha, base, items)
        self._settled.add(repo_id.lower())
        if cut:
            raise SourceUnavailable(_LIMIT_REACHED)

    # Answering

    def _result(self, repo_id: str, path: str) -> FallbackResult:
        data = self._repos[repo_id]
        owner, name = repo_id.split("/", 1)
        url = f"{_SITE}/{repo_id}"
        mapped = _malcolmrey_repo(repo_id)
        parsed = parse_malcolmrey_name(path) if mapped else None
        if parsed:
            slug, version = parsed
            return FallbackResult(
                source="huggingface", url=url, title=" ".join(slug.replace("_", " ").split()), creator=owner,
                version_name=version, base_model=mapped[1], image_url=self._sample_url(slug, mapped[0]),
            )
        image = pick_repo_image(path, data["images"])
        return FallbackResult(
            source="huggingface", url=url, title=_spaced(name), creator=owner,
            version_name=PurePosixPath(path).stem, base_model=data["baseModel"],
            image_url=f"{_SITE}/{repo_id}/resolve/main/{quote(image)}" if image else "",
        )

    def _sample_url(self, slug: str, framework: str) -> str:
        """The first listed sample for this slug; fetched at most once per run, only for a hit,
        and never after a partial build (that run sends nothing more)."""
        if self._samples is None:
            body = None
            if self._error is None:
                try:
                    body = self._get_json(_SAMPLES_URL)
                except SourceUnavailable:
                    pass
            self._samples = body if isinstance(body, dict) else {}
        by_framework = self._samples.get(slug)
        samples = by_framework.get(framework) if isinstance(by_framework, dict) else None
        for sample in samples if isinstance(samples, list) else []:
            url = sample.get("url") if isinstance(sample, dict) else None
            if isinstance(url, str) and is_allowed_url(url):
                return url
        return ""
