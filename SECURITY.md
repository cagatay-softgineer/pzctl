# Security Policy

## Supported Versions

Only the latest commit on `master` is supported. There are no maintained
release branches at this time.

## Reporting a Vulnerability

If you find a security issue, please report it privately rather than
opening a public issue: use GitHub's [private vulnerability
reporting](https://github.com/cagatay-softgineer/pzctl/security/advisories/new)
for this repository, or open an issue asking a maintainer to contact you
directly if that option isn't available.

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected version/commit

You should get an acknowledgement within a few days.

## Scope notes

pzctl exposes a local web control panel guarded by a bearer token (see the
"Security" section of the README). Reports about the following are
especially welcome:

- Token handling or authentication bypass
- Path traversal or command injection via the INI/SandboxVars editors,
  mod manager, or RCON bridge
- Anything that lets a request from an unauthenticated origin control the
  daemon or the game server

Reports about running the panel bound to `0.0.0.0` without a reverse proxy
are a known tradeoff documented in the README, not a new finding, unless
they describe a bypass of the token check itself.
