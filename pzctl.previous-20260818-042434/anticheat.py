"""Group the server's anti-cheat toggles so they can be managed together.

The setting is `AntiCheatProtectionType1` through `AntiCheatProtectionType24`,
each a boolean defaulting to true. There is no severity policy - a type is
either enforced or it is not.

The practical need this serves is narrow and specific: a mod trips one of the
checks and the admin has to turn that one off. Scattered through a list of 135
server options they are tedious to find; together, with the documented ones
labelled, they are manageable.

Only the types whose meaning is actually documented carry a description. No
complete published mapping of all 24 exists, and inventing plausible labels
would be worse than leaving numbers bare - an admin would disable the wrong
check believing they had disabled the right one.
"""

from __future__ import annotations

from . import pzini
from .config import Config

COUNT = 24
PREFIX = "AntiCheatProtectionType"

# Attested in community troubleshooting. Everything else is deliberately blank.
KNOWN: dict[int, str] = {
    12: "Lua checksum mismatch - commonly tripped by mods",
    21: "Malformed packet / packet checksum - commonly tripped by mods",
}

# The types most often turned off to get a modded server running.
MOD_FRIENDLY = (12, 21)


def key_for(number: int) -> str:
    return f"{PREFIX}{number}"


def _is_enabled(raw: str | None) -> bool:
    # Absent means default, and the default is on.
    if raw is None:
        return True
    return str(raw).strip().lower() != "false"


def read(cfg: Config) -> dict:
    """Report every anti-cheat type and whether it is enforced."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    values = pzini.read(cfg.ini_path)
    types = []
    for number in range(1, COUNT + 1):
        raw = values.get(key_for(number))
        types.append(
            {
                "number": number,
                "key": key_for(number),
                "enabled": _is_enabled(raw),
                "explicit": raw is not None,
                "description": KNOWN.get(number, ""),
                "mod_friendly": number in MOD_FRIENDLY,
            }
        )
    return {
        "ok": True,
        "types": types,
        "enabled_count": sum(1 for entry in types if entry["enabled"]),
        "total": COUNT,
    }


def write(cfg: Config, changes: dict) -> dict:
    """Set anti-cheat types. `changes` maps a type number to a boolean."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    patch: dict[str, str] = {}
    for number, enabled in (changes or {}).items():
        try:
            index = int(number)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"not an anti-cheat type: {number!r}"}
        if not 1 <= index <= COUNT:
            return {"ok": False, "error": f"anti-cheat type out of range: {index}"}
        patch[key_for(index)] = "true" if enabled else "false"

    if not patch:
        return {"ok": True, "changed": []}

    changed = pzini.write(cfg.ini_path, patch)
    return {"ok": True, "changed": changed}
