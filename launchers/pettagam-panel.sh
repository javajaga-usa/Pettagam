#!/bin/sh
# Start the Pettagam panel on Ubuntu (or any Linux).
# On a server without a screen, open it from another computer instead:
#   ./pettagam-panel.sh --host 0.0.0.0 --no-browser   then browse to http://<server>:8484
cd "$(dirname "$0")/.." || exit 1
if command -v pettagam >/dev/null 2>&1; then
  exec pettagam panel "$@"
fi
exec python3 -m pettagam panel "$@"
