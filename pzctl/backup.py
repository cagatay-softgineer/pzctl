"""World backups: save the world, zip the save folder (+ config), prune old archives."""

from __future__ import annotations

import time
import zipfile
from datetime import datetime
from pathlib import Path

from .config import Config


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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{cfg.get('server_name')}-{stamp}.zip"

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
