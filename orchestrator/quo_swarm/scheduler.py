"""The orchestrator loop: account health, usage caps, and run admission.

Rules:
- Nothing launches or resumes unless config ``launch_enabled`` is true.
- A session stays on its account forever. When the account is capped,
  limited, unverified, or shows extra usage, its runs are interrupted and wait;
  they are never moved to another account or provider.
- New runs start only on verified accounts below every cap.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import traceback

from . import config, guard, macos, runner, store

IDENTITY_INTERVAL = 300
TICK = 5
RETRY_SECONDS = 120
COOLDOWN_BASE = 120
COOLDOWN_MAX = 1800
THROTTLED = re.compile(r"\b429\b|Too Many Requests", re.IGNORECASE)
# Temporary provider or login-refresh conditions: retry the same session later.
TRANSIENT = re.compile(r"refresh(ing)? (the )?OAuth token|another Claude Code process|429|Too Many Requests|"
                       r"overloaded|\b529\b|\b50[234]\b|ECONNRESET|ETIMEDOUT|timed out|stream disconnected",
                       re.IGNORECASE)


class Orchestrator:
    def __init__(self):
        self.threads: dict[str, threading.Thread] = {}
        self.identity_checked: dict[str, float] = {}
        self.lock = threading.Lock()
        self.last_new_start_by_pool: dict[str, float] = {}
        self.last_account_start: dict[str, float] = {}
        # Provider request throttling (HTTP 429): pause new starts on that account with
        # exponential backoff. Running sessions continue; nothing moves to another account.
        self.cooldown: dict[str, tuple[float, int]] = {}

    # ---- account availability -------------------------------------------------

    def refresh_identity(self, cfg: dict, account: dict, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.identity_checked.get(account["id"], 0) < IDENTITY_INTERVAL:
            return
        self.identity_checked[account["id"]] = now
        with store.connect() as conn:
            try:
                info = guard.verify(cfg, account)
                row = conn.execute("select state from account_state where account=?", (account["id"],)).fetchone()
                if not info.get("ok"):
                    store.set_account_state(conn, account["id"], "logged_out", info.get("problem"), identity=info)
                elif row is None or row["state"] in ("unknown", "logged_out", "guard_failed"):
                    store.set_account_state(conn, account["id"], "ready", None, identity=info)
                else:
                    conn.execute("update account_state set identity=?, checked_at=? where account=?",
                                 (json.dumps(info), now, account["id"]))
            except guard.GuardError as exc:
                store.set_account_state(conn, account["id"], "guard_failed", str(exc), identity={"ok": False})
                store.event(conn, "error", f"guard failed: {exc}", account["id"])

    def availability(self, conn, cfg: dict, account: dict) -> tuple[bool, str | None, float | None]:
        """Return (available, reason, until)."""
        if not account.get("enabled", True):
            return False, "disabled in config", None
        row = conn.execute("select * from account_state where account=?", (account["id"],)).fetchone()
        if row is None or row["state"] in ("unknown", "logged_out", "guard_failed"):
            return False, (row["reason"] if row else None) or "not verified", None
        if row["state"] == "blocked":  # overage or credit spend: manual clear only
            return False, row["reason"], None
        now = time.time()
        if row["state"] == "limited" and (row["until"] is None or row["until"] > now):
            return False, row["reason"], row["until"]
        if row["credits_baseline"] is not None and row["credits_latest"] is not None \
                and row["credits_latest"] < row["credits_baseline"] - 0.001:
            return False, "credits were spent", None
        caps = cfg["caps_percent"][account["provider"]]
        for u in conn.execute("select * from usage where account=?", (account["id"],)):
            if u["resets_at"] and u["resets_at"] <= now:
                continue  # window has reset since we observed it
            # A per-account cap_percent overrides the provider defaults for every window.
            cap = account.get("cap_percent", caps.get(u["window"], min(caps.values())))
            if u["used_percent"] is not None and u["used_percent"] >= cap:
                return False, f"{u['window']} at {u['used_percent']:.0f}% (cap {cap}%)", u["resets_at"]
            # The numeric cap decides. A provider warning stops new work only when no percentage is known;
            # a provider rejection always does.
            if u["status"] == "rejected" or (u["status"] == "allowed_warning" and u["used_percent"] is None):
                return False, f"{u['window']} status {u['status']}", u["resets_at"]
        return True, None, None

    # ---- runs -----------------------------------------------------------------

    @staticmethod
    def interrupt(run_id: str) -> None:
        if runner.DARWIN:
            with store.connect() as conn:
                row = conn.execute("select pid from runs where id=?", (run_id,)).fetchone()
            macos.interrupt(row["pid"] if row else None)
            return
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGINT", f"quo-run-{run_id}.scope"],
                       capture_output=True)

    def interrupt_all(self) -> None:
        """macOS has no systemd KillMode: on shutdown, interrupt every live run (sessions kept)."""
        if not runner.DARWIN:
            return
        with store.connect() as conn:
            rows = conn.execute("select id, pid from runs where pid is not null "
                                "and state in ('starting','running','interrupting')").fetchall()
            # Mark first, so workers record the exit as an interruption (resumable), not a failure.
            for r in rows:
                conn.execute("update runs set state='interrupting', reason='orchestrator stopped', updated_at=? "
                             "where id=?", (time.time(), r["id"]))
            pids = [r["pid"] for r in rows]
        for pid in pids:
            macos.interrupt(pid)

    def _worker(self, run_id: str, account: dict, cfg: dict, resume: bool) -> None:
        def on_pid(pid):
            with store.connect() as conn:
                conn.execute("update runs set pid=?, state='running', started_at=coalesce(started_at, ?), "
                             "updated_at=?, turns=turns+1 where id=?", (pid, time.time(), time.time(), run_id))
        try:
            summary = runner.run_turn(run_id, cfg, account, resume, on_pid)
            with store.connect() as conn:
                now = time.time()
                if summary["overage"]:
                    store.set_account_state(conn, account["id"], "blocked", "extra usage detected; clear manually")
                    store.event(conn, "error", "extra usage detected; account blocked", account["id"], run_id)
                if summary["limited"]:
                    until = summary["limited_until"] or now + 900
                    store.set_account_state(conn, account["id"], "limited", "provider usage limit", until)
                    conn.execute("update runs set state='waiting_reset', resume_at=?, pid=null, updated_at=? "
                                 "where id=?", (until, now, run_id))
                    return
                state = conn.execute("select state from runs where id=?", (run_id,)).fetchone()["state"]
                if state == "interrupting":
                    conn.execute("update runs set state='waiting_reset', pid=null, updated_at=? where id=?",
                                 (now, run_id))
                elif summary["exit_code"] == 0 and not summary["error"]:
                    self.cooldown.pop(account["id"], None)
                    conn.execute("update runs set state='completed', ended_at=?, pid=null, updated_at=? "
                                 "where id=?", (now, now, run_id))
                elif THROTTLED.search(summary["error"] or ""):
                    _, level = self.cooldown.get(account["id"], (0, 0))
                    delay = min(COOLDOWN_MAX, COOLDOWN_BASE * 2 ** level)
                    self.cooldown[account["id"]] = (now + delay, level + 1)
                    conn.execute("update runs set state='waiting_resume', reason=?, resume_at=?, pid=null, "
                                 "updated_at=? where id=?", (("throttled: " + summary["error"])[:500],
                                                             now + delay, now, run_id))
                    store.event(conn, "warn", f"provider throttling (429); account cooldown {int(delay)}s",
                                account["id"], run_id)
                elif TRANSIENT.search(summary["error"] or ""):
                    # Same session, same account, retried after a pause; never moved elsewhere.
                    conn.execute("update runs set state='waiting_resume', reason=?, resume_at=?, pid=null, "
                                 "updated_at=? where id=?", (("retry: " + summary["error"])[:500],
                                                             now + RETRY_SECONDS, now, run_id))
                    store.event(conn, "warn", f"transient error, retry in {RETRY_SECONDS}s: "
                                f"{summary['error'][:160]}", account["id"], run_id)
                else:
                    conn.execute("update runs set state='failed', reason=?, ended_at=?, pid=null, updated_at=? "
                                 "where id=?", ((summary["error"] or f"exit {summary['exit_code']}")[:500],
                                                now, now, run_id))
        except Exception as exc:  # noqa: BLE001
            try:
                with store.connect() as conn:
                    conn.execute("update runs set state='waiting_resume', reason=?, pid=null, updated_at=? "
                                 "where id=?", (f"{type(exc).__name__}: {exc}"[:500], time.time(), run_id))
                    store.event(conn, "error", traceback.format_exc()[-800:], account["id"], run_id)
            except Exception:  # noqa: BLE001 - the reconciler requeues runs whose worker is gone
                pass
        finally:
            with self.lock:
                self.threads.pop(run_id, None)

    def start(self, run_id: str, account: dict, cfg: dict, resume: bool) -> None:
        with store.connect() as conn:
            conn.execute("update runs set state='starting', updated_at=? where id=?", (time.time(), run_id))
        thread = threading.Thread(target=self._worker, args=(run_id, account, cfg, resume), daemon=True)
        with self.lock:
            self.threads[run_id] = thread
        thread.start()

    # ---- main loop ------------------------------------------------------------

    def reconcile(self, conn) -> None:
        """A run marked active without a live worker thread is requeued (session kept)."""
        with self.lock:
            live = set(self.threads)
        for run in conn.execute("select id, account from runs where state in ('starting','running','interrupting')"
                                ).fetchall():
            if run["id"] not in live:
                conn.execute("update runs set state='waiting_resume', reason='worker lost; requeued', pid=null, "
                             "updated_at=? where id=?", (time.time(), run["id"]))
                store.event(conn, "warn", "worker lost; run requeued with its session", run["account"], run["id"])

    def recover(self) -> None:
        """Runs that were active when the orchestrator stopped become resumable."""
        with store.connect() as conn:
            if runner.DARWIN:  # stop agents left over from a crashed orchestrator before resuming them
                for r in conn.execute("select pid from runs where pid is not null "
                                      "and state in ('starting','running','interrupting')").fetchall():
                    macos.interrupt(r["pid"], macos.signal.SIGTERM)
            conn.execute("update runs set state='waiting_resume', pid=null, updated_at=? "
                         "where state in ('starting','running','interrupting')", (time.time(),))

    def poll_codex_usage(self, accounts: dict) -> None:
        """Refresh Codex limits from active sessions' rollout files about once a minute."""
        if time.time() - getattr(self, "_codex_polled", 0) < 60:
            return
        self._codex_polled = time.time()
        from . import usage
        with store.connect() as conn:
            for run in conn.execute("select account, session_id from runs where provider='codex' "
                                    "and state='running' and session_id is not null").fetchall():
                usage.codex_rollout_limits(conn, run["account"], config.account_home(run["account"]),
                                           run["session_id"])

    def tick(self) -> None:
        cfg = config.load()
        accounts = {a["id"]: a for a in cfg["accounts"]}
        for account in accounts.values():
            self.refresh_identity(cfg, account)
        self.poll_codex_usage(accounts)
        with store.connect() as conn:
            self.reconcile(conn)
            free_gib = shutil.disk_usage(config.ROOT).free / 2**30
            critical = free_gib < float(cfg.get("disk_critical_gib", 25))
            avail = {aid: self.availability(conn, cfg, a) for aid, a in accounts.items()}
            if critical:  # protect the host: every running agent pauses, sessions kept
                avail = {aid: (False, f"free disk {free_gib:.0f} GiB below critical reserve", None) for aid in avail}
            # Interrupt active runs on accounts that became unavailable; they wait, same account.
            # Commit the new state before signalling so the worker sees 'interrupting'.
            to_interrupt = []
            for run in conn.execute("select * from runs where state='running'").fetchall():
                ok, reason, until = avail.get(run["account"], (False, "account removed", None))
                if not ok:
                    conn.execute("update runs set state='interrupting', reason=?, resume_at=?, updated_at=? "
                                 "where id=?", (reason, until, time.time(), run["id"]))
                    store.event(conn, "warn", f"interrupting: {reason}", run["account"], run["id"])
                    to_interrupt.append(run["id"])
            conn.commit()
            for run_id in to_interrupt:
                self.interrupt(run_id)
            now = time.time()
            low_disk = free_gib < float(cfg.get("disk_reserve_gib", 50))
            if low_disk and now - getattr(self, "_disk_warned", 0) > 3600:
                self._disk_warned = now
                store.event(conn, "warn", f"free disk {free_gib:.0f} GiB below reserve; no new or resumed runs")
            if low_disk:
                return
            if runner.DARWIN:  # no memory cgroups on macOS: hold new starts and resumes under memory pressure
                mem = macos.memory_free_percent()
                if mem is not None and mem < int(cfg.get("memory_reserve_percent", 15)):
                    if now - getattr(self, "_mem_warned", 0) > 3600:
                        self._mem_warned = now
                        store.event(conn, "warn", f"free memory {mem}% below reserve; no new or resumed runs")
                    return
            for pool in config.pools(cfg).values():
                if config.pool_ready(pool, list(accounts.values())):
                    self.admit(conn, cfg, pool, accounts, avail, now)

    def admit(self, conn, cfg: dict, pool: dict, accounts: dict, avail: dict, now: float,
              allow_new: bool = True) -> None:
        """Resume this pool's waiting sessions (same account), then start new runs."""
        name = pool["name"]
        limit = int(pool.get("concurrency", 0))
        per_account = int(pool.get("per_account_concurrency") or limit)
        account_gap = float(pool.get("account_start_interval_seconds", 45))
        interval = float(pool.get("new_run_interval_seconds", 60))
        priority = {p: i for i, p in enumerate(pool.get("provider_priority", []))}
        rank = lambda provider: priority.get(provider, len(priority))  # noqa: E731
        active = {r["account"]: r["n"] for r in conn.execute(
            "select account, count(*) n from runs where pool=? and state in ('starting','running','interrupting') "
            "group by account", (name,))}
        total = sum(active.values())
        waiting = sorted(conn.execute("select * from runs where pool=? and state in ('waiting_reset','waiting_resume') "
                                      "order by updated_at", (name,)).fetchall(), key=lambda r: rank(r["provider"]))
        for run in waiting:
            ok, _, _ = avail.get(run["account"], (False, None, None))
            if total >= limit or not ok or active.get(run["account"], 0) >= per_account:
                continue
            if run["resume_at"] and run["resume_at"] > now:
                continue
            if now - self.last_account_start.get(run["account"], 0) < account_gap:
                continue  # one start per account at a time (shared login refresh), across all pools
            if self.cooldown.get(run["account"], (0, 0))[0] > now:
                continue
            self.last_account_start[run["account"]] = now
            self.start(run["id"], accounts[run["account"]], cfg, resume=bool(run["session_id"]))
            active[run["account"]] = active.get(run["account"], 0) + 1
            total += 1
        if not allow_new:
            return
        last_used = {r["account"]: r["t"] for r in conn.execute(
            "select account, max(created_at) t from runs group by account")}
        # A draining account finishes its existing sessions but starts no new runs.
        candidates = sorted((a for aid, a in accounts.items() if avail[aid][0] and not a.get("drain")),
                            key=lambda a: (rank(a["provider"]), last_used.get(a["id"], 0)))
        # Stagger new runs so later agents can read earlier agents' publications.
        for account in candidates:
            if total >= limit or active.get(account["id"], 0) >= per_account:
                continue
            if now - self.last_new_start_by_pool.get(name, 0) < interval:
                break
            if now - self.last_account_start.get(account["id"], 0) < account_gap:
                continue
            if self.cooldown.get(account["id"], (0, 0))[0] > now:
                continue
            self.last_account_start[account["id"]] = now
            run_id = runner.new_run(conn, account, cfg, pool)
            conn.commit()
            self.start(run_id, account, cfg, resume=False)
            self.last_new_start_by_pool[name] = now
            active[account["id"]] = active.get(account["id"], 0) + 1
            total += 1
            break

    def serve(self, stop: threading.Event) -> None:
        self.recover()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                with store.connect() as conn:
                    store.event(conn, "error", traceback.format_exc()[-800:])
            stop.wait(TICK)
        self.interrupt_all()
