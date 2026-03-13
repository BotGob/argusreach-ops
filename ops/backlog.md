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
- Full SOP documented: `ops/client-email-setup-sop.md`
- Secondary domain (NOT user alias) required for standalone mailbox
- DNS: MX, SPF, DKIM, DMARC — all required before launch
- Approve Instantly as trusted app in Google Workspace Admin first
- Connect via Google OAuth in Instantly, enable warmup Day 1
- Warmup minimum 2–3 weeks before any campaign sends
- Enable `stop_on_reply=true` on every campaign
- Decide delivery model per client: admin access (preferred) vs guided screen share

### 8b. Prospect list sourcing & email verification (pre-campaign gate)
- **Apollo paid ($49-99/mo)** — required before first client campaign. Free tier (50 exports/mo) only gives generic info@ emails from web scraping. Paid gives verified personal emails by title/location/company size.
- **Email verification (NeverBounce or ZeroBounce)** — run every list through verifier before loading into Instantly. Target <2% bounce rate. Cost: ~$3 per 1,000 emails.
- Process: Apollo search → export CSV → NeverBounce verify → remove invalid/risky → load into Instantly
- This is non-negotiable for client campaigns. Generic info@ emails = high bounces = damaged sender reputation.
- Upgrade Apollo to Basic ($49/mo) when first client is signed — add to pending costs table.

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

## 💰 Pending Costs — Upgrade When First Client Signs

| Tool | Current | Upgrade Trigger | Cost |
|------|---------|-----------------|------|
| Instantly.ai | Free trial (expires Mar 23 — goes inactive) | First client campaign ready to launch | $47/mo (Growth) |
| Calendly | Free Basic (1 event type) | Ready to auto-log meetings to Airtable | $10/mo |
| Apollo.io | Free (50 exports/mo) | Need more than 50 contacts/mo | $49/mo (Basic) |
| Airtable | Free (1,000 records/base) | 5+ clients with large prospect lists | $20/mo |
| DocuSign/HelloSign | Free (limited) | High signing volume | $15-25/mo |

> **Current monthly burn:** ~$129/mo (Claude Pro $100 + Hostinger $19.99 + Google Workspace $6 + Claude API ~$3)
> **At first client launch:** ~$275/mo (+ Instantly $47 + Apollo $99)
> **At scale (5+ clients):** ~$295/mo (+ Calendly $10 + Airtable $20 when needed)
> ⚠️ Hostinger renews Mar 19 at $19.99/mo — staying monthly for now, revisit annual plan when first client signs

### Pending Costs — Upgrade When First Client Signs
| Tool | Plan | Cost | Trigger |
|------|------|------|---------|
| Instantly.ai | Growth | $47/mo | First client ready to launch |
| Apollo.io | Professional | $99/mo | First client ready to launch |
| Calendly | Standard | $10/mo | When webhook → Airtable automation needed (not urgent) |

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
