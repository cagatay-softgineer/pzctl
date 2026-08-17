"""Replace pzctl's own code with a newer release.

This overwrites the running program, so it is written to fail safely rather
than to be clever:

- It refuses while the game server is running. Applying an upgrade means
  restarting the daemon, and the daemon stops the game server cleanly on
  shutdown - so upgrading under a live server would disconnect every player.
- It validates the archive completely before touching anything on disk.
- It never touches `pzctl.json` (admin and RCON passwords, access token) or
  `pzctl-data/`. Only the `pzctl/` package directory is replaced.
- The previous version is renamed aside rather than deleted, so a bad upgrade
  is recoverable by hand.
- Swapping the files does NOT make the new code live. Python already holds the
  old modules in memory, so the daemon has to be restarted. Every result says
  so; claiming otherwise would leave an admin thinking they had upgraded.

Zip members are untrusted, exactly as in `backup.restore`, so paths are
resolved and contained rather than joined blindly.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from . import updates
from .config import SERVER_DIR

PACKAGE_DIR = SERVER_DIR / "pzctl"
# Files that prove the archive really is a pzctl release rather than any zip.
REQUIRED_MEMBERS = ("pzctl/__init__.py", "pzctl/app.py", "pzctl/web/index.html")
DOWNLOAD_TIMEOUT_SEC = 120


def _pick_asset(release: dict) -> dict | None:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.startswith("pzctl-") and name.endswith(".zip"):
            return asset
    return None


def _contained(root: Path, member: str) -> Path | None:
    """Resolve a zip member under `root`, or None if it escapes."""
    if not member or member.endswith("/"):
        return None
    try:
        target = (root / member).resolve()
    except (OSError, ValueError):
        return None
    return target if target.is_relative_to(root.resolve()) else None


def inspect_archive(path: Path) -> dict:
    """Confirm the zip is a pzctl release and find the package inside it."""
    if not zipfile.is_zipfile(path):
        return {"ok": False, "error": "the download is not a valid zip archive"}
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip() is not None:
                return {"ok": False, "error": "the download is corrupt"}
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": str(exc)}

    # Releases wrap everything in a pzctl-<version>/ directory, but tolerate a
    # flat archive too rather than failing on a detail.
    for prefix in {name.split("/")[0] + "/" for name in names if "/" in name} | {""}:
        if all(f"{prefix}{required}" in names for required in REQUIRED_MEMBERS):
            return {"ok": True, "prefix": prefix, "members": len(names)}

    return {
        "ok": False,
        "error": "the archive does not look like a pzctl release (no pzctl/ package inside)",
    }


def _download(url: str, into: Path) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": f"pzctl/{updates.__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
            into.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"download failed: HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "error": "could not reach GitHub to download the release"}
    return {"ok": True}


def apply(
    supervisor,
    archive: Path | None = None,
    package_dir: Path | None = None,
) -> dict:
    """Install a newer release.

    `archive` bypasses the download and `package_dir` overrides the install
    target; both exist so tests can exercise the real swap without touching
    the package they are running from.
    """
    target_dir = package_dir or PACKAGE_DIR
    if supervisor is not None and supervisor.is_alive():
        return {
            "ok": False,
            "error": "stop the game server before upgrading - applying an upgrade "
            "restarts pzctl, which would disconnect any players",
        }

    workspace = Path(tempfile.mkdtemp(prefix="pzctl-upgrade-"))
    try:
        source = archive
        version = None

        if source is None:
            try:
                release = updates._fetch_latest()
            except Exception:
                return {"ok": False, "error": "could not reach GitHub to check for a release"}
            asset = _pick_asset(release)
            if asset is None:
                return {"ok": False, "error": "the latest release has no pzctl zip attached"}
            version = str(release.get("tag_name") or "").lstrip("vV")
            if updates.compare(updates.__version__, version) != "newer_available":
                return {
                    "ok": False,
                    "error": f"already on {updates.__version__}; latest release is {version}",
                }
            source = workspace / str(asset.get("name") or "pzctl.zip")
            downloaded = _download(str(asset.get("browser_download_url") or ""), source)
            if not downloaded["ok"]:
                return downloaded

        details = inspect_archive(source)
        if not details["ok"]:
            return details

        # Extract fully before replacing anything, so a bad archive cannot
        # leave a half-installed package behind.
        staging = workspace / "staged"
        staging.mkdir(parents=True, exist_ok=True)
        prefix = details["prefix"]
        with zipfile.ZipFile(source) as zf:
            for member in zf.namelist():
                if not member.startswith(f"{prefix}pzctl/") or member.endswith("/"):
                    continue
                target = _contained(staging, member[len(prefix):])
                if target is None:
                    return {"ok": False, "error": f"unsafe path in archive: {member}"}
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        staged_package = staging / "pzctl"
        if not (staged_package / "__init__.py").is_file():
            return {"ok": False, "error": "the archive did not contain a usable pzctl package"}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        kept = target_dir.with_name(f"pzctl.previous-{stamp}")
        target_dir.rename(kept)
        try:
            shutil.move(str(staged_package), str(target_dir))
        except OSError:
            kept.rename(target_dir)  # put it back exactly as it was
            raise

        return {
            "ok": True,
            "installed": version,
            "previous_kept_as": kept.name,
            "restart_required": True,
            "note": (
                "Files replaced. pzctl is still running the old code - restart "
                "the daemon to use the new version."
            ),
        }
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
