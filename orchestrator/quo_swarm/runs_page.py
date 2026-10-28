"""Read-only operator page summarizing each run (agents never see it).

Shows each run's state, tool-call count, sci sign/push commands, and latest
messages. Everything shown was written by agents: it is escaped and never interpreted.
"""

from __future__ import annotations

import html
import json
import time

from . import config, store

def run_summary(run_id: str, messages: int = 3) -> dict:
    """Tool-call count, sci sign/push commands, and the last agent messages (Claude or Codex)."""
    texts, tools, sci = [], 0, []
    path = config.RUNS / run_id / "events.jsonl"
    if not path.exists():
        return {"tools": 0, "sci": [], "messages": []}
    for line in path.open(errors="replace"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        commands = []
        if o.get("type") == "assistant":
            for c in o.get("message", {}).get("content", []):
                if c.get("type") == "text":
                    texts.append(c["text"])
                elif c.get("type") == "tool_use":
                    tools += 1
                    commands.append(str((c.get("input") or {}).get("command", "")))
        elif o.get("type") == "item.completed":
            item = o.get("item", {})
            if item.get("type") == "agent_message":
                texts.append(item.get("text", ""))
            elif item.get("type") == "command_execution":
                tools += 1
                commands.append(str(item.get("command", "")))
        sci += [c[:160] for c in commands if "sci push" in c or "sci sign" in c]
    return {"tools": tools, "sci": sci[-3:], "messages": [" ".join(t.split())[:600] for t in texts[-messages:]]}


def page() -> bytes:
    e = html.escape
    with store.connect() as conn:
        runs = [dict(r) for r in conn.execute(
            "select * from runs where state != 'failed' or reason not like 'smoke test%' order by created_at desc")]
    out = ["<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>",
           "<meta http-equiv=refresh content=60><title>Swarm runs</title>",
           "<style>body{font:14px system-ui;margin:0 auto;max-width:980px;padding:16px;background:#fafaf8;color:#222}"
           "h1{font-size:20px}.run{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:12px 0}"
           ".meta{color:#666;font-size:12px}pre{white-space:pre-wrap;background:#f3f3f0;padding:8px;border-radius:6px;"
           "font-size:12.5px}.msg{margin:6px 0;padding-left:8px;border-left:3px solid #c9d6e8}"
           ".state-running{color:#1a7f37}.state-completed{color:#555}.state-failed{color:#b42318}"
           "a{color:#2459a8}</style>",
           f"<h1>Swarm runs</h1><p class=meta>Updated {time.strftime('%Y-%m-%d %H:%M:%S')} · refreshes every "
           f"60 s · <a href='/'>usage console</a></p>"]
    for r in runs:
        s = run_summary(r["id"])
        out.append(f"<div class=run><b>{e(r['id'][:8])}</b> · {e(r['account'])} · {e(r.get('model') or '')} · "
                   f"<span class='state-{e(r['state'])}'>{e(r['state'])}</span> · turns {r['turns']} · "
                   f"tool calls {s['tools']}<div class=meta>started "
                   f"{time.strftime('%m-%d %H:%M', time.localtime(r['created_at']))}"
                   f"{' · ' + e(r['reason'][:120]) if r.get('reason') else ''}</div>")
        if s["sci"]:
            out.append("<p><b>SCI activity</b></p><pre>" + e("\n".join(s["sci"])) + "</pre>")
        out.append("<p><b>Latest messages</b></p>" + "".join(f"<div class=msg>{e(m)}</div>" for m in s["messages"]))
        out.append("</div>")
    return "".join(out).encode()
