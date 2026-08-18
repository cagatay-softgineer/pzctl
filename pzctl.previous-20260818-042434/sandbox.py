"""Line-preserving reader/writer for <server>_SandboxVars.lua.

The file is a single `SandboxVars = { ... }` table with one nesting level of
sub-tables (ZombieLore, Map, etc.). We record the source span of every scalar
so edits splice values back without reformatting the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*?)(?P<comma>,?)\s*$"
)
INT_RE = re.compile(r"^-?\d+$")
FLOAT_RE = re.compile(r"^-?(\d+\.\d*|\.\d+)$")


class Entry:
    __slots__ = ("path", "value", "line", "raw")

    def __init__(self, path: str, value: Any, line: int, raw: str):
        self.path = path
        self.value = value
        self.line = line
        self.raw = raw


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text in ("true", "false"):
        return text == "true"
    if INT_RE.match(text):
        return int(text)
    if FLOAT_RE.match(text):
        return float(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _format_scalar(value: Any, previous: Any) -> str:
    if isinstance(previous, bool) or isinstance(value, bool):
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "1", "yes", "on")
        return "true" if value else "false"
    if isinstance(previous, (int, float)) and not isinstance(previous, bool):
        text = str(value).strip()
        if INT_RE.match(text):
            return text
        if FLOAT_RE.match(text):
            return text
        # Refuse to write a non-numeric value into a numeric slot.
        raise ValueError(f"expected a number, got {value!r}")
    return '"' + str(value).replace('"', '\\"') + '"'


def parse(path: Path) -> list[Entry]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[Entry] = []
    stack: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped in ("}", "},", "};"):
            if stack:
                stack.pop()
            continue
        match = ENTRY_RE.match(line)
        if not match:
            continue
        key = match.group("key")
        raw = match.group("val").strip()
        if raw.startswith("{"):
            stack.append(key)
            continue
        if key == "SandboxVars":
            continue
        # The server writes `SandboxVars = { ... }`; the shipped presets use a
        # bare `return { ... }`. Strip the wrapper only when it is actually there.
        prefix = stack[1:] if stack and stack[0] == "SandboxVars" else stack
        dotted = ".".join(prefix + [key])
        entries.append(Entry(dotted, _parse_scalar(raw), index, raw))
    return entries


def to_dict(path: Path) -> dict[str, Any]:
    return {entry.path: entry.value for entry in parse(path)}


def write(path: Path, changes: dict[str, Any]) -> list[str]:
    """Apply dotted-path `changes` in place. Returns keys actually modified."""
    if not path.exists():
        raise FileNotFoundError(path)
    # newline="" keeps the line endings intact; the default universal-newlines
    # mode would translate CRLF to LF and make the check below always fail.
    # Path.open is used rather than Path.read_text(newline=...), which only
    # accepts that argument on Python 3.13+.
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    by_path = {entry.path: entry for entry in parse(path)}
    touched: list[str] = []

    for dotted, value in changes.items():
        entry = by_path.get(dotted)
        if entry is None:
            continue
        formatted = _format_scalar(value, entry.value)
        if formatted == entry.raw:
            continue
        original = lines[entry.line]
        match = ENTRY_RE.match(original)
        if not match:
            continue
        lines[entry.line] = (
            f"{match.group('indent')}{match.group('key')} = {formatted}{match.group('comma')}"
        )
        touched.append(dotted)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")
    tmp.replace(path)
    return touched
