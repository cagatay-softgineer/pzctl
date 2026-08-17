"""Tests for the pzctl update check.

No test here touches the network: `_fetch_latest` is replaced throughout. A
unit test that reached GitHub would be slow, flaky, and would fail on the very
machines this feature has to behave well on - offline ones.
"""

from __future__ import annotations

import json
import unittest
import urllib.error

from pzctl import updates


class ParseVersionTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(updates.parse_version("1.2.3"), (1, 2, 3))

    def test_v_prefixed(self):
        self.assertEqual(updates.parse_version("v1.2.3"), (1, 2, 3))

    def test_trailing_suffix_ignored(self):
        self.assertEqual(updates.parse_version("1.2.3-beta"), (1, 2, 3))

    def test_double_digits(self):
        self.assertEqual(updates.parse_version("v10.20.30"), (10, 20, 30))

    def test_unparseable(self):
        for value in ["", "latest", "abc", None, "1.2"]:
            self.assertIsNone(updates.parse_version(value), value)


class CompareTests(unittest.TestCase):
    def test_newer_available(self):
        self.assertEqual(updates.compare("1.0.0", "1.1.0"), "newer_available")

    def test_patch_bump_counts(self):
        self.assertEqual(updates.compare("1.0.0", "1.0.1"), "newer_available")

    def test_same(self):
        self.assertEqual(updates.compare("1.0.0", "1.0.0"), "current")

    def test_v_prefix_does_not_confuse_it(self):
        self.assertEqual(updates.compare("1.0.0", "v1.0.0"), "current")

    def test_local_build_ahead_is_not_reported_as_current(self):
        """Saying 'up to date' when running unreleased code would be misleading."""
        self.assertEqual(updates.compare("1.1.0", "1.0.0"), "ahead")

    def test_numeric_not_lexical(self):
        """'10' must beat '9', which a string comparison would get backwards."""
        self.assertEqual(updates.compare("1.9.0", "1.10.0"), "newer_available")

    def test_unknown_when_unparseable(self):
        self.assertEqual(updates.compare("1.0.0", "not-a-version"), "unknown")


class CheckTests(unittest.TestCase):
    def patch_fetch(self, replacement):
        original = updates._fetch_latest
        updates._fetch_latest = replacement
        self.addCleanup(lambda: setattr(updates, "_fetch_latest", original))

    def test_reports_a_newer_release(self):
        self.patch_fetch(
            lambda: {"tag_name": "v9.9.9", "html_url": "https://example/9", "body": "notes"}
        )
        result = updates.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "newer_available")
        self.assertEqual(result["latest"], "9.9.9")
        self.assertEqual(result["current"], updates.__version__)

    def test_reports_current(self):
        self.patch_fetch(lambda: {"tag_name": "v" + updates.__version__})
        self.assertEqual(updates.check()["status"], "current")

    def test_offline_is_not_an_error_the_user_must_decipher(self):
        def boom():
            raise urllib.error.URLError("no route to host")

        self.patch_fetch(boom)
        result = updates.check()
        self.assertFalse(result["ok"])
        self.assertIn("could not reach GitHub", result["error"])
        # The current version is still reported so the panel keeps working.
        self.assertEqual(result["current"], updates.__version__)

    def test_timeout_handled(self):
        def boom():
            raise TimeoutError()

        self.patch_fetch(boom)
        self.assertFalse(updates.check()["ok"])

    def test_rate_limit_is_explained(self):
        def boom():
            raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)

        self.patch_fetch(boom)
        result = updates.check()
        self.assertFalse(result["ok"])
        self.assertIn("rate limited", result["error"])

    def test_no_releases_is_not_an_error(self):
        def boom():
            raise urllib.error.HTTPError("u", 404, "not found", {}, None)

        self.patch_fetch(boom)
        result = updates.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "no_releases")

    def test_garbage_response_handled(self):
        def boom():
            raise ValueError("bad json")

        self.patch_fetch(boom)
        self.assertFalse(updates.check()["ok"])

    def test_missing_tag_field(self):
        self.patch_fetch(lambda: {})
        result = updates.check()
        self.assertEqual(result["status"], "unknown")

    def test_notes_are_truncated(self):
        self.patch_fetch(lambda: {"tag_name": "v9.9.9", "body": "x" * 9000})
        self.assertLessEqual(len(updates.check()["notes"]), 2000)

    def test_always_includes_a_releases_link(self):
        self.patch_fetch(lambda: {"tag_name": "v9.9.9"})
        self.assertTrue(updates.check()["releases_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
