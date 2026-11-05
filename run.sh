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
if ! .venv/bin/python -c 'import mediapipe, zmq' >/dev/null 2>&1; then
  if ! command -v uv >/dev/null 2>&1; then
    echo 'Install dependencies from requirements-camera.txt into .venv' >&2
    exit 1
  fi
  uv pip install --python .venv/bin/python -r requirements-camera.txt
fi
exec .venv/bin/python webcorder.py
