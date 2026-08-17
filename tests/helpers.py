"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path


def read_raw(path: Path) -> str:
    """Read a file without newline translation.

    `Path.read_text(newline="")` would be the obvious call, but that argument
    only exists on Python 3.13+ and pzctl supports 3.11+. `Path.open` accepts
    it everywhere.

    Without this, the default universal-newlines mode rewrites CRLF to LF in
    the returned string, which would make the line-ending assertions pass
    regardless of what was actually written to disk.
    """
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()
