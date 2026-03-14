#!/bin/bash
# ArgusReach Monitor Watchdog
# Checks if monitor.py is running. If not, restarts it.
# Run via cron every 5 minutes.

MONITOR_SCRIPT="/home/argus/.openclaw/workspace/argusreach/monitor/monitor.py"
LOG="/home/argus/.openclaw/workspace/argusreach/monitor/logs/monitor.log"
ENV_FILE="/home/argus/.openclaw/workspace/argusreach/monitor/.env"

if ! pgrep -f "monitor.py" > /dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] Monitor not running — restarting..." >> "$LOG"

    # Load env vars
    export $(grep -v '#' "$ENV_FILE" | xargs)

    cd /home/argus/.openclaw/workspace/argusreach/monitor
    nohup python3 "$MONITOR_SCRIPT" >> "$LOG" 2>&1 &

    echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] Monitor restarted PID: $!" >> "$LOG"
else
    : # Running fine, stay quiet
fi
