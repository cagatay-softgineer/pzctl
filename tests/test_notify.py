"""Tests for flexible scheduling and Discord notifications.

No test makes a network request: `notify._post` is replaced throughout.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pzctl import notify
from pzctl.config import Config
from pzctl.scheduler import next_run

MONDAY = datetime(2026, 8, 17, 10, 0)


class NextRunTests(unittest.TestCase):
    def test_daily_job_unchanged(self):
        """Existing pzctl.json files must keep working untouched."""
        self.assertEqual(next_run({"time": "04:00"}, MONDAY), datetime(2026, 8, 18, 4, 0))

    def test_daily_later_today(self):
        self.assertEqual(next_run({"time": "18:00"}, MONDAY), datetime(2026, 8, 17, 18, 0))

    def test_weekday_restriction(self):
        self.assertEqual(
            next_run({"time": "04:00", "days": ["sat"]}, MONDAY), datetime(2026, 8, 22, 4, 0)
        )

    def test_multiple_weekdays_picks_the_nearest(self):
        job = {"time": "04:00", "days": ["wed", "sat"]}
        self.assertEqual(next_run(job, MONDAY), datetime(2026, 8, 19, 4, 0))

    def test_day_names_are_forgiving(self):
        for spelling in (["Saturday"], ["SAT"], ["sat"]):
            self.assertEqual(
                next_run({"time": "04:00", "days": spelling}, MONDAY),
                datetime(2026, 8, 22, 4, 0),
                spelling,
            )

    def test_unrecognised_days_fall_back_to_daily(self):
        """An unusable day list must not silently mean 'never run'."""
        self.assertEqual(
            next_run({"time": "04:00", "days": ["xyz"]}, MONDAY), datetime(2026, 8, 18, 4, 0)
        )

    def test_interval_anchored_at_midnight(self):
        self.assertEqual(next_run({"every_hours": 6}, MONDAY), datetime(2026, 8, 17, 12, 0))

    def test_interval_rolls_into_tomorrow(self):
        late = datetime(2026, 8, 17, 23, 30)
        self.assertEqual(next_run({"every_hours": 6}, late), datetime(2026, 8, 18, 0, 0))

    def test_interval_takes_precedence_over_time(self):
        job = {"time": "04:00", "every_hours": 6}
        self.assertEqual(next_run(job, MONDAY), datetime(2026, 8, 17, 12, 0))

    def test_invalid_interval(self):
        for bad in (0, 25, -1, "soon", None):
            job = {"every_hours": bad}
            if bad is None:
                continue
            self.assertIsNone(next_run(job, MONDAY), bad)

    def test_missing_time(self):
        self.assertIsNone(next_run({}, MONDAY))


class NotifyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(Path(self._tmp.name) / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.posted: list[tuple[str, str]] = []

        original = notify._post
        notify._post = lambda url, content: (
            self.posted.append((url, content)),
            {"ok": True, "status": 204},
        )[1]
        self.addCleanup(lambda: setattr(notify, "_post", original))

    def enable(self, url: str = "https://discord.com/api/webhooks/x") -> None:
        self.cfg.set("notify.discord_webhook", url)


class SendTests(NotifyTestCase):
    def test_no_webhook_is_not_an_error(self):
        """Notifications are off by default; that is not a failure."""
        result = notify.send(self.cfg, "hello")
        self.assertTrue(result["ok"])
        self.assertIn("no webhook", result["skipped"])
        self.assertEqual(self.posted, [])

    def test_configured_returns_false_by_default(self):
        self.assertFalse(notify.configured(self.cfg))
        self.enable()
        self.assertTrue(notify.configured(self.cfg))

    def test_sends_when_configured(self):
        self.enable()
        result = notify.send(self.cfg, "hello", blocking=True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.posted[0][1], "hello")

    def test_refuses_a_non_https_url(self):
        """A webhook URL is a secret; plain http would leak it."""
        self.enable("http://discord.com/api/webhooks/x")
        result = notify.send(self.cfg, "hello")
        self.assertFalse(result["ok"])
        self.assertIn("https", result["error"])
        self.assertEqual(self.posted, [])

    def test_empty_message_refused(self):
        self.enable()
        self.assertFalse(notify.send(self.cfg, "   ")["ok"])

    def test_long_message_is_truncated(self):
        self.enable()
        notify.send(self.cfg, "x" * 5000, blocking=True)
        self.assertLessEqual(len(self.posted[0][1]), notify.MAX_CONTENT)


class EventTests(NotifyTestCase):
    def test_event_includes_the_server_name(self):
        self.enable()
        notify.event(self.cfg, "crashed", "exit code 1")
        self.assertIn("servertest", self.posted[0][1])
        self.assertIn("crashed", self.posted[0][1])
        self.assertIn("exit code 1", self.posted[0][1])

    def test_disabled_event_is_not_sent(self):
        self.enable()
        self.cfg.set("notify.events", {"backup": False})
        result = notify.event(self.cfg, "backup", "x")
        self.assertTrue(result["ok"])
        self.assertEqual(self.posted, [])

    def test_enabled_event_is_sent(self):
        self.enable()
        self.cfg.set("notify.events", {"backup": True})
        notify.event(self.cfg, "backup", "x")
        self.assertEqual(len(self.posted), 1)

    def test_unlisted_event_defaults_to_sending(self):
        self.enable()
        self.cfg.set("notify.events", {})
        notify.event(self.cfg, "something_new")
        self.assertEqual(len(self.posted), 1)

    def test_backup_is_off_by_default(self):
        """Backups are frequent; announcing each one would be noise."""
        self.assertFalse((self.cfg.get("notify.events") or {})["backup"])

    def test_crashed_is_on_by_default(self):
        self.assertTrue((self.cfg.get("notify.events") or {})["crashed"])

    def test_failure_never_raises(self):
        self.enable()
        notify._post = lambda url, content: (_ for _ in ()).throw(OSError("boom"))
        # Blocking send surfaces the failure as a result, not an exception.
        try:
            notify.send(self.cfg, "hello")
        except OSError:
            self.fail("a failed notification must not propagate")


if __name__ == "__main__":
    unittest.main()
