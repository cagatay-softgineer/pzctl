"""Daemon entry point: wires config, supervisor, scheduler and the HTTP panel together."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import webbrowser

from . import __version__, httpd
from .config import DATA_DIR, LOG_DIR, Config
from .scheduler import Scheduler
from .supervisor import Supervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pzctl", description="Project Zomboid dedicated server daemon + control panel"
    )
    parser.add_argument("--host", help="override the listen address (e.g. 0.0.0.0 for LAN)")
    parser.add_argument("--port", type=int, help="override the listen port")
    parser.add_argument("--open", action="store_true", help="open the panel in a browser")
    parser.add_argument("--start", action="store_true", help="start the game server immediately")
    parser.add_argument("--no-schedule", action="store_true", help="disable timed restarts/backups")
    args = parser.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    cfg = Config()
    if args.host:
        cfg.set("http.host", args.host)
    if args.port:
        cfg.set("http.port", args.port)

    sup = Supervisor(cfg)
    sched = Scheduler(cfg, sup)
    ctx = httpd.Context(cfg, sup, sched)

    try:
        server = httpd.serve(ctx)
    except OSError as exc:
        print(f"cannot bind {cfg.get('http.host')}:{cfg.get('http.port')} - {exc}", file=sys.stderr)
        return 1

    host = cfg.get("http.host")
    if host in ("0.0.0.0", "::"):
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                display_host = probe.getsockname()[0]
        except OSError:
            display_host = "127.0.0.1"
    else:
        display_host = host
    url = f"http://{display_host}:{cfg.get('http.port')}/"

    print(f"pzctl {__version__} listening on http://{host}:{cfg.get('http.port')}/")
    print(f"  panel:  {url}")
    print(f"  token:  {cfg.get('http.token')}   (needed only from other machines)")
    print(f"  config: {cfg.path}")
    print("  Ctrl+C to shut down (the game server is stopped cleanly first)")
    print("  SIGTERM does the same, so `docker stop` and systemd stop cleanly too")

    if not args.no_schedule:
        sched.start()

    threading.Thread(target=server.serve_forever, name="pz-http", daemon=True).start()

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if args.start or cfg.get("supervisor.autostart"):
        threading.Timer(1.0, sup.start).start()

    stopping = threading.Event()

    def _shutdown(*_):
        if stopping.is_set():
            return
        stopping.set()

    # SIGTERM is what `docker stop`, systemd and `kill` send. Without a handler
    # for it the process is killed outright and the world never gets its final
    # save, so it matters as much as Ctrl+C does.
    for name in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"):
        handler = getattr(signal, name, None)
        if handler is None:
            continue  # SIGBREAK is Windows-only, SIGHUP is not
        try:
            signal.signal(handler, _shutdown)
        except (OSError, ValueError):
            # Some signals cannot be caught in every context; the rest still are.
            pass

    try:
        while not stopping.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass

    print("\nshutting down...")
    sched.stop()
    server.shutdown()
    sup.shutdown()
    time.sleep(0.3)
    print("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
