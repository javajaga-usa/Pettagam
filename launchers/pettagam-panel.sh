#!/bin/sh
# Start the Pettagam panel on Ubuntu (or any Linux).
# On a server without a screen, open it from another computer instead:
#   ./pettagam-panel.sh --host 0.0.0.0 --no-browser   then browse to the link it prints
cd "$(dirname "$0")/.." || exit 1
. launchers/find-python.sh
PY="$(pettagam_python "$PWD")" || { echo "Pettagam needs Python $PETTAGAM_MIN_PYTHON or newer; run launchers/setup-ubuntu.sh"; exit 1; }
exec "$PY" -m pettagam panel "$@"
