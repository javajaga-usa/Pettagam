#!/bin/sh
# Pettagam: one-time setup for Ubuntu (desktop or server).
#
#   ./launchers/setup-ubuntu.sh            add the `pettagam` command (and a menu entry on a desktop)
#   ./launchers/setup-ubuntu.sh --ai       also set up AI tagging in a .venv (needs internet)
#
# Nothing is installed system-wide. Remove it again with "Uninstall Pettagam.command" (it works on Linux too).
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${PETTAGAM_BIN_DIR:-$HOME/.local/bin}"
PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Pettagam needs Python 3.9 or newer:  sudo apt install python3"
  exit 1
fi
echo "Using $("$PY" --version)"

if [ "${1:-}" = "--ai" ]; then
  "$PY" -m venv "$HERE/.venv" 2>/dev/null || { echo "Install venv support first:  sudo apt install python3-venv"; exit 1; }
  "$HERE/.venv/bin/python" -m pip install --quiet --upgrade pip && "$HERE/.venv/bin/python" -m pip install --quiet "anthropic>=1,<3" \
    && echo "AI tagging is ready. Set your key:  export ANTHROPIC_API_KEY=..." \
    || echo "Could not install AI tagging (no internet?). Everything else works."
fi

mkdir -p "$BIN"
cat > "$BIN/pettagam" <<WRAP
#!/bin/sh
# pettagam command, made by setup-ubuntu.sh
if [ -x "$HERE/.venv/bin/python" ]; then exec "$HERE/.venv/bin/python" -m pettagam "\$@"; fi
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
