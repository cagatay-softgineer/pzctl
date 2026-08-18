"""Checks over the mod configuration, and an offline update signal.

Two things that are easy to get wrong and only show up at boot:

- A `Map=` entry whose mod is not in `Mods=`. Project Zomboid does not report
  this usefully; it fails at startup, often with a null error naming nothing.
- A `Mods=` or `WorkshopItems=` entry with nothing installed to match it.

And one thing that is invisible entirely: a Workshop mod updated on disk since
the server last started. Host and client desync when that happens, and the fix
is a restart. pzctl makes no network calls, so this compares folder
modification times against a snapshot taken at the last start rather than
asking Steam - a weaker signal than the server's own `checkModsNeedUpdate`
(see modcheck.py), but one that works with the server stopped and RCON off.
"""

from __future__ import annotations

from pathlib import Path

from . import mods
from .config import Config

# The base game map, which no mod provides.
VANILLA_MAPS = {"Muldraugh, KY"}


def check(cfg: Config) -> dict:
    """Look for mod configuration that will fail at boot."""
    data = mods.read(cfg)
    if not data["ini_exists"]:
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    active = [str(m) for m in data["mods"]]
    active_lower = {m.lower() for m in active}
    installed = data["installed"]

    by_mod_id = {str(entry["mod_id"]).lower(): entry for entry in installed}
    installed_workshop = {str(entry["workshop_id"]) for entry in installed}
    # Which installed mod provides each map folder.
    map_providers: dict[str, list[dict]] = {}
    for entry in installed:
        for name in entry.get("maps") or []:
            map_providers.setdefault(name.lower(), []).append(entry)

    problems: list[dict] = []

    for map_name in data["map"]:
        if map_name in VANILLA_MAPS:
            continue
        providers = map_providers.get(map_name.lower(), [])
        if not providers:
            problems.append(
                {
                    "level": "warning",
                    "subject": map_name,
                    "message": f"map '{map_name}' is in Map= but no installed mod provides it",
                }
            )
            continue
        if not any(str(p["mod_id"]).lower() in active_lower for p in providers):
            names = ", ".join(sorted({str(p["mod_id"]) for p in providers}))
            problems.append(
                {
                    "level": "error",
                    "subject": map_name,
                    "message": (
                        f"map '{map_name}' is in Map= but its mod ({names}) is not in Mods= - "
                        "the server will fail to start"
                    ),
                }
            )

    for mod_id in active:
        if mod_id.lower() not in by_mod_id:
            problems.append(
                {
                    "level": "warning",
                    "subject": mod_id,
                    "message": f"'{mod_id}' is in Mods= but is not installed in the Workshop folder",
                }
            )

    for workshop_id in data["workshop_items"]:
        if str(workshop_id) not in installed_workshop:
            problems.append(
                {
                    "level": "warning",
                    "subject": str(workshop_id),
                    "message": (
                        f"Workshop item {workshop_id} is listed but not downloaded yet - "
                        "the server fetches it on next start"
                    ),
                }
            )

    return {
        "ok": True,
        "problems": problems,
        "errors": sum(1 for p in problems if p["level"] == "error"),
        "warnings": sum(1 for p in problems if p["level"] == "warning"),
    }


def _current_stamps() -> dict[str, float]:
    """Newest modification time inside each installed Workshop item."""
    stamps: dict[str, float] = {}
    root = mods.WORKSHOP_ROOT
    if not root.is_dir():
        return stamps
    for workshop_dir in root.iterdir():
        if not workshop_dir.is_dir():
            continue
        newest = 0.0
        for path in workshop_dir.rglob("*"):
            try:
                if path.is_file():
                    newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
        if newest:
            stamps[workshop_dir.name] = newest
    return stamps


def snapshot(cfg: Config) -> dict:
    """Record the current state as 'seen', normally at server start."""
    stamps = _current_stamps()
    cfg.set("mods_seen", {key: round(value, 3) for key, value in stamps.items()})
    cfg.save()
    return {"ok": True, "tracked": len(stamps)}


def updates(cfg: Config) -> dict:
    """Report Workshop items whose files changed since the last snapshot."""
    seen = cfg.get("mods_seen") or {}
    stamps = _current_stamps()
    if not stamps:
        return {"ok": True, "checked": False, "changed": [], "note": "no Workshop content found"}
    if not seen:
        return {
            "ok": True,
            "checked": False,
            "changed": [],
            "note": "no baseline recorded yet - it is taken when the server starts",
        }

    installed = {str(e["workshop_id"]): e for e in mods.discover_installed()}
    changed = []
    for workshop_id, stamp in sorted(stamps.items()):
        previous = seen.get(workshop_id)
        if previous is None:
            changed.append({"workshop_id": workshop_id, "reason": "newly installed"})
        elif stamp > float(previous) + 1:
            changed.append({"workshop_id": workshop_id, "reason": "files changed"})
    for entry in changed:
        found = installed.get(entry["workshop_id"])
        entry["name"] = found["name"] if found else entry["workshop_id"]

    return {
        "ok": True,
        "checked": True,
        "changed": changed,
        "note": (
            "Compares file times against the last server start. It cannot tell a "
            "Workshop update from any other change on disk - ask the server with "
            "the mod update check when it is running."
        ),
    }
