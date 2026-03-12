# ArgusReach — Product & Ops Backlog

> COO-maintained. Items are ordered roughly by value/effort.
> Last updated: 2026-03-12 by Gob

---

## ✅ Completed (2026-03-11 / 03-12)

- ✅ Monitor deployed as systemd service — running 24/7, auto-restarts on reboot
- ✅ Monitor v2.1 — AI model fixed (claude-haiku-4-5-20251001), all 5 reply scenarios tested and passing
- ✅ Instantly sequence pause — handled by `stop_on_reply` campaign setting (no API call needed)
- ✅ Instantly unsubscribe — v2 blocklist endpoint wired, falls back to local DNC
- ✅ Airtable sync — pre-built in monitor, fires on every classified reply
- ✅ Subject line double "Re:" bug — fixed
- ✅ Telegram approval routing — alerts come to this chat, Vito approves/rejects via Gob directly
- ✅ Heartbeat pending check — Gob checks pending_approvals.json every 30 min, alerts if drafts are waiting
- ✅ Prospect CSV import script — `tools/import_prospects.py`, fuzzy column matching, dedup, rate limited
- ✅ Full dry run completed — 3/3 replies handled correctly (positive, not_now, negative/DNC)
- ✅ Service file fixed — loads keys from .env, no hardcoded values
- ✅ Backlog doc created — this file
- ✅ Master flowchart updated

---

## 🔴 Vito — Action Required Now

### 1. Reach out to Creekside Recovery Residences (Carter Pope)
- Warm intro, friend relationship — highest probability first client
- Vertical: sober living referral pipeline (therapists, treatment centers, discharge planners)
- Markets: Atlanta (down 25% YoY — pain point) + Tampa Bay
- Gob has assessed the fit: ✅ strong match
- **Action:** Send the message Gob drafted. Do it tomorrow morning.

### 2. Calendly — decide free vs. paid before March 22
- Trial expires ~2026-03-22 (10 days)
- Free Basic ($0, 1 event type) works for MVP
- Paid ($10/mo) needed for Calendly webhooks (meeting → Airtable auto-log)
- **Recommendation:** Gob paid. $10/mo is worth the automation.

### 3. Send first 5 outreach messages for ArgusReach itself
- Use `outreach/vito-first-outreach.md` or `sales/linkedin-outreach-script.md`
- Target: your network first — warm intros close faster
- Creekside is #1. Who else do you know that runs a professional services firm?

---

## 🔴 Gob — Action Required Next Client

### 4. Wire up first real client in clients.json
- Copy example block, fill in outreach email + app password + Calendly + ICP
- Set `active: true`, `mode: "draft_approval"`
- Test IMAP connection before going live

### 5. Import client prospect list into Airtable
- Run `tools/import_prospects.py --csv <file> --client <id>` before first email fires
- This MUST happen before Instantly sends Touch 1 — non-negotiable

### 6. Set up client sending domain in Instantly
- Create subdomain (e.g. `outreach.clientdomain.com`)
- Connect Gmail, start warm-up immediately on Day 1 of onboarding
- Enable `stop_on_reply=true` on every campaign created

---

## 🟡 High Value — Build When First Client is Live

### 7. Calendly Webhook → Airtable + Telegram
- When meeting booked → auto-update Prospect record + alert Vito
- Requires Calendly paid plan
- Implementation: Calendly webhook → n8n → Airtable PATCH + Telegram notify

### 8. Instantly Campaign Creation Script
- Script to create a new campaign in Instantly via API, pre-configured with correct settings
- Eliminates manual dashboard setup — Gob does it all from terminal
- Build once we have first active Instantly account

### 9. Monthly Report Auto-Generation
- Pull stats from Airtable → populate report template → email to client on 1st of month
- Tools: Python script + Airtable API

### 10. ArgusReach Self-Prospecting Domain Warm-Up
- Set up `outreach@mail.argusreach.com` in Google Workspace
- Add to Instantly, start warm-up — use for finding ArgusReach clients via cold email
- Start this when Creekside or first client is signed (parallel track)

---

## 🟢 Nice to Have — Scale Features

### 11. Client-Facing Dashboard
- Read-only Airtable share link per client: campaign status, emails sent, meetings booked
- Zero build cost — filtered Airtable view + share link

### 12. Daily Send Cap Per Client (Circuit Breaker)
- Add `max_auto_responses_per_day` to clients.json
- Default: 10. Prevents runaway scenarios.

### 13. List-Unsubscribe Header Support
- Some ESPs send unsubscribes via header, not reply text
- Low priority until volume demands it

### 14. HubSpot CRM Migration
- Move from Airtable when 5+ clients
- Free forever for core CRM features

### 15. Lead Sourcing Automation (Clay.com)
- $149/mo — add when running 3+ simultaneous campaigns
- Replaces manual Apollo exports

---

## 💡 Longer-Term Ideas

- **Referral Partner Program** — accountants, attorneys who refer clients get a cut
- **White-label** — sell ArgusReach under client's brand to their clients
- **Vertical-specific landing pages** — deeper pages for PT, RIA, Insurance, Sober Living
- **Case study machine** — auto-generate case study from Airtable data after first client result
- **AI-personalized opening lines** — Clay + Claude writes 1-line custom openers at scale
- **Sober Living vertical page** — Creekside conversation revealed this is a real market
