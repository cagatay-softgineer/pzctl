"""Tests for the line-preserving server .ini reader/writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import pzini

# A CRLF sample, matching what the dedicated server actually writes on Windows.
SAMPLE = (
    "# Server configuration\r\n"
    "\r\n"
    "PVP=true\r\n"
    "PauseEmpty=true\r\n"
    "# a comment about the next key\r\n"
    "MaxPlayers=32\r\n"
    "ServerWelcomeMessage=Welcome to the server\r\n"
    "Mods=modA;modB;modC\r\n"
    "RCONPassword=\r\n"
)


class TempIniTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "servertest.ini"
        self.path.write_text(SAMPLE, encoding="utf-8", newline="")

    def raw(self) -> str:
        return self.path.read_text(encoding="utf-8", newline="")


class ReadTests(TempIniTest):
    def test_reads_key_values(self):
        data = pzini.read(self.path)
        self.assertEqual(data["PVP"], "true")
        self.assertEqual(data["MaxPlayers"], "32")
        self.assertEqual(data["ServerWelcomeMessage"], "Welcome to the server")

    def test_skips_comments_and_blanks(self):
        data = pzini.read(self.path)
        self.assertNotIn("#", "".join(data.keys()))
        self.assertEqual(len(data), 6)

    def test_empty_value_is_empty_string(self):
        self.assertEqual(pzini.read(self.path)["RCONPassword"], "")

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(pzini.read(self.dir / "nope.ini"), {})


class WriteTests(TempIniTest):
    def test_returns_only_keys_actually_changed(self):
        touched = pzini.write(self.path, {"PVP": "false", "PauseEmpty": "true"})
        # PauseEmpty was already "true", so it is not reported as modified.
        self.assertEqual(touched, ["PVP"])

    def test_value_is_updated(self):
        pzini.write(self.path, {"MaxPlayers": "64"})
        self.assertEqual(pzini.read(self.path)["MaxPlayers"], "64")

    def test_unrelated_lines_survive_byte_for_byte(self):
        before = self.raw().split("\r\n")
        pzini.write(self.path, {"MaxPlayers": "64"})
        after = self.raw().split("\r\n")

        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(changed, [5], "only the MaxPlayers line should differ")
        self.assertEqual(after[5], "MaxPlayers=64")

    def test_comments_and_blank_lines_preserved(self):
        pzini.write(self.path, {"PVP": "false"})
        text = self.raw()
        self.assertIn("# Server configuration", text)
        self.assertIn("# a comment about the next key", text)
        self.assertIn("\r\n\r\n", text)

    def test_unknown_keys_are_appended(self):
        touched = pzini.write(self.path, {"BrandNewKey": "1"})
        self.assertEqual(touched, ["BrandNewKey"])
        self.assertEqual(pzini.read(self.path)["BrandNewKey"], "1")
        # Appending must not disturb what was already there.
        self.assertEqual(pzini.read(self.path)["PVP"], "true")

    def test_no_op_write_leaves_content_identical(self):
        before = self.raw()
        touched = pzini.write(self.path, {"PVP": "true"})
        self.assertEqual(touched, [])
        self.assertEqual(self.raw(), before)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            pzini.write(self.dir / "nope.ini", {"PVP": "false"})

    def test_no_tmp_file_left_behind(self):
        pzini.write(self.path, {"PVP": "false"})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_value_containing_equals_is_preserved(self):
        pzini.write(self.path, {"ServerWelcomeMessage": "a=b=c"})
        self.assertEqual(pzini.read(self.path)["ServerWelcomeMessage"], "a=b=c")


class NewlineTests(TempIniTest):
    def test_crlf_input_stays_crlf(self):
        pzini.write(self.path, {"PVP": "false"})
        text = self.raw()
        self.assertIn("\r\n", text)
        # Every newline must be part of a CRLF pair - no bare LF anywhere.
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_lf_input_is_normalised_to_crlf(self):
        """Documents current behaviour: writes are always CRLF.

        A LF-only file is rewritten wholesale rather than line-by-line. That is
        harmless on Windows, where the server writes CRLF anyway, but it means
        the byte-for-byte preservation guarantee does not hold for LF input.
        Relevant to the Linux support work.
        """
        lf_path = self.dir / "lf.ini"
        lf_path.write_text("PVP=true\nMaxPlayers=32\n", encoding="utf-8", newline="")
        pzini.write(lf_path, {"PVP": "false"})
        text = lf_path.read_text(encoding="utf-8", newline="")
        self.assertEqual(text, "PVP=false\r\nMaxPlayers=32\r\n")


class ListHelperTests(unittest.TestCase):
    def test_split_list(self):
        self.assertEqual(pzini.split_list("a;b;c"), ["a", "b", "c"])

    def test_split_list_trims_and_drops_empties(self):
        self.assertEqual(pzini.split_list(" a ; ; b ;"), ["a", "b"])

    def test_split_empty_string(self):
        self.assertEqual(pzini.split_list(""), [])

    def test_join_list(self):
        self.assertEqual(pzini.join_list(["a", "b"]), "a;b")

    def test_join_list_drops_empties(self):
        self.assertEqual(pzini.join_list([" a ", "", "b"]), "a;b")

    def test_round_trip(self):
        items = ["Mod_A", "Mod_B", "Mod_C"]
        self.assertEqual(pzini.split_list(pzini.join_list(items)), items)


if __name__ == "__main__":
    unittest.main()
