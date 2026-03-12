# ArgusReach — Ops & Product Backlog

> COO-maintained. Active items only — completed items live in the flowchart.
> Last updated: 2026-03-12 by Gob

---

## 🔴 Vito — Action Required

### 1. Reach out to Creekside Recovery Residences (Carter Pope)
- Warm intro, friend relationship — highest probability first client
- Vertical: sober living referral pipeline (therapists, treatment centers, discharge planners)
- Markets: Atlanta (down 25% YoY — pain point) + Tampa Bay
- Fit: ✅ strong match — "Behavioral Health & Recovery" vertical on website covers this
- **Action:** Send the message Gob drafted.

### 2. Identify 4 more warm contacts in your network to pitch
- Creekside is #1. Who else runs a professional services firm you could intro to?
- Use `sales/pitch-deck-script.md` and `outreach/vito-first-outreach.md` for guidance

### 3. DocuSign or HelloSign account setup
- Need for service agreement signing when first client is ready
- Both have free tiers for low volume

### 5. LLC filing — ArgusReach LLC
- sunbiz.org — Florida LLC — $125
- Do before first client signs

---

## 🔴 Gob — Next Client Actions

### 6. Wire first real client into clients.json
- Copy example block, fill in: outreach email, app password, Calendly URL, ICP
- Set `active: true`, `mode: "draft_approval"`
- Test IMAP connection before going live

### 7. Import client prospect list into Airtable before any email fires
- Run `tools/import_prospects.py --csv <file> --client <id>`
- Non-negotiable gate — prospects must be in Airtable before Touch 1

### 8. Set up client sending domain in Instantly
- Create subdomain (e.g. `outreach.clientdomain.com`)
- Connect Gmail, start warm-up Day 1 of onboarding
- Enable `stop_on_reply=true` on every campaign

---

## 🟡 High Value — Build When First Client is Live

### 9. Calendly Webhook → Airtable + Telegram
- Meeting booked → auto-update Prospect record + alert Vito
- Requires Calendly paid plan
- Stack: Calendly webhook → Airtable PATCH + Telegram notify

### 10. Instantly Campaign Creation Script
- Create + configure campaigns via API — no dashboard needed
- Build once first Instantly account is active

### 11. Monthly Report Auto-Generation
- Pull stats from Airtable → populate report template → email to client 1st of month

### 12. ArgusReach Self-Prospecting Domain Warm-Up
- Set up `outreach@mail.argusreach.com` in Google Workspace
- Add to Instantly, start warm-up — use for ArgusReach's own cold outreach
- Start when first client signs (parallel track)

---

## 🟢 Scale Features

### 13. Client-Facing Dashboard
- Read-only Airtable share link per client — zero build cost

### 14. Daily Send Cap Per Client (Circuit Breaker)
- Add `max_auto_responses_per_day` to clients.json — default 10

### 15. HubSpot CRM Migration
- Move from Airtable when 5+ clients

### 16. Lead Sourcing Automation (Clay.com)
- $149/mo — replace manual Apollo exports at 3+ simultaneous campaigns

---

## 🚀 Post-MVP Product Expansion

### Voice Calling — Argus Books Meetings by Phone
- Argus calls positive replies within minutes, confirms interest, locks in meeting time
- Tools to evaluate: Bland.ai, Vapi.ai, Retell.ai
- Human-sounding voice from client's number, no human involvement needed
- **Why it matters:** 10-minute callback after positive reply closes 3x more vs next-day email
- **Build when:** 3+ active clients, proven email ROI

---

## 💡 Longer-Term Ideas

- Referral Partner Program — attorneys/accountants refer clients, get a cut
- White-label — sell ArgusReach under client's brand
- Vertical-specific landing pages — deeper pages per vertical
- Case study machine — auto-generate from Airtable data after first result
- AI-personalized opening lines at scale — Clay + Claude
