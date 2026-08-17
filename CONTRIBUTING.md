# Contributing to pzctl

Thanks for taking the time to contribute.

## Reporting bugs

Open an issue and include:

- Your OS and Python version (`python --version`)
- The exact command or panel action that triggered the problem
- Relevant log output from `pzctl-data\logs\`
- Steps to reproduce, if known

## Suggesting features

Open an issue describing the use case, not just the desired implementation.
If it touches the INI/SandboxVars editors, note which game version you
tested against, since option metadata is derived from the game's own
translation files.

## Development

pzctl is pure Python 3.11+ standard library — no dependencies to install.

1. Fork and clone the repo.
2. Make your changes.
3. Test against a real Project Zomboid Dedicated Server install (drop
   `pzctl/` and `PZ-Control.bat` into the server directory as described in
   the README).
4. Keep changes focused — avoid unrelated formatting or refactors in the
   same PR as a functional change.

## Pull requests

- Describe what changed and why.
- Reference any related issue.
- Keep the diff scoped to a single concern where possible.
- Note any manual testing you did (e.g. "verified server start/stop and
  RCON test button on Windows 11").

## Style

- Match the existing code style in the file you're editing.
- No new external dependencies without discussion first — the
  zero-dependency, standard-library-only design is intentional.
