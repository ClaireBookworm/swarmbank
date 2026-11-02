"""Scheduler heartbeat and systemd watchdog, outside all researcher sandboxes."""
from __future__ import annotations

import json
import os
import socket
import time

from . import config


def heartbeat() -> None:
    """Record real scheduler progress; an HTTP server alone is not a healthy swarm."""
    config.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = config.STATE / "heartbeat.tmp"
    tmp.write_text(json.dumps({"checked_at": time.time(), "pid": os.getpid(),
                               "watchdog": bool(os.environ.get("NOTIFY_SOCKET"))}) + "\n", "utf-8")
    tmp.replace(config.STATE / "heartbeat.json")
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.sendto(b"WATCHDOG=1", address)


def snapshot() -> dict:
    try:
        report = json.loads((config.STATE / "heartbeat.json").read_text("utf-8"))
        age = max(0, time.time() - float(report["checked_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "age_seconds": None, "watchdog": False}
    alive = False
    try:
        pid = int(report["pid"])
        if pid > 0:
            os.kill(pid, 0)  # also works when status is read by the operator CLI
            alive = True
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return {**report, "ok": age < 90 and alive, "age_seconds": age}
