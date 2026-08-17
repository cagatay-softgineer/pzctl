"""Tests for the anti-cheat toggles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import anticheat, pzini
from pzctl.config import Config


class AntiCheatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text("# server\r\nPVP=true\r\n", encoding="utf-8", newline="")


class NamingTests(unittest.TestCase):
    def test_key_shape_matches_the_server(self):
        """AntiCheatProtectionType<N> - not the named options the issue described."""
        self.assertEqual(anticheat.key_for(1), "AntiCheatProtectionType1")
        self.assertEqual(anticheat.key_for(24), "AntiCheatProtectionType24")

    def test_there_are_24(self):
        self.assertEqual(anticheat.COUNT, 24)

    def test_only_documented_types_carry_a_description(self):
        """Inventing labels would have admins disable the wrong check."""
        self.assertEqual(set(anticheat.KNOWN), {12, 21})


class ReadTests(AntiCheatTestCase):
    def test_missing_ini(self):
        self.cfg.ini_path.unlink()
        result = anticheat.read(self.cfg)
        self.assertFalse(result["ok"])

    def test_all_enabled_by_default_when_absent(self):
        """Absent means the server default, which is on."""
        result = anticheat.read(self.cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["types"]), 24)
        self.assertTrue(all(entry["enabled"] for entry in result["types"]))
        self.assertEqual(result["enabled_count"], 24)

    def test_absent_types_are_marked_not_explicit(self):
        self.assertFalse(anticheat.read(self.cfg)["types"][0]["explicit"])

    def test_reads_a_disabled_type(self):
        pzini.write(self.cfg.ini_path, {"AntiCheatProtectionType12": "false"})
        types = {e["number"]: e for e in anticheat.read(self.cfg)["types"]}
        self.assertFalse(types[12]["enabled"])
        self.assertTrue(types[12]["explicit"])
        self.assertEqual(anticheat.read(self.cfg)["enabled_count"], 23)

    def test_case_insensitive_false(self):
        pzini.write(self.cfg.ini_path, {"AntiCheatProtectionType3": "FALSE"})
        types = {e["number"]: e for e in anticheat.read(self.cfg)["types"]}
        self.assertFalse(types[3]["enabled"])

    def test_documented_types_are_flagged_for_mods(self):
        types = {e["number"]: e for e in anticheat.read(self.cfg)["types"]}
        self.assertTrue(types[12]["mod_friendly"])
        self.assertTrue(types[21]["mod_friendly"])
        self.assertFalse(types[1]["mod_friendly"])

    def test_undocumented_types_have_no_invented_description(self):
        types = {e["number"]: e for e in anticheat.read(self.cfg)["types"]}
        for number in (1, 5, 13, 24):
            self.assertEqual(types[number]["description"], "", number)


class WriteTests(AntiCheatTestCase):
    def test_disables_a_type(self):
        result = anticheat.write(self.cfg, {12: False})
        self.assertTrue(result["ok"])
        self.assertEqual(pzini.read(self.cfg.ini_path)["AntiCheatProtectionType12"], "false")

    def test_enables_a_type(self):
        anticheat.write(self.cfg, {12: False})
        anticheat.write(self.cfg, {12: True})
        self.assertEqual(pzini.read(self.cfg.ini_path)["AntiCheatProtectionType12"], "true")

    def test_accepts_string_keys_from_json(self):
        self.assertTrue(anticheat.write(self.cfg, {"12": False})["ok"])
        self.assertEqual(pzini.read(self.cfg.ini_path)["AntiCheatProtectionType12"], "false")

    def test_bulk_disable(self):
        anticheat.write(self.cfg, {n: False for n in range(1, 25)})
        self.assertEqual(anticheat.read(self.cfg)["enabled_count"], 0)

    def test_round_trips(self):
        anticheat.write(self.cfg, {n: (n % 2 == 0) for n in range(1, 25)})
        types = {e["number"]: e["enabled"] for e in anticheat.read(self.cfg)["types"]}
        self.assertTrue(types[2])
        self.assertFalse(types[3])

    def test_rejects_out_of_range(self):
        for bad in (0, 25, -1, 100):
            result = anticheat.write(self.cfg, {bad: False})
            self.assertFalse(result["ok"], bad)

    def test_rejects_non_numeric(self):
        result = anticheat.write(self.cfg, {"NotAType": False})
        self.assertFalse(result["ok"])

    def test_a_rejected_write_changes_nothing(self):
        before = self.cfg.ini_path.read_text(encoding="utf-8", newline="")
        anticheat.write(self.cfg, {99: False})
        self.assertEqual(self.cfg.ini_path.read_text(encoding="utf-8", newline=""), before)

    def test_other_settings_untouched(self):
        anticheat.write(self.cfg, {12: False})
        self.assertEqual(pzini.read(self.cfg.ini_path)["PVP"], "true")
        self.assertIn("# server", self.cfg.ini_path.read_text(encoding="utf-8"))

    def test_no_bogus_keys_are_written(self):
        """The named options from the original issue do not exist."""
        anticheat.write(self.cfg, {12: False})
        keys = pzini.read(self.cfg.ini_path)
        for ghost in ("AntiCheatSafety", "AntiCheatSpeed", "AntiCheatNoClip"):
            self.assertNotIn(ghost, keys)

    def test_empty_change_is_a_no_op(self):
        result = anticheat.write(self.cfg, {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], [])

    def test_missing_ini(self):
        self.cfg.ini_path.unlink()
        self.assertFalse(anticheat.write(self.cfg, {12: False})["ok"])


if __name__ == "__main__":
    unittest.main()
