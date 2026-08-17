"""Tests for read-only access to the game's own log files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import logs
from pzctl.config import Config


class LogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.logs_dir = logs.log_dir(self.cfg)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def write_log(self, name: str, text: str, where: Path | None = None) -> Path:
        path = (where or self.logs_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the bytes on disk are exactly what the test asked for;
        # otherwise Windows would translate \n to \r\n and the tail assertions
        # would be comparing against something the test never wrote.
        path.write_text(text, encoding="utf-8", newline="")
        return path


class DiscoverTests(LogTestCase):
    def test_empty_when_no_logs(self):
        self.assertEqual(logs.discover(self.cfg), [])

    def test_missing_directory_is_not_an_error(self):
        cfg = Config(self.dir / "other.json")
        cfg.set("zomboid_dir", str(self.dir / "nope"))
        self.assertEqual(logs.discover(cfg), [])

    def test_finds_logs(self):
        self.write_log("25-08-17_12-00-00_DebugLog-server.txt", "boot")
        self.write_log("25-08-17_12-00-00_PerkLog.txt", "perks")
        names = [entry["name"] for entry in logs.discover(self.cfg)]
        self.assertEqual(len(names), 2)

    def test_finds_server_console_in_zomboid_root(self):
        """Some versions write server-console.txt beside Logs/, not inside it."""
        self.write_log("server-console.txt", "boot errors", where=self.cfg.zomboid_dir)
        names = [entry["name"] for entry in logs.discover(self.cfg)]
        self.assertIn("server-console.txt", names)

    def test_classifies_timestamped_names(self):
        """PZ rotates logs with a session prefix, so matching is on the suffix."""
        cases = {
            "25-08-17_12-00-00_DebugLog-server.txt": "debug",
            "25-08-17_12-00-00_PerkLog.txt": "perks",
            "25-08-17_12-00-00_ClientActionLogs.txt": "client actions",
            "25-08-17_12-00-00_chat.txt": "chat",
            "server-console.txt": "console",
        }
        for name in cases:
            self.write_log(name, "x")
        found = {entry["name"]: entry["kind"] for entry in logs.discover(self.cfg)}
        for name, kind in cases.items():
            self.assertEqual(found[name], kind, name)

    def test_unrecognised_logs_still_listed(self):
        """An unknown log must not vanish - it lands in 'other'."""
        self.write_log("something-new.txt", "x")
        found = {entry["name"]: entry["kind"] for entry in logs.discover(self.cfg)}
        self.assertEqual(found["something-new.txt"], "other")

    def test_non_log_files_ignored(self):
        self.write_log("notes.md", "x")
        self.write_log("archive.zip", "x")
        self.assertEqual(logs.discover(self.cfg), [])

    def test_directories_ignored(self):
        (self.logs_dir / "subdir.txt").mkdir()
        self.assertEqual(logs.discover(self.cfg), [])

    def test_newest_first(self):
        import os

        old = self.write_log("old.txt", "x")
        new = self.write_log("new.txt", "x")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        names = [entry["name"] for entry in logs.discover(self.cfg)]
        self.assertEqual(names, ["new.txt", "old.txt"])

    def test_entry_shape(self):
        self.write_log("server-console.txt", "hello")
        entry = logs.discover(self.cfg)[0]
        self.assertEqual(set(entry), {"name", "kind", "size_kb", "mtime"})


class ResolveTests(LogTestCase):
    def test_resolves_a_listed_log(self):
        self.write_log("server-console.txt", "x")
        self.assertIsNotNone(logs.resolve(self.cfg, "server-console.txt"))

    def test_unknown_name(self):
        self.assertIsNone(logs.resolve(self.cfg, "nope.txt"))

    def test_empty_name(self):
        self.assertIsNone(logs.resolve(self.cfg, ""))

    def test_rejects_traversal(self):
        secret = self.dir / "secret.txt"
        secret.write_text("password", encoding="utf-8")
        for candidate in ("../secret.txt", "..\\secret.txt", "../../secret.txt"):
            self.assertIsNone(logs.resolve(self.cfg, candidate), candidate)

    def test_rejects_absolute_path(self):
        secret = self.dir / "secret.txt"
        secret.write_text("password", encoding="utf-8")
        self.assertIsNone(logs.resolve(self.cfg, str(secret)))

    def test_cannot_read_arbitrary_file_in_zomboid_dir(self):
        """Only Logs/ contents and server-console.txt are viewable."""
        self.write_log("pzctl.json", '{"secret": 1}', where=self.cfg.zomboid_dir)
        self.assertIsNone(logs.resolve(self.cfg, "pzctl.json"))


class TailTests(LogTestCase):
    def test_reads_short_file_whole(self):
        self.write_log("server-console.txt", "line one\nline two\n")
        result = logs.tail(self.cfg, "server-console.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "line one\nline two\n")
        self.assertFalse(result["truncated"])

    def test_unknown_log(self):
        result = logs.tail(self.cfg, "nope.txt")
        self.assertFalse(result["ok"])
        self.assertIn("no such log", result["error"])

    def test_truncates_large_file_to_the_end(self):
        body = "".join(f"line {i}\n" for i in range(20000))
        self.write_log("big.txt", body)

        result = logs.tail(self.cfg, "big.txt", max_bytes=2048)

        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode()), 2048)
        # The end of the file is what matters, so the last line must be present.
        self.assertIn("line 19999", result["text"])
        self.assertNotIn("line 0\n", result["text"])

    def test_partial_first_line_is_dropped(self):
        self.write_log("big.txt", "AAAAAAAAAA\n" * 500)
        result = logs.tail(self.cfg, "big.txt", max_bytes=1024)
        # Every retained line must be whole.
        for line in result["text"].splitlines():
            self.assertEqual(line, "AAAAAAAAAA")

    def test_reports_full_size_not_returned_size(self):
        body = "x" * 50000
        self.write_log("big.txt", body)
        result = logs.tail(self.cfg, "big.txt", max_bytes=1024)
        self.assertAlmostEqual(result["size_kb"], round(50000 / 1024, 1))

    def test_byte_limit_is_capped(self):
        self.write_log("server-console.txt", "hello\n")
        result = logs.tail(self.cfg, "server-console.txt", max_bytes=99_999_999)
        self.assertTrue(result["ok"])

    def test_byte_limit_has_a_floor(self):
        self.write_log("server-console.txt", "hello\n")
        result = logs.tail(self.cfg, "server-console.txt", max_bytes=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "hello\n")

    def test_empty_file(self):
        self.write_log("server-console.txt", "")
        result = logs.tail(self.cfg, "server-console.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "")

    def test_invalid_utf8_does_not_raise(self):
        path = self.logs_dir / "binary.txt"
        path.write_bytes(b"valid\n\xff\xfe broken bytes\n")
        result = logs.tail(self.cfg, "binary.txt")
        self.assertTrue(result["ok"])
        self.assertIn("valid", result["text"])

    def test_traversal_rejected(self):
        (self.dir / "secret.txt").write_text("password", encoding="utf-8")
        result = logs.tail(self.cfg, "../secret.txt")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
