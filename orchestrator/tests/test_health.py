import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quo_swarm import config, health


class HealthTests(unittest.TestCase):
    def test_heartbeat_is_fresh_and_stale_or_previous_process_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "STATE", Path(folder)), \
             patch.dict(os.environ, {}, clear=True):
            self.assertFalse(health.snapshot()["ok"])
            health.heartbeat()
            self.assertTrue(health.snapshot()["ok"])
            p = Path(folder) / "heartbeat.json"
            report = json.loads(p.read_text())
            report["checked_at"] = time.time() - 180
            p.write_text(json.dumps(report))
            self.assertFalse(health.snapshot()["ok"])
            report.update(checked_at=time.time(), pid=0)
            p.write_text(json.dumps(report))
            self.assertFalse(health.snapshot()["ok"])

    def test_watchdog_notifies_only_the_systemd_socket(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "STATE", Path(folder)), \
             patch.dict(os.environ, {"NOTIFY_SOCKET": "@watchdog-test"}, clear=True), \
             patch.object(health.socket, "socket") as sock:
            health.heartbeat()
            sock.return_value.__enter__.return_value.sendto.assert_called_once_with(
                b"WATCHDOG=1", "\0watchdog-test")


if __name__ == "__main__":
    unittest.main()
