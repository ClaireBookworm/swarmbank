#!/usr/bin/env bash
# Read-only checks. Missing Codex/Claude authentication is intentional, not failure.
# bash -c expressions deliberately expand in the child shell.
# shellcheck disable=SC2016
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$ROOT/lib/common.sh"
case "${1:-}" in --help|-h) echo 'Usage: bash doctor.sh'; exit 0;; '') ;; *) die 'Usage: bash doctor.sh';; esac
require_ubuntu_user
load_tools
FAILED=0
check() {
  local title=$1
  shift
  printf '\n--- %s ---\n' "$title"
  if ! "$@"; then echo 'FAIL: needs attention.'; FAILED=1; fi
}
check 'SSH' systemctl is-active ssh
check 'Tailscale daemon' systemctl is-active tailscaled
check 'Tailscale address (join the tailnet first)' tailscale ip -4
for tool in git gh node npm python3 uv codex claude; do check "$tool installed" "$tool" --version; done
check 'Git name' git config --global --get user.name
check 'Git email' git config --global --get user.email
check 'GitHub CLI login' gh auth status --hostname github.com
check 'GitHub API access' gh api user --jq .login
check 'GitHub protocol' bash -c '[[ $(gh config get git_protocol --host github.com) == ssh ]]'
check 'GitHub SSH' bash -c '
  status=0
  result=$(ssh -T -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes git@github.com 2>&1) || status=$?
  printf "%s\n" "$result"
  [[ $status == 1 && $result == *"successfully authenticated"* ]]
'
if command -v t3 >/dev/null; then
  check 'T3 version' t3 --version
  check 'T3 running' systemctl --user is-active t3code.service
  check 'T3 enabled at boot' systemctl --user is-enabled t3code.service
  check 'User services survive logout' bash -c '[[ $(loginctl show-user "$(id -un)" --property=Linger --value) == yes ]]'
else
  echo 'T3 not installed (optional with --no-t3).'
fi
check 'Shell PATH (startup from a minimal PATH)' env PATH=/usr/bin:/bin bash --noprofile --norc -c '. "$HOME/.bashrc"; for tool in node codex claude; do command -v "$tool" || exit 1; done'
check 'Disk space' df -h "$HOME"
check 'Automatic security updates' grep -Fx 'APT::Periodic::Unattended-Upgrade "1";' /etc/apt/apt.conf.d/80-swarmbank-periodic
check 'No automatic reboot' grep -Fx 'Unattended-Upgrade::Automatic-Reboot "false";' /etc/apt/apt.conf.d/99-swarmbank-no-reboot
if [[ -f "$HOME/.local/state/swarmbank-onboarding/power-configured" ]]; then
  check 'AC lid-close configuration' grep -Fx 'HandleLidSwitchExternalPower=ignore' /etc/systemd/logind.conf.d/80-swarmbank-lid.conf
  if command -v gsettings >/dev/null && gsettings list-schemas | grep -Fx org.gnome.settings-daemon.plugins.power >/dev/null; then
    check 'GNOME AC idle sleep' test "$(gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type)" = "'nothing'"
  fi
fi
if [[ -f /var/run/reboot-required ]]; then echo 'NOTE: Ubuntu requests a reboot; schedule it after jobs finish.'; fi
echo 'Codex/Claude account login is deliberately not required by this check.'
echo 'Also test SSH from your controller after reboot and with the lid closed on AC.'
exit "$FAILED"
