"""Apply server .ini changes to a running server, without a restart.

Project Zomboid documents two admin commands for this:

    changeoption optionName "newValue"    change a server option
    reloadoptions                         reload server options and send to clients

What is *not* documented is whether `reloadoptions` re-reads the ini from disk
or merely re-broadcasts what is already in memory. This module is deliberately
correct either way: the file is written first (by the caller, through the
line-preserving writer), then every changed key is pushed with `changeoption`,
then `reloadoptions` runs once. Disk and memory therefore agree before the
reload happens, so neither interpretation can undo the other.

Which options actually take effect live is decided by the server, not by a
list maintained here - each `changeoption` reply is reported back verbatim. The
only keys refused up front are ones consumed while the process is starting,
where a live change is meaningless rather than merely unsupported.
"""

from __future__ import annotations

from .config import Config

# Keys consumed during startup, where applying live cannot work regardless of
# what the server would say. Sockets are bound and content is loaded once, at
# boot. Kept deliberately short: anything not obviously in this category is
# sent to the server so it can answer for itself.
BOOT_ONLY = frozenset(
    {
        # Sockets bound at startup
        "DefaultPort",
        "UDPPort",
        "RCONPort",
        "RCONPassword",
        "UPnP",
        "IP",
        # Content loaded at startup
        "Mods",
        "WorkshopItems",
        "Map",
    }
)


def _quote(value: str) -> str:
    """Wrap a value for the changeoption argument."""
    return '"' + str(value).replace('"', '\\"') + '"'


def apply(cfg: Config, supervisor, changes: dict[str, str]) -> dict:
    """Push `changes` to the running server.

    `changes` should be the keys that actually changed on disk, so a save that
    altered nothing does not generate traffic.
    """
    if not changes:
        return {"ok": True, "applied": [], "restart_required": [], "failed": [], "reloaded": False}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running - changes apply at next start"}

    # changeoption replies are the whole point, and stdin gives us nothing back,
    # so this path requires RCON rather than silently falling back.
    if not supervisor.rcon_ready():
        return {
            "ok": False,
            "error": "RCON is not configured - live apply needs it to report results",
        }

    applied: list[dict] = []
    failed: list[dict] = []
    restart_required = sorted(key for key in changes if key in BOOT_ONLY)

    for key, value in sorted(changes.items()):
        if key in BOOT_ONLY:
            continue
        ok, reply = supervisor.send_command(
            f"changeoption {key} {_quote(value)}", prefer="rcon"
        )
        if ok:
            applied.append({"key": key, "value": str(value), "reply": (reply or "").strip()})
        else:
            failed.append({"key": key, "error": reply})

    reloaded = False
    reload_reply = ""
    if applied:
        ok, reply = supervisor.send_command("reloadoptions", prefer="rcon")
        reloaded = ok
        reload_reply = (reply or "").strip()
        if not ok:
            failed.append({"key": "reloadoptions", "error": reply})

    return {
        "ok": not failed,
        "applied": applied,
        "restart_required": restart_required,
        "failed": failed,
        "reloaded": reloaded,
        "reload_reply": reload_reply,
    }
