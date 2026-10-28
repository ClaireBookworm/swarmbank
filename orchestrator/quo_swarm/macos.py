"""macOS backend: sandbox-exec (Seatbelt) instead of systemd-run + bubblewrap.

Seatbelt cannot remap paths, so the agent sees real host paths. The profile
denies reads and writes in the host home directory and writes everywhere else,
then allows only: this run's private home/work/tmp, the account's own CLI
profile, the shared agent tools (Python, sci, uv; read-only), the pinned CLI
binary, the pool's signing key (read-only), and optional shared read-only data
directories. The environment is built from scratch as on Linux.

There are no per-run CPU or memory cgroups on macOS. Size the pool's
concurrency for the machine instead; the scheduler also stops new starts when
free memory is low (see `memory_ok`).
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from . import config

TOOLS = config.ROOT / "tools"
SCI_VENV = TOOLS / "sci-venv"


def _q(path) -> str:
    return '"' + str(Path(path).resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile(run_dir: Path, account_home: Path, cli: Path, key: Path | None, shared_ro=(), shared_rw=()) -> str:
    rw = [run_dir, account_home] + list(shared_rw)
    ro = [TOOLS, cli] + list(shared_ro)
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read-data file-write* (subpath {_q(Path.home())}))",
        '(deny file-read-data file-write* (subpath "/private/tmp") (subpath "/Volumes"))',
        '(deny file-write* (subpath "/"))',
        '(allow file-write* (subpath "/dev") (subpath "/private/var/folders"))',
        # Seatbelt: an allow must name file-read-data to override the deny above (file-read* alone does not).
        "(allow file-read* file-read-data file-write* " + " ".join(f"(subpath {_q(p)})" for p in rw) + ")",
        "(allow file-read* file-read-data " + " ".join(f"(subpath {_q(p)})" for p in ro) + ")",
    ]
    # The shared data store: files readable by path, but its folders cannot be listed.
    data, objects = config.DATA, config.DATA / "objects"
    lines.append(f"(allow file-read* file-read-data (subpath {_q(objects)}))")
    lines.append(f"(deny file-read-data (literal {_q(data)}) (literal {_q(objects)}))")
    if key:
        lines.append(f"(allow file-read* file-read-data (literal {_q(key)}))")
    return "\n".join(lines) + "\n"


def wrap(cmd: list[str], profile_path: Path) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path)] + cmd


def write_profile(run_dir: Path, account_home: Path, cli: Path, key: Path | None, shared_ro=(),
                  shared_rw=()) -> Path:
    path = run_dir / "sandbox.sb"
    path.write_text(profile(run_dir, account_home, cli, key, shared_ro, shared_rw), "utf-8")
    return path


def account_profile(account_home: Path, cli: Path) -> Path:
    """Profile for login/status outside a run: only the account folder is writable."""
    path = config.STATE / f"account-{account_home.name}.sb"
    path.write_text(profile(account_home, account_home, cli, None), "utf-8")
    return path


def cli_root(provider: str, cli: Path) -> Path:
    """What the sandbox must read for a CLI: the Claude binary, or the whole Codex release folder."""
    return cli.parent.parent if provider == "codex" else cli


def interrupt(pid: int | None, sig=signal.SIGINT) -> bool:
    """Signal a run's whole process group (the runner starts each run in a new session)."""
    if not pid:
        return False
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def memory_free_percent() -> int | None:
    try:
        out = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "free percentage" in line:
            try:
                return int(line.rsplit(":", 1)[1].strip().rstrip("%"))
            except ValueError:
                return None
    return None
