"""Regressions observed during real ThinkPad onboarding. No network or sudo."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tailscale_health", ROOT / "lib/tailscale_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def test_online_without_warnings(self):
        for warnings in (None, []):
            self.assertEqual(health.problems({"BackendState": "Running", "Self": {"Online": True},
                                              "Health": warnings}), [])

    def test_assigned_ip_and_running_daemon_do_not_hide_offline_control(self):
        status = {"BackendState": "Running", "TailscaleIPs": ["100.64.0.1"],
                  "Self": {"Online": False}, "Health": []}
        self.assertTrue(health.problems(status))

    def test_health_warning_fails_even_when_online(self):
        warnings = ["Unable to connect to the Tailscale coordination server"]
        result = health.problems({"BackendState": "Running", "Self": {"Online": True},
                                  "Health": warnings})
        self.assertIn(warnings[0], result[0])

    def test_missing_login_or_status_is_not_healthy(self):
        for status in (None, [], {}, {"BackendState": "NeedsLogin"},
                       {"BackendState": "Stopped", "Self": {"Online": True}}):
            self.assertTrue(health.problems(status))

    def test_cli_rejects_bad_input_and_does_not_print_peer_inventory(self):
        for payload, expected in (("not json", 1), (json.dumps({
            "BackendState": "Running", "Self": {"Online": True}, "Health": [],
            "Peer": {"private-peer": {"HostName": "do-not-print-this"}}}), 0)):
            result = subprocess.run(["python3", str(ROOT / "lib/tailscale_health.py")],
                                    input=payload, text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertNotIn("do-not-print-this", result.stdout + result.stderr)

    def test_os_release_does_not_replace_requested_hostname(self):
        with tempfile.TemporaryDirectory(prefix="swarmbank-release-") as directory:
            base = Path(directory)
            release = base / "os-release"
            release.write_text('ID=ubuntu\nVERSION_ID="26.04"\nNAME="Ubuntu"\n')
            (base / "systemd").mkdir()
            # Substitute only host probes, so this runs on macOS and root CI too.
            source = (ROOT / "lib/common.sh").read_text()
            source = source.replace("/etc/os-release", str(release))
            source = source.replace("/run/systemd/system", str(base / "systemd"))
            source = source.replace("/cdrom/casper", str(base / "no-live-cd"))
            source = source.replace("/run/casper", str(base / "no-live-run"))
            source = source.replace("$EUID", "1000")
            script = source + '''
uname() { if [[ $1 == -s ]]; then echo Linux; else echo x86_64; fi; }
NAME=syam-test
require_ubuntu_user
[[ $NAME == syam-test ]]
'''
            result = subprocess.run(["bash", "-e", "-c", script], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
