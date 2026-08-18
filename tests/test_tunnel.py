"""Tests for Cloudflare Tunnel support.

No test runs cloudflared. The decisions worth covering are which command is
built, what is refused, and that the URL is picked out of the output.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import tunnel
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self):
        self.emitted: list[str] = []

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.emitted.append(text)


class TunnelTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("http.port", 8077)
        tunnel.reset()
        self.addCleanup(tunnel.reset)

        self.commands: list[list[str]] = []
        self._orig_popen = tunnel.subprocess.Popen

        import threading as _threading

        class FakeProc:
            """Stands in for cloudflared: emits its lines, then stays up.

            A real tunnel keeps running until stopped, so the fake must too -
            otherwise the reader thread ends immediately and the tunnel looks
            stopped before a test can look at it.
            """

            def __init__(self, lines):
                self.done = _threading.Event()
                self.terminated = False

                def stream():
                    for line in lines:
                        yield line
                    self.done.wait(5)

                self.stdout = stream()

            def wait(self, timeout=None):
                self.done.wait(timeout if timeout is not None else 5)
                return 0

            def terminate(self):
                self.terminated = True
                self.done.set()

            def kill(self):
                self.done.set()

        self.FakeProc = FakeProc
        self.lines: list[str] = []

        def fake_popen(cmd, **kwargs):
            self.commands.append(cmd)
            return FakeProc(list(self.lines))

        tunnel.subprocess.Popen = fake_popen
        self.addCleanup(lambda: setattr(tunnel.subprocess, "Popen", self._orig_popen))

    def fake_binary(self) -> Path:
        path = self.dir / "cloudflared.exe"
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        self.cfg.set("tunnel.cloudflared_path", str(path))
        return path


class DiscoveryTests(TunnelTestCase):
    def test_configured_path(self):
        path = self.fake_binary()
        self.assertEqual(tunnel.find_cloudflared(self.cfg), path)

    def test_configured_path_missing(self):
        self.cfg.set("tunnel.cloudflared_path", str(self.dir / "nope.exe"))
        self.assertIsNone(tunnel.find_cloudflared(self.cfg))

    def test_refuses_without_cloudflared(self):
        self.cfg.set("tunnel.cloudflared_path", str(self.dir / "nope.exe"))
        result = tunnel.start(self.cfg, None)
        self.assertFalse(result["ok"])
        self.assertIn("cloudflared not found", result["error"])


class StartTests(TunnelTestCase):
    def test_quick_tunnel_without_a_token(self):
        self.fake_binary()
        result = tunnel.start(self.cfg, FakeSupervisor())
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "quick")
        self.assertIn("--url", self.commands[0])
        self.assertIn("http://127.0.0.1:8077", self.commands[0])

    def test_named_tunnel_when_a_token_is_set(self):
        self.fake_binary()
        self.cfg.set("tunnel.token", "sekrit")
        result = tunnel.start(self.cfg, FakeSupervisor())
        self.assertEqual(result["mode"], "named")
        self.assertIn("run", self.commands[0])
        self.assertIn("--token", self.commands[0])

    def test_uses_the_configured_port(self):
        self.fake_binary()
        self.cfg.set("http.port", 9000)
        tunnel.start(self.cfg, FakeSupervisor())
        self.assertIn("http://127.0.0.1:9000", self.commands[0])

    def test_start_warns_about_exposure(self):
        """The panel becoming reachable is the whole point and the whole risk."""
        self.fake_binary()
        result = tunnel.start(self.cfg, FakeSupervisor())
        self.assertIn("only thing protecting it", result["warning"])

    def test_refuses_a_second_tunnel(self):
        self.fake_binary()
        tunnel.start(self.cfg, FakeSupervisor())
        result = tunnel.start(self.cfg, FakeSupervisor())
        self.assertFalse(result["ok"])
        self.assertIn("already running", result["error"])

    def test_status_reflects_a_running_tunnel(self):
        self.fake_binary()
        tunnel.start(self.cfg, FakeSupervisor())
        self.assertTrue(tunnel.status()["running"])


class UrlTests(TunnelTestCase):
    def test_picks_the_url_out_of_the_output(self):
        self.fake_binary()
        self.lines = [
            "some banner",
            "|  https://brave-otter-1234.trycloudflare.com   |",
            "more output",
        ]
        sup = FakeSupervisor()
        tunnel.start(self.cfg, sup)
        import time

        for _ in range(50):
            if tunnel.status()["url"]:
                break
            time.sleep(0.02)
        self.assertEqual(tunnel.status()["url"], "https://brave-otter-1234.trycloudflare.com")

    def test_reports_the_url_to_the_console(self):
        self.fake_binary()
        self.lines = ["https://brave-otter-1234.trycloudflare.com"]
        sup = FakeSupervisor()
        tunnel.start(self.cfg, sup)
        import time

        for _ in range(50):
            if any("reachable at" in line for line in sup.emitted):
                break
            time.sleep(0.02)
        self.assertTrue(any("reachable at" in line for line in sup.emitted))


class StopTests(TunnelTestCase):
    def test_stop_without_a_tunnel(self):
        result = tunnel.stop()
        self.assertFalse(result["ok"])
        self.assertIn("no tunnel", result["error"])

    def test_stop_terminates(self):
        self.fake_binary()
        tunnel.start(self.cfg, FakeSupervisor())
        result = tunnel.stop(FakeSupervisor())
        self.assertTrue(result["ok"])
        self.assertFalse(tunnel.status()["running"])


class DefaultTests(TunnelTestCase):
    def test_off_by_default(self):
        self.assertEqual(self.cfg.get("tunnel.token"), "")
        self.assertFalse(self.cfg.get("tunnel.autostart"))
        self.assertFalse(tunnel.status()["running"])


if __name__ == "__main__":
    unittest.main()
