"""DNS-rebinding protection of request_guard: which Host a loopback-only ComfyUI accepts."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from multidict import CIMultiDict

from comfy_updater import request_guard
from comfy_updater.request_guard import (
    TRUSTED_HOSTS_ENV,
    guarded,
    host_rejection,
    listens_only_on_loopback,
    parse_trusted_hosts,
    request_rejection,
)

PANEL = CIMultiDict({"X-Civitai-Updater": "1"})

ACCEPTED_HOSTS = (
    "localhost",
    "localhost:8188",
    "LOCALHOST:8188",
    "localhost.",
    "localhost.:8188",
    "comfy.localhost:8188",
    "127.0.0.1",
    "127.0.0.1:8188",
    "127.0.0.1.:8188",
    "192.168.1.20:8188",
    "[::1]",
    "[::1]:8188",
    "[2001:db8::5]:8188",
)

REJECTED_HOSTS = (
    "",
    None,
    "   ",
    "attacker.example",
    "attacker.example:8188",
    "ATTACKER.example.:8188",
    "127.0.0.1.attacker.example:8188",
    "localhost.attacker.example:8188",
    "localhostattacker.example",
    "[attacker.example]:8188",
    "[127.0.0.1]:8188",
    "[::1",
    "[::1]junk",
    "0x7f.1:8188",
)


class HostRuleTests(unittest.TestCase):
    def test_accepted_host_forms(self) -> None:
        for host in ACCEPTED_HOSTS:
            self.assertIsNone(host_rejection(host), host)

    def test_rejected_host_forms(self) -> None:
        for host in REJECTED_HOSTS:
            self.assertIsNotNone(host_rejection(host), host)

    def test_refusal_names_the_host_and_the_way_to_allow_it(self) -> None:
        message = host_rejection("Attacker.Example.:8188")
        self.assertIn('"attacker.example"', message)
        self.assertIn(f"{TRUSTED_HOSTS_ENV}=attacker.example", message)
        self.assertNotIn("8188", message)

    def test_trusted_host_is_accepted(self) -> None:
        trusted = parse_trusted_hosts("comfy.example.net")
        self.assertIsNone(host_rejection("comfy.example.net", trusted))
        self.assertIsNone(host_rejection("COMFY.example.net.:443", trusted))
        self.assertIsNotNone(host_rejection("other.example.net", trusted))
        self.assertIsNotNone(host_rejection("evil.comfy.example.net", trusted))


class TrustedHostsEnvTests(unittest.TestCase):
    def test_parsing(self) -> None:
        self.assertEqual(
            frozenset({"a.example", "b.example", "c.example", "::1"}),
            parse_trusted_hosts(" A.example , b.example:8188,,c.example.,  [::1]:8188 "),
        )

    def test_empty_values(self) -> None:
        for value in (None, "", " ", ",", " , "):
            self.assertEqual(frozenset(), parse_trusted_hosts(value), value)

    def test_request_rejection_reads_the_environment(self) -> None:
        with patch.dict("os.environ", {TRUSTED_HOSTS_ENV: "tunnel.example"}):
            self.assertIsNone(request_rejection("GET", PANEL, "tunnel.example", listen="127.0.0.1"))
        with patch.dict("os.environ", {TRUSTED_HOSTS_ENV: ""}):
            self.assertIsNotNone(request_rejection("GET", PANEL, "tunnel.example", listen="127.0.0.1"))


class ListenTests(unittest.TestCase):
    def test_loopback_only_listen_values(self) -> None:
        for listen in ("127.0.0.1", "::1", "[::1]", "localhost", "127.0.0.1,::1", " 127.0.0.2 "):
            self.assertTrue(listens_only_on_loopback(listen), listen)

    def test_exposed_listen_values(self) -> None:
        for listen in ("0.0.0.0", "::", "0.0.0.0,::", "192.168.1.20", "127.0.0.1,192.168.1.20", "myhost"):
            self.assertFalse(listens_only_on_loopback(listen), listen)

    def test_unknown_listen_values(self) -> None:
        for listen in (None, "", " , "):
            self.assertIsNone(listens_only_on_loopback(listen), listen)

    def test_host_check_applies_only_on_loopback(self) -> None:
        rebinding = ("GET", PANEL, "attacker.example:8188")
        self.assertIsNotNone(request_rejection(*rebinding, listen="127.0.0.1", trusted_hosts=""))
        for listen in ("0.0.0.0", "::", "0.0.0.0,::", "192.168.1.20", None):
            self.assertIsNone(request_rejection(*rebinding, listen=listen, trusted_hosts=""), listen)

    def test_missing_header_is_still_reported_first(self) -> None:
        self.assertEqual(
            "Missing X-Civitai-Updater header.",
            request_rejection("GET", CIMultiDict(), "attacker.example", listen="127.0.0.1"),
        )

    def test_listen_is_read_from_comfy(self) -> None:
        comfy = types.ModuleType("comfy")
        cli_args = types.ModuleType("comfy.cli_args")
        cli_args.args = types.SimpleNamespace(listen="127.0.0.1")
        comfy.cli_args = cli_args
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.cli_args": cli_args}), patch.object(
            request_guard, "_comfy_listen_cache", None
        ), patch.dict("os.environ", {TRUSTED_HOSTS_ENV: ""}):
            self.assertIsNotNone(request_rejection("GET", PANEL, "attacker.example"))
            self.assertIsNone(request_rejection("GET", PANEL, "127.0.0.1:8188"))

    def test_without_comfy_the_check_is_skipped(self) -> None:
        with patch.dict(sys.modules, {"comfy": None, "comfy.cli_args": None}), patch.object(
            request_guard, "_comfy_listen_cache", None
        ):
            self.assertIsNone(request_guard._comfy_listen())
            self.assertIsNone(request_rejection("GET", PANEL, "attacker.example"))

    def test_importing_the_guard_does_not_import_comfy(self) -> None:
        source = open(request_guard.__file__, encoding="utf-8").read()
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse([line for line in top_level if "comfy" in line], top_level)


class GuardedRebindingTests(unittest.TestCase):
    def _call(self, host: str):
        calls = []

        async def handler(request):
            calls.append(request)
            return "handled"

        request = MagicMock(method="GET", host=host, headers=CIMultiDict(PANEL))
        with patch.object(request_guard, "_comfy_listen", return_value="127.0.0.1"), patch.dict(
            "os.environ", {TRUSTED_HOSTS_ENV: ""}
        ):
            return asyncio.run(guarded(handler)(request)), calls

    def test_rebinding_request_gets_403_with_the_message(self) -> None:
        response, calls = self._call("attacker.example:8188")
        self.assertEqual([], calls)
        self.assertEqual(403, response.status)
        error = json.loads(response.text)["error"]
        self.assertIn('"attacker.example"', error)
        self.assertIn(TRUSTED_HOSTS_ENV, error)

    def test_normal_request_reaches_the_handler(self) -> None:
        response, calls = self._call("127.0.0.1:8188")
        self.assertEqual("handled", response)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
