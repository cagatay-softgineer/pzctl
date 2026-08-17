"""Game Master actions - granting XP, for now.

`addxp "username" perkname=amount` takes an **internal** perk identifier, and
those differ from the names shown in game: `Sprinting` is displayed as
"Running", `Doctor` as "First Aid", `Blunt` as "Long Blunt". Typing what you see
produces a command the server quietly ignores, which is the whole reason the
panel offers a picker fed by `gamedata.perks()`.

The picker lists every `IGUI_perks_*` id the game defines. Some of those are
category headings rather than grantable skills - `Combat`, `Agility`,
`PhysicalCategory` sit in the same table as `Sprinting`, and nothing in the data
distinguishes them. Rather than invent a taxonomy and risk hiding a real skill,
everything is offered and the server's reply is reported verbatim: an id it does
not accept simply comes back rejected, which is honest and costs nothing.
"""

from __future__ import annotations

import re

from .moderation import validate_name

PERK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# The game caps skills at 10; XP totals are far larger, so allow a wide range
# but refuse values that are obviously a mistake or an overflow attempt.
MAX_XP = 10_000_000


def add_xp(supervisor, username: str, perk: str, amount) -> dict:
    """Grant XP in a skill. `amount` is XP, not a level."""
    username = str(username or "").strip()
    perk = str(perk or "").strip()

    problem = validate_name(username)
    if problem:
        return {"ok": False, "error": problem}
    if not perk:
        return {"ok": False, "error": "no skill given"}
    if not PERK_RE.match(perk):
        # The perk goes into a command line, so anything but a bare identifier
        # is refused rather than escaped.
        return {"ok": False, "error": f"invalid skill id: {perk!r}"}

    try:
        xp = int(amount)
    except (TypeError, ValueError):
        return {"ok": False, "error": "XP amount must be a whole number"}
    if xp == 0:
        return {"ok": False, "error": "XP amount must not be zero"}
    if abs(xp) > MAX_XP:
        return {"ok": False, "error": f"XP amount must be between -{MAX_XP} and {MAX_XP}"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"gm: granting {xp} {perk} XP to {username!r}", "pzctl")
    ok, reply = supervisor.send_command(f'addxp "{username}" {perk}={xp}', prefer="auto")
    if not ok:
        supervisor.emit(f"gm: addxp for {username!r} FAILED - {reply}", "error")
    return {"ok": ok, "username": username, "perk": perk, "xp": xp, "reply": reply}
