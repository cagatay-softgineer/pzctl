"""Tests for in-place upgrade of pzctl itself.

Every test installs into a temporary directory via `package_dir`. Nothing here
may touch the package the tests are running from.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from pzctl import upgrade


class FakeSupervisor:
    def __init__(self, alive: bool = False):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class UpgradeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

        # A stand-in for the installed package plus its neighbours.
        self.package = self.dir / "pzctl"
        (self.package / "web").mkdir(parents=True)
        (self.package / "__init__.py").write_text("__version__ = '1.0.0'", encoding="utf-8")
        (self.package / "app.py").write_text("# old", encoding="utf-8")
        (self.package / "web" / "index.html").write_text("<old>", encoding="utf-8")

        self.config = self.dir / "pzctl.json"
        self.config.write_text('{"admin_password": "secret"}', encoding="utf-8")
        self.data = self.dir / "pzctl-data" / "logs"
        self.data.mkdir(parents=True)
        (self.data / "console.log").write_text("history", encoding="utf-8")

    def make_release_zip(self, prefix: str = "pzctl-2.0.0/", extra: dict | None = None) -> Path:
        path = self.dir / "release.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"{prefix}pzctl/__init__.py", "__version__ = '2.0.0'")
            zf.writestr(f"{prefix}pzctl/app.py", "# new")
            zf.writestr(f"{prefix}pzctl/web/index.html", "<new>")
            zf.writestr(f"{prefix}README.md", "readme")
            for name, body in (extra or {}).items():
                zf.writestr(name, body)
        return path

    def upgrade(self, archive):
        return upgrade.apply(FakeSupervisor(), archive=archive, package_dir=self.package)


class GuardTests(UpgradeTestCase):
    def test_refused_while_the_game_server_is_running(self):
        result = upgrade.apply(FakeSupervisor(alive=True), package_dir=self.package)
        self.assertFalse(result["ok"])
        self.assertIn("stop the game server", result["error"])

    def test_refusal_explains_the_consequence(self):
        result = upgrade.apply(FakeSupervisor(alive=True), package_dir=self.package)
        self.assertIn("disconnect", result["error"])

    def test_running_server_leaves_files_untouched(self):
        upgrade.apply(FakeSupervisor(alive=True), package_dir=self.package)
        self.assertEqual((self.package / "app.py").read_text(encoding="utf-8"), "# old")


class ArchiveValidationTests(UpgradeTestCase):
    def test_rejects_a_non_zip(self):
        bogus = self.dir / "bogus.zip"
        bogus.write_text("not a zip", encoding="utf-8")
        result = self.upgrade(bogus)
        self.assertFalse(result["ok"])
        self.assertIn("not a valid zip", result["error"])

    def test_rejects_a_zip_that_is_not_pzctl(self):
        other = self.dir / "other.zip"
        with zipfile.ZipFile(other, "w") as zf:
            zf.writestr("something/else.txt", "hi")
        result = self.upgrade(other)
        self.assertFalse(result["ok"])
        self.assertIn("does not look like a pzctl release", result["error"])

    def test_accepts_a_flat_archive(self):
        self.assertTrue(self.upgrade(self.make_release_zip(prefix=""))["ok"])

    def test_bad_archive_leaves_the_install_intact(self):
        bogus = self.dir / "bogus.zip"
        bogus.write_text("not a zip", encoding="utf-8")
        self.upgrade(bogus)
        self.assertEqual((self.package / "app.py").read_text(encoding="utf-8"), "# old")

    def test_rejects_zip_slip(self):
        evil = self.make_release_zip(extra={"pzctl-2.0.0/pzctl/../../escaped.txt": "pwned"})
        result = self.upgrade(evil)
        self.assertFalse(result["ok"])
        self.assertFalse((self.dir.parent / "escaped.txt").exists())


class SwapTests(UpgradeTestCase):
    def test_installs_the_new_package(self):
        result = self.upgrade(self.make_release_zip())
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual((self.package / "app.py").read_text(encoding="utf-8"), "# new")
        self.assertIn("2.0.0", (self.package / "__init__.py").read_text(encoding="utf-8"))

    def test_web_assets_come_across(self):
        self.upgrade(self.make_release_zip())
        self.assertEqual(
            (self.package / "web" / "index.html").read_text(encoding="utf-8"), "<new>"
        )

    def test_previous_version_is_kept(self):
        result = self.upgrade(self.make_release_zip())
        kept = self.dir / result["previous_kept_as"]
        self.assertTrue(kept.is_dir())
        self.assertEqual((kept / "app.py").read_text(encoding="utf-8"), "# old")

    def test_pzctl_json_is_never_touched(self):
        """It holds the admin password, RCON password and access token."""
        self.upgrade(self.make_release_zip())
        self.assertEqual(self.config.read_text(encoding="utf-8"), '{"admin_password": "secret"}')

    def test_data_directory_survives(self):
        self.upgrade(self.make_release_zip())
        self.assertEqual((self.data / "console.log").read_text(encoding="utf-8"), "history")

    def test_non_package_files_in_the_zip_are_ignored(self):
        """Only pzctl/ is replaced; a README in the zip must not overwrite anything."""
        self.upgrade(self.make_release_zip())
        self.assertFalse((self.dir / "README.md").exists())

    def test_says_a_restart_is_required(self):
        """The files are swapped but Python still holds the old modules."""
        result = self.upgrade(self.make_release_zip())
        self.assertTrue(result["restart_required"])
        self.assertIn("restart", result["note"].lower())

    def test_leaves_no_workspace_behind(self):
        self.upgrade(self.make_release_zip())
        self.assertEqual(list(self.dir.glob("pzctl-upgrade-*")), [])


class InspectTests(UpgradeTestCase):
    def test_finds_the_prefix(self):
        details = upgrade.inspect_archive(self.make_release_zip())
        self.assertTrue(details["ok"])
        self.assertEqual(details["prefix"], "pzctl-2.0.0/")

    def test_flat_archive_has_no_prefix(self):
        details = upgrade.inspect_archive(self.make_release_zip(prefix=""))
        self.assertEqual(details["prefix"], "")


class AssetTests(unittest.TestCase):
    def test_picks_the_release_zip(self):
        release = {
            "assets": [
                {"name": "notes.txt"},
                {"name": "pzctl-1.2.3.zip", "browser_download_url": "https://example/z"},
            ]
        }
        self.assertEqual(upgrade._pick_asset(release)["name"], "pzctl-1.2.3.zip")

    def test_no_asset(self):
        self.assertIsNone(upgrade._pick_asset({"assets": []}))
        self.assertIsNone(upgrade._pick_asset({}))


if __name__ == "__main__":
    unittest.main()
