"""Named server profiles - keep a test world alongside a production one.

Project Zomboid's `-servername <name>` selects a whole set of files:

    Zomboid/Server/<name>.ini
    Zomboid/Server/<name>_SandboxVars.lua
    Zomboid/Server/<name>_spawnpoints.lua
    Zomboid/Server/<name>_spawnregions.lua
    Zomboid/Saves/Multiplayer/<name>/

`Config` already derives every one of those paths from `server_name`, so
switching profiles is a single settings change and moves nothing on disk.
Nothing here renames or deletes files; the risky operations the issue warned
about are not needed to switch, and are deliberately not offered:

- **Renaming** a profile means renaming every file in the set. Miss one and PZ
  does not error - it generates fresh defaults under the new name, which looks
  exactly like the world was wiped.
- **Deleting** a profile means deleting a world.

Both are better done deliberately in a file manager than behind a button.

What does need care is that a profile name is a filename. It is validated, and
switching to a name with no files is reported as creating a new profile rather
than presented as if an existing world were being opened.
"""

from __future__ import annotations

import re
import shutil

from .config import Config

# suffix -> what it holds. The ini is what marks a profile as existing.
PROFILE_SUFFIXES = {
    "": "ini",
    "_SandboxVars.lua": "sandbox",
    "_spawnpoints.lua": "spawnpoints",
    "_spawnregions.lua": "spawnregions",
}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


def validate_name(name: str) -> str | None:
    """A profile name becomes a filename, so it is checked rather than trusted."""
    name = str(name or "").strip()
    if not name:
        return "no profile name given"
    if not NAME_RE.match(name):
        return (
            "a profile name may only contain letters, digits, spaces, "
            "hyphens and underscores"
        )
    return None


def _files_for(cfg: Config, name: str) -> dict[str, object]:
    directory = cfg.server_config_dir
    found = {}
    for suffix, label in PROFILE_SUFFIXES.items():
        path = directory / (f"{name}.ini" if label == "ini" else f"{name}{suffix}")
        found[label] = path.is_file()
    return found


def discover(cfg: Config) -> dict:
    """List every profile that has config on disk, plus the one in use."""
    current = str(cfg.get("server_name") or "")
    directory = cfg.server_config_dir
    names: list[str] = []
    if directory.is_dir():
        names = sorted(path.stem for path in directory.glob("*.ini") if path.is_file())

    # The configured profile belongs in the list even before its first boot.
    if current and current not in names:
        names.append(current)

    saves_root = cfg.zomboid_dir / "Saves" / "Multiplayer"
    profiles = []
    for name in names:
        files = _files_for(cfg, name)
        profiles.append(
            {
                "name": name,
                "current": name == current,
                "files": files,
                "has_config": bool(files["ini"]),
                "has_save": (saves_root / name).is_dir(),
            }
        )

    return {
        "ok": True,
        "current": current,
        "profiles": profiles,
        "server_dir": str(directory),
    }


def switch(cfg: Config, supervisor, name: str) -> dict:
    """Point pzctl at a different profile. Moves nothing."""
    problem = validate_name(name)
    if problem:
        return {"ok": False, "error": problem}
    name = name.strip()

    if supervisor is not None and supervisor.is_alive():
        return {
            "ok": False,
            "error": "stop the server before switching profiles - it is running "
            "the current world",
        }

    existing = {entry["name"] for entry in discover(cfg)["profiles"]}
    files = _files_for(cfg, name)

    cfg.set("server_name", name)
    cfg.save()

    return {
        "ok": True,
        "current": name,
        "existed": name in existing and bool(files["ini"]),
        # Said plainly, because a typo here looks like a lost world rather than
        # a new profile.
        "note": (
            ""
            if files["ini"]
            else f"{name} has no config yet - the server will generate a fresh "
            "world and default settings the first time it starts"
        ),
    }


def create(cfg: Config, name: str, copy_from: str | None = None) -> dict:
    """Create a profile, optionally copying an existing one's settings.

    Only config files are copied. The world itself is not: duplicating a save
    is a large, slow copy that is rarely what someone wants from a button, and
    a fresh profile normally wants a fresh world.
    """
    problem = validate_name(name)
    if problem:
        return {"ok": False, "error": problem}
    name = name.strip()

    directory = cfg.server_config_dir
    if (directory / f"{name}.ini").exists():
        return {"ok": False, "error": f"a profile named {name} already exists"}

    copied: list[str] = []
    if copy_from:
        problem = validate_name(copy_from)
        if problem:
            return {"ok": False, "error": f"source profile: {problem}"}
        copy_from = copy_from.strip()
        if not (directory / f"{copy_from}.ini").is_file():
            return {"ok": False, "error": f"no profile named {copy_from} to copy from"}

        directory.mkdir(parents=True, exist_ok=True)
        for suffix, label in PROFILE_SUFFIXES.items():
            source = directory / (
                f"{copy_from}.ini" if label == "ini" else f"{copy_from}{suffix}"
            )
            if not source.is_file():
                continue
            target = directory / (f"{name}.ini" if label == "ini" else f"{name}{suffix}")
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                return {"ok": False, "error": f"could not copy {source.name}: {exc}"}
            copied.append(target.name)

    return {
        "ok": True,
        "name": name,
        "copied": copied,
        "note": (
            f"copied settings from {copy_from}; the world itself is not copied"
            if copied
            else f"{name} will be generated with default settings on first start"
        ),
    }
