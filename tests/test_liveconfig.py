"""Tests for applying .ini changes to a running server without a restart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import liveconfig
from pzctl.config import Config


class FakeSupervisor:
    """Records the commands sent and returns scripted replies."""

    def __init__(self, alive: bool = True, rcon: bool = True, replies: dict | None = None):
        self._alive = alive
        self._rcon = rcon
        self.sent: list[str] = []
        self.replies = replies or {}

    def is_alive(self) -> bool:
        return self._alive

    def rcon_ready(self) -> bool:
        return self._rcon

    def send_command(self, cmd: str, prefer: str = "auto") -> tuple[bool, str]:
        self.sent.append(cmd)
        for needle, reply in self.replies.items():
            if cmd.startswith(needle):
                return reply
        return True, "OK"


class LiveConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(Path(self._tmp.name) / "pzctl.json")


class GuardTests(LiveConfigTestCase):
    def test_no_changes_is_a_no_op(self):
        sup = FakeSupervisor()
        result = liveconfig.apply(self.cfg, sup, {})
        self.assertTrue(result["ok"])
        self.assertEqual(sup.sent, [])

    def test_requires_a_running_server(self):
        result = liveconfig.apply(self.cfg, FakeSupervisor(alive=False), {"PVP": "true"})
        self.assertFalse(result["ok"])
        self.assertIn("not running", result["error"])

    def test_requires_a_supervisor(self):
        result = liveconfig.apply(self.cfg, None, {"PVP": "true"})
        self.assertFalse(result["ok"])

    def test_requires_rcon(self):
        """Without RCON there is no reply, so results could not be reported."""
        sup = FakeSupervisor(rcon=False)
        result = liveconfig.apply(self.cfg, sup, {"PVP": "true"})
        self.assertFalse(result["ok"])
        self.assertIn("RCON", result["error"])
        self.assertEqual(sup.sent, [], "must not send anything it cannot verify")


class ApplyTests(LiveConfigTestCase):
    def test_sends_changeoption_then_reloadoptions(self):
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"PVP": "false"})
        self.assertEqual(sup.sent, ['changeoption PVP "false"', "reloadoptions"])

    def test_reloadoptions_runs_once_for_many_changes(self):
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"PVP": "false", "PauseEmpty": "true", "Open": "false"})
        self.assertEqual(sup.sent.count("reloadoptions"), 1)
        self.assertEqual(sup.sent[-1], "reloadoptions", "reload must come last")

    def test_reports_each_reply(self):
        sup = FakeSupervisor(replies={"changeoption PVP": (True, "PVP set to false")})
        result = liveconfig.apply(self.cfg, sup, {"PVP": "false"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"][0]["key"], "PVP")
        self.assertEqual(result["applied"][0]["reply"], "PVP set to false")

    def test_values_are_quoted(self):
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"ServerWelcomeMessage": "hello survivors"})
        self.assertIn('changeoption ServerWelcomeMessage "hello survivors"', sup.sent)

    def test_embedded_quotes_are_escaped(self):
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"ServerWelcomeMessage": 'say "hi"'})
        self.assertIn(r'changeoption ServerWelcomeMessage "say \"hi\""', sup.sent)

    def test_failure_is_reported_and_not_swallowed(self):
        sup = FakeSupervisor(replies={"changeoption PVP": (False, "rcon failed: timeout")})
        result = liveconfig.apply(self.cfg, sup, {"PVP": "false"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"][0]["key"], "PVP")
        self.assertEqual(result["applied"], [])

    def test_one_failure_does_not_stop_the_others(self):
        sup = FakeSupervisor(replies={"changeoption PVP": (False, "nope")})
        result = liveconfig.apply(self.cfg, sup, {"PVP": "false", "PauseEmpty": "true"})
        self.assertEqual([entry["key"] for entry in result["applied"]], ["PauseEmpty"])
        self.assertEqual([entry["key"] for entry in result["failed"]], ["PVP"])

    def test_failed_reload_is_reported(self):
        sup = FakeSupervisor(replies={"reloadoptions": (False, "rcon dropped")})
        result = liveconfig.apply(self.cfg, sup, {"PVP": "false"})
        self.assertFalse(result["ok"])
        self.assertFalse(result["reloaded"])
        self.assertIn("reloadoptions", [entry["key"] for entry in result["failed"]])


class BootOnlyTests(LiveConfigTestCase):
    def test_boot_only_keys_are_not_sent(self):
        sup = FakeSupervisor()
        result = liveconfig.apply(self.cfg, sup, {"RCONPort": "27016"})
        self.assertEqual(sup.sent, [], "must not attempt a change that cannot work")
        self.assertEqual(result["restart_required"], ["RCONPort"])

    def test_mods_require_a_restart(self):
        """Mods load at boot, so pushing them live would be meaningless."""
        sup = FakeSupervisor()
        result = liveconfig.apply(self.cfg, sup, {"Mods": "modA;modB"})
        self.assertEqual(result["restart_required"], ["Mods"])
        self.assertEqual(sup.sent, [])

    def test_no_reload_when_only_boot_only_keys_changed(self):
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"Mods": "modA", "RCONPort": "1"})
        self.assertNotIn("reloadoptions", sup.sent)

    def test_mixed_changes_split_correctly(self):
        sup = FakeSupervisor()
        result = liveconfig.apply(self.cfg, sup, {"PVP": "false", "Mods": "modA"})
        self.assertEqual([entry["key"] for entry in result["applied"]], ["PVP"])
        self.assertEqual(result["restart_required"], ["Mods"])
        self.assertIn('changeoption PVP "false"', sup.sent)
        self.assertNotIn('changeoption Mods "modA"', sup.sent)

    def test_boot_only_alone_is_still_ok(self):
        """Nothing failed - the keys simply need a restart."""
        result = liveconfig.apply(self.cfg, FakeSupervisor(), {"Mods": "modA"})
        self.assertTrue(result["ok"])

    def test_unknown_keys_are_sent_for_the_server_to_judge(self):
        """pzctl does not keep a whitelist; the server is the authority."""
        sup = FakeSupervisor()
        liveconfig.apply(self.cfg, sup, {"SomeFutureOption": "1"})
        self.assertIn('changeoption SomeFutureOption "1"', sup.sent)


if __name__ == "__main__":
    unittest.main()
