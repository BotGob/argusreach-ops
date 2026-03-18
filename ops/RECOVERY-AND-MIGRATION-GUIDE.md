# ArgusReach — Recovery & Migration Guide
*Written for Vito. No technical background required.*
*Last updated: 2026-03-17*

---

## The One Thing You Need to Know

**Everything important is on GitHub.** Even if the server burns down, nothing is permanently lost. The only things NOT on GitHub are API keys — keep those in a secure Google Drive note.

---

## Current System State (as of 2026-03-17)

### Server
- **IP:** 93.127.197.101
- **User:** argus (sudo available)
- **OS:** Ubuntu, standard VPS

### Public URLs
| URL | What it is |
|-----|-----------|
| https://argusreach.com | Marketing website (GitHub Pages) |
| https://admin.argusreach.com | Internal admin portal (port 5056) |
| https://hooks.argusreach.com | Webhook server for Stripe + Calendly (port 5055) |
| https://hooks.argusreach.com/health/monitor | Monitor health check |

### GitHub Repos
| Repo | What it contains |
|------|-----------------|
| BotGob/argusreach-ops | All code, monitor, tools, SOPs |
| BotGob/argusreach-website | Public marketing site |
| BotGob/argusreach-memory | Gob's memory files (workspace) |

### Active Systemd Services (all 5 should be running)
```
argusreach-admin       — admin portal, port 5056
argusreach-webhooks    — webhook server, port 5055
argusreach-watcher     — watches admin/ dir, auto-restarts admin on code changes
argusreach-sync.timer  — hourly Instantly API sync → SQLite DB
argusreach-dashboard.timer — hourly dashboard HTML refresh
argusreach-healthcheck.timer — checks monitor heartbeat every 30 min, Telegrams Vito if silent
```

Check status: `sudo systemctl status argusreach-admin argusreach-webhooks argusreach-watcher`

### Data Layer
- **Database:** SQLite at `argusreach/db/argusreach.db` (NOT on GitHub — gitignored)
- **Clients config:** `argusreach/monitor/clients.json`
- **Environment/keys:** `argusreach/monitor/.env`
- **DNC lists:** `argusreach/monitor/dnc/` (global.txt + per-client files)
- **Processed email IDs:** `argusreach/monitor/logs/processed_ids.json`

### Admin Portal Login
- URL: https://admin.argusreach.com
- Password: set in `monitor/.env` as `ADMIN_PASSWORD` (default: argusreach2026)

---

## Part 1 — If Gob Goes Silent

### Quick fix (try this first):
```
ssh argus@93.127.197.101
openclaw gateway restart
```
Send a Telegram message. If Gob responds, done.

### If still silent:
```bash
sudo systemctl restart openclaw
```

### Check that the monitor is still running:
```bash
sudo systemctl status argusreach-monitor
# If failed:
sudo systemctl restart argusreach-monitor
```

**Gob's memory is in files, not RAM.** A crash or restart loses nothing — Gob reads the files on startup and picks up where it left off.

---

## Part 2 — Full Rebuild From Scratch (Worst Case: Server Gone)

**Time required: ~2 hours**

### Step 1 — New VPS
Any Ubuntu 22.04+ VPS. Minimum 2GB RAM. DigitalOcean, Linode, Vultr all work.

### Step 2 — Install OpenClaw
Follow docs.openclaw.ai. Connect your Telegram bot token during setup.

### Step 3 — Restore Gob's workspace
```bash
cd ~/.openclaw
git clone https://github.com/BotGob/argusreach-memory.git workspace
```

### Step 4 — Restore ArgusReach code
```bash
cd ~/.openclaw/workspace
git clone https://github.com/BotGob/argusreach-ops.git argusreach
```

### Step 5 — Recreate API keys
Create `argusreach/monitor/.env` with these values (get from your Google Drive secure note):
```
INSTANTLY_API_KEY=
ANTHROPIC_API_KEY=
ARGUSREACH_BOT_TOKEN=
ARGUSREACH_CHAT_ID=
ADMIN_PASSWORD=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
```

### Step 6 — Reinstall services
```bash
# Copy service files
sudo cp argusreach/ops/*.service /etc/systemd/system/
sudo cp argusreach/monitor/argusreach-monitor.service /etc/systemd/system/

# Install timers
sudo bash argusreach/ops/setup-timers.sh

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now argusreach-monitor argusreach-admin argusreach-webhooks argusreach-watcher
```

### Step 7 — Set up nginx + SSL
```bash
sudo bash argusreach/ops/setup-nginx.sh
```
Point DNS for admin.argusreach.com and hooks.argusreach.com to the new server IP, then run certbot.

### Step 8 — Restore database
The SQLite DB is not on GitHub. If you have a backup, restore it. If not, the system will recreate an empty DB on first run — you'll lose historical stats but the monitor will work immediately.

---

## Part 3 — Moving to a Bigger Server (No Urgency)

1. Spin up new server, follow Part 2
2. Test: `python3 argusreach/monitor/monitor.py --test`
3. Stop old monitor: `sudo systemctl stop argusreach-monitor`
4. Start new monitor: `sudo systemctl start argusreach-monitor`
5. Update DNS if server IP changed
6. Run old server for 24h as backup, then decommission

**Downtime: under 5 minutes**

---

## Part 4 — What Gob Remembers (and Where)

| File | What it contains |
|------|-----------------|
| `MEMORY.md` | Long-term memory — key decisions, current state, lessons |
| `SOUL.md` | Personality and values |
| `USER.md` | Everything about Vito |
| `AGENTS.md` | Operating rules |
| `HEARTBEAT.md` | What Gob checks proactively |
| `memory/YYYY-MM-DD.md` | Daily session notes |
| `argusreach/ops/backlog.md` | Open items only (no completed tasks) |

All of these are backed up to GitHub (BotGob/argusreach-memory) nightly at 3am UTC via systemd timer.

---

## Part 5 — What to Keep in Google Drive

Create a folder: **"ArgusReach — Private"**

1. **API Keys** — a text file with all keys from Step 5 above. Update when keys change.
2. **A copy of this document**
3. **clients.json** — export a copy after each new client is added
4. **DNC lists** — export `monitor/dnc/*.txt` monthly

---

## Part 6 — Handing This Off to a New AI (If Not Gob)

If you ever need to start fresh with a different AI assistant, give them these files in this order:
1. `SOUL.md` — who Gob is
2. `USER.md` — who you are
3. `MEMORY.md` — current state of the business
4. `argusreach/ops/RECOVERY-AND-MIGRATION-GUIDE.md` — this file
5. `argusreach/ops/backlog.md` — what's open

That's enough to get any capable AI up to speed in one session.

---

## Quick Reference

| Problem | Fix |
|---------|-----|
| Gob not responding | `openclaw gateway restart` |
| Monitor stopped | `sudo systemctl restart argusreach-monitor` |
| Admin portal down | `sudo systemctl restart argusreach-admin` |
| Server gone | Follow Part 2 (~2 hrs) |
| Moving servers | Follow Part 3 (~5 min downtime) |
| Lost API keys | Google Drive → ArgusReach Private |
| Lost DNC list | Rebuild from `logs/replies.json` |
| Lost DB | Empty DB recreates on startup — historical stats lost |

---

*Last updated by Gob after every major system change. Keep a copy in Google Drive.*
