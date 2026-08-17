"""Tests for crash diagnosis: what the log names, and what it must not invent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import crashdiag, logs
from pzctl.config import Config

# A stack trace the way the game annotates it.
MOD_TRACE = """ERROR: General f: ExceptionLogger.logException> Exception thrown
java.lang.NullPointerException
    at zombie.Lua.LuaManager.call(LuaManager.java:120)
    function: onFillInventoryObjectContextMenu -- file: BadMod.lua line # 42 | MOD: SuperCoolMod
"""

VANILLA_TRACE = """ERROR: General f: ExceptionLogger.logException> Exception thrown
java.lang.RuntimeException: something broke
    function: doStuff -- file: ISHandcraftAction.lua line # 354 | Vanilla
"""

WORKSHOP_PATH_TRACE = r"""ERROR: General f: loading failed
java.lang.RuntimeException: could not load
    at C:\steamapps\workshop\content\108600\2101234567\mods\FirearmsExpansion\media\lua\shared\Foo.lua
"""

CLEAN_LOG = """server starting
LOG  : General, 1234> Loading world
player connected
zombie spawned
"""


class CrashDiagTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.logs_dir = logs.log_dir(self.cfg)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def write_log(self, text: str, name: str = "25-08-17_12-00-00_DebugLog-server.txt") -> Path:
        path = self.logs_dir / name
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def write_ini(self, mods_line: str) -> None:
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text(
            f"Mods={mods_line}\r\nWorkshopItems=2101234567\r\n", encoding="utf-8", newline=""
        )


class NoLogsTests(CrashDiagTestCase):
    def test_reports_when_there_is_nothing_to_read(self):
        result = crashdiag.analyse(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("no game logs", result["error"])


class AttributionTests(CrashDiagTestCase):
    def test_finds_mod_named_by_the_game(self):
        self.write_log(MOD_TRACE)
        result = crashdiag.analyse(self.cfg)
        self.assertTrue(result["ok"])
        self.assertEqual([s["mod"] for s in result["suspects"]], ["SuperCoolMod"])

    def test_records_how_the_mod_was_named(self):
        self.write_log(MOD_TRACE)
        suspect = crashdiag.analyse(self.cfg)["suspects"][0]
        self.assertIn("named in a stack trace (| MOD:)", suspect["evidence"])

    def test_finds_mod_from_a_workshop_path(self):
        self.write_log(WORKSHOP_PATH_TRACE)
        suspects = crashdiag.analyse(self.cfg)["suspects"]
        self.assertEqual([s["mod"] for s in suspects], ["FirearmsExpansion"])
        self.assertEqual(suspects[0]["workshop_ids"], ["2101234567"])

    def test_vanilla_frames_are_not_blamed_on_a_mod(self):
        """`| Vanilla` marks base-game code; blaming a mod for it would mislead."""
        self.write_log(VANILLA_TRACE)
        result = crashdiag.analyse(self.cfg)
        self.assertEqual(result["suspects"], [])
        self.assertTrue(result["errors"], "the error itself should still be shown")

    def test_multiple_mods_are_all_reported(self):
        self.write_log(MOD_TRACE + "\n" + WORKSHOP_PATH_TRACE)
        names = {s["mod"] for s in crashdiag.analyse(self.cfg)["suspects"]}
        self.assertEqual(names, {"SuperCoolMod", "FirearmsExpansion"})

    def test_scans_every_log_file(self):
        self.write_log(CLEAN_LOG)
        self.write_log(MOD_TRACE, name="server-console.txt")
        result = crashdiag.analyse(self.cfg)
        self.assertEqual([s["mod"] for s in result["suspects"]], ["SuperCoolMod"])
        self.assertEqual(len(result["scanned"]), 2)


class NoGuessingTests(CrashDiagTestCase):
    def test_clean_log_names_nobody(self):
        self.write_log(CLEAN_LOG)
        result = crashdiag.analyse(self.cfg)
        self.assertEqual(result["suspects"], [])
        self.assertIn("will not guess", result["note"])

    def test_does_not_blame_a_configured_mod_the_log_never_mentions(self):
        """The whole point: a plausible but unevidenced accusation is worse than none."""
        self.write_ini("InnocentModA;InnocentModB;InnocentModC")
        self.write_log(CLEAN_LOG)
        result = crashdiag.analyse(self.cfg)
        self.assertEqual(result["suspects"], [])

    def test_error_without_attribution_still_shows_the_error(self):
        self.write_log("java.lang.OutOfMemoryError: Java heap space\n    at zombie.Foo\n")
        result = crashdiag.analyse(self.cfg)
        self.assertEqual(result["suspects"], [])
        self.assertTrue(result["errors"])
        self.assertIn("OutOfMemoryError", result["errors"][0]["text"])


class CrossReferenceTests(CrashDiagTestCase):
    def test_flags_a_named_mod_that_is_in_the_load_order(self):
        self.write_ini("SuperCoolMod;OtherMod")
        self.write_log(MOD_TRACE)
        suspect = crashdiag.analyse(self.cfg)["suspects"][0]
        self.assertTrue(suspect["in_load_order"])

    def test_flags_a_named_mod_that_is_not_loaded(self):
        """A stale path in an old log should not look like a live problem."""
        self.write_ini("SomethingElse")
        self.write_log(MOD_TRACE)
        suspect = crashdiag.analyse(self.cfg)["suspects"][0]
        self.assertFalse(suspect["in_load_order"])

    def test_loaded_mods_are_listed_first(self):
        self.write_ini("FirearmsExpansion")
        self.write_log(MOD_TRACE + "\n" + WORKSHOP_PATH_TRACE)
        suspects = crashdiag.analyse(self.cfg)["suspects"]
        self.assertTrue(suspects[0]["in_load_order"])
        self.assertEqual(suspects[0]["mod"], "FirearmsExpansion")

    def test_missing_ini_does_not_break_analysis(self):
        self.write_log(MOD_TRACE)
        result = crashdiag.analyse(self.cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["suspects"][0]["mod"], "SuperCoolMod")


class ExcerptTests(CrashDiagTestCase):
    def test_excerpt_includes_the_error_and_context(self):
        self.write_log(MOD_TRACE)
        excerpt = crashdiag.analyse(self.cfg)["errors"][0]
        self.assertIn("NullPointerException", excerpt["text"])
        self.assertEqual(excerpt["log"], "25-08-17_12-00-00_DebugLog-server.txt")

    def test_excerpt_count_is_capped(self):
        self.write_log("java.lang.Exception: boom\n" * 500)
        result = crashdiag.analyse(self.cfg)
        self.assertLessEqual(len(result["errors"]), crashdiag.MAX_EXCERPTS)

    def test_full_count_reported_even_when_capped(self):
        self.write_log("java.lang.Exception: boom\n" * 500)
        result = crashdiag.analyse(self.cfg)
        self.assertGreater(result["error_count"], len(result["errors"]))

    def test_only_the_tail_of_a_huge_log_is_read(self):
        body = "harmless line\n" * 100000 + MOD_TRACE
        self.write_log(body)
        result = crashdiag.analyse(self.cfg)
        self.assertEqual([s["mod"] for s in result["suspects"]], ["SuperCoolMod"])

    def test_invalid_utf8_does_not_raise(self):
        path = self.logs_dir / "binary.txt"
        path.write_bytes(b"\xff\xfe java.lang.Exception: boom\n")
        self.assertTrue(crashdiag.analyse(self.cfg)["ok"])


if __name__ == "__main__":
    unittest.main()
