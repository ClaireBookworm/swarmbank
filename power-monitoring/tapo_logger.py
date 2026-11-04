#!/usr/bin/env python3
"""Read-only per-outlet power logger for a TP-Link Tapo energy-monitoring power strip.

Every TAPO_INTERVAL seconds it appends one CSV row per outlet to $STATE_DIRECTORY/power.csv
and atomically replaces $STATE_DIRECTORY/latest.json. It only ever calls update() on the
device: there is no code path that switches outlets or changes device settings.

Configuration (environment, normally from /etc/tapo-logger/config.env):
  TAPO_HOST      strip IP or hostname (required unless TAPO_MAC is set)
  TAPO_MAC       optional; if set, the strip is verified by MAC and rediscovered by MAC
                 on the LAN when TAPO_HOST stops answering (e.g. after a DHCP change)
  TAPO_INTERVAL  seconds between readings (default 60, minimum 10)
Credentials: a systemd credential named "tapo" (first line: TP-Link account email,
second line: password), read from $CREDENTIALS_DIRECTORY/tapo.
"""
import asyncio
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

FIELDS = ["time", "outlet", "on", "watts", "volts", "amps", "kwh_today", "kwh_month"]


def norm_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


def load_credentials(directory: str):
    from kasa import Credentials

    lines = (Path(directory) / "tapo").read_text().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1]:
        raise ValueError("credential file must contain the account email and password on two lines")
    return Credentials(lines[0].strip(), lines[1])


def _round(value, digits):
    return "" if value is None else round(float(value), digits)


def rows(dev, energy_module, now=None):
    """One dict per outlet. energy_module is the key used to look up the energy module."""
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    for child in dev.children:
        energy = child.modules.get(energy_module)
        yield {
            "time": stamp,
            "outlet": child.alias,
            "on": int(bool(child.is_on)),
            "watts": _round(energy.current_consumption, 1) if energy else "",
            "volts": _round(energy.voltage, 1) if energy else "",
            "amps": _round(energy.current, 3) if energy else "",
            "kwh_today": energy.consumption_today if energy else "",
            "kwh_month": energy.consumption_this_month if energy else "",
        }


def append(out_dir: Path, snapshot):
    """Append rows to power.csv (header on first write) and replace latest.json atomically."""
    csv_path = out_dir / "power.csv"
    new = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(snapshot)
    tmp = out_dir / "latest.json.tmp"
    tmp.write_text(json.dumps(snapshot, indent=1))
    tmp.replace(out_dir / "latest.json")


async def connect(host, mac, creds):
    from kasa import Discover

    if host:
        try:
            dev = await Discover.discover_single(host, credentials=creds, timeout=10)
            await dev.update()
            if not mac or norm_mac(dev.mac) == mac:
                return dev
            print(f"{host} is not the configured strip (MAC {dev.mac}); rediscovering", flush=True)
            await dev.disconnect()
        except Exception as e:  # noqa: BLE001 - log and fall back to discovery
            print(f"connect to {host} failed: {e!r}", flush=True)
    if not mac:
        raise RuntimeError("strip unreachable and TAPO_MAC is not set for rediscovery")
    found = await Discover.discover(credentials=creds, discovery_timeout=8)
    match = None
    for ip, dev in found.items():
        if match is None and norm_mac(dev.mac) == mac:
            match = dev
            print(f"strip found at {ip}; consider updating TAPO_HOST", flush=True)
        else:
            await dev.disconnect()
    if match is None:
        raise RuntimeError("strip not found by MAC on the LAN")
    await match.update()
    return match


async def main():
    from kasa import Module

    host = os.environ.get("TAPO_HOST", "").strip()
    mac = norm_mac(os.environ.get("TAPO_MAC", ""))
    if not host and not mac:
        raise SystemExit("Set TAPO_HOST (and optionally TAPO_MAC) in the service environment.")
    interval = max(10, int(os.environ.get("TAPO_INTERVAL", "60")))
    out_dir = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/tapo-logger"))
    creds = load_credentials(os.environ["CREDENTIALS_DIRECTORY"])
    dev = None
    while True:
        start = time.monotonic()
        try:
            if dev is None:
                dev = await connect(host, mac, creds)
            else:
                await dev.update()
            append(out_dir, list(rows(dev, Module.Energy)))
        except Exception as e:  # noqa: BLE001 - keep logging through outages
            print(f"read failed: {e!r}", flush=True)
            if dev is not None:
                try:
                    await dev.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            dev = None
        await asyncio.sleep(max(5, interval - (time.monotonic() - start)))


if __name__ == "__main__":
    asyncio.run(main())
