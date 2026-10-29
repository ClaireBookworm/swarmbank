#!/usr/bin/env bash
# Human-approved account connections only. Never signs in Codex or Claude.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$ROOT/lib/common.sh"
ACTION=${1:-all}
case "$ACTION" in
  --help|-h) echo 'Usage: bash connect.sh [all|tailscale|github]'; exit 0;;
  all|tailscale|github) ;;
  *) die 'Choose all, tailscale, or github.';;
esac
require_ubuntu_user
[[ -t 0 ]] || die 'Run in a terminal, or use ssh -t. Complete the browser approvals yourself.'
load_tools
STATE="$HOME/.local/state/swarmbank-onboarding"
install -d -m 700 "$STATE"
# Avoid two pending GitHub device codes from copies of this helper.
exec 9>"$STATE/connect.lock"
flock -n 9 || die 'Another connect.sh is running. Finish or cancel it before starting a new login.'

if [[ $ACTION == all || $ACTION == tailscale ]]; then
  command -v tailscale >/dev/null || die 'Run setup.sh first.'
  note 'Join the same Tailscale account/network as your controller. Approve the printed URL.'
  sudo tailscale up
  IP=$(tailscale ip -4)
  [[ -n $IP ]] || die 'Tailscale has not assigned an IPv4 address.'
  printf '\nController connection: ssh %s@%s\n' "$(id -un)" "$IP"
  echo 'Compare this host-key fingerprint on your first controller connection:'
  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
  printf '\nIf no controller key was supplied to setup.sh, run on the controller:\n  ssh-copy-id -i ~/.ssh/id_ed25519.pub %s@%s\n' "$(id -un)" "$IP"
fi

if [[ $ACTION == all || $ACTION == github ]]; then
  command -v gh >/dev/null || die 'Run setup.sh first.'
  KEY="$HOME/.ssh/id_ed25519_swarmbank_github.pub"
  [[ -f $KEY ]] || die 'Run setup.sh to generate the GitHub key for this machine.'
  note 'GitHub SSH key (public):'
  cat "$KEY"
  printf '\nOn your controller, open https://github.com/settings/ssh/new\nTitle: %s\nType: Authentication Key\nPaste the public key above. If already added, keep the existing entry.\n' "$(hostname)-github"
  read -r -p 'Press Enter after adding/confirming this key in GitHub. ' REPLY
  if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    note 'Now authorize GitHub CLI at https://github.com/login/device on your controller.'
    echo 'Use ONLY the new code printed below; keep this terminal running until it says Authentication complete.'
    echo 'If asked about uploading an SSH key, choose Skip (it is already registered).'
    # "true" absorbs browser launch; the OAuth code is entered on the controller.
    # Compatible with older distro gh versions that lack --skip-ssh-key.
    GH_BROWSER=true gh auth login --hostname github.com --git-protocol ssh --web
  fi
  gh config set git_protocol ssh --host github.com
  gh auth status --hostname github.com
  gh api user --jq .login
  # Install the officially published key only if no host entry exists.
  # Never remove or replace a changed host key to make a connection succeed.
  touch "$HOME/.ssh/known_hosts"
  chmod 600 "$HOME/.ssh/known_hosts"
  if ! ssh-keygen -F github.com -f "$HOME/.ssh/known_hosts" >/dev/null; then
    printf '\n' >> "$HOME/.ssh/known_hosts"
    cat "$ROOT/github_known_hosts" >> "$HOME/.ssh/known_hosts"
  fi
  STATUS=0
  RESULT=$(ssh -T -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes git@github.com 2>&1) || STATUS=$?
  printf '%s\n' "$RESULT"
  [[ $STATUS == 1 && $RESULT == *'successfully authenticated'* ]] || die 'GitHub SSH test failed. Check key registration, SSH config, and ssh-agent for passphrase-protected keys.'
fi
note 'Connections verified. Codex/Claude login is intentionally deferred.'
