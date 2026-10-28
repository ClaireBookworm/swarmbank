#!/usr/bin/env bash
# Install or update quo-swarm on macOS (Seatbelt sandbox, tmux service). Never enables launching.
set -euo pipefail
umask 077
SRC="$(cd "$(dirname "$0")" && pwd)"
R="$HOME/.local/share/quo-swarm"
T="$R/tools"
PLATFORM="${SCI_SOURCE:-git+https://github.com/azaho/sci}"
mkdir -p "$R/app" "$T" "$HOME/.local/bin"
rm -rf "$R/app/quo_swarm.new"
cp -r "$SRC/quo_swarm" "$R/app/quo_swarm.new"
rm -rf "$R/app/quo_swarm"; mv "$R/app/quo_swarm.new" "$R/app/quo_swarm"
git -C "$SRC" rev-parse HEAD > "$R/app/COMMIT" 2>/dev/null || true

# Agent tools (read-only inside the sandbox): uv, Python 3.12, and a venv with sci + a science stack.
if [ ! -x "$T/sci-venv/bin/sci" ]; then
  [ -x "$T/uv" ] || { /usr/bin/python3 -m venv "$T/uv-bootstrap" && "$T/uv-bootstrap/bin/pip" install -q uv \
                      && cp "$T/uv-bootstrap/bin/uv" "$T/uv"; }
  UV_PYTHON_INSTALL_DIR="$T/python" "$T/uv" python install 3.12
  UV_PYTHON_INSTALL_DIR="$T/python" "$T/uv" venv -q --python 3.12 "$T/sci-venv"
  "$T/uv" pip install -q --python "$T/sci-venv/bin/python" "$PLATFORM" numpy scipy pandas pyarrow \
    matplotlib networkx statsmodels scikit-learn requests h5py tqdm seaborn
fi

# Client for the shared download service (runs inside the sandbox; stdlib only).
{ echo "#!$T/sci-venv/bin/python"; tail -n +2 "$SRC/tools/fetch"; } > "$T/fetch"; chmod 755 "$T/fetch"

cat > "$HOME/.local/bin/quo-swarm" <<WRAP
#!/bin/sh
PYTHONPATH="$R/app" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 exec "$T/sci-venv/bin/python" -m quo_swarm "\$@"
WRAP
chmod 700 "$HOME/.local/bin/quo-swarm"

# Service: tmux session "quo-swarm" with a restart loop (no GUI launchd domain over SSH, no root).
cat > "$HOME/.local/bin/quo-swarm-service" <<'SVC'
#!/bin/sh
# usage: quo-swarm-service start|stop|restart|status|log
R="$HOME/.local/share/quo-swarm"; S=quo-swarm
case "$1" in
  start)   tmux has-session -t $S 2>/dev/null && { echo "already running"; exit 0; }
           tmux new-session -d -s $S "while true; do $HOME/.local/bin/quo-swarm serve >> $R/state/serve.log 2>&1; \
             echo \"\$(date) serve exited \$?; restarting in 10 s\" >> $R/state/serve.log; sleep 10; done"
           echo "started (tmux session $S)";;
  stop)    tmux send-keys -t $S C-c 2>/dev/null; sleep 35; tmux kill-session -t $S 2>/dev/null; echo stopped;;
  restart) "$0" stop; "$0" start;;
  status)  tmux has-session -t $S 2>/dev/null && echo running || echo stopped;;
  log)     tail -f "$R/state/serve.log";;
  *)       echo "usage: $0 start|stop|restart|status|log"; exit 2;;
esac
SVC
chmod 700 "$HOME/.local/bin/quo-swarm-service"
PYTHONPATH="$R/app" "$T/sci-venv/bin/python" -m quo_swarm init
echo "installed; launch_enabled=$("$T/sci-venv/bin/python" -c "import json;print(json.load(open('$R/config.json'))['launch_enabled'])")"
