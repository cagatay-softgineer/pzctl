"""Tests for kick/ban/unban command building and dispatch."""

from __future__ import annotations

import unittest

from pzctl import moderation


class FakeSupervisor:
    def __init__(self, alive: bool = True, reply=(True, "OK")):
        self._alive = alive
        self.reply = reply
        self.sent: list[str] = []
        self.emitted: list[tuple[str, str]] = []

    def is_alive(self) -> bool:
        return self._alive

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.emitted.append((text, stream))

    def send_command(self, cmd: str, prefer: str = "auto"):
        self.sent.append(cmd)
        return self.reply


class BuildCommandTests(unittest.TestCase):
    def build(self, *args, **kwargs):
        return moderation.build_command(*args, **kwargs)

    def test_kick_uses_kickuser_not_kick(self):
        """The documented command is `kickuser`; plain `kick` does not exist."""
        command, error = self.build("kick", "rj")
        self.assertIsNone(error)
        self.assertEqual(command, 'kickuser "rj"')

    def test_kick_with_reason(self):
        command, _ = self.build("kick", "rj", reason="afk")
        self.assertEqual(command, 'kickuser "rj" -r "afk"')

    def test_ban_matches_documented_syntax(self):
        command, _ = self.build("ban", "rj", reason="spawn kill", ban_ip=True)
        self.assertEqual(command, 'banuser "rj" -ip -r "spawn kill"')

    def test_ban_without_ip_flag(self):
        command, _ = self.build("ban", "rj")
        self.assertEqual(command, 'banuser "rj"')

    def test_unban(self):
        command, _ = self.build("unban", "rj")
        self.assertEqual(command, 'unbanuser "rj"')

    def test_banid_takes_a_bare_id(self):
        command, error = self.build("banid", "76561198000000000")
        self.assertIsNone(error)
        self.assertEqual(command, "banid 76561198000000000")

    def test_unbanid(self):
        command, _ = self.build("unbanid", "76561198000000000")
        self.assertEqual(command, "unbanid 76561198000000000")

    def test_banip(self):
        command, error = self.build("banip", "192.168.0.5")
        self.assertIsNone(error)
        self.assertEqual(command, "banip 192.168.0.5")

    def test_unknown_action(self):
        command, error = self.build("nuke", "rj")
        self.assertIsNone(command)
        self.assertIn("unknown action", error)


class ValidationTests(unittest.TestCase):
    def test_empty_name_rejected(self):
        command, error = moderation.build_command("kick", "")
        self.assertIsNone(command)
        self.assertIn("no player name", error)

    def test_quote_in_name_rejected(self):
        """A quote would close the argument and let a second command follow."""
        command, error = moderation.build_command("kick", 'rj" -r "x')
        self.assertIsNone(command)
        self.assertIn("not allowed", error)

    def test_newline_in_name_rejected(self):
        command, error = moderation.build_command("ban", "rj\nquit")
        self.assertIsNone(command)
        self.assertIn("not allowed", error)

    def test_carriage_return_in_name_rejected(self):
        command, error = moderation.build_command("ban", "rj\rquit")
        self.assertIsNone(command)

    def test_absurdly_long_name_rejected(self):
        command, error = moderation.build_command("kick", "x" * 200)
        self.assertIsNone(command)
        self.assertIn("long", error)

    def test_reason_is_stripped_of_breakout_characters(self):
        command, error = moderation.build_command("kick", "rj", reason='bad"\nquit')
        self.assertIsNone(error)
        self.assertEqual(command, 'kickuser "rj" -r "badquit"')
        self.assertEqual(command.count('"'), 4)

    def test_non_numeric_steam_id_rejected(self):
        command, error = moderation.build_command("banid", "not-an-id")
        self.assertIsNone(command)
        self.assertIn("Steam ID", error)

    def test_steam_id_with_injection_rejected(self):
        command, error = moderation.build_command("banid", "123456\nquit")
        self.assertIsNone(command)

    def test_bad_ip_rejected(self):
        for value in ["999.1.1.1", "1.2.3", "not.an.ip.addr", "1.2.3.4.5"]:
            command, error = moderation.build_command("banip", value)
            self.assertIsNone(command, value)

    def test_valid_edge_ip_accepted(self):
        command, error = moderation.build_command("banip", "255.255.255.255")
        self.assertIsNone(error)


class ActTests(unittest.TestCase):
    def test_sends_the_command(self):
        sup = FakeSupervisor()
        result = moderation.act(sup, "kick", "rj")
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, ['kickuser "rj"'])

    def test_requires_a_running_server(self):
        sup = FakeSupervisor(alive=False)
        result = moderation.act(sup, "kick", "rj")
        self.assertFalse(result["ok"])
        self.assertIn("not running", result["error"])
        self.assertEqual(sup.sent, [])

    def test_no_supervisor(self):
        self.assertFalse(moderation.act(None, "kick", "rj")["ok"])

    def test_invalid_target_never_reaches_the_server(self):
        sup = FakeSupervisor()
        result = moderation.act(sup, "kick", 'rj" -r "x')
        self.assertFalse(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_action_is_logged_for_audit(self):
        sup = FakeSupervisor()
        moderation.act(sup, "ban", "rj", reason="griefing")
        audit = [text for text, _ in sup.emitted]
        self.assertTrue(any("moderation: ban" in line for line in audit), audit)
        self.assertTrue(any("griefing" in line for line in audit), audit)

    def test_ip_ban_is_noted_in_the_audit_line(self):
        sup = FakeSupervisor()
        moderation.act(sup, "ban", "rj", ban_ip=True)
        self.assertTrue(any("IP ban" in text for text, _ in sup.emitted))

    def test_audit_written_before_sending(self):
        """The record must survive a command that fails or never returns."""
        sup = FakeSupervisor(reply=(False, "rcon failed"))
        moderation.act(sup, "ban", "rj")
        self.assertTrue(sup.emitted, "nothing was logged")
        self.assertIn("moderation: ban", sup.emitted[0][0])

    def test_failure_is_reported_and_logged(self):
        sup = FakeSupervisor(reply=(False, "rcon failed"))
        result = moderation.act(sup, "kick", "rj")
        self.assertFalse(result["ok"])
        self.assertTrue(any(stream == "error" for _, stream in sup.emitted))

    def test_result_includes_the_command_sent(self):
        result = moderation.act(FakeSupervisor(), "ban", "rj", reason="x", ban_ip=True)
        self.assertEqual(result["command"], 'banuser "rj" -ip -r "x"')


if __name__ == "__main__":
    unittest.main()
