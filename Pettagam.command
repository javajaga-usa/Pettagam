#!/bin/bash
# Double-click in Finder to open the Pettagam panel.
cd "$(dirname "$0")" || exit 1
exec python3 -m pettagam.gui
