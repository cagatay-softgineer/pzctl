"""Expose the panel through a Cloudflare Tunnel.

The README's advice for reaching the panel from elsewhere has been "bind to
0.0.0.0 and put it behind a VPN or reverse proxy yourself". That is correct and
unhelpful: the panel is plain HTTP with a bearer token, so the alternative
people actually take is exposing it directly.

`cloudflared` gives an HTTPS URL without any of that. Two forms:

    cloudflared tunnel --url http://127.0.0.1:8077   an ephemeral trycloudflare URL
    cloudflared tunnel run --token <token>           a named tunnel, stable hostname

The quick tunnel needs no Cloudflare account but its URL changes every run. The
named one needs a token from a tunnel the admin created, and keeps its hostname.

Both are supervised the same way the game server is: a child process whose
output is streamed to the console. No new Python dependency - cloudflared is an
external binary, like java.exe.

The tunnel is off unless asked for, and the panel's bearer token remains the
only thing standing between the internet and the server once it is on. That is
stated wherever this is offered rather than left implied.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

from .config import Config

# cloudflared prints the assigned URL to stderr, in a banner.
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_state: dict = {"running": False, "url": None, "mode": None, "error": None}
_process: dict = {"proc": None}
_lock = threading.Lock()


def find_cloudflared(cfg: Config) -> Path | None:
    configured = str(cfg.get("tunnel.cloudflared_path") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    found = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    return Path(found) if found else None


def status() -> dict:
    with _lock:
        return {"ok": True, **_state}


def _reader(proc, supervisor) -> None:
    log = supervisor.emit if supervisor is not None else (lambda *a, **k: None)
    for line in proc.stdout or []:
        line = line.rstrip()
        if not line:
            continue
        log(line, "tunnel")
        match = URL_RE.search(line)
        if match:
            with _lock:
                _state["url"] = match.group(0)
            log(f"tunnel: panel reachable at {match.group(0)}", "pzctl")
    code = proc.wait()
    with _lock:
        _state.update({"running": False, "error": None if code == 0 else f"cloudflared exited {code}"})
    log(f"tunnel: stopped (exit {code})", "pzctl")


def start(cfg: Config, supervisor) -> dict:
    """Start a tunnel. Named if a token is configured, quick otherwise."""
    with _lock:
        if _state["running"]:
            return {"ok": False, "error": "a tunnel is already running"}

    binary = find_cloudflared(cfg)
    if binary is None:
        return {
            "ok": False,
            "error": "cloudflared not found - install it and put it on PATH, or set "
            "tunnel.cloudflared_path in pzctl.json",
        }

    token = str(cfg.get("tunnel.token") or "").strip()
    port = int(cfg.get("http.port", 8077))
    if token:
        command = [str(binary), "tunnel", "run", "--token", token]
        mode = "named"
    else:
        command = [str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}"]
        mode = "quick"

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        return {"ok": False, "error": f"could not start cloudflared: {exc}"}

    with _lock:
        _state.update({"running": True, "url": None, "mode": mode, "error": None})
        _process["proc"] = proc

    threading.Thread(target=_reader, args=(proc, supervisor), name="pz-tunnel", daemon=True).start()

    if supervisor is not None:
        supervisor.emit(f"tunnel: starting {mode} tunnel via {binary.name}", "pzctl")
    return {
        "ok": True,
        "mode": mode,
        "port": port,
        # Said plainly at the moment of exposure, not buried in documentation.
        "warning": (
            "The panel is now reachable from the internet. Its bearer token is the "
            "only thing protecting it - anyone with that token can control your server."
        ),
    }


def stop(supervisor=None) -> dict:
    with _lock:
        proc = _process.get("proc")
        running = _state["running"]
    if not running or proc is None:
        return {"ok": False, "error": "no tunnel is running"}

    try:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    with _lock:
        _state.update({"running": False, "url": None})
    if supervisor is not None:
        supervisor.emit("tunnel: stopped", "pzctl")
    return {"ok": True}


def reset() -> None:
    with _lock:
        _state.update({"running": False, "url": None, "mode": None, "error": None})
        _process["proc"] = None
