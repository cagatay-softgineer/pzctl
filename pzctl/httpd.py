"""HTTP control API + static panel. Stdlib only; console streams over Server-Sent Events."""

from __future__ import annotations

import json
import mimetypes
import queue
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import (
    anticheat,
    backup,
    crashdiag,
    liveconfig,
    logconfig,
    logs,
    modcheck,
    moderation,
    mods,
    optionmeta,
    pzini,
    rcon,
    sandbox,
    serverupdate,
    updates,
    upgrade,
    whitelist,
)
from .config import Config

WEB_DIR = Path(__file__).resolve().parent / "web"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class Context:
    def __init__(self, cfg: Config, supervisor, scheduler):
        self.cfg = cfg
        self.sup = supervisor
        self.sched = scheduler


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pzctl"
    ctx: Context = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):  # noqa: A003 - silence stdlib access log
        pass

    # -- plumbing --------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"ok": False, "error": message}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _is_loopback(self) -> bool:
        return self.client_address[0] in LOOPBACK

    def _authorized(self, params: dict) -> bool:
        token = self.ctx.cfg.get("http.token")
        supplied = self.headers.get("X-PZ-Token") or (params.get("token") or [""])[0]
        return bool(token) and supplied == token

    # -- routing ---------------------------------------------------------

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_HEAD(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if not path.startswith("/api/"):
                return self._serve_static(path)
            if not self._authorized(params):
                return self._error(401, "missing or invalid token")
            handler = ROUTES.get((method, path))
            if handler is None:
                return self._error(404, f"no route for {method} {path}")
            handler(self, params)
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            try:
                self._error(500, f"{type(exc).__name__}: {exc}")
            except Exception:
                pass

    def _serve_static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error(404, "not found")
        data = target.read_bytes()
        if target.name == "index.html":
            # Loopback clients get the token baked in; remote clients must type it.
            token = self.ctx.cfg.get("http.token") if self._is_loopback() else ""
            data = data.replace(b"__PZCTL_TOKEN__", token.encode("utf-8"))
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or target.suffix in (".js", ".css", ".html"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    # -- endpoints -------------------------------------------------------

    def api_status(self, params):
        status = self.ctx.sup.status()
        status["schedule"] = self.ctx.sched.upcoming()
        status["paths"] = {
            "ini": str(self.ctx.cfg.ini_path),
            "sandbox": str(self.ctx.cfg.sandbox_path),
            "save": str(self.ctx.cfg.save_dir),
            "backups": str(self.ctx.cfg.backup_dir),
        }
        status["ini_exists"] = self.ctx.cfg.ini_path.exists()
        status["version"] = __version__
        status["ok"] = True
        self._json(status)

    def api_console(self, params):
        limit = int((params.get("limit") or ["400"])[0])
        lines = list(self.ctx.sup.console)[-max(1, min(limit, 4000)):]
        self._json({"ok": True, "lines": lines})

    def api_console_stream(self, params):
        sub = self.ctx.sup.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last_ping = time.time()
        try:
            while True:
                try:
                    record = sub.get(timeout=5)
                    payload = json.dumps(record)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    if time.time() - last_ping > 10:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.ctx.sup.unsubscribe(sub)

    def api_start(self, params):
        ok, message = self.ctx.sup.start()
        self._json({"ok": ok, "message": message})

    def api_stop(self, params):
        ok, message = self.ctx.sup.stop()
        self._json({"ok": ok, "message": message})

    def api_restart(self, params):
        ok, message = self.ctx.sup.restart()
        self._json({"ok": ok, "message": message})

    def api_kill(self, params):
        ok, message = self.ctx.sup.kill()
        self._json({"ok": ok, "message": message})

    def api_command(self, params):
        body = self._body()
        ok, message = self.ctx.sup.send_command(
            body.get("cmd", ""), body.get("prefer", "auto")
        )
        self._json({"ok": ok, "message": message})

    def api_get_ini(self, params):
        cfg = self.ctx.cfg
        values = pzini.read(cfg.ini_path)
        self._json(
            {
                "ok": True,
                "exists": cfg.ini_path.exists(),
                "path": str(cfg.ini_path),
                "values": values,
                "meta": optionmeta.describe_all(values, "ini"),
                "groups": optionmeta.groups_for("ini"),
            }
        )

    def api_set_ini(self, params):
        body = self._body()
        changes = body.get("changes") or {}
        if not changes:
            return self._json({"ok": True, "changed": []})
        try:
            changed = pzini.write(self.ctx.cfg.ini_path, {k: str(v) for k, v in changes.items()})
        except FileNotFoundError:
            return self._error(409, "server .ini does not exist yet - start the server once")

        result = {"ok": True, "changed": changed}
        # The file is always written first and stays the source of truth; the
        # live push is an extra step over the top of it.
        if body.get("apply_live") and changed:
            result["live"] = liveconfig.apply(
                self.ctx.cfg, self.ctx.sup, {k: str(changes[k]) for k in changed}
            )
        self._json(result)

    def api_get_sandbox(self, params):
        cfg = self.ctx.cfg
        values = sandbox.to_dict(cfg.sandbox_path)
        self._json(
            {
                "ok": True,
                "exists": cfg.sandbox_path.exists(),
                "path": str(cfg.sandbox_path),
                "values": values,
                "meta": optionmeta.describe_all(values, "sandbox"),
                "groups": optionmeta.groups_for("sandbox"),
                "preset": optionmeta.DEFAULT_PRESET,
                "presets": optionmeta.preset_names(),
            }
        )

    def api_set_sandbox(self, params):
        changes = self._body().get("changes") or {}
        if not changes:
            return self._json({"ok": True, "changed": []})
        try:
            changed = sandbox.write(self.ctx.cfg.sandbox_path, changes)
        except FileNotFoundError:
            return self._error(409, "SandboxVars.lua does not exist yet - start the server once")
        except ValueError as exc:
            return self._error(400, str(exc))
        self._json({"ok": True, "changed": changed})

    def api_get_mods(self, params):
        payload = mods.read(self.ctx.cfg)
        payload["ok"] = True
        self._json(payload)

    def api_set_mods(self, params):
        body = self._body()
        try:
            changed = mods.write(
                self.ctx.cfg,
                body.get("mods") or [],
                body.get("workshop_items") or [],
                body.get("map") or [],
            )
        except FileNotFoundError:
            return self._error(409, "server .ini does not exist yet - start the server once")
        self._json({"ok": True, "changed": changed})

    def api_get_launcher(self, params):
        config = self.ctx.cfg.data
        if not self._is_loopback():
            # Secrets are round-tripped for editing locally; remote clients only get a
            # signal that a value is set, never the cleartext admin/RCON/http secrets.
            config = json.loads(json.dumps(config))
            if config.get("admin_password"):
                config["admin_password"] = "********"
            if config.get("rcon", {}).get("password"):
                config["rcon"]["password"] = "********"
            config.get("http", {}).pop("token", None)
        self._json({"ok": True, "config": config, "cmdline": self.ctx.sup.build_command()})

    def api_set_launcher(self, params):
        patch = self._body().get("config") or {}
        patch.pop("http", None)  # port/token changes need a daemon restart; not via UI
        if patch.get("admin_password") == "********":
            patch.pop("admin_password")
        if isinstance(patch.get("rcon"), dict) and patch["rcon"].get("password") == "********":
            patch["rcon"].pop("password")
        self.ctx.cfg.update(patch)
        self._json({"ok": True, "config": self.ctx.cfg.data, "cmdline": self.ctx.sup.build_command()})

    def api_backups(self, params):
        self._json({"ok": True, "backups": backup.listing(self.ctx.cfg), "dir": str(self.ctx.cfg.backup_dir)})

    def api_run_backup(self, params):
        self._json(backup.run(self.ctx.cfg, self.ctx.sup, reason="manual"))

    def api_inspect_backup(self, params):
        self._json(backup.inspect(self.ctx.cfg, (params.get("name") or [""])[0]))

    def api_restore_backup(self, params):
        body = self._body()
        # Restoring destroys the current world, so the client has to say so
        # explicitly - a stray POST must not be enough.
        if body.get("confirm") is not True:
            return self._error(400, "restore requires confirm: true")
        self._json(
            backup.restore(
                self.ctx.cfg,
                body.get("name", ""),
                self.ctx.sup,
                pre_backup=body.get("pre_backup", True),
            )
        )

    def api_moderate(self, params):
        body = self._body()
        self._json(
            moderation.act(
                self.ctx.sup,
                body.get("action", ""),
                body.get("target", ""),
                body.get("reason", ""),
                bool(body.get("ban_ip")),
            )
        )

    def api_diagnose(self, params):
        self._json(crashdiag.analyse(self.ctx.cfg))

    def api_mod_check_start(self, params):
        self._json(modcheck.request(self.ctx.cfg, self.ctx.sup))

    def api_mod_check_poll(self, params):
        self._json(modcheck.poll(self.ctx.cfg))

    def api_whitelist(self, params):
        self._json(whitelist.status(self.ctx.cfg))

    def api_whitelist_mode(self, params):
        self._json(
            whitelist.set_mode(self.ctx.cfg, self.ctx.sup, bool(self._body().get("enabled")))
        )

    def api_whitelist_user(self, params):
        body = self._body()
        username = body.get("username", "")
        if body.get("action") == "remove":
            return self._json(whitelist.remove_user(self.ctx.sup, username))
        self._json(whitelist.add_user(self.ctx.sup, username, body.get("password", "")))

    def api_check_updates(self, params):
        self._json(updates.check())

    def api_upgrade(self, params):
        # Replacing pzctl's own code is not something a stray POST should do.
        if self._body().get("confirm") is not True:
            return self._error(400, "upgrade requires confirm: true")
        self._json(upgrade.apply(self.ctx.sup))

    def api_log_level(self, params):
        body = self._body()
        self._json(logconfig.set_level(self.ctx.sup, body.get("type", ""), body.get("level", "")))

    def api_access_level(self, params):
        body = self._body()
        self._json(
            moderation.set_access_level(
                self.ctx.sup, body.get("username", ""), body.get("level", "")
            )
        )

    def api_get_anticheat(self, params):
        self._json(anticheat.read(self.ctx.cfg))

    def api_set_anticheat(self, params):
        self._json(anticheat.write(self.ctx.cfg, self._body().get("types") or {}))

    def api_server_update(self, params):
        body = self._body()
        if body.get("confirm") is not True:
            return self._error(400, "server update requires confirm: true")
        self._json(serverupdate.start(self.ctx.cfg, self.ctx.sup))

    def api_server_update_status(self, params):
        self._json(serverupdate.status())

    def api_logs(self, params):
        self._json(
            {
                "ok": True,
                "logs": logs.discover(self.ctx.cfg),
                "dir": str(logs.log_dir(self.ctx.cfg)),
            }
        )

    def api_log_tail(self, params):
        name = (params.get("name") or [""])[0]
        raw_bytes = (params.get("bytes") or [""])[0]
        try:
            max_bytes = int(raw_bytes) if raw_bytes else logs.DEFAULT_TAIL_BYTES
        except ValueError:
            max_bytes = logs.DEFAULT_TAIL_BYTES
        self._json(logs.tail(self.ctx.cfg, name, max_bytes))

    def api_rcon_test(self, params):
        cfg = self.ctx.cfg
        try:
            reply = rcon.run_command(
                cfg.get("rcon.host"), int(cfg.get("rcon.port")), cfg.get("rcon.password"), "players"
            )
            self._json({"ok": True, "message": reply or "connected"})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)})


ROUTES = {
    ("GET", "/api/status"): Handler.api_status,
    ("GET", "/api/console"): Handler.api_console,
    ("GET", "/api/console/stream"): Handler.api_console_stream,
    ("POST", "/api/server/start"): Handler.api_start,
    ("POST", "/api/server/stop"): Handler.api_stop,
    ("POST", "/api/server/restart"): Handler.api_restart,
    ("POST", "/api/server/kill"): Handler.api_kill,
    ("POST", "/api/command"): Handler.api_command,
    ("GET", "/api/config/ini"): Handler.api_get_ini,
    ("POST", "/api/config/ini"): Handler.api_set_ini,
    ("GET", "/api/config/sandbox"): Handler.api_get_sandbox,
    ("POST", "/api/config/sandbox"): Handler.api_set_sandbox,
    ("GET", "/api/config/mods"): Handler.api_get_mods,
    ("POST", "/api/config/mods"): Handler.api_set_mods,
    ("GET", "/api/config/launcher"): Handler.api_get_launcher,
    ("POST", "/api/config/launcher"): Handler.api_set_launcher,
    ("GET", "/api/backups"): Handler.api_backups,
    ("GET", "/api/backups/inspect"): Handler.api_inspect_backup,
    ("POST", "/api/backups/run"): Handler.api_run_backup,
    ("POST", "/api/backups/restore"): Handler.api_restore_backup,
    ("POST", "/api/moderate"): Handler.api_moderate,
    ("POST", "/api/access-level"): Handler.api_access_level,
    ("POST", "/api/server/update"): Handler.api_server_update,
    ("GET", "/api/server/update"): Handler.api_server_update_status,
    ("GET", "/api/anticheat"): Handler.api_get_anticheat,
    ("POST", "/api/anticheat"): Handler.api_set_anticheat,
    ("GET", "/api/diagnose"): Handler.api_diagnose,
    ("POST", "/api/mods/check"): Handler.api_mod_check_start,
    ("GET", "/api/mods/check"): Handler.api_mod_check_poll,
    ("GET", "/api/whitelist"): Handler.api_whitelist,
    ("POST", "/api/whitelist/mode"): Handler.api_whitelist_mode,
    ("POST", "/api/whitelist/user"): Handler.api_whitelist_user,
    ("GET", "/api/updates"): Handler.api_check_updates,
    ("POST", "/api/updates/apply"): Handler.api_upgrade,
    ("POST", "/api/logs/level"): Handler.api_log_level,
    ("GET", "/api/logs"): Handler.api_logs,
    ("GET", "/api/logs/tail"): Handler.api_log_tail,
    ("POST", "/api/rcon/test"): Handler.api_rcon_test,
}


def serve(ctx: Context) -> ThreadingHTTPServer:
    Handler.ctx = ctx
    host = ctx.cfg.get("http.host", "127.0.0.1")
    port = int(ctx.cfg.get("http.port", 8077))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
