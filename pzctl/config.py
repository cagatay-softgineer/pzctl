"""Daemon configuration: pzctl.json, loaded with defaults and dotted-path access."""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SERVER_DIR / "pzctl.json"
DATA_DIR = SERVER_DIR / "pzctl-data"
LOG_DIR = DATA_DIR / "logs"


def user_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def defaults() -> dict:
    zomboid = user_dir() / "Zomboid"
    return {
        "server_name": "servertest",
        "zomboid_dir": str(zomboid),
        "cache_dir": "",
        "admin_username": "admin",
        "admin_password": "",
        "steam": True,
        # Empty means "find steamcmd on PATH". App 380870 is the dedicated server.
        # Workshop folder mtimes recorded at the last server start.
        "mods_seen": {},
        "steamcmd_path": "",
        "steam_app_id": "380870",
        "java": {
            "xms": "16g",
            "xmx": "16g",
            "gc": "-XX:+UseZGC",
            "extra_args": [],
        },
        "http": {"host": "127.0.0.1", "port": 8077, "token": ""},
        "supervisor": {
            "autostart": False,
            "auto_restart": True,
            "max_restarts": 5,
            "restart_window_sec": 900,
            "backoff_sec": [5, 15, 30, 60, 120],
            "stop_timeout_sec": 90,
        },
        "rcon": {"enabled": True, "host": "127.0.0.1", "port": 27015, "password": ""},
        # DebugType categories for -debuglog / -disablelog at launch.
        "logging": {"debug": [], "disable": []},
        # Each entry: {"time": "HH:MM", "enabled": true, "warn_minutes": [...]}
        "schedule": {"restarts": [], "backups": []},
        "backup": {
            "dir": str(zomboid / "Backups"),
            "retention": 14,
            "include_config": True,
        },
    }


def _merge(base: dict, override: Any) -> Any:
    """Recursively fill `override` with anything missing from `base`."""
    if not isinstance(override, dict):
        return base if override is None else override
    out = dict(base)
    for key, value in override.items():
        if key in base and isinstance(base[key], dict):
            out[key] = _merge(base[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Thread-safe view over pzctl.json."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.data = defaults()
        self.load()

    def load(self) -> dict:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raw = {}
                self.data = _merge(defaults(), raw)
            else:
                self.data = defaults()
            # A token is minted once and reused, so panel sessions survive restarts.
            if not self.data["http"]["token"]:
                self.data["http"]["token"] = secrets.token_urlsafe(24)
                self.save()
            return self.data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def update(self, patch: dict) -> None:
        with self._lock:
            self.data = _merge(self.data, patch)
            self.save()

    # -- Derived paths ---------------------------------------------------

    @property
    def zomboid_dir(self) -> Path:
        return Path(self.get("zomboid_dir"))

    @property
    def server_config_dir(self) -> Path:
        return self.zomboid_dir / "Server"

    @property
    def ini_path(self) -> Path:
        return self.server_config_dir / f"{self.get('server_name')}.ini"

    @property
    def sandbox_path(self) -> Path:
        return self.server_config_dir / f"{self.get('server_name')}_SandboxVars.lua"

    @property
    def spawnregions_path(self) -> Path:
        return self.server_config_dir / f"{self.get('server_name')}_spawnregions.lua"

    @property
    def save_dir(self) -> Path:
        return self.zomboid_dir / "Saves" / "Multiplayer" / str(self.get("server_name"))

    @property
    def backup_dir(self) -> Path:
        return Path(self.get("backup.dir"))
