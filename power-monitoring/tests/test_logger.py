"""Offline checks for the Tapo logger: no device, network, sudo or python-kasa needed."""
import ast
import csv
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tapo_logger", ROOT / "tapo_logger.py")
tapo_logger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tapo_logger)

ENERGY = "Energy"


def outlet(alias, on, watts=None, energy=True):
    modules = {}
    if energy:
        modules[ENERGY] = SimpleNamespace(
            current_consumption=watts, voltage=120.44, current=0.1234,
            consumption_today=0.25, consumption_this_month=3.5)
    return SimpleNamespace(alias=alias, is_on=on, modules=modules)


class LoggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tapo test ")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_rows_cover_every_outlet_and_round_values(self):
        dev = SimpleNamespace(children=[outlet("a", True, 51.06), outlet("b", False, 0), outlet("c", True, energy=False)])
        got = list(tapo_logger.rows(dev, ENERGY, self.now))
        self.assertEqual([r["outlet"] for r in got], ["a", "b", "c"])
        self.assertEqual(got[0]["watts"], 51.1)
        self.assertEqual(got[0]["volts"], 120.4)
        self.assertEqual(got[0]["amps"], 0.123)
        self.assertEqual(got[1]["on"], 0)
        self.assertEqual(got[2]["watts"], "")
        self.assertEqual(got[0]["time"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(set(got[0]), set(tapo_logger.FIELDS))

    def test_missing_reading_is_blank_not_zero(self):
        dev = SimpleNamespace(children=[outlet("a", True, None)])
        self.assertEqual(next(tapo_logger.rows(dev, ENERGY, self.now))["watts"], "")

    def test_append_writes_one_header_and_atomic_latest(self):
        dev = SimpleNamespace(children=[outlet("a", True, 10), outlet("b", True, 20)])
        for _ in range(3):
            tapo_logger.append(self.dir, list(tapo_logger.rows(dev, ENERGY, self.now)))
        with (self.dir / "power.csv").open() as f:
            lines = list(csv.reader(f))
        self.assertEqual(lines[0], tapo_logger.FIELDS)
        self.assertEqual(len(lines), 1 + 3 * 2)
        latest = json.loads((self.dir / "latest.json").read_text())
        self.assertEqual([r["outlet"] for r in latest], ["a", "b"])
        self.assertFalse((self.dir / "latest.json.tmp").exists())

    def test_logger_never_calls_device_mutators(self):
        tree = ast.parse((ROOT / "tapo_logger.py").read_text())
        called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        forbidden = {a for a in called if a.startswith(("turn_", "set_", "reboot", "factory", "update_firmware"))}
        self.assertEqual(forbidden, set(), f"logger must stay read-only, found {forbidden}")
        self.assertNotIn("feature", called)

    def test_mac_normalisation(self):
        self.assertEqual(tapo_logger.norm_mac(" aa-bb-cc-dd-ee-ff "), "AA:BB:CC:DD:EE:FF")

    def test_credentials_must_have_two_lines(self):
        (self.dir / "tapo").write_text("only-email@example.com\n")
        with self.assertRaises((ValueError, ModuleNotFoundError)):
            tapo_logger.load_credentials(str(self.dir))


class ViewerAndInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tapo test ")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        dev = SimpleNamespace(children=[outlet("host-a", True, 50), outlet("host-b", False, 0)])
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        for _ in range(5):
            tapo_logger.append(self.dir, list(tapo_logger.rows(dev, ENERGY, now)))

    def run_viewer(self, *args):
        env = {**os.environ, "TAPO_LOG_DIR": str(self.dir)}
        return subprocess.run(["bash", str(ROOT / "tapo-power"), *args], env=env, capture_output=True, text=True, check=True).stdout

    def test_viewer_totals_latest_snapshot(self):
        out = self.run_viewer()
        self.assertIn("host-a", out)
        self.assertIn("OFF", out)
        self.assertRegex(out, r"total\s+50\.0 W")

    def test_viewer_history_keeps_header_and_limits_rows(self):
        lines = self.run_viewer("host-a", "2").strip().splitlines()
        self.assertTrue(lines[0].startswith("time"))
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("host-a" in line for line in lines[1:]))

    def test_installer_rejects_bad_input_before_sudo(self):
        for args in ([], ["--host", "bad host;rm"], ["--host", "10.0.0.2", "--mac", "nope"],
                     ["--host", "10.0.0.2", "--interval", "5"], ["--host", "10.0.0.2"]):
            r = subprocess.run(["bash", str(ROOT / "install.sh"), *args], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("ERROR", r.stderr, args)
            self.assertNotIn("sudo", r.stdout + r.stderr, args)


if __name__ == "__main__":
    unittest.main()
