"""Read-only access to the game's own log files.

Project Zomboid writes its logs into `Zomboid/Logs/`, and rotates them per
session with a timestamp prefix - `25-08-17_12-30-00_DebugLog-server.txt`
rather than a fixed name. So the files are discovered by pattern rather than
looked up by hardcoded name, and anything unrecognised is still listed instead
of disappearing.

This module only ever reads, and only from inside the log directories.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config

# Suffix patterns mapped to a human label. Matched case-insensitively against
# the tail of the filename, so the session timestamp prefix does not matter.
KINDS: tuple[tuple[str, str], ...] = (
    ("server-console.txt", "console"),
    ("debuglog-server.txt", "debug"),
    ("clientactionlogs.txt", "client actions"),
    ("perklog.txt", "perks"),
    ("chat.txt", "chat"),
    ("user.txt", "users"),
    ("admin.txt", "admin"),
    ("cmd.txt", "commands"),
    ("item.txt", "items"),
    ("map.txt", "map"),
    ("pvp.txt", "pvp"),
)

SUFFIXES = (".txt", ".log")

DEFAULT_TAIL_BYTES = 256 * 1024
MAX_TAIL_BYTES = 4 * 1024 * 1024


def _classify(name: str) -> str:
    lowered = name.lower()
    for suffix, label in KINDS:
        if lowered.endswith(suffix):
            return label
    return "other"


def log_dir(cfg: Config) -> Path:
    return cfg.zomboid_dir / "Logs"


def candidates(cfg: Config) -> list[Path]:
    """Files eligible for viewing.

    Everything in `Zomboid/Logs/`, plus `server-console.txt` which some
    versions write to the `Zomboid/` root instead.
    """
    found: list[Path] = []
    directory = log_dir(cfg)
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in SUFFIXES:
                found.append(path)
    loose = cfg.zomboid_dir / "server-console.txt"
    if loose.is_file():
        found.append(loose)
    return found


def discover(cfg: Config) -> list[dict]:
    """List the game's log files, newest first."""
    entries = []
    for path in candidates(cfg):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": path.name,
                "kind": _classify(path.name),
                "size_kb": round(stat.st_size / 1024, 1),
                "mtime": stat.st_mtime,
            }
        )
    entries.sort(key=lambda entry: entry["mtime"], reverse=True)
    return entries


def resolve(cfg: Config, name: str) -> Path | None:
    """Map a requested log name onto a real file, or None.

    The name arrives over HTTP, so it is matched against the discovered set
    rather than joined onto a directory - a path that is not already a listed
    log file cannot be read, whatever it contains.
    """
    if not name or name != Path(name).name:
        return None
    for path in candidates(cfg):
        if path.name == name:
            return path
    return None


def tail(cfg: Config, name: str, max_bytes: int = DEFAULT_TAIL_BYTES) -> dict:
    """Return the last `max_bytes` of a log file.

    Log files grow without bound - a long-running server's console log can
    reach hundreds of megabytes - so this seeks to the end rather than reading
    the whole file into memory.
    """
    path = resolve(cfg, name)
    if path is None:
        return {"ok": False, "error": "no such log file"}

    max_bytes = max(1024, min(int(max_bytes or DEFAULT_TAIL_BYTES), MAX_TAIL_BYTES))

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = max(0, size - max_bytes)
            if offset:
                handle.seek(offset)
            chunk = handle.read()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    text = chunk.decode("utf-8", errors="replace")
    truncated = offset > 0
    if truncated:
        # Seeking lands mid-line; drop the partial first line rather than
        # showing a fragment that looks like a real entry.
        newline = text.find("\n")
        text = text[newline + 1:] if newline != -1 else ""

    return {
        "ok": True,
        "name": path.name,
        "kind": _classify(path.name),
        "text": text,
        "size_kb": round(size / 1024, 1),
        "truncated": truncated,
        "mtime": path.stat().st_mtime,
    }
