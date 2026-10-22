#!/bin/sh
# Pettagam: one-time setup for Ubuntu (desktop or server).
#
#   ./launchers/setup-ubuntu.sh            add the `pettagam` command (and a menu entry on a desktop)
#   ./launchers/setup-ubuntu.sh --ai       also set up AI tagging (needs internet)
#
# Nothing is installed system-wide: Pettagam runs from a .venv beside the code. Remove it again with "Uninstall Pettagam.command" (it works on Linux too).
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${PETTAGAM_BIN_DIR:-$HOME/.local/bin}"
. "$HERE/launchers/find-python.sh"
VENV="$HERE/.venv"
if ! pettagam_python_ok "$VENV/bin/python"; then
  BASE="$(pettagam_python)" || {
    echo "Pettagam needs Python $PETTAGAM_MIN_PYTHON or newer, and this computer doesn't have it."
    echo "Ubuntu 24.04 comes with 3.12. Install 3.13 next to it (the system Python is left alone):"
    echo "  sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.13 python3.13-venv"
    echo "then run this again."
    exit 1
  }
  rm -rf "$VENV"
  # Pettagam runs from its own .venv, so the command and the background job always use this Python.
  "$BASE" -m venv "$VENV" 2>/dev/null || {
    v="$("$BASE" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "Install venv support first:  sudo apt install python$v-venv"
    exit 1
  }
fi
PY="$VENV/bin/python"
echo "Using $("$PY" --version) in $VENV"
if [ -f "$HOME/.pettagam/service.json" ] && PYTHONPATH="$HERE" "$PY" -m pettagam service refresh >/dev/null 2>&1; then
  echo "Background inbox sorting now uses this Python too."
fi

if [ "${1:-}" = "--ai" ]; then
  "$PY" -m pip install --quiet --upgrade pip && "$PY" -m pip install --quiet "anthropic>=1,<3" \
    && echo "AI tagging is ready. Set your key:  export ANTHROPIC_API_KEY=..." \
    || echo "Could not install AI tagging (no internet?). Everything else works."
fi

mkdir -p "$BIN"
cat > "$BIN/pettagam" <<WRAP
#!/bin/sh
# pettagam command, made by setup-ubuntu.sh
PYTHONPATH="$HERE\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m pettagam "\$@"
WRAP
chmod +x "$BIN/pettagam"
echo "Added the pettagam command: $BIN/pettagam"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH yet; log out and in again, or run it as $BIN/pettagam";; esac

if [ -n "${XDG_CURRENT_DESKTOP:-}" ]; then
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  "$PY" "$HERE/launchers/make_icon.py" "$HOME/.pettagam/icon" >/dev/null 2>&1
  cat > "$APPS/pettagam.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Pettagam
Comment=Organize files into a smart library
Exec=$BIN/pettagam panel
Icon=$HOME/.pettagam/icon/icon_256x256.png
Terminal=true
Categories=Utility;
DESK
  echo "Added Pettagam to the applications menu"
fi
echo "Done. Try:  pettagam doctor"
