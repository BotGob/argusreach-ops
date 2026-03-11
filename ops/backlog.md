# ArgusReach — Product & Ops Backlog

> COO-maintained. Items are ordered roughly by value/effort. Move to `launch-checklist.md` when prioritized for a sprint.

---

## 🔴 Next Up (do these when first client signs)

### 1. Deploy Monitor as System Service
- Run: `sudo cp monitor/argusreach-monitor.service /etc/systemd/system/`
- Then: `sudo systemctl daemon-reload && sudo systemctl enable argusreach-monitor && sudo systemctl start argusreach-monitor`
- Prereq: active client entry in `monitor/clients.json`
- **Note:** Service file is fixed — loads keys from `.env` automatically

### 2. Wire Up First Real Client in clients.json
- Copy the example block in `monitor/clients.json`
- Fill in: outreach email, Gmail app password, Calendly link, ICP summary
- Set `active: true`, `mode: "draft_approval"` (always start here — switch to automated after 2 weeks of clean drafts)

### 3. Instantly.ai — Client Sending Domain Warm-Up
- Create client's sending subdomain (e.g., `outreach.clientdomain.com`)
- Add to Instantly, connect Gmail, start warm-up sequence
- Do NOT send real volume until 3+ weeks of warm-up
- Warm-up runs in parallel — start on Day 1 of onboarding

---

## 🟡 High Value — Build When We Have 2+ Clients

### 4. Monitor → Airtable Auto-Sync
When monitor classifies a reply, auto-update the Prospect record in Airtable:
- Set `Status` to match reply type (Replied — Interested, DNC, etc.)
- Log `Last Reply` text
- Set `Last Contacted` date
- Currently: monitor logs to local JSON only — manual Airtable updates required
- **Implementation:** Add Airtable API calls to `monitor.py` after `log_reply()`

### 5. Instantly.ai → Monitor Sequence Auto-Pause
When monitor detects a positive/negative reply, auto-pause that prospect's sequence in Instantly via API:
- Prevents Touch 2 going out after a positive reply
- Currently: manual step in reply-handling-sop.md
- **Implementation:** Add `pause_instantly_contact()` call using `INSTANTLY_API_KEY` from `.env`
- API endpoint: `POST /api/v1/lead/pause` (check Instantly docs)

### 6. Calendly Webhook → Airtable Meeting Log
When a meeting is booked via Calendly:
- Auto-set `Meeting Booked = true` and `Meeting Date` in Airtable Prospect record
- Auto-notify Vito via Telegram: "📅 Meeting booked — [name] at [company] for [date]"
- **Implementation:** Calendly webhook → n8n → Airtable + Telegram
- Calendly webhooks available on paid plan (another reason to keep paid vs. free Basic)

### 7. ArgusReach Self-Prospecting Domain Warm-Up
- Set up `outreach@mail.argusreach.com` in Google Workspace
- Add to Instantly.ai and start warm-up for Vito's own pipeline
- Use this to prospect for ArgusReach clients via cold email
- Separate from client campaigns — ArgusReach sells itself this way

---

## 🟢 Nice to Have — Scale Features

### 8. Client-Facing Dashboard
Simple read-only Airtable share link per client showing:
- Their campaign status, emails sent, meetings booked this month
- Cuts down on reporting overhead / client check-in calls
- Zero build cost — just a filtered Airtable view + share link

### 9. Monthly Report Auto-Generation
- Pull stats from Airtable (emails sent, reply rate, meetings booked)
- Populate a Google Slides template automatically
- Send to client on the 1st of each month via email
- **Tools:** n8n + Google Slides API or a simple Python script

### 10. Reply Volume Dashboard (internal)
Telegram daily digest exists — add a weekly summary:
- Total replies across all clients this week
- Meetings booked vs. prior week
- Any DNC spikes (signals bad list quality)

### 11. List-Unsubscribe Header Support
Some ESPs send unsubscribes via `List-Unsubscribe` headers rather than reply text.
Monitor should parse and honor these even if the reply body is neutral.
Low priority until we see volume.

### 12. Daily Send Cap Per Client (Circuit Breaker)
Add a configurable `max_auto_responses_per_day` field to each client in `clients.json`.
Default: 10. Prevents runaway auto-responses if something goes wrong.

### 13. HubSpot CRM Migration
- Move from Airtable to HubSpot Free when we hit 5+ clients
- Better pipeline visibility, email tracking, deal stages
- Free forever for core CRM features

### 14. Lead Sourcing Automation (Clay.com)
At scale (3+ active campaigns running):
- Clay.com automates Apollo exports + personalization at scale
- $149/month — worth it when volume demands it
- Replaces manual Apollo exports

---

## 💡 Longer-Term Ideas

- **Referral Partner Program** — accountants, attorneys who refer clients get a cut
- **White-label** — sell ArgusReach under a client's brand to their clients
- **Vertical-specific landing pages** — PT, RIA, Insurance each get a dedicated page with vertical-specific proof points
- **Case study machine** — after first client result, auto-generate case study from Airtable data
- **AI-personalized opening lines** — use Clay + Claude to write 1-line personalized openers at scale before loading into Instantly

---

*Last updated: 2026-03-11 by Go*
