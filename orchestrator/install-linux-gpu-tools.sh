#!/usr/bin/env bash
# Run as the swarm user before install.sh. One shared environment, no model login.
set -euo pipefail
umask 077
SRC="$(cd "$(dirname "$0")" && pwd)"
R="$HOME/.local/share/quo-swarm"
T="$R/tools"
mkdir -p "$T"
if [ ! -x "$R/bootstrap/bin/uv" ]; then
  /usr/bin/python3 -m venv "$R/bootstrap"
  "$R/bootstrap/bin/pip" install uv
fi
UV="$R/bootstrap/bin/uv"
"$UV" python install 3.12
if [ ! -x "$T/sci-venv/bin/python" ]; then
  "$UV" venv --python 3.12 "$T/sci-venv"
fi
"$T/sci-venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3,12), "GPU tools require Python 3.12; use a fresh tools environment"'
"$UV" pip install --python "$T/sci-venv/bin/python" \
  "${SCI_SOURCE:-git+https://github.com/azaho/sci}" -r "$SRC/requirements-linux-gpu.txt"
echo 'GPU tools installed. Run install.sh, then the Linux sandbox checks.'
