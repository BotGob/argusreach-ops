#!/bin/bash
# ArgusReach — Basic firewall setup (ufw)
# Allows only SSH, HTTP, HTTPS, and the two app ports

set -e

apt-get install -y ufw

# Default: deny all inbound, allow all outbound
ufw default deny incoming
ufw default allow outgoing

# SSH — must allow before enabling or you'll lock yourself out
ufw allow 22/tcp

# Web
ufw allow 80/tcp
ufw allow 443/tcp

# ArgusReach admin portal
ufw allow 5056/tcp

# ArgusReach webhook server
ufw allow 5055/tcp

# Enable (non-interactive)
ufw --force enable

echo "✅ Firewall enabled"
ufw status verbose
