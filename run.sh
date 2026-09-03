#!/usr/bin/env bash
# Start the TradingAgents UI, creating a virtualenv on first run.
set -euo pipefail

cd "$(dirname "$0")"
VENV="${VENV:-.venv}"

if [ ! -d "$VENV" ]; then
  echo "Creating virtualenv in $VENV ..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

exec "$VENV/bin/streamlit" run Home.py "$@"
