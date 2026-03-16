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
