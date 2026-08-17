"""Tests for backup creation, retention pruning and listing."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from pzctl import backup
from pzctl.config import Config


class TempBackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.set("backup.dir", str(self.dir / "Backups"))

    def make_archive(self, name: str, mtime: float) -> Path:
        path = self.cfg.backup_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"zip")
        os.utime(path, (mtime, mtime))
        return path

    def populate_save(self) -> Path:
        save = self.cfg.save_dir
        (save / "sub").mkdir(parents=True, exist_ok=True)
        (save / "map_0_0.bin").write_text("chunk", encoding="utf-8")
        (save / "players.db").write_text("players", encoding="utf-8")
        (save / "sub" / "nested.bin").write_text("nested", encoding="utf-8")
        return save

    def populate_config_files(self) -> None:
        server_dir = self.cfg.server_config_dir
        server_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text("PVP=true\n", encoding="utf-8")
        self.cfg.sandbox_path.write_text("SandboxVars = {\n}\n", encoding="utf-8")


class PruneTests(TempBackupTest):
    def test_keeps_newest_and_removes_the_rest(self):
        self.cfg.set("backup.retention", 3)
        for i in range(6):
            self.make_archive(f"servertest-2026010{i}-000000.zip", 1_000_000 + i * 100)

        removed = backup.prune(self.cfg)

        remaining = sorted(p.name for p in self.cfg.backup_dir.glob("*.zip"))
        self.assertEqual(len(remaining), 3)
        self.assertEqual(len(removed), 3)
        # The three newest (highest mtime) survive.
        self.assertEqual(
            remaining,
            [
                "servertest-20260103-000000.zip",
                "servertest-20260104-000000.zip",
                "servertest-20260105-000000.zip",
            ],
        )

    def test_no_pruning_when_under_retention(self):
        self.cfg.set("backup.retention", 5)
        for i in range(3):
            self.make_archive(f"servertest-2026010{i}-000000.zip", 1_000_000 + i * 100)
        self.assertEqual(backup.prune(self.cfg), [])
        self.assertEqual(len(list(self.cfg.backup_dir.glob("*.zip"))), 3)

    def test_retention_zero_disables_pruning(self):
        self.cfg.set("backup.retention", 0)
        for i in range(4):
            self.make_archive(f"servertest-2026010{i}-000000.zip", 1_000_000 + i * 100)
        self.assertEqual(backup.prune(self.cfg), [])
        self.assertEqual(len(list(self.cfg.backup_dir.glob("*.zip"))), 4)

    def test_negative_retention_disables_pruning(self):
        self.cfg.set("backup.retention", -1)
        self.make_archive("servertest-20260101-000000.zip", 1_000_000)
        self.assertEqual(backup.prune(self.cfg), [])

    def test_missing_backup_dir_is_not_an_error(self):
        self.cfg.set("backup.retention", 3)
        self.assertEqual(backup.prune(self.cfg), [])

    def test_only_matching_server_name_is_pruned(self):
        """Pruning is scoped to this server's archives.

        `prune` globs `<server_name>-*.zip` while `listing` globs `*.zip`, so
        archives belonging to another server name are shown but never pruned.
        """
        self.cfg.set("backup.retention", 1)
        self.make_archive("servertest-20260101-000000.zip", 1_000_100)
        self.make_archive("servertest-20260102-000000.zip", 1_000_200)
        self.make_archive("otherserver-20260101-000000.zip", 1_000_000)

        removed = backup.prune(self.cfg)

        self.assertEqual(removed, ["servertest-20260101-000000.zip"])
        self.assertTrue((self.cfg.backup_dir / "otherserver-20260101-000000.zip").exists())


class ListingTests(TempBackupTest):
    def test_missing_dir_returns_empty(self):
        self.assertEqual(backup.listing(self.cfg), [])

    def test_newest_first(self):
        self.make_archive("servertest-20260101-000000.zip", 1_000_000)
        self.make_archive("servertest-20260102-000000.zip", 1_000_200)
        names = [entry["name"] for entry in backup.listing(self.cfg)]
        self.assertEqual(
            names, ["servertest-20260102-000000.zip", "servertest-20260101-000000.zip"]
        )

    def test_entry_shape(self):
        self.make_archive("servertest-20260101-000000.zip", 1_000_000)
        entry = backup.listing(self.cfg)[0]
        self.assertEqual(set(entry), {"name", "size_mb", "mtime"})

    def test_non_zip_files_ignored(self):
        self.make_archive("servertest-20260101-000000.zip", 1_000_000)
        (self.cfg.backup_dir / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(len(backup.listing(self.cfg)), 1)


class RunTests(TempBackupTest):
    def test_missing_save_dir_reports_error(self):
        result = backup.run(self.cfg)
        self.assertFalse(result["ok"])
        self.assertIn("no save folder", result["error"])

    def test_creates_archive_containing_the_save(self):
        self.populate_save()
        result = backup.run(self.cfg)

        self.assertTrue(result["ok"], result.get("error"))
        archive = Path(result["file"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertIn("Saves/map_0_0.bin", names)
        self.assertIn("Saves/players.db", names)
        self.assertIn("Saves/sub/nested.bin", names)

    def test_includes_config_when_enabled(self):
        self.populate_save()
        self.populate_config_files()
        self.cfg.set("backup.include_config", True)

        result = backup.run(self.cfg)

        with zipfile.ZipFile(result["file"]) as zf:
            names = zf.namelist()
        self.assertIn("Server/servertest.ini", names)
        self.assertIn("Server/servertest_SandboxVars.lua", names)

    def test_excludes_config_when_disabled(self):
        self.populate_save()
        self.populate_config_files()
        self.cfg.set("backup.include_config", False)

        result = backup.run(self.cfg)

        with zipfile.ZipFile(result["file"]) as zf:
            names = zf.namelist()
        self.assertFalse([n for n in names if n.startswith("Server/")])

    def test_archive_content_round_trips(self):
        self.populate_save()
        result = backup.run(self.cfg)
        with zipfile.ZipFile(result["file"]) as zf:
            self.assertEqual(zf.read("Saves/players.db").decode(), "players")

    def test_result_reports_file_count(self):
        self.populate_save()
        self.cfg.set("backup.include_config", False)
        self.assertEqual(backup.run(self.cfg)["files"], 3)

    def test_run_prunes_old_archives(self):
        self.populate_save()
        self.cfg.set("backup.retention", 1)
        self.make_archive("servertest-20200101-000000.zip", 1_000_000)

        result = backup.run(self.cfg)

        self.assertEqual(result["pruned"], ["servertest-20200101-000000.zip"])
        self.assertFalse((self.cfg.backup_dir / "servertest-20200101-000000.zip").exists())

    def test_archive_name_uses_server_name(self):
        self.populate_save()
        self.cfg.set("server_name", "myserver")
        (self.cfg.save_dir).mkdir(parents=True, exist_ok=True)
        (self.cfg.save_dir / "map.bin").write_text("x", encoding="utf-8")

        result = backup.run(self.cfg)

        self.assertTrue(result["name"].startswith("myserver-"))
        self.assertTrue(result["name"].endswith(".zip"))


if __name__ == "__main__":
    unittest.main()
