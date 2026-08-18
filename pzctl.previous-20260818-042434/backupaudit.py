"""Reconcile pzctl's backups with the game's own.

Project Zomboid backs up independently of pzctl, configured in the server .ini:

    BackupsOnStart          true by default
    BackupsOnVersionChange  true by default
    BackupsPeriod           minutes, 0 = off
    BackupsCount            how many the game keeps

`BackupsOnStart` defaulting to **true** is the one that surprises people: every
pzctl-initiated restart already triggers a game-side backup, and with scheduled
restarts that compounds. Two independent retention policies then prune two sets
of archives, and neither knows about the other, so a backup folder grows faster
than either setting explains.

This reports both configurations side by side and names the overlaps, rather
than changing anything - which of the two an admin wants to keep is a judgement
about their own disk and risk.
"""

from __future__ import annotations

from . import backup, pzini
from .config import Config

GAME_KEYS = {
    "BackupsOnStart": ("true", "a backup every time the server starts"),
    "BackupsOnVersionChange": ("true", "a backup when the game version changes"),
    "BackupsPeriod": ("0", "a backup every N minutes while running"),
    "BackupsCount": ("5", "how many backups the game keeps"),
}


def _is_true(value: str | None, default: str) -> bool:
    return str(value if value is not None else default).strip().lower() == "true"


def audit(cfg: Config) -> dict:
    """Describe both backup systems and where they overlap."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    values = pzini.read(cfg.ini_path)
    game = {}
    for key, (default, describes) in GAME_KEYS.items():
        raw = values.get(key)
        game[key] = {
            "value": raw if raw is not None else default,
            "explicit": raw is not None,
            "describes": describes,
        }

    restarts = [job for job in (cfg.get("schedule.restarts") or []) if job.get("enabled", True)]
    backups = [job for job in (cfg.get("schedule.backups") or []) if job.get("enabled", True)]

    try:
        period = int(str(game["BackupsPeriod"]["value"]).strip() or 0)
    except ValueError:
        period = 0

    notes: list[dict] = []

    if _is_true(game["BackupsOnStart"]["value"], "true") and restarts:
        notes.append(
            {
                "level": "warning",
                "message": (
                    f"BackupsOnStart is on and you have {len(restarts)} scheduled restart(s), "
                    "so the game writes a backup on every one of them, on top of anything "
                    "pzctl does"
                ),
            }
        )
    if period > 0 and backups:
        notes.append(
            {
                "level": "warning",
                "message": (
                    f"BackupsPeriod is {period} minutes and pzctl has {len(backups)} scheduled "
                    "backup(s) - both are backing up the same world on their own timers"
                ),
            }
        )
    if not backups and not period and not _is_true(game["BackupsOnStart"]["value"], "true"):
        notes.append(
            {
                "level": "warning",
                "message": "nothing is backing this world up automatically - neither pzctl nor the game",
            }
        )

    return {
        "ok": True,
        "game": game,
        "pzctl": {
            "scheduled_backups": len(backups),
            "scheduled_restarts": len(restarts),
            "retention": cfg.get("backup.retention"),
            "dir": str(cfg.backup_dir),
            "archives": len(backup.listing(cfg)),
        },
        "notes": notes,
    }
