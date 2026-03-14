# ArgusReach Reply Monitor v2

Monitors every active client's outreach inbox. Classifies replies with Claude. Auto-responds or queues drafts for Vito's approval. Sends a nightly digest. Runs 24/7.

---

## How It Works

Every 10 minutes:
1. Checks every active client's Gmail inbox for new replies
2. Filters out: automated senders, cold inbound, spam, DNC contacts, already-processed messages
3. Classifies each genuine reply with Claude (`claude-haiku-4-5-20251001`)
4. **Automated mode:** sends response immediately
5. **Draft approval mode:** queues the draft and sends Vito a Telegram message with the text — Vito replies `APPROVE [id]` or `REJECT [id]`
6. Logs everything to `logs/replies.json`
7. Sends a daily digest at 6pm with totals by classification + pending approval count

---

## Telegram Commands (Vito sends these)

| Command | What it does |
|---|---|
| `PENDING` | Lists all drafts waiting for approval |
| `APPROVE [id]` | Sends the queued draft to the prospect |
| `REJECT [id]` | Discards the draft (contact stays on DNC if negative) |

The `[id]` is included in the draft notification Telegram sends automatically.

---

## Adding a New Client

Open `clients.json` and add a new entry to the `clients` array. Copy the example block. Fill in all fields. Set `"active": true`.

**Minimum fields required:**
```json
{
  "id": "unique_client_id",
  "active": true,
  "mode": "draft_approval",
  "outreach_email": "sender@clientdomain.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "sender_name": "First Last",
  "firm_name": "Client Firm Name",
  "vertical": "RIA",
  "calendly_link": "https://calendly.com/their-link/30min",
  "icp_summary": "Who they're targeting and why (used in AI prompt)",
  "tone": "warm-professional",
  "compliance_note": "What the AI must never say for this client"
}
```

**Mode options:**
- `"draft_approval"` — AI drafts, Vito approves via Telegram before anything sends. **Use for all new clients.**
- `"automated"` — AI responds immediately. Use only after 30 days of reviewing drafts and confirming quality.

---

## Client Gmail Setup (what to tell each new client)

Two steps. Takes 5 minutes. Walk them through this on the onboarding call.

**Step 1 — Create a dedicated outreach user in Google Workspace:**
```
admin.google.com → Directory → Users → Add new user
Create: firstname.lastname@theirdomain.com
(This is the outreach-only account. Their main email is never touched.)
```

**Step 2 — Generate a Gmail App Password:**
```
Log into the new account
→ Google Account (top right) → Security
→ Enable 2-Step Verification (if not already on)
→ Search "App Passwords" → Create one named "ArgusReach" → Mail
→ Copy the 16-character password (format: xxxx xxxx xxxx xxxx)
→ Email it to vito@argusreach.com
```

That's all they do. We handle everything else.

---

## Running the Monitor

**First time — install dependencies:**
```bash
pip install anthropic requests --break-system-packages
```

**Set your API key:**
```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**Start (dev / manual):**
```bash
cd /home/argus/.openclaw/workspace/argusreach/monitor
python3 monitor.py
```

**Test mode (no emails sent, no Telegram — safe for testing):**
```bash
python3 monitor.py --test
```

**Production — background process:**
```bash
nohup python3 monitor.py >> logs/monitor.log 2>&1 &
echo $! > monitor.pid
echo "Started PID $(cat monitor.pid)"
```

**Stop background process:**
```bash
kill $(cat monitor.pid)
```

**Watch live logs:**
```bash
tail -f logs/monitor.log
```

---

## Auto-Start on Server Reboot (systemd)

```bash
# Copy service file to systemd
sudo cp argusreach-monitor.service /etc/systemd/system/

# Edit the service file to add your real ANTHROPIC_API_KEY
sudo nano /etc/systemd/system/argusreach-monitor.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable argusreach-monitor
sudo systemctl start argusreach-monitor

# Check status
sudo systemctl status argusreach-monitor
```

---

## DNC (Do Not Contact) Lists

Each client has a DNC file at `dnc/[client_id].txt`.

- Negative replies add the prospect to the DNC list and unsubscribe them from Instantly
- In **draft_approval mode**, negative replies are also queued for Vito's review via Telegram before any acknowledgment is sent (APPROVE [id] to send removal ack, REJECT [id] to discard)
- In **automated mode**, a removal acknowledgment is sent immediately
- DNC emails are skipped in all future processing cycles — they will never be processed again
- To manually add someone: add their email address (one per line) to `dnc/client_id.txt`
- To remove someone: delete their line from the file

---

## Log Files

| File | Contents |
|---|---|
| `logs/monitor.log` | Full timestamped activity log |
| `logs/replies.json` | Every processed reply with classification and outcome |
| `logs/pending_approvals.json` | Drafts queued for Vito's approval (auto-cleared when approved/rejected) |
| `logs/processed_ids.json` | Fingerprints of every processed message (prevents double-processing on restart) |
| `dnc/[client_id].txt` | Per-client do-not-contact lists |

---

## Architecture Notes

**Why In-Reply-To filtering matters:** The monitor only processes emails that are replies to something we sent (they have `In-Reply-To` or `References` headers). Cold inbound emails don't have these headers. This eliminates ~95% of spam before any AI call, keeping costs low.

**Prospect list filtering:** In addition to reply-header filtering, the monitor validates that the sender's email exists in the client's `prospects_csv` file (configured per client in `clients.json`). This ensures we only respond to people we actually reached out to — warmup emails, inbound cold pitches, and other noise are ignored even if they have reply headers. Every new client requires a `campaigns/[client_id]/prospects.csv` before the monitor is activated.

**Why deduplication matters:** If the monitor restarts mid-cycle, it could see the same UNSEEN emails again. The `processed_ids.json` fingerprinting prevents any email from being responded to twice.

**Cost control:** Claude is only called after all local filters pass. With typical volumes (3–5 clients, 50–100 replies/day) the daily AI cap of 100 calls costs roughly $0.08–$0.15/day at Haiku pricing.

**Draft approval flow:** Drafts are stored in `pending_approvals.json`. The monitor polls Telegram for APPROVE/REJECT commands every cycle. No separate process needed.

---

## Requirements

```
anthropic>=0.84.0
requests
```

**Integrations:**
- **Anthropic Claude** — AI classification and draft generation (`claude-haiku-4-5-20251001`)
- **Instantly.ai** — prospect sequence pause and blocklist via API
- **Airtable** — CRM sync: reply classification and follow-up dates are written back to the prospect record automatically
- **Telegram** — Vito receives draft notifications and sends APPROVE/REJECT commands
