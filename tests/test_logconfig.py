"""Tests for log verbosity control."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import logconfig
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self, alive: bool = True, reply=(True, "OK")):
        self._alive = alive
        self.reply = reply
        self.sent: list[str] = []

    def is_alive(self) -> bool:
        return self._alive

    def send_command(self, cmd: str, prefer: str = "auto", echo_as=None):
        self.sent.append(cmd)
        return self.reply


class ParseTests(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(logconfig.parse_categories("Network,Sound"), ["Network", "Sound"])

    def test_whitespace_trimmed(self):
        self.assertEqual(logconfig.parse_categories(" Network , Sound "), ["Network", "Sound"])

    def test_blanks_dropped(self):
        self.assertEqual(logconfig.parse_categories("Network,,Sound,"), ["Network", "Sound"])

    def test_accepts_a_list(self):
        self.assertEqual(logconfig.parse_categories(["Network", "Sound"]), ["Network", "Sound"])

    def test_empty(self):
        for value in ["", None, [], "   "]:
            self.assertEqual(logconfig.parse_categories(value), [], repr(value))


class ValidateTests(unittest.TestCase):
    def test_valid_tokens(self):
        tokens, error = logconfig.validate_categories("Network,Sound")
        self.assertIsNone(error)
        self.assertEqual(tokens, ["Network", "Sound"])

    def test_rejects_spaces_and_punctuation(self):
        for bad in ["Net work", "Sound!", "a;b", "-flag", "1Network"]:
            tokens, error = logconfig.validate_categories(bad)
            self.assertIsNotNone(error, bad)
            self.assertEqual(tokens, [])

    def test_rejects_injection_attempt(self):
        tokens, error = logconfig.validate_categories("Network -adminpassword hunter2")
        self.assertIsNotNone(error)
        self.assertEqual(tokens, [])

    def test_underscores_allowed(self):
        tokens, error = logconfig.validate_categories("Some_Type")
        self.assertIsNone(error)
        self.assertEqual(tokens, ["Some_Type"])


class LaunchArgTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(Path(self._tmp.name) / "pzctl.json")

    def test_nothing_configured_adds_nothing(self):
        self.assertEqual(logconfig.launch_args(self.cfg), [])

    def test_debug_categories(self):
        self.cfg.set("logging.debug", ["Network", "Sound"])
        self.assertEqual(logconfig.launch_args(self.cfg), ["-debuglog=Network,Sound"])

    def test_disable_categories(self):
        self.cfg.set("logging.disable", "All")
        self.assertEqual(logconfig.launch_args(self.cfg), ["-disablelog=All"])

    def test_both(self):
        self.cfg.set("logging.debug", ["Network"])
        self.cfg.set("logging.disable", ["Sound"])
        self.assertEqual(
            logconfig.launch_args(self.cfg), ["-debuglog=Network", "-disablelog=Sound"]
        )

    def test_invalid_values_are_dropped_not_passed_through(self):
        """A malformed flag would stop the server booting at all."""
        self.cfg.set("logging.debug", ["bad token"])
        self.cfg.set("logging.disable", ["Sound"])
        self.assertEqual(logconfig.launch_args(self.cfg), ["-disablelog=Sound"])

    def test_appears_in_the_real_command_line(self):
        from pzctl.supervisor import Supervisor

        self.cfg.set("logging.debug", ["Network"])
        args = Supervisor(self.cfg).build_command()
        self.assertIn("-debuglog=Network", args)

    def test_absent_from_the_command_line_by_default(self):
        from pzctl.supervisor import Supervisor

        args = Supervisor(self.cfg).build_command()
        self.assertFalse([a for a in args if a.startswith("-debuglog")])


class SetLevelTests(unittest.TestCase):
    def test_sends_documented_command(self):
        sup = FakeSupervisor()
        result = logconfig.set_level(sup, "Network", "Debug")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ['log "Network" "Debug"'])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        result = logconfig.set_level(sup, "Network", "Debug")
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_missing_type(self):
        sup = FakeSupervisor()
        self.assertFalse(logconfig.set_level(sup, "", "Debug")["ok"])
        self.assertEqual(sup.sent, [])

    def test_missing_level(self):
        sup = FakeSupervisor()
        self.assertFalse(logconfig.set_level(sup, "Network", "")["ok"])
        self.assertEqual(sup.sent, [])

    def test_injection_never_reaches_the_server(self):
        sup = FakeSupervisor()
        for bad_type, bad_level in [('Net" "x', "Debug"), ("Network", 'D" ; quit'), ("a\nb", "c")]:
            result = logconfig.set_level(sup, bad_type, bad_level)
            self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_reports_the_server_reply(self):
        sup = FakeSupervisor(reply=(True, "log level set"))
        self.assertEqual(logconfig.set_level(sup, "Network", "Debug")["reply"], "log level set")

    def test_failure_reported(self):
        sup = FakeSupervisor(reply=(False, "rcon down"))
        self.assertFalse(logconfig.set_level(sup, "Network", "Debug")["ok"])


if __name__ == "__main__":
    unittest.main()
