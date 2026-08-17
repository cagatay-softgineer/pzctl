"""Player moderation: kick, ban and unban through the server's admin commands.

Documented syntax (PZwiki Admin_commands):

    kickuser "username" -r "reason"
    banuser "username" -ip -r "reason"
    unbanuser "username"
    banid SteamID          unbanid SteamID
    banip IP               unbanip IP

Note the kick command is `kickuser`, not `kick`.

Targets are interpolated into a command string, so they are validated rather
than trusted: a quote or a newline in a username would otherwise let a caller
close the argument and append a second command.
"""

from __future__ import annotations

import re

STEAM_ID_RE = re.compile(r"^\d{5,25}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# action -> (needs a name, builds the command)
ACTIONS = ("kick", "ban", "unban", "banid", "unbanid", "banip", "unbanip")


def _clean_text(value: str) -> str:
    """Strip characters that would break out of a quoted argument."""
    return re.sub(r'["\r\n]', "", str(value or "")).strip()


def _validate_name(name: str) -> str | None:
    if not name:
        return "no player name given"
    if len(name) > 64:
        return "player name is implausibly long"
    if re.search(r'["\r\n]', name):
        return "player name contains characters that are not allowed"
    return None


def _validate_steam_id(value: str) -> str | None:
    return None if STEAM_ID_RE.match(value or "") else "expected a numeric Steam ID"


def _validate_ip(value: str) -> str | None:
    if not IPV4_RE.match(value or ""):
        return "expected an IPv4 address"
    if any(int(part) > 255 for part in value.split(".")):
        return "expected an IPv4 address"
    return None


def build_command(action: str, target: str, reason: str = "", ban_ip: bool = False) -> tuple[str | None, str | None]:
    """Return (command, error). Exactly one of the two is set."""
    target = str(target or "").strip()
    reason = _clean_text(reason)

    if action not in ACTIONS:
        return None, f"unknown action {action!r}"

    if action in ("kick", "ban", "unban"):
        problem = _validate_name(target)
        if problem:
            return None, problem
        if action == "unban":
            return f'unbanuser "{target}"', None
        verb = "kickuser" if action == "kick" else "banuser"
        command = f'{verb} "{target}"'
        if action == "ban" and ban_ip:
            command += " -ip"
        if reason:
            command += f' -r "{reason}"'
        return command, None

    if action in ("banid", "unbanid"):
        problem = _validate_steam_id(target)
        if problem:
            return None, problem
        return f"{action} {target}", None

    problem = _validate_ip(target)
    if problem:
        return None, problem
    return f"{action} {target}", None


def act(supervisor, action: str, target: str, reason: str = "", ban_ip: bool = False) -> dict:
    """Run a moderation action against the running server."""
    command, error = build_command(action, target, reason, ban_ip)
    if error:
        return {"ok": False, "error": error}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    # Record the intent before sending, so the audit line exists even if the
    # command itself fails or the connection drops mid-request.
    audit = f"moderation: {action} {target!r}"
    if reason:
        audit += f" - reason: {_clean_text(reason)}"
    if action == "ban" and ban_ip:
        audit += " (with IP ban)"
    supervisor.emit(audit, "pzctl")

    ok, reply = supervisor.send_command(command, prefer="auto")
    if not ok:
        supervisor.emit(f"moderation: {action} {target!r} FAILED - {reply}", "error")
    return {"ok": ok, "action": action, "target": target, "command": command, "reply": reply}
