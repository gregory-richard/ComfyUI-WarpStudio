from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comfy_updater.civitai_client import CivitaiClient, CivitaiRequestError
from comfy_updater.sidecar import info_sidecar_path, write_json
from comfy_updater.updater_service import UpdaterService


class _Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.reason = "OK" if self.ok else "Error"
        self._payload = payload

    def json(self):
        return self._payload


class CreatorLookupTests(unittest.TestCase):
    def _client(self, response=None, error=None):
        client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=0)
        self.urls: list[str] = []

        def get(url, **kwargs):
            self.urls.append(url)
            if error:
                raise error
            return response

        client.session.get = get  # type: ignore[assignment]
        return client

    def test_url_has_the_encoded_username_limit_and_nsfw(self) -> None:
        client = self._client(_Response(200, {"items": [{"id": 1}]}))
        self.assertEqual("active", client.creator_lookup("a b&c_"))
        self.assertEqual(["https://civitai.red/api/v1/models?username=a%20b%26c_&limit=1&nsfw=true"], self.urls)

    def test_no_items_means_false(self) -> None:
        self.assertEqual("gone", self._client(_Response(200, {"items": []})).creator_lookup("gone"))

    def test_a_username_404_means_missing(self) -> None:
        self.assertEqual("missing", self._client(_Response(404)).creator_lookup("x"))

    def test_other_errors_mean_none(self) -> None:
        import requests

        self.assertIsNone(self._client(_Response(500)).creator_lookup("x"))
        self.assertIsNone(self._client(error=requests.ConnectionError("down")).creator_lookup("x"))
        self.assertIsNone(self._client(_Response(200, {"unexpected": 1})).creator_lookup("x"))


class _RemovedClient:
    def __init__(self, status=404, answer="active"):
        self.status = status
        self.answer = answer
        self.lookups: list[str] = []
        self.creator_status_cache: dict = {}

    def get_version_by_hash(self, sha256_hash):  # noqa: ARG002
        return None

    def get_model_versions_for_model(self, model_id):
        raise CivitaiRequestError(f"{model_id} :: gone", status=self.status)

    def creator_lookup(self, username):
        self.lookups.append(username)
        return self.answer

    def model_page_url(self, model_id):
        return f"https://civitai.red/models/{model_id}"

    def version_page_url(self, model_id, version_id=None):
        return f"https://civitai.red/models/{model_id}?modelVersionId={version_id}"


def _check(client, tmpdir: str, name: str, creator: str | None = "artist") -> dict:
    path = Path(tmpdir) / f"{name}.safetensors"
    path.write_bytes(b"x")
    info = {"id": 456, "modelId": 123, "name": "Local", "publishedAt": "2024-01-01T00:00:00Z", "images": []}
    if creator:
        info["creator"] = {"username": creator}
    write_json(info_sidecar_path(path), info)
    return UpdaterService(None)._process_one(
        client=client, model_path=path, model_type="lora", mode="check", refetch_metadata=False, force_rehash=False
    )


class CreatorStatusServiceTests(unittest.TestCase):
    def test_404_sets_active_or_gone(self) -> None:
        for answer, expected in (("active", "active"), ("gone", "gone"), ("missing", "missing"), (None, "")):
            with tempfile.TemporaryDirectory() as tmpdir:
                item = _check(_RemovedClient(answer=answer), tmpdir, "m")
            self.assertEqual(expected, item["creatorStatus"], answer)

    def test_two_models_by_one_creator_look_up_once(self) -> None:
        client = _RemovedClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            first = _check(client, tmpdir, "a")
            second = _check(client, tmpdir, "b")
        self.assertEqual(["artist"], client.lookups)
        self.assertEqual(("active", "active"), (first["creatorStatus"], second["creatorStatus"]))

    def test_a_failed_lookup_is_not_repeated_for_the_same_creator(self) -> None:
        client = _RemovedClient(answer=None)
        with tempfile.TemporaryDirectory() as tmpdir:
            _check(client, tmpdir, "a")
            _check(client, tmpdir, "b")
        self.assertEqual(["artist"], client.lookups)

    def test_other_failures_do_no_lookup(self) -> None:
        for status in (None, 500, 403):
            client = _RemovedClient(status=status)
            with tempfile.TemporaryDirectory() as tmpdir:
                item = _check(client, tmpdir, "m")
            self.assertEqual([], client.lookups, status)
            self.assertEqual("", item["creatorStatus"], status)

    def test_unknown_creator_does_no_lookup(self) -> None:
        client = _RemovedClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            item = _check(client, tmpdir, "m", creator=None)
        self.assertEqual([], client.lookups)
        self.assertEqual("", item["creatorStatus"])


if __name__ == "__main__":
    unittest.main()
