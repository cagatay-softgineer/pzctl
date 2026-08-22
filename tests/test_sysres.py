"""Tests for system resource sampling.

The platform readers are exercised against the machine the tests run on, since
both supported platforms are in CI. The parts that carry the real risk - turning
cumulative counters into rates, and keeping gaps intact through downsampling -
are driven with synthetic counters so they can be checked exactly.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from pzctl import sysres
from pzctl.config import Config


class FakeSupervisor:
    def __init__(self, pid=None, memory_mb=128.0):
        self._pid = os.getpid() if pid is None else pid
        self._memory = memory_mb

    def status(self):
        return {"pid": self._pid, "memory_mb": self._memory}


class SamplerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(Path(self._tmp.name) / "pzctl.json")


class ReaderTests(unittest.TestCase):
    """Every reader must answer or return None - never raise."""

    def test_process_cpu_is_readable_for_this_process(self):
        self.assertIsNotNone(sysres._process_cpu_seconds(os.getpid()))

    def test_process_cpu_accumulates(self):
        first = sysres._process_cpu_seconds(os.getpid())
        deadline = time.time() + 0.3
        while time.time() < deadline:
            pass
        self.assertGreater(sysres._process_cpu_seconds(os.getpid()), first)

    def test_process_cpu_of_a_missing_pid_is_none(self):
        self.assertIsNone(sysres._process_cpu_seconds(999999999))

    def test_process_cpu_of_no_pid_is_none(self):
        self.assertIsNone(sysres._process_cpu_seconds(0))

    def test_system_cpu_busy_never_exceeds_total(self):
        times = sysres._system_cpu_times()
        self.assertIsNotNone(times)
        busy, total = times
        self.assertGreaterEqual(total, busy)
        self.assertGreater(total, 0)

    def test_system_memory_is_sane(self):
        memory = sysres.system_memory()
        self.assertIsNotNone(memory)
        total, available = memory
        self.assertGreater(total, 0)
        self.assertGreaterEqual(total, available)

    def test_network_counters_only_increase(self):
        first = sysres._network_octets()
        self.assertIsNotNone(first)
        second = sysres._network_octets()
        self.assertGreaterEqual(second[0], first[0])
        self.assertGreaterEqual(second[1], first[1])


class DiskTests(unittest.TestCase):
    def test_reports_usage_for_an_existing_path(self):
        usage = sysres.disk_usage(Path(__file__).parent)
        self.assertIsNotNone(usage)
        self.assertGreater(usage["total"], 0)
        self.assertLessEqual(usage["used"], usage["total"])

    def test_walks_up_to_an_existing_parent(self):
        """A world that has never been saved must still report its volume."""
        missing = Path(__file__).resolve().parent / "no" / "such" / "dir"
        self.assertFalse(missing.exists())
        self.assertIsNotNone(sysres.disk_usage(missing))

    def test_unknown_volume_reports_nothing_rather_than_raising(self):
        self.assertIsNone(sysres.disk_usage("Q:\\nope" if os.name == "nt" else "\0bad"))


class RateTests(unittest.TestCase):
    def test_plain_delta(self):
        self.assertEqual(sysres._rate(1000, 3000, 2.0), 1000.0)

    def test_zero_elapsed_is_none_not_a_division_error(self):
        self.assertIsNone(sysres._rate(0, 100, 0))

    def test_a_wrapped_32bit_counter_is_not_a_negative_spike(self):
        """Interface counters are 32-bit and wrap; the rate must stay positive."""
        before = sysres.COUNTER_WRAP - 500
        after = 500  # wrapped, 1000 bytes really moved
        self.assertEqual(sysres._rate(before, after, 1.0), 1000.0)

    def test_an_implausible_jump_is_discarded(self):
        self.assertIsNone(sysres._rate(0, -(sysres.COUNTER_WRAP * 2), 1.0))


class SampleTests(SamplerTestCase):
    def test_first_sample_has_no_rates(self):
        """Rates need two readings; the first must not invent a zero."""
        sample = sysres.Sampler(self.cfg, FakeSupervisor()).sample()
        self.assertIsNone(sample["cpu_process"])
        self.assertIsNone(sample["cpu_system"])
        self.assertIsNone(sample["net_in_bps"])

    def test_second_sample_has_rates(self):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        sampler.sample()
        time.sleep(0.15)
        sample = sampler.sample()
        self.assertIsNotNone(sample["cpu_system"])
        self.assertIsNotNone(sample["cpu_process"])
        self.assertGreaterEqual(sample["cpu_process"], 0)

    def test_cpu_percentages_stay_within_bounds(self):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        sampler.sample()
        deadline = time.time() + 0.3
        while time.time() < deadline:
            pass
        sample = sampler.sample()
        for key in ("cpu_process", "cpu_system"):
            self.assertGreaterEqual(sample[key], 0.0, key)
            self.assertLessEqual(sample[key], 100.0, key)

    def test_a_restarted_server_does_not_spike(self):
        """A new pid resets the CPU counter; that is not 100% usage."""
        sampler = sysres.Sampler(self.cfg, FakeSupervisor(pid=os.getpid()))
        sampler.sample()
        time.sleep(0.05)
        sampler.sup = FakeSupervisor(pid=1)
        self.assertIsNone(sampler.sample()["cpu_process"])

    def test_memory_is_reported_for_the_machine_and_the_process(self):
        sample = sysres.Sampler(self.cfg, FakeSupervisor(memory_mb=256.0)).sample()
        self.assertEqual(sample["memory_process_mb"], 256.0)
        self.assertIsNotNone(sample["memory_total_mb"])
        self.assertGreaterEqual(sample["memory_percent"], 0)

    def test_a_dead_supervisor_does_not_break_sampling(self):
        class Broken:
            def status(self):
                raise RuntimeError("no server")

        sample = sysres.Sampler(self.cfg, Broken()).sample()
        self.assertIn("at", sample)
        self.assertIsNone(sample["cpu_process"])

    def test_no_supervisor_at_all_is_survivable(self):
        sample = sysres.Sampler(self.cfg, None).sample()
        self.assertIn("at", sample)


class HistoryTests(SamplerTestCase):
    def make(self, count, **fields):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        now = time.time()
        for i in range(count):
            row = {"at": now - (count - i), "cpu_process": float(i % 100)}
            row.update({k: v(i) for k, v in fields.items()})
            sampler._history.append(row)
        return sampler

    def test_history_is_bounded(self):
        sampler = self.make(0)
        for _ in range(sysres.HISTORY_SAMPLES + 250):
            sampler._history.append({"at": time.time()})
        self.assertEqual(len(sampler._history), sysres.HISTORY_SAMPLES)

    def test_downsampling_hits_the_requested_size(self):
        rows = self.make(1000).history(seconds=3600, points=50)
        self.assertEqual(len(rows), 50)

    def test_short_history_is_returned_untouched(self):
        rows = self.make(10).history(seconds=3600, points=180)
        self.assertEqual(len(rows), 10)

    def test_gaps_survive_downsampling(self):
        """An averaged-away gap would draw as a zero, inventing idle time."""
        sampler = self.make(600, cpu_system=lambda i: None if i < 300 else 50.0)
        rows = sampler.history(seconds=3600, points=60)
        self.assertTrue(any(r["cpu_system"] is None for r in rows))
        self.assertTrue(any(r["cpu_system"] == 50.0 for r in rows))

    def test_every_row_keeps_its_timestamp(self):
        rows = self.make(500).history(seconds=3600, points=40)
        self.assertTrue(all("at" in r for r in rows))
        self.assertEqual(rows, sorted(rows, key=lambda r: r["at"]))

    def test_window_excludes_older_samples(self):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        now = time.time()
        sampler._history.append({"at": now - 5000, "cpu_process": 1.0})
        sampler._history.append({"at": now - 10, "cpu_process": 2.0})
        rows = sampler.history(seconds=60, points=180)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cpu_process"], 2.0)

    def test_snapshot_shape(self):
        snap = self.make(20).snapshot()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["cpu_count"], sysres.cpu_count())
        self.assertIn("current", snap)
        self.assertEqual(len(snap["history"]), 20)

    def test_snapshot_with_no_samples_yet(self):
        snap = sysres.Sampler(self.cfg, FakeSupervisor()).snapshot()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["current"], {})
        self.assertEqual(snap["history"], [])


class ThreadTests(SamplerTestCase):
    def test_start_and_stop(self):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        sampler.start()
        self.addCleanup(sampler.stop)
        self.assertTrue(sampler._thread.is_alive())
        sampler.stop()
        sampler._thread.join(timeout=sysres.INTERVAL_SECONDS + 5)
        self.assertFalse(sampler._thread.is_alive())

    def test_starting_twice_does_not_make_a_second_thread(self):
        sampler = sysres.Sampler(self.cfg, FakeSupervisor())
        sampler.start()
        self.addCleanup(sampler.stop)
        first = sampler._thread
        sampler.start()
        self.assertIs(sampler._thread, first)


if __name__ == "__main__":
    unittest.main()
