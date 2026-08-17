"""Tests for JVM sizing advice, GC logging, network tuning and port checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pzctl import perf, pzini
from pzctl.config import Config

GB = 1024**3


class PerfTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = Config(self.dir / "pzctl.json")
        self.cfg.set("server_name", "servertest")
        self.cfg.set("zomboid_dir", str(self.dir / "Zomboid"))
        self.cfg.server_config_dir.mkdir(parents=True, exist_ok=True)

        self._orig = perf.total_memory_bytes
        self.addCleanup(lambda: setattr(perf, "total_memory_bytes", self._orig))

    def with_ram(self, gigabytes: float) -> None:
        perf.total_memory_bytes = lambda: int(gigabytes * GB)

    def write_ini(self, extra: str = "") -> None:
        self.cfg.ini_path.write_text(f"PVP=true\r\n{extra}", encoding="utf-8", newline="")


class ParseSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(perf.parse_size("4g"), 4 * GB)
        self.assertEqual(perf.parse_size("512m"), 512 * 1024**2)
        self.assertEqual(perf.parse_size("2048k"), 2048 * 1024)

    def test_case_and_b_suffix(self):
        self.assertEqual(perf.parse_size("4G"), 4 * GB)
        self.assertEqual(perf.parse_size("4gb"), 4 * GB)

    def test_invalid(self):
        for bad in ("", "lots", "4", "g", None):
            self.assertIsNone(perf.parse_size(bad), repr(bad))


class JvmAdviceTests(PerfTestCase):
    def test_sensible_settings_produce_no_notes(self):
        self.with_ram(16)
        self.cfg.set("java.xms", "4g")
        self.cfg.set("java.xmx", "4g")
        self.assertEqual(perf.jvm_advice(self.cfg)["notes"], [])

    def test_xms_above_ram_is_an_error(self):
        """This does not degrade performance - the JVM refuses to start."""
        self.with_ram(8)
        self.cfg.set("java.xms", "16g")
        self.cfg.set("java.xmx", "16g")
        notes = perf.jvm_advice(self.cfg)["notes"]
        errors = [n for n in notes if n["level"] == "error"]
        self.assertTrue(any("will not start" in n["message"] for n in errors))

    def test_xmx_above_ram_is_an_error(self):
        self.with_ram(8)
        self.cfg.set("java.xms", "1g")
        self.cfg.set("java.xmx", "16g")
        notes = perf.jvm_advice(self.cfg)["notes"]
        self.assertTrue(any(n["level"] == "error" for n in notes))

    def test_xmx_close_to_ram_is_a_warning(self):
        self.with_ram(8)
        self.cfg.set("java.xms", "1g")
        self.cfg.set("java.xmx", "7g")
        notes = perf.jvm_advice(self.cfg)["notes"]
        self.assertTrue(any("little for the operating system" in n["message"] for n in notes))

    def test_xms_greater_than_xmx_is_an_error(self):
        self.with_ram(32)
        self.cfg.set("java.xms", "8g")
        self.cfg.set("java.xmx", "4g")
        notes = perf.jvm_advice(self.cfg)["notes"]
        self.assertTrue(any("larger than xmx" in n["message"] for n in notes))

    def test_stock_value_is_called_out(self):
        """16g is what StartServer64.bat ships with and is meant to be lowered."""
        self.with_ram(16)
        self.cfg.set("java.xms", "1g")
        self.cfg.set("java.xmx", "16g")
        notes = perf.jvm_advice(self.cfg)["notes"]
        self.assertTrue(any("StartServer64.bat" in n["message"] for n in notes))

    def test_stock_value_fine_on_a_large_host(self):
        self.with_ram(64)
        self.cfg.set("java.xms", "16g")
        self.cfg.set("java.xmx", "16g")
        self.assertEqual(perf.jvm_advice(self.cfg)["notes"], [])

    def test_malformed_size_reported(self):
        self.with_ram(16)
        self.cfg.set("java.xmx", "lots")
        notes = perf.jvm_advice(self.cfg)["notes"]
        self.assertTrue(any("not a valid size" in n["message"] for n in notes))

    def test_unknown_ram_does_not_crash(self):
        perf.total_memory_bytes = lambda: None
        self.cfg.set("java.xmx", "4g")
        result = perf.jvm_advice(self.cfg)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["total_memory_gb"])


class GcLoggingTests(PerfTestCase):
    def test_enable_adds_the_flag(self):
        result = perf.set_gc_logging(self.cfg, True)
        self.assertIn(perf.GC_LOG_FLAG, result["extra_args"])
        self.assertTrue(result["restart_required"])

    def test_disable_removes_it(self):
        perf.set_gc_logging(self.cfg, True)
        result = perf.set_gc_logging(self.cfg, False)
        self.assertNotIn(perf.GC_LOG_FLAG, result["extra_args"])

    def test_enabling_twice_does_not_duplicate(self):
        perf.set_gc_logging(self.cfg, True)
        result = perf.set_gc_logging(self.cfg, True)
        self.assertEqual(result["extra_args"].count(perf.GC_LOG_FLAG), 1)

    def test_other_args_are_preserved(self):
        self.cfg.set("java.extra_args", ["-XX:+SomethingElse"])
        result = perf.set_gc_logging(self.cfg, True)
        self.assertIn("-XX:+SomethingElse", result["extra_args"])

    def test_state_is_reported_by_advice(self):
        perf.set_gc_logging(self.cfg, True)
        self.assertTrue(perf.jvm_advice(self.cfg)["gc_logging"])


class NetworkOptionTests(PerfTestCase):
    def test_missing_ini(self):
        self.assertFalse(perf.network_options(self.cfg)["ok"])

    def test_defaults_when_absent(self):
        self.write_ini()
        options = {o["key"]: o for o in perf.network_options(self.cfg)["options"]}
        self.assertEqual(options["MaxPacketsPerSecond"]["value"], "300")
        self.assertFalse(options["MaxPacketsPerSecond"]["explicit"])

    def test_reads_explicit_values(self):
        self.write_ini("MaxPacketsPerSecond=500\r\n")
        options = {o["key"]: o for o in perf.network_options(self.cfg)["options"]}
        self.assertEqual(options["MaxPacketsPerSecond"]["value"], "500")
        self.assertTrue(options["MaxPacketsPerSecond"]["explicit"])

    def test_writes_a_valid_value(self):
        self.write_ini()
        result = perf.set_network_options(self.cfg, {"MaxPacketsPerSecond": 500})
        self.assertTrue(result["ok"])
        self.assertEqual(pzini.read(self.cfg.ini_path)["MaxPacketsPerSecond"], "500")

    def test_rejects_out_of_range(self):
        """The server clamps or rejects these, so catching it here saves a restart."""
        self.write_ini()
        for value in (99, 1001):
            result = perf.set_network_options(self.cfg, {"MaxPacketsPerSecond": value})
            self.assertFalse(result["ok"], value)
            self.assertIn("between 100 and 1000", result["error"])

    def test_rejects_unknown_option(self):
        self.write_ini()
        self.assertFalse(perf.set_network_options(self.cfg, {"PVP": "false"})["ok"])

    def test_rejects_non_numeric(self):
        self.write_ini()
        self.assertFalse(perf.set_network_options(self.cfg, {"PingLimit": "lots"})["ok"])

    def test_booleans(self):
        self.write_ini()
        perf.set_network_options(self.cfg, {"UPnP": False})
        self.assertEqual(pzini.read(self.cfg.ini_path)["UPnP"], "false")

    def test_a_rejected_write_changes_nothing(self):
        self.write_ini("MaxPacketsPerSecond=300\r\n")
        perf.set_network_options(self.cfg, {"MaxPacketsPerSecond": 5000})
        self.assertEqual(pzini.read(self.cfg.ini_path)["MaxPacketsPerSecond"], "300")


class PortStatusTests(PerfTestCase):
    def test_reports_default_ports(self):
        self.write_ini()
        ports = {p["key"]: p for p in perf.port_status(self.cfg)["ports"]}
        self.assertEqual(ports["DefaultPort"]["port"], 16261)
        self.assertEqual(ports["UDPPort"]["port"], 16262)

    def test_uses_configured_ports(self):
        self.write_ini("DefaultPort=17000\r\n")
        ports = {p["key"]: p for p in perf.port_status(self.cfg)["ports"]}
        self.assertEqual(ports["DefaultPort"]["port"], 17000)

    def test_detects_a_bound_port(self):
        import socket

        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        self.addCleanup(holder.close)

        self.write_ini(f"DefaultPort={port}\r\n")
        ports = {p["key"]: p for p in perf.port_status(self.cfg)["ports"]}
        self.assertTrue(ports["DefaultPort"]["in_use"])

    def test_note_does_not_claim_health(self):
        """PZ exposes no health endpoint, so 'in use' is the honest claim."""
        self.write_ini()
        note = perf.port_status(self.cfg)["note"]
        self.assertIn("does not prove", note)


if __name__ == "__main__":
    unittest.main()
