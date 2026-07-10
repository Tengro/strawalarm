#!/bin/sh
# Local install without pip: wrapper scripts into ~/.local/bin, desktop
# entry + icon into ~/.local/share. Run from the repo checkout.
set -e

REPO=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$BIN" "$APPS" "$ICONS"

for pair in strawalarm:cli strawalarm-gui:gui strawalarmd:daemon; do
    name=${pair%%:*}; mod=${pair##*:}
    cat > "$BIN/$name" <<EOF
#!/bin/sh
export PYTHONPATH="$REPO/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m strawalarm.$mod "\$@"
EOF
    chmod 755 "$BIN/$name"
done

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cp "$REPO/data/strawalarmd.service" "$UNITS/strawalarmd.service"
systemctl --user daemon-reload 2>/dev/null || true

sed "s|^Exec=.*|Exec=$BIN/strawalarm-gui|" \
    "$REPO/data/strawalarm.desktop" > "$APPS/strawalarm.desktop"
cp "$REPO/src/strawalarm/data/strawalarm.svg" "$ICONS/strawalarm.svg"

update-desktop-database -q "$APPS" 2>/dev/null || true

echo "Installed: $BIN/strawalarm, $BIN/strawalarm-gui, $BIN/strawalarmd"
echo "Desktop entry: $APPS/strawalarm.desktop (look for 'Straw Alarm' in your launcher)"
echo "Optional background daemon (alarms survive closing the GUI):"
echo "  systemctl --user enable --now strawalarmd.service"
