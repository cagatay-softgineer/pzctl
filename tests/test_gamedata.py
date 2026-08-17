"""Tests for catalogs parsed from the installed game's data files.

Fixtures stand in for a game install: CI has no Project Zomboid, and a test
that needed one would only ever run on a machine that happens to have it.
The fixtures mirror the real B42 layout that this module was written against.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import gamedata

# The real layout: scripts nested under entities/, names in ItemName.json.
SCRIPT = """module Base
{
    item Axe
    {
        Type = Weapon,
        DisplayName = Axe,
        Icon = Axe,
    }

    item OldAxeHead
    {
        Type = Normal,
        DisplayName = Axe Head,
    }
}
"""

MOD_SCRIPT = """module CoolMod
{
    item SuperHammer
    {
        Type = Weapon,
        DisplayName = Super Hammer,
    }
}
"""

ITEM_NAMES = """{
    "Base.Axe": "Firefighter Axe",
    "Base.OldAxeHead": "Axe Head"
}
"""

IG_UI = """{
    "IGUI_perks_Blunt": "Long Blunt",
    "IGUI_perks_Woodwork": "Carpentry",
    "IGUI_perks_Carpentry": "Carpentry",
    "IGUI_perks_Doctor": "First Aid",
    "IGUI_perks_Sprinting": "Running",
    "IGUI_perks_Aiming": "Aiming",
    "IGUI_perks_Carpentry_Description": "Allows the building of wooden constructions."
}
"""


class GameDataTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        self.scripts = self.dir / "media" / "scripts"
        (self.scripts / "entities").mkdir(parents=True)
        (self.scripts / "entities" / "weapons.txt").write_text(SCRIPT, encoding="utf-8")

        self.translate = self.dir / "media" / "lua" / "shared" / "Translate" / "EN"
        self.translate.mkdir(parents=True)
        (self.translate / "ItemName.json").write_text(ITEM_NAMES, encoding="utf-8")
        (self.translate / "IG_UI.json").write_text(IG_UI, encoding="utf-8")

        self._orig = (gamedata.SCRIPTS_DIR, gamedata.TRANSLATE_DIR)
        gamedata.SCRIPTS_DIR = self.scripts
        gamedata.TRANSLATE_DIR = self.translate
        gamedata.reset_cache()

        def restore():
            gamedata.SCRIPTS_DIR, gamedata.TRANSLATE_DIR = self._orig
            gamedata.reset_cache()

        self.addCleanup(restore)


class PerkTests(GameDataTestCase):
    def test_reads_the_perks(self):
        result = gamedata.perks()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 6)

    def test_description_keys_are_not_perks(self):
        """IGUI_perks_X_Description describes a perk; it is not one."""
        ids = [p["id"] for p in gamedata.perks()["perks"]]
        self.assertNotIn("Carpentry_Description", ids)

    def test_description_is_attached_to_its_perk(self):
        perks = {p["id"]: p for p in gamedata.perks()["perks"]}
        self.assertIn("wooden constructions", perks["Carpentry"]["description"])

    def test_ids_that_differ_from_labels_are_flagged(self):
        """This divergence is the whole reason a picker is needed."""
        perks = {p["id"]: p for p in gamedata.perks()["perks"]}
        self.assertEqual(perks["Blunt"]["name"], "Long Blunt")
        self.assertTrue(perks["Blunt"]["differs"])
        self.assertEqual(perks["Sprinting"]["name"], "Running")
        self.assertEqual(perks["Doctor"]["name"], "First Aid")

    def test_matching_id_and_label_not_flagged(self):
        perks = {p["id"]: p for p in gamedata.perks()["perks"]}
        self.assertFalse(perks["Aiming"]["differs"])

    def test_duplicate_labels_are_both_kept(self):
        """B42 ships Woodwork and Carpentry, both labelled Carpentry."""
        ids = [p["id"] for p in gamedata.perks()["perks"]]
        self.assertIn("Woodwork", ids)
        self.assertIn("Carpentry", ids)

    def test_missing_translations(self):
        (self.translate / "IG_UI.json").unlink()
        result = gamedata.perks()
        self.assertFalse(result["ok"])
        self.assertIn("is pzctl inside the server directory", result["error"])


class ItemTests(GameDataTestCase):
    def test_parses_items(self):
        result = gamedata.items()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 2)

    def test_ids_are_module_qualified(self):
        ids = [e["id"] for e in gamedata.items()["items"]]
        self.assertIn("Base.Axe", ids)

    def test_translated_name_wins_over_the_script(self):
        """The script says Axe; the game shows Firefighter Axe."""
        entry = {e["id"]: e for e in gamedata.items()["items"]}["Base.Axe"]
        self.assertEqual(entry["name"], "Firefighter Axe")

    def test_falls_back_to_the_script_name(self):
        """A modded item with no translation must still be usable."""
        (self.scripts / "entities" / "mod.txt").write_text(MOD_SCRIPT, encoding="utf-8")
        gamedata.reset_cache()
        entry = {e["id"]: e for e in gamedata.items(search="hammer")["items"]}
        self.assertEqual(entry["CoolMod.SuperHammer"]["name"], "Super Hammer")

    def test_captures_type(self):
        entry = {e["id"]: e for e in gamedata.items()["items"]}["Base.Axe"]
        self.assertEqual(entry["type"], "Weapon")

    def test_search_matches_display_name(self):
        self.assertEqual(gamedata.items(search="firefighter")["total"], 1)

    def test_search_matches_id(self):
        self.assertEqual(gamedata.items(search="oldaxehead")["total"], 1)

    def test_search_is_case_insensitive(self):
        self.assertEqual(gamedata.items(search="FIREFIGHTER")["total"], 1)

    def test_pagination(self):
        page = gamedata.items(limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["total"], 2)
        second = gamedata.items(limit=1, offset=1)
        self.assertNotEqual(page["items"][0]["id"], second["items"][0]["id"])

    def test_limit_is_capped_and_floored(self):
        self.assertTrue(gamedata.items(limit=99999)["ok"])
        self.assertTrue(gamedata.items(limit=0)["ok"])

    def test_negative_offset_treated_as_zero(self):
        self.assertEqual(gamedata.items(offset=-5)["offset"], 0)

    def test_missing_scripts_directory(self):
        gamedata.SCRIPTS_DIR = self.dir / "nowhere"
        result = gamedata.items()
        self.assertFalse(result["ok"])
        self.assertIn("is pzctl inside the server directory", result["error"])

    def test_cache_rebuilds_when_scripts_change(self):
        self.assertEqual(gamedata.items()["total"], 2)
        (self.scripts / "entities" / "mod.txt").write_text(MOD_SCRIPT, encoding="utf-8")
        # A new file changes the newest mtime, which invalidates the cache.
        self.assertEqual(gamedata.items()["total"], 3)

    def test_no_icons_are_offered(self):
        """A dedicated server ships no textures, so promising icons would lie."""
        entry = gamedata.items()["items"][0]
        self.assertNotIn("icon", entry)


if __name__ == "__main__":
    unittest.main()


VEHICLE_SCRIPT = """module Base
{
    vehicle CarLightsPolice
    {
        mechanicType = 1,
    }

    vehicle CarNormalBurnt
    {
        mechanicType = 1,
    }
}
"""

VEHICLE_NAMES = """{
    "IGUI_VehicleNameCarLightsPolice": "Police Chevalier Nyala"
}
"""


class VehicleTests(GameDataTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.scripts / "entities" / "vehicles.txt").write_text(VEHICLE_SCRIPT, encoding="utf-8")
        # Vehicle names live alongside the perk keys in IG_UI.json.
        merged = IG_UI.rstrip().rstrip("}").rstrip().rstrip(",")
        merged += ',\n    "IGUI_VehicleNameCarLightsPolice": "Police Chevalier Nyala"\n}\n'
        (self.translate / "IG_UI.json").write_text(merged, encoding="utf-8")
        gamedata.reset_cache()

    def test_finds_vehicles(self):
        result = gamedata.vehicles()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 2)

    def test_ids_are_module_qualified(self):
        ids = [v["id"] for v in gamedata.vehicles()["vehicles"]]
        self.assertIn("Base.CarLightsPolice", ids)

    def test_translated_name_used(self):
        entry = {v["id"]: v for v in gamedata.vehicles()["vehicles"]}["Base.CarLightsPolice"]
        self.assertEqual(entry["name"], "Police Chevalier Nyala")

    def test_untranslated_falls_back_to_script_name(self):
        """Burnt and smashed variants have no translation but are still usable."""
        entry = {v["id"]: v for v in gamedata.vehicles()["vehicles"]}["Base.CarNormalBurnt"]
        self.assertEqual(entry["name"], "CarNormalBurnt")

    def test_search_matches_name(self):
        self.assertEqual(gamedata.vehicles(search="police")["total"], 1)

    def test_search_matches_id(self):
        self.assertEqual(gamedata.vehicles(search="burnt")["total"], 1)

    def test_items_and_vehicles_are_separate(self):
        self.assertEqual(gamedata.items()["total"], 2)
        self.assertEqual(gamedata.vehicles()["total"], 2)

    def test_missing_scripts_directory(self):
        gamedata.SCRIPTS_DIR = self.dir / "nowhere"
        self.assertFalse(gamedata.vehicles()["ok"])
