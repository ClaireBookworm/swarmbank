#!/usr/bin/env python3
"""Check `tailscale status --json` without changing the host or printing peers."""
import json
import sys


def problems(status):
    if not isinstance(status, dict):
        return ["Invalid Tailscale status: expected a JSON object."]
    failures = []
    if status.get("BackendState") != "Running":
        failures.append(f"Tailscale is not running: {status.get('BackendState', 'unknown')}.")
    own_status = status.get("Self")
    if not isinstance(own_status, dict) or own_status.get("Online") is not True:
        failures.append("Tailscale does not report this machine online with its coordination server.")
    health = status.get("Health")
    if isinstance(health, list):
        failures.extend(f"Tailscale health: {warning}" for warning in health)
    elif health is not None:
        failures.append("Unexpected Tailscale Health field; inspect `tailscale status --json`.")
    return failures


def main():
    try:
        failures = problems(json.load(sys.stdin))
    except (ValueError, OSError) as exc:
        print(f"Could not read Tailscale status: {exc}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        print("An assigned IP or working SSH session alone does not prove Tailscale is healthy. "
              "Check `tailscale status` and `journalctl -u tailscaled`.", file=sys.stderr)
        return 1
    print("Tailscale: running, online, no reported health warnings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
