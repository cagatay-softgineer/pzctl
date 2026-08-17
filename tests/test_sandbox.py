"""Tests for the line-preserving SandboxVars.lua reader/writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import sandbox

# Mirrors the shape the server writes: one nesting level of sub-tables.
SAMPLE = """SandboxVars = {
    VERSION = 5,
    Zombies = 3,
    Distribution = 1,
    DayLength = 3,
    -- a comment inside the table
    PillsEffect = 3,
    ZombieLore = {
        Speed = 2,
        Strength = 2,
        Toughness = 2,
        Cognition = 3,
    },
    Map = {
        AllowMiniMap = false,
        AllowWorldMap = true,
    },
    Multiplier = 1.5,
    ServerName = "my server",
}
"""


class TempSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "servertest_SandboxVars.lua"
        self.path.write_text(SAMPLE, encoding="utf-8", newline="")

    def raw(self) -> str:
        return self.path.read_text(encoding="utf-8", newline="")


class ParseTests(TempSandboxTest):
    def test_top_level_scalars(self):
        data = sandbox.to_dict(self.path)
        self.assertEqual(data["Zombies"], 3)
        self.assertEqual(data["VERSION"], 5)

    def test_nested_keys_are_dotted(self):
        data = sandbox.to_dict(self.path)
        self.assertEqual(data["ZombieLore.Speed"], 2)
        self.assertEqual(data["ZombieLore.Cognition"], 3)
        self.assertEqual(data["Map.AllowMiniMap"], False)

    def test_sandboxvars_wrapper_is_stripped(self):
        data = sandbox.to_dict(self.path)
        self.assertNotIn("SandboxVars.Zombies", data)
        self.assertIn("Zombies", data)

    def test_scalar_types(self):
        data = sandbox.to_dict(self.path)
        self.assertIsInstance(data["Zombies"], int)
        self.assertIsInstance(data["Multiplier"], float)
        self.assertIsInstance(data["Map.AllowWorldMap"], bool)
        self.assertEqual(data["ServerName"], "my server")

    def test_booleans_are_bools_not_strings(self):
        data = sandbox.to_dict(self.path)
        self.assertIs(data["Map.AllowWorldMap"], True)
        self.assertIs(data["Map.AllowMiniMap"], False)

    def test_comments_are_ignored(self):
        self.assertNotIn("-- a comment inside the table", sandbox.to_dict(self.path))

    def test_missing_file_returns_empty(self):
        self.assertEqual(sandbox.parse(self.dir / "nope.lua"), [])
        self.assertEqual(sandbox.to_dict(self.dir / "nope.lua"), {})

    def test_bare_return_table_has_no_wrapper(self):
        """The shipped presets use `return { ... }` rather than `SandboxVars = { ... }`."""
        preset = self.dir / "preset.lua"
        preset.write_text(
            "return {\n    Zombies = 1,\n    ZombieLore = {\n        Speed = 1,\n    },\n}\n",
            encoding="utf-8",
            newline="",
        )
        data = sandbox.to_dict(preset)
        self.assertEqual(data["Zombies"], 1)
        self.assertEqual(data["ZombieLore.Speed"], 1)


class WriteTests(TempSandboxTest):
    def test_updates_scalar(self):
        touched = sandbox.write(self.path, {"Zombies": 4})
        self.assertEqual(touched, ["Zombies"])
        self.assertEqual(sandbox.to_dict(self.path)["Zombies"], 4)

    def test_updates_nested_scalar(self):
        sandbox.write(self.path, {"ZombieLore.Speed": 1})
        self.assertEqual(sandbox.to_dict(self.path)["ZombieLore.Speed"], 1)

    def test_unrelated_lines_survive_byte_for_byte(self):
        before = self.raw().split("\n")
        sandbox.write(self.path, {"ZombieLore.Speed": 1})
        after = self.raw().split("\n")

        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1, "exactly one line should differ")
        self.assertEqual(after[changed[0]].strip(), "Speed = 1,")

    def test_indentation_and_trailing_comma_preserved(self):
        sandbox.write(self.path, {"ZombieLore.Speed": 1})
        line = [ln for ln in self.raw().split("\n") if "Speed" in ln][0]
        self.assertTrue(line.startswith("        "), "indentation kept")
        self.assertTrue(line.rstrip().endswith(","), "trailing comma kept")

    def test_comment_inside_table_preserved(self):
        sandbox.write(self.path, {"Zombies": 4})
        self.assertIn("-- a comment inside the table", self.raw())

    def test_no_op_write_leaves_content_identical(self):
        before = self.raw()
        touched = sandbox.write(self.path, {"Zombies": 3})
        self.assertEqual(touched, [])
        self.assertEqual(self.raw(), before)

    def test_unknown_key_is_ignored(self):
        before = self.raw()
        touched = sandbox.write(self.path, {"NoSuchKey": 1, "Nested.Nope": 2})
        self.assertEqual(touched, [])
        self.assertEqual(self.raw(), before)

    def test_boolean_written_as_lua_literal(self):
        sandbox.write(self.path, {"Map.AllowMiniMap": True})
        line = [ln for ln in self.raw().split("\n") if "AllowMiniMap" in ln][0]
        self.assertIn("= true", line)
        self.assertNotIn("True", line)

    def test_string_boolean_coerced(self):
        sandbox.write(self.path, {"Map.AllowMiniMap": "true"})
        self.assertIs(sandbox.to_dict(self.path)["Map.AllowMiniMap"], True)

    def test_string_value_is_quoted(self):
        sandbox.write(self.path, {"ServerName": "renamed"})
        self.assertIn('ServerName = "renamed"', self.raw())
        self.assertEqual(sandbox.to_dict(self.path)["ServerName"], "renamed")

    def test_non_numeric_into_numeric_slot_raises(self):
        with self.assertRaises(ValueError):
            sandbox.write(self.path, {"Zombies": "not-a-number"})

    def test_float_value(self):
        sandbox.write(self.path, {"Multiplier": 2.25})
        self.assertEqual(sandbox.to_dict(self.path)["Multiplier"], 2.25)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            sandbox.write(self.dir / "nope.lua", {"Zombies": 1})

    def test_no_tmp_file_left_behind(self):
        sandbox.write(self.path, {"Zombies": 4})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_multiple_changes_at_once(self):
        touched = sandbox.write(
            self.path, {"Zombies": 4, "ZombieLore.Speed": 1, "Map.AllowWorldMap": False}
        )
        self.assertEqual(set(touched), {"Zombies", "ZombieLore.Speed", "Map.AllowWorldMap"})
        data = sandbox.to_dict(self.path)
        self.assertEqual(data["Zombies"], 4)
        self.assertEqual(data["ZombieLore.Speed"], 1)
        self.assertIs(data["Map.AllowWorldMap"], False)


class NewlineTests(TempSandboxTest):
    def test_lf_input_stays_lf(self):
        sandbox.write(self.path, {"Zombies": 4})
        self.assertNotIn("\r\n", self.raw())

    def test_crlf_input_stays_crlf(self):
        crlf = self.dir / "crlf.lua"
        crlf.write_text(SAMPLE.replace("\n", "\r\n"), encoding="utf-8", newline="")
        sandbox.write(crlf, {"Zombies": 4})
        text = crlf.read_text(encoding="utf-8", newline="")
        self.assertNotIn("\n", text.replace("\r\n", ""))


if __name__ == "__main__":
    unittest.main()
