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
3. Run the test suite (see below).
4. Test against a real Project Zomboid Dedicated Server install (drop
   `pzctl/` and `PZ-Control.bat` into the server directory as described in
   the README).
5. Keep changes focused — avoid unrelated formatting or refactors in the
   same PR as a functional change.

## Tests

Standard library `unittest`, no test dependencies. From the repository root:

```
python -m unittest discover -s tests -t .
```

Or a single module:

```
python -m unittest tests.test_pzini
```

The suite covers the pure-logic modules — `pzini.py`, `sandbox.py`,
`config.py` and `backup.py`. It runs entirely in temporary directories and
never touches a real server install.

If you change `pzini.py` or `sandbox.py`, take the round-trip tests
seriously: they assert that editing one value leaves every other line
byte-for-byte identical, including comments, indentation, trailing commas
and line endings. That guarantee is the whole point of those two modules —
a regression there quietly rewrites somebody's server config.

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

## Cutting a release

Releases are built by `.github/workflows/release.yml` when a `v*` tag is pushed.

1. Bump `__version__` in `pzctl/__init__.py`
2. Commit it
3. Tag and push:

```
git tag v1.2.3
git push origin v1.2.3
```

The workflow runs the tests, checks the tag matches `__version__`, builds
`pzctl-1.2.3.zip` and publishes the release with generated notes.

**The tag must match `__version__`.** The workflow fails if it does not, on
purpose: the version is shown in the panel header, in `/api/status` and in the
startup banner, so a mismatch would have every install reporting a version it
is not.

The zip contains only what a user drops into their server directory — the
`pzctl/` package, `PZ-Control.bat`, `pzctl.json.example`, `README.md` and
`LICENSE`. It deliberately excludes `tests/`, `.github/` and `pzctl.json`, the
last of which holds admin and RCON passwords.

There is no PyPI package. `pzctl/config.py` derives `SERVER_DIR` from the
package's own location on disk, and `pzctl/mods.py` walks up from there to find
Steam Workshop content, so pzctl has to live inside the server directory. A
site-packages install would break both.
