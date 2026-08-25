#!/usr/bin/env bash
#
# Install the Wake systemd user timers, with paths resolved for THIS checkout.
#
# The unit files in deploy/systemd/ are templates: they contain @WAKE_ROOT@ and
# @WAKE_PYTHON@ placeholders rather than absolute paths, so the same files work
# on any machine. This script substitutes the real values and installs the
# result into ~/.config/systemd/user/.
#
# Re-running is safe: it overwrites the installed units and reloads systemd.
#
# Usage:
#   ./deploy/install-timers.sh            # install and enable
#   ./deploy/install-timers.sh --no-enable # install only, enable later yourself
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_PYTHON="$REPO_ROOT/venv/bin/python"
UNIT_DIR="$HOME/.config/systemd/user"
ENABLE=1
[ "${1:-}" = "--no-enable" ] && ENABLE=0

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: no virtualenv python at $VENV_PYTHON" >&2
    echo "Create it first:  python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# Fail early rather than installing timers that will fail at 17:30 with a
# ModuleNotFoundError nobody is watching for.
if [ ! -f "$REPO_ROOT/libraries/db/pwd.py" ]; then
    echo "ERROR: libraries/db/pwd.py is missing (MySQL credentials)." >&2
    echo "  cp libraries/db/pwd.py.example libraries/db/pwd.py   # then edit it" >&2
    exit 1
fi

echo "Repo:   $REPO_ROOT"
echo "Python: $VENV_PYTHON"
mkdir -p "$UNIT_DIR"

for template in "$REPO_ROOT"/deploy/systemd/*.service "$REPO_ROOT"/deploy/systemd/*.timer; do
    name="$(basename "$template")"
    sed -e "s|@WAKE_ROOT@|$REPO_ROOT|g" \
        -e "s|@WAKE_PYTHON@|$VENV_PYTHON|g" \
        "$template" > "$UNIT_DIR/$name"
    echo "  installed $name"
done

systemctl --user daemon-reload

# Catch an invalid unit here rather than discovering at 17:30 that a timer
# loaded as 'bad-setting' and silently never fired.
for unit in wake-daily-update.service wake-price-snapshot.service; do
    systemd-analyze --user verify "$UNIT_DIR/$unit" || {
        echo "ERROR: $unit failed verification" >&2; exit 1; }
done

if [ "$ENABLE" = "1" ]; then
    systemctl --user enable --now wake-daily-update.timer wake-price-snapshot.timer
    echo
    systemctl --user list-timers 'wake-*' --all --no-pager
    echo
    if [ "$(loginctl show-user "$USER" --property=Linger --value)" != "yes" ]; then
        echo "NOTE: linger is off, so these timers stop when you log out."
        echo "      Run:  sudo loginctl enable-linger $USER"
    fi
else
    echo "Installed but not enabled. Enable with:"
    echo "  systemctl --user enable --now wake-daily-update.timer wake-price-snapshot.timer"
fi
