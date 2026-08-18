"""Control how much the game server logs.

Two documented mechanisms:

    -debuglog=Network,Sound     at launch, turn categories on
    -disablelog=All             at launch, turn categories off
    log "Type" "Level"          at runtime, over RCON

The category names are `DebugType` values from the game. pzctl does **not**
ship a list of them: no authoritative enumeration could be found, the set
changes between builds, and a hardcoded dropdown would quietly omit whatever
was missing. Categories are typed by the admin, validated only for shape, and
the server decides whether it recognises them - the same approach the live
config apply takes with option names.
"""

from __future__ import annotations

import re

from .config import Config

# A DebugType is a bare identifier. Anything else would be going into a command
# line or an RCON command, so it is refused rather than escaped.
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Attested in the documentation; offered as hints, not as the allowed set.
KNOWN_HINTS = ("All", "General", "Network", "Sound", "Lua", "Mod")


def parse_categories(value) -> list[str]:
    """Split a comma-separated list into clean tokens, dropping blanks."""
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = str(value or "").split(",")
    return [part.strip() for part in parts if part.strip()]


def validate_categories(value) -> tuple[list[str], str | None]:
    """Return (tokens, error). Tokens are only returned when all are valid."""
    tokens = parse_categories(value)
    bad = [token for token in tokens if not TOKEN_RE.match(token)]
    if bad:
        return [], f"not valid log categories: {', '.join(bad)}"
    return tokens, None


def launch_args(cfg: Config) -> list[str]:
    """Build the -debuglog/-disablelog flags for the server command line."""
    args: list[str] = []
    for key, flag in (("logging.debug", "-debuglog"), ("logging.disable", "-disablelog")):
        tokens, problem = validate_categories(cfg.get(key) or [])
        if problem or not tokens:
            # An invalid value is dropped rather than passed through; a bad
            # flag would stop the server from starting at all.
            continue
        args.append(f"{flag}={','.join(tokens)}")
    return args


def set_level(supervisor, log_type: str, level: str) -> dict:
    """Adjust logging on a running server."""
    log_type = str(log_type or "").strip()
    level = str(level or "").strip()
    for label, value in (("type", log_type), ("level", level)):
        if not value:
            return {"ok": False, "error": f"no log {label} given"}
        if not TOKEN_RE.match(value):
            return {"ok": False, "error": f"invalid log {label}: {value!r}"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    ok, reply = supervisor.send_command(f'log "{log_type}" "{level}"', prefer="auto")
    return {"ok": ok, "type": log_type, "level": level, "reply": reply}
