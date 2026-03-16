#!/bin/bash
# monitor-healthcheck.sh
# Run every 30 min via systemd timer. Alerts Telegram if monitor heartbeat is stale.

HEARTBEAT="/home/argus/.openclaw/workspace/argusreach/monitor/logs/monitor_heartbeat.txt"
MAX_AGE_MINUTES=35
ENV_FILE="/home/argus/.openclaw/workspace/argusreach/monitor/.env"

# Load env vars
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

if [ ! -f "$HEARTBEAT" ]; then
    echo "No heartbeat file — monitor may not have started yet"
    exit 0
fi

LAST_BEAT=$(cat "$HEARTBEAT")
NOW=$(date -u +%s)
BEAT_TS=$(date -u -d "$LAST_BEAT" +%s 2>/dev/null || python3 -c "from datetime import datetime; print(int(datetime.fromisoformat('$LAST_BEAT').timestamp()))")
AGE_SECONDS=$(( NOW - BEAT_TS ))
AGE_MINUTES=$(( AGE_SECONDS / 60 ))

if [ "$AGE_MINUTES" -gt "$MAX_AGE_MINUTES" ]; then
    MSG="⚠️ *ArgusReach Monitor Alert*%0AMonitor has been silent for ${AGE_MINUTES} minutes.%0ALast heartbeat: ${LAST_BEAT}%0ACheck server: \`sudo systemctl status argusreach-monitor\`"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}&text=${MSG}&parse_mode=Markdown" > /dev/null
    echo "⚠️ Alert sent — monitor stale for ${AGE_MINUTES} min"
else
    echo "✅ Monitor alive — last beat ${AGE_MINUTES} min ago"
fi
