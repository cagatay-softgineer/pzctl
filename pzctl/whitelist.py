"""Whitelist mode and the users allowed onto a whitelisted server.

Documented behaviour:

    Open=false                          in the server .ini - whitelist enforced
    adduser "username" "password"       add a user to a whitelisted server
    removeuserfromwhitelist "username"  remove one

Note the inversion: `Open` describes the *server*, so whitelisting is on when
`Open` is false. The API here talks in terms of the whitelist being enabled and
converts, because getting that backwards silently opens a server to everyone.

`adduser` carries the new user's password. It is never echoed to the console or
written to the log - see the redaction below.
"""

from __future__ import annotations

import re

from . import liveconfig, pzini
from .config import Config
from .moderation import validate_name

OPEN_KEY = "Open"
REDACTED = "********"


def _validate_password(password: str) -> str | None:
    if not password:
        return "no password given"
    if re.search(r'["\r\n]', password):
        return "password contains characters that are not allowed"
    return None


def status(cfg: Config) -> dict:
    """Report whether the whitelist is enforced, per the .ini on disk."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}
    raw = pzini.read(cfg.ini_path).get(OPEN_KEY, "true")
    is_open = str(raw).strip().lower() != "false"
    return {"ok": True, "enabled": not is_open, "open": is_open}


def set_mode(cfg: Config, supervisor, enabled: bool) -> dict:
    """Turn the whitelist on or off by writing `Open`, and apply it live if possible."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    # Whitelist enabled means the server is NOT open.
    value = "false" if enabled else "true"
    changed = pzini.write(cfg.ini_path, {OPEN_KEY: value})

    result = {"ok": True, "enabled": enabled, "changed": bool(changed)}
    if changed and supervisor is not None and supervisor.is_alive():
        # Reuse the live-apply path so the change does not need a restart.
        result["live"] = liveconfig.apply(cfg, supervisor, {OPEN_KEY: value})
    return result


def add_user(supervisor, username: str, password: str) -> dict:
    """Add a user to the whitelist.

    The password is sent to the server but never echoed or logged.
    """
    problem = validate_name(username) or _validate_password(password)
    if problem:
        return {"ok": False, "error": problem}
    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"whitelist: adding user {username!r}", "pzctl")
    ok, reply = supervisor.send_command(
        f'adduser "{username}" "{password}"',
        prefer="auto",
        echo_as=f'adduser "{username}" "{REDACTED}"',
    )
    if not ok:
        supervisor.emit(f"whitelist: adding {username!r} FAILED - {reply}", "error")
    return {"ok": ok, "action": "add", "username": username, "reply": reply}


def remove_user(supervisor, username: str) -> dict:
    problem = validate_name(username)
    if problem:
        return {"ok": False, "error": problem}
    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"whitelist: removing user {username!r}", "pzctl")
    ok, reply = supervisor.send_command(f'removeuserfromwhitelist "{username}"', prefer="auto")
    if not ok:
        supervisor.emit(f"whitelist: removing {username!r} FAILED - {reply}", "error")
    return {"ok": ok, "action": "remove", "username": username, "reply": reply}
