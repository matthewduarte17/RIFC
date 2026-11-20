#!/bin/bash
# RIFC Player Translation Engine: optional page watching (changedetection.io) and club source check.
# Double click to run. It explains the cost first and asks before installing anything. Safe to run again.
APP="$(cd "$(dirname "$0")" && pwd)"
DATA_HOME="$HOME/RIFC Translation Engine"
PY="$DATA_HOME/.venv/bin/python"
cd "$APP" || exit 1
[ -x "$PY" ] || { echo "Start the app once first (double click start.command), then run this again."; read -r -p "Press Return to close." _; exit 1; }
export RIFC_TE_HOME="$DATA_HOME"
echo "Page watching for the recruitment monitor"
echo
"$PY" -m engine.monitor watch setup || { echo; echo "Nothing was installed."; read -r -p "Press Return to close." _; exit 1; }
echo
echo "Checking the club websites (reads each club's public home page, news and roster page; takes a few minutes)."
"$PY" -m engine.monitor directory verify
echo
echo "Done. Open the app > Admin > Monitor Status > Coverage by club to see what is covered and what is not."
echo "Page watching runs only during each scan; it is not a background service."
read -r -p "Press Return to close." _
