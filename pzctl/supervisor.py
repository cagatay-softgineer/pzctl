"""Process supervisor: launches the PZ java server, tails its console, restarts it on crash."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from . import rcon
from .config import LOG_DIR, SERVER_DIR, Config

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
BACKOFF = "backoff"
CRASHED = "crashed"

CONSOLE_HISTORY = 4000
READY_MARKERS = ("SERVER STARTED", "Server Steam ID")
PLAYER_COUNT_RE = re.compile(r"\((\d+)\)")
CREATE_NO_WINDOW = 0x08000000


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _working_set_bytes(pid: int) -> int:
    """Resident memory of `pid`, or 0 if it cannot be read."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        return 0
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return 0
    finally:
        kernel32.CloseHandle(handle)


class Supervisor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = STOPPED
        self.proc: subprocess.Popen[str] | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.last_exit_code: int | None = None
        self.status_note = ""
        self.players = 0
        self.player_names: list[str] = []
        self.max_players = 0
        self.next_retry_at: float | None = None

        self.console: deque[dict] = deque(maxlen=CONSOLE_HISTORY)
        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self._lock = threading.RLock()
        self._intentional_stop = False
        self._restart_times: list[float] = []
        self._log_file = None
        self._log_day = ""
        self._stop_polling = threading.Event()

        threading.Thread(target=self._poll_loop, name="pz-poll", daemon=True).start()

    # -- console ---------------------------------------------------------

    def emit(self, text: str, stream: str = "pzctl") -> None:
        record = {"t": time.time(), "s": stream, "line": text.rstrip("\r\n")}
        self.console.append(record)
        with self._sub_lock:
            targets = list(self._subscribers)
        for sub in targets:
            try:
                sub.put_nowait(record)
            except queue.Full:
                pass
        self._write_log(record)

    def _write_log(self, record: dict) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            if self._log_file is None or day != self._log_day:
                if self._log_file is not None:
                    self._log_file.close()
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                self._log_file = open(
                    LOG_DIR / f"console-{day}.log", "a", encoding="utf-8", errors="replace"
                )
                self._log_day = day
            stamp = datetime.fromtimestamp(record["t"]).strftime("%H:%M:%S")
            self._log_file.write(f"[{stamp}] {record['line']}\n")
            self._log_file.flush()
        except OSError:
            pass

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=2000)
        with self._sub_lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._sub_lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    # -- command line ----------------------------------------------------

    def build_command(self) -> list[str]:
        cfg = self.cfg
        java = SERVER_DIR / "jre64" / "bin" / "java.exe"
        args = [
            str(java),
            "-Djava.awt.headless=true",
            f"-Dzomboid.steam={1 if cfg.get('steam') else 0}",
            "-Dzomboid.znetlog=1",
            "-Djava.library.path=natives/",
            "-XX:-CreateCoredumpOnCrash",
            "-XX:-OmitStackTraceInFastThrow",
        ]
        gc = (cfg.get("java.gc") or "").strip()
        if gc:
            args.append(gc)
        xms = (cfg.get("java.xms") or "").strip()
        xmx = (cfg.get("java.xmx") or "").strip()
        if xms:
            args.append(f"-Xms{xms}")
        if xmx:
            args.append(f"-Xmx{xmx}")
        args.extend(str(a) for a in (cfg.get("java.extra_args") or []) if str(a).strip())
        args += ["-cp", "java/;java/projectzomboid.jar", "zombie.network.GameServer"]
        args += ["-statistic", "0", "-servername", str(cfg.get("server_name"))]
        if not cfg.get("steam"):
            args.append("-nosteam")
        cache = (cfg.get("cache_dir") or "").strip()
        if cache:
            args.append(f"-cachedir={cache}")
        admin_user = (cfg.get("admin_username") or "").strip()
        admin_pass = cfg.get("admin_password") or ""
        if admin_user and admin_pass:
            args += ["-adminusername", admin_user, "-adminpassword", admin_pass]
        return args

    # -- lifecycle -------------------------------------------------------

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self.state in (STARTING, RUNNING, STOPPING):
                return False, f"server is already {self.state}"
            java = SERVER_DIR / "jre64" / "bin" / "java.exe"
            if not java.exists():
                return False, f"java runtime not found at {java}"
            if not (self.cfg.get("admin_password") or "").strip():
                return False, (
                    "no admin password set - the server would block on an "
                    "interactive prompt. Set it on the Launcher tab first."
                )

            cmd = self.build_command()
            env = dict(os.environ)
            self._intentional_stop = False
            self.next_retry_at = None
            self.status_note = ""
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    cwd=str(SERVER_DIR),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=CREATE_NO_WINDOW,
                )
            except OSError as exc:
                self.state = CRASHED
                self.status_note = str(exc)
                self.emit(f"failed to launch: {exc}", "error")
                return False, str(exc)

            self.state = STARTING
            self.started_at = time.time()
            self.ready_at = None
            self.last_exit_code = None
            self.emit(f"launching pid {self.proc.pid}: {' '.join(cmd)}", "pzctl")
            threading.Thread(target=self._reader, args=(self.proc,), name="pz-out", daemon=True).start()
            threading.Thread(target=self._monitor, args=(self.proc,), name="pz-mon", daemon=True).start()
            return True, f"started (pid {self.proc.pid})"

    def stop(self, timeout: float | None = None) -> tuple[bool, str]:
        with self._lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                self.state = STOPPED
                return False, "server is not running"
            self._intentional_stop = True
            self.state = STOPPING
            timeout = timeout if timeout is not None else float(
                self.cfg.get("supervisor.stop_timeout_sec", 90)
            )
        self.emit("stop requested - saving and quitting", "pzctl")
        if not self._send_stdin("quit"):
            try:
                self._rcon("quit")
            except Exception:
                pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return True, "server stopped"
            time.sleep(0.5)

        self.emit(f"still alive after {timeout:.0f}s - terminating", "pzctl")
        proc.terminate()
        try:
            proc.wait(20)
            return True, "server terminated"
        except subprocess.TimeoutExpired:
            proc.kill()
            self.emit("terminate ignored - killed", "error")
            return True, "server killed"

    def kill(self) -> tuple[bool, str]:
        with self._lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return False, "server is not running"
            self._intentional_stop = True
            self.state = STOPPING
        self.emit("hard kill requested", "pzctl")
        proc.kill()
        return True, "server killed"

    def restart(self) -> tuple[bool, str]:
        if self.is_alive():
            ok, msg = self.stop()
            if not ok:
                return ok, msg
            time.sleep(2)
        return self.start()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- io threads ------------------------------------------------------

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            self.emit(line, "server")
            if self.state == STARTING and any(m in line for m in READY_MARKERS):
                self.state = RUNNING
                self.ready_at = time.time()
                self.emit("server reports ready", "pzctl")

    def _monitor(self, proc: subprocess.Popen[str]) -> None:
        code = proc.wait()
        with self._lock:
            if self.proc is not proc:
                return
            self.last_exit_code = code
            self.players = 0
            self.player_names = []
            intentional = self._intentional_stop
            self.emit(f"process exited with code {code}", "pzctl" if intentional else "error")
            if intentional:
                self.state = STOPPED
                self.started_at = None
                self.ready_at = None
                return
            self.state = CRASHED

        if not self.cfg.get("supervisor.auto_restart", True):
            self.status_note = f"crashed (exit {code}); auto-restart disabled"
            return

        now = time.time()
        window = float(self.cfg.get("supervisor.restart_window_sec", 900))
        self._restart_times = [t for t in self._restart_times if now - t < window]
        max_restarts = int(self.cfg.get("supervisor.max_restarts", 5))
        if len(self._restart_times) >= max_restarts:
            self.status_note = (
                f"crash loop: {len(self._restart_times)} restarts within "
                f"{int(window)}s - giving up, start it manually"
            )
            self.emit(self.status_note, "error")
            return

        backoff_table = self.cfg.get("supervisor.backoff_sec") or [5, 15, 30, 60, 120]
        delay = float(backoff_table[min(len(self._restart_times), len(backoff_table) - 1)])
        self._restart_times.append(now)
        self.state = BACKOFF
        self.next_retry_at = now + delay
        self.status_note = f"crashed (exit {code}); restarting in {delay:.0f}s"
        self.emit(self.status_note, "pzctl")

        deadline = now + delay
        while time.time() < deadline:
            if self.state != BACKOFF:  # a manual start/stop overtook us
                return
            time.sleep(0.5)
        if self.state == BACKOFF:
            self.state = STOPPED
            self.start()

    # -- commands --------------------------------------------------------

    def _send_stdin(self, cmd: str, echo_as: str | None = None) -> bool:
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(cmd + "\n")
            proc.stdin.flush()
            self.emit(f"> {echo_as if echo_as is not None else cmd}", "input")
            return True
        except OSError:
            return False

    def _rcon(self, cmd: str) -> str:
        return rcon.run_command(
            self.cfg.get("rcon.host", "127.0.0.1"),
            int(self.cfg.get("rcon.port", 27015)),
            self.cfg.get("rcon.password", ""),
            cmd,
        )

    def rcon_ready(self) -> bool:
        return bool(self.cfg.get("rcon.enabled") and (self.cfg.get("rcon.password") or "").strip())

    def send_command(
        self, cmd: str, prefer: str = "auto", echo_as: str | None = None
    ) -> tuple[bool, str]:
        """Run a console command. `prefer` is auto | rcon | stdin.

        `echo_as` replaces the command in the console echo and the on-disk log.
        Commands that carry a secret - `adduser` takes a player's password -
        must pass a redacted form, or it lands in the log in cleartext.
        """
        cmd = cmd.strip()
        if not cmd:
            return False, "empty command"
        if not self.is_alive():
            return False, "server is not running"

        shown = echo_as if echo_as is not None else cmd
        use_rcon = prefer == "rcon" or (prefer == "auto" and self.rcon_ready())
        if use_rcon:
            try:
                reply = self._rcon(cmd)
                self.emit(f"> {shown}", "input")
                for line in (reply or "(no output)").splitlines():
                    self.emit(line, "rcon")
                self._note_players(cmd, reply)
                return True, reply
            except Exception as exc:
                if prefer == "rcon":
                    return False, f"rcon failed: {exc}"
                self.emit(f"rcon unavailable ({exc}) - falling back to console", "pzctl")

        if self._send_stdin(cmd, echo_as=shown):
            return True, "sent to server console"
        return False, "could not write to server console"

    def _note_players(self, cmd: str, reply: str) -> None:
        if not reply or not cmd.strip().lower().startswith("players"):
            return
        match = PLAYER_COUNT_RE.search(reply.splitlines()[0])
        if match:
            self.players = int(match.group(1))
        self.player_names = [
            line.lstrip("-").strip()
            for line in reply.splitlines()[1:]
            if line.strip()
        ]

    def _poll_loop(self) -> None:
        while not self._stop_polling.wait(15):
            if self.state != RUNNING or not self.rcon_ready():
                continue
            try:
                reply = self._rcon("players")
                self._note_players("players", reply)
            except Exception:
                pass

    # -- status ----------------------------------------------------------

    def status(self) -> dict:
        proc = self.proc
        pid = proc.pid if proc is not None and proc.poll() is None else None
        uptime = time.time() - self.started_at if (self.started_at and pid) else 0.0
        return {
            "state": self.state,
            "pid": pid,
            "uptime_sec": round(uptime, 1),
            "ready": self.state == RUNNING,
            "players": self.players,
            "player_names": self.player_names,
            "exit_code": self.last_exit_code,
            "note": self.status_note,
            "next_retry_in": (
                round(self.next_retry_at - time.time(), 1)
                if self.next_retry_at and self.state == BACKOFF
                else None
            ),
            "rcon_ready": self.rcon_ready(),
            "memory_mb": round(_working_set_bytes(pid) / (1024 * 1024), 1) if pid else 0.0,
            "xmx": self.cfg.get("java.xmx"),
            "server_name": self.cfg.get("server_name"),
        }

    def shutdown(self) -> None:
        self._stop_polling.set()
        if self.is_alive():
            self.stop()
