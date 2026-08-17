"""Update the dedicated server itself, through SteamCMD.

    steamcmd +force_install_dir <dir> +login anonymous +app_update 380870 validate +quit

App 380870 is the dedicated server (108600 is the game, which is why mods.py
uses that one for Workshop content). Anonymous login works - owning the game is
not required.

Two things make this worth doing carefully rather than shelling out and hoping:

- `force_install_dir` pointing somewhere unexpected does not fail. SteamCMD
  quietly installs a second copy there instead of updating the server you meant,
  leaving you with a fresh install and an untouched old one. So the target is
  checked for the markers of a real PZ server install before anything runs.
- An update replaces the server binaries. A backup is taken first, because a
  bad update is exactly when one is wanted and exactly when nobody has one.

The update runs on a background thread and streams SteamCMD's output to the
pzctl console, since it can take several minutes and silence for that long is
indistinguishable from a hang.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import backup
from .config import SERVER_DIR, Config

APP_ID_DEFAULT = "380870"

# Files that mark a directory as a Project Zomboid dedicated server install.
# Any one of them is enough; different platforms and versions ship different sets.
INSTALL_MARKERS = (
    "StartServer64.bat",
    "StartServer64_nosteam.bat",
    "start-server.sh",
    "ProjectZomboid64.json",
    "java",
    "jre64",
)

_state: dict = {"running": False, "started_at": None, "result": None}
_lock = threading.Lock()


def find_steamcmd(cfg: Config) -> Path | None:
    """Locate steamcmd: configured path first, then PATH."""
    configured = str(cfg.get("steamcmd_path") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("steamcmd") or shutil.which("steamcmd.exe")
    return Path(found) if found else None


def looks_like_server_install(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in INSTALL_MARKERS)


def status() -> dict:
    with _lock:
        state = dict(_state)
    if state["running"]:
        state["elapsed_sec"] = round(time.time() - (state["started_at"] or time.time()), 1)
    return {"ok": True, **state}


def _run(cfg: Config, supervisor, steamcmd: Path, install_dir: Path, app_id: str) -> None:
    log = supervisor.emit if supervisor is not None else (lambda *a, **k: None)
    command = [
        str(steamcmd),
        "+force_install_dir",
        str(install_dir),
        "+login",
        "anonymous",
        "+app_update",
        app_id,
        "validate",
        "+quit",
    ]
    log(f"server update: running {steamcmd.name} for app {app_id}", "pzctl")

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in process.stdout or []:
            line = line.rstrip()
            if line:
                log(line, "steamcmd")
        code = process.wait()
    except OSError as exc:
        with _lock:
            _state.update({"running": False, "result": {"ok": False, "error": str(exc)}})
        log(f"server update: failed - {exc}", "error")
        return

    ok = code == 0
    log(
        "server update: finished successfully" if ok else f"server update: FAILED (exit {code})",
        "pzctl" if ok else "error",
    )
    with _lock:
        _state.update(
            {
                "running": False,
                "result": {"ok": ok, "exit_code": code}
                if ok
                else {"ok": False, "exit_code": code, "error": f"SteamCMD exited {code}"},
            }
        )


def start(cfg: Config, supervisor, pre_backup: bool = True) -> dict:
    """Begin an update. Returns once it has been started, not when it finishes."""
    with _lock:
        if _state["running"]:
            return {"ok": False, "error": "an update is already running"}

    if supervisor is not None and supervisor.is_alive():
        return {
            "ok": False,
            "error": "stop the game server before updating it - SteamCMD replaces "
            "files the running server has open",
        }

    steamcmd = find_steamcmd(cfg)
    if steamcmd is None:
        return {
            "ok": False,
            "error": "steamcmd not found - install it and put it on PATH, or set "
            "steamcmd_path in pzctl.json",
        }

    install_dir = SERVER_DIR
    if not looks_like_server_install(install_dir):
        # Refusing here is the whole point: a wrong directory does not error,
        # it silently produces a second install somewhere useless.
        return {
            "ok": False,
            "error": f"{install_dir} does not look like a Project Zomboid server "
            "install - refusing to point SteamCMD at it",
        }

    safety = None
    if pre_backup:
        result = backup.run(cfg, supervisor=None, reason="pre-update")
        if result.get("ok"):
            safety = result["name"]
        elif "no save folder" not in str(result.get("error", "")):
            # A missing world is fine on a fresh install; anything else is not.
            return {"ok": False, "error": f"aborted - backup failed: {result.get('error')}"}

    app_id = str(cfg.get("steam_app_id") or APP_ID_DEFAULT).strip() or APP_ID_DEFAULT
    with _lock:
        _state.update({"running": True, "started_at": time.time(), "result": None})

    threading.Thread(
        target=_run,
        args=(cfg, supervisor, steamcmd, install_dir, app_id),
        name="pz-steamcmd",
        daemon=True,
    ).start()

    return {
        "ok": True,
        "started": True,
        "steamcmd": str(steamcmd),
        "install_dir": str(install_dir),
        "app_id": app_id,
        "safety_backup": safety,
    }


def reset() -> None:
    with _lock:
        _state.update({"running": False, "started_at": None, "result": None})
