#!/bin/sh
# Start the Pettagam panel on Ubuntu (or any Linux).
# On a server without a screen, open it from another computer instead:
#   ./pettagam-panel.sh --host 0.0.0.0 --no-browser   then browse to the link it prints
cd "$(dirname "$0")/.." || exit 1
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m pettagam panel "$@"
fi
exec python3 -m pettagam panel "$@"
