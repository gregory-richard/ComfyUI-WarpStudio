from __future__ import annotations

import unittest

from comfy_updater.civitai_client import CivitaiClient, CivitaiRequestError, _is_civitai_https_url
from comfy_updater.constants import (
    MODEL_BY_ID_URL,
    MODEL_VERSION_BY_ID_URL,
    PLUGIN_VERSION,
    USER_AGENT,
    VERSION_BY_HASH_URL,
)


class CivitaiClientUrlTests(unittest.TestCase):
    def test_api_urls_use_civitai_red(self) -> None:
        # civitai.com API responses are SFW-filtered since the April 2026
        # domain split; only civitai.red serves the full catalog.
        self.assertEqual("https://civitai.red/api/v1/model-versions/by-hash", VERSION_BY_HASH_URL)
        self.assertEqual("https://civitai.red/api/v1/models", MODEL_BY_ID_URL)
        self.assertEqual("https://civitai.red/api/v1/model-versions", MODEL_VERSION_BY_ID_URL)

    def test_page_urls_always_use_civitai_red(self) -> None:
        client = CivitaiClient(api_key="", timeout_seconds=30, max_retries=0)
        self.assertEqual("https://civitai.red/models/6424", client.model_page_url(6424))
        self.assertEqual(
            "https://civitai.red/models/6424?modelVersionId=11745",
            client.version_page_url(6424, 11745),
        )
        self.assertEqual("https://civitai.red/models/6424", client.version_page_url(6424, None))

    def test_user_agent_carries_the_release_version(self) -> None:
        # The version is read from pyproject.toml so it cannot drift from the
        # published release the way a hard-coded string did.
        self.assertNotEqual("unknown", PLUGIN_VERSION)
        self.assertEqual(f"comfyui-civitai-updater/{PLUGIN_VERSION}", USER_AGENT)
        client = CivitaiClient(api_key="", timeout_seconds=30, max_retries=0)
        self.assertEqual(USER_AGENT, client.default_headers["User-Agent"])

    def test_user_info_or_backslash_is_not_a_civitai_url(self) -> None:
        for url in (
            "https://evil.example\\@civitai.red/x",
            "https://user@civitai.red/x",
            "https://civitai.red@evil.example/x",
        ):
            self.assertFalse(_is_civitai_https_url(url), url)
        self.assertTrue(_is_civitai_https_url("https://civitai.red/api/x"))


class _Ok:
    status_code = 200
    ok = True
    reason = "OK"

    def json(self):
        return {"id": 1}


class CivitaiClientPathValueTests(unittest.TestCase):
    """Hashes and ids come from sidecars: only real ones go into a request path."""

    def _client(self) -> tuple[CivitaiClient, list[str]]:
        client = CivitaiClient(api_key="secret", timeout_seconds=5, max_retries=0)
        urls: list[str] = []
        client.session.get = lambda url, **kwargs: urls.append(url) or _Ok()  # type: ignore[assignment]
        return client, urls

    def test_bad_ids_and_hashes_send_no_request(self) -> None:
        client, urls = self._client()
        for bad in ("1/../../user/x", "12?x=1", "", " 12", "-3", -3, True, None, 1.5, "\u0661\u0662"):
            self.assertIsNone(client.get_model(bad), bad)
            self.assertIsNone(client.get_version(bad), bad)
            with self.assertRaises(CivitaiRequestError) as raised:
                client.get_version(bad, raise_on_error=True)
            self.assertEqual(404, raised.exception.status)
            with self.assertRaises(CivitaiRequestError):
                client.get_model(bad, raise_on_error=True)
        for bad in ("ab" * 31, "ab" * 33, "g" * 64, "a" * 63 + "/", "a" * 60 + "?x=1", "", None):
            self.assertIsNone(client.get_version_by_hash(bad), bad)
            with self.assertRaises(CivitaiRequestError) as raised:
                client.get_version_by_hash(bad, raise_on_error=True)
            self.assertEqual(404, raised.exception.status)
        self.assertEqual([], urls)

    def test_real_ids_and_hashes_are_requested(self) -> None:
        client, urls = self._client()
        client.get_model(6424)
        client.get_version("11745")
        client.get_version_by_hash("AB" * 32)
        self.assertEqual(
            [f"{MODEL_BY_ID_URL}/6424", f"{MODEL_VERSION_BY_ID_URL}/11745", f"{VERSION_BY_HASH_URL}/{'AB' * 32}"],
            urls,
        )


if __name__ == "__main__":
    unittest.main()
