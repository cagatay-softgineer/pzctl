# Changelog

All notable changes to pzctl are recorded here.

## Versioning

pzctl follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

For a self-hosted tool with no public API, what those mean in practice:

| Part | Bumped when |
|---|---|
| **MAJOR** | An upgrade needs manual work — a `pzctl.json` key that must be changed by hand, a moved file, a dropped Python version, or anything that breaks an existing install on restart |
| **MINOR** | New features or new settings, where upgrading and restarting is enough. Existing `pzctl.json` files keep working |
| **PATCH** | Bug fixes only |

Two consequences worth knowing:

- **Your `pzctl.json` is never rewritten by an upgrade.** New settings appear with their defaults because `config.py` merges defaults under whatever your file already contains. That is what makes most changes MINOR rather than MAJOR.
- **The version is the one in `pzctl/__init__.py`.** It is shown in the panel header, returned by `/api/status` and printed at startup, and the release workflow refuses to publish a tag that disagrees with it.

Releases live on the [Releases page](https://github.com/cagatay-softgineer/pzctl/releases). See [Updating](https://github.com/cagatay-softgineer/pzctl/wiki/Updating) for how to install a new one.

---

## v1.3.0

Resource monitoring.

### Added

- **Resource trends in the panel.** CPU, memory, disk and network are sampled every 5 seconds and charted on the dashboard, over a 5m/30m/1h/2h window, with a hover readout that scrubs the history rather than only reporting the latest value. CPU and memory are each plotted twice — the server process against the whole machine — because a server pinned at one core looks calm on a machine-wide graph, and a machine starved by something else looks calm on a process graph. New `GET /api/sysres`; the header gains CPU and disk-free tiles. All standard library: `GetProcessTimes`/`GetSystemTimes` and `GetIfTable` on Windows, `/proc` on Linux, `shutil.disk_usage` everywhere.
- A `tests/test_web_assets.py` guard that catches JavaScript string literals broken across a line break without needing node, so it runs everywhere the Python suite does rather than only in the one CI job that has node.

### Fixed

- Disk space is reported for the volume even when the save directory does not exist yet, by walking up to the nearest existing parent. Without it a server that has not yet saved a world reported no disk figure at all — exactly when a filling disk is most likely to go unnoticed.

---

## v1.2.0

Server administration and maintenance.

### Added

- **Log verbosity control** (#32) — `-debuglog`/`-disablelog` categories at launch, and `log "Type" "Level"` on a running server. No category list is shipped: there is no authoritative published enumeration and it varies by build, so what you type is passed through and the server decides.
- **In-game access levels** (#3) — set a player's role (`admin`, `moderator`, `overseer`, `gm`, `observer`, or `none` to demote). Separate from the pzctl panel token, which remains single-admin.
- **Anti-cheat panel** (#34) — the 24 `AntiCheatProtectionType` toggles gathered in one place with bulk enable/disable. Only types 12 and 21 carry descriptions; no complete published mapping of the rest exists, and a wrong label would have you disable the wrong check.
- **Server update via SteamCMD** (#29) — updates Project Zomboid itself (app 380870). Refuses while the server is running, backs up first, and refuses to run at all unless the target looks like a real server install — a wrong `force_install_dir` does not fail, it silently creates a second copy.

### Fixed

- Four dialogs in the panel contained real newlines inside JavaScript string literals, which broke `app.js` entirely and left the panel blank. CI now runs `node --check` on the panel JavaScript; the Python suite cannot see this class of error.

### Note on #34

The original issue described eight named anti-cheat options with a severity enum. Those do not exist — the setting is 24 booleans. Building it as written would have written nonexistent keys into `servertest.ini` permanently, since the INI writer appends keys it does not find. The issue has been corrected.

---

## v1.1.0

Self-update support.

### Added

- **Update check** (#50) — a *check* button beside the version in the panel header asks GitHub whether a newer release exists, and links to it. Runs **only when pressed**: pzctl makes no other outbound request, and a background poll would change that property of the tool. Being offline is treated as a normal state, not an error.
- **In-panel upgrade** (#51) — installs a newer release from the panel. Refuses while the game server is running, since applying an upgrade restarts pzctl and would disconnect players. Only the `pzctl/` package is replaced; `pzctl.json` and `pzctl-data/` are never touched, and the previous version is kept as `pzctl.previous-<timestamp>`.

### Notes

Replacing the files does not make the new code live — Python holds the loaded modules for the life of the process. **Restart pzctl after upgrading.** The panel says so, and the header keeps showing the old version until you do.

---

## v1.0.0

First tagged release. Supervisor daemon and web control panel for a Project Zomboid dedicated server, pure Python 3.11+ standard library with no dependencies.

### Added

- **Test suite and CI** (#12) — stdlib `unittest`, running on Windows and Linux across Python 3.11–3.13 on every push and pull request
- **Backup restore** (#4) — restore a world from any archive, with the current world backed up first and kept aside; refuses while the server is running
- **Game log viewer** (#6) — read the game's own logs from the panel, discovered by pattern since PZ rotates them per session with a timestamp prefix
- **Live config apply** (#23) — push changed Server INI options to a running server with `changeoption`/`reloadoptions`, no restart needed
- **Player moderation** (#2) — kick, ban and unban from the player list, with every action recorded in the log
- **Whitelist management** (#1) — enforce whitelist mode and add or remove users; player passwords are never written to the console or the log
- **Mod update check** (#31) — ask the server whether its Workshop mods need updating
- **Crash diagnosis** (#7) — scan the game's logs after a crash and report which mod they name, without guessing when they name none
- **Releases** — tagged releases with a drop-in zip, and the version surfaced in the panel, `/api/status` and the startup banner

### Existing before v1.0.0

Process supervision with crash-loop detection and escalating backoff, line-preserving INI and SandboxVars editors annotated from the game's own translation files, mod load-order management, scheduled restarts with in-game countdown warnings, scheduled backups with retention, and an RCON bridge falling back to the server's console.
