"""Human-readable metadata for server INI keys and SandboxVars.

Labels, tooltips and enum choices are read from the game's own translation files
(`media/lua/shared/Translate/EN/{UI,Sandbox}.json`) so they stay correct across
game updates instead of being hard-coded here. Only the *grouping* is authored
locally, because the game keeps that in compiled Java rather than in data.

Anything the game has no metadata for still renders — it falls back to a
prettified key name and lands in the "Other" group.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .config import SERVER_DIR

TRANSLATE_DIR = SERVER_DIR / "media" / "lua" / "shared" / "Translate" / "EN"

# ── label lookup ────────────────────────────────────────────────────────

# SandboxVars whose translation key is not simply "Sandbox_<name>".
SANDBOX_ALIASES = {
    "Zombies": "ZombieCount",
    "Distribution": "ZombieDistribution",
    "Temperature": "WorldTemperature",
    "Rain": "RainAmount",
    "Alarm": "HouseAlarmFrequency",
    "CarAlarm": "CarAlarmFrequency",
    "ZombieLore.Mortality": "ZInfectionMortality",
    "ZombieLore.Reanimate": "ZReanimateTime",
    "ZombieLore.ActiveOnly": "ActiveOnly",
}

ACRONYMS = ("PVP", "RCON", "UPnP", "VAC", "XP", "ID", "FX", "URL", "IP")


def prettify(name: str) -> str:
    """MaxPlayersOnline -> 'Max Players Online'; RCONPassword -> 'RCON Password'."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return text.replace("_", " ").strip()


@lru_cache(maxsize=None)
def _translations(filename: str) -> dict:
    path = TRANSLATE_DIR / filename
    try:
        # The game ships these with a BOM.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _choices(table: dict, base: str) -> list[str]:
    out = []
    index = 1
    while f"{base}_option{index}" in table:
        out.append(table[f"{base}_option{index}"])
        index += 1
    return out


def _clean(text: str) -> str:
    """Translation strings carry markup meant for the in-game tooltip renderer."""
    text = text.replace("<br>", " ").replace("<LINE>", " ")
    text = re.sub(r"\\n", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── grouping ────────────────────────────────────────────────────────────

def _rule(group, exact=(), prefix=(), pattern=None):
    return {
        "group": group,
        "exact": set(exact),
        "prefix": tuple(prefix),
        "pattern": re.compile(pattern) if pattern else None,
    }


# Order matters: the first matching rule wins, so narrow rules come first.
SANDBOX_RULES = [
    _rule("Zombie Lore", prefix=("ZombieLore.",)),
    _rule("Skill XP Multipliers", prefix=("MultiplierConfig.",)),
    _rule("Map & Minimap", prefix=("Map.",)),
    _rule("Basements", prefix=("Basement.",)),
    _rule("Animals", prefix=("Animal",), exact=("MaximumRatIndex", "DaysUntilMaximumRatIndex")),
    _rule("Population",
          exact=("Zombies", "Distribution", "ZombieVoronoiNoise", "ZombieRespawn", "ZombieMigrate"),
          prefix=("ZombieConfig.",)),
    _rule("Time & Weather",
          exact=("DayLength", "StartYear", "StartMonth", "StartDay", "StartTime", "DayNightCycle",
                 "ClimateCycle", "FogCycle", "NightDarkness", "NightLength", "Temperature", "Rain",
                 "MaxFogIntensity", "MaxRainFxIntensity", "EnableSnowOnGround", "TimeSinceApo")),
    _rule("Power, Water & Fire",
          exact=("WaterShut", "ElecShut", "AlarmDecay", "WaterShutModifier", "ElecShutModifier",
                 "AlarmDecayModifier", "GeneratorFuelConsumption", "GeneratorSpawning",
                 "AllowExteriorGenerator", "GeneratorTileRange", "GeneratorVerticalPowerRange",
                 "LightBulbLifespan", "MaximumFireFuelHours", "FireSpread")),
    _rule("Vehicles",
          exact=("EnableVehicles", "CarSpawnRate", "VehicleEasyUse", "InitialGas", "LockedCar",
                 "CarGasConsumption", "CarGeneralCondition", "CarDamageOnImpact", "TrafficJam",
                 "CarAlarm", "PlayerDamageFromCrash", "SirenShutoffHours", "ChanceHasGas",
                 "RecentlySurvivorVehicles", "ZombieAttractionMultiplier", "SirenEffectsZombies",
                 "VehicleStoryChance", "DamageToPlayerFromHitByACar"),
          prefix=("FuelStation",)),
    _rule("Combat & Firearms",
          prefix=("Firearm",),
          exact=("MultiHitZombies", "RearVulnerability", "AttackBlockMovements")),
    _rule("Nature & Farming",
          prefix=("Farming", "Plant"),
          exact=("CompostTime", "NatureAbundance", "ErosionSpeed", "ErosionDays", "ClayLakeChance",
                 "ClayRiverChance", "FishAbundance", "KillInsideCrops", "PlaceDirtAboveground")),
    _rule("Loot",
          pattern=r"Loot",
          exact=("RollsMultiplier", "SeenHoursPreventLootRespawn", "HoursForLootRespawn",
                 "MaxItemsForLootRespawn", "ConstructionPreventsLootRespawn",
                 "HoursForWorldItemRemoval", "WorldItemRemovalList",
                 "ItemRemovalListBlacklistToggle", "MaximumLooted", "DaysUntilMaximumLooted",
                 "RuralLooted", "MaximumDiminishedLoot", "DaysUntilMaximumDiminishedLoot",
                 "AnnotatedMapChance", "StarterKit")),
    _rule("Corpses & Decay",
          exact=("HoursForCorpseRemoval", "DecayingCorpseHealthImpact", "ZombieHealthImpact",
                 "DaysForRottenFoodRemoval", "BloodSplatLifespanDays", "MaggotSpawn",
                 "FoodRotSpeed", "FridgeFactor")),
    _rule("Meta & Story",
          exact=("Helicopter", "MetaEvent", "SleepingEvent", "SurvivorHouseChance", "ZoneStoryChance",
                 "Alarm", "LockedHouses", "MetaKnowledge", "MaximumLootedBuildingRooms")),
    _rule("Character",
          exact=("CharacterFreePoints", "ConstructionBonusPoints", "StatsDecrease", "EndRegen",
                 "Nutrition", "BoneFracture", "InjurySeverity", "BloodLevel", "ClothingDegradation",
                 "MuscleStrainFactor", "DiscomfortFactor", "WoundInfectionFactor",
                 "NegativeTraitsPenalty", "MinutesPerPage", "LiteratureCooldown",
                 "SeeNotLearntRecipe", "LevelForMediaXPCutoff", "LevelForDismantleXPCutoff",
                 "EnablePoisoning", "AllClothesUnlocked", "NoBlackClothes", "EasyClimbing",
                 "EnableTaintedWaterText")),
]

INI_RULES = [
    _rule("Identity & Visibility",
          exact=("PublicName", "PublicDescription", "Public", "Open", "Password", "MaxPlayers",
                 "ResetID", "ServerPlayerID", "Seed", "ServerWelcomeMessage", "PauseEmpty")),
    _rule("Mods & Map", exact=("Mods", "WorkshopItems", "Map", "SpawnPoint", "SpawnItems")),
    _rule("Network & Performance",
          prefix=("AntiCheat",),
          exact=("DefaultPort", "UPnP", "PingLimit", "MaxPacketsPerSecond", "SendBufferSize",
                 "ZombieUpdateRadius", "KickFastPlayers", "MaxAccountsPerUser", "SteamVAC",
                 "SteamScoreboard", "MultiplayerStatisticsPeriod", "SaveWorldEveryMinutes",
                 "DoLuaChecksum", "ClientCommandFilter", "ClientActionLogs", "PerkLogs",
                 "ItemNumbersLimitPerContainer", "FastForwardMultiplier")),
    _rule("RCON", exact=("RCONPort", "RCONPassword")),
    _rule("PVP & Combat",
          prefix=("PVP",),
          exact=("KnockedDownAllowed", "PlayerBumpPlayer", "HidePlayersBehindYou",
                 "CarEngineAttractionModifier", "SafetySystem", "ShowSafety", "SafetyToggleTimer",
                 "SafetyCooldownTimer")),
    _rule("Safehouses", prefix=("Safehouse", "SafeHouse"),
          exact=("PlayerSafehouse", "AdminSafehouse", "DisableSafehouseWhenPlayerConnected",
                 "DisableSafehouseWhenOwnerConnected", "SledgehammerOnlyInSafehouse")),
    _rule("Factions & War", prefix=("Faction", "War")),
    _rule("Chat, Voice & Discord",
          prefix=("Discord", "Voice", "BadWord"),
          exact=("GlobalChat", "GoodWordListFile", "WebhookAddress")),
    _rule("Radio Permissions", prefix=("DisableRadio",)),
    _rule("Players & Respawn",
          exact=("AllowCoop", "AutoCreateUserInWhiteList", "DropOffWhiteListAfterDeath",
                 "AllowNonAsciiUsername", "DisplayUserName", "ShowFirstAndLastName",
                 "MouseOverToSeeDisplayName", "ShowCoordinates", "MapRemotePlayerVisibility",
                 "AnnounceDeath", "AnnounceAnimalDeath", "PlayerRespawnWithSelf",
                 "PlayerRespawnWithOther", "SleepAllowed", "SleepNeeded", "HideAdminsInPlayerList",
                 "DisableScoreboard")),
    _rule("World Rules",
          exact=("NoFire", "NoFireSpread", "AllowDestructionBySledgehammer", "DisableVehicleTowing",
                 "DisableTrailerTowing", "DisableBurntTowing", "TrashDeleteAll", "MinutesPerPage",
                 "BloodSplatLifespanDays")),
    _rule("Loot & Cleanup",
          pattern=r"LootRespawn|CorpseRemoval|WorldItemRemoval|ItemRemoval",
          exact=("HoursForLootRespawn", "MaxItemsForLootRespawn", "HoursForCorpseRemoval",
                 "HoursForWorldItemRemoval", "WorldItemRemovalList")),
]

OTHER = "Other"


def _classify(key: str, rules: list[dict]) -> str:
    for rule in rules:
        if key in rule["exact"]:
            return rule["group"]
        if rule["prefix"] and key.startswith(rule["prefix"]):
            return rule["group"]
        if rule["pattern"] and rule["pattern"].search(key):
            return rule["group"]
    return OTHER


def group_order(rules: list[dict]) -> list[str]:
    seen: list[str] = []
    for rule in rules:
        if rule["group"] not in seen:
            seen.append(rule["group"])
    seen.append(OTHER)
    return seen


# ── public API ──────────────────────────────────────────────────────────

# Values long enough that a single-line input is painful to edit.
INI_MULTILINE = {"ServerWelcomeMessage", "PublicDescription", "WorldItemRemovalList",
                 "Mods", "WorkshopItems", "Map", "ClientCommandFilter", "ClientActionLogs"}
SANDBOX_MULTILINE = {"WorldItemRemovalList"}


PRESET_DIR = SERVER_DIR / "media" / "lua" / "shared" / "Sandbox"
DEFAULT_PRESET = "Apocalypse"


@lru_cache(maxsize=None)
def preset_defaults(name: str = DEFAULT_PRESET) -> dict:
    """Default SandboxVars from a preset the game ships (Apocalypse, Survivor, ...)."""
    from . import sandbox  # imported here to keep module import order simple

    path = PRESET_DIR / f"{name}.lua"
    if not path.is_file():
        return {}
    return sandbox.to_dict(path)


@lru_cache(maxsize=None)
def preset_names() -> list[str]:
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.lua") if p.stem != "SandboxVars")


def value_hint(key: str, value) -> str:
    """A short, honest note about the scale a numeric value is measured on.

    Deliberately pattern-based: it only says what the key name itself licenses,
    so it can never contradict the game's own tooltip.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    leaf = key.split(".")[-1]
    if leaf.endswith(("Multiplier", "Modifier", "Factor")) or leaf in ("RollsMultiplier",):
        return "1.0 = unchanged · higher means more"
    if leaf.endswith("LootNew") or leaf.endswith("Loot"):
        return "spawn rate · 1.0 = normal, lower is rarer"
    if leaf.endswith("Percentage") or leaf.endswith("MaxDefense"):
        return "percent (0–100)"
    if leaf.endswith("Chance"):
        return "chance · higher means more often"
    if leaf.startswith("HoursFor") or leaf.endswith("Hours"):
        return "in in-game hours"
    if leaf.startswith(("DaysUntil", "DaysFor")) or leaf.endswith("Days"):
        return "in in-game days"
    if leaf.endswith("Minutes"):
        return "in minutes"
    return ""


def sandbox_meta(key: str) -> dict:
    table = _translations("Sandbox.json")
    leaf = key.split(".")[-1]
    bases = []
    if key in SANDBOX_ALIASES:
        bases.append(SANDBOX_ALIASES[key])
    bases.append(key.replace(".", ""))
    bases.append(leaf)
    if key.startswith("ZombieLore."):
        bases.append("Z" + leaf)

    label = tooltip = None
    choices: list[str] = []
    for base in bases:
        full = f"Sandbox_{base}"
        if full in table:
            label = table[full]
            tooltip = table.get(f"{full}_tooltip") or table.get(f"{full}_help")
            choices = _choices(table, full)
            break

    return {
        "label": label or prettify(leaf),
        "tooltip": _clean(tooltip) if tooltip else "",
        "choices": choices,
        "group": _classify(key, SANDBOX_RULES),
        "multiline": leaf in SANDBOX_MULTILINE,
    }


def ini_meta(key: str) -> dict:
    table = _translations("UI.json")
    base = f"UI_ServerOption_{key}"
    tooltip = table.get(f"{base}_tooltip")
    choices = _choices(table, base)
    # Every AntiCheat* switch shares one set of actions.
    if not choices and key.startswith("AntiCheat"):
        choices = _choices(table, "UI_ServerOption_AntiCheat")
    return {
        "label": table.get(base) or prettify(key),
        "tooltip": _clean(tooltip) if tooltip else "",
        "choices": choices,
        "group": _classify(key, INI_RULES),
        "multiline": key in INI_MULTILINE,
    }


def describe_all(values: dict, kind: str) -> dict:
    """Metadata for every key, enriched with the shipped default and a scale hint."""
    meta_for = sandbox_meta if kind == "sandbox" else ini_meta
    defaults = preset_defaults() if kind == "sandbox" else {}
    out = {}
    for key, value in values.items():
        meta = meta_for(key)
        meta["hint"] = value_hint(key, value)
        if key in defaults:
            meta["default"] = defaults[key]
        out[key] = meta
    return out


def groups_for(kind: str) -> list[str]:
    return group_order(SANDBOX_RULES if kind == "sandbox" else INI_RULES)
