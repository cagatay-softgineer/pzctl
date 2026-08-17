"""Tests for named server profiles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import profiles
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self, alive: bool = False):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class ProfileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)

    def make_profile(self, name: str, full: bool = True, save: bool = False) -> None:
        directory = self.cfg.server_config_dir
        (directory / f"{name}.ini").write_text(f"# {name}\r\nPVP=true\r\n", encoding="utf-8", newline="")
        if full:
            (directory / f"{name}_SandboxVars.lua").write_text("SandboxVars = {\n}\n", encoding="utf-8")
            (directory / f"{name}_spawnpoints.lua").write_text("-- points\n", encoding="utf-8")
            (directory / f"{name}_spawnregions.lua").write_text("-- regions\n", encoding="utf-8")
        if save:
            (self.cfg.zomboid_dir / "Saves" / "Multiplayer" / name).mkdir(parents=True, exist_ok=True)


class NameValidationTests(unittest.TestCase):
    def test_accepts_reasonable_names(self):
        for name in ["servertest", "My Server", "test-2", "prod_world", "a"]:
            self.assertIsNone(profiles.validate_name(name), name)

    def test_rejects_empty(self):
        self.assertIsNotNone(profiles.validate_name(""))
        self.assertIsNotNone(profiles.validate_name("   "))

    def test_rejects_path_separators(self):
        """A profile name becomes a filename."""
        for name in ["../evil", "a/b", "a\\b", "..", "/etc/passwd"]:
            self.assertIsNotNone(profiles.validate_name(name), name)

    def test_rejects_punctuation_that_would_confuse_a_filename(self):
        for name in ["a.b", "a*b", 'a"b', "a:b", "a|b"]:
            self.assertIsNotNone(profiles.validate_name(name), name)

    def test_rejects_absurdly_long(self):
        self.assertIsNotNone(profiles.validate_name("x" * 200))


class DiscoverTests(ProfileTestCase):
    def test_current_profile_listed_even_with_no_files(self):
        """A configured profile exists conceptually before its first boot."""
        result = profiles.discover(self.cfg)
        names = [p["name"] for p in result["profiles"]]
        self.assertEqual(names, ["servertest"])
        self.assertFalse(result["profiles"][0]["has_config"])
        self.assertTrue(result["profiles"][0]["current"])

    def test_finds_profiles_on_disk(self):
        self.make_profile("servertest")
        self.make_profile("testworld")
        names = sorted(p["name"] for p in profiles.discover(self.cfg)["profiles"])
        self.assertEqual(names, ["servertest", "testworld"])

    def test_marks_the_current_one(self):
        self.make_profile("servertest")
        self.make_profile("other")
        current = [p for p in profiles.discover(self.cfg)["profiles"] if p["current"]]
        self.assertEqual([p["name"] for p in current], ["servertest"])

    def test_reports_which_files_exist(self):
        self.make_profile("partial", full=False)
        entry = {p["name"]: p for p in profiles.discover(self.cfg)["profiles"]}["partial"]
        self.assertTrue(entry["files"]["ini"])
        self.assertFalse(entry["files"]["sandbox"])

    def test_reports_whether_a_world_exists(self):
        self.make_profile("played", save=True)
        self.make_profile("unplayed")
        entries = {p["name"]: p for p in profiles.discover(self.cfg)["profiles"]}
        self.assertTrue(entries["played"]["has_save"])
        self.assertFalse(entries["unplayed"]["has_save"])

    def test_missing_server_dir_is_not_an_error(self):
        cfg = Config(self.dir / "other.json")
        cfg.set("zomboid_dir", str(self.dir / "nowhere"))
        cfg.set("server_name", "servertest")
        self.assertTrue(profiles.discover(cfg)["ok"])


class SwitchTests(ProfileTestCase):
    def test_switching_changes_the_configured_name(self):
        self.make_profile("testworld")
        result = profiles.switch(self.cfg, FakeSupervisor(), "testworld")
        self.assertTrue(result["ok"])
        self.assertEqual(self.cfg.get("server_name"), "testworld")

    def test_switching_moves_no_files(self):
        """Every path derives from server_name, so nothing needs moving."""
        self.make_profile("servertest")
        self.make_profile("testworld")
        before = sorted(p.name for p in self.cfg.server_config_dir.iterdir())
        profiles.switch(self.cfg, FakeSupervisor(), "testworld")
        after = sorted(p.name for p in self.cfg.server_config_dir.iterdir())
        self.assertEqual(before, after)

    def test_derived_paths_follow_the_switch(self):
        self.make_profile("testworld")
        profiles.switch(self.cfg, FakeSupervisor(), "testworld")
        self.assertEqual(self.cfg.ini_path.name, "testworld.ini")
        self.assertEqual(self.cfg.sandbox_path.name, "testworld_SandboxVars.lua")
        self.assertTrue(str(self.cfg.save_dir).endswith("testworld"))

    def test_switch_persists(self):
        self.make_profile("testworld")
        profiles.switch(self.cfg, FakeSupervisor(), "testworld")
        self.assertEqual(Config(self.cfg.path).get("server_name"), "testworld")

    def test_refuses_while_the_server_is_running(self):
        self.make_profile("testworld")
        result = profiles.switch(self.cfg, FakeSupervisor(alive=True), "testworld")
        self.assertFalse(result["ok"])
        self.assertEqual(self.cfg.get("server_name"), "servertest")

    def test_switching_to_a_new_name_says_it_will_be_created(self):
        """A typo here looks like a lost world unless it is spelled out."""
        result = profiles.switch(self.cfg, FakeSupervisor(), "brandnew")
        self.assertTrue(result["ok"])
        self.assertFalse(result["existed"])
        self.assertIn("no config yet", result["note"])

    def test_switching_to_an_existing_profile_has_no_warning(self):
        self.make_profile("testworld")
        result = profiles.switch(self.cfg, FakeSupervisor(), "testworld")
        self.assertTrue(result["existed"])
        self.assertEqual(result["note"], "")

    def test_rejects_a_bad_name(self):
        result = profiles.switch(self.cfg, FakeSupervisor(), "../evil")
        self.assertFalse(result["ok"])
        self.assertEqual(self.cfg.get("server_name"), "servertest")


class CreateTests(ProfileTestCase):
    def test_creating_without_a_source_writes_nothing(self):
        result = profiles.create(self.cfg, "fresh")
        self.assertTrue(result["ok"])
        self.assertEqual(result["copied"], [])
        self.assertIn("default settings", result["note"])

    def test_copying_duplicates_the_whole_config_set(self):
        self.make_profile("servertest")
        result = profiles.create(self.cfg, "copy1", copy_from="servertest")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(
            sorted(result["copied"]),
            sorted(
                [
                    "copy1.ini",
                    "copy1_SandboxVars.lua",
                    "copy1_spawnpoints.lua",
                    "copy1_spawnregions.lua",
                ]
            ),
        )

    def test_copied_settings_match_the_source(self):
        self.make_profile("servertest")
        profiles.create(self.cfg, "copy1", copy_from="servertest")
        source = (self.cfg.server_config_dir / "servertest.ini").read_text(encoding="utf-8")
        copy = (self.cfg.server_config_dir / "copy1.ini").read_text(encoding="utf-8")
        self.assertEqual(source, copy)

    def test_copying_a_partial_set_copies_what_exists(self):
        self.make_profile("partial", full=False)
        result = profiles.create(self.cfg, "copy2", copy_from="partial")
        self.assertEqual(result["copied"], ["copy2.ini"])

    def test_the_world_is_not_copied(self):
        """Duplicating a save is slow and rarely wanted from a button."""
        self.make_profile("servertest", save=True)
        profiles.create(self.cfg, "copy1", copy_from="servertest")
        saves = self.cfg.zomboid_dir / "Saves" / "Multiplayer"
        self.assertFalse((saves / "copy1").exists())

    def test_refuses_to_overwrite_an_existing_profile(self):
        self.make_profile("servertest")
        result = profiles.create(self.cfg, "servertest")
        self.assertFalse(result["ok"])
        self.assertIn("already exists", result["error"])

    def test_unknown_source(self):
        result = profiles.create(self.cfg, "copy1", copy_from="nosuch")
        self.assertFalse(result["ok"])
        self.assertIn("no profile named", result["error"])

    def test_rejects_a_bad_new_name(self):
        self.assertFalse(profiles.create(self.cfg, "../evil")["ok"])

    def test_rejects_a_bad_source_name(self):
        self.assertFalse(profiles.create(self.cfg, "fine", copy_from="../evil")["ok"])


if __name__ == "__main__":
    unittest.main()
