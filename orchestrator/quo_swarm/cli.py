"""quo-swarm: official-CLI orchestrator for the quo organizational swarm (no proxy)."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time

from . import config, guard, macos, store

CODEX_PROFILE_CONFIG = (
    "# quo-swarm account profile. Official Codex CLI, direct ChatGPT login.\n"
    "# Direct provider connection only: do not add custom providers or endpoints here.\n"
    'cli_auth_credentials_store = "file"\n'
)


def cmd_init(args) -> int:
    cfg = config.init()
    for account in cfg["accounts"]:
        if account["provider"] == "codex":
            path = config.account_home(account["id"]) / "config.toml"
            if not path.exists():
                path.write_text(CODEX_PROFILE_CONFIG, "utf-8")
                path.chmod(0o600)
    print(f"initialized {config.ROOT}")
    return 0


def cmd_login(args) -> int:
    cfg = config.init()
    account = config.account(cfg, args.account)
    if account["email"].lower() in [e.lower() for e in cfg["excluded_emails"].get(account["provider"], [])]:
        print(f"{account['email']} is excluded for {account['provider']}", file=sys.stderr)
        return 2
    cmd_init(args)
    guard.check_profile_files(account)
    env = guard.clean_env(guard.profile_env(account))
    print(f"Logging {account['id']} into {account['email']} with the official "
          f"{'Claude Code' if account['provider'] == 'claude' else 'Codex'} CLI.")
    if account["provider"] == "claude":
        print("Open the printed URL in a browser signed into that Claude account, choose your Claude "
              "subscription, and paste the code here if asked.\n")
        command = [str(guard.claude_binary()), "auth", "login", "--claudeai", "--email", account["email"]]
        logout = [str(guard.claude_binary()), "auth", "logout"]
    else:
        print("Open the printed URL, sign into that ChatGPT account, and enter the one-time code. "
              "Device login must be allowed in ChatGPT security settings.\n")
        command = [str(guard.codex_binary()), "login", "--device-auth"]
        logout = [str(guard.codex_binary()), "logout"]
    cwd = None
    if sys.platform == "darwin":  # log in under the account's sandbox so credentials stay in its folder
        cwd = config.account_home(account["id"])  # the sandbox hides the host home, so start inside the profile
        env["HOME"] = str(cwd)
        sb = macos.account_profile(cwd, macos.cli_root(account["provider"], guard.binary(account["provider"])))
        command, logout = macos.wrap(command, sb), macos.wrap(logout, sb)
    code = subprocess.run(command, env=env, cwd=cwd).returncode
    if code:
        return code
    try:
        info = guard.verify(cfg, account)
    except guard.GuardError as exc:
        subprocess.run(logout, env=env, cwd=cwd, capture_output=True)
        print(f"\nREJECTED and logged out again: {exc}", file=sys.stderr)
        return 1
    if not info.get("ok"):
        print(f"\nLogin not completed: {info.get('problem')}", file=sys.stderr)
        return 1
    with store.connect() as conn:
        store.set_account_state(conn, account["id"], "ready", None, identity=info)
        store.event(conn, "info", f"logged in as {info.get('email')} ({info.get('plan') or 'plan unknown'})",
                    account["id"])
    print(f"\nOK: {account['id']} verified as {info.get('email')} (plan: {info.get('plan') or 'unknown'}).")
    return 0


def cmd_check(args) -> int:
    cfg = config.init()
    worst = 0
    for account in cfg["accounts"]:
        if args.account and account["id"] != args.account:
            continue
        try:
            info = guard.verify(cfg, account)
            state = "ready" if info.get("ok") else "logged_out"
            note = f"{info.get('email')} · {info.get('plan') or '?'} · {info.get('auth_method')}" if info.get("ok") \
                else info.get("problem")
            worst = max(worst, 0 if info.get("ok") else 1)
        except guard.GuardError as exc:
            info, state, note, worst = {"ok": False}, "guard_failed", str(exc), 2
        with store.connect() as conn:
            row = conn.execute("select state from account_state where account=?", (account["id"],)).fetchone()
            keep = row and row["state"] in ("limited", "blocked") and state == "ready"
            if not keep:
                store.set_account_state(conn, account["id"], state, None if state == "ready" else note,
                                        identity=info)
        print(f"{account['id']:<22} {state:<13} {note}")
    return worst


def cmd_status(args) -> int:
    from .console import snapshot
    data = snapshot()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"launch: {'ENABLED' if data['launch_enabled'] else 'disabled'}   concurrency: {data['pool']['concurrency']}"
          f"   proxy: {'none (ok)' if data['proxy']['ok'] else 'DETECTED'}")
    for a in data["accounts"]:
        windows = ", ".join(f"{u['window']} {u['used_percent']:.0f}%" for u in a["usage"]
                            if u["used_percent"] is not None) or "no usage observed"
        print(f"  {a['id']:<22} {a['state']:<12} {a['plan'] or '':<8} {windows}")
    for pool in data["pools"]:
        print(f"pool {pool['name']:<12} {'ENABLED' if pool['enabled'] else 'paused':<8} target {pool['concurrency']:<3} "
              f"active {pool['active']:<3} waiting {pool['waiting']:<3} completed {pool['completed']:<4} -> {pool['server']}")
    print("runs:", json.dumps(data["run_counts"]))
    return 0


def _set_launch(enabled: bool, pool: str | None = None) -> list[str]:
    """Enable/disable one pool, or every pool when pool is None. Returns affected pool names."""
    cfg = config.load()
    names = [pool] if pool else ["quo", *(cfg.get("pools") or {})]
    for name in names:
        if name == "quo":
            cfg["launch_enabled"] = enabled
        elif name in (cfg.get("pools") or {}):
            cfg["pools"][name]["enabled"] = enabled
        else:
            raise SystemExit(f"unknown pool {name!r}")
    config.save(cfg)
    return names


def cmd_pause(args) -> int:
    names = _set_launch(False, args.pool)
    print(f"paused {', '.join(names)}: no new or resumed runs; active runs finish their current turn")
    return 0


def cmd_resume(args) -> int:
    names = _set_launch(True, args.pool)
    print(f"enabled {', '.join(names)}")
    return 0


def cmd_stop_runs(args) -> int:
    names = _set_launch(False, args.pool)
    with store.connect() as conn:
        marks = ",".join("?" * len(names))
        rows = conn.execute(f"select id from runs where state in ('starting','running') and pool in ({marks})",
                            names).fetchall()
        for row in rows:
            conn.execute("update runs set state='interrupting', reason='operator stop', updated_at=? where id=?",
                         (time.time(), row["id"]))
    from .scheduler import Orchestrator
    for row in rows:
        Orchestrator.interrupt(row["id"])
    print(f"paused {', '.join(names)}; interrupted {len(rows)} run(s); their sessions are kept for resumption")
    return 0


def cmd_note(args) -> int:
    """Leave a message for one run; it is added to the prompt when the session next resumes."""
    with store.connect() as conn:
        rows = conn.execute("select id, state from runs where id like ?", (args.run + "%",)).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"{args.run!r} matches {len(rows)} runs")
    run_id, state = rows[0]["id"], rows[0]["state"]
    note = config.RUNS / run_id / "operator_note.md"
    with note.open("a", encoding="utf-8") as f:
        f.write(args.text.strip() + "\n")
    if args.now and state == "running":
        from .scheduler import Orchestrator
        with store.connect() as conn:
            conn.execute("update runs set state='interrupting', reason='operator note', updated_at=? where id=?",
                         (time.time(), run_id))
            store.event(conn, "info", "interrupted to deliver an operator note", None, run_id)
        Orchestrator.interrupt(run_id)
        print(f"{run_id[:8]}: note saved; run interrupted, it resumes with the note")
    else:
        print(f"{run_id[:8]}: note saved; delivered at the next resume (state {state})")
    return 0


def cmd_clear(args) -> int:
    with store.connect() as conn:
        store.set_account_state(conn, args.account, "unknown", "cleared by operator")
        conn.execute("update account_state set credits_baseline=credits_latest where account=?", (args.account,))
        store.event(conn, "warn", "block cleared by operator", args.account)
    print(f"{args.account}: cleared; it will be re-verified before use")
    return 0


def cmd_serve(args) -> int:
    from .console import serve as console_server
    from .scheduler import Orchestrator
    config.init()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    server = console_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if sys.platform == "darwin":
        from .datastore import serve as data_server
        data = data_server()
        threading.Thread(target=data.serve_forever, daemon=True).start()
    orchestrator = Orchestrator()
    worker = threading.Thread(target=orchestrator.serve, args=(stop,), daemon=True)
    worker.start()
    stop.wait()
    worker.join(timeout=30)  # lets the orchestrator interrupt live runs (macOS)
    server.shutdown()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quo-swarm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the state directories and account profiles")
    p = sub.add_parser("login", help="log one account in with its official CLI (interactive)")
    p.add_argument("account")
    p = sub.add_parser("check", help="verify identities and no-proxy guards (no model calls)")
    p.add_argument("account", nargs="?")
    p = sub.add_parser("status", help="show accounts, usage, and runs")
    p.add_argument("--json", action="store_true")
    sub.add_parser("serve", help="run the orchestrator and console (systemd service or launchd agent)")
    p = sub.add_parser("pause", help="disable a pool (default: all); active turns finish")
    p.add_argument("--pool")
    p = sub.add_parser("resume", help="enable a pool (default: all)")
    p.add_argument("--pool")
    p = sub.add_parser("stop-runs", help="disable a pool (default: all) and interrupt its runs (sessions kept)")
    p.add_argument("--pool")
    p = sub.add_parser("note", help="message one run; delivered when its session next resumes")
    p.add_argument("run", help="run id or unique prefix")
    p.add_argument("text")
    p.add_argument("--now", action="store_true", help="interrupt the run now (kills its background jobs)")
    p = sub.add_parser("clear", help="clear a manual block (extra usage / credit spend) after review")
    p.add_argument("account")
    args = parser.parse_args(argv)
    return {"init": cmd_init, "login": cmd_login, "check": cmd_check, "status": cmd_status,
            "serve": cmd_serve, "pause": cmd_pause, "resume": cmd_resume, "stop-runs": cmd_stop_runs,
            "clear": cmd_clear, "note": cmd_note}[args.command](args)
