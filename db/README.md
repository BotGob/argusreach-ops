# ArgusReach — Data Layer

Single SQLite source of truth for all prospect, campaign, event, meeting, and revenue data.

## What's in the DB

| Table | Purpose |
|-------|---------|
| `clients` | One row per client (synced from clients.json) |
| `campaigns` | Campaign stats synced from Instantly API |
| `prospects` | Every prospect with current stage |
| `events` | Immutable event log — every touchpoint |
| `meetings` | Calendly bookings |
| `revenue` | Stripe payments |

## Prospect Stages

`added → emailed → opened → replied → replied_by_us → meeting_booked → closed_won / closed_lost / unsubscribed`

## Files

| File | Purpose |
|------|---------|
| `database.py` | Schema, helpers, DB connection |
| `instantly_sync.py` | Pull campaign stats from Instantly API |
| `generate_dashboard.py` | Build `dashboard.html` from DB |
| `dashboard.html` | Generated ops dashboard (open in browser) |
| `argusreach.db` | SQLite database (gitignored) |

## Setup

### 1. Install deps
```bash
pip install flask stripe python-dotenv requests
```

### 2. Add to monitor/.env
```
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SECRET_KEY=sk_live_...
```

Get `STRIPE_WEBHOOK_SECRET` from Stripe Dashboard → Developers → Webhooks → your endpoint → Signing secret.

### 3. Initialize DB
```bash
cd argusreach/db
python3 database.py
```

## Running the Webhook Server

### Manual
```bash
cd argusreach/webhooks
python3 server.py
```

### As a systemd service
```bash
sudo cp argusreach-webhooks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable argusreach-webhooks
sudo systemctl start argusreach-webhooks
sudo systemctl status argusreach-webhooks
```

### Expose to internet (for Stripe/Calendly)
If behind NAT, use a tunnel or configure nginx. Stripe and Calendly need a public HTTPS URL.
Webhook URLs to register:
- **Stripe:** `https://yourdomain.com/webhooks/stripe`
- **Calendly:** `https://yourdomain.com/webhooks/calendly`

## Syncing Instantly Stats

```bash
cd argusreach/db
python3 instantly_sync.py
```

Add to crontab for hourly sync:
```
0 * * * * cd /home/argus/.openclaw/workspace/argusreach/db && python3 instantly_sync.py >> /tmp/instantly_sync.log 2>&1
```

## Generating the Dashboard

```bash
cd argusreach/db
python3 generate_dashboard.py
# Opens: argusreach/db/dashboard.html
```

Add to crontab for hourly refresh:
```
15 * * * * cd /home/argus/.openclaw/workspace/argusreach/db && python3 generate_dashboard.py >> /tmp/dashboard.log 2>&1
```

## Environment Variables

All loaded from `argusreach/monitor/.env`:

| Variable | Required | Purpose |
|----------|----------|---------|
| `INSTANTLY_API_KEY` | Yes | Instantly API sync |
| `STRIPE_WEBHOOK_SECRET` | Yes (webhooks) | Verify Stripe payloads |
| `STRIPE_SECRET_KEY` | Yes (webhooks) | Stripe SDK |
| `ARGUSREACH_BOT_TOKEN` | Yes | Telegram notifications |
| `ARGUSREACH_CHAT_ID` | Yes | Telegram chat target |
