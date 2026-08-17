"""Tests for the RCON-driven mod update check."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import logs, modcheck
from pzctl.config import Config

# What the server actually replies - the answer is not in here.
STARTED_REPLY = "Checking started. The answer will be written in the log file and in the chat"


class FakeSupervisor:
    def __init__(self, alive: bool = True, reply=(True, STARTED_REPLY)):
        self._alive = alive
        self.reply = reply
        self.sent: list[str] = []

    def is_alive(self) -> bool:
        return self._alive

    def emit(self, text: str, stream: str = "pzctl") -> None:
        pass

    def send_command(self, cmd: str, prefer: str = "auto", echo_as: str | None = None):
        self.sent.append(cmd)
        return self.reply


class ModCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.logs_dir = logs.log_dir(self.cfg)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log = self.logs_dir / "25-08-17_12-00-00_DebugLog-server.txt"
        self.log.write_text("server starting\n", encoding="utf-8", newline="")

        modcheck.reset()
        self.addCleanup(modcheck.reset)

    def append(self, text: str) -> None:
        with self.log.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)


class RequestTests(ModCheckTestCase):
    def test_sends_the_command(self):
        sup = FakeSupervisor()
        result = modcheck.request(self.cfg, sup)
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ["checkModsNeedUpdate"])

    def test_status_is_checking_not_an_answer(self):
        """The RCON reply acknowledges the request; it is not the result."""
        result = modcheck.request(self.cfg, FakeSupervisor())
        self.assertEqual(result["status"], "checking")
        self.assertIn("Checking started", result["reply"])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        result = modcheck.request(self.cfg, sup)
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_send_failure_is_reported(self):
        result = modcheck.request(self.cfg, FakeSupervisor(reply=(False, "rcon down")))
        self.assertFalse(result["ok"])
        self.assertIn("rcon down", result["error"])


class PollTests(ModCheckTestCase):
    def test_idle_before_any_check(self):
        self.assertEqual(modcheck.poll(self.cfg)["status"], "idle")

    def test_checking_while_nothing_has_appeared(self):
        modcheck.request(self.cfg, FakeSupervisor())
        self.assertEqual(modcheck.poll(self.cfg)["status"], "checking")

    def test_detects_the_marker(self):
        modcheck.request(self.cfg, FakeSupervisor())
        self.append(modcheck.MARKER + "\n")
        result = modcheck.poll(self.cfg)
        self.assertEqual(result["status"], "update_needed")
        self.assertEqual(result["found_in"], self.log.name)

    def test_marker_from_before_the_check_is_ignored(self):
        """Only bytes appended after the command was sent count."""
        self.append(modcheck.MARKER + "\n")
        modcheck.request(self.cfg, FakeSupervisor())
        self.assertEqual(modcheck.poll(self.cfg)["status"], "checking")

    def test_result_is_sticky(self):
        modcheck.request(self.cfg, FakeSupervisor())
        self.append(modcheck.MARKER + "\n")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "update_needed")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "update_needed")

    def test_marker_in_any_log_file_counts(self):
        other = self.logs_dir / "server-console.txt"
        other.write_text("boot\n", encoding="utf-8", newline="")
        modcheck.request(self.cfg, FakeSupervisor())
        with other.open("a", encoding="utf-8", newline="") as handle:
            handle.write(modcheck.MARKER + "\n")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "update_needed")

    def test_a_log_created_after_the_check_is_still_scanned(self):
        modcheck.request(self.cfg, FakeSupervisor())
        fresh = self.logs_dir / "25-08-17_13-00-00_DebugLog-server.txt"
        fresh.write_text(modcheck.MARKER + "\n", encoding="utf-8", newline="")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "update_needed")

    def test_unrelated_log_growth_is_not_a_false_positive(self):
        modcheck.request(self.cfg, FakeSupervisor())
        self.append("player connected\nzombie spawned\n")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "checking")

    def test_window_expiry_never_claims_up_to_date(self):
        """Absence of the marker is not proof that mods are current."""
        modcheck.request(self.cfg, FakeSupervisor())
        modcheck._state["at"] -= modcheck.WINDOW_SEC + 1

        result = modcheck.poll(self.cfg)

        self.assertEqual(result["status"], "no_update_reported")
        text = (result["status"] + result.get("note", "")).lower()
        self.assertNotIn("up to date", result["status"])
        self.assertIn("absence", text)

    def test_marker_still_wins_after_the_window(self):
        modcheck.request(self.cfg, FakeSupervisor())
        modcheck._state["at"] -= modcheck.WINDOW_SEC + 1
        self.append(modcheck.MARKER + "\n")
        self.assertEqual(modcheck.poll(self.cfg)["status"], "update_needed")

    def test_missing_log_dir_does_not_raise(self):
        cfg = Config(self.dir / "other.json")
        cfg.set("zomboid_dir", str(self.dir / "nowhere"))
        modcheck.request(cfg, FakeSupervisor())
        self.assertEqual(modcheck.poll(cfg)["status"], "checking")

    def test_reset_clears_state(self):
        modcheck.request(self.cfg, FakeSupervisor())
        modcheck.reset()
        self.assertEqual(modcheck.poll(self.cfg)["status"], "idle")


class MarkerTests(unittest.TestCase):
    def test_marker_is_the_documented_string(self):
        """Community tooling greps for this exact text; do not paraphrase it."""
        self.assertEqual(modcheck.MARKER, "CheckModsNeedUpdate: Mods need update")


if __name__ == "__main__":
    unittest.main()
