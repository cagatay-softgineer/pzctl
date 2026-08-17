"""Catalogs read from the installed game's own data files.

Everything here is parsed from the game at request time, following the pattern
in `optionmeta.py`, so it tracks whatever build and mods are installed rather
than a list baked into pzctl.

Findings from inspecting a real B42 dedicated server install, which decided the
shape of this module:

- **Item scripts live at `media/scripts/**/*.txt`**, nested under `entities/`
  and `generated/` - not the flat `media/scripts/*.txt` layout older guides
  describe. Blocks look like `module Base { item Axe { DisplayName = Axe, } }`.

- **A dedicated server ships no item icons.** There is no `media/textures/`
  directory, no `Item_*.png`, and no `.pack` archives - the scripts reference
  `Icon = Something` but the textures themselves are absent, because a headless
  server never renders anything. Icons are therefore not offered here. They are
  not locked behind a format that cannot be read; they simply are not present.

- **Perk ids come from `Translate/EN/IG_UI.json`**, as `IGUI_perks_<id>` keys.
  The ids differ from their labels in ways nobody would guess: `Blunt` is
  "Long Blunt", `Sprinting` is "Running", `Doctor` is "First Aid". B42 carries
  both `Woodwork` and `Carpentry`, both labelled "Carpentry", and has no
  `PlantScavenging` at all - it is `Foraging` now. Any hardcoded list would be
  wrong on at least one of those.

Parsing 1000+ script files is too slow to repeat per request, so results are
cached against the newest modification time under the scripts directory: edit a
script or install a mod and the cache rebuilds, otherwise it is reused.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import SERVER_DIR

SCRIPTS_DIR = SERVER_DIR / "media" / "scripts"
TRANSLATE_DIR = SERVER_DIR / "media" / "lua" / "shared" / "Translate" / "EN"

MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z0-9_]+)")
ITEM_RE = re.compile(r"^\s*item\s+([A-Za-z0-9_.]+)\s*$")
DISPLAY_RE = re.compile(r"^\s*DisplayName\s*=\s*(.+?)\s*,?\s*$")
TYPE_RE = re.compile(r"^\s*Type\s*=\s*(.+?)\s*,?\s*$")
PERK_RE = re.compile(r'"IGUI_perks_([A-Za-z0-9_]+)"\s*:\s*"([^"]*)"')
# Display names are keyed by full item id: "Base.Axe": "Axe".
ITEM_NAME_RE = re.compile(r'"([A-Za-z0-9_]+\.[A-Za-z0-9_]+)"\s*:\s*"([^"]*)"')

_cache: dict = {"items": None, "stamp": None}


def _scripts_stamp() -> float | None:
    """Newest mtime under the scripts tree, or None if it is not there."""
    if not SCRIPTS_DIR.is_dir():
        return None
    newest = 0.0
    for path in SCRIPTS_DIR.rglob("*.txt"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _display_names() -> dict[str, str]:
    """Item id -> the name players see, from the game's own translations.

    The scripts mostly do not carry DisplayName for items; that lives in
    ItemName.json, keyed by the full id.
    """
    path = TRANSLATE_DIR / "ItemName.json"
    if not path.is_file():
        return {}
    try:
        return dict(ITEM_NAME_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return {}


def _parse_items() -> list[dict]:
    """Walk the script files and pull out every item declaration."""
    found: list[dict] = []
    if not SCRIPTS_DIR.is_dir():
        return found

    for path in sorted(SCRIPTS_DIR.rglob("*.txt")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        module = ""
        item = None
        display = ""
        item_type = ""
        depth_at_item = None
        opened = False
        depth = 0

        for line in lines:
            match = MODULE_RE.match(line)
            if match and item is None:
                module = match.group(1)

            match = ITEM_RE.match(line)
            if match:
                item = match.group(1)
                display = ""
                item_type = ""
                depth_at_item = depth
                opened = False

            if item is not None:
                match = DISPLAY_RE.match(line)
                if match:
                    display = match.group(1)
                match = TYPE_RE.match(line)
                if match:
                    item_type = match.group(1)

            depth += line.count("{") - line.count("}")
            if item is not None and depth > (depth_at_item or 0):
                # The item's own brace has been seen; its body can now close.
                opened = True

            # The item block closed - record it.
            if item is not None and opened and depth <= (depth_at_item or 0):
                full = f"{module}.{item}" if module else item
                found.append(
                    {
                        "id": full,
                        "module": module,
                        "name": display or item,
                        "type": item_type,
                    }
                )
                item = None
                depth_at_item = None

    names = _display_names()
    for entry in found:
        # Prefer the translated name; fall back to the script's DisplayName,
        # then the raw id, so a modded item still shows something usable.
        entry["name"] = names.get(entry["id"]) or entry["name"]

    # A module can be reopened across files; keep the first definition of an id.
    seen: set[str] = set()
    unique = []
    for entry in found:
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        unique.append(entry)
    return sorted(unique, key=lambda e: e["name"].lower())


def items(search: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Search the item catalog. Cached until the scripts change on disk."""
    if not SCRIPTS_DIR.is_dir():
        return {
            "ok": False,
            "error": f"no game scripts at {SCRIPTS_DIR} - is pzctl inside the server directory?",
        }

    stamp = _scripts_stamp()
    if _cache["items"] is None or _cache["stamp"] != stamp:
        _cache["items"] = _parse_items()
        _cache["stamp"] = stamp

    catalog = _cache["items"]
    needle = str(search or "").strip().lower()
    if needle:
        catalog = [
            entry
            for entry in catalog
            if needle in entry["name"].lower() or needle in entry["id"].lower()
        ]

    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    return {
        "ok": True,
        "total": len(catalog),
        "offset": offset,
        "items": catalog[offset : offset + limit],
    }


def perks() -> dict:
    """Skill ids and their display names, from the game's own translations."""
    path = TRANSLATE_DIR / "IG_UI.json"
    if not path.is_file():
        return {
            "ok": False,
            "error": f"no translations at {path} - is pzctl inside the server directory?",
        }
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    found = dict(PERK_RE.findall(raw))
    # IGUI_perks_<id>_Description entries describe a perk rather than naming one.
    entries = [
        {
            "id": key,
            "name": label,
            "description": found.get(f"{key}_Description", ""),
            "differs": key.lower() != label.lower().replace(" ", ""),
        }
        for key, label in found.items()
        if not key.endswith("_Description")
    ]
    entries.sort(key=lambda e: e["name"].lower())
    return {"ok": True, "perks": entries, "total": len(entries)}


def reset_cache() -> None:
    _cache.update({"items": None, "stamp": None})
