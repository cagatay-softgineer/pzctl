"""Tests for the SteamCMD-driven server update.

No test runs SteamCMD. The guards that decide whether it runs at all are the
part worth covering; actually downloading a game server is not something a
test suite should do.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import serverupdate
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self, alive: bool = False):
        self._alive = alive
        self.emitted: list[str] = []

    def is_alive(self) -> bool:
        return self._alive

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.emitted.append(text)


class ServerUpdateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        serverupdate.reset()
        self.addCleanup(serverupdate.reset)

    def fake_steamcmd(self) -> Path:
        path = self.dir / "steamcmd.exe"
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.cfg.set("steamcmd_path", str(path))
        return path


class InstallDetectionTests(ServerUpdateTestCase):
    def test_empty_directory_is_not_a_server_install(self):
        self.assertFalse(serverupdate.looks_like_server_install(self.dir))

    def test_windows_launcher_counts(self):
        (self.dir / "StartServer64.bat").write_text("x", encoding="utf-8")
        self.assertTrue(serverupdate.looks_like_server_install(self.dir))

    def test_linux_launcher_counts(self):
        (self.dir / "start-server.sh").write_text("x", encoding="utf-8")
        self.assertTrue(serverupdate.looks_like_server_install(self.dir))

    def test_bundled_jre_counts(self):
        (self.dir / "jre64").mkdir()
        self.assertTrue(serverupdate.looks_like_server_install(self.dir))

    def test_the_pzctl_repo_is_not_a_server_install(self):
        """The guard that stops SteamCMD laying down a second copy somewhere odd."""
        from pzctl.config import SERVER_DIR

        self.assertFalse(serverupdate.looks_like_server_install(SERVER_DIR))


class SteamCmdDiscoveryTests(ServerUpdateTestCase):
    def test_configured_path_is_used(self):
        path = self.fake_steamcmd()
        self.assertEqual(serverupdate.find_steamcmd(self.cfg), path)

    def test_configured_path_that_does_not_exist(self):
        self.cfg.set("steamcmd_path", str(self.dir / "nope.exe"))
        self.assertIsNone(serverupdate.find_steamcmd(self.cfg))

    def test_blank_falls_back_to_path_lookup(self):
        self.cfg.set("steamcmd_path", "")
        # Whatever the machine has; the call must not raise either way.
        result = serverupdate.find_steamcmd(self.cfg)
        self.assertTrue(result is None or isinstance(result, Path))


class GuardTests(ServerUpdateTestCase):
    def test_refuses_while_the_server_is_running(self):
        self.fake_steamcmd()
        result = serverupdate.start(self.cfg, FakeSupervisor(alive=True))
        self.assertFalse(result["ok"])
        self.assertIn("stop the game server", result["error"])

    def test_refusal_explains_why(self):
        result = serverupdate.start(self.cfg, FakeSupervisor(alive=True))
        self.assertIn("open", result["error"])

    def test_refuses_without_steamcmd(self):
        self.cfg.set("steamcmd_path", str(self.dir / "missing.exe"))
        result = serverupdate.start(self.cfg, FakeSupervisor())
        self.assertFalse(result["ok"])
        self.assertIn("steamcmd not found", result["error"])

    def test_refuses_when_the_target_is_not_a_server_install(self):
        """SteamCMD would silently create a second install rather than fail."""
        self.fake_steamcmd()
        result = serverupdate.start(self.cfg, FakeSupervisor())
        self.assertFalse(result["ok"])
        self.assertIn("does not look like", result["error"])

    def test_refuses_a_second_concurrent_update(self):
        serverupdate._state["running"] = True
        result = serverupdate.start(self.cfg, FakeSupervisor())
        self.assertFalse(result["ok"])
        self.assertIn("already running", result["error"])


class StatusTests(ServerUpdateTestCase):
    def test_idle(self):
        state = serverupdate.status()
        self.assertTrue(state["ok"])
        self.assertFalse(state["running"])
        self.assertIsNone(state["result"])

    def test_reports_elapsed_while_running(self):
        import time

        serverupdate._state.update({"running": True, "started_at": time.time() - 5})
        state = serverupdate.status()
        self.assertTrue(state["running"])
        self.assertGreaterEqual(state["elapsed_sec"], 5)

    def test_reset_clears(self):
        serverupdate._state.update({"running": True, "result": {"ok": False}})
        serverupdate.reset()
        self.assertFalse(serverupdate.status()["running"])


class AppIdTests(ServerUpdateTestCase):
    def test_default_is_the_dedicated_server_not_the_game(self):
        """380870 is the server; 108600 is the game and would be wrong here."""
        self.assertEqual(serverupdate.APP_ID_DEFAULT, "380870")

    def test_default_is_present_in_config(self):
        self.assertEqual(self.cfg.get("steam_app_id"), "380870")

    def test_app_id_is_overridable(self):
        """So a wrong default can be fixed without a code change."""
        self.cfg.set("steam_app_id", "999")
        self.assertEqual(self.cfg.get("steam_app_id"), "999")


if __name__ == "__main__":
    unittest.main()
