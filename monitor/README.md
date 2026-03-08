# ArgusReach Reply Monitor

Monitors all client outreach inboxes, classifies replies with AI, drafts and sends responses automatically. Runs 24/7 on the server.

---

## How It Works

Every 10 minutes the script:
1. Loops through every active client in `clients.json`
2. Connects to their outreach Gmail inbox via IMAP
3. Reads any unread replies from the last 12 hours
4. Uses Claude to classify each reply and draft the ideal response
5. Sends the response (automated mode) or forwards draft to Vito for approval (draft_approval mode)
6. Notifies Vito via Telegram for hot replies
7. Logs everything to `logs/replies.json`

---

## Adding a New Client

Open `clients.json` and add an entry to the `clients` array:

```json
{
  "id": "unique_client_id",
  "active": true,
  "mode": "draft_approval",
  "outreach_email": "name@clientdomain.com",
  "app_password": "xxxx xxxx xxxx xxxx",
  "sender_name": "First Last",
  "firm_name": "Client Firm Name",
  "vertical": "RIA",
  "calendly_link": "https://calendly.com/their-link/30min",
  "icp_summary": "Description of who they're targeting and why",
  "tone": "warm-professional",
  "compliance_note": "Any compliance restrictions (e.g. no performance promises)"
}
```

Set `"active": false` to pause a client without removing them.

### Mode options:
- `"automated"` — AI drafts and sends automatically. Client gets replies directly.
- `"draft_approval"` — AI drafts, Vito gets the draft via Telegram to approve before sending.

---

## Client Setup (what to tell each new client)

They need to do two things in their Google Workspace:

**Step 1 — Create a new Google Workspace user:**
- Go to admin.google.com → Directory → Users → Add user
- Create: `firstname.lastname@theirdomain.com` (or similar)
- This is the outreach-only account. Their main email is untouched.

**Step 2 — Generate a Gmail App Password:**
- Log into the new account → Google Account → Security → 2-Step Verification (enable it) → App Passwords
- Create an App Password for "Mail"
- Copy the 16-character password (format: `xxxx xxxx xxxx xxxx`)
- Share it with ArgusReach — this is the only credential we ever receive

That's it. We handle everything from there.

---

## Starting the Monitor

```bash
cd /home/argus/.openclaw/workspace/argusreach/monitor
export ANTHROPIC_API_KEY=your_key_here
python3 monitor.py
```

### Run as background service (recommended):

```bash
# Start in background, log to file
nohup python3 monitor.py >> logs/monitor.log 2>&1 &
echo $! > monitor.pid
```

### Stop:
```bash
kill $(cat monitor.pid)
```

### Check logs:
```bash
tail -f logs/monitor.log
cat logs/replies.json | python3 -m json.tool
```

---

## Telegram Notifications

Vito receives Telegram messages for:
- 🎯 Positive replies (hot leads)
- ❓ Questions from prospects
- ⚠️ Errors or unclear replies needing review
- ✅ Confirmations when auto-responses are sent

For `draft_approval` mode, the draft is included in the Telegram message. Reply with `SEND [client_id] [prospect_email]` to approve. *(manual approval flow — auto-approval coming later)*

---

## Scaling

Adding the 10th client works exactly the same as adding the 1st. The script loops through all active clients in sequence. At 10+ clients, consider reducing `LOOKBACK_HOURS` to 6 to keep cycles fast.

---

## Requirements

```
anthropic
requests
```

Install: `pip install anthropic requests --break-system-packages`
