"""Parse usage and limit signals from the official CLIs' own output.

Claude Code: stream-json ``rate_limit_event`` (status, rateLimitType, utilization,
resetsAt, overage flags) and ``result`` (token usage, session id).
Codex: ``exec --json`` events (thread id, turn usage) and the session rollout's
``token_count`` events (rate_limits: primary/secondary used_percent, resets_at,
credits).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import store


def _pct(value):
    if value is None:
        return None
    value = float(value)
    return value * 100 if value <= 1.0 else value


def claude_line(conn, run: dict, obj: dict) -> dict:
    """Return run updates derived from one Claude stream-json object."""
    updates: dict = {}
    kind = obj.get("type")
    if obj.get("session_id") and not run.get("session_id"):
        updates["session_id"] = obj["session_id"]
    if kind == "system" and obj.get("subtype") == "init":
        updates["model"] = obj.get("model")
    elif kind == "rate_limit_event":
        info = obj.get("rate_limit_info") or {}
        window = info.get("rateLimitType") or "unified"
        windows = info.get("unifiedWindows") or {}
        utilization = info.get("utilization")
        if utilization is None and isinstance(windows.get(window), dict):
            utilization = windows[window].get("utilization")  # newer CLIs put the percent only here
        store.record_usage(conn, run["account"], window, _pct(utilization),
                           info.get("resetsAt"), info.get("status"), info)
        for other, item in windows.items():
            if other != window and isinstance(item, dict):
                store.record_usage(conn, run["account"], other, _pct(item.get("utilization")),
                                   item.get("resetsAt"), "observed", item)
        if info.get("isUsingOverage") or info.get("overageInUse"):
            updates["_overage"] = True
        if info.get("status") == "rejected":
            updates["_limited"] = info.get("resetsAt")
    elif kind == "result":
        usage = obj.get("usage") or {}
        updates["_tokens"] = {
            "input_tokens": usage.get("input_tokens") or 0,
            "cached_input_tokens": usage.get("cache_read_input_tokens") or 0,
            "cache_write_tokens": usage.get("cache_creation_input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "cost_usd": obj.get("total_cost_usd") or 0,
        }
        updates["_result"] = obj.get("subtype") or "success"
        if obj.get("is_error"):
            updates["_error"] = str(obj.get("result") or obj.get("subtype"))[:500]
    return updates


def codex_line(conn, run: dict, obj: dict) -> dict:
    updates: dict = {}
    kind = obj.get("type")
    if kind == "thread.started" and obj.get("thread_id"):
        updates["session_id"] = obj["thread_id"]
    elif kind == "turn.completed":
        usage = obj.get("usage") or {}
        updates["_tokens"] = {
            "input_tokens": usage.get("input_tokens") or 0,
            "cached_input_tokens": usage.get("cached_input_tokens") or 0,
            "cache_write_tokens": 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "cost_usd": 0,
        }
        updates["_result"] = "success"
    elif kind in ("turn.failed", "error"):
        message = json.dumps(obj.get("error") or obj.get("message") or obj)[:500]
        updates["_error"] = message
        if "usage limit" in message.lower() or "rate limit" in message.lower():
            updates["_limited"] = None
    return updates


def codex_rollout_limits(conn, account: str, codex_home: Path, session_id: str | None) -> dict:
    """Read the newest token_count rate limits for a session from its rollout file."""
    if not session_id:
        return {}
    files = sorted((codex_home / "sessions").glob(f"*/*/*/rollout-*{session_id}.jsonl"))
    if not files:
        return {}
    latest = None
    with files[-1].open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if '"rate_limits"' in line:
                latest = line
    if not latest:
        return {}
    payload = json.loads(latest).get("payload") or {}
    limits = payload.get("rate_limits") or {}
    updates: dict = {}
    for window in ("primary", "secondary"):
        item = limits.get(window)
        if item:
            store.record_usage(conn, account, window, item.get("used_percent"), item.get("resets_at"),
                               limits.get("rate_limit_reached_type") or "observed",
                               dict(item, plan_type=limits.get("plan_type")))
    credits = limits.get("credits") or {}
    if credits.get("balance") is not None:
        store.record_credits(conn, account, float(credits["balance"]))
    if limits.get("rate_limit_reached_type") or limits.get("spend_control_reached"):
        updates["_limited"] = None
    return updates
