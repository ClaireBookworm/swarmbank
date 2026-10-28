"""Paths and configuration for the quo organizational swarm orchestrator."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("QUO_SWARM_ROOT") or Path.home() / ".local/share/quo-swarm")
CONFIG = ROOT / "config.json"
ACCOUNTS = ROOT / "accounts"
RUNS = ROOT / "runs"
STATE = ROOT / "state"
DB = STATE / "swarm.sqlite"
DATA = ROOT / "data"

DEFAULT = {
    # Master switch. Nothing launches while this is false, including at boot.
    "launch_enabled": False,
    "pool": {"concurrency": 0, "per_account_concurrency": 1, "new_run_interval_seconds": 60,
             "account_start_interval_seconds": 45, "provider_priority": ["codex", "claude"]},
    # One official CLI profile per account. Never route through a proxy. Example:
    # {"id": "claude-1", "provider": "claude", "email": "you@example.com", "enabled": true}
    "accounts": [],
    # Never used, even if someone logs a profile into them.
    "excluded_emails": {"claude": [], "codex": []},
    # Usage caps in percent of each provider window. At or above a cap the account
    # starts no new turns; running turns are interrupted and wait for the reset.
    # Sessions never move to another account.
    "caps_percent": {
        "claude": {"five_hour": 80, "seven_day": 80, "seven_day_opus": 80, "seven_day_sonnet": 80},
        "codex": {"primary": 80, "secondary": 80},
    },
    # Models and reasoning effort for each provider.
    "models": {
        "claude": {"model": None, "effort": None},
        "codex": {"model": None, "effort": None},
    },
    # Top-level pool (disabled by default). Most swarms use entries under "pools" instead.
    "sci": {"server": "https://sci.azaho.org/", "key": ""},
    "instructions_file": None,
    # Additional pools (each with its own prompt, server, keys, and concurrency); see pools().
    "pools": {},
    # Do not start new runs when free disk space falls below this many GiB.
    "disk_reserve_gib": 50,
    # Below this, every running agent is interrupted (sessions kept) to protect the host.
    "disk_critical_gib": 25,
    "resources": {"cpu_per_run": 1.0, "memory_high_per_run": "2G", "memory_max_per_run": "3G"},
    # Hosts checked for a listening retired proxy port (shown on the console).
    "proxy_check": {"hosts": ["127.0.0.1"], "port": 8317},
    "console": {
        "host": "127.0.0.1",
        "port": 8318,
        # Tailnet devices allowed to view the console (Tailscale Serve X-Forwarded-For).
        "allow": ["127.0.0.1"],
    },
    # Shared download service (macOS): agents run `fetch URL` and read one read-only copy.
    "data_service": {"host": "127.0.0.1", "port": 8319, "max_file_gib": 60},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    if not CONFIG.exists():
        return copy.deepcopy(DEFAULT)
    return _merge(DEFAULT, json.loads(CONFIG.read_text("utf-8")))


def save(cfg: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".config.", dir=ROOT)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)


def init() -> dict:
    for path in (ROOT, ACCOUNTS, RUNS, STATE):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    cfg = load()
    if not CONFIG.exists():
        save(cfg)
    for account in cfg["accounts"]:
        home = account_home(account["id"])
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(home, 0o700)
    return cfg


def account(cfg: dict, account_id: str) -> dict:
    for item in cfg["accounts"]:
        if item["id"] == account_id:
            return item
    raise SystemExit(f"unknown account {account_id!r}; known: {', '.join(a['id'] for a in cfg['accounts'])}")


def account_home(account_id: str) -> Path:
    return ACCOUNTS / account_id


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


POOL_KEYS = ("concurrency", "per_account_concurrency", "new_run_interval_seconds",
             "account_start_interval_seconds", "provider_priority")


def pools(cfg: dict) -> dict:
    """All pools as uniform dicts. The original top-level settings are the 'quo' pool."""
    quo = {
        "name": "quo", "enabled": bool(cfg.get("launch_enabled")),
        "prompt": None, "task_prompt": cfg.get("task_prompt"), "instructions_file": cfg.get("instructions_file"),
        "sci": {"server": cfg["sci"]["server"],
                "keys": {"claude": cfg["sci"]["key"], "codex": cfg["sci"]["key"]}},
        "models": cfg["models"],
    }
    quo.update({k: cfg["pool"][k] for k in POOL_KEYS if k in cfg["pool"]})
    result = {"quo": quo}
    for name, extra in (cfg.get("pools") or {}).items():
        pool = {k: quo[k] for k in POOL_KEYS if k in quo}
        pool.update({"name": name, "enabled": False, "models": cfg["models"]})
        pool.update(extra)
        pool["name"] = name
        result[name] = pool
    return result


def pool_key(pool: dict, provider: str) -> Path:
    keys = pool["sci"].get("keys") or {}
    return expand(keys.get(provider) or pool["sci"].get("key") or "")


def pool_prompt(pool: dict) -> str:
    if pool.get("prompt"):
        return pool["prompt"].strip() + "\n"
    instructions = expand(pool["instructions_file"]).read_text("utf-8")
    return instructions.rstrip() + "\n\n## Current task\n\n" + pool["task_prompt"].strip() + "\n"


def pool_ready(pool: dict, accounts: list) -> bool:
    if not pool.get("enabled"):
        return False
    if not (pool.get("prompt") or pool.get("task_prompt")):
        return False
    for a in accounts:
        if not a.get("enabled", True):
            continue
        if not pool["models"][a["provider"]].get("model"):
            return False
        keys = pool["sci"].get("keys") or {}
        if not (keys.get(a["provider"]) or pool["sci"].get("key")):
            return False  # no signing key configured for this provider
    return True
