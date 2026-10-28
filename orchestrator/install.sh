#!/usr/bin/env bash
# Install or update quo-swarm from this checkout. Never enables launching.
set -euo pipefail
umask 077
SRC="$(cd "$(dirname "$0")" && pwd)"
R="$HOME/.local/share/quo-swarm"
# sci CLI source: a local SCI checkout if present, otherwise the public repository.
PLATFORM="${SCI_SOURCE:-$HOME/.local/share/sci-org/platform}"
[ -d "$PLATFORM" ] || PLATFORM="git+https://github.com/azaho/sci"
mkdir -p "$R/app" "$R/tools"
rm -rf "$R/app/quo_swarm.new"
cp -r "$SRC/quo_swarm" "$R/app/quo_swarm.new"
rm -rf "$R/app/quo_swarm"; mv "$R/app/quo_swarm.new" "$R/app/quo_swarm"
git -C "$SRC" rev-parse HEAD > "$R/app/COMMIT" 2>/dev/null || true

# sci CLI for agents, mounted read-only at /opt/sci inside the sandbox.
if [ ! -x "$R/tools/sci-venv/bin/sci" ]; then
  /usr/bin/python3 -m venv "$R/tools/sci-venv"
  "$R/tools/sci-venv/bin/pip" install -q "$PLATFORM"
  for f in "$R/tools/sci-venv/bin/"*; do
    [ -f "$f" ] && head -c 2 "$f" | grep -q '#!' && sed -i "1s|^#!.*/sci-venv/bin/python.*|#!/opt/sci/bin/python|" "$f"
  done
fi

install -m 600 "$SRC/systemd/quo-swarm.service" "$HOME/.config/systemd/user/quo-swarm.service"
install -m 600 "$SRC/systemd/quo-agents.slice" "$HOME/.config/systemd/user/quo-agents.slice"
cat > "$HOME/.local/bin/quo-swarm" <<WRAP
#!/bin/sh
PYTHONPATH="$R/app" exec /usr/bin/python3 -m quo_swarm "\$@"
WRAP
chmod 700 "$HOME/.local/bin/quo-swarm"
PYTHONPATH="$R/app" /usr/bin/python3 -m quo_swarm init
systemctl --user daemon-reload
echo "installed; launch_enabled=$(python3 -c "import json;print(json.load(open('$R/config.json'))['launch_enabled'])")"
