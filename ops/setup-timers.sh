#!/bin/bash
# Install systemd timers for Instantly sync + dashboard refresh
# Run as root: sudo bash ops/setup-timers.sh
set -e
TIMER_DIR=/etc/systemd/system
OPS=/home/argus/.openclaw/workspace/argusreach/ops

cp $OPS/argusreach-sync.service $TIMER_DIR/
cp $OPS/argusreach-sync.timer $TIMER_DIR/
cp $OPS/argusreach-dashboard.service $TIMER_DIR/
cp $OPS/argusreach-dashboard.timer $TIMER_DIR/

systemctl daemon-reload
systemctl enable argusreach-sync.timer argusreach-dashboard.timer
systemctl start argusreach-sync.timer argusreach-dashboard.timer
systemctl list-timers | grep argusreach
echo "✅ Timers installed and running"

# ── ADMIN AUTO-RESTART WATCHER ────────────────────────────────────────────────
echo "Installing admin watcher service..."

cp /home/argus/.openclaw/workspace/argusreach/ops/argusreach-watcher.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable argusreach-watcher
systemctl restart argusreach-watcher

# Grant argus user permission to restart argusreach-admin without password prompt
SUDOERS_LINE="argus ALL=(ALL) NOPASSWD: /bin/systemctl restart argusreach-admin"
SUDOERS_FILE="/etc/sudoers.d/argusreach-watcher"
if ! grep -qF "$SUDOERS_LINE" "$SUDOERS_FILE" 2>/dev/null; then
    echo "$SUDOERS_LINE" > "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    echo "✅ Sudoers rule added for argusreach-watcher"
else
    echo "✅ Sudoers rule already in place"
fi

systemctl status argusreach-watcher --no-pager | grep -E "Active|running"
echo "✅ Admin watcher installed — code changes now auto-restart the portal"
