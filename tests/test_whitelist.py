"""Tests for whitelist mode and whitelisted users."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import pzini, whitelist
from pzctl.config import Config

SAMPLE_INI = "# server\r\nPVP=true\r\nOpen=true\r\nMaxPlayers=32\r\n"


class FakeSupervisor:
    def __init__(self, alive: bool = True, rcon: bool = True, reply=(True, "OK")):
        self._alive = alive
        self._rcon = rcon
        self.reply = reply
        self.sent: list[tuple[str, str | None]] = []
        self.emitted: list[tuple[str, str]] = []

    def is_alive(self) -> bool:
        return self._alive

    def rcon_ready(self) -> bool:
        return self._rcon

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.emitted.append((text, stream))

    def send_command(self, cmd: str, prefer: str = "auto", echo_as: str | None = None):
        self.sent.append((cmd, echo_as))
        return self.reply

    # Everything the console would ever show or write to disk.
    def visible_text(self) -> str:
        echoes = [echo if echo is not None else cmd for cmd, echo in self.sent]
        return "\n".join(echoes + [text for text, _ in self.emitted])


class WhitelistTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text(SAMPLE_INI, encoding="utf-8", newline="")


class StatusTests(WhitelistTestCase):
    def test_open_true_means_whitelist_off(self):
        result = whitelist.status(self.cfg)
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertTrue(result["open"])

    def test_open_false_means_whitelist_on(self):
        pzini.write(self.cfg.ini_path, {"Open": "false"})
        result = whitelist.status(self.cfg)
        self.assertTrue(result["enabled"])
        self.assertFalse(result["open"])

    def test_case_insensitive(self):
        pzini.write(self.cfg.ini_path, {"Open": "FALSE"})
        self.assertTrue(whitelist.status(self.cfg)["enabled"])

    def test_missing_key_defaults_to_open(self):
        self.cfg.ini_path.write_text("PVP=true\r\n", encoding="utf-8", newline="")
        self.assertFalse(whitelist.status(self.cfg)["enabled"])

    def test_missing_ini(self):
        self.cfg.ini_path.unlink()
        result = whitelist.status(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["error"])


class SetModeTests(WhitelistTestCase):
    def test_enabling_writes_open_false(self):
        """The inversion matters: getting it backwards opens the server."""
        result = whitelist.set_mode(self.cfg, None, True)
        self.assertTrue(result["ok"])
        self.assertEqual(pzini.read(self.cfg.ini_path)["Open"], "false")

    def test_disabling_writes_open_true(self):
        whitelist.set_mode(self.cfg, None, True)
        whitelist.set_mode(self.cfg, None, False)
        self.assertEqual(pzini.read(self.cfg.ini_path)["Open"], "true")

    def test_round_trips_through_status(self):
        whitelist.set_mode(self.cfg, None, True)
        self.assertTrue(whitelist.status(self.cfg)["enabled"])
        whitelist.set_mode(self.cfg, None, False)
        self.assertFalse(whitelist.status(self.cfg)["enabled"])

    def test_other_settings_are_untouched(self):
        whitelist.set_mode(self.cfg, None, True)
        values = pzini.read(self.cfg.ini_path)
        self.assertEqual(values["PVP"], "true")
        self.assertEqual(values["MaxPlayers"], "32")
        self.assertIn("# server", self.cfg.ini_path.read_text(encoding="utf-8"))

    def test_no_change_reports_nothing_changed(self):
        result = whitelist.set_mode(self.cfg, None, False)
        self.assertFalse(result["changed"])

    def test_applies_live_when_server_is_up(self):
        sup = FakeSupervisor()
        result = whitelist.set_mode(self.cfg, sup, True)
        self.assertIn("live", result)
        self.assertIn('changeoption Open "false"', [cmd for cmd, _ in sup.sent])

    def test_no_live_push_when_server_is_down(self):
        sup = FakeSupervisor(alive=False)
        result = whitelist.set_mode(self.cfg, sup, True)
        self.assertNotIn("live", result)
        self.assertEqual(sup.sent, [])

    def test_missing_ini(self):
        self.cfg.ini_path.unlink()
        self.assertFalse(whitelist.set_mode(self.cfg, None, True)["ok"])


class AddUserTests(WhitelistTestCase):
    def test_sends_documented_command(self):
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, "bob", "s3cret")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent[0][0], 'adduser "bob" "s3cret"')

    def test_password_is_redacted_in_the_echo(self):
        sup = FakeSupervisor()
        whitelist.add_user(sup, "bob", "s3cret")
        self.assertEqual(sup.sent[0][1], 'adduser "bob" "********"')

    def test_password_never_appears_in_anything_logged(self):
        """The console echo is written to disk, so this is the whole point."""
        sup = FakeSupervisor()
        whitelist.add_user(sup, "bob", "hunter2")
        self.assertNotIn("hunter2", sup.visible_text())

    def test_password_not_leaked_on_failure(self):
        sup = FakeSupervisor(reply=(False, "rcon failed"))
        whitelist.add_user(sup, "bob", "hunter2")
        self.assertNotIn("hunter2", sup.visible_text())

    def test_password_not_returned_to_the_caller(self):
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, "bob", "hunter2")
        self.assertNotIn("hunter2", repr(result))

    def test_username_is_logged(self):
        sup = FakeSupervisor()
        whitelist.add_user(sup, "bob", "x")
        self.assertTrue(any("bob" in text for text, _ in sup.emitted))

    def test_password_is_optional(self):
        """The game documents it as optional; pzctl used to demand one."""
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, "bob", "")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent[0][0], 'adduser "bob"')

    def test_omitted_password_is_not_sent_as_an_empty_pair(self):
        sup = FakeSupervisor()
        whitelist.add_user(sup, "bob", "")
        self.assertNotIn('""', sup.sent[0][0])

    def test_password_with_quote_rejected(self):
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, "bob", 'pa"ss')
        self.assertFalse(result["ok"])
        self.assertIn("not allowed", result["error"])
        self.assertEqual(sup.sent, [])

    def test_password_with_newline_rejected(self):
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, "bob", "pass\nquit")
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_bad_username_rejected(self):
        sup = FakeSupervisor()
        result = whitelist.add_user(sup, 'bob" "x', "pass")
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_requires_a_running_server(self):
        result = whitelist.add_user(FakeSupervisor(alive=False), "bob", "x")
        self.assertFalse(result["ok"])
        self.assertIn("not running", result["error"])


class RemoveUserTests(WhitelistTestCase):
    def test_sends_documented_command(self):
        sup = FakeSupervisor()
        result = whitelist.remove_user(sup, "bob")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent[0][0], 'removeuserfromwhitelist "bob"')

    def test_bad_username_rejected(self):
        sup = FakeSupervisor()
        self.assertFalse(whitelist.remove_user(sup, "bob\nquit")["ok"])
        self.assertEqual(sup.sent, [])

    def test_requires_a_running_server(self):
        self.assertFalse(whitelist.remove_user(FakeSupervisor(alive=False), "bob")["ok"])

    def test_is_logged(self):
        sup = FakeSupervisor()
        whitelist.remove_user(sup, "bob")
        self.assertTrue(any("whitelist: removing" in text for text, _ in sup.emitted))

    def test_failure_logged_as_error(self):
        sup = FakeSupervisor(reply=(False, "nope"))
        whitelist.remove_user(sup, "bob")
        self.assertTrue(any(stream == "error" for _, stream in sup.emitted))


if __name__ == "__main__":
    unittest.main()


class ApproveConnectedTests(WhitelistTestCase):
    def test_approves_one_player(self):
        sup = FakeSupervisor()
        result = whitelist.approve_connected(sup, "bob")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent[0][0], 'addusertowhitelist "bob"')

    def test_approves_everyone_connected(self):
        """No username means all currently connected accounts."""
        sup = FakeSupervisor()
        result = whitelist.approve_connected(sup)
        self.assertTrue(result["all"])
        self.assertEqual(sup.sent[0][0], "addalltowhitelist")

    def test_injection_refused(self):
        sup = FakeSupervisor()
        self.assertFalse(whitelist.approve_connected(sup, 'bob" ; quit')["ok"])
        self.assertEqual(sup.sent, [])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        self.assertFalse(whitelist.approve_connected(sup, "bob")["ok"])
        self.assertEqual(sup.sent, [])

    def test_is_logged(self):
        sup = FakeSupervisor()
        whitelist.approve_connected(sup, "bob")
        self.assertTrue(any("whitelist:" in text for text, _ in sup.emitted))
