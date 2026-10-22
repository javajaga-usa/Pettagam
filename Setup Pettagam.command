#!/bin/bash
# ---------------------------------------------------------------------------
#  Pettagam: one-time setup for macOS.
#
#  Run this once (double-click it in Finder). It puts a Pettagam app in your
#  Applications folder, so after this Pettagam opens like any other app from
#  Launchpad or Spotlight.
#
#  Why a setup script rather than a ready-made app: anything downloaded is
#  marked "quarantined", and recent macOS will not open a quarantined app that
#  isn't signed by a registered developer. An app *built on this Mac* carries
#  no such mark, so this script builds the launcher here.
#
#  Nothing is installed system-wide. Pettagam needs only the Python that comes
#  with macOS. AI tagging, if you choose it, goes in a .venv folder beside this
#  file. To remove everything again, run "Uninstall Pettagam.command".
# ---------------------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
APPS_DIR="${PETTAGAM_APPS_DIR:-$HOME/Applications}"
# PETTAGAM_NONINTERACTIVE=1 skips every question (used by the tests).
ASK=1
[ "${PETTAGAM_NONINTERACTIVE:-0}" = 1 ] && ASK=0

step()  { printf '\n  \033[1;36m%s\033[0m %s\n' "$1" "$2"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail()  { printf '\n  \033[31m✗\033[0m %s\n\n' "$1"; }
note()  { printf '    \033[2m%s\033[0m\n' "$1"; }

finish() {
    [ "$ASK" = 1 ] || return 0
    printf '\n  Press Return to close this window.\n'
    read -r _ || true
}

# dialog "text" "button1" "button2" -> prints the button pressed (the default when not asking)
dialog() {
    if [ "$ASK" = 0 ] || ! command -v osascript >/dev/null 2>&1; then
        printf '%s' "$3"
        return
    fi
    osascript -e "display dialog \"$1\" buttons {\"$2\", \"$3\"} default button \"$3\" with title \"Pettagam\" with icon note" \
              -e 'button returned of result' 2>/dev/null
}

printf '\n  \033[1;36mPettagam\033[0m: setting up on %s\n' "$(sw_vers -productName 2>/dev/null || uname -s) $(sw_vers -productVersion 2>/dev/null)"

# --- 1. Quarantine ------------------------------------------------------------
step "1" "Clearing the download quarantine"
if command -v xattr >/dev/null 2>&1 && xattr -dr com.apple.quarantine "$HERE" 2>/dev/null; then
    ok "Done: macOS will treat these files as your own."
else
    note "Nothing to clear, which is fine."
fi

# --- 2. Python ----------------------------------------------------------------
step "2" "Looking for Python 3.9 or newer"
PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    warn "Python 3 is not installed."
    note "It comes with Apple's command line tools: one download, straight from Apple."
    if [ "$(dialog "Pettagam needs Python 3, which comes with Apple's command line tools.\n\nAsk macOS to install them now?" "Not now" "Install")" = "Install" ]; then
        xcode-select --install 2>/dev/null || true
        note "When that finishes, run Setup Pettagam again."
    else
        fail "Cancelled. Nothing was changed."
    fi
    finish
    exit 1
fi
ok "$("$PYTHON" --version 2>&1): $PYTHON"
if ! "$PYTHON" -c 'import sys; sys.path.insert(0, "'"$HERE"'"); import pettagam' 2>/dev/null; then
    fail "The pettagam folder is missing next to this file. Download Pettagam again."
    finish
    exit 1
fi

# --- 3. Optional: AI tagging -----------------------------------------------------
step "3" "AI tagging (optional)"
if [ -x "$HERE/.venv/bin/python" ] && "$HERE/.venv/bin/python" -c 'import anthropic' 2>/dev/null; then
    ok "Already set up."
elif [ "$(dialog "Set up AI tagging too?\n\nIt lets Claude add tags like beach or receipt to your photos and documents. It needs an internet connection now and an Anthropic API key later. You can skip it and run setup again any time." "Set up" "Skip")" = "Set up" ]; then
    if "$PYTHON" -m venv "$HERE/.venv" && "$HERE/.venv/bin/python" -m pip install --quiet --upgrade pip \
       && "$HERE/.venv/bin/python" -m pip install --quiet "anthropic>=1,<3"; then
        ok "AI tagging is ready. Add your API key in Terminal: export ANTHROPIC_API_KEY=..."
    else
        warn "Could not install it (no internet?). Everything else works; try again later."
    fi
else
    note "Skipped. Everything else works without it."
fi

# --- 4. The app -------------------------------------------------------------------
step "4" "Building the Pettagam app"
mkdir -p "$APPS_DIR"
APP="$APPS_DIR/Pettagam.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
VERSION="$("$PYTHON" -c 'import sys; sys.path.insert(0, "'"$HERE"'"); import pettagam; print(pettagam.__version__)' 2>/dev/null || echo 1.0)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Pettagam</string>
  <key>CFBundleDisplayName</key><string>Pettagam</string>
  <key>CFBundleIdentifier</key><string>local.pettagam.launcher</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>Pettagam</string>
  <key>CFBundleIconFile</key><string>Pettagam</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
# The panel runs in a Terminal window so you can see it and stop it.
cat > "$APP/Contents/MacOS/Pettagam" <<LAUNCHER
#!/bin/bash
if [ ! -d "$HERE/pettagam" ]; then
    osascript -e 'display dialog "The Pettagam folder has moved or was deleted. Run Setup Pettagam from its new place." buttons {"OK"} default button "OK" with title "Pettagam" with icon caution' >/dev/null 2>&1
    exit 1
fi
open -a Terminal "$HERE/launchers/Pettagam.command"
LAUNCHER
chmod +x "$APP/Contents/MacOS/Pettagam" "$HERE/launchers/Pettagam.command" "$HERE/Uninstall Pettagam.command" 2>/dev/null || true

ICON_WORK="$(mktemp -d)"
if "$PYTHON" "$HERE/launchers/make_icon.py" "$ICON_WORK/Pettagam.iconset" 2>/dev/null \
   && command -v iconutil >/dev/null 2>&1 \
   && iconutil -c icns "$ICON_WORK/Pettagam.iconset" -o "$APP/Contents/Resources/Pettagam.icns" 2>/dev/null; then
    ok "Icon drawn"
else
    note "No icon: harmless, the app uses the default one."
fi
rm -rf "$ICON_WORK"
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" >/dev/null 2>&1 || true
ok "Pettagam is in $APPS_DIR"

# --- 5. Done --------------------------------------------------------------------
printf '\n  \033[1;32mSet up.\033[0m\n\n'
note "Open Pettagam from Applications, Launchpad or Spotlight."
note "It opens the panel in your browser; close its Terminal window to stop it."
if [ "$(dialog "Pettagam is set up and is in your Applications folder.\n\nOpen it now?" "Later" "Open Pettagam")" = "Open Pettagam" ] && [ "$ASK" = 1 ]; then
    open "$APP"
    exit 0
fi
finish
