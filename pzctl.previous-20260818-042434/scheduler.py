"""Cron-lite scheduler for nightly restarts (with in-game warnings) and timed backups."""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta

from . import backup, notify
from .config import Config

TICK_SECONDS = 20
# Slack for a tick that arrived late. Wide enough to survive a stalled tick,
# narrow enough that we never announce "restarts in 15 minutes" at minute 12.
WARN_GRACE_SECONDS = 90


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hour, minute = text.strip().split(":")
        hour, minute = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return None


def next_occurrence(hhmm: str, now: datetime | None = None) -> datetime | None:
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        return None
    now = now or datetime.now()
    target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    if target <= now - timedelta(minutes=2):
        target += timedelta(days=1)
    return target


DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _wanted_days(job: dict) -> set[int] | None:
    """Weekday numbers a job may run on, or None for every day."""
    raw = job.get("days")
    if not raw:
        return None
    wanted = set()
    for entry in raw:
        name = str(entry).strip().lower()[:3]
        if name in DAY_NAMES:
            wanted.add(DAY_NAMES.index(name))
    # An unrecognised day list would otherwise silently mean "never run".
    return wanted or None


def next_run(job: dict, now: datetime | None = None) -> datetime | None:
    """When this job next fires.

    Three shapes, all optional additions to the original daily HH:MM job so
    existing pzctl.json files keep working untouched:

      {"time": "04:00"}                     every day
      {"time": "04:00", "days": ["sat"]}    only on those weekdays
      {"every_hours": 6}                    every N hours from midnight
    """
    now = now or datetime.now()

    every = job.get("every_hours")
    if every:
        try:
            hours = int(every)
        except (TypeError, ValueError):
            return None
        if hours < 1 or hours > 24:
            return None
        # Anchored at midnight so the times are predictable and survive a
        # restart, rather than drifting from whenever the daemon last started.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = (now - midnight).total_seconds() / 3600
        step = int(elapsed // hours) + 1
        return midnight + timedelta(hours=step * hours)

    target = next_occurrence(job.get("time", ""), now)
    if target is None:
        return None

    days = _wanted_days(job)
    if days is not None:
        for _ in range(8):
            if target.weekday() in days:
                return target
            target += timedelta(days=1)
        return None
    return target


class Scheduler:
    def __init__(self, cfg: Config, supervisor):
        self.cfg = cfg
        self.sup = supervisor
        self._fired: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pz-sched", daemon=True)
        # Jobs run on their own thread: a multi-minute world zip must never
        # delay the tick that owes players a restart warning.
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, name="pz-sched-job", daemon=True).start()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _submit(self, fn, *args) -> None:
        self._jobs.put((fn, args))

    def _worker(self) -> None:
        while True:
            fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception as exc:
                self.sup.emit(f"scheduled job failed: {exc}", "error")
            finally:
                self._jobs.task_done()

    # -- introspection for the UI ---------------------------------------

    def upcoming(self) -> list[dict]:
        now = datetime.now()
        events = []
        for kind, jobs in (
            ("restart", self.cfg.get("schedule.restarts") or []),
            ("backup", self.cfg.get("schedule.backups") or []),
        ):
            for job in jobs:
                if not job.get("enabled", True):
                    continue
                target = next_run(job, now)
                if target is None:
                    continue
                events.append(
                    {
                        "kind": kind,
                        "time": job.get("time") or f"every {job.get('every_hours')}h",
                        "at": target.timestamp(),
                        "in_sec": round((target - now).total_seconds()),
                        "warn_minutes": job.get("warn_minutes") or [],
                    }
                )
        return sorted(events, key=lambda e: e["at"])

    # -- loop ------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self._tick(datetime.now())
            except Exception as exc:  # a scheduler bug must not kill the daemon
                self.sup.emit(f"scheduler error: {exc}", "error")

    def _tick(self, now: datetime) -> None:
        self._prune_fired(now)
        for index, job in enumerate(self.cfg.get("schedule.restarts") or []):
            self._tick_restart(index, job, now)
        for index, job in enumerate(self.cfg.get("schedule.backups") or []):
            self._tick_backup(index, job, now)

    def _due(self, key: str) -> bool:
        if key in self._fired:
            return False
        self._fired.add(key)
        return True

    def _prune_fired(self, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        self._fired = {k for k in self._fired if today in k or yesterday in k}

    def _tick_restart(self, index: int, job: dict, now: datetime) -> None:
        if not job.get("enabled", True):
            return
        target = next_run(job, now)
        if target is None:
            return
        seconds_out = (target - now).total_seconds()
        stamp = target.strftime("%Y-%m-%d %H:%M")

        for warn in sorted({int(m) for m in (job.get("warn_minutes") or [])}, reverse=True):
            low = warn * 60 - WARN_GRACE_SECONDS
            if low < seconds_out <= warn * 60 and self._due(f"warn|{index}|{stamp}|{warn}"):
                unit = "minute" if warn == 1 else "minutes"
                self._submit(self._broadcast, f"Server restarts in {warn} {unit}.")
                notify.event(self.cfg, "restart_warning", f"in {warn} {unit}")

        if -120 <= seconds_out <= TICK_SECONDS and self._due(f"restart|{index}|{stamp}"):
            self._submit(self._do_restart, str(job.get("time")))

    def _do_restart(self, label: str) -> None:
        self.sup.emit(f"scheduled restart ({label})", "pzctl")
        if self.sup.is_alive():
            self._broadcast("Server is restarting now.")
            time.sleep(3)
            self.sup.restart()
        else:
            self.sup.start()

    def _tick_backup(self, index: int, job: dict, now: datetime) -> None:
        if not job.get("enabled", True):
            return
        target = next_run(job, now)
        if target is None:
            return
        seconds_out = (target - now).total_seconds()
        stamp = target.strftime("%Y-%m-%d %H:%M")
        if -120 <= seconds_out <= TICK_SECONDS and self._due(f"backup|{index}|{stamp}"):
            self._submit(self._do_backup, str(job.get("time")))

    def _do_backup(self, label: str) -> None:
        self.sup.emit(f"scheduled backup ({label})", "pzctl")
        result = backup.run(self.cfg, self.sup, reason="scheduled")
        if not result.get("ok"):
            self.sup.emit(f"scheduled backup failed: {result.get('error')}", "error")

    def _broadcast(self, message: str) -> None:
        if not self.sup.is_alive():
            return
        ok, _ = self.sup.send_command(f'servermsg "{message}"')
        if ok:
            self.sup.emit(f"broadcast: {message}", "pzctl")
