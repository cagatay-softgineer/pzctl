"""Post daemon events to a Discord webhook.

This is pzctl's own notification path and is unrelated to the game's built-in
Discord bridge (`DiscordEnable` and friends in the server .ini), which relays
in-game chat. This one reports things only pzctl knows: the server crashed, a
backup finished, a scheduled restart is coming.

Off unless a webhook URL is configured. It is the second outbound request pzctl
can make - the first being the update check - and like that one it never runs on
its own initiative: something has to happen first.

Delivery is best-effort and never raises. A notification failing must not take
down a restart or a backup; the point of the message was that something already
happened.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from .config import Config

TIMEOUT_SEC = 10
MAX_CONTENT = 1900  # Discord rejects messages over 2000 characters.


def configured(cfg: Config) -> bool:
    return bool(str(cfg.get("notify.discord_webhook") or "").strip())


def _post(url: str, content: str) -> dict:
    payload = json.dumps({"content": content}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "pzctl"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"Discord returned HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "error": "could not reach Discord"}


def send(cfg: Config, message: str, blocking: bool = False) -> dict:
    """Post a message. Silently does nothing when no webhook is configured."""
    url = str(cfg.get("notify.discord_webhook") or "").strip()
    if not url:
        return {"ok": True, "skipped": "no webhook configured"}
    if not url.startswith("https://"):
        return {"ok": False, "error": "the webhook URL must start with https://"}

    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "no message"}
    # Truncating here rather than at the transport keeps it a property of the
    # message, so every path that sends one is bounded the same way.
    text = text[:MAX_CONTENT]

    if blocking:
        return _post(url, text)

    # Fire and forget: the caller is usually mid-restart or mid-backup and must
    # not wait on, or fail because of, a chat notification.
    threading.Thread(
        target=lambda: _post(url, text), name="pz-notify", daemon=True
    ).start()
    return {"ok": True, "queued": True}


def event(cfg: Config, kind: str, detail: str = "") -> dict:
    """Send one of the events an admin asked to hear about."""
    wanted = cfg.get("notify.events") or {}
    if kind in wanted and not wanted[kind]:
        return {"ok": True, "skipped": f"{kind} notifications are off"}

    server = cfg.get("server_name")
    prefixes = {
        "started": "🟢",
        "stopped": "⚪",
        "crashed": "🔴",
        "backup": "💾",
        "restart_warning": "⏰",
    }
    mark = prefixes.get(kind, "•")
    body = f"{mark} **{server}** {kind.replace('_', ' ')}"
    if detail:
        body += f" — {detail}"
    return send(cfg, body)
