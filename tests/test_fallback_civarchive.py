from __future__ import annotations

import json
import unittest

from comfy_updater.fallback_civarchive import lookup_civarchive, parse_hash_page
from comfy_updater.fallback_types import SourceUnavailable

SHA = "ab" * 32


def page(models):
    data = {"props": {"pageProps": {"models": models}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data).replace("<", "\\u003c") + "</script></html>"


def civitai_model(**over):
    m = {
        "id": "77", "name": "Cool Style", "username": "someone", "is_nsfw": True, "platform": "civitai",
        "version": {
            "name": "v2", "base_model": "Krea 2", "trigger": ["coolstyle"], "description": "<p>d</p>",
            "civitai_model_id": 77, "civitai_model_version_id": 88,
            "files": [{"sha256": SHA.upper()}],
            "images": [
                {"url": "https://c.genur.art/kid_small.webp", "type": "image", "minor": True, "nsfwLevel": 1},
                {"url": "https://c.genur.art/vid.mp4", "type": "video"},
                {"image_url": "https://c.genur.art/spicy_small.webp", "type": "image", "minor": False, "nsfwLevel": 8},
                {"url": "https://c.genur.art/ok_small.webp", "type": "image", "minor": False, "nsfwLevel": 2},
                {"url": "https://c.genur.art/ok2_small.webp", "type": "image", "minor": False, "nsfwLevel": 2},
            ],
        },
    }
    m.update(over)
    return m


class FakeHttp:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error
        self.urls = []

    def get_text(self, url, max_bytes=5_000_000):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.text


class CivarchiveTests(unittest.TestCase):
    def test_civitai_platform_hit(self):
        r = parse_hash_page(page([civitai_model()]), SHA)
        self.assertEqual(
            (r.source, r.title, r.creator, r.version_name, r.base_model),
            ("civarchive", "Cool Style", "someone", "v2", "Krea 2"),
        )
        self.assertEqual((r.civitai_model_id, r.civitai_version_id), ("77", "88"))
        self.assertEqual(
            (r.image_url, r.image_nsfw_level, r.nsfw), ("https://c.genur.art/ok_small.webp", 2, True)
        )
        self.assertEqual(r.trained_words, ["coolstyle"])
        self.assertEqual(r.description, "d")
        self.assertEqual(r.url, "https://civarchive.com/sha256/" + SHA)

    def test_image_url_key_is_read_when_url_missing(self):
        m = civitai_model()
        m["version"]["images"] = [{"image_url": "https://c.genur.art/a.webp", "type": "image", "nsfwLevel": 1}]
        self.assertEqual(parse_hash_page(page([m]), SHA).image_url, "https://c.genur.art/a.webp")

    def test_description_is_plain_text(self):
        m = civitai_model()
        m["version"]["description"] = "<p>Hi <b>there</b></p><script>x()</script><style>p{}</style>&amp; more"
        self.assertEqual(parse_hash_page(page([m]), SHA).description, "Hi there & more")

    def test_description_falls_back_to_model(self):
        m = civitai_model(description="<i>model</i> text")
        m["version"]["description"] = ""
        self.assertEqual(parse_hash_page(page([m]), SHA).description, "model text")

    def test_prefers_civitai_platform(self):
        other = civitai_model(platform="tensorart", name="Other")
        r = parse_hash_page(page([other, civitai_model()]), SHA)
        self.assertEqual(r.title, "Cool Style")

    def test_other_platform_has_no_civitai_ids(self):
        r = parse_hash_page(page([civitai_model(platform="tensorart")]), SHA)
        self.assertEqual((r.civitai_model_id, r.civitai_version_id), ("", ""))
        self.assertEqual(r.title, "Cool Style")

    def test_hash_must_match_a_version_file(self):
        m = civitai_model()
        m["version"]["files"] = [{"sha256": "cd" * 32}]
        self.assertIsNone(parse_hash_page(page([m]), SHA))

    def test_unknown_hash(self):
        self.assertIsNone(parse_hash_page(page([]), SHA))

    def test_creator_username_fallback(self):
        m = civitai_model()
        del m["username"]
        m["creator_username"] = "x"
        self.assertEqual(parse_hash_page(page([m]), SHA).creator, "x")

    def test_trigger_as_string(self):
        m = civitai_model()
        m["version"]["trigger"] = "a, b, ,"
        self.assertEqual(parse_hash_page(page([m]), SHA).trained_words, ["a", "b"])

    def test_only_minor_images_gives_no_image(self):
        m = civitai_model()
        m["version"]["images"] = [{"url": "https://c.genur.art/k.webp", "type": "image", "minor": True}]
        r = parse_hash_page(page([m]), SHA)
        self.assertEqual(r.image_url, "")

    def test_odd_fields_are_tolerated(self):
        m = civitai_model(name=5)
        m["version"]["images"] = "nope"
        m["version"]["trigger"] = 3
        r = parse_hash_page(page([m]), SHA)
        self.assertEqual((r.title, r.image_url, r.trained_words), ("", "", []))
        m["version"]["images"] = [{"url": "https://c.genur.art/a.webp", "type": "image", "nsfwLevel": "x"}]
        self.assertEqual(parse_hash_page(page([m]), SHA).image_nsfw_level, 0)

    def test_page_without_next_data_raises(self):
        with self.assertRaises(SourceUnavailable):
            parse_hash_page("<html>nothing</html>", SHA)

    def test_invalid_json_and_bad_shapes_raise(self):
        bad = [
            '<script id="__NEXT_DATA__" type="application/json">{not json</script>',
            '<script id="__NEXT_DATA__" type="application/json">[]</script>',
            '<script id="__NEXT_DATA__" type="application/json">{"props": []}</script>',
            '<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"models": {}}}}</script>',
            page(["x"]),
            page([civitai_model(version=[])]),
        ]
        for html in bad:
            with self.assertRaises(SourceUnavailable, msg=html):
                parse_hash_page(html, SHA)

    def test_lookup_sends_only_the_hash(self):
        http = FakeHttp(text=page([civitai_model()]))
        lookup_civarchive(SHA, http)
        self.assertEqual(http.urls, ["https://civarchive.com/sha256/" + SHA])

    def test_lookup_lowercases_hash(self):
        http = FakeHttp(text=page([]))
        self.assertIsNone(lookup_civarchive(SHA.upper(), http))
        self.assertEqual(http.urls, ["https://civarchive.com/sha256/" + SHA])

    def test_lookup_propagates_unavailable(self):
        with self.assertRaises(SourceUnavailable):
            lookup_civarchive(SHA, FakeHttp(error=SourceUnavailable("down")))


if __name__ == "__main__":
    unittest.main()
