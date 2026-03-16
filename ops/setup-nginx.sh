#!/bin/bash
# ArgusReach — nginx + SSL setup
# Run as root: sudo bash setup-nginx.sh
set -e

ADMIN_DOMAIN="admin.argusreach.com"
HOOKS_DOMAIN="hooks.argusreach.com"
EMAIL="vito@argusreach.com"

echo "=== ArgusReach nginx + SSL Setup ==="

# 1. Install nginx + certbot
apt-get update -qq
apt-get install -y nginx certbot python3-certbot-nginx

# 2. Write nginx configs
cat > /etc/nginx/sites-available/argusreach-admin << EOF
server {
    listen 80;
    server_name ${ADMIN_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:5056;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        client_max_body_size 20M;
    }
}
EOF

cat > /etc/nginx/sites-available/argusreach-hooks << EOF
server {
    listen 80;
    server_name ${HOOKS_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:5055;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        client_max_body_size 10M;
    }
}
EOF

# 3. Enable sites
ln -sf /etc/nginx/sites-available/argusreach-admin /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/argusreach-hooks /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# 4. Test + start nginx
nginx -t
systemctl enable nginx
systemctl restart nginx

# 5. Get SSL certs
certbot --nginx -d ${ADMIN_DOMAIN} -d ${HOOKS_DOMAIN} --non-interactive --agree-tos -m ${EMAIL}

# 6. Install systemd services for admin + webhooks
cp /home/argus/.openclaw/workspace/argusreach/admin/argusreach-admin.service /etc/systemd/system/
cp /home/argus/.openclaw/workspace/argusreach/webhooks/argusreach-webhooks.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable argusreach-admin argusreach-webhooks
systemctl start argusreach-admin argusreach-webhooks

echo ""
echo "=== Done ==="
echo "Admin portal:    https://${ADMIN_DOMAIN}"
echo "Webhook server:  https://${HOOKS_DOMAIN}"
echo ""
echo "Register these in Stripe + Calendly:"
echo "  Stripe webhook URL:   https://${HOOKS_DOMAIN}/webhooks/stripe"
echo "  Calendly webhook URL: https://${HOOKS_DOMAIN}/webhooks/calendly"
