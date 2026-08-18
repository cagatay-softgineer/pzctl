"""Tests for cross-platform process supervision.

These exercise the platform-dependent decisions directly rather than waiting
for a Linux machine: the launch command is built from `IS_WINDOWS` and
`os.pathsep`, so both branches can be checked from either platform.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pzctl import supervisor
from pzctl.config import Config


class PlatformTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(Path(self._tmp.name) / "pzctl.json")

        self._orig = supervisor.IS_WINDOWS
        self.addCleanup(lambda: setattr(supervisor, "IS_WINDOWS", self._orig))

    def as_platform(self, windows: bool) -> None:
        supervisor.IS_WINDOWS = windows


class JavaPathTests(PlatformTestCase):
    def test_windows_uses_the_exe(self):
        self.as_platform(True)
        self.assertEqual(supervisor.java_path().name, "java.exe")

    def test_posix_has_no_extension(self):
        self.as_platform(False)
        self.assertEqual(supervisor.java_path().name, "java")

    def test_both_live_under_the_bundled_runtime(self):
        for windows in (True, False):
            self.as_platform(windows)
            parts = supervisor.java_path().parts
            self.assertIn("jre64", parts)
            self.assertIn("bin", parts)


class ClasspathTests(PlatformTestCase):
    def test_uses_the_platform_separator(self):
        """A hardcoded ';' makes the JVM silently miss the server class on Linux."""
        args = supervisor.Supervisor(self.cfg).build_command()
        classpath = args[args.index("-cp") + 1]
        self.assertIn(os.pathsep, classpath)
        self.assertEqual(
            classpath, os.pathsep.join(("java/", "java/projectzomboid.jar"))
        )

    def test_names_the_server_class(self):
        args = supervisor.Supervisor(self.cfg).build_command()
        self.assertEqual(args[args.index("-cp") + 2], "zombie.network.GameServer")


class CreationFlagTests(PlatformTestCase):
    def test_flag_is_zero_off_windows(self):
        """subprocess raises if creationflags is passed on POSIX."""
        if supervisor.IS_WINDOWS:
            self.assertEqual(supervisor.CREATE_NO_WINDOW, 0x08000000)
        else:
            self.assertEqual(supervisor.CREATE_NO_WINDOW, 0)


class MemoryReportingTests(PlatformTestCase):
    def test_unreadable_pid_reports_zero_not_an_error(self):
        """Memory is a status field; failing to read it must not raise."""
        self.assertEqual(supervisor._working_set_bytes(999999999), 0)

    def test_posix_path_reads_proc_status(self):
        self.as_platform(False)
        if os.path.isdir("/proc"):
            self.assertGreater(supervisor._working_set_bytes(os.getpid()), 0)
        else:
            # No /proc here, so the POSIX branch correctly reports nothing.
            self.assertEqual(supervisor._working_set_bytes(os.getpid()), 0)

    def test_windows_path_reads_psapi(self):
        if not self._orig:
            self.skipTest("psapi is only available on Windows")
        self.as_platform(True)
        self.assertGreater(supervisor._working_set_bytes(os.getpid()), 0)


class LauncherTests(unittest.TestCase):
    def test_posix_launcher_ships(self):
        root = Path(supervisor.SERVER_DIR)
        self.assertTrue((root / "pz-control.sh").is_file())

    def test_posix_launcher_runs_the_module(self):
        script = (Path(supervisor.SERVER_DIR) / "pz-control.sh").read_text(encoding="utf-8")
        self.assertIn("-m pzctl", script)
        # It must work from a double-click or any cwd, like the .bat does.
        self.assertIn('cd "$(dirname "$0")"', script)

    def test_posix_launcher_reports_a_missing_python(self):
        script = (Path(supervisor.SERVER_DIR) / "pz-control.sh").read_text(encoding="utf-8")
        self.assertIn("Python 3.11 or newer is required", script)


if __name__ == "__main__":
    unittest.main()
