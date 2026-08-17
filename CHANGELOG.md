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
