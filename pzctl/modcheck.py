"""Ask the server whether its Workshop mods need updating.

The game provides `checkModsNeedUpdate`, but it does not answer over RCON. It
replies "Checking started. The answer will be written in the log file and in
the chat" and the real result lands in the log some time later, as the exact
string:

    CheckModsNeedUpdate: Mods need update

There is deliberately no matching "mods are up to date" line. The command only
ever announces a problem, so silence is the *only* evidence that nothing needs
updating - and silence is also what a broken check looks like. Nothing here
reports "up to date" as a positive fact; it reports that no update was
announced within the window, which is a weaker claim and the honest one.

To decide whether a marker is from this check rather than an earlier one, the
size of every log file is recorded when the command is sent and only bytes
appended after that point are scanned. That avoids depending on the log's
timestamp format, which is not documented.
"""

from __future__ import annotations

import time

from . import logs
from .config import Config

MARKER = "CheckModsNeedUpdate: Mods need update"
COMMAND = "checkModsNeedUpdate"

# How long to keep looking before reporting that nothing was announced.
WINDOW_SEC = 90

# Single admin, single server, so one in-flight check is enough.
_state: dict = {"at": None, "offsets": {}, "result": None}


def _offsets(cfg: Config) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in logs.candidates(cfg):
        try:
            sizes[str(path)] = path.stat().st_size
        except OSError:
            continue
    return sizes


def request(cfg: Config, supervisor) -> dict:
    """Send the check. The answer arrives later, in the log."""
    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    offsets = _offsets(cfg)
    ok, reply = supervisor.send_command(COMMAND, prefer="auto")
    if not ok:
        return {"ok": False, "error": reply}

    _state.update({"at": time.time(), "offsets": offsets, "result": None})
    return {
        "ok": True,
        "status": "checking",
        "reply": (reply or "").strip(),
        "window_sec": WINDOW_SEC,
    }


def _scan(cfg: Config, offsets: dict[str, int]) -> str | None:
    """Look for the marker in whatever each log gained since the check began."""
    for path in logs.candidates(cfg):
        start = offsets.get(str(path), 0)
        try:
            size = path.stat().st_size
            if size <= start:
                continue
            with path.open("rb") as handle:
                handle.seek(start)
                chunk = handle.read()
        except OSError:
            continue
        if MARKER in chunk.decode("utf-8", errors="replace"):
            return path.name
    return None


def poll(cfg: Config) -> dict:
    """Report on the check in flight, if any."""
    if _state["at"] is None:
        return {"ok": True, "status": "idle"}

    elapsed = time.time() - _state["at"]

    if _state["result"] is None:
        found_in = _scan(cfg, _state["offsets"])
        if found_in:
            _state["result"] = {"status": "update_needed", "found_in": found_in}

    if _state["result"]:
        return {"ok": True, "elapsed_sec": round(elapsed, 1), **_state["result"]}

    if elapsed < WINDOW_SEC:
        return {"ok": True, "status": "checking", "elapsed_sec": round(elapsed, 1)}

    # The command never says "everything is fine", so this is as far as it goes.
    return {
        "ok": True,
        "status": "no_update_reported",
        "elapsed_sec": round(elapsed, 1),
        "note": (
            "The server did not announce a mod update. It has no message for "
            "'up to date', so this is the absence of a warning rather than "
            "confirmation."
        ),
    }


def reset() -> None:
    _state.update({"at": None, "offsets": {}, "result": None})
