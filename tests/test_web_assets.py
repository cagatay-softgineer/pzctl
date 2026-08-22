"""Sanity checks on the panel's static assets, without needing node.

The `web` CI job runs `node --check`, which is the real syntax check. This is a
second line of defence that runs everywhere the Python suite does, including on
machines with no node installed.

It exists because of a specific, repeated failure: scripted edits introduced a
raw newline inside a JavaScript string literal, which made the whole panel fail
to parse. The daemon served it happily, every Python test passed, and the only
symptom was `Uncaught SyntaxError` in the browser console - so nothing in the
suite noticed until someone opened the page.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "pzctl" / "web"


def scan_strings(source: str) -> list[tuple[int, str]]:
    """Find quoted strings broken by a raw newline.

    A hand-rolled scanner rather than a real parser: it tracks quotes, comments
    and escapes, which is enough to catch the failure this guards against.
    Backticks are skipped because template literals may legally span lines.

    Returns (line number, quote character) for each unterminated string.
    """
    problems: list[tuple[int, str]] = []
    line = 1
    index = 0
    length = len(source)
    quote: str | None = None
    start_line = 0

    while index < length:
        char = source[index]

        if quote is None:
            if char == "\n":
                line += 1
            elif char == "/" and index + 1 < length and source[index + 1] == "/":
                while index < length and source[index] != "\n":
                    index += 1
                continue
            elif char == "/" and index + 1 < length and source[index + 1] == "*":
                index += 2
                while index + 1 < length and not (
                    source[index] == "*" and source[index + 1] == "/"
                ):
                    if source[index] == "\n":
                        line += 1
                    index += 1
                index += 2
                continue
            elif char in "'\"":
                quote = char
                start_line = line
        else:
            if char == "\\":
                # An escaped newline is a legal line continuation.
                if index + 1 < length and source[index + 1] == "\n":
                    line += 1
                index += 2
                continue
            if char == "\n":
                problems.append((start_line, quote))
                line += 1
                quote = None
            elif char == quote:
                quote = None

        index += 1

    return problems


class StringLiteralTests(unittest.TestCase):
    def test_no_string_spans_a_line_break(self):
        for path in sorted(WEB_DIR.glob("*.js")):
            with self.subTest(file=path.name):
                problems = scan_strings(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    problems,
                    [],
                    f"{path.name}: string literal broken across lines at "
                    f"{', '.join(f'line {n} ({q})' for n, q in problems)} - "
                    "this makes the whole panel fail to parse",
                )

    def test_the_scanner_catches_a_planted_break(self):
        """The check is worthless if it cannot fail."""
        broken = 'var a = "hello\nworld";'
        self.assertEqual(scan_strings(broken), [(1, '"')])

    def test_the_scanner_allows_legitimate_code(self):
        ok = "\n".join([
            'var a = "he said \\"hi\\"";',
            "var b = 'a\\nb';",
            "// a comment with an ' apostrophe",
            "/* block with a \" quote",
            "   spanning lines */",
            "var c = `template",
            "spanning lines`;",
        ])
        self.assertEqual(scan_strings(ok), [])


class BalanceTests(unittest.TestCase):
    """Catches truncated files, which a scripted edit can also produce."""

    def test_braces_balance(self):
        for path in sorted(WEB_DIR.glob("*.js")):
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                # Strip strings and comments before counting, so braces inside
                # them do not register.
                stripped = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', source)
                stripped = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", stripped)
                stripped = re.sub(r"//[^\n]*", "", stripped)
                stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
                self.assertEqual(
                    stripped.count("{"), stripped.count("}"),
                    f"{path.name}: unbalanced braces",
                )


class MarkupTests(unittest.TestCase):
    def test_every_script_reference_exists(self):
        """A renamed or missing asset yields a silently half-working panel."""
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for src in re.findall(r'<script src="/([^"]+)"', html):
            with self.subTest(src=src):
                self.assertTrue(
                    (WEB_DIR / src).is_file(), f"index.html references missing {src}"
                )
        for href in re.findall(r'<link[^>]+href="/([^"]+)"', html):
            with self.subTest(href=href):
                self.assertTrue(
                    (WEB_DIR / href).is_file(), f"index.html references missing {href}"
                )

    def test_ids_the_scripts_target_are_present(self):
        """The resource panel is wired by id; a typo leaves it blank forever."""
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for element_id in ("resGrid", "resWindows", "resHint", "statCpu", "statDisk"):
            with self.subTest(id=element_id):
                self.assertIn(f'id="{element_id}"', html)


if __name__ == "__main__":
    unittest.main()
