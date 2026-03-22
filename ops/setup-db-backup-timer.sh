#!/bin/bash
# ArgusReach — Install SQLite DB backup timer
# Backs up argusreach.db daily to /home/argus/db-backups/, keeps 30 days

set -e

mkdir -p /home/argus/db-backups

cat > /etc/systemd/system/argusreach-db-backup.service << 'EOF'
[Unit]
Description=ArgusReach — SQLite DB Backup

[Service]
Type=oneshot
User=argus
ExecStart=/bin/bash -c 'sqlite3 /home/argus/.openclaw/workspace/argusreach/db/argusreach.db ".backup /home/argus/db-backups/argusreach-$(date +%Y%m%d).db" && find /home/argus/db-backups -name "*.db" -mtime +30 -delete'
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/argusreach-db-backup.timer << 'EOF'
[Unit]
Description=ArgusReach — DB Backup (daily 2am ET / 6am UTC)

[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Install logrotate config
cp /home/argus/.openclaw/workspace/argusreach/ops/argusreach-logrotate.conf /etc/logrotate.d/argusreach

systemctl daemon-reload
systemctl enable --now argusreach-db-backup.timer

echo "✅ DB backup timer installed — runs daily at 2am ET, keeps 30 days"
echo "✅ Logrotate config installed for monitor.log"
