"""SQLite state: accounts, usage windows, runs, token totals, and events."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
create table if not exists account_state(
  account text primary key, state text not null, reason text, until real,
  identity text, checked_at real, credits_baseline real, credits_latest real);
create table if not exists usage(
  account text not null, window text not null, used_percent real, resets_at real,
  status text, detail text, observed_at real not null, primary key(account, window));
create table if not exists usage_history(
  account text not null, window text not null, used_percent real, resets_at real,
  status text, observed_at real not null);
create index if not exists usage_history_at on usage_history(account, observed_at);
create table if not exists runs(
  id text primary key, account text not null, provider text not null, session_id text,
  state text not null, reason text, created_at real not null, updated_at real not null,
  started_at real, ended_at real, resume_at real, pid integer, turns integer not null default 0,
  model text, input_tokens integer not null default 0, cached_input_tokens integer not null default 0,
  cache_write_tokens integer not null default 0, output_tokens integer not null default 0,
  cost_usd real not null default 0);
create index if not exists runs_state on runs(state);
create table if not exists events(
  id integer primary key autoincrement, at real not null, level text not null,
  account text, run text, message text not null);
"""


@contextmanager
def connect():
    config.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(config.DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.executescript(SCHEMA)
    if "pool" not in [r[1] for r in conn.execute("pragma table_info(runs)")]:
        conn.execute("alter table runs add column pool text not null default 'quo'")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def event(conn, level: str, message: str, account: str | None = None, run: str | None = None) -> None:
    conn.execute("insert into events(at, level, account, run, message) values (?,?,?,?,?)",
                 (time.time(), level, account, run, message))


def set_account_state(conn, account: str, state: str, reason: str | None = None,
                      until: float | None = None, identity: dict | None = None) -> None:
    row = conn.execute("select * from account_state where account=?", (account,)).fetchone()
    ident = json.dumps(identity) if identity is not None else (row["identity"] if row else None)
    checked = time.time() if identity is not None else (row["checked_at"] if row else None)
    conn.execute(
        """insert into account_state(account, state, reason, until, identity, checked_at)
           values (?,?,?,?,?,?)
           on conflict(account) do update set state=excluded.state, reason=excluded.reason,
             until=excluded.until, identity=excluded.identity, checked_at=excluded.checked_at""",
        (account, state, reason, until, ident, checked))


def record_usage(conn, account: str, window: str, used_percent, resets_at, status=None, detail=None) -> None:
    # A known percent replaces the old one. A new window (different reset time) without a percent clears it,
    # so a reading from an earlier window never blocks the next one.
    now = time.time()
    conn.execute(
        """insert into usage(account, window, used_percent, resets_at, status, detail, observed_at)
           values (?,?,?,?,?,?,?)
           on conflict(account, window) do update set used_percent=case
               when excluded.used_percent is not null then excluded.used_percent
               when excluded.resets_at is not null and excluded.resets_at != usage.resets_at then null
               else usage.used_percent end,
             resets_at=coalesce(excluded.resets_at, usage.resets_at), status=excluded.status,
             detail=excluded.detail, observed_at=excluded.observed_at""",
        (account, window, used_percent, resets_at, status, json.dumps(detail) if detail else None, now))
    conn.execute("insert into usage_history values (?,?,?,?,?,?)",
                 (account, window, used_percent, resets_at, status, now))


def record_credits(conn, account: str, balance: float) -> None:
    row = conn.execute("select credits_baseline from account_state where account=?", (account,)).fetchone()
    if row is None:
        set_account_state(conn, account, "unknown")
    conn.execute(
        "update account_state set credits_latest=?, credits_baseline=coalesce(credits_baseline, ?) where account=?",
        (balance, balance, account))
