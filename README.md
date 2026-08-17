# pzctl

[![tests](https://github.com/cagatay-softgineer/pzctl/actions/workflows/tests.yml/badge.svg)](https://github.com/cagatay-softgineer/pzctl/actions/workflows/tests.yml)

Supervisor daemon + web control panel for a Project Zomboid dedicated server.
Pure Python 3.11+ standard library — nothing to install, no dependencies.

Full documentation lives in the [wiki](https://github.com/cagatay-softgineer/pzctl/wiki)
— configuration reference, RCON setup, and troubleshooting. Planned work is
sequenced on the [roadmap board](https://github.com/users/cagatay-softgineer/projects/2).

## Install

Drop the `pzctl/` folder and `PZ-Control.bat` into your Project Zomboid Dedicated
Server install directory (the one with `jre64\`, `StartServer64.bat`, etc. — this
repo doesn't ship the game server itself, just the control layer).

Copy `pzctl.json.example` to `pzctl.json` in that same directory before first run
if you want to pre-fill settings; otherwise the daemon creates `pzctl.json` with
defaults (and a fresh random token) on first launch.

## Run it

Double-click `PZ-Control.bat` in the server folder, or:

```
python -m pzctl --open
```

The panel is served at <http://127.0.0.1:8077/>. Useful flags:

| flag | effect |
|---|---|
| `--open` | open the panel in your browser on startup |
| `--start` | start the game server immediately |
| `--host 0.0.0.0` | expose the panel to your LAN (token required from other machines) |
| `--port 9000` | listen elsewhere |
| `--no-schedule` | ignore timed restarts and backups this run |

Ctrl+C shuts the daemon down and stops the game server cleanly first.

## First-time setup

1. Start the daemon and open the **Launcher** tab.
2. Set an **admin username and password**. The server refuses to boot headless
   without one — it would sit waiting on an interactive prompt — so pzctl blocks
   the start and tells you instead of hanging.
3. Press **Start**. The first boot generates
   `%USERPROFILE%\Zomboid\Server\servertest.ini` and `servertest_SandboxVars.lua`;
   until then the INI and Sandbox tabs have nothing to show.
4. Set `RCONPassword` (and `RCONPort`) on the **Server INI** tab, mirror them on
   the **Launcher** tab, restart, then hit **test** next to RCON.

## What the daemon does

- **Supervision** — launches `jre64\bin\java.exe` with your JVM settings, streams
  the console to the panel and to `pzctl-data\logs\console-YYYY-MM-DD.log`.
- **Auto-restart** — a non-intentional exit triggers a restart with escalating
  backoff (5s → 15s → 30s → 60s → 120s). More than `max_restarts` inside
  `restart_window_sec` is treated as a crash loop and the daemon stops trying.
- **RCON** — commands from the panel go over RCON when it is configured (so you
  see the reply), otherwise they are written to the server's stdin.
- **Scheduled restarts** — with in-game `servermsg` countdown warnings at the
  minute marks you choose.
- **Scheduled backups** — issues `save`, then zips the world save (plus the INI
  and SandboxVars) into `%USERPROFILE%\Zomboid\Backups`, keeping the newest N.

## Config editing

The INI and SandboxVars editors rewrite **only the values you changed**, in
place. Comments, key order and settings pzctl doesn't know about are preserved
byte-for-byte. Both files are written atomically via a `.tmp` + replace.

Changes need a server restart to take effect — PZ reads these at boot.

### Where the labels come from

Both editors are annotated from the game's own translation files
(`media/lua/shared/Translate/EN/UI.json` and `Sandbox.json`), read at request
time rather than copied into pzctl. That means:

- **Real names, not raw keys** — `ZombieLore.Speed` shows as "Speed",
  `PVPMeleeDamageModifier` as "PVP Melee Damage Modifier". The raw key stays
  visible next to it so you can still match it against a wiki or a guide.
- **The game's own descriptions** — 135/135 server options and 245/269 sandbox
  vars carry the same explanatory text the in-game settings screen shows.
- **Dropdowns instead of magic numbers** — 61 sandbox vars are enums, so
  `Zombies = 4` renders as a picker reading "4 — Normal" rather than a bare `4`.
- **Defaults you can compare against** — sandbox fields show the value from the
  shipped Apocalypse preset, mark themselves when they differ, and offer a
  one-click reset.
- **Scale hints** — numeric fields note what the number is measured in
  ("1.0 = unchanged", "in in-game hours"). These are derived from the key name,
  so they never contradict the game's own description.

Options are collapsed into categories (Population, Zombie Lore, Loot, Vehicles,
Safehouses, RCON, …) with jump-to chips, a search box that also matches
description text, and "modified only" / "differs from preset" filters.

Grouping is the one piece authored inside pzctl (`optionmeta.py`) — the game
keeps it in compiled Java rather than in data. Anything the game has no metadata
for, including keys added by mods or a future game update, still renders with a
prettified name and lands in the "Other" group instead of disappearing.

The **Mods** tab scans `steamapps\workshop\content\108600` for what is actually
installed, and keeps `Mods` and `WorkshopItems` in sync when you add or remove a
mod. Load order is top-to-bottom; use the arrows.

## Daemon settings

Everything lives in `pzctl.json` next to the server executable, editable from the
Launcher tab or by hand (the daemon reads it at startup). This file is
git-ignored since it holds your admin/RCON passwords and access token —
`pzctl.json.example` shows the shape.

## Security

The API requires the token printed in the daemon console. Requests from
localhost get it injected into the page automatically; from any other machine
the panel prompts for it once and remembers it.

The default bind is `127.0.0.1`, so nothing is reachable off this box unless you
pass `--host 0.0.0.0`. If you do: the panel is plain HTTP with a bearer token —
put it behind a VPN or an HTTPS reverse proxy rather than on the open internet,
and note that `pzctl.json` stores the admin and RCON passwords in cleartext.

## Documentation and roadmap

| | |
|---|---|
| [Wiki](https://github.com/cagatay-softgineer/pzctl/wiki) | Installation, full `pzctl.json` reference, panel guide, RCON setup, scheduling and backups, mods, security, troubleshooting |
| [Roadmap board](https://github.com/users/cagatay-softgineer/projects/2) | Planned features, sequenced into phases with their dependencies |
| [Issues](https://github.com/cagatay-softgineer/pzctl/issues) | Bugs and feature requests |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: standard library
only — no new dependencies without discussion first, since the zero-dependency
design is deliberate.

## License

[GNU General Public License v3.0](LICENSE).

You may use, modify and redistribute pzctl, including commercially. If you
distribute it — modified or not — you must do so under the same license and
make the source available. Project Zomboid itself is not covered by this; pzctl
ships only the control layer.
