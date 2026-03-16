# ArgusReach — Data Layer

Single SQLite source of truth for all prospect, campaign, event, meeting, and revenue data.

## Why This Exists

Instantly tracks opens/clicks/sends. We track everything downstream:
- Reply intent (interested / not_now / ooo / unsubscribe)
- Draft approval state
- Meetings booked (via Calendly)
- Revenue (via Stripe)
- Full prospect journey from first email → closed deal

## Files

| File | Purpose |
|------|---------|
| `database.py` | Schema, connection helpers, write functions |
| `instantly_sync.py` | Pull campaign stats from Instantly API → DB |
| `generate_dashboard.py` | Generate `dashboard.html` from DB |
| `argusreach.db` | SQLite database (gitignored) |
| `dashboard.html` | Generated ops dashboard (gitignored) |

## Setup

### 1. Install dependencies
```bash
pip install flask stripe requests python-dotenv
```

### 2. Initialize DB
```bash
cd argusreach/db
python3 database.py
```

### 3. Add env vars to `monitor/.env`
```
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SECRET_KEY=sk_live_...
```

## Usage

### Sync Instantly campaign stats
```bash
python3 argusreach/db/instantly_sync.py
```

### Generate dashboard
```bash
python3 argusreach/db/generate_dashboard.py
# Opens argusreach/db/dashboard.html
```

### Start webhook server
```bash
python3 argusreach/webhooks/server.py
# Runs on port 5055
```

### Install as systemd service
```bash
sudo cp argusreach/webhooks/argusreach-webhooks.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now argusreach-webhooks
sudo systemctl status argusreach-webhooks
```

## Stripe Webhook Setup
1. Go to Stripe Dashboard → Webhooks → Add endpoint
2. URL: `https://yourdomain.com/webhooks/stripe`
3. Event: `checkout.session.completed`
4. Copy signing secret → add as `STRIPE_WEBHOOK_SECRET` in `monitor/.env`

## Calendly Webhook Setup
1. Go to Calendly → Integrations → Webhooks
2. URL: `https://yourdomain.com/webhooks/calendly`
3. Events: `invitee.created`, `invitee.canceled`

## DB Schema

- **clients** — one row per client (synced from clients.json)
- **campaigns** — Instantly campaign stats (updated by sync)
- **prospects** — every contact, with current stage
- **events** — immutable log of every touchpoint
- **meetings** — Calendly bookings
- **revenue** — Stripe payments

## Prospect Stages
`added → emailed → opened → replied → replied_by_us → meeting_booked → closed_won / closed_lost / unsubscribed`
