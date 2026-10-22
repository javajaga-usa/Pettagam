# Finds a Python new enough for Pettagam (3.13 or newer). Sourced by the setup and launch scripts.
#   pettagam_python [DIR]   prints the interpreter to use, preferring DIR/.venv, or nothing.
# PETTAGAM_PYTHON names one to use instead (and is checked like the others).
PETTAGAM_MIN_PYTHON="3.13"

pettagam_python_ok() {
    [ -n "$1" ] && [ -x "$1" ] && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null
}

pettagam_python() {
    for p in "${PETTAGAM_PYTHON:-}" "${1:+$1/.venv/bin/python}"; do
        if pettagam_python_ok "$p"; then printf '%s\n' "$p"; return 0; fi
    done
    for name in python3.14 python3.13 python3; do
        p="$(command -v "$name" 2>/dev/null || true)"
        if pettagam_python_ok "$p"; then printf '%s\n' "$p"; return 0; fi
    done
    # Where the python.org installer and Homebrew put it, for when PATH doesn't include them (a double-clicked app).
    for p in /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
             /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
             /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3 \
             /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3; do
        if pettagam_python_ok "$p"; then printf '%s\n' "$p"; return 0; fi
    done
    return 1
}
