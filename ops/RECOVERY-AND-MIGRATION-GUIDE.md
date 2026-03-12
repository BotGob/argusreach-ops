# ArgusReach — Recovery & Migration Guide
*Written for Vito. No technical background required.*
*Last updated: 2026-03-12*

---

## What's Backed Up Right Now

Everything important lives in GitHub. Even if the server burns down tonight, nothing is lost permanently.

| What | Where on GitHub | How often |
|------|----------------|-----------|
| Gob's memory, identity, personality files | BotGob/argusreach-memory | Nightly at 3am + on demand |
| ArgusReach code, monitor, tools, SOPs | BotGob/argusreach-ops | Every time Gob makes a change |
| Website | BotGob/argusreach-website | Every time Gob makes a change |
| API keys (.env) | **NOT on GitHub** (intentional — security) | Stored only on server |

**The only things NOT backed up automatically:**
- API keys (Anthropic, Airtable, Instantly) — stored in `monitor/.env` on the server only
- DNC lists per client — stored in `monitor/dnc/` on the server only
- You need to keep a copy of these somewhere safe (see below)

---

## Part 1 — What to Do If Gob Crashes or Goes Silent

### If Gob stops responding in Telegram:

**Step 1 — Restart the OpenClaw gateway**
```
ssh argus@YOUR_SERVER_IP
openclaw gateway restart
```

**Step 2 — Check if it came back**
Send any message in Telegram. If Gob responds, you're done.

**Step 3 — If still not responding, restart the server process**
```
sudo systemctl restart openclaw
```

**Step 4 — Check monitor is still running**
```
sudo systemctl status argusreach-monitor
```
If it says "inactive" or "failed":
```
sudo systemctl restart argusreach-monitor
```

That's it. Gob's memory and personality are loaded from files on startup — nothing is lost when it crashes.

---

## Part 2 — Full Rebuild From Scratch (Worst Case)

If the entire server is gone and you need to start fresh on a new machine:

### Step 1 — Set up a new VPS
- Any Ubuntu 22.04+ VPS works (DigitalOcean, Linode, Vultr, etc.)
- Minimum: 2GB RAM, 2 CPU, 40GB disk
- Recommended: 4GB RAM, 2 CPU, 80GB disk (~$24/month on DigitalOcean)

### Step 2 — Install OpenClaw
Follow the OpenClaw setup guide at docs.openclaw.ai. Connect your Telegram bot token during setup.

### Step 3 — Restore Gob's memory and workspace
```bash
cd ~
mkdir -p .openclaw/workspace
cd .openclaw/workspace
git clone https://github.com/BotGob/argusreach-memory.git .
git clone https://github.com/BotGob/argusreach-ops.git argusreach/monitor_restore
```

### Step 4 — Restore API keys
You need to recreate `argusreach/monitor/.env` manually. Keep this list somewhere safe (password manager, Google Drive secure note):

```
INSTANTLY_API_KEY=     ← from Instantly dashboard → Settings → API
AIRTABLE_TOKEN=        ← from Airtable → Account → API
AIRTABLE_BASE_ID=      ← appquzx2A8BByrarX (your CRM base)
ANTHROPIC_API_KEY=     ← from console.anthropic.com
ARGUSREACH_BOT_TOKEN=  ← 8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk
ARGUSREACH_CHAT_ID=    ← 8135725412
```

### Step 5 — Reinstall monitor service
```bash
sudo cp argusreach/monitor/argusreach-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now argusreach-monitor
```

### Step 6 — Restore DNC lists
If you have any active clients, the DNC lists (do-not-contact) live in `monitor/dnc/`. These are small text files. If you backed them up (see below), restore them here. If not, they can be rebuilt from reply logs.

**Time to full recovery: approximately 2 hours** (mostly waiting for installs)

---

## Part 3 — Migrating to a Bigger/Faster Machine

Same process as Part 2, but you have the luxury of doing it without urgency.

### Recommended approach: clone, test, then switch

**Step 1 — Spin up the new server** (keep old one running)

**Step 2 — Set up OpenClaw and restore everything** (follow Part 2)

**Step 3 — Run the monitor in test mode on the new server**
```bash
cd argusreach/monitor
python3 monitor.py --test
```
Confirm it connects to Gmail inboxes and AI is working.

**Step 4 — Stop the monitor on the old server**
```bash
sudo systemctl stop argusreach-monitor
```

**Step 5 — Start it on the new server**
```bash
sudo systemctl start argusreach-monitor
```

**Step 6 — Point Telegram to the new server** (if needed)
Nothing changes in Telegram — the bot token stays the same. OpenClaw just needs to be running on the new machine.

**Step 7 — Decommission old server** once you've confirmed everything works for 24 hours.

**Downtime during migration: under 5 minutes** (the gap between stopping old and starting new)

---

## Part 4 — What Gob Keeps in Memory

Gob's "brain" is these files on the server (all backed up to GitHub nightly):

| File | What it contains |
|------|-----------------|
| `MEMORY.md` | Long-term memory — key decisions, context, lessons |
| `SOUL.md` | Personality, values, how Gob thinks |
| `USER.md` | Everything about you — your goals, working style, preferences |
| `AGENTS.md` | Operating rules — how Gob handles memory, group chats, tools |
| `HEARTBEAT.md` | What Gob checks proactively between conversations |
| `IDENTITY.md` | Gob's name, emoji, avatar |
| `memory/YYYY-MM-DD.md` | Daily session notes — raw log of what happened each day |

When Gob restarts (crash, reboot, new session), it reads these files and picks up exactly where it left off. **Nothing is lost between sessions as long as these files are on GitHub.**

---

## Part 5 — Things to Store in Your Google Drive

Save these in a secure Google Drive folder called "ArgusReach — Private":

1. **API Keys doc** — a text file with all the keys from Step 4 above (keep this updated when keys change)
2. **A copy of this document** — so you have it even if the server is gone
3. **clients.json** — the active client config file. Export a copy after each new client is added.
4. **DNC lists** — export `monitor/dnc/*.txt` files periodically (monthly is fine)

---

## Part 6 — Recommended Backup Schedule (What Gob Will Do)

Currently running:
- ✅ Nightly memory backup at 3am UTC (SOUL, USER, MEMORY, daily notes → GitHub)
- ✅ Code changes pushed to GitHub on every update

**To add (Gob will set this up):**
- [ ] Weekly DNC list backup — copy client DNC files to a dated archive
- [ ] Monthly `.env` reminder — alert Vito to verify API keys are still saved in Drive

---

## Quick Reference Card

| Situation | What to do |
|-----------|-----------|
| Gob not responding | `openclaw gateway restart` on the server |
| Monitor stopped | `sudo systemctl restart argusreach-monitor` |
| Entire server gone | Follow Part 2 (2 hours to full recovery) |
| Moving to bigger server | Follow Part 3 (~5 min downtime) |
| Lost API keys | Recover from Google Drive secure note |
| Lost DNC list | Rebuild from `logs/replies.json` |

---

*Save this document to Google Drive. It has everything you need to get back up and running without Gob's help.*
