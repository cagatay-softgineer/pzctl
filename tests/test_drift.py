"""Tests for config drift detection and the backup audit."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import backupaudit, drift
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self, alive=True, rcon=True, reply=(True, "")):
        self._alive, self._rcon, self.reply = alive, rcon, reply
        self.sent: list[str] = []

    def is_alive(self):
        return self._alive

    def rcon_ready(self):
        return self._rcon

    def send_command(self, cmd, prefer="auto", echo_as=None):
        self.sent.append(cmd)
        return self.reply


class ParseTests(unittest.TestCase):
    def test_plain_pairs(self):
        self.assertEqual(drift.parse_options("PVP=true\nMaxPlayers=32"),
                         {"PVP": "true", "MaxPlayers": "32"})

    def test_tolerates_spacing_and_colons(self):
        """The reply format is undocumented, so parsing is loose."""
        parsed = drift.parse_options("* PVP : true\n  MaxPlayers = 32\n- Open: false")
        self.assertEqual(parsed, {"PVP": "true", "MaxPlayers": "32", "Open": "false"})

    def test_ignores_lines_that_are_not_options(self):
        parsed = drift.parse_options("List of options:\n\nPVP=true\n(end)")
        self.assertEqual(parsed, {"PVP": "true"})

    def test_empty(self):
        self.assertEqual(drift.parse_options(""), {})


class CompareTests(unittest.TestCase):
    def test_no_difference(self):
        self.assertEqual(drift.compare({"PVP": "true"}, {"PVP": "true"}), [])

    def test_case_insensitive_values(self):
        self.assertEqual(drift.compare({"PVP": "TRUE"}, {"PVP": "true"}), [])

    def test_difference_reported_with_both_sides(self):
        out = drift.compare({"PVP": "false"}, {"PVP": "true"})
        self.assertEqual(out[0]["file"], "true")
        self.assertEqual(out[0]["live"], "false")

    def test_key_only_live_is_flagged(self):
        out = drift.compare({"NewOption": "1"}, {})
        self.assertEqual(out[0]["why"], "not in the file")

    def test_key_only_on_disk_is_not_flagged(self):
        """The server does not report every key the file may hold."""
        self.assertEqual(drift.compare({}, {"SomeKey": "1"}), [])

    def test_secrets_are_not_compared(self):
        self.assertEqual(drift.compare({"RCONPassword": "a"}, {"RCONPassword": "b"}), [])


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text("PVP=true\r\nMaxPlayers=32\r\n", encoding="utf-8", newline="")

    def test_requires_a_running_server(self):
        result = drift.check(self.cfg, FakeSupervisor(alive=False))
        self.assertFalse(result["ok"])
        self.assertIn("not running", result["error"])

    def test_requires_rcon(self):
        result = drift.check(self.cfg, FakeSupervisor(rcon=False))
        self.assertFalse(result["ok"])
        self.assertIn("RCON", result["error"])

    def test_in_sync(self):
        sup = FakeSupervisor(reply=(True, "PVP=true\nMaxPlayers=32"))
        result = drift.check(self.cfg, sup)
        self.assertTrue(result["ok"])
        self.assertTrue(result["in_sync"])
        self.assertEqual(sup.sent, ["showoptions"])

    def test_detects_drift(self):
        sup = FakeSupervisor(reply=(True, "PVP=false\nMaxPlayers=32"))
        result = drift.check(self.cfg, sup)
        self.assertFalse(result["in_sync"])
        self.assertEqual(result["drift"][0]["key"], "PVP")

    def test_unparseable_reply_is_an_error_not_a_clean_result(self):
        """Reporting 'no drift' for a reply we did not understand would mislead."""
        result = drift.check(self.cfg, FakeSupervisor(reply=(True, "???")))
        self.assertFalse(result["ok"])
        self.assertIn("could not read any options", result["error"])

    def test_missing_ini(self):
        self.cfg.ini_path.unlink()
        self.assertFalse(drift.check(self.cfg, FakeSupervisor())["ok"])


class BackupAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.set("backup.dir", str(self.dir / "Backups"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)

    def write_ini(self, extra=""):
        self.cfg.ini_path.write_text(f"PVP=true\r\n{extra}", encoding="utf-8", newline="")

    def test_missing_ini(self):
        self.assertFalse(backupaudit.audit(self.cfg)["ok"])

    def test_defaults_are_reported_when_absent(self):
        """BackupsOnStart defaults to true, which is the surprising one."""
        self.write_ini()
        game = backupaudit.audit(self.cfg)["game"]
        self.assertEqual(game["BackupsOnStart"]["value"], "true")
        self.assertFalse(game["BackupsOnStart"]["explicit"])

    def test_explicit_values_are_marked(self):
        self.write_ini("BackupsOnStart=false\r\n")
        game = backupaudit.audit(self.cfg)["game"]
        self.assertEqual(game["BackupsOnStart"]["value"], "false")
        self.assertTrue(game["BackupsOnStart"]["explicit"])

    def test_warns_when_restarts_double_up_with_backups_on_start(self):
        self.write_ini()
        self.cfg.set("schedule.restarts", [{"time": "04:00", "enabled": True}])
        notes = backupaudit.audit(self.cfg)["notes"]
        self.assertTrue(any("BackupsOnStart" in n["message"] for n in notes))

    def test_warns_when_both_run_on_timers(self):
        self.write_ini("BackupsPeriod=60\r\n")
        self.cfg.set("schedule.backups", [{"time": "05:00", "enabled": True}])
        notes = backupaudit.audit(self.cfg)["notes"]
        self.assertTrue(any("own timers" in n["message"] for n in notes))

    def test_warns_when_nothing_backs_up(self):
        self.write_ini("BackupsOnStart=false\r\n")
        notes = backupaudit.audit(self.cfg)["notes"]
        self.assertTrue(any("nothing is backing this world up" in n["message"] for n in notes))

    def test_disabled_jobs_do_not_count(self):
        self.write_ini()
        self.cfg.set("schedule.restarts", [{"time": "04:00", "enabled": False}])
        notes = backupaudit.audit(self.cfg)["notes"]
        self.assertFalse(any("scheduled restart" in n["message"] for n in notes))

    def test_reports_pzctl_side(self):
        self.write_ini()
        result = backupaudit.audit(self.cfg)
        self.assertEqual(result["pzctl"]["retention"], 14)
        self.assertEqual(result["pzctl"]["archives"], 0)


if __name__ == "__main__":
    unittest.main()
