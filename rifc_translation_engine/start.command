#!/bin/bash
# RIFC Player Translation Engine: double click to start.
# Your data lives in "~/RIFC Translation Engine", outside this folder, so a new version never replaces it.
# On each start the launcher checks that folder, backs it up and carries your work forward when the version changes.
APP="$(cd "$(dirname "$0")" && pwd)"
DATA_HOME="$HOME/RIFC Translation Engine"
VENV="$DATA_HOME/.venv"
cd "$APP" || exit 1
echo "RIFC Player Translation Engine"
echo

stop() { echo; echo "$1"; read -r -p "Press Return to close." _; exit 1; }

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -z "$PY" ] && stop "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/macos/ then double click this file again."
echo "Using $($PY --version)"
mkdir -p "$DATA_HOME" || stop "Could not create $DATA_HOME"

# Python packages: installed once, and again only when requirements.txt changes.
REQ_HASH="$(shasum -a 256 requirements.txt | cut -d' ' -f1)"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$VENV/.requirements_hash" 2>/dev/null)" != "$REQ_HASH" ]; then
  if [ ! -x "$VENV/bin/python" ]; then
    echo "Setting up (a few minutes, once)."
    "$PY" -m venv "$VENV" || stop "Could not create the Python environment."
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
  else
    echo "Updating Python packages."
  fi
  "$VENV/bin/python" -m pip install -r requirements.txt --quiet || stop "Install failed. Check your internet connection and try again."
  echo "$REQ_HASH" > "$VENV/.requirements_hash"
fi

# Data: find, back up and upgrade. Stops without changing anything if a check fails.
"$VENV/bin/python" -m engine.upgrade --bundle "$APP" --home "$DATA_HOME"
case $? in
  0) ;;
  2) stop "This app folder is older than your data. Open the newest app folder instead." ;;
  *) stop "The data upgrade did not finish, so your data was left exactly as it was. Send the text above to support." ;;
esac

# The recruitment monitor does not install anything here and starts nothing in the background.
# Run a scan from the Recruitment Inbox page in the app. A daily schedule is optional and separate (see README_MONITOR.md).
export RIFC_TE_HOME="$DATA_HOME"
mkdir -p "$DATA_HOME/logs"

echo
echo "Your data: $DATA_HOME/data"
echo "Starting. Your browser opens at http://localhost:8501"
echo "Keep this window open while you use the app. Close it (or press Ctrl+C) to stop."
echo
"$VENV/bin/python" -m streamlit run app.py --browser.gatherUsageStats false
