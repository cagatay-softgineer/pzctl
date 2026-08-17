"""Check whether a newer pzctl release exists.

This is the only outbound network request pzctl makes. Everything else -
including mod discovery, which deliberately avoids the Steam API - works
offline. So the check runs only when an admin asks for it, never on a timer or
at startup, and a failure to reach GitHub is reported as "could not check"
rather than as an error, because being offline is a normal state for a machine
running a game server.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import __version__

RELEASES_API = "https://api.github.com/repos/cagatay-softgineer/pzctl/releases/latest"
RELEASES_PAGE = "https://github.com/cagatay-softgineer/pzctl/releases"
TIMEOUT_SEC = 10

VERSION_RE = re.compile(r"^\D*(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Turn 'v1.2.3' or '1.2.3' into a comparable tuple, or None if unparseable."""
    match = VERSION_RE.match(str(text or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def compare(current: str, latest: str) -> str:
    """One of: newer_available, current, ahead, unknown."""
    here, there = parse_version(current), parse_version(latest)
    if here is None or there is None:
        return "unknown"
    if there > here:
        return "newer_available"
    if there < here:
        # A dev build ahead of the last release; not a problem, but say so
        # rather than claiming it is up to date.
        return "ahead"
    return "current"


def _fetch_latest() -> dict:
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"pzctl/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


def check() -> dict:
    """Ask GitHub for the latest release. Never raises."""
    result = {"ok": True, "current": __version__, "releases_url": RELEASES_PAGE}

    try:
        data = _fetch_latest()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No published release yet - not an error worth alarming anyone about.
            return {**result, "status": "no_releases"}
        reason = "rate limited by GitHub" if exc.code == 403 else f"HTTP {exc.code}"
        return {**result, "ok": False, "error": f"could not check for updates: {reason}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {
            **result,
            "ok": False,
            "error": "could not reach GitHub - check your internet connection",
        }
    except (ValueError, json.JSONDecodeError):
        return {**result, "ok": False, "error": "could not read GitHub's response"}

    tag = str(data.get("tag_name") or "")
    latest = tag.lstrip("vV")
    return {
        **result,
        "status": compare(__version__, latest),
        "latest": latest,
        "tag": tag,
        "url": data.get("html_url") or RELEASES_PAGE,
        "published_at": data.get("published_at"),
        "notes": (data.get("body") or "")[:2000],
    }
