#!/usr/bin/env bash
# Install the read-only Tapo power logger as a hardened system service on Ubuntu.
# Usage: bash install.sh --host STRIP_IP [--mac AA:BB:CC:DD:EE:FF] [--interval 60]
# Prompts for the TP-Link account email/password and stores them root-only.
set -Eeuo pipefail
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
KASA_VERSION=0.12.0
HOST='' MAC='' INTERVAL=60
die() { echo "ERROR: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case $1 in
    --host) HOST=${2:-}; shift 2;;
    --mac) MAC=${2:-}; shift 2;;
    --interval) INTERVAL=${2:-}; shift 2;;
    -h|--help) sed -n '2,4p' "$0"; exit 0;;
    *) die "Unknown option: $1";;
  esac
done
[[ -n $HOST || -n $MAC ]] || die 'Pass --host STRIP_IP (find it with: uvx --from python-kasa kasa discover).'
[[ -z $HOST || $HOST =~ ^[A-Za-z0-9.:-]+$ ]] || die 'Invalid --host.'
[[ -z $MAC || $MAC =~ ^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$ ]] || die 'Invalid --mac.'
[[ $INTERVAL =~ ^[0-9]+$ && $INTERVAL -ge 10 ]] || die '--interval must be an integer >= 10.'
[[ -t 0 ]] || die 'Run interactively (or with ssh -t) so credentials can be typed, not piped.'
command -v python3 >/dev/null || die 'python3 is required.'
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x $UV ]] || die 'uv is required (machine-onboarding/setup.sh installs it).'

sudo -v
if ! sudo test -s /etc/tapo-logger/credentials; then
  read -r -p 'TP-Link account email: ' EMAIL
  read -r -s -p 'TP-Link account password: ' PASS; echo
  [[ -n $EMAIL && -n $PASS ]] || die 'Email and password are required.'
  sudo install -d -m 700 -o root -g root /etc/tapo-logger
  printf '%s\n%s\n' "$EMAIL" "$PASS" | sudo install -m 600 -o root -g root /dev/stdin /etc/tapo-logger/credentials
  unset EMAIL PASS
else
  echo 'Keeping existing /etc/tapo-logger/credentials.'
fi
printf 'TAPO_HOST=%s\nTAPO_MAC=%s\nTAPO_INTERVAL=%s\n' "$HOST" "$MAC" "$INTERVAL" |
  sudo install -m 644 -o root -g root /dev/stdin /etc/tapo-logger/config.env

id tapo-logger >/dev/null 2>&1 ||
  sudo useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin tapo-logger
CACHE=$(mktemp -d)
sudo install -d -m 755 /opt/tapo-logger
sudo UV_CACHE_DIR="$CACHE" "$UV" venv -q --allow-existing --python /usr/bin/python3 /opt/tapo-logger/venv
sudo UV_CACHE_DIR="$CACHE" "$UV" pip install -q --python /opt/tapo-logger/venv/bin/python "python-kasa==$KASA_VERSION"
sudo rm -rf "$CACHE"
sudo install -m 644 "$SRC/tapo_logger.py" /opt/tapo-logger/tapo_logger.py
sudo install -m 755 "$SRC/tapo-power" /usr/local/bin/tapo-power
sudo install -m 644 "$SRC/tapo-logger.service" /etc/systemd/system/tapo-logger.service
sudo systemctl daemon-reload
sudo systemctl enable tapo-logger >/dev/null
sudo systemctl restart tapo-logger

echo 'Waiting for the first reading...'
for _ in $(seq 1 30); do [[ -s /var/lib/tapo-logger/latest.json ]] && break; sleep 2; done
systemctl is-active tapo-logger
journalctl -u tapo-logger -n 5 --no-pager -o cat
[[ -s /var/lib/tapo-logger/latest.json ]] || die 'No reading yet; see journalctl -u tapo-logger.'
tapo-power
