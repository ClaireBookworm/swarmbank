"""Read-only usage console (replaces the retired gateway console on :8318 / tailnet :8443)."""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from . import config, store


def _proxy_safety() -> dict:
    """Evidence that no retired proxy is running."""
    check = config.load().get("proxy_check") or {"hosts": ["127.0.0.1"], "port": 8317}
    listening = False
    for host in check["hosts"]:
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            s.settimeout(0.3)
            listening |= s.connect_ex((host, int(check["port"]))) == 0
    if sys.platform == "darwin":  # launchd jobs instead of systemd units
        jobs = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
        units = "\n".join(line for line in jobs.splitlines() if "proxy" in line.lower())
    else:
        units = subprocess.run(["systemctl", "--user", "list-unit-files", "proxy-sci*", "--no-legend"],
                               capture_output=True, text=True).stdout.strip()
    return {"port_8317_listening": listening, "proxy_units_installed": bool(units),
            "ok": not listening and not units}


def snapshot() -> dict:
    cfg = config.load()
    now = time.time()
    with store.connect() as conn:
        states = {r["account"]: dict(r) for r in conn.execute("select * from account_state")}
        usage: dict = {}
        for r in conn.execute("select * from usage order by account, window"):
            usage.setdefault(r["account"], []).append({k: r[k] for k in ("window", "used_percent", "resets_at",
                                                                          "status", "observed_at")})
        totals = {}
        for span, seconds in (("24h", 86400), ("7d", 7 * 86400)):
            for r in conn.execute(
                    """select account, count(*) runs, sum(input_tokens) input, sum(cached_input_tokens) cached,
                              sum(cache_write_tokens) cache_write, sum(output_tokens) output, sum(cost_usd) cost
                       from runs where updated_at >= ? group by account""", (now - seconds,)):
                totals.setdefault(r["account"], {})[span] = dict(r)
        runs = [dict(r) for r in conn.execute(
            "select id, pool, account, provider, state, reason, model, turns, created_at, updated_at, resume_at, "
            "input_tokens, cached_input_tokens, output_tokens, cost_usd from runs "
            "order by case when state in ('running','starting','interrupting') then 0 "
            "when state like 'waiting%' then 1 else 2 end, updated_at desc limit 60")]
        counts = {r["state"]: r["n"] for r in conn.execute("select state, count(*) n from runs group by state")}
        by_pool = {}
        for r in conn.execute("select pool, state, count(*) n from runs group by pool, state"):
            by_pool.setdefault(r["pool"], {})[r["state"]] = r["n"]
        pools = []
        for name, pool in config.pools(cfg).items():
            c = by_pool.get(name, {})
            pools.append({"name": name, "enabled": bool(pool.get("enabled")), "concurrency": pool.get("concurrency", 0),
                          "server": pool["sci"]["server"],
                          "active": c.get("running", 0) + c.get("starting", 0) + c.get("interrupting", 0),
                          "waiting": c.get("waiting_reset", 0) + c.get("waiting_resume", 0),
                          "completed": c.get("completed", 0), "failed": c.get("failed", 0)})
        events = [dict(r) for r in conn.execute("select * from events order by id desc limit 40")]
    accounts = []
    for a in cfg["accounts"]:
        st = states.get(a["id"], {})
        ident = json.loads(st["identity"]) if st.get("identity") else {}
        accounts.append({
            "id": a["id"], "provider": a["provider"], "email": a["email"], "enabled": a.get("enabled", True),
            "state": st.get("state", "unknown"), "reason": st.get("reason"), "until": st.get("until"),
            "logged_in": ident.get("logged_in"), "verified_email": ident.get("email"), "plan": ident.get("plan"),
            "checked_at": st.get("checked_at"), "credits_baseline": st.get("credits_baseline"),
            "credits_latest": st.get("credits_latest"), "usage": usage.get(a["id"], []),
            "totals": totals.get(a["id"], {}),
            "caps": ({w: a["cap_percent"] for w in cfg["caps_percent"][a["provider"]]} | {"default": a["cap_percent"]})
            if "cap_percent" in a else cfg["caps_percent"][a["provider"]],
        })
    return {
        "now": now, "launch_enabled": cfg["launch_enabled"], "pool": cfg["pool"], "models": cfg["models"],
        "task_configured": bool(cfg.get("task_prompt")), "excluded": cfg["excluded_emails"],
        "accounts": accounts, "pools": pools, "runs": runs, "run_counts": counts, "events": events,
        "proxy": _proxy_safety(),
    }


class Handler(BaseHTTPRequestHandler):
    allow: set = set()

    def log_message(self, *args):
        pass

    def _client(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:  # Tailscale Serve appends the real client; trust the last entry
            return forwarded.split(",")[-1].strip()
        return self.client_address[0]

    def _allowed(self) -> bool:
        try:
            client = ipaddress.ip_address(self._client())
        except ValueError:
            return False
        return any(client == ipaddress.ip_address(a) for a in self.allow)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; base-uri 'none'; form-action 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if not self._allowed():
            return self._send(403, b"forbidden\n", "text/plain")
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self._send(200, json.dumps(snapshot()).encode(), "application/json")
        if path in ("/", "/index.html"):
            return self._send(200, files("quo_swarm").joinpath("console.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path == "/runs":
            from .runs_page import page
            return self._send(200, page(), "text/html; charset=utf-8")
        if path == "/console.js":
            return self._send(200, files("quo_swarm").joinpath("console.js").read_bytes(),
                              "text/javascript; charset=utf-8")
        return self._send(404, b"not found\n", "text/plain")


def serve(stop_event=None) -> ThreadingHTTPServer:
    cfg = config.load()
    Handler.allow = set(cfg["console"]["allow"])
    server = ThreadingHTTPServer((cfg["console"]["host"], int(cfg["console"]["port"])), Handler)
    server.daemon_threads = True
    return server
