#!/bin/bash
# Double-click in Finder to open the Pettagam panel in your browser.
# First time: right-click > Open, because macOS blocks unsigned scripts.
cd "$(dirname "$0")/.." || exit 1
if command -v pettagam >/dev/null 2>&1; then
  exec pettagam panel
fi
exec python3 -m pettagam panel
