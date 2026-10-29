#!/usr/bin/env bash
# AC-only laptop settings. Never masks sleep targets or changes battery policy.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$ROOT/lib/common.sh"
case "${1:-}" in
  --help|-h) echo 'Usage: bash power.sh apply|restore'; exit 0;;
  apply|restore) ;;
  *) die 'Usage: bash power.sh apply|restore';;
esac
require_ubuntu_user
STATE="$HOME/.local/state/swarmbank-onboarding"
install -d -m 700 "$STATE"
SCHEMA=org.gnome.settings-daemon.plugins.power
HAS_GNOME=0
if command -v gsettings >/dev/null && gsettings list-schemas | grep -Fx "$SCHEMA" >/dev/null; then HAS_GNOME=1; fi
if [[ -S /run/user/$(id -u)/bus ]]; then
  export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
fi
if [[ $1 == apply ]]; then
  if ((HAS_GNOME)); then
    [[ -f "$STATE/ac-sleep-before" ]] || gsettings get "$SCHEMA" sleep-inactive-ac-type > "$STATE/ac-sleep-before"
    gsettings set "$SCHEMA" sleep-inactive-ac-type nothing
    [[ $(gsettings get "$SCHEMA" sleep-inactive-ac-type) == "'nothing'" ]] || die 'Could not set desktop AC sleep; retry from your Ubuntu desktop terminal.'
  else
    echo 'No GNOME power schema: check the desktop AC idle-sleep setting manually.'
  fi
  sudo install -d -m 755 /etc/systemd/logind.conf.d
  printf '[Login]\nHandleLidSwitchExternalPower=ignore\n' | sudo tee /etc/systemd/logind.conf.d/80-swarmbank-lid.conf >/dev/null
  touch "$STATE/power-configured"
  echo 'AC lid policy saved. Reboot when ready; do not restart logind in this session.'
else
  if [[ -f "$STATE/ac-sleep-before" ]]; then
    ((HAS_GNOME)) || die 'Restore from a GNOME session so the saved desktop setting can be applied.'
    gsettings set "$SCHEMA" sleep-inactive-ac-type "$(cat "$STATE/ac-sleep-before")"
    rm -f "$STATE/ac-sleep-before"
  fi
  sudo rm -f /etc/systemd/logind.conf.d/80-swarmbank-lid.conf
  rm -f "$STATE/power-configured"
  echo 'Original desktop AC setting restored and managed lid rule removed. Reboot when ready.'
fi
