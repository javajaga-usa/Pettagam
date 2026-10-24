#!/bin/bash
# ---------------------------------------------------------------------------
#  Pettagam: get the newest version from GitHub (double-click it in Finder).
#
#  It downloads only Pettagam's own files into this folder, and restarts the
#  panel if it is open. Your files, the library, your settings and the catalog
#  are not touched. The panel's "Update and restart" button does the same.
# ---------------------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
ASK=1
[ "${PETTAGAM_NONINTERACTIVE:-0}" = 1 ] && ASK=0

finish() {
    [ "$ASK" = 1 ] || return 0
    printf '\n  Press Return to close this window.\n'
    read -r _ || true
}

printf '\n  \033[1;36mPettagam\033[0m: looking for a new version\n\n'
. "$HERE/launchers/find-python.sh"
PYTHON="$(pettagam_python "$HERE")" || {
    printf '  Pettagam needs Python %s or newer. Run Setup Pettagam first.\n' "$PETTAGAM_MIN_PYTHON"
    finish
    exit 1
}
PYTHONPATH="$HERE" "$PYTHON" -m pettagam update
code=$?
finish
exit $code
