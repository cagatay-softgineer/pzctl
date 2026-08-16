"""Line-preserving reader/writer for the PZ server .ini (flat Key=Value, no sections).

Rewrites only the value spans of keys that actually changed, so comments,
ordering and unknown keys survive untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

LINE_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<val>.*)$")

# PZ writes booleans as lowercase true/false; everything else is a bare string
# or a semicolon-delimited list.
LIST_KEYS = {"Mods", "WorkshopItems", "Map"}


def read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LINE_RE.match(stripped)
        if match:
            out[match.group("key")] = match.group("val").strip()
    return out


def write(path: Path, changes: dict[str, str]) -> list[str]:
    """Apply `changes` in place. Returns the list of keys actually modified."""
    if not path.exists():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    remaining = dict(changes)
    touched: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LINE_RE.match(stripped)
        if not match:
            continue
        key = match.group("key")
        if key not in remaining:
            continue
        new_value = str(remaining.pop(key))
        if match.group("val").strip() != new_value:
            lines[index] = f"{key}={new_value}"
            touched.append(key)

    # Keys the server has not written yet (e.g. a fresh ini) get appended.
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
        touched.append(key)

    _atomic_write(path, "\r\n".join(lines) + "\r\n")
    return touched


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def join_list(items: list[str]) -> str:
    return ";".join(item.strip() for item in items if item.strip())
