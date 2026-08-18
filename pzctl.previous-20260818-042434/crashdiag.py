"""Point at the mod that probably broke the server, using what the log actually says.

Bad Workshop mods are the usual cause of a dedicated server crash loop, and
Project Zomboid leaves two direct pieces of evidence in its logs:

    ... ISFoo.lua line # 42 | MOD: SomeModName
    ...\\steamapps\\workshop\\content\\108600\\2101234567\\mods\\SomeMod\\media\\lua\\...

The first is the game's own annotation on stack trace lines; vanilla frames are
marked `| Vanilla` instead. The second is a file path that names the Workshop
item and the mod folder.

Both name a mod outright, so nothing here has to infer one. This module
deliberately does NOT guess - it will not blame the most recently added mod or
the last entry in the load order, because a plausible accusation that happens
to be wrong sends an admin off disabling innocent mods while the real culprit
keeps crashing the server. If the log does not name something, the answer is
"no mod named in the log", and the error text is shown so a human can judge.
"""

from __future__ import annotations

import re

from . import logs, mods
from .config import Config

# The game's own attribution on a stack trace line.
MOD_ANNOTATION_RE = re.compile(r"\|\s*MOD:\s*([^|\r\n]+)", re.IGNORECASE)
VANILLA_ANNOTATION_RE = re.compile(r"\|\s*Vanilla\b", re.IGNORECASE)

# A path inside the Workshop content tree: .../108600/<workshop id>/mods/<mod folder>/...
WORKSHOP_PATH_RE = re.compile(
    r"108600[/\\](\d+)[/\\]mods[/\\]([^/\\\s\"']+)", re.IGNORECASE
)

# Lines that mark the start of something worth reading. Kept broad on purpose:
# a missed error is worse than an extra one, since everything is shown to a
# human rather than acted on automatically.
ERROR_MARKERS = (
    "exception",
    "error:",
    "caused by",
    "java.lang.",
    "stack traceback",
    "callframe",
)

DEFAULT_SCAN_BYTES = 512 * 1024
MAX_EXCERPTS = 15
CONTEXT_LINES = 6


def _is_error_line(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in ERROR_MARKERS)


def _tail_text(path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            chunk = handle.read()
    except OSError:
        return ""
    return chunk.decode("utf-8", errors="replace")


def _attributions(text: str) -> dict[str, dict]:
    """Pull every mod the text names, with how it was named."""
    found: dict[str, dict] = {}

    for match in MOD_ANNOTATION_RE.finditer(text):
        name = match.group(1).strip()
        if not name or name.lower() == "vanilla":
            continue
        entry = found.setdefault(name, {"mod": name, "evidence": set(), "workshop_ids": set()})
        entry["evidence"].add("named in a stack trace (| MOD:)")

    for match in WORKSHOP_PATH_RE.finditer(text):
        workshop_id, folder = match.group(1), match.group(2)
        entry = found.setdefault(folder, {"mod": folder, "evidence": set(), "workshop_ids": set()})
        entry["evidence"].add("appeared in a Workshop file path")
        entry["workshop_ids"].add(workshop_id)

    return found


def _excerpts(text: str) -> tuple[list[dict], int]:
    """Return up to MAX_EXCERPTS blocks, plus the true number found.

    Counting continues past the cap so the total is honest - "showing 15 of
    240" tells an admin something that "15" does not.
    """
    lines = text.splitlines()
    out: list[dict] = []
    total = 0
    index = 0
    while index < len(lines):
        if _is_error_line(lines[index]):
            total += 1
            if len(out) < MAX_EXCERPTS:
                block = lines[index : index + CONTEXT_LINES]
                out.append({"line": index + 1, "text": "\n".join(block).rstrip()})
            index += CONTEXT_LINES
            continue
        index += 1
    return out, total


def analyse(cfg: Config, max_bytes: int = DEFAULT_SCAN_BYTES) -> dict:
    """Read the end of each game log and report what it blames, if anything."""
    scanned: list[str] = []
    excerpts: list[dict] = []
    attributions: dict[str, dict] = {}
    error_count = 0

    for path in logs.candidates(cfg):
        text = _tail_text(path, max_bytes)
        if not text:
            continue
        scanned.append(path.name)
        blocks, found = _excerpts(text)
        error_count += found
        for excerpt in blocks:
            excerpt["log"] = path.name
            excerpts.append(excerpt)
        for name, entry in _attributions(text).items():
            existing = attributions.setdefault(
                name, {"mod": name, "evidence": set(), "workshop_ids": set(), "logs": set()}
            )
            existing["evidence"] |= entry["evidence"]
            existing["workshop_ids"] |= entry["workshop_ids"]
            existing["logs"].add(path.name)

    if not scanned:
        return {
            "ok": False,
            "error": "no game logs found - has the server run yet?",
        }

    # Cross-reference against what is actually configured, so an admin can tell
    # a loaded mod from a stale path left in an old log.
    try:
        configured = mods.read(cfg)
    except Exception:
        configured = {"mods": [], "workshop_items": [], "installed": []}
    active = {str(entry).lower() for entry in configured.get("mods", [])}
    installed = {
        str(entry.get("mod_id", "")).lower(): entry for entry in configured.get("installed", [])
    }

    suspects = []
    for entry in attributions.values():
        key = entry["mod"].lower()
        known = installed.get(key)
        suspects.append(
            {
                "mod": entry["mod"],
                "evidence": sorted(entry["evidence"]),
                "workshop_ids": sorted(entry["workshop_ids"]),
                "logs": sorted(entry["logs"]),
                "in_load_order": key in active,
                "installed_name": known.get("name") if known else None,
            }
        )
    # Mods currently loaded first: those are the ones worth disabling.
    suspects.sort(key=lambda item: (not item["in_load_order"], item["mod"].lower()))

    return {
        "ok": True,
        "scanned": sorted(scanned),
        "suspects": suspects,
        "errors": excerpts[:MAX_EXCERPTS],
        "error_count": error_count,
        "note": (
            ""
            if suspects
            else "No mod is named in the logs. The errors below may still be "
            "informative, but pzctl will not guess at a culprit."
        ),
    }
