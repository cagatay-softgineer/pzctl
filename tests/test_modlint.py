"""Tests for the mod configuration lint and the offline update signal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import modlint, mods
from pzctl.config import Config


class ModLintTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)

        self.workshop = self.dir / "workshop" / "content" / "108600"
        self.workshop.mkdir(parents=True)
        self._orig_root = mods.WORKSHOP_ROOT
        mods.WORKSHOP_ROOT = self.workshop
        self.addCleanup(lambda: setattr(mods, "WORKSHOP_ROOT", self._orig_root))

    def write_ini(self, mods_line="", workshop="", map_line="Muldraugh, KY"):
        self.cfg.ini_path.write_text(
            f"Mods={mods_line}\r\nWorkshopItems={workshop}\r\nMap={map_line}\r\n",
            encoding="utf-8",
            newline="",
        )

    def install(self, workshop_id: str, mod_id: str, maps=()) -> Path:
        mod_dir = self.workshop / workshop_id / "mods" / mod_id
        mod_dir.mkdir(parents=True)
        (mod_dir / "mod.info").write_text(f"id={mod_id}\nname={mod_id}\n", encoding="utf-8")
        for name in maps:
            (mod_dir / "media" / "maps" / name).mkdir(parents=True)
        return mod_dir


class LintTests(ModLintTestCase):
    def test_missing_ini(self):
        self.assertFalse(modlint.check(self.cfg)["ok"])

    def test_clean_configuration(self):
        self.install("111", "CoolMod")
        self.write_ini(mods_line="CoolMod", workshop="111")
        result = modlint.check(self.cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["problems"], [])

    def test_vanilla_map_is_not_flagged(self):
        self.write_ini()
        self.assertEqual(modlint.check(self.cfg)["problems"], [])

    def test_map_whose_mod_is_not_enabled_is_an_error(self):
        """This is the documented failure: the server will not start."""
        self.install("111", "BigMap", maps=["Riverside Expansion"])
        self.write_ini(mods_line="", workshop="111", map_line="Riverside Expansion;Muldraugh, KY")
        result = modlint.check(self.cfg)
        self.assertEqual(result["errors"], 1)
        self.assertIn("not in Mods=", result["problems"][0]["message"])
        self.assertIn("fail to start", result["problems"][0]["message"])

    def test_map_with_its_mod_enabled_is_fine(self):
        self.install("111", "BigMap", maps=["Riverside Expansion"])
        self.write_ini(mods_line="BigMap", workshop="111", map_line="Riverside Expansion;Muldraugh, KY")
        self.assertEqual(modlint.check(self.cfg)["errors"], 0)

    def test_map_no_mod_provides_is_a_warning(self):
        self.write_ini(map_line="Ghost Town;Muldraugh, KY")
        result = modlint.check(self.cfg)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["warnings"], 1)
        self.assertIn("no installed mod provides it", result["problems"][0]["message"])

    def test_enabled_mod_that_is_not_installed(self):
        self.write_ini(mods_line="NotThere")
        result = modlint.check(self.cfg)
        self.assertEqual(result["warnings"], 1)
        self.assertIn("not installed", result["problems"][0]["message"])

    def test_workshop_item_not_downloaded(self):
        self.write_ini(workshop="999")
        result = modlint.check(self.cfg)
        self.assertEqual(result["warnings"], 1)
        self.assertIn("not downloaded", result["problems"][0]["message"])

    def test_case_insensitive_mod_matching(self):
        self.install("111", "CoolMod")
        self.write_ini(mods_line="coolmod", workshop="111")
        self.assertEqual(modlint.check(self.cfg)["problems"], [])


class OfflineUpdateTests(ModLintTestCase):
    def test_no_baseline_yet(self):
        self.install("111", "CoolMod")
        result = modlint.updates(self.cfg)
        self.assertTrue(result["ok"])
        self.assertFalse(result["checked"])
        self.assertIn("no baseline", result["note"])

    def test_no_workshop_content(self):
        result = modlint.updates(self.cfg)
        self.assertFalse(result["checked"])

    def test_snapshot_then_nothing_changed(self):
        self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        result = modlint.updates(self.cfg)
        self.assertTrue(result["checked"])
        self.assertEqual(result["changed"], [])

    def test_detects_a_changed_file(self):
        mod_dir = self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        import os

        target = mod_dir / "mod.info"
        future = target.stat().st_mtime + 3600
        os.utime(target, (future, future))

        changed = modlint.updates(self.cfg)["changed"]
        self.assertEqual([c["workshop_id"] for c in changed], ["111"])
        self.assertEqual(changed[0]["reason"], "files changed")

    def test_detects_a_newly_installed_mod(self):
        self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        self.install("222", "OtherMod")
        changed = modlint.updates(self.cfg)["changed"]
        self.assertEqual([c["workshop_id"] for c in changed], ["222"])
        self.assertEqual(changed[0]["reason"], "newly installed")

    def test_changed_entries_carry_a_name(self):
        self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        self.install("222", "OtherMod")
        self.assertEqual(modlint.updates(self.cfg)["changed"][0]["name"], "OtherMod")

    def test_snapshot_persists(self):
        self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        self.assertTrue(Config(self.cfg.path).get("mods_seen"))

    def test_note_admits_the_signal_is_weak(self):
        """It cannot tell a Workshop update from any other disk change."""
        self.install("111", "CoolMod")
        modlint.snapshot(self.cfg)
        self.assertIn("cannot tell", modlint.updates(self.cfg)["note"])


if __name__ == "__main__":
    unittest.main()
