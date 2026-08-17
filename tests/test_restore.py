"""Tests for restoring a world from a backup archive."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from pzctl import backup
from pzctl.config import Config


class FakeSupervisor:
    """Minimal stand-in - restore only reads .state and calls .emit."""

    def __init__(self, state: str = "stopped"):
        self.state = state
        self.messages: list[str] = []

    def emit(self, text: str, stream: str = "pzctl") -> None:
        self.messages.append(text)


class RestoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.set("backup.dir", str(self.dir / "Backups"))
        self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)

    # -- fixtures ---------------------------------------------------------

    def write_world(self, marker: str) -> None:
        save = self.cfg.save_dir
        (save / "sub").mkdir(parents=True, exist_ok=True)
        (save / "map_0_0.bin").write_text(marker, encoding="utf-8")
        (save / "sub" / "nested.bin").write_text(marker, encoding="utf-8")

    def write_configs(self, marker: str) -> None:
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.ini_path.write_text(f"PVP={marker}\n", encoding="utf-8")
        self.cfg.sandbox_path.write_text(f"-- {marker}\n", encoding="utf-8")

    def make_backup(self, marker: str, include_config: bool = True) -> str:
        self.write_world(marker)
        if include_config:
            self.write_configs(marker)
        self.cfg.set("backup.include_config", include_config)
        result = backup.run(self.cfg)
        self.assertTrue(result["ok"], result.get("error"))
        return result["name"]

    def make_raw_zip(self, name: str, entries: dict[str, str]) -> str:
        path = self.cfg.backup_dir / name
        with zipfile.ZipFile(path, "w") as zf:
            for member, content in entries.items():
                zf.writestr(member, content)
        return name

    def world_marker(self) -> str:
        return (self.cfg.save_dir / "map_0_0.bin").read_text(encoding="utf-8")


class ResolveArchiveTests(RestoreTestCase):
    def test_resolves_a_real_archive(self):
        name = self.make_backup("v1")
        self.assertIsNotNone(backup.resolve_archive(self.cfg, name))

    def test_rejects_unknown_name(self):
        self.assertIsNone(backup.resolve_archive(self.cfg, "nope.zip"))

    def test_rejects_empty_name(self):
        self.assertIsNone(backup.resolve_archive(self.cfg, ""))

    def test_rejects_path_traversal(self):
        (self.dir / "outside.zip").write_bytes(b"x")
        for candidate in ("../outside.zip", "..\\outside.zip", "sub/../../outside.zip"):
            self.assertIsNone(
                backup.resolve_archive(self.cfg, candidate), f"accepted {candidate!r}"
            )

    def test_rejects_absolute_path(self):
        outside = self.dir / "outside.zip"
        outside.write_bytes(b"x")
        self.assertIsNone(backup.resolve_archive(self.cfg, str(outside)))

    def test_rejects_directory(self):
        (self.cfg.backup_dir / "adir.zip").mkdir()
        self.assertIsNone(backup.resolve_archive(self.cfg, "adir.zip"))


class InspectTests(RestoreTestCase):
    def test_describes_a_valid_archive(self):
        name = self.make_backup("v1")
        info = backup.inspect(self.cfg, name)
        self.assertTrue(info["ok"])
        self.assertEqual(info["save_files"], 2)
        self.assertEqual(info["world"], "servertest")
        self.assertIn("servertest.ini", info["config_files"])

    def test_missing_archive(self):
        info = backup.inspect(self.cfg, "nope.zip")
        self.assertFalse(info["ok"])
        self.assertIn("no such backup", info["error"])

    def test_non_zip_is_rejected(self):
        (self.cfg.backup_dir / "bogus.zip").write_text("not a zip", encoding="utf-8")
        info = backup.inspect(self.cfg, "bogus.zip")
        self.assertFalse(info["ok"])
        self.assertIn("not a valid zip", info["error"])

    def test_zip_without_saves_is_rejected(self):
        self.make_raw_zip("empty.zip", {"Server/servertest.ini": "PVP=true"})
        info = backup.inspect(self.cfg, "empty.zip")
        self.assertFalse(info["ok"])
        self.assertIn("no Saves/", info["error"])


class RestoreTests(RestoreTestCase):
    def test_restores_world_contents(self):
        name = self.make_backup("original")
        self.write_world("changed")
        self.assertEqual(self.world_marker(), "changed")

        result = backup.restore(self.cfg, name)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.world_marker(), "original")
        self.assertEqual(
            (self.cfg.save_dir / "sub" / "nested.bin").read_text(encoding="utf-8"), "original"
        )

    def test_restores_config_files(self):
        name = self.make_backup("original")
        self.write_configs("changed")

        result = backup.restore(self.cfg, name)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.cfg.ini_path.read_text(encoding="utf-8"), "PVP=original\n")
        self.assertIn("servertest.ini", result["config_files"])

    def test_takes_safety_backup_of_current_world(self):
        name = self.make_backup("original")
        self.write_world("precious")

        result = backup.restore(self.cfg, name)

        self.assertTrue(result["ok"], result.get("error"))
        safety = result["safety_backup"]
        self.assertIsNotNone(safety)
        with zipfile.ZipFile(self.cfg.backup_dir / safety) as zf:
            self.assertEqual(zf.read("Saves/map_0_0.bin").decode(), "precious")

    def test_previous_world_is_kept_aside(self):
        name = self.make_backup("original")
        self.write_world("changed")

        result = backup.restore(self.cfg, name)

        displaced = self.cfg.save_dir.with_name(result["displaced"])
        self.assertTrue(displaced.is_dir())
        self.assertEqual(
            (displaced / "map_0_0.bin").read_text(encoding="utf-8"), "changed"
        )

    def test_pre_backup_can_be_disabled(self):
        name = self.make_backup("original")
        before = len(list(self.cfg.backup_dir.glob("*.zip")))

        result = backup.restore(self.cfg, name, pre_backup=False)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertIsNone(result["safety_backup"])
        self.assertEqual(len(list(self.cfg.backup_dir.glob("*.zip"))), before)

    def test_restores_into_a_missing_save_dir(self):
        name = self.make_backup("original")
        import shutil

        shutil.rmtree(self.cfg.save_dir)

        result = backup.restore(self.cfg, name)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.world_marker(), "original")
        self.assertIsNone(result["displaced"])

    def test_leaves_no_staging_directory(self):
        name = self.make_backup("original")
        backup.restore(self.cfg, name)
        leftovers = list(self.cfg.save_dir.parent.glob(".pzctl-restore-*"))
        self.assertEqual(leftovers, [])

    def test_unknown_archive_is_refused(self):
        result = backup.restore(self.cfg, "nope.zip")
        self.assertFalse(result["ok"])


class ServerStateTests(RestoreTestCase):
    def test_refused_while_running(self):
        name = self.make_backup("original")
        self.write_world("live")

        result = backup.restore(self.cfg, name, FakeSupervisor("running"))

        self.assertFalse(result["ok"])
        self.assertIn("stop it before restoring", result["error"])
        # The live world must be untouched.
        self.assertEqual(self.world_marker(), "live")

    def test_refused_while_starting(self):
        name = self.make_backup("original")
        result = backup.restore(self.cfg, name, FakeSupervisor("starting"))
        self.assertFalse(result["ok"])

    def test_allowed_when_stopped(self):
        name = self.make_backup("original")
        result = backup.restore(self.cfg, name, FakeSupervisor("stopped"))
        self.assertTrue(result["ok"], result.get("error"))

    def test_allowed_after_crash(self):
        """A crashed server is exactly when a restore is most likely wanted."""
        name = self.make_backup("original")
        result = backup.restore(self.cfg, name, FakeSupervisor("crashed"))
        self.assertTrue(result["ok"], result.get("error"))


class MaliciousArchiveTests(RestoreTestCase):
    def test_rejects_parent_traversal_member(self):
        self.make_raw_zip(
            "evil.zip",
            {"Saves/ok.bin": "fine", "Saves/../../../escaped.txt": "pwned"},
        )
        result = backup.restore(self.cfg, "evil.zip", pre_backup=False)

        self.assertFalse(result["ok"])
        self.assertIn("unsafe path", result["error"])
        self.assertFalse((self.dir.parent / "escaped.txt").exists())

    def test_config_entries_cannot_write_outside_server_dir(self):
        """Config members are matched by basename, never by their archive path."""
        self.make_raw_zip(
            "sneaky.zip",
            {
                "Saves/ok.bin": "fine",
                "Server/../../../evil.ini": "pwned",
                "Server/unrelated.txt": "ignored",
            },
        )
        result = backup.restore(self.cfg, "sneaky.zip", pre_backup=False)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["config_files"], [])
        self.assertFalse((self.dir.parent / "evil.ini").exists())
        self.assertFalse((self.cfg.server_config_dir / "unrelated.txt").exists())

    def test_corrupt_archive_does_not_touch_the_world(self):
        self.write_world("precious")
        (self.cfg.backup_dir / "corrupt.zip").write_text("garbage", encoding="utf-8")

        result = backup.restore(self.cfg, "corrupt.zip")

        self.assertFalse(result["ok"])
        self.assertEqual(self.world_marker(), "precious")


if __name__ == "__main__":
    unittest.main()
