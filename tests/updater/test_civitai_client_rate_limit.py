from __future__ import annotations

import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from comfy_updater import civitai_client
from comfy_updater.civitai_client import (
    RETRY_AFTER_MAX_SECONDS,
    RETRY_AFTER_MIN_SECONDS,
    CivitaiClient,
    CivitaiRequestError,
    _retry_delay_seconds,
)


class _Response:
    def __init__(self, status_code: int, headers: dict | None = None, body: dict | None = None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.reason = "Too Many Requests" if status_code == 429 else "Reason"
        self.headers = headers or {}
        self._body = body if body is not None else {"id": 1}

    def json(self):
        return self._body


class _FakeTime:
    """A clock that only moves when something sleeps, and records the sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeTime()
        patcher = patch.object(
            civitai_client, "time", types.SimpleNamespace(monotonic=self.clock.monotonic, sleep=self.clock.sleep)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        wall = patch.object(
            civitai_client, "_wall_now", lambda: datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        )
        wall.start()
        self.addCleanup(wall.stop)

    def _client(self, replies: list, max_retries: int = 3, should_stop=None) -> tuple[CivitaiClient, list]:
        client = CivitaiClient(api_key="", timeout_seconds=5, max_retries=max_retries, should_stop=should_stop)
        sent: list[str] = []
        queue = list(replies)

        def get(url, **kwargs):
            sent.append(url)
            return queue.pop(0)

        client.session.get = get  # type: ignore[assignment]
        return client, sent

    def test_retry_after_seconds_replaces_backoff(self) -> None:
        client, sent = self._client([_Response(429, {"Retry-After": "7"}), _Response(200)])
        self.assertEqual({"id": 1}, client.get_version(1))
        self.assertEqual(2, len(sent))
        self.assertEqual(7.0, sum(self.clock.sleeps))

    def test_retry_after_http_date(self) -> None:
        header = {"Retry-After": "Tue, 01 Jan 2030 00:00:30 GMT"}
        client, _ = self._client([_Response(429, header), _Response(200)])
        client.get_version(1)
        self.assertAlmostEqual(30.0, sum(self.clock.sleeps))

    def test_unusable_header_falls_back_to_backoff(self) -> None:
        for value in ("soon", "-5", "", "Mon, 01 Jan 2029 00:00:00 GMT"):
            with self.subTest(value=value):
                self.clock.sleeps.clear()
                client, _ = self._client([_Response(429, {"Retry-After": value}), _Response(200)])
                client.get_version(1)
                self.assertEqual([_retry_delay_seconds(0)], self.clock.sleeps)

    def test_wait_is_clamped_at_both_ends(self) -> None:
        client, _ = self._client([_Response(429, {"Retry-After": "0"}), _Response(200)])
        client.get_version(1)
        self.assertEqual(RETRY_AFTER_MIN_SECONDS, sum(self.clock.sleeps))

        self.clock.sleeps.clear()
        client, _ = self._client([_Response(429, {"Retry-After": "86400"}), _Response(200)])
        client.get_version(1)
        self.assertEqual(RETRY_AFTER_MAX_SECONDS, sum(self.clock.sleeps))

    def test_503_honours_the_header_only_when_present(self) -> None:
        client, _ = self._client([_Response(503, {"Retry-After": "9"}), _Response(200)])
        client.get_version(1)
        self.assertEqual(9.0, sum(self.clock.sleeps))

        self.clock.sleeps.clear()
        client, _ = self._client([_Response(503), _Response(200)])
        client.get_version(1)
        self.assertEqual([_retry_delay_seconds(0)], self.clock.sleeps)

    def test_other_statuses_ignore_the_header(self) -> None:
        client, _ = self._client([_Response(500, {"Retry-After": "50"}), _Response(200)])
        client.get_version(1)
        self.assertEqual([_retry_delay_seconds(0)], self.clock.sleeps)

    def test_cooldown_of_one_request_holds_back_the_next_without_costing_an_attempt(self) -> None:
        client, sent = self._client(
            [_Response(429, {"Retry-After": "20"}), _Response(200), _Response(200)], max_retries=0
        )
        with self.assertRaises(CivitaiRequestError):
            client.get_version(1, raise_on_error=True)
        self.assertEqual(1, len(sent))

        started = self.clock.now
        self.assertEqual({"id": 1}, client.get_version(2))
        self.assertAlmostEqual(20.0, self.clock.now - started)

    def test_waiting_for_a_cooldown_keeps_every_attempt(self) -> None:
        client, sent = self._client(
            [_Response(429, {"Retry-After": "5"})]
            + [_Response(429, {"Retry-After": "5"})] * 3
            + [_Response(200)],
            max_retries=3,
        )
        client._extend_cooldown(30)  # Set by some other request.
        self.assertIsNone(client.get_version(1))
        # 1 + max_retries sends, no more, whatever the cool-downs.
        self.assertEqual(4, len(sent))

    def test_a_smaller_later_value_does_not_shorten_the_cooldown(self) -> None:
        client, _ = self._client([])
        client._extend_cooldown(60)
        client._extend_cooldown(5)
        started = self.clock.now
        self.assertTrue(client._wait_for_cooldown())
        self.assertAlmostEqual(60.0, self.clock.now - started)

    def test_attempts_per_request_unchanged_without_header(self) -> None:
        client, sent = self._client([_Response(429)] * 4, max_retries=3)
        self.assertIsNone(client.get_version(1))
        self.assertEqual(4, len(sent))
        # The same total wait as before; it is slept as a shared cool-down now.
        self.assertAlmostEqual(sum(_retry_delay_seconds(n) for n in range(3)), sum(self.clock.sleeps))

    def test_a_429_without_a_header_holds_back_the_next_request_too(self) -> None:
        client, sent = self._client([_Response(429), _Response(200), _Response(200)], max_retries=3)
        self.assertEqual({"id": 1}, client.get_version(1))
        self.assertEqual(2, len(sent))
        client._extend_cooldown(0)  # no-op: must not shorten or clear anything
        slept = sum(self.clock.sleeps)
        self.assertAlmostEqual(_retry_delay_seconds(0), slept)

        other, _ = self._client([_Response(429)], max_retries=0)
        other.get_version(1)
        # The last attempt has no retry to wait for, so it sets no cool-down.
        self.assertTrue(other._wait_for_cooldown())
        self.assertAlmostEqual(slept, sum(self.clock.sleeps))

    def test_stop_ends_a_cooldown_wait(self) -> None:
        client, sent = self._client([_Response(200)], should_stop=lambda: True)
        client._extend_cooldown(100)
        self.assertIsNone(client.get_version(1))
        self.assertEqual([], sent)
        self.assertEqual([], self.clock.sleeps)

    def test_stop_during_a_cooldown_is_an_error_for_a_caller_that_asked_for_errors(self) -> None:
        client, sent = self._client([_Response(200)], should_stop=lambda: True)
        client._extend_cooldown(100)
        with self.assertRaises(CivitaiRequestError):
            client._get_json("https://civitai.com/api/v1/model-versions/1", raise_on_error=True)
        self.assertEqual([], sent)

    def test_cooldown_sleeps_in_slices(self) -> None:
        client, _ = self._client([])
        client._extend_cooldown(3.5)
        client._wait_for_cooldown()
        self.assertEqual([1.0, 1.0, 1.0, 0.5], self.clock.sleeps)


if __name__ == "__main__":
    unittest.main()
