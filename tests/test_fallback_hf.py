from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_updater import fallback_hf
from comfy_updater.fallback_hf import (
    HF_BASE_NAMES,
    MALCOLMREY_REPOS,
    HuggingFaceIndex,
    parse_malcolmrey_name,
    pick_repo_image,
)
from comfy_updater.fallback_types import SourceUnavailable

API = "https://huggingface.co/api/models"
SAMPLES = "https://huggingface.co/spaces/malcolmrey/browser/resolve/main/data-hf-images.json"
SAMPLE_URL = "https://huggingface.co/datasets/malcolmrey/samples/resolve/main/krea2/krea2_someone_00001_.png"
REPO = "example/Cool-Lora_x"
SHA = "ab" * 32
SHA2 = "cd" * 32
SHA3 = "ef" * 32


def h(i: int) -> str:
    return f"{i:064x}"


def listing_url(author: str) -> str:
    return f"{API}?author={author}&expand[]=sha&limit=100"


def info_url(repo: str) -> str:
    return f"{API}/{repo}?expand[]=sha&expand[]=cardData"


def tree_url(repo: str) -> str:
    return f"{API}/{repo}/tree/main?recursive=true"


def lfs(path: str, oid: str) -> dict:
    return {"type": "file", "path": path, "lfs": {"oid": oid}}


def plain(path: str) -> dict:
    return {"type": "file", "path": path}


def error(status: int) -> SourceUnavailable:
    return SourceUnavailable(f"HTTP {status} from huggingface.co", status=status)


def malcolmrey_bodies(sha: str = "s1", oid: str = SHA, samples: dict | None = None) -> dict:
    if samples is None:
        samples = {"someone": {"Krea2": [{"url": SAMPLE_URL, "filename": "krea2_someone_00001_.png", "framework": "Krea2"}]}}
    return {
        listing_url("malcolmrey"): [{"id": "malcolmrey/krea2", "sha": sha}],
        tree_url("malcolmrey/krea2"): [lfs("krea2/krea2_someone_v1_onetrainer.safetensors", oid)],
        SAMPLES: samples,
    }


def other_bodies(sha: str = "s9", base="OpenMOSS-Team/MOSS-Image-Base", oid: str = SHA) -> dict:
    return {
        info_url(REPO): {"id": REPO, "sha": sha, "cardData": {"base_model": base}},
        tree_url(REPO): [
            {"type": "directory", "path": "w"},
            lfs("w/cool_v3.safetensors", oid),
            plain("w/cool_v3.png"),
            plain("assets/a.jpg"),
        ],
    }


class FakeHttp:
    """URL -> body; ``links`` maps a URL to its next page. Unknown URLs answer 404."""

    def __init__(self, bodies=None, links=None, errors=None):
        self.bodies = dict(bodies or {})
        self.links = dict(links or {})
        self.errors = dict(errors or {})
        self.urls: list[str] = []
        self.request_count = 0

    def _fetch(self, url):
        self.urls.append(url)
        self.request_count += 1
        if url in self.errors:
            raise self.errors[url]
        if url not in self.bodies:
            raise error(404)
        return copy.deepcopy(self.bodies[url])

    def get_json(self, url):
        return self._fetch(url)

    def get_json_pages(self, url, max_pages=100):
        items: list = []
        next_url = url
        for _ in range(max_pages):
            if not next_url:
                break
            body = self._fetch(next_url)
            items.extend(body if isinstance(body, list) else [body])
            next_url = self.links.get(next_url)
        return items

    def count(self, part: str) -> int:
        return sum(1 for url in self.urls if part in url)


class IndexTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "hf_index.json"

    def index(self, entries, http) -> HuggingFaceIndex:
        return HuggingFaceIndex(entries, http, self.cache)

    def cached_repos(self) -> dict:
        return json.loads(self.cache.read_text(encoding="utf-8"))["repos"]


class BriefTests(IndexTestCase):
    def test_author_repo_match_malcolmrey(self):
        http = FakeHttp(malcolmrey_bodies())
        r = self.index(["malcolmrey"], http).find(SHA)
        self.assertEqual((r.source, r.creator, r.title, r.version_name, r.base_model),
                         ("huggingface", "malcolmrey", "someone", "v1", "Krea 2"))
        self.assertTrue(r.image_url.endswith("krea2_someone_00001_.png"))
        self.assertEqual(r.url, "https://huggingface.co/malcolmrey/krea2")
        # A mapped malcolmrey repo takes its base from the map: no repo-info request.
        self.assertEqual(http.count("expand[]=cardData"), 0)

    def test_other_repo_match(self):
        http = FakeHttp(other_bodies())
        r = self.index([REPO], http).find(SHA)
        self.assertEqual((r.source, r.url), ("huggingface", "https://huggingface.co/example/Cool-Lora_x"))
        self.assertEqual((r.creator, r.title, r.version_name, r.base_model),
                         ("example", "Cool Lora x", "cool_v3", "MOSS Image"))
        self.assertEqual(r.image_url, "https://huggingface.co/example/Cool-Lora_x/resolve/main/w/cool_v3.png")

    def test_tree_pages_are_followed(self):
        bodies = other_bodies(oid=SHA2)
        page2 = tree_url(REPO) + "&cursor=p2"
        bodies[page2] = [lfs("w/second.safetensors", SHA)]
        http = FakeHttp(bodies, links={tree_url(REPO): page2})
        r = self.index([REPO], http).find(SHA)
        self.assertEqual(r.version_name, "second")
        self.assertIn(page2, http.urls)

    def test_unchanged_sha_uses_cache(self):
        self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA)
        http = FakeHttp(malcolmrey_bodies())
        r = self.index(["malcolmrey"], http).find(SHA)
        self.assertEqual(r.title, "someone")
        self.assertEqual(http.count("/tree/"), 0)

    def test_changed_sha_relists_tree(self):
        self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA)
        http = FakeHttp(malcolmrey_bodies(sha="s2", oid=SHA2))
        index = self.index(["malcolmrey"], http)
        self.assertEqual(index.find(SHA2).version_name, "v1")
        self.assertIsNone(index.find(SHA))
        self.assertEqual(http.count("/tree/"), 1)

    def test_unknown_hash_returns_none(self):
        self.assertIsNone(self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA3))

    def test_listing_failure_raises_unavailable_once(self):
        http = FakeHttp(malcolmrey_bodies(), errors={listing_url("malcolmrey"): error(500)})
        index = self.index(["malcolmrey"], http)
        with self.assertRaises(SourceUnavailable):
            index.find(SHA)
        self.assertEqual(http.request_count, 1)
        with self.assertRaises(SourceUnavailable):
            index.find(SHA)
        self.assertEqual(http.request_count, 1)

    def test_no_user_data_in_requests(self):
        bodies = {**malcolmrey_bodies(), **other_bodies(oid=SHA2)}
        http = FakeHttp(bodies)
        index = self.index(["malcolmrey", REPO], http)
        index.find(SHA)
        index.find(SHA2)
        index.find(SHA3)
        self.assertTrue(http.urls)
        for url in http.urls:
            for secret in (SHA, SHA2, SHA3, SHA.upper(), "x.safetensors"):
                self.assertNotIn(secret, url)

    def test_pick_repo_image_order(self):
        imgs = ["z/other.webp", "assets/b.png", "w/x.jpg", "w/cool_v3.png"]
        self.assertEqual(pick_repo_image("w/cool_v3.safetensors", imgs), "w/cool_v3.png")
        self.assertEqual(pick_repo_image("w/none.safetensors", imgs[:3]), "w/x.jpg")
        self.assertEqual(pick_repo_image("q/none.safetensors", ["z/other.webp", "assets/b.png"]), "assets/b.png")
        self.assertEqual(pick_repo_image("q/none.safetensors", ["z/other.webp"]), "z/other.webp")
        self.assertIsNone(pick_repo_image("q/n.safetensors", []))

    def test_pick_repo_image_prefers_same_stem_in_same_folder(self):
        self.assertEqual(pick_repo_image("a/m.safetensors", ["b/m.png", "a/m.png"]), "a/m.png")
        self.assertEqual(pick_repo_image("m.safetensors", ["x/y.png", "cover.jpg"]), "cover.jpg")
        self.assertEqual(pick_repo_image("q/n.safetensors", ["z/samples/s.png", "z/o.png"]), "z/samples/s.png")

    def test_parse_malcolmrey_name(self):
        self.assertEqual(parse_malcolmrey_name("krea2_some_one_v1_onetrainer.safetensors"), ("some_one", "v1"))
        self.assertEqual(parse_malcolmrey_name("zimage_someone_v12.safetensors"), ("someone", "v12"))
        self.assertIsNone(parse_malcolmrey_name("random.safetensors"))
        self.assertEqual(parse_malcolmrey_name("krea2/krea2_someone_v1_onetrainer.safetensors"), ("someone", "v1"))
        self.assertIsNone(parse_malcolmrey_name("krea2_v1.safetensors"))

    def test_malcolmrey_without_sample_still_matches(self):
        http = FakeHttp(malcolmrey_bodies(samples={"other": {"Krea2": [{"url": SAMPLE_URL}]}}))
        r = self.index(["malcolmrey"], http).find(SHA)
        self.assertEqual(r.image_url, "")
        self.assertEqual((r.creator, r.title, r.version_name, r.base_model), ("malcolmrey", "someone", "v1", "Krea 2"))

    def test_author_repo_cap(self):
        bodies, links = {}, {}
        pages = [listing_url("someone"), listing_url("someone") + "&cursor=2", listing_url("someone") + "&cursor=3"]
        for page, (start, stop) in enumerate(((0, 150), (150, 250), (250, 300))):
            bodies[pages[page]] = [{"id": f"someone/r{i}", "sha": "s"} for i in range(start, stop)]
            if page < 2:
                links[pages[page]] = pages[page + 1]
        for i in range(300):
            bodies[info_url(f"someone/r{i}")] = {"id": f"someone/r{i}", "sha": "s"}
            bodies[tree_url(f"someone/r{i}")] = [lfs("m.safetensors", h(i))]
        http = FakeHttp(bodies, links)
        with patch.object(fallback_hf, "MAX_REQUESTS_PER_RUN", 1000):
            index = self.index(["someone"], http)
            self.assertIsNotNone(index.find(h(199)))
            self.assertIsNone(index.find(h(200)))
        trees = [url for url in http.urls if "/tree/" in url]
        self.assertEqual(trees, [tree_url(f"someone/r{i}") for i in range(200)])
        self.assertNotIn(pages[2], http.urls)  # 200 repos need no more than 2 listing pages of 100

    def test_tree_page_cap(self):
        bodies, links = other_bodies(oid=h(1000)), {}
        page_urls = [tree_url(REPO)] + [tree_url(REPO) + f"&cursor={n}" for n in range(1, 40)]
        for n, url in enumerate(page_urls):
            bodies[url] = [lfs(f"w/m{n}.safetensors", h(n))]
            if n + 1 < len(page_urls):
                links[url] = page_urls[n + 1]
        http = FakeHttp(bodies, links)
        index = self.index([REPO], http)
        self.assertEqual(index.find(h(0)).version_name, "m0")
        self.assertEqual(index.find(h(29)).version_name, "m29")
        self.assertIsNone(index.find(h(30)))
        self.assertEqual(http.count("/tree/"), 30)

    def test_request_budget(self):
        bodies = {listing_url("someone"): [{"id": f"someone/r{i}", "sha": "s"} for i in range(4)]}
        for i in range(4):
            bodies[info_url(f"someone/r{i}")] = {"id": f"someone/r{i}", "sha": "s"}
            bodies[tree_url(f"someone/r{i}")] = [lfs("m.safetensors", h(i))]
        http = FakeHttp(bodies)
        with patch.object(fallback_hf, "MAX_REQUESTS_PER_RUN", 5):
            index = self.index(["someone"], http)
            with self.assertRaises(SourceUnavailable) as ctx:
                index.find(h(3))
            self.assertIn("request limit", str(ctx.exception))
            self.assertEqual(http.request_count, 5)
            self.assertEqual(index.find(h(1)).creator, "someone")
            self.assertEqual(http.request_count, 5)

            failing = FakeHttp(bodies, errors={listing_url("someone"): error(500)})
            later = self.index(["someone"], failing)
            self.assertIsNotNone(later.find(h(0)))
            self.assertIsNotNone(later.find(h(1)))
            with self.assertRaises(SourceUnavailable):
                later.find(h(3))

    def test_corrupt_cache_is_rebuilt(self):
        for text in ("{not json", "[]", '{"version": 2, "repos": {}}',
                     '{"version": 1, "repos": {"malcolmrey/krea2": "junk"}}',
                     '{"version": 1, "repos": {"malcolmrey/krea2": {"sha": "s1", "files": [], "images": 3}}}'):
            with self.subTest(text=text):
                self.cache.write_text(text, encoding="utf-8")
                http = FakeHttp(malcolmrey_bodies())
                self.assertEqual(self.index(["malcolmrey"], http).find(SHA).title, "someone")
                self.assertEqual(http.count("/tree/"), 1)
                self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["version"], 1)


class BudgetTests(IndexTestCase):
    def test_paged_call_never_overshoots_and_cut_tree_is_listed_again(self):
        bodies, links = other_bodies(oid=h(0)), {}
        page_urls = [tree_url(REPO)] + [tree_url(REPO) + f"&cursor={n}" for n in range(1, 5)]
        for n, url in enumerate(page_urls):
            bodies[url] = [lfs(f"w/m{n}.safetensors", h(n))]
            if n + 1 < len(page_urls):
                links[url] = page_urls[n + 1]
        http = FakeHttp(bodies, links)
        with patch.object(fallback_hf, "MAX_REQUESTS_PER_RUN", 3):
            index = self.index([REPO], http)
            self.assertEqual(index.find(h(1)).version_name, "m1")
            self.assertEqual(http.request_count, 3)
            with self.assertRaises(SourceUnavailable):
                index.find(h(4))

        http = FakeHttp(bodies, links)
        self.assertEqual(self.index([REPO], http).find(h(4)).version_name, "m4")
        self.assertEqual(http.count("/tree/"), 5)

    def test_budget_is_checked_before_single_requests(self):
        second = "example/second"
        bodies = {
            **other_bodies(),
            info_url(second): {"id": second, "sha": "t1"},
            tree_url(second): [lfs("m.safetensors", SHA2)],
        }
        self.index([REPO, second], FakeHttp(bodies)).find(SHA)
        http = FakeHttp(bodies)
        with patch.object(fallback_hf, "MAX_REQUESTS_PER_RUN", 1):
            index = self.index([REPO, second], http)
            self.assertEqual(index.find(SHA2).version_name, "m")  # kept from the cache
            with self.assertRaises(SourceUnavailable):
                index.find(SHA3)
        self.assertEqual(http.urls, [info_url(REPO)])


class MissingAndPartialTests(IndexTestCase):
    """404 answers contribute nothing; any other failure makes the run's index partial."""

    def test_missing_entries_leave_the_index_complete(self):
        http = FakeHttp(malcolmrey_bodies())
        index = self.index(["", "  ", "nobody", "example/missing", " malcolmrey "], http)
        self.assertEqual(index.find(SHA).title, "someone")
        self.assertIsNone(index.find(SHA3))
        self.assertIn(listing_url("nobody"), http.urls)
        self.assertIn(info_url("example/missing"), http.urls)

    def test_missing_tree_contributes_nothing_and_is_not_asked_again(self):
        bodies = other_bodies()
        del bodies[tree_url(REPO)]
        self.assertIsNone(self.index([REPO], FakeHttp(bodies)).find(SHA))
        http = FakeHttp(bodies)
        self.assertIsNone(self.index([REPO], http).find(SHA))
        self.assertEqual(http.count("/tree/"), 0)

    def test_listing_failure_still_answers_from_the_cache(self):
        self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA)
        http = FakeHttp(malcolmrey_bodies(), errors={listing_url("malcolmrey"): error(503)})
        index = self.index(["malcolmrey"], http)
        with self.assertRaises(SourceUnavailable):
            index.find(SHA3)
        self.assertEqual(http.request_count, 1)
        self.assertEqual(index.find(SHA).title, "someone")
        with self.assertRaises(SourceUnavailable):
            index.find(SHA3)
        self.assertEqual(http.urls, [listing_url("malcolmrey"), SAMPLES])

    def test_tree_failure_makes_the_index_partial_and_saves_what_was_listed(self):
        bodies = malcolmrey_bodies()
        bodies[listing_url("malcolmrey")] = [{"id": "malcolmrey/krea2", "sha": "s1"}, {"id": "malcolmrey/zimage", "sha": "s1"}]
        http = FakeHttp(bodies, errors={tree_url("malcolmrey/zimage"): error(500)})
        index = self.index(["malcolmrey"], http)
        self.assertEqual(index.find(SHA).title, "someone")
        with self.assertRaises(SourceUnavailable):
            index.find(SHA3)
        self.assertEqual(set(self.cached_repos()), {"malcolmrey/krea2"})

    def test_stop_makes_the_index_partial(self):
        http = FakeHttp(malcolmrey_bodies(), errors={tree_url("malcolmrey/krea2"): SourceUnavailable("stopped")})
        with self.assertRaises(SourceUnavailable):
            self.index(["malcolmrey"], http).find(SHA3)

    def test_partial_build_keeps_cached_repos_not_yet_refreshed(self):
        bodies = {
            listing_url("malcolmrey"): [{"id": f"malcolmrey/{name}", "sha": "s1"} for name in ("krea2", "zimage", "flux")],
            tree_url("malcolmrey/krea2"): [lfs("krea2/krea2_someone_v1.safetensors", SHA)],
            tree_url("malcolmrey/zimage"): [lfs("zimage/zimage_someone_v1.safetensors", SHA2)],
            tree_url("malcolmrey/flux"): [lfs("flux/flux_someone_v1.safetensors", SHA3)],
            SAMPLES: {},
        }
        self.index(["malcolmrey"], FakeHttp(bodies)).find(SHA)

        bodies[listing_url("malcolmrey")] = [{"id": "malcolmrey/krea2", "sha": "s2"}, {"id": "malcolmrey/zimage", "sha": "s2"}]
        http = FakeHttp(bodies, errors={tree_url("malcolmrey/krea2"): error(500)})
        index = self.index(["malcolmrey"], http)
        self.assertEqual(index.find(SHA).base_model, "Krea 2")
        self.assertEqual(index.find(SHA2).base_model, "ZImageTurbo")
        with self.assertRaises(SourceUnavailable):
            index.find(SHA3)  # flux left the completed listing: dropped
        repos = self.cached_repos()
        self.assertEqual(set(repos), {"malcolmrey/krea2", "malcolmrey/zimage"})
        self.assertEqual(repos["malcolmrey/krea2"]["sha"], "s1")


class RepoInfoTests(IndexTestCase):
    def test_author_listed_repo_reads_its_card_base_only_when_sha_changed(self):
        bodies = {
            listing_url("example"): [{"id": REPO, "sha": "s9"}],
            **other_bodies(base=["OpenMOSS-Team/MOSS-Image-Base", "other/base"]),
        }
        http = FakeHttp(bodies)
        self.assertEqual(self.index(["example"], http).find(SHA).base_model, "MOSS Image")
        self.assertEqual(http.count("expand[]=cardData"), 1)

        http = FakeHttp(bodies)
        self.assertEqual(self.index(["example"], http).find(SHA).base_model, "MOSS Image")
        self.assertEqual((http.count("expand[]=cardData"), http.count("/tree/")), (0, 0))

    def test_explicit_repo_reads_its_info_every_run(self):
        self.index([REPO], FakeHttp(other_bodies())).find(SHA)
        http = FakeHttp(other_bodies())
        self.assertEqual(self.index([REPO], http).find(SHA).title, "Cool Lora x")
        self.assertEqual((http.count("expand[]=cardData"), http.count("/tree/")), (1, 0))

    def test_unknown_or_missing_card_base_is_empty(self):
        for card in ({"base_model": "some/unknown-base"}, {}, None, {"base_model": [3]}):
            with self.subTest(card=card):
                self.cache.unlink(missing_ok=True)
                bodies = other_bodies()
                bodies[info_url(REPO)]["cardData"] = card
                self.assertEqual(self.index([REPO], FakeHttp(bodies)).find(SHA).base_model, "")


class MalcolmreyRuleTests(IndexTestCase):
    def test_unmapped_malcolmrey_repo_uses_generic_rules(self):
        bodies = {
            listing_url("malcolmrey"): [{"id": "malcolmrey/other-things", "sha": "s1"}],
            info_url("malcolmrey/other-things"): {"id": "malcolmrey/other-things", "sha": "s1"},
            tree_url("malcolmrey/other-things"): [lfs("krea2_someone_v1.safetensors", SHA), plain("cover.png")],
        }
        http = FakeHttp(bodies)
        r = self.index(["malcolmrey"], http).find(SHA)
        self.assertEqual((r.creator, r.title, r.version_name, r.base_model),
                         ("malcolmrey", "other things", "krea2_someone_v1", ""))
        self.assertEqual(r.image_url, "https://huggingface.co/malcolmrey/other-things/resolve/main/cover.png")
        self.assertNotIn(SAMPLES, http.urls)

    def test_unparsable_file_in_mapped_repo_uses_generic_rules(self):
        bodies = malcolmrey_bodies()
        bodies[tree_url("malcolmrey/krea2")] = [lfs("krea2/odd name.safetensors", SHA), plain("krea2/odd name.png")]
        http = FakeHttp(bodies)
        r = self.index(["malcolmrey"], http).find(SHA)
        self.assertEqual((r.title, r.version_name, r.base_model), ("krea2", "odd name", "Krea 2"))
        self.assertEqual(r.image_url, "https://huggingface.co/malcolmrey/krea2/resolve/main/krea2/odd%20name.png")
        self.assertNotIn(SAMPLES, http.urls)

    def test_slug_underscores_become_spaces(self):
        bodies = malcolmrey_bodies()
        bodies[tree_url("malcolmrey/krea2")] = [
            lfs("krea2/krea2_some_one_v2_onetrainer.safetensors", SHA),
            lfs("krea2/krea2_some-one_x_v3.safetensors", SHA2),
        ]
        index = self.index(["malcolmrey"], FakeHttp(bodies))
        r = index.find(SHA)
        self.assertEqual((r.title, r.version_name), ("some one", "v2"))
        self.assertEqual(index.find(SHA2).title, "some-one x")

    def test_samples_index_fetched_once_and_only_for_malcolmrey_hits(self):
        bodies = {**malcolmrey_bodies(), **other_bodies(oid=SHA3)}
        bodies[tree_url("malcolmrey/krea2")] = [
            lfs("krea2/krea2_someone_v1_onetrainer.safetensors", SHA),
            lfs("krea2/krea2_other_v2_onetrainer.safetensors", SHA2),
        ]
        http = FakeHttp(bodies)
        index = self.index(["malcolmrey", REPO], http)
        self.assertEqual(index.find(SHA3).title, "Cool Lora x")
        self.assertEqual(http.count(SAMPLES), 0)
        self.assertTrue(index.find(SHA).image_url)
        self.assertEqual(index.find(SHA2).image_url, "")
        self.assertEqual(http.count(SAMPLES), 1)

    def test_samples_index_failure_still_matches_without_picture(self):
        http = FakeHttp(malcolmrey_bodies(), errors={SAMPLES: error(500)})
        index = self.index(["malcolmrey"], http)
        r = index.find(SHA)
        self.assertEqual((r.title, r.image_url), ("someone", ""))
        index.find(SHA)
        self.assertEqual(http.count(SAMPLES), 1)

    def test_sample_url_off_the_allow_list_is_ignored(self):
        http = FakeHttp(malcolmrey_bodies(samples={"someone": {"Krea2": [{"url": "https://example.com/a.png"}]}}))
        self.assertEqual(self.index(["malcolmrey"], http).find(SHA).image_url, "")

    def test_maps_hold_the_built_in_values(self):
        self.assertEqual(MALCOLMREY_REPOS["krea2"], ("Krea2", "Krea 2"))
        self.assertEqual(MALCOLMREY_REPOS["zimage"], ("ZImage", "ZImageTurbo"))
        self.assertEqual(MALCOLMREY_REPOS["qwen"], ("Qwen", "Qwen"))
        self.assertEqual(HF_BASE_NAMES["openmoss-team/moss-image-base"], "MOSS Image")


class CacheAndInputTests(IndexTestCase):
    def test_cache_shape_and_written_only_when_changed(self):
        real_write = fallback_hf.write_json
        with patch.object(fallback_hf, "write_json", side_effect=real_write) as write:
            self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.kwargs, {"compact": True})
            self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies())).find(SHA)
            self.assertEqual(write.call_count, 1)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), {
            "version": 1,
            "repos": {"malcolmrey/krea2": {
                "sha": "s1", "baseModel": "Krea 2",
                "files": {SHA: "krea2/krea2_someone_v1_onetrainer.safetensors"}, "images": [],
            }},
        })

    def test_removed_entry_is_not_matched(self):
        bodies = {**malcolmrey_bodies(), **other_bodies(oid=SHA2)}
        self.index(["malcolmrey", REPO], FakeHttp(bodies)).find(SHA)
        index = self.index([REPO], FakeHttp(bodies))
        self.assertIsNone(index.find(SHA))
        self.assertIsNotNone(index.find(SHA2))

    def test_hash_match_is_case_insensitive(self):
        index = self.index(["malcolmrey"], FakeHttp(malcolmrey_bodies(oid=SHA.upper())))
        self.assertIsNotNone(index.find(SHA))
        self.assertIsNotNone(index.find(" " + SHA.upper()))

    def test_malformed_bodies_are_skipped(self):
        bodies = malcolmrey_bodies()
        bodies[listing_url("someone")] = {"error": "x"}
        bodies[listing_url("malcolmrey")] = [
            {"sha": "s1"}, "junk", {"id": 5}, {"id": "malcolmrey/../x", "sha": "s1"},
            {"id": "malcolmrey/krea2", "sha": "s1"},
        ]
        bodies[tree_url("malcolmrey/krea2")] = [
            "junk", {"path": "a.safetensors"}, {"path": "b.safetensors", "lfs": {}},
            {"path": "c.safetensors", "lfs": {"oid": 7}}, {"lfs": {"oid": SHA2}},
            {"path": "d.safetensors", "lfs": {"oid": "not-a-hash"}},
            lfs("krea2/krea2_someone_v1_onetrainer.safetensors", SHA),
        ]
        bodies[info_url(REPO)] = ["not", "a", "dict"]
        http = FakeHttp(bodies)
        index = self.index(["someone", "malcolmrey", REPO], http)
        self.assertEqual(index.find(SHA).title, "someone")
        self.assertIsNone(index.find(SHA2))
        self.assertFalse(any("/../" in url for url in http.urls))


if __name__ == "__main__":
    unittest.main()
