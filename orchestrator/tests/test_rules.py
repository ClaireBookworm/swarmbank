"""Safety rules: no proxy settings, no switching, caps, extra usage, launch switch."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = tempfile.mkdtemp(prefix="quo-swarm-test-")
os.environ["QUO_SWARM_ROOT"] = ROOT

from quo_swarm import config, guard, scheduler, store, usage  # noqa: E402

_cfg = config.load()
_cfg["accounts"] = [
    {"id": "claude-a", "provider": "claude", "email": "a@example.com", "enabled": True},
    {"id": "claude-b", "provider": "claude", "email": "b@example.com", "enabled": True},
    {"id": "codex-a", "provider": "codex", "email": "a@example.com", "enabled": True},
    {"id": "codex-b", "provider": "codex", "email": "b@example.com", "enabled": True},
]
_cfg["excluded_emails"] = {"claude": ["banned@example.com"], "codex": []}
_cfg["instructions_file"] = os.path.join(ROOT, "instructions.md")
Path(ROOT).mkdir(parents=True, exist_ok=True)
Path(_cfg["instructions_file"]).write_text("Test instructions.\n")
_cfg["sci"] = {"server": "https://sci.example.org/", "key": os.path.join(ROOT, "test-key.pem")}
Path(_cfg["sci"]["key"]).write_text("not a real key")
config.save(_cfg)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.init()

    def test_clean_env_never_inherits_provider_variables(self):
        os.environ["ANTHROPIC_BASE_URL"] = "http://192.0.2.1:8317"
        os.environ["OPENAI_API_KEY"] = "x"
        try:
            env = guard.clean_env()
            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("OPENAI_API_KEY", env)
        finally:
            del os.environ["ANTHROPIC_BASE_URL"], os.environ["OPENAI_API_KEY"]

    def test_clean_env_refuses_explicit_proxy_variables(self):
        for key in ("ANTHROPIC_BASE_URL", "HTTPS_PROXY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            with self.assertRaises(guard.GuardError):
                guard.clean_env({key: "x"})

    def test_codex_profile_with_custom_provider_is_rejected(self):
        account = config.account(self.cfg, "codex-b")
        path = config.account_home(account["id"]) / "config.toml"
        path.write_text('model_provider = "sci"\n[model_providers.sci]\nbase_url = "http://192.0.2.1:8317/v1"\n')
        try:
            with self.assertRaises(guard.GuardError):
                guard.check_profile_files(account)
        finally:
            path.unlink()

    def test_claude_profile_with_base_url_is_rejected(self):
        account = config.account(self.cfg, "claude-b")
        path = config.account_home(account["id"]) / "settings.json"
        path.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8317"}}))
        try:
            with self.assertRaises(guard.GuardError):
                guard.check_profile_files(account)
        finally:
            path.unlink()

    def test_codex_api_key_login_is_rejected(self):
        account = config.account(self.cfg, "codex-a")
        path = config.account_home(account["id"]) / "auth.json"
        path.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"}))
        try:
            with self.assertRaises(guard.GuardError):
                guard.check_profile_files(account)
        finally:
            path.unlink()

    def test_excluded_or_wrong_account_is_rejected(self):
        account = config.account(self.cfg, "claude-a")
        original = guard.identity
        try:
            guard.identity = lambda a: {"logged_in": True, "email": "banned@example.com"}
            with self.assertRaises(guard.GuardError):
                guard.verify(self.cfg, account)
            guard.identity = lambda a: {"logged_in": True, "email": "someone@else.com"}
            with self.assertRaises(guard.GuardError):
                guard.verify(self.cfg, account)
            guard.identity = lambda a: {"logged_in": True, "email": "a@example.com"}
            self.assertTrue(guard.verify(self.cfg, account)["ok"])
        finally:
            guard.identity = original


class UsageAndAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.init()
        self.cfg["disk_reserve_gib"] = 0  # test state lives on a small tmpfs
        self.cfg["disk_critical_gib"] = 0
        config.save(self.cfg)
        with store.connect() as conn:
            conn.execute("delete from usage")
            conn.execute("delete from account_state")
            conn.execute("delete from runs")
            for a in self.cfg["accounts"]:
                store.set_account_state(conn, a["id"], "ready", identity={"ok": True})
        self.orch = scheduler.Orchestrator()

    def avail(self, account_id):
        with store.connect() as conn:
            return self.orch.availability(conn, self.cfg, config.account(self.cfg, account_id))

    def test_claude_rate_limit_events_apply_caps(self):
        run = {"account": "claude-b"}
        with store.connect() as conn:
            usage.claude_line(conn, run, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.5,
                "resetsAt": time.time() + 3600}})
        self.assertTrue(self.avail("claude-b")[0])
        with store.connect() as conn:
            usage.claude_line(conn, run, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.85,
                "resetsAt": time.time() + 3600}})
        self.assertTrue(self.avail("claude-b")[0])
        with store.connect() as conn:
            usage.claude_line(conn, run, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "five_hour", "utilization": 1.0,
                "resetsAt": time.time() + 3600}})
        ok, reason, until = self.avail("claude-b")
        self.assertFalse(ok)
        self.assertIn("five_hour", reason)
        self.assertTrue(self.avail("claude-a")[0])  # other accounts unaffected

    def test_unified_windows_are_recorded(self):
        with store.connect() as conn:
            usage.claude_line(conn, {"account": "claude-b"}, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "seven_day", "utilization": 0.1, "resetsAt": time.time() + 9999,
                "unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": time.time() + 999},
                                   "seven_day": {"utilization": 0.1, "resetsAt": time.time() + 9999}}}})
        ok, reason, _ = self.avail("claude-b")
        self.assertFalse(ok)
        self.assertIn("five_hour", reason)

    def test_warning_without_percentage_stops_new_work(self):
        with store.connect() as conn:
            usage.claude_line(conn, {"account": "claude-b"}, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed_warning", "rateLimitType": "seven_day", "resetsAt": time.time() + 9999}})
        self.assertFalse(self.avail("claude-b")[0])

    def test_codex_default_cap_allows_below_100_and_stops_at_100(self):
        for window in ("primary", "secondary"):
            with self.subTest(window=window):
                with store.connect() as conn:
                    conn.execute("delete from usage")
                    store.record_usage(conn, "codex-b", window, 99.9, time.time() + 999, "observed")
                self.assertTrue(self.avail("codex-b")[0])
                with store.connect() as conn:
                    store.record_usage(conn, "codex-b", window, 100, time.time() + 999, "observed")
                self.assertFalse(self.avail("codex-b")[0])

    def test_provider_rejection_stops_below_default_cap(self):
        with store.connect() as conn:
            usage.claude_line(conn, {"account": "claude-b"}, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "rejected", "rateLimitType": "five_hour", "utilization": 0.5,
                "resetsAt": time.time() + 3600}})
        self.assertFalse(self.avail("claude-b")[0])

    def test_warning_below_cap_uses_numeric_cap(self):
        with store.connect() as conn:
            usage.claude_line(conn, {"account": "claude-b"}, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed_warning", "rateLimitType": "seven_day", "utilization": 0.75,
                "resetsAt": time.time() + 9999}})
        self.assertTrue(self.avail("claude-b")[0])

    def test_per_account_cap_overrides_provider_cap(self):
        cfg = config.load()
        for a in cfg["accounts"]:
            if a["id"] == "codex-a":
                a["cap_percent"] = 95
        self.cfg = cfg
        with store.connect() as conn:
            store.record_usage(conn, "codex-a", "primary", 90, time.time() + 999, "observed")
        self.assertTrue(self.avail("codex-a")[0])
        with store.connect() as conn:
            store.record_usage(conn, "codex-a", "primary", 95, time.time() + 999, "observed")
        self.assertFalse(self.avail("codex-a")[0])

    def test_provider_priority_starts_codex_first(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append(account["provider"])
        self.orch.refresh_identity = lambda *a, **k: None
        self.orch.last_new_start_by_pool = {}
        cfg = config.load()
        cfg.update(launch_enabled=True, task_prompt="t")
        cfg["pool"].update(concurrency=24, per_account_concurrency=6, provider_priority=["codex", "claude"])
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        config.save(cfg)
        self.orch.tick()
        self.assertEqual(started, ["codex"])

    def test_transient_errors_are_retryable(self):
        for msg in ("Failed to refresh OAuth token: another Claude Code process is refreshing it",
                    '{"message": "exceeded retry limit, last status: 429 Too Many Requests"}'):
            self.assertTrue(scheduler.TRANSIENT.search(msg))
        self.assertFalse(scheduler.TRANSIENT.search("model produced invalid output"))

    def test_resumes_are_spaced_per_account(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append(run_id)
        self.orch.refresh_identity = lambda *a, **k: None
        cfg = config.load()
        cfg.update(launch_enabled=True, task_prompt="t")
        cfg["pool"].update(concurrency=24, per_account_concurrency=6, new_run_interval_seconds=10**9)
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        config.save(cfg)
        self.orch.last_new_start_by_pool = {"quo": time.time()}
        now = time.time()
        with store.connect() as conn:
            for i in range(3):
                conn.execute("insert into runs(id, account, provider, session_id, state, created_at, updated_at) "
                             "values (?,?,?,?,?,?,?)", (f"w{i}", "claude-b", "claude", f"s{i}", "waiting_resume", now, now))
        self.orch.tick()
        self.assertEqual(started, ["w0"])

    def test_throttled_account_cools_down_without_switching(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append((run_id, account["id"]))
        self.orch.refresh_identity = lambda *a, **k: None
        cfg = config.load()
        cfg.update(launch_enabled=True, task_prompt="t")
        cfg["pool"].update(concurrency=24, per_account_concurrency=6, new_run_interval_seconds=10**9)
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        config.save(cfg)
        self.orch.last_new_start_by_pool = {"quo": time.time()}
        self.orch.cooldown["codex-a"] = (time.time() + 600, 1)
        now = time.time()
        with store.connect() as conn:
            conn.execute("insert into runs(id, account, provider, session_id, state, created_at, updated_at) "
                         "values ('t1','codex-a','codex','s','waiting_resume',?,?)", (now, now))
        self.orch.tick()
        self.assertNotIn("t1", [r for r, _ in started])
        self.assertTrue(scheduler.THROTTLED.search("exceeded retry limit, last status: 429 Too Many Requests"))

    def test_second_pool_uses_its_own_prompt_server_and_keys(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append(run_id)
        self.orch.refresh_identity = lambda *a, **k: None
        self.orch.last_new_start_by_pool = {}
        cfg = config.load()
        cfg.update(launch_enabled=False, task_prompt="t")
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        keydir = Path(ROOT) / "keys"
        keydir.mkdir(exist_ok=True)
        for p in ("claude", "codex"):
            (keydir / f"{p}.pem").write_text("x")
        cfg["pools"] = {"science": {"enabled": True, "concurrency": 2, "prompt": "Research objective: X",
                                    "sci": {"server": "https://sci.example.org/",
                                            "keys": {p: str(keydir / f"{p}.pem") for p in ("claude", "codex")}}}}
        config.save(cfg)
        self.orch.tick()
        self.assertEqual(len(started), 1)
        with store.connect() as conn:
            row = conn.execute("select * from runs where id=?", (started[0],)).fetchone()
        self.assertEqual(row["pool"], "science")
        run_dir = config.RUNS / started[0]
        self.assertIn("Research objective: X", (run_dir / "prompt.txt").read_text())
        meta = json.loads((run_dir / "meta.json").read_text())
        self.assertEqual(meta["sci_server"], "https://sci.example.org/")
        self.assertTrue(meta["signing_key"].endswith(f"{row['provider']}.pem"))
        self.assertEqual(json.loads((run_dir / "home/.sci/config.json").read_text())["server"],
                         "https://sci.example.org/")

    def test_paused_pool_does_not_resume(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append(run_id)
        self.orch.refresh_identity = lambda *a, **k: None
        cfg = config.load()
        cfg.update(launch_enabled=False, task_prompt="t", pools={})
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        config.save(cfg)
        now = time.time()
        with store.connect() as conn:
            conn.execute("insert into runs(id, account, provider, session_id, state, created_at, updated_at, pool) "
                         "values ('q1','codex-b','codex','s','waiting_resume',?,?,'quo')", (now, now))
        self.orch.tick()
        self.assertEqual(started, [])

    def test_dead_worker_runs_are_requeued(self):
        now = time.time()
        with store.connect() as conn:
            conn.execute("insert into runs(id, account, provider, session_id, state, created_at, updated_at) "
                         "values ('dead1','codex-b','codex','s','running',?,?)", (now, now))
            self.orch.reconcile(conn)
            state = conn.execute("select state from runs where id='dead1'").fetchone()["state"]
        self.assertEqual(state, "waiting_resume")

    def test_window_reset_restores_availability(self):
        with store.connect() as conn:
            store.record_usage(conn, "codex-b", "primary", 100, time.time() - 1, "observed")
        self.assertTrue(self.avail("codex-b")[0])

    def test_overage_flag_is_reported(self):
        with store.connect() as conn:
            out = usage.claude_line(conn, {"account": "claude-b"}, {"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "five_hour", "isUsingOverage": True}})
        self.assertTrue(out["_overage"])

    def test_codex_rollout_limits_and_credit_spend_block(self):
        home = config.account_home("codex-b")
        sid = "019a0000-0000-7000-8000-000000000001"
        d = home / "sessions/2026/10/24"
        d.mkdir(parents=True, exist_ok=True)
        def write(balance, pct):
            (d / f"rollout-2026-10-24T00-00-00-{sid}.jsonl").write_text(json.dumps({
                "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                    "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": time.time() + 999},
                    "credits": {"balance": balance}}}}) + "\n")
        write("100.0", 10)
        with store.connect() as conn:
            usage.codex_rollout_limits(conn, "codex-b", home, sid)
        self.assertTrue(self.avail("codex-b")[0])
        write("99.5", 12)
        with store.connect() as conn:
            usage.codex_rollout_limits(conn, "codex-b", home, sid)
        ok, reason, _ = self.avail("codex-b")
        self.assertFalse(ok)
        self.assertIn("credits", reason)

    def test_unverified_or_blocked_accounts_are_unavailable(self):
        with store.connect() as conn:
            store.set_account_state(conn, "codex-a", "blocked", "extra usage detected")
            store.set_account_state(conn, "claude-a", "logged_out", "not logged in")
        self.assertFalse(self.avail("codex-a")[0])
        self.assertFalse(self.avail("claude-a")[0])

    def test_launch_disabled_starts_nothing(self):
        started = []
        self.orch.start = lambda *a, **k: started.append(a)
        self.orch.refresh_identity = lambda *a, **k: None
        cfg = config.load()
        cfg.update(launch_enabled=False, task_prompt="t")
        cfg["pool"]["concurrency"] = 4
        config.save(cfg)
        self.orch.tick()
        self.assertEqual(started, [])

    def test_waiting_sessions_resume_only_on_their_own_account(self):
        started = []
        self.orch.start = lambda run_id, account, cfg, resume: started.append((run_id, account["id"]))
        self.orch.last_new_start_by_pool = {}
        self.orch.refresh_identity = lambda *a, **k: None
        cfg = config.load()
        cfg.update(launch_enabled=True, task_prompt="t")
        cfg["pool"]["concurrency"] = 1
        for p in ("claude", "codex"):
            cfg["models"][p]["model"] = "m"
        config.save(cfg)
        now = time.time()
        with store.connect() as conn:
            conn.execute("insert into runs(id, account, provider, session_id, state, created_at, updated_at) "
                         "values ('r1','claude-b','claude','s1','waiting_reset',?,?)", (now, now))
            store.record_usage(conn, "claude-b", "five_hour", 100, now + 3600, "allowed")
        self.orch.tick()
        self.assertNotIn("r1", [s[0] for s in started])  # its account is capped: it waits
        self.assertTrue(all(acc != "claude-b" for _, acc in started))


if __name__ == "__main__":
    unittest.main()
