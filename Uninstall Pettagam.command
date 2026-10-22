#!/bin/bash
# ---------------------------------------------------------------------------
#  Pettagam: remove it from this Mac (or Linux computer).
#
#  Removes the Pettagam app, the background inbox sorting, and Pettagam's
#  .venv. You choose whether to keep your settings and catalog (~/.pettagam).
#
#  Your files are never touched: not the old files, not the library, not the
#  inbox. They stay exactly where they are. To also remove the library itself,
#  delete its folder in Finder once you're sure you don't need it.
# ---------------------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
APPS_DIR="${PETTAGAM_APPS_DIR:-$HOME/Applications}"
ASK=1
[ "${PETTAGAM_NONINTERACTIVE:-0}" = 1 ] && ASK=0
# PETTAGAM_REMOVE_SETTINGS=1 also removes ~/.pettagam without asking.
REMOVE_SETTINGS="${PETTAGAM_REMOVE_SETTINGS:-0}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
note() { printf '    \033[2m%s\033[0m\n' "$1"; }

ask() {
    if [ "$ASK" = 0 ]; then printf '%s' "$2"; return; fi  # without a person: the safe choice
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display dialog \"$1\" buttons {\"$2\", \"$3\"} default button \"$2\" with title \"Pettagam\" with icon caution" \
                  -e 'button returned of result' 2>/dev/null
    else
        printf '%s [%s/%s] ' "$1" "$2" "$3" >&2
        read -r reply
        printf '%s' "${reply:-$2}"
    fi
}

printf '\n  \033[1;36mPettagam\033[0m: uninstalling\n\n'
if [ "$ASK" = 1 ] && [ "$(ask "Remove Pettagam from this computer?\n\nYour old files, the library and the inbox are not touched." "Cancel" "Remove")" != "Remove" ]; then
    note "Cancelled. Nothing was changed."
    exit 0
fi

. "$HERE/launchers/find-python.sh"
PYTHON="$(pettagam_python "$HERE")" || PYTHON=""
if [ -n "$PYTHON" ] && PYTHONPATH="$HERE" "$PYTHON" -m pettagam service off >/dev/null 2>&1; then
    ok "Background inbox sorting stopped"
fi
for f in "$HOME/Library/LaunchAgents/local.pettagam.inbox.plist" \
         "$HOME/Library/LaunchAgents/local.pettagam.fixity.plist" \
         "$HOME/.config/systemd/user/pettagam-inbox.service" \
         "$HOME/.config/systemd/user/pettagam-inbox.timer" \
         "$HOME/.config/systemd/user/pettagam-inbox.path" \
         "$HOME/.config/systemd/user/pettagam-fixity.service" \
         "$HOME/.config/systemd/user/pettagam-fixity.timer"; do
    [ -e "$f" ] && rm -f "$f"
done

[ -d "$APPS_DIR/Pettagam.app" ] && rm -rf "$APPS_DIR/Pettagam.app" && ok "Removed the Pettagam app"
[ -e "$HOME/.local/bin/pettagam" ] && grep -q "pettagam" "$HOME/.local/bin/pettagam" 2>/dev/null \
    && rm -f "$HOME/.local/bin/pettagam" && ok "Removed the pettagam command"
[ -e "$HOME/.local/share/applications/pettagam.desktop" ] && rm -f "$HOME/.local/share/applications/pettagam.desktop"
[ -d "$HERE/.venv" ] && rm -rf "$HERE/.venv" && ok "Removed Pettagam's Python environment (.venv)"

if [ -d "$HOME/.pettagam" ]; then
    if [ "$REMOVE_SETTINGS" = 1 ] || [ "$(ask "Also remove your Pettagam settings and catalog (~/.pettagam)?\n\nKeep them if you might use Pettagam again. A copy of the catalog is also kept in the library's _meta folder." "Keep" "Remove")" = "Remove" ]; then
        rm -rf "$HOME/.pettagam"
        ok "Removed settings and catalog"
    else
        note "Kept your settings and catalog in ~/.pettagam"
    fi
fi

printf '\n  \033[1;32mDone.\033[0m Your files were not touched. You can now delete this Pettagam folder.\n\n'
if [ "$ASK" = 1 ]; then
    printf '  Press Return to close this window.\n'
    read -r _ || true
fi
