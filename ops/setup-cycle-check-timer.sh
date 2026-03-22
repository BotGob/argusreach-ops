#!/bin/bash
# ArgusReach — Install monthly_cycle campaign check timer
# Runs daily at 9 AM ET to detect campaigns >75% complete and alert Vito

set -e

cat > /etc/systemd/system/argusreach-cycle-check.service << 'EOF'
[Unit]
Description=ArgusReach — Campaign Completion Check
After=network.target

[Service]
Type=oneshot
User=argus
WorkingDirectory=/home/argus/.openclaw/workspace/argusreach/tools
EnvironmentFile=/home/argus/.openclaw/workspace/argusreach/monitor/.env
ExecStart=/usr/bin/python3 monthly_cycle.py --check-all
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/argusreach-cycle-check.timer << 'EOF'
[Unit]
Description=ArgusReach — Campaign Check (daily 9am ET)

[Timer]
OnCalendar=*-*-* 13:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now argusreach-cycle-check.timer

echo "✅ Campaign check timer installed — runs daily at 9am ET (13:00 UTC)"
systemctl list-timers | grep argusreach-cycle-check
