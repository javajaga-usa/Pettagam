#!/bin/bash
# Opens the Pettagam panel in your browser. Close this window to stop it.
# Run "Setup Pettagam.command" once to get a Pettagam app in Applications instead.
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m pettagam panel "$@"
fi
exec python3 -m pettagam panel "$@"
