"""Compare the running server's options against the .ini on disk.

`showoptions` dumps what the server currently holds. Once options can be changed
live - by pzctl's apply-live, by an in-game admin, or by someone editing the
file while the server is up - the two can diverge silently. The file then no
longer describes the server that is running, and the difference only surfaces at
the next restart, when settings appear to change for no reason.

The reply format is not documented, so parsing is deliberately loose: any line
holding `Key=Value` or `Key: Value` is taken as an option, and anything that
does not look like one is ignored rather than guessed at. A key present on only
one side is reported as such rather than being treated as a difference in value.
"""

from __future__ import annotations

import re

from . import pzini
from .config import Config

# Tolerates "PVP=true", "PVP = true", "* PVP: true" and similar.
OPTION_RE = re.compile(r"^[\s*\-]*([A-Za-z][A-Za-z0-9_]*)\s*[=:]\s*(.*?)\s*$")

# Options whose live value is not meaningfully comparable with the file.
IGNORED = {"RCONPassword", "Password", "ServerPlayerID"}


def parse_options(reply: str) -> dict[str, str]:
    """Pull Key=Value pairs out of a showoptions reply."""
    found: dict[str, str] = {}
    for line in str(reply or "").splitlines():
        match = OPTION_RE.match(line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def compare(live: dict[str, str], on_disk: dict[str, str]) -> list[dict]:
    """Differences between the running server and the file."""
    out: list[dict] = []
    for key in sorted(set(live) | set(on_disk)):
        if key in IGNORED:
            continue
        here, there = on_disk.get(key), live.get(key)
        if here is None:
            out.append({"key": key, "file": None, "live": there, "why": "not in the file"})
        elif there is None:
            # The server reporting fewer options than the file holds is normal
            # for keys it does not expose, so this is informational.
            continue
        elif str(here).strip().lower() != str(there).strip().lower():
            out.append({"key": key, "file": here, "live": there, "why": "differs"})
    return out


def check(cfg: Config, supervisor) -> dict:
    """Ask the running server what it holds and diff it against the file."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}
    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running - there is nothing to compare against"}
    if not supervisor.rcon_ready():
        return {
            "ok": False,
            "error": "RCON is not configured - the server's live options cannot be read without it",
        }

    ok, reply = supervisor.send_command("showoptions", prefer="rcon")
    if not ok:
        return {"ok": False, "error": reply}

    live = parse_options(reply)
    if not live:
        # Better to say the reply was not understood than to report "no drift",
        # which would look like a clean result.
        return {
            "ok": False,
            "error": "could not read any options from the server's reply",
            "reply": (reply or "")[:500],
        }

    drift = compare(live, pzini.read(cfg.ini_path))
    return {
        "ok": True,
        "drift": drift,
        "live_count": len(live),
        "in_sync": not drift,
    }
