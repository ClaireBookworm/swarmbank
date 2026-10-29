"""Offline checks of safety properties and SSH configuration; no sudo/network."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure", ROOT / "lib/configure.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="swarmbank test ")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def key(self, name="controller"):
        path = self.home / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
        return path.with_suffix(".pub")

    def test_public_key_import_is_idempotent_and_preserves_other_keys(self):
        first, second = self.key(), self.key("another")
        configure.authorize(self.home, first)
        configure.authorize(self.home, second)
        before = (self.home / ".ssh/authorized_keys").read_bytes()
        configure.authorize(self.home, first)
        target = self.home / ".ssh/authorized_keys"
        self.assertEqual(before, target.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn(second.read_text().strip(), target.read_text())

    def test_private_key_and_multiline_input_rejected_without_writes(self):
        pub = self.key()
        with self.assertRaises(ValueError):
            configure.authorize(self.home, pub.with_suffix(""))
        self.assertFalse((self.home / ".ssh").exists())
        bad = self.home / "multiple.pub"
        bad.write_text(pub.read_text() * 2)
        with self.assertRaises(ValueError):
            configure.authorize(self.home, bad)
        self.assertFalse((self.home / ".ssh").exists())

    def test_restricted_existing_key_is_not_duplicated_as_unrestricted(self):
        pub = self.key()
        target = self.home / ".ssh/authorized_keys"
        target.parent.mkdir()
        original = 'restrict ' + pub.read_text()
        target.write_text(original)
        configure.authorize(self.home, pub)
        self.assertEqual(original, target.read_text())

    def test_path_loads_before_noninteractive_return_and_preserves_content(self):
        original = 'case $- in *i*) ;; *) return;; esac\n# user configuration\n'
        (self.home / ".bashrc").write_text(original)
        (self.home / ".bash_profile").write_text('# custom login profile\n')
        configure.shell_path(self.home)
        first = (self.home / ".bashrc").read_text()
        configure.shell_path(self.home)
        self.assertEqual(first, (self.home / ".bashrc").read_text())
        self.assertTrue(first.endswith(original))
        self.assertIn(configure.PATH_MARKER, (self.home / ".bash_profile").read_text())
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        tool = bin_dir / "swarmbank-test-tool"
        tool.write_text('#!/bin/sh\nexit 0\n')
        tool.chmod(0o755)
        result = subprocess.run(['bash', '-c', '. "$HOME/.bashrc"; command -v swarmbank-test-tool'],
                                env={**os.environ, "HOME": str(self.home)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(tool))

    def test_github_config_preserves_unrelated_hosts_and_precedes_defaults(self):
        ssh = self.home / ".ssh"
        ssh.mkdir()
        original = 'Host existing\n    HostName example.invalid\nHost *\n    User other\n'
        (ssh / "config").write_text(original)
        configure.github_config(self.home)
        first = (ssh / "config").read_text()
        configure.github_config(self.home)
        self.assertEqual(first, (ssh / "config").read_text())
        self.assertTrue(first.endswith(original))
        result = subprocess.run(['ssh', '-G', '-F', str(ssh / 'config'), 'github.com'],
                                text=True, capture_output=True, check=True)
        self.assertIn('user git\n', result.stdout)
        self.assertIn('hostname github.com\n', result.stdout)
        self.assertIn('identityfile ~/.ssh/id_ed25519_swarmbank_github\n', result.stdout)

    def test_existing_github_identity_is_not_overwritten(self):
        ssh = self.home / ".ssh"
        ssh.mkdir()
        original = 'Host github.com other\n    IdentityFile ~/.ssh/custom\n'
        (ssh / "config").write_text(original)
        with self.assertRaises(ValueError):
            configure.github_config(self.home)
        self.assertEqual(original, (ssh / "config").read_text())

    def test_global_ssh_options_still_apply_to_unrelated_hosts(self):
        ssh = self.home / ".ssh"
        ssh.mkdir()
        (ssh / "config").write_text('ServerAliveInterval 37\nHost existing\n    HostName example.invalid\n')
        configure.github_config(self.home)
        result = subprocess.run(['ssh', '-G', '-F', str(ssh / 'config'), 'existing'],
                                text=True, capture_output=True, check=True)
        self.assertIn('serveraliveinterval 37\n', result.stdout)

    def test_malformed_managed_block_is_not_silently_replaced(self):
        ssh = self.home / ".ssh"
        ssh.mkdir()
        original = configure.SSH_BEGIN + '\nHost private\n'
        (ssh / "config").write_text(original)
        with self.assertRaises(ValueError):
            configure.github_config(self.home)
        self.assertEqual(original, (ssh / "config").read_text())


class ScriptTests(unittest.TestCase):
    def test_help_and_invalid_flags_are_read_only(self):
        with tempfile.TemporaryDirectory() as home:
            for script in ('setup.sh', 'connect.sh', 'doctor.sh', 'power.sh'):
                result = subprocess.run(['bash', str(ROOT / script), '--help'],
                                        env={**os.environ, 'HOME': home}, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['bash', str(ROOT / 'setup.sh'), '--name'],
                                    env={**os.environ, 'HOME': home}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(home).iterdir()), [])

    def test_names_reject_option_and_config_injection(self):
        for name, valid in [('swarm-01', True), ('a', True), ('a' * 63, True),
                            ('a' * 64, False), ('-bad', False), ('bad-', False),
                            ('has space', False), ('host\nHost *', False), ('$(id)', False)]:
            result = subprocess.run(['bash', '-c', '. "$1"; valid_name "$2"', '_',
                                     str(ROOT / 'lib/common.sh'), name])
            self.assertEqual(result.returncode == 0, valid, name)

    def test_unsupported_t3_help_is_not_mistaken_for_browser_setup(self):
        for help_text, supported in [('USAGE: t3 <subcommand>\nserve\nservice\n', False),
                                      ('USAGE: t3 browser\nSUBCOMMANDS\n  setup Install libraries\n', True)]:
            with tempfile.TemporaryDirectory() as tmp:
                tool = Path(tmp) / 't3'
                tool.write_text("#!/bin/sh\ncat <<'HELP'\n" + help_text + 'HELP\n')
                tool.chmod(0o755)
                result = subprocess.run(['bash', '-c', '. "$1"; browser_setup_supported',
                                         '_', str(ROOT / 'lib/common.sh')],
                                        env={**os.environ, 'PATH': tmp + os.pathsep + os.environ['PATH']})
                self.assertEqual(result.returncode == 0, supported)

    def test_documented_github_host_key_fingerprint(self):
        result = subprocess.run(['ssh-keygen', '-lf', str(ROOT / 'github_known_hosts')],
                                text=True, capture_output=True, check=True)
        self.assertIn('SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU', result.stdout)


if __name__ == '__main__':
    unittest.main()
