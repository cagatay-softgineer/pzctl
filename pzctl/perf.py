"""Performance and health: JVM memory sanity, GC logging, and liveness.

Four things that are otherwise invisible until something breaks:

- **Memory sizing.** The stock `StartServer64.bat` allocates 16 GB. Copying its
  values onto a smaller host is a common first-run failure, and `-Xms` above
  what the machine can reserve stops the JVM booting at all rather than merely
  running badly.
- **GC logging.** Off by default and worth turning on when diagnosing the memory
  creep behind "just restart it nightly" advice.
- **Network tuning.** A handful of .ini options with documented ranges, scattered
  through 135 others.
- **Liveness.** A running process is not the same as a reachable server. pzctl
  can see the first; the second needs the port probed.

There is no PZ-native metrics endpoint, so nothing here invents one. Health is
"is the port answering", not a tick-rate graph pzctl has no way to obtain.
"""

from __future__ import annotations

import socket
import ctypes
import re

from . import pzini
from .config import Config

STOCK_HEAP = "16g"
SIZE_RE = re.compile(r"^(\d+)\s*([kmg])b?$", re.IGNORECASE)
UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}

# Documented ranges. Values outside them are rejected by the server or clamped,
# so catching them here saves a restart to find out.
NETWORK_OPTIONS = {
    "MaxPacketsPerSecond": {"default": "300", "min": 100, "max": 1000, "kind": "int"},
    "PingLimit": {"default": "0", "min": 0, "max": 60000, "kind": "int"},
    "LoginQueueEnabled": {"default": "false", "kind": "bool"},
    "LoginQueueConnectTimeout": {"default": "60", "min": 20, "max": 1200, "kind": "int"},
    "UPnP": {"default": "true", "kind": "bool"},
}

GC_LOG_FLAG = "-Xlog:gc*=info,heap*=debug,safepoint=info"


def parse_size(value: str) -> int | None:
    """Turn '4g' or '512m' into bytes, or None if it is not a JVM size."""
    match = SIZE_RE.match(str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * UNITS[match.group(2).lower()]


def total_memory_bytes() -> int | None:
    """Physical RAM, or None where it cannot be read."""
    try:  # Windows
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

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    except (AttributeError, OSError):
        pass
    try:  # Linux
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def jvm_advice(cfg: Config) -> dict:
    """Check the configured heap against the machine it will run on."""
    xms_raw = str(cfg.get("java.xms") or "").strip()
    xmx_raw = str(cfg.get("java.xmx") or "").strip()
    xms, xmx = parse_size(xms_raw), parse_size(xmx_raw)
    total = total_memory_bytes()

    notes: list[dict] = []

    if xmx_raw and xmx is None:
        notes.append({"level": "error", "message": f"xmx '{xmx_raw}' is not a valid size (use 4g, 512m)"})
    if xms_raw and xms is None:
        notes.append({"level": "error", "message": f"xms '{xms_raw}' is not a valid size (use 4g, 512m)"})

    if xms and xmx and xms > xmx:
        notes.append({"level": "error", "message": "xms is larger than xmx - the JVM will not start"})

    if total:
        if xms and xms > total:
            # This one does not degrade; it fails outright.
            notes.append({
                "level": "error",
                "message": (
                    f"xms ({xms_raw}) is more than this machine's memory "
                    f"({total / 1024**3:.1f} GB) - the server will not start"
                ),
            })
        if xmx and xmx > total:
            notes.append({
                "level": "error",
                "message": (
                    f"xmx ({xmx_raw}) is more than this machine's memory "
                    f"({total / 1024**3:.1f} GB)"
                ),
            })
        elif xmx and xmx > total * 0.85:
            notes.append({
                "level": "warning",
                "message": (
                    f"xmx ({xmx_raw}) leaves little for the operating system - "
                    f"this machine has {total / 1024**3:.1f} GB"
                ),
            })

    if xmx_raw.lower() == STOCK_HEAP and (not total or parse_size(STOCK_HEAP) > total * 0.85):
        notes.append({
            "level": "warning",
            "message": (
                "16g is the value the stock StartServer64.bat ships with - it is "
                "meant to be lowered to match the host"
            ),
        })

    return {
        "ok": True,
        "xms": xms_raw,
        "xmx": xmx_raw,
        "total_memory_gb": round(total / 1024**3, 1) if total else None,
        "gc": cfg.get("java.gc"),
        "gc_logging": GC_LOG_FLAG in (cfg.get("java.extra_args") or []),
        "notes": notes,
    }


def set_gc_logging(cfg: Config, enabled: bool) -> dict:
    """Add or remove the GC logging flags from the JVM arguments."""
    args = [str(a) for a in (cfg.get("java.extra_args") or [])]
    present = GC_LOG_FLAG in args
    if enabled and not present:
        args.append(GC_LOG_FLAG)
    elif not enabled and present:
        args = [a for a in args if a != GC_LOG_FLAG]
    cfg.set("java.extra_args", args)
    cfg.save()
    return {"ok": True, "enabled": enabled, "extra_args": args, "restart_required": True}


def network_options(cfg: Config) -> dict:
    """The tuning options, with their documented ranges and current values."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}
    values = pzini.read(cfg.ini_path)
    out = []
    for key, spec in NETWORK_OPTIONS.items():
        raw = values.get(key)
        out.append({
            "key": key,
            "value": raw if raw is not None else spec["default"],
            "explicit": raw is not None,
            **{k: v for k, v in spec.items() if k != "default"},
        })
    return {"ok": True, "options": out}


def set_network_options(cfg: Config, changes: dict) -> dict:
    """Write tuning options, refusing values the server would reject."""
    if not cfg.ini_path.exists():
        return {"ok": False, "error": "server .ini does not exist yet - start the server once"}

    patch: dict[str, str] = {}
    for key, value in (changes or {}).items():
        spec = NETWORK_OPTIONS.get(key)
        if spec is None:
            return {"ok": False, "error": f"not a tuning option: {key}"}
        if spec["kind"] == "bool":
            patch[key] = "true" if str(value).strip().lower() in ("true", "1", "yes", "on") else "false"
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{key} must be a whole number"}
        if not spec["min"] <= number <= spec["max"]:
            return {
                "ok": False,
                "error": f"{key} must be between {spec['min']} and {spec['max']}, got {number}",
            }
        patch[key] = str(number)

    if not patch:
        return {"ok": True, "changed": []}
    return {"ok": True, "changed": pzini.write(cfg.ini_path, patch), "restart_required": True}


def port_status(cfg: Config, timeout: float = 1.5) -> dict:
    """Is anything bound to the server's UDP ports?

    A UDP probe cannot prove a server is answering - nothing replies to an
    empty datagram - but a bind failure does prove something holds the port.
    So this reports 'in use' rather than 'healthy', which is the honest claim.
    """
    values = pzini.read(cfg.ini_path) if cfg.ini_path.exists() else {}
    ports = []
    for key, default in (("DefaultPort", "16261"), ("UDPPort", "16262")):
        raw = str(values.get(key) or default).strip()
        try:
            ports.append((key, int(raw)))
        except ValueError:
            continue

    results = []
    for key, port in ports:
        in_use = False
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(timeout)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                in_use = True
            finally:
                probe.close()
        except OSError:
            in_use = False
        results.append({"key": key, "port": port, "in_use": in_use})

    return {
        "ok": True,
        "ports": results,
        "note": (
            "'in use' means something is bound to the port. It does not prove the "
            "server is answering players - Project Zomboid exposes no health "
            "endpoint pzctl could ask."
        ),
    }
