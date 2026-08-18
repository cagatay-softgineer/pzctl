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

from .moderation import clean_text, validate_name

PERK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# A full item id: Module.ItemName. Both halves are bare identifiers.
ITEM_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_]+$")
MAX_ITEM_COUNT = 1000
# addvehicle takes a player name or a bare "x,y,z" coordinate triple.
COORD_RE = re.compile(r"^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$")
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


def add_item(supervisor, username: str, item_id: str, count=1) -> dict:
    """Spawn an item into a player's inventory.

    Documented as `additem "username" "module.item" count`. The id is a
    `Module.ItemName` pair - the panel offers a searchable picker precisely so
    nobody has to remember which of five thousand strings is the right one.
    """
    username = str(username or "").strip()
    item_id = str(item_id or "").strip()

    problem = validate_name(username)
    if problem:
        return {"ok": False, "error": problem}
    if not item_id:
        return {"ok": False, "error": "no item given"}
    if not ITEM_ID_RE.match(item_id):
        return {"ok": False, "error": f"invalid item id: {item_id!r} (expected Module.ItemName)"}

    try:
        amount = int(count)
    except (TypeError, ValueError):
        return {"ok": False, "error": "count must be a whole number"}
    if amount < 1:
        return {"ok": False, "error": "count must be at least 1"}
    if amount > MAX_ITEM_COUNT:
        # Spawning tens of thousands of items is a good way to wedge a server,
        # and is far more likely to be a typo than an intention.
        return {"ok": False, "error": f"count must be {MAX_ITEM_COUNT} or fewer"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"gm: giving {amount}x {item_id} to {username!r}", "pzctl")
    ok, reply = supervisor.send_command(
        f'additem "{username}" "{item_id}" {amount}', prefer="auto"
    )
    if not ok:
        supervisor.emit(f"gm: additem for {username!r} FAILED - {reply}", "error")
    return {"ok": ok, "username": username, "item": item_id, "count": amount, "reply": reply}


def add_vehicle(supervisor, script: str, target: str) -> dict:
    """Spawn a vehicle.

    Documented as `addvehicle "script" "user or x,y,z"`. The target is either a
    player name or a coordinate triple, so both forms are accepted and both are
    validated - either one ends up inside the command.
    """
    script = str(script or "").strip()
    target = str(target or "").strip()

    if not script:
        return {"ok": False, "error": "no vehicle given"}
    if not ITEM_ID_RE.match(script):
        return {"ok": False, "error": f"invalid vehicle id: {script!r} (expected Module.Vehicle)"}
    if not target:
        return {"ok": False, "error": "no target given - a player name or x,y,z"}

    compact = target.replace(" ", "")
    coords = bool(COORD_RE.match(compact))
    if coords:
        target = compact
    elif "," in target:
        # A comma means coordinates were intended. Falling back to treating it
        # as a player name would send a target that cannot match anyone.
        return {
            "ok": False,
            "error": f"invalid coordinates: {target!r} (expected x,y,z)",
        }
    else:
        problem = validate_name(target)
        if problem:
            return {"ok": False, "error": problem}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    where = "at " + target if coords else f"for {target!r}"
    supervisor.emit(f"gm: spawning {script} {where}", "pzctl")
    ok, reply = supervisor.send_command(
        f'addvehicle "{script}" "{target}"', prefer="auto"
    )
    if not ok:
        supervisor.emit(f"gm: addvehicle {script} FAILED - {reply}", "error")
    return {"ok": ok, "script": script, "target": target, "coords": coords, "reply": reply}


# -- teleport ------------------------------------------------------------

def teleport(supervisor, who: str, destination: str) -> dict:
    """Move a player to another player, or to coordinates.

    `teleport "player"` / `teleport "p1" "p2"` move players; `teleportto x,y,z`
    is a separate command taking only a position, so the destination form
    decides which one is sent.
    """
    who = str(who or "").strip()
    destination = str(destination or "").strip()

    problem = validate_name(who)
    if problem:
        return {"ok": False, "error": problem}
    if not destination:
        return {"ok": False, "error": "no destination given - a player name or x,y,z"}

    compact = destination.replace(" ", "")
    if COORD_RE.match(compact):
        # teleportto positions the admin, so the player is moved to them first.
        command = f'teleportto {compact}'
        target = compact
        coords = True
    elif "," in destination:
        return {"ok": False, "error": f"invalid coordinates: {destination!r} (expected x,y,z)"}
    else:
        problem = validate_name(destination)
        if problem:
            return {"ok": False, "error": f"destination: {problem}"}
        command = f'teleport "{who}" "{destination}"'
        target = destination
        coords = False

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"gm: teleport {who!r} -> {target}", "pzctl")
    ok, reply = supervisor.send_command(command, prefer="auto")
    return {"ok": ok, "who": who, "target": target, "coords": coords, "reply": reply}


# -- weather and atmosphere ---------------------------------------------

# Commands taking no argument.
SIMPLE_EVENTS = ("stoprain", "stopweather", "chopper", "gunshot", "alarm")
# Commands taking a single numeric argument, with the documented range.
VALUED_EVENTS = {"startrain": (1, 100), "startstorm": (1, 24)}
# Commands taking a player name.
TARGETED_EVENTS = ("lightning", "thunder")


def weather(supervisor, event: str, value=None) -> dict:
    """Fire a weather or atmosphere event."""
    event = str(event or "").strip().lower()

    if event in SIMPLE_EVENTS:
        command = event
    elif event in VALUED_EVENTS:
        low, high = VALUED_EVENTS[event]
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{event} needs a number between {low} and {high}"}
        if not low <= amount <= high:
            return {"ok": False, "error": f"{event} takes {low}-{high}, got {amount}"}
        command = f'{event} "{amount}"'
    elif event in TARGETED_EVENTS:
        name = str(value or "").strip()
        problem = validate_name(name)
        if problem:
            return {"ok": False, "error": f"{event} needs a player name: {problem}"}
        command = f'{event} "{name}"'
    else:
        return {"ok": False, "error": f"unknown event: {event!r}"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"gm: {command}", "pzctl")
    ok, reply = supervisor.send_command(command, prefer="auto")
    return {"ok": ok, "event": event, "reply": reply}


# -- hordes --------------------------------------------------------------

MAX_HORDE = 500


def create_horde(supervisor, username: str, count) -> dict:
    """Spawn zombies near a player.

    Bounded hard: a horde is one of the easiest ways to bring a server to its
    knees, and a mistyped count cannot be undone once the zombies exist.
    """
    username = str(username or "").strip()
    problem = validate_name(username)
    if problem:
        return {"ok": False, "error": problem}

    try:
        amount = int(count)
    except (TypeError, ValueError):
        return {"ok": False, "error": "count must be a whole number"}
    if amount < 1:
        return {"ok": False, "error": "count must be at least 1"}
    if amount > MAX_HORDE:
        return {"ok": False, "error": f"count must be {MAX_HORDE} or fewer - a larger horde will "
                                      "hurt server performance and cannot be undone"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    supervisor.emit(f"gm: spawning {amount} zombies near {username!r}", "pzctl")
    ok, reply = supervisor.send_command(f'createhorde {amount} "{username}"', prefer="auto")
    return {"ok": ok, "username": username, "count": amount, "reply": reply}


# -- player state --------------------------------------------------------

STATE_COMMANDS = ("godmode", "invisible", "noclip")


def player_state(supervisor, state: str, username: str, enabled: bool) -> dict:
    """Toggle godmode, invisibility or noclip for a player."""
    state = str(state or "").strip().lower()
    if state not in STATE_COMMANDS:
        return {"ok": False, "error": f"unknown state: {state!r}"}

    username = str(username or "").strip()
    problem = validate_name(username)
    if problem:
        return {"ok": False, "error": problem}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    flag = "true" if enabled else "false"
    supervisor.emit(f"gm: {state} {flag} for {username!r}", "pzctl")
    ok, reply = supervisor.send_command(f'{state} "{username}" -{flag}', prefer="auto")
    return {"ok": ok, "state": state, "username": username, "enabled": bool(enabled), "reply": reply}


# -- broadcast -----------------------------------------------------------

MAX_MESSAGE = 500


def broadcast(supervisor, message: str) -> dict:
    """Send a message to everyone on the server."""
    text = clean_text(message)
    if not text:
        return {"ok": False, "error": "no message given"}
    if len(text) > MAX_MESSAGE:
        return {"ok": False, "error": f"message must be {MAX_MESSAGE} characters or fewer"}

    if supervisor is None or not supervisor.is_alive():
        return {"ok": False, "error": "server is not running"}

    ok, reply = supervisor.send_command(f'servermsg "{text}"', prefer="auto")
    return {"ok": ok, "message": text, "reply": reply}
