#!/bin/sh
# One-time root install: applies CAP_WAKE_ALARM to PowerDevil now and
# keeps it applied across package updates and reboots via systemd units.
set -e
if [ "$(id -u)" != 0 ]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi
HERE=$(cd "$(dirname "$0")" && pwd)
install -m644 "$HERE/powerdevil-wakealarm-caps.service" \
              "$HERE/powerdevil-wakealarm-caps.path" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now powerdevil-wakealarm-caps.service
systemctl enable --now powerdevil-wakealarm-caps.path
echo "Capability now: $(getcap /usr/libexec/org_kde_powerdevil)"
echo "Done. Restart PowerDevil in your desktop session with:"
echo "  systemctl --user restart plasma-powerdevil.service"
