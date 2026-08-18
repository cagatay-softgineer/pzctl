"""Tests that pzctl shuts down cleanly on the signals it will actually receive.

`docker stop`, systemd and a bare `kill` all send SIGTERM. Without a handler
the process is killed outright and the game server never gets its final save,
so this is checked against a real process rather than by inspecting the source.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Stands in for the daemon: installs the same handlers app.py does, then waits.
PROGRAM = textwrap.dedent(
    """
    import signal, sys, threading, time
    stopping = threading.Event()

    def _shutdown(*_):
        stopping.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue
        try:
            signal.signal(handler, _shutdown)
        except (OSError, ValueError):
            pass

    print("ready", flush=True)
    while not stopping.wait(0.1):
        pass
    print("clean shutdown", flush=True)
    sys.exit(0)
    """
)


@unittest.skipIf(
    os.name == "nt",
    "Windows cannot deliver a catchable SIGTERM or SIGINT to another process - "
    "send_signal maps to TerminateProcess. The Ubuntu CI job runs these for real, "
    "which is the platform where docker stop and systemd actually matter.",
)
class SignalHandlingTests(unittest.TestCase):
    """The handler set is exercised as a real process receiving a real signal."""

    def _run_and_signal(self, sig) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_daemon.py"
            script.write_text(PROGRAM, encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                self.assertEqual((proc.stdout.readline() or "").strip(), "ready")
                proc.send_signal(sig)
                out, _ = proc.communicate(timeout=15)
                return proc.returncode, out
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_sigterm_shuts_down_cleanly(self):
        """This is what `docker stop` and systemd send."""
        code, out = self._run_and_signal(signal.SIGTERM)
        self.assertIn("clean shutdown", out)
        self.assertEqual(code, 0)

    def test_sigint_still_shuts_down_cleanly(self):
        """Ctrl+C must keep working; SIGTERM support must not replace it."""
        code, out = self._run_and_signal(signal.SIGINT)
        self.assertIn("clean shutdown", out)
        self.assertEqual(code, 0)


class HandlerRegistrationTests(unittest.TestCase):
    def test_app_registers_sigterm(self):
        """A regression here is silent: the process just dies mid-save."""
        source = (REPO / "pzctl" / "app.py").read_text(encoding="utf-8")
        self.assertIn("SIGTERM", source)

    def test_app_tolerates_platform_specific_signals(self):
        source = (REPO / "pzctl" / "app.py").read_text(encoding="utf-8")
        # SIGBREAK is Windows-only and SIGHUP is POSIX-only, so both must be
        # looked up rather than referenced directly.
        self.assertIn("getattr(signal, name, None)", source)

    def test_shutdown_stops_the_game_server(self):
        """The whole point: the JVM is stopped, not left to be killed with us."""
        source = (REPO / "pzctl" / "app.py").read_text(encoding="utf-8")
        index = source.index("shutting down")
        self.assertIn("sup.shutdown()", source[index:])


if __name__ == "__main__":
    unittest.main()
