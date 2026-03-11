#!/bin/bash
# ArgusReach Monitor — manual launcher (use this until systemd service is installed)
# To install as a system service (run once):
#   sudo cp argusreach-monitor.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable argusreach-monitor
#   sudo systemctl start argusreach-monitor

set -a
source "$(dirname "$0")/.env"
set +a

mkdir -p "$(dirname "$0")/logs"
exec python3 "$(dirname "$0")/monitor.py" "$@"
