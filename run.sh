#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 1
  fi
  uv venv --python 3.13 .venv
fi
if ! .venv/bin/python -c 'import mediapipe, zmq, PIL' >/dev/null 2>&1; then
  if ! command -v uv >/dev/null 2>&1; then
    echo 'Install dependencies from requirements-web.txt into .venv' >&2
    exit 1
  fi
  uv pip install --python .venv/bin/python -r requirements-web.txt
fi
app_url='http://127.0.0.1:8765/'
healthcheck() {
  .venv/bin/python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('${app_url}api/health', timeout=1))['app'] == 'webcorder'" >/dev/null 2>&1
}
if healthcheck; then
  xdg-open "$app_url" >/dev/null 2>&1 || true
  exit 0
fi
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/webcorder"
mkdir -p "$state_dir"
setsid -f .venv/bin/python server.py >>"$state_dir/server.log" 2>&1 </dev/null
tries=0
until healthcheck; do
  tries=$((tries + 1))
  if [ "$tries" -ge 25 ]; then
    echo "Webcorder did not start. See $state_dir/server.log" >&2
    exit 1
  fi
  sleep 0.2
done
printf 'Webcorder is running at %s\n' "$app_url"
