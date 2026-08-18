"""Mod list management: the ini's Mods/WorkshopItems/Map plus discovery of installed Workshop mods."""

from __future__ import annotations

import re
from pathlib import Path

from . import pzini
from .config import SERVER_DIR, Config

# ...\steamapps\common\Project Zomboid Dedicated Server -> ...\steamapps\workshop\content\108600
WORKSHOP_ROOT = SERVER_DIR.parent.parent / "workshop" / "content" / "108600"
KV_RE = re.compile(r"^\s*(?P<key>[A-Za-z_]+)\s*=\s*(?P<val>.*?)\s*$")


def _read_mod_info(info_path: Path) -> dict:
    data: dict[str, str] = {}
    try:
        text = info_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        match = KV_RE.match(line)
        if match:
            key = match.group("key").lower()
            if key not in data:  # first wins; mod.info sometimes repeats keys
                data[key] = match.group("val")
    return data


def discover_installed() -> list[dict]:
    """Scan the Steam Workshop content folder for mods available to this server."""
    found: list[dict] = []
    if not WORKSHOP_ROOT.is_dir():
        return found
    for workshop_dir in sorted(WORKSHOP_ROOT.iterdir()):
        if not workshop_dir.is_dir():
            continue
        mods_root = workshop_dir / "mods"
        if not mods_root.is_dir():
            continue
        for mod_dir in sorted(mods_root.iterdir()):
            info = mod_dir / "mod.info"
            if not info.is_file():
                continue
            meta = _read_mod_info(info)
            found.append(
                {
                    "workshop_id": workshop_dir.name,
                    "mod_id": meta.get("id") or mod_dir.name,
                    "name": meta.get("name") or mod_dir.name,
                    "description": meta.get("description", ""),
                    "path": str(mod_dir),
                    "maps": sorted(
                        p.name for p in (mod_dir / "media" / "maps").iterdir()
                    )
                    if (mod_dir / "media" / "maps").is_dir()
                    else [],
                }
            )
    return found


def read(cfg: Config) -> dict:
    values = pzini.read(cfg.ini_path)
    return {
        "ini_exists": cfg.ini_path.exists(),
        "ini_path": str(cfg.ini_path),
        "mods": pzini.split_list(values.get("Mods", "")),
        "workshop_items": pzini.split_list(values.get("WorkshopItems", "")),
        "map": pzini.split_list(values.get("Map", "Muldraugh, KY")),
        "installed": discover_installed(),
        "workshop_root": str(WORKSHOP_ROOT),
    }


def write(cfg: Config, mods: list[str], workshop_items: list[str], maps: list[str]) -> list[str]:
    changes = {
        "Mods": pzini.join_list(mods),
        "WorkshopItems": pzini.join_list(workshop_items),
    }
    if maps:
        changes["Map"] = pzini.join_list(maps)
    return pzini.write(cfg.ini_path, changes)
