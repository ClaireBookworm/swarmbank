#!/usr/bin/env bash
# Shared functions only; sourcing this file performs no installation.
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
note() { printf '\n%s\n' "$*"; }

require_ubuntu_user() {
  [[ $(uname -s) == Linux && -r /etc/os-release ]] || die 'Run on the new Ubuntu machine, not your controller/Mac.'
  [[ $EUID != 0 ]] || die 'Run as your normal user, without sudo. Individual system steps ask for sudo.'
  # os-release defines NAME too; setup.sh uses its own NAME for the hostname.
  # shellcheck disable=SC2034
  local ID VERSION_ID NAME
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ $ID == ubuntu ]] || die 'Supported systems: Ubuntu 24.04 or 26.04 LTS.'
  case "$VERSION_ID" in 24.04|26.04) ;; *) die 'Supported systems: Ubuntu 24.04 or 26.04 LTS.';; esac
  case "$(uname -m)" in x86_64|aarch64) ;; *) die 'Supported architectures: x86_64 and aarch64.';; esac
  [[ -d /run/systemd/system ]] || die 'A booted Ubuntu installation with systemd is required (not a container/chroot).'
  if [[ -d /cdrom/casper || -d /run/casper ]]; then die 'Install Ubuntu to the internal drive first; do not run in the live USB session.'; fi
}

load_tools() {
  export PATH="$HOME/.local/opt/swarmbank-node/bin:$HOME/.local/bin:$PATH"
  export NPM_CONFIG_PREFIX="$HOME/.local"
}

download() {
  # Bound connection/transfer time; never execute a partially downloaded script.
  local url=$1 destination=$2
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --show-error --location \
    --connect-timeout 20 --max-time 900 --retry 3 "$url" -o "$destination.part"
  mv -- "$destination.part" "$destination"
}

valid_name() { [[ ${#1} -le 63 && $1 =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; }
