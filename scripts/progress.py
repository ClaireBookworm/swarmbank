"""Summarize each swarm run: provider, tool calls, last messages, sci sign/push activity."""
import json, sqlite3, sys
from pathlib import Path
R = Path.home() / ".local/share/quo-swarm"
db = sqlite3.connect(R / "state/swarm.sqlite"); db.row_factory = sqlite3.Row
for run in db.execute("select * from runs where state!='failed' order by account, created_at"):
    texts, tools, sci = [], 0, []
    for line in open(R / "runs" / run["id"] / "events.jsonl", errors="replace"):
        try: o = json.loads(line)
        except ValueError: continue
        if o.get("type") == "assistant":  # Claude
            for c in o["message"].get("content", []):
                if c.get("type") == "text": texts.append(c["text"])
                if c.get("type") == "tool_use":
                    tools += 1; cmd = str((c.get("input") or {}).get("command", ""))
                    if "sci sign" in cmd or "sci push" in cmd: sci.append(cmd[:80])
        elif o.get("type") == "item.completed":  # Codex
            it = o.get("item", {})
            if it.get("type") == "agent_message": texts.append(it.get("text", ""))
            if it.get("type") == "command_execution":
                tools += 1; cmd = str(it.get("command", ""))
                if "sci sign" in cmd or "sci push" in cmd: sci.append(cmd[:80])
    print(f"== {run['id'][:8]} {run['account']} {run['state']} tools={tools} sci={len(sci)}")
    for t in texts[-int(sys.argv[1] if len(sys.argv) > 1 else 2):]: print("   >", " ".join(t.split())[:400])
