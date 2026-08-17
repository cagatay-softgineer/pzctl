"""World backups: save the world, zip the save folder (+ config), prune old archives.

Restoring is the inverse and is destructive, so it is deliberately more
defensive than the rest of this module: the archive is validated before
anything is touched, the current world is backed up first, and the swap into
place is a rename that can be rolled back.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import notify
from .config import Config

SAVES_PREFIX = "Saves/"
CONFIG_PREFIX = "Server/"


def _add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str) -> int:
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            zf.write(path, f"{arc_prefix}/{path.relative_to(root).as_posix()}")
            count += 1
        except (OSError, ValueError):
            # A file the server is actively writing can vanish mid-walk; skip it.
            continue
    return count


def _unique_archive_path(cfg: Config, backup_dir: Path) -> Path:
    """Pick an archive name that does not already exist.

    The timestamp only has second resolution, so two backups in the same
    second - a scheduled one racing a manual one, or the safety backup taken
    during a restore - would otherwise silently overwrite each other.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{cfg.get('server_name')}-{stamp}"
    target = backup_dir / f"{base}.zip"
    counter = 2
    while target.exists():
        target = backup_dir / f"{base}-{counter}.zip"
        counter += 1
    return target


def run(cfg: Config, supervisor=None, reason: str = "manual") -> dict:
    save_dir = cfg.save_dir
    if not save_dir.is_dir():
        return {"ok": False, "error": f"no save folder at {save_dir}"}

    log = supervisor.emit if supervisor is not None else (lambda *a, **k: None)

    if supervisor is not None and supervisor.state == "running":
        log(f"backup ({reason}): flushing world to disk", "pzctl")
        supervisor.send_command("save")
        time.sleep(5)

    backup_dir = cfg.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_archive_path(cfg, backup_dir)

    started = time.time()
    files = 0
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            files += _add_tree(zf, save_dir, "Saves")
            if cfg.get("backup.include_config", True):
                for path in (cfg.ini_path, cfg.sandbox_path, cfg.spawnregions_path):
                    if path.is_file():
                        zf.write(path, f"Server/{path.name}")
                        files += 1
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    pruned = prune(cfg)
    result = {
        "ok": True,
        "file": str(target),
        "name": target.name,
        "size_mb": round(target.stat().st_size / (1024 * 1024), 2),
        "files": files,
        "seconds": round(time.time() - started, 1),
        "pruned": pruned,
    }
    log(
        f"backup ({reason}): {target.name} - {result['size_mb']} MB, "
        f"{files} files in {result['seconds']}s",
        "pzctl",
    )
    notify.event(cfg, "backup", f"{target.name} ({result['size_mb']} MB, {reason})")
    return result


def prune(cfg: Config) -> list[str]:
    retention = int(cfg.get("backup.retention", 14))
    if retention <= 0:
        return []
    archives = sorted(
        cfg.backup_dir.glob(f"{cfg.get('server_name')}-*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in archives[retention:]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


def resolve_archive(cfg: Config, name: str) -> Path | None:
    """Map a user-supplied archive name onto a file inside the backup dir.

    Returns None if the name escapes the backup directory or is not a file.
    The name arrives from an HTTP request, so it is never trusted.
    """
    if not name or name != Path(name).name:
        return None
    backup_dir = cfg.backup_dir
    try:
        target = (backup_dir / name).resolve()
        if not target.is_relative_to(backup_dir.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


def inspect(cfg: Config, name: str) -> dict:
    """Validate an archive and describe what restoring it would replace."""
    archive = resolve_archive(cfg, name)
    if archive is None:
        return {"ok": False, "error": "no such backup"}
    if not zipfile.is_zipfile(archive):
        return {"ok": False, "error": f"{name} is not a valid zip archive"}

    try:
        with zipfile.ZipFile(archive) as zf:
            if zf.testzip() is not None:
                return {"ok": False, "error": f"{name} is corrupt"}
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": str(exc)}

    saves = [n for n in names if n.startswith(SAVES_PREFIX) and not n.endswith("/")]
    configs = [n for n in names if n.startswith(CONFIG_PREFIX) and not n.endswith("/")]
    if not saves:
        return {
            "ok": False,
            "error": f"{name} contains no {SAVES_PREFIX} entries - not a pzctl backup",
        }
    return {
        "ok": True,
        "name": archive.name,
        "world": str(cfg.get("server_name")),
        "save_files": len(saves),
        "config_files": sorted(Path(n).name for n in configs),
        "size_mb": round(archive.stat().st_size / (1024 * 1024), 2),
    }


def _staged_target(root: Path, member: str, prefix: str) -> Path | None:
    """Resolve a zip member to a path under `root`, or None if it escapes.

    Guards against archives whose entries contain absolute paths or `..`
    segments - a zip is attacker-controlled input the moment somebody drops a
    file into the backup folder.
    """
    relative = member[len(prefix):]
    if not relative or relative.endswith("/"):
        return None
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    return target if target.is_relative_to(root.resolve()) else None


def restore(cfg: Config, name: str, supervisor=None, pre_backup: bool = True) -> dict:
    """Replace the current world with the contents of a backup archive.

    Refuses to run while the server is up. Takes a safety backup of the
    current world first unless explicitly told not to, so a restore of the
    wrong archive is itself recoverable.
    """
    details = inspect(cfg, name)
    if not details["ok"]:
        return details
    archive = resolve_archive(cfg, name)
    if archive is None:
        return {"ok": False, "error": "no such backup"}

    log = supervisor.emit if supervisor is not None else (lambda *a, **k: None)

    if supervisor is not None and supervisor.state not in ("stopped", "crashed"):
        return {
            "ok": False,
            "error": f"server is {supervisor.state} - stop it before restoring",
        }

    save_dir = cfg.save_dir
    safety = None

    # Stage inside the save directory's parent so the final move is a rename on
    # the same filesystem rather than a copy across volumes.
    save_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pzctl-restore-", dir=save_dir.parent))
    restored_configs: list[str] = []
    displaced: Path | None = None

    try:
        # Extract before touching anything else, so a corrupt or hostile
        # archive is rejected while the current world is still intact - and so
        # the source archive is fully read before the safety backup writes into
        # the same directory.
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                if member.startswith(SAVES_PREFIX):
                    target = _staged_target(staging, member, SAVES_PREFIX)
                    if target is None:
                        raise ValueError(f"unsafe path in archive: {member}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

        if pre_backup and save_dir.is_dir():
            log("restore: backing up the current world before replacing it", "pzctl")
            result = run(cfg, supervisor=None, reason="pre-restore")
            if not result.get("ok"):
                shutil.rmtree(staging, ignore_errors=True)
                return {
                    "ok": False,
                    "error": (
                        "aborted - could not back up the current world: "
                        f"{result.get('error')}"
                    ),
                }
            safety = result["name"]

        # Swap the staged world in, keeping the old one until we are sure.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        displaced = save_dir.with_name(f"{save_dir.name}.replaced-{stamp}")
        if save_dir.exists():
            save_dir.rename(displaced)
        try:
            staging.rename(save_dir)
        except OSError:
            if displaced.exists():
                displaced.rename(save_dir)
            raise

        # Config files are restored by name into the server config directory,
        # never by the path recorded in the archive.
        wanted = {
            cfg.ini_path.name: cfg.ini_path,
            cfg.sandbox_path.name: cfg.sandbox_path,
            cfg.spawnregions_path.name: cfg.spawnregions_path,
        }
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not member.startswith(CONFIG_PREFIX) or member.endswith("/"):
                    continue
                destination = wanted.get(Path(member).name)
                if destination is None:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored_configs.append(destination.name)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": str(exc), "safety_backup": safety}

    shutil.rmtree(staging, ignore_errors=True)
    result = {
        "ok": True,
        "restored": archive.name,
        "save_files": details["save_files"],
        "config_files": sorted(restored_configs),
        "safety_backup": safety,
        "displaced": displaced.name if displaced is not None and displaced.exists() else None,
    }
    log(
        f"restore: {archive.name} restored ({details['save_files']} save files); "
        f"previous world kept as {result['displaced']}",
        "pzctl",
    )
    return result


def listing(cfg: Config) -> list[dict]:
    if not cfg.backup_dir.is_dir():
        return []
    out = []
    for path in sorted(cfg.backup_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        out.append(
            {
                "name": path.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "mtime": stat.st_mtime,
            }
        )
    return out
