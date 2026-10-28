"""Launch one turn of an official CLI inside a bubblewrap sandbox.

The sandbox sees only: system directories (read-only), the pinned CLI binary,
the sci CLI, this run's private home/work/tmp, the account's own CLI profile,
and the organizational signing key (read-only). The host home directory, other
accounts, the platform's secrets, and all other keys are not mounted. The
environment is built from scratch, so no proxy, base URL, or API key can leak in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import config, guard, macos, store, usage

DARWIN = sys.platform == "darwin"

SANDBOX_HOME = "/home/agent"
CODEX_VENDOR = "/opt/codex-vendor"
CODEX_IN_SANDBOX = f"{CODEX_VENDOR}/bin/codex"
SCI_VENV = config.ROOT / "tools/sci-venv"


def new_run(conn, account: dict, cfg: dict, pool: dict | None = None) -> str:
    pool = pool or config.pools(cfg)["quo"]
    run_id = str(uuid.uuid4())
    run_dir = config.RUNS / run_id
    for sub in ("home/.sci", "work", "tmp"):
        (run_dir / sub).mkdir(parents=True, mode=0o700)
    session_id = str(uuid.uuid4()) if account["provider"] == "claude" else None
    (run_dir / "prompt.txt").write_text(config.pool_prompt(pool), "utf-8")
    key_path = str(config.pool_key(pool, account["provider"]).resolve()) if DARWIN else f"{SANDBOX_HOME}/.sci/key.pem"
    (run_dir / "home/.sci/config.json").write_text(json.dumps(
        {"server": pool["sci"]["server"], "key": key_path}, indent=2) + "\n", "utf-8")
    model = pool["models"][account["provider"]]
    (run_dir / "meta.json").write_text(json.dumps({
        "run": run_id, "pool": pool["name"], "sci_server": pool["sci"]["server"],
        "signing_key": str(config.pool_key(pool, account["provider"])),
        "account": account["id"], "provider": account["provider"], "email": account["email"],
        "model": model, "created_at": time.time(), "cli": str(guard.binary(account["provider"])),
    }, indent=2) + "\n", "utf-8")
    now = time.time()
    conn.execute("""insert into runs(id, account, provider, session_id, state, created_at, updated_at, model, pool)
                    values (?,?,?,?,?,?,?,?,?)""",
                 (run_id, account["id"], account["provider"], session_id, "queued", now, now, model.get("model"),
                  pool["name"]))
    store.event(conn, "info", f"run created ({pool['name']})", account["id"], run_id)
    return run_id


def _cli_args(provider: str, pool: dict, session_id: str | None, resume: bool,
              claude: str = "/opt/cli/claude", codex: str = CODEX_IN_SANDBOX, work: str = "/work") -> list[str]:
    model = pool["models"][provider]
    if provider == "claude":
        args = [claude, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "bypassPermissions", "--allow-dangerously-skip-permissions"]
        args += ["--resume", session_id] if resume else ["--session-id", session_id]
        if model.get("model"):
            args += ["--model", model["model"]]
        if model.get("effort"):
            args += ["--effort", model["effort"]]
        return args
    common = ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
    if model.get("model"):
        common += ["-m", model["model"]]
    if model.get("effort"):
        common += ["-c", f'model_reasoning_effort="{model["effort"]}"']
    if resume:
        return [codex, "exec", "resume", *common, session_id, "-"]
    return [codex, "exec", *common, "-C", work, "-"]


def darwin_launch(account: dict, cfg: dict, run_id: str, session_id: str | None, resume: bool,
                  pool: dict) -> tuple[list[str], dict, Path]:
    """macOS: (command, environment, working directory) for one sandboxed turn."""
    provider = account["provider"]
    run_dir = config.RUNS / run_id
    cli = guard.binary(provider)
    key = config.pool_key(pool, provider).resolve()
    home = run_dir / "home"
    model = pool["models"][provider].get("model") or "default model"
    env = guard.clean_env({
        "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME": str(config.account_home(account["id"])),
        "QUO_AGENT": f"{'Claude Code' if provider == 'claude' else 'Codex'} agent ({model})",
        "SCI_HOME": str(home / ".sci"),
        "PATH": f"{macos.SCI_VENV}/bin:{macos.TOOLS}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(run_dir / "tmp"),
        "CLAUDE_CODE_TMPDIR": str(run_dir / "tmp"),  # otherwise /tmp/claude-<uid>, which the sandbox hides
        "UV_CACHE_DIR": str(run_dir / "tmp/uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(macos.TOOLS / "python"),
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
        "QUO_RUN_ID": run_id,
    }, home=str(home))
    shared = [config.expand(p) for p in pool.get("shared_readonly") or []]
    sb = macos.write_profile(run_dir, config.account_home(account["id"]), macos.cli_root(provider, cli), key, shared)
    cmd = macos.wrap(_cli_args(provider, pool, session_id, resume, claude=str(cli), codex=str(cli),
                               work=str(run_dir / "work")), sb)
    return cmd, env, run_dir / "work"


def sandbox_command(account: dict, cfg: dict, run_id: str, session_id: str | None, resume: bool,
                    pool: dict | None = None) -> list[str]:
    pool = pool or config.pools(cfg)["quo"]
    run_dir = config.RUNS / run_id
    provider = account["provider"]
    cli = guard.binary(provider)
    key = config.pool_key(pool, provider)
    profile_mount = f"{SANDBOX_HOME}/.claude-profile" if provider == "claude" else f"{SANDBOX_HOME}/.codex"
    env = guard.clean_env({
        "CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME": profile_mount,
        "SCI_HOME": f"{SANDBOX_HOME}/.sci",
        "PATH": f"/opt/sci/bin:/opt/cli:{CODEX_VENDOR}/bin:{CODEX_VENDOR}/codex-path:/usr/local/bin:/usr/bin:/bin",
        "TMPDIR": "/tmp",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
        "QUO_RUN_ID": run_id,
        "QUO_AGENT": f"{'Claude Code' if provider == 'claude' else 'Codex'} agent "
                     f"({pool['models'][provider].get('model') or 'default model'})",
    }, home=SANDBOX_HOME)
    res = cfg["resources"]
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
           f"--unit=quo-run-{run_id}", "--slice=quo-agents.slice",
           f"-pCPUQuota={int(float(res['cpu_per_run']) * 100)}%",
           f"-pMemoryHigh={res['memory_high_per_run']}", f"-pMemoryMax={res['memory_max_per_run']}",
           "--", "bwrap", "--die-with-parent", "--new-session",
           "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
           "--share-net", "--clearenv",
           "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
           "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
           "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
           "--ro-bind-try", "/run/systemd/resolve", "/run/systemd/resolve",
           "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/var/tmp",
           "--bind", str(run_dir / "tmp"), "/tmp",
           "--bind", str(run_dir / "home"), SANDBOX_HOME,
           "--bind", str(run_dir / "work"), "/work",
           "--bind", str(config.account_home(account["id"])), profile_mount,
           "--ro-bind", str(key), f"{SANDBOX_HOME}/.sci/key.pem",
           "--ro-bind", str(SCI_VENV), "/opt/sci"]
    if provider == "claude":
        cmd += ["--ro-bind", str(cli), "/opt/cli/claude"]
    else:
        cmd += ["--ro-bind", str(cli.parent.parent), CODEX_VENDOR]
    for name, value in env.items():
        cmd += ["--setenv", name, value]
    cmd += ["--chdir", "/work", "--"] + _cli_args(provider, pool, session_id, resume)
    return cmd


RESUME_PROMPT = ("Your session was paused because the account reached its configured usage cap "
                 "or the orchestrator restarted. Continue the same task from where you stopped. "
                 "Check the record for new updates from the operator before continuing.\n")


RESUME_PROMPT_SCIENCE = ("Your session was paused because the account reached its usage limit or the "
                         "orchestrator restarted. Continue the same investigation from where you stopped.\n")


def run_turn(run_id: str, cfg: dict, account: dict, resume: bool, on_pid) -> dict:
    """Run one CLI turn to completion. Returns a summary; persists events and usage."""
    guard.verify(cfg, account)  # identity + no-proxy checks immediately before launch
    run_dir = config.RUNS / run_id
    with store.connect() as conn:
        row = dict(conn.execute("select * from runs where id=?", (run_id,)).fetchone())
    pool = config.pools(cfg).get(row.get("pool") or "quo")
    if pool is None:
        raise RuntimeError(f"pool {row.get('pool')!r} is no longer configured")
    resume_prompt = RESUME_PROMPT if pool["name"] == "quo" else RESUME_PROMPT_SCIENCE
    prompt = resume_prompt if resume else (run_dir / "prompt.txt").read_text("utf-8")
    note = run_dir / "operator_note.md"
    if resume and note.exists():  # delivered once, on the next resume of this session
        prompt += "\nMessage from the swarm operator:\n" + note.read_text("utf-8").strip() + "\n"
        note.rename(run_dir / f"operator_note.delivered-{int(time.time())}.md")
    if DARWIN:
        cmd, outer, cwd = darwin_launch(account, cfg, run_id, row["session_id"], resume, pool)
    else:
        cmd, cwd = sandbox_command(account, cfg, run_id, row["session_id"], resume, pool), None
    summary = {"limited": False, "limited_until": None, "overage": False, "error": None, "result": None}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as events, \
         (run_dir / "stderr.log").open("a", encoding="utf-8") as errors:
        errors.write(f"--- turn start {time.strftime('%Y-%m-%dT%H:%M:%S')} resume={resume}\n")
        errors.flush()
        if not DARWIN:
            outer = guard.clean_env({k: os.environ[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
                                     if k in os.environ})  # only for systemd-run; bwrap clears it
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                text=True, env=outer, cwd=cwd, start_new_session=True)
        on_pid(proc.pid)
        proc.stdin.write(prompt)
        proc.stdin.close()
        parse = usage.claude_line if account["provider"] == "claude" else usage.codex_line
        for line in proc.stdout:
            events.write(line)
            events.flush()
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            with store.connect() as conn:
                updates = parse(conn, dict(row), obj)
                if updates.get("session_id") and updates["session_id"] != row.get("session_id"):
                    row["session_id"] = updates["session_id"]
                    conn.execute("update runs set session_id=? where id=?", (row["session_id"], run_id))
                if updates.get("model"):
                    conn.execute("update runs set model=? where id=?", (updates["model"], run_id))
                if "_tokens" in updates:
                    t = updates["_tokens"]
                    conn.execute("""update runs set input_tokens=input_tokens+?, cached_input_tokens=cached_input_tokens+?,
                                    cache_write_tokens=cache_write_tokens+?, output_tokens=output_tokens+?,
                                    cost_usd=cost_usd+? where id=?""",
                                 (t["input_tokens"], t["cached_input_tokens"], t["cache_write_tokens"],
                                  t["output_tokens"], t["cost_usd"], run_id))
                if account["provider"] == "codex" and obj.get("type") in ("turn.completed", "turn.failed"):
                    updates.update(usage.codex_rollout_limits(
                        conn, account["id"], config.account_home(account["id"]), row.get("session_id")))
            if updates.get("_overage"):
                summary["overage"] = True
            if "_limited" in updates:
                summary["limited"] = True
                summary["limited_until"] = updates["_limited"]
            if updates.get("_error"):
                summary["error"] = updates["_error"]
            if updates.get("_result"):
                summary["result"] = updates["_result"]
        summary["exit_code"] = proc.wait()
    if account["provider"] == "codex":
        with store.connect() as conn:
            extra = usage.codex_rollout_limits(conn, account["id"], config.account_home(account["id"]),
                                               row.get("session_id"))
            if "_limited" in extra:
                summary["limited"] = True
    return summary
