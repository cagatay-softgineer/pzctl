"""System resource sampling: CPU, memory, disk and network, over time.

The panel has always shown the server's memory as a single live number. That
answers "how much is it using now" and nothing else - not whether it has been
climbing all evening, not whether the machine is out of CPU, not whether the
disk is about to fill and take the backups down with it.

This module samples the four resources on an interval and keeps the history in
memory, so the panel can draw trends rather than instants.

Two figures are kept for each of CPU and memory: the **server process** and the
**whole machine**. They answer different questions. A server pinned at 100% of
one core looks fine on a machine-wide graph with eight cores; a machine starved
by something else entirely looks fine on a process graph. Diagnosing "the server
is lagging" needs both.

Everything here is standard library. There is no psutil and no new dependency:

- CPU comes from `GetProcessTimes`/`GetSystemTimes` on Windows and
  `/proc/<pid>/stat` + `/proc/stat` on Linux, all through counters that only
  ever increase - a percentage is the delta between two samples, which is why
  the first sample after startup reports no CPU figure at all.
- Memory reuses the working-set reader the supervisor already has.
- Disk is `shutil.disk_usage`, which is portable and needs no platform code.
- Network is `/proc/net/dev` on Linux and `GetIfTable` on Windows.

Every reader returns `None` rather than raising when a platform cannot answer,
and a sample with `None` in it is still a valid sample. A metric that cannot be
read on some machine must leave a gap in one graph, never break the panel.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
import time
from collections import deque
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")

# 5s is frequent enough to show a stall as it happens and cheap enough to leave
# running forever. 1440 samples keeps two hours, which covers an evening's play
# session - long enough to answer "was it degrading before it died?".
INTERVAL_SECONDS = 5.0
HISTORY_SAMPLES = 1440

# Windows counts CPU time in 100-nanosecond units.
FILETIME_PER_SECOND = 10_000_000

# 32-bit interface counters wrap. At the sample interval a wrap needs multi-Gbps
# traffic, but treating a wrap as a negative rate would draw a spike downward.
COUNTER_WRAP = 2 ** 32


def cpu_count() -> int:
    return os.cpu_count() or 1


# -- CPU counters --------------------------------------------------------
#
# Each returns cumulative seconds of CPU time, or None. Percentages are worked
# out from the difference between two of these, never from a single reading.


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    @property
    def seconds(self) -> float:
        return ((self.high << 32) | self.low) / FILETIME_PER_SECOND


def _process_cpu_seconds(pid: int) -> float | None:
    """Total CPU time consumed by `pid` since it started."""
    if not pid:
        return None
    if IS_WINDOWS:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return None
            try:
                creation, exited, kernel, user = (_FileTime() for _ in range(4))
                ok = kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
                if not ok:
                    return None
                return kernel.seconds + user.seconds
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None

    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            content = handle.read()
        # The comm field can contain spaces and brackets, so everything before
        # the final ')' is skipped rather than split on.
        fields = content[content.rfind(")") + 2:].split()
        ticks = os.sysconf("SC_CLK_TCK")
        # After the skipped pid and comm, utime and stime are fields 12 and 13.
        return (int(fields[11]) + int(fields[12])) / ticks
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _system_cpu_times() -> tuple[float, float] | None:
    """(busy_seconds, total_seconds) across all cores, cumulative."""
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            idle, kernel, user = (_FileTime() for _ in range(3))
            ok = kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
            if not ok:
                return None
            # GetSystemTimes reports idle time *inside* the kernel figure, so
            # kernel + user is the total and idle must be subtracted from it
            # rather than added alongside.
            total = kernel.seconds + user.seconds
            return total - idle.seconds, total
        except (AttributeError, OSError):
            return None

    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            fields = handle.readline().split()
        if not fields or fields[0] != "cpu":
            return None
        ticks = os.sysconf("SC_CLK_TCK")
        values = [int(v) for v in fields[1:]]
        total = sum(values) / ticks
        # user nice system idle iowait irq softirq steal - idle and iowait are
        # both time the CPU was not doing work.
        idle = (values[3] + (values[4] if len(values) > 4 else 0)) / ticks
        return total - idle, total
    except (OSError, ValueError, IndexError, AttributeError):
        return None


# -- memory --------------------------------------------------------------


def system_memory() -> tuple[int, int] | None:
    """(total_bytes, available_bytes) for the machine."""
    if IS_WINDOWS:
        class Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None
        return None

    try:
        total = available = None
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
                if total is not None and available is not None:
                    break
        if total is not None and available is not None:
            return total, available
    except (OSError, ValueError, IndexError):
        pass
    return None


# -- disk ----------------------------------------------------------------


def disk_usage(path) -> dict | None:
    """Free space on the volume holding `path`.

    Reported for the save directory rather than the install: that is the one
    that grows without bound, and it is the one whose filling up silently
    breaks backups.

    The directory need not exist. `shutil.disk_usage` requires a real path, but
    the interesting figure is the *volume*, which exists as soon as the drive
    does - so this walks up to the nearest existing parent. Without that, a
    server that has not saved its world yet would report no disk information at
    all, which is exactly when a full disk is most likely to go unnoticed.
    """
    try:
        # resolve() itself rejects some inputs - an embedded null raises
        # ValueError before any filesystem call - so it is inside the guard
        # rather than in front of it.
        probe = Path(str(path)).resolve()
    except (OSError, ValueError):
        return None

    for candidate in (probe, *probe.parents):
        try:
            usage = shutil.disk_usage(str(candidate))
            break
        except (OSError, ValueError):
            continue
    else:
        return None
    return {
        "total": int(usage.total),
        "used": int(usage.used),
        "free": int(usage.free),
        "percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
    }


# -- network -------------------------------------------------------------


class _MibIfRow(ctypes.Structure):
    """MIB_IFROW, trimmed to the fields actually read.

    The layout must still match the real struct exactly up to the last field
    used, because the kernel writes the whole thing.
    """

    _fields_ = [
        ("wszName", ctypes.c_wchar * 256),
        ("dwIndex", ctypes.c_ulong),
        ("dwType", ctypes.c_ulong),
        ("dwMtu", ctypes.c_ulong),
        ("dwSpeed", ctypes.c_ulong),
        ("dwPhysAddrLen", ctypes.c_ulong),
        ("bPhysAddr", ctypes.c_ubyte * 8),
        ("dwAdminStatus", ctypes.c_ulong),
        ("dwOperStatus", ctypes.c_ulong),
        ("dwLastChange", ctypes.c_ulong),
        ("dwInOctets", ctypes.c_ulong),
        ("dwInUcastPkts", ctypes.c_ulong),
        ("dwInNUcastPkts", ctypes.c_ulong),
        ("dwInDiscards", ctypes.c_ulong),
        ("dwInErrors", ctypes.c_ulong),
        ("dwInUnknownProtos", ctypes.c_ulong),
        ("dwOutOctets", ctypes.c_ulong),
        ("dwOutUcastPkts", ctypes.c_ulong),
        ("dwOutNUcastPkts", ctypes.c_ulong),
        ("dwOutDiscards", ctypes.c_ulong),
        ("dwOutErrors", ctypes.c_ulong),
        ("dwOutQLen", ctypes.c_ulong),
        ("dwDescrLen", ctypes.c_ulong),
        ("bDescr", ctypes.c_ubyte * 256),
    ]


IF_TYPE_SOFTWARE_LOOPBACK = 24


def _network_octets() -> tuple[int, int] | None:
    """(bytes_in, bytes_out) summed across real interfaces, cumulative."""
    if IS_WINDOWS:
        try:
            iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
            size = ctypes.c_ulong(0)
            # First call sizes the buffer; 122 is ERROR_INSUFFICIENT_BUFFER.
            iphlpapi.GetIfTable(None, ctypes.byref(size), False)
            if not size.value:
                return None
            buffer = ctypes.create_string_buffer(size.value)
            if iphlpapi.GetIfTable(buffer, ctypes.byref(size), False) != 0:
                return None
            count = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong))[0]
            rows = ctypes.cast(
                ctypes.byref(buffer, ctypes.sizeof(ctypes.c_ulong)),
                ctypes.POINTER(_MibIfRow),
            )
            inbound = outbound = 0
            for index in range(count):
                row = rows[index]
                if row.dwType == IF_TYPE_SOFTWARE_LOOPBACK:
                    continue
                inbound += int(row.dwInOctets)
                outbound += int(row.dwOutOctets)
            return inbound, outbound
        except (AttributeError, OSError, ValueError, IndexError):
            return None

    try:
        inbound = outbound = 0
        with open("/proc/net/dev", encoding="utf-8") as handle:
            for line in handle.readlines()[2:]:
                name, _, rest = line.partition(":")
                if name.strip() == "lo":
                    continue
                fields = rest.split()
                inbound += int(fields[0])
                outbound += int(fields[8])
        return inbound, outbound
    except (OSError, ValueError, IndexError):
        return None


def _rate(previous: int, current: int, elapsed: float) -> float | None:
    """Bytes per second between two cumulative counters."""
    if elapsed <= 0:
        return None
    delta = current - previous
    if delta < 0:
        # A 32-bit counter wrapped, or the interface was reset. Assume a wrap;
        # if it was a reset the single sample is wrong but the next is right.
        delta += COUNTER_WRAP
        if delta < 0 or delta > COUNTER_WRAP:
            return None
    return delta / elapsed


class Sampler:
    """Samples resources on an interval and keeps a bounded history.

    Started and stopped with the daemon, like the scheduler. Sampling failures
    are swallowed: this is instrumentation, and it must never be the reason the
    panel or the daemon stops working.
    """

    def __init__(self, cfg, supervisor):
        self.cfg = cfg
        self.sup = supervisor
        self._history: deque[dict] = deque(maxlen=HISTORY_SAMPLES)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Previous cumulative counters, needed to turn totals into rates.
        self._last: dict = {}

    # -- lifecycle --

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="pz-sysres", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Prime the counters so the first recorded sample already has rates,
        # rather than a gap at the left edge of every graph after a restart.
        try:
            self.sample(record=False)
        except Exception:  # noqa: BLE001 - instrumentation must not crash the daemon
            pass
        while not self._stop.wait(INTERVAL_SECONDS):
            try:
                self.sample()
            except Exception:  # noqa: BLE001
                pass

    # -- sampling --

    def sample(self, record: bool = True) -> dict:
        """Take one reading. Rates are relative to the previous call."""
        now = time.time()
        previous, self._last = self._last, {"at": now}
        elapsed = now - previous["at"] if "at" in previous else 0.0

        pid = 0
        try:
            status = self.sup.status() if self.sup is not None else {}
            pid = int(status.get("pid") or 0)
        except Exception:  # noqa: BLE001
            status = {}

        sample: dict = {"at": round(now, 1)}

        # -- CPU. A restarted server resets its counter, so a negative delta
        #    means a new process and the sample is skipped rather than shown
        #    as a huge negative spike.
        process_cpu = _process_cpu_seconds(pid)
        self._last["process_cpu"] = process_cpu
        self._last["pid"] = pid
        sample["cpu_process"] = None
        if (
            process_cpu is not None
            and previous.get("process_cpu") is not None
            and previous.get("pid") == pid
            and elapsed > 0
        ):
            delta = process_cpu - previous["process_cpu"]
            if delta >= 0:
                sample["cpu_process"] = round(
                    min(delta / elapsed / cpu_count() * 100, 100.0), 1
                )

        system_cpu = _system_cpu_times()
        self._last["system_cpu"] = system_cpu
        sample["cpu_system"] = None
        if system_cpu is not None and previous.get("system_cpu") is not None:
            busy = system_cpu[0] - previous["system_cpu"][0]
            total = system_cpu[1] - previous["system_cpu"][1]
            if total > 0 and busy >= 0:
                sample["cpu_system"] = round(min(busy / total * 100, 100.0), 1)

        # -- memory
        memory_mb = status.get("memory_mb")
        sample["memory_process_mb"] = float(memory_mb) if memory_mb else None
        memory = system_memory()
        if memory:
            total, available = memory
            sample["memory_total_mb"] = round(total / 1024 ** 2, 1)
            sample["memory_used_mb"] = round((total - available) / 1024 ** 2, 1)
            sample["memory_percent"] = round((total - available) / total * 100, 1)
        else:
            sample["memory_total_mb"] = None
            sample["memory_used_mb"] = None
            sample["memory_percent"] = None

        # -- disk
        disk = None
        try:
            disk = disk_usage(self.cfg.save_dir)
        except Exception:  # noqa: BLE001
            disk = None
        sample["disk_percent"] = disk["percent"] if disk else None
        sample["disk_free_gb"] = round(disk["free"] / 1024 ** 3, 2) if disk else None

        # -- network
        octets = _network_octets()
        self._last["network"] = octets
        sample["net_in_bps"] = None
        sample["net_out_bps"] = None
        if octets is not None and previous.get("network") is not None:
            inbound = _rate(previous["network"][0], octets[0], elapsed)
            outbound = _rate(previous["network"][1], octets[1], elapsed)
            sample["net_in_bps"] = round(inbound, 1) if inbound is not None else None
            sample["net_out_bps"] = round(outbound, 1) if outbound is not None else None

        if record:
            with self._lock:
                self._history.append(sample)
        return sample

    # -- reading back --

    def current(self) -> dict:
        with self._lock:
            return dict(self._history[-1]) if self._history else {}

    def history(self, seconds: int = 3600, points: int = 180) -> list[dict]:
        """History for the last `seconds`, averaged down to about `points`.

        Downsampling happens here rather than in the browser so the response
        stays small no matter how long the daemon has been up.
        """
        cutoff = time.time() - max(seconds, 0)
        with self._lock:
            rows = [s for s in self._history if s.get("at", 0) >= cutoff]
        if len(rows) <= points or points <= 0:
            return rows

        bucket_size = len(rows) / points
        out: list[dict] = []
        keys = [k for k in rows[0] if k != "at"]
        for index in range(points):
            chunk = rows[int(index * bucket_size):int((index + 1) * bucket_size)]
            if not chunk:
                continue
            averaged: dict = {"at": chunk[-1]["at"]}
            for key in keys:
                values = [s[key] for s in chunk if s.get(key) is not None]
                # A bucket with no readings stays None so the gap survives
                # downsampling instead of being averaged away into a zero.
                averaged[key] = round(sum(values) / len(values), 1) if values else None
            out.append(averaged)
        return out

    def snapshot(self, seconds: int = 3600, points: int = 180) -> dict:
        return {
            "ok": True,
            "interval_seconds": INTERVAL_SECONDS,
            "cpu_count": cpu_count(),
            "current": self.current(),
            "history": self.history(seconds, points),
        }
