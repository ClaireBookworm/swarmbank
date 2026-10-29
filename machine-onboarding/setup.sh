#!/usr/bin/env bash
# Install tools and configure the host. Does not log in to any external account.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$ROOT/lib/common.sh"
trap 'printf "\nSetup stopped at line %s. Fix the error and rerun the same command.\n" "$LINENO" >&2' ERR

usage() {
  cat <<'HELP'
Usage: bash setup.sh [options]
  --name NAME             Unique hostname, e.g. swarm-01 (prompted if omitted)
  --git-name NAME          Git author name (prompted if omitted)
  --git-email EMAIL        Git author email, including a GitHub noreply address
  --authorized-key FILE    Controller's public .pub key for incoming SSH (optional)
  --unattended-key         Create the host's GitHub key without a passphrase
  --keep-sleep             Preserve sleep/lid settings
  --no-t3                  Skip T3 installation and its background service
  --help                  Show this help without changing anything
No Codex/Claude sign-in, model calls, swarm launch, or automatic reboot.
HELP
}
NAME='' GIT_NAME='' GIT_EMAIL='' AUTHORIZED_KEY='' KEEP_SLEEP=0 NO_T3=0 UNATTENDED_KEY=0
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0;;
    --name|--git-name|--git-email|--authorized-key)
      (($# >= 2)) && [[ -n $2 && $2 != --* ]] || die "Missing value for $1"
      case "$1" in
        --name) NAME=$2;; --git-name) GIT_NAME=$2;; --git-email) GIT_EMAIL=$2;; --authorized-key) AUTHORIZED_KEY=$2;;
      esac
      shift 2;;
    --keep-sleep) KEEP_SLEEP=1; shift;;
    --no-t3) NO_T3=1; shift;;
    --unattended-key) UNATTENDED_KEY=1; shift;;
    *) die "Unknown option: $1";;
  esac
done
require_ubuntu_user
[[ -t 0 ]] || die 'Run interactively from a terminal (or ssh -t); do not pipe this script into bash.'
if [[ -z $NAME ]]; then read -r -p 'Unique machine name (e.g. swarm-01): ' NAME; fi
valid_name "$NAME" || die 'Name must be 1–63 lowercase letters/digits/hyphens, with a letter or digit at each end.'
if [[ -z $GIT_NAME ]]; then read -r -p 'Git author name: ' GIT_NAME; fi
if [[ -z $GIT_EMAIL ]]; then read -r -p 'Git author email (or GitHub noreply email): ' GIT_EMAIL; fi
[[ -n $GIT_NAME && $GIT_NAME != *$'\n'* && $GIT_EMAIL == ?*@?* && $GIT_EMAIL != *[[:space:]]* ]] || die 'A name and valid-looking email are required.'
if [[ -n $AUTHORIZED_KEY ]]; then
  [[ -f $AUTHORIZED_KEY ]] || die 'Controller public-key file does not exist.'
  # Reject obvious private keys before invoking any system changes.
  grep -q 'PRIVATE KEY' "$AUTHORIZED_KEY" && die 'Use the .pub file, never a private key.'
fi

STATE="$HOME/.local/state/swarmbank-onboarding"
install -d -m 700 "$STATE"
mkdir -p "$HOME/.local/bin" "$HOME/.local/opt" "$HOME/projects"
# Do not tee or redirect stdin/stdout around apt: keep terminal job control intact.
sudo -v
note 'Installing Ubuntu tools, SSH, and security updates...'
sudo apt-get update
sudo apt-get install -y openssh-server ca-certificates curl gnupg git gh \
  build-essential pkg-config python3 python3-venv python3-pip pipx bubblewrap \
  jq ripgrep tmux htop unzip zip xz-utils rsync unattended-upgrades
sudo hostnamectl set-hostname "$NAME"
# Keep Ubuntu's loopback hostname mapping consistent without touching other hosts.
sudo python3 - "$NAME" <<'PY'
import sys
from pathlib import Path
p = Path('/etc/hosts')
lines = p.read_text().splitlines()
replacement = '127.0.1.1\t' + sys.argv[1]
found = False
for i, line in enumerate(lines):
    if line.split()[:1] == ['127.0.1.1']:
        lines[i] = replacement
        found = True
if not found:
    lines.append(replacement)
p.write_text('\n'.join(lines) + '\n')
PY
python3 "$ROOT/lib/configure.py" shell-path
load_tools
git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"
if [[ -n $AUTHORIZED_KEY ]]; then python3 "$ROOT/lib/configure.py" authorize-key "$AUTHORIZED_KEY"; fi
sudo /usr/sbin/sshd -t
sudo systemctl enable --now ssh
if command -v ufw >/dev/null && sudo env LC_ALL=C ufw status | grep -q '^Status: active'; then
  sudo ufw allow in on tailscale0 to any port 22 proto tcp comment 'swarmbank SSH'
fi

if ! command -v tailscale >/dev/null; then
  note 'Installing Tailscale...'
  download https://tailscale.com/install.sh "$STATE/tailscale-install.sh"
  sudo sh "$STATE/tailscale-install.sh"
fi
sudo systemctl enable --now tailscaled

NODE_MAJOR=0
if command -v node >/dev/null; then NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]'); fi
if ((NODE_MAJOR < 24)); then
  note 'Installing user-owned Node.js 24 LTS (verified SHA-256)...'
  ARCH=x64
  [[ $(uname -m) != aarch64 ]] || ARCH=arm64
  WORK=$(mktemp -d)
  trap 'rm -rf -- "$WORK"' EXIT
  download https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt "$WORK/SHASUMS256.txt"
  NODE_FILE=$(awk -v arch="$ARCH" '$2 ~ "^node-v24\\.[0-9]+\\.[0-9]+-linux-" arch "\\.tar\\.xz$" {print $2}' "$WORK/SHASUMS256.txt")
  [[ $NODE_FILE =~ ^node-v24\.[0-9]+\.[0-9]+-linux-(x64|arm64)\.tar\.xz$ ]] || die 'Unexpected Node release manifest.'
  NODE_VERSION=${NODE_FILE#node-}; NODE_VERSION=${NODE_VERSION%-linux-*}
  download "https://nodejs.org/dist/$NODE_VERSION/$NODE_FILE" "$WORK/$NODE_FILE"
  awk -v f="$NODE_FILE" '$2 == f' "$WORK/SHASUMS256.txt" > "$WORK/node.sha256"
  (cd "$WORK" && sha256sum -c node.sha256)
  NODE_DIR="$HOME/.local/opt/${NODE_FILE%.tar.xz}"
  # Extract fully before publishing the version directory; interrupted runs can retry.
  tar -xJf "$WORK/$NODE_FILE" -C "$WORK"
  if [[ -e $NODE_DIR ]]; then
    [[ -x $NODE_DIR/bin/node ]] || die "Incomplete existing Node directory: $NODE_DIR. Move it aside and rerun."
  else
    mv -- "$WORK/${NODE_FILE%.tar.xz}" "$NODE_DIR"
  fi
  ln -sfnT "$NODE_DIR" "$HOME/.local/opt/swarmbank-node"
  hash -r
fi
command -v npm >/dev/null || die 'Node exists but npm is missing; install npm for that Node installation and rerun.'
if ! command -v uv >/dev/null; then pipx install uv; fi
if ! command -v codex >/dev/null; then npm install -g @openai/codex@latest; fi
if ! command -v claude >/dev/null; then
  download https://claude.ai/install.sh "$STATE/claude-install.sh"
  bash "$STATE/claude-install.sh" stable
fi

KEY="$HOME/.ssh/id_ed25519_swarmbank_github"
install -d -m 700 "$HOME/.ssh"
if [[ ! -e $KEY && ! -e $KEY.pub ]]; then
  note 'Creating a GitHub SSH key for this machine. Never copy it to another machine.'
  if ((UNATTENDED_KEY)); then
    ssh-keygen -q -t ed25519 -N '' -C "$NAME-github" -f "$KEY"
  else
    ssh-keygen -t ed25519 -C "$NAME-github" -f "$KEY"
  fi
elif [[ ! -f $KEY || ! -f $KEY.pub ]]; then
  die "Incomplete key pair at $KEY. Recover it manually; setup will not replace a key."
fi
chmod 600 "$KEY"
ssh-keygen -lf "$KEY.pub"
python3 "$ROOT/lib/configure.py" github-config

if ((!NO_T3)); then
  if ! command -v t3 >/dev/null; then
    download https://t3.codes/install.sh "$STATE/t3-install.sh"
    sh "$STATE/t3-install.sh"
  fi
  # Browser setup is optional; its absence/failure must not strand core setup.
  if browser_setup_supported; then
    if ! sudo env "PATH=$PATH" "$(command -v t3)" browser setup; then
      echo 'T3 browser setup failed; core setup continues. See README troubleshooting.'
      touch "$STATE/browser-pending"
    else
      rm -f "$STATE/browser-pending"
    fi
  else
    echo 'This T3 version has no browser setup command; browser automation remains pending.'
    touch "$STATE/browser-pending"
  fi
  sudo loginctl enable-linger "$(id -un)"
  systemctl --user import-environment PATH
  # Avoid restarting an already enabled, healthy service when resuming setup.
  if ! systemctl --user is-active --quiet t3code.service || ! systemctl --user is-enabled --quiet t3code.service; then
    t3 service install
  fi
fi

if ((!KEEP_SLEEP)); then
  bash "$ROOT/power.sh" apply
fi
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' | sudo tee /etc/apt/apt.conf.d/80-swarmbank-periodic >/dev/null
printf 'Unattended-Upgrade::Automatic-Reboot "false";\n' | sudo tee /etc/apt/apt.conf.d/99-swarmbank-no-reboot >/dev/null
note 'Installed. Codex and Claude have not been signed in; no agents were started.'
for tool in node npm uv codex claude; do "$tool" --version; done
printf '\nNext: bash %q/connect.sh\nThen: bash %q/doctor.sh\n' "$ROOT" "$ROOT"
echo 'Use a new terminal for the updated PATH. Reboot only when ready to test unattended startup.'
