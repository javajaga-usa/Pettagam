#!/bin/bash
# Opens the Pettagam panel in your browser. Close this window to stop it.
# Run "Setup Pettagam.command" once to get a Pettagam app in Applications instead.
cd "$(dirname "$0")/.." || exit 1
. launchers/find-python.sh
PY="$(pettagam_python "$PWD")" || {
    echo "Pettagam needs Python $PETTAGAM_MIN_PYTHON or newer. Install it from https://www.python.org/downloads/macos/"
    echo "and run Setup Pettagam again."
    read -r _
    exit 1
}
exec "$PY" -m pettagam panel "$@"
