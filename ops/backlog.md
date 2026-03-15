# ArgusReach — Ops & Product Backlog

> COO-maintained. Active items only — completed items live in the flowchart.
> Last updated: 2026-03-15 by Gob

---

## 🔴 Vito — Action Required

### 1. Reach out to Creekside Recovery Residences (Carter Pope)
- Warm intro, friend relationship — highest probability first client
- Vertical: sober living referral pipeline (therapists, treatment centers, discharge planners)
- Markets: Atlanta (down 25% YoY — pain point) + Tampa Bay
- **Action:** Send the message Gob drafted.

### 2. Identify 4 more warm contacts in your network to pitch
- Use `sales/pitch-deck-script.md` for guidance

### 3. DocuSign or HelloSign account setup
- Need for service agreement signing when first client is ready
- Both have free tiers for low volume

### 4. LLC filing — ArgusReach LLC
- sunbiz.org — Florida LLC — $125
- Do before first client signs

---

## 🔴 Pre-Launch Gates — Must Complete Before First Real Client

### 5. Campaign Creation Script (PINNED — highest priority)
- Reads `campaigns/[client_id]/sequence.md`, creates Instantly campaign via API with proper HTML formatting
- Also handles: prospect import into Instantly AND prospects.csv simultaneously (prevents the sync gap that caused missed replies)
- Eliminates manual copy-paste, formatting errors, and the Instantly lead API pitfalls we discovered in testing
- **Root cause solved:** Instantly editor doesn't format paragraphs — API is the only reliable path
- **Decision:** Build before PT Tampa Bay launch

### 6. Route Reply Emails Through Instantly (Not Raw SMTP)
- **Partial fix done (2026-03-15):** Added `In-Reply-To` / `References` threading headers — significantly improves deliverability by signaling replies vs cold outreach
- **Remaining:** Route approved reply drafts through Instantly's sending infrastructure instead of raw SMTP — Instantly's warmed IPs have better reputation with Yahoo/Outlook
- **Discovered:** Yahoo received Instantly sequence email but not our raw SMTP reply in sandbox test

### 7. Wire first real client into clients.json
- Copy example block, fill in: outreach email, app password, Calendly URL, ICP, prospects_csv path
- Set `active: true`, `mode: "draft_approval"`
- Test IMAP connection before going live

### 8. Set up client sending domain in Instantly
- Full SOP: `ops/client-email-setup-sop.md`
- Secondary domain (NOT user alias) required for standalone mailbox
- DNS: MX, SPF, DKIM, DMARC — all required before launch
- Warmup minimum 2–3 weeks before any campaign sends

### 9. Apollo paid + NeverBounce (pre-campaign gate)
- Apollo Basic ($49/mo) — needed for verified personal emails by title. Free tier only gives info@ generics.
- NeverBounce — verify every list before loading into Instantly. Target <2% bounce rate.
- Process: Apollo → export CSV → NeverBounce → remove invalid → load into Instantly + prospects.csv

### 10. Prospect List Ingestion & Hygiene Test
- Simulate real client onboarding with fake data before going live with a paying client
- Cross-reference client's existing contacts vs Apollo prospects → flag overlaps → check DNC → output clean list
- Non-negotiable: first client will have existing relationships we must not contact

---

## 🟡 High Value — Build When First Client is Live

### 11. Calendly Webhook → Airtable + Telegram
- Meeting booked → auto-update Prospect record + alert Vito
- Requires Calendly paid plan ($10/mo)

### 12. Monthly Report Auto-Generation
- Pull stats from Airtable → populate report template → email to client 1st of month

### 13. ArgusReach Self-Prospecting Domain Warm-Up
- Set up `outreach@mail.argusreach.com` in Google Workspace
- Add to Instantly, start warmup — for ArgusReach's own cold outreach
- Start when first client signs (parallel track)

---

## 🟢 Scale Features (3+ Clients)

### 14. Client-Facing Dashboard
- Read-only Airtable share link per client — zero build cost

### 15. Daily Send Cap Per Client (Circuit Breaker)
- Add `max_auto_responses_per_day` to clients.json — default 10

### 16. HubSpot CRM Migration
- Move from Airtable when 5+ clients

### 17. Lead Sourcing Automation (Clay.com)
- $149/mo — replace manual Apollo exports at 3+ simultaneous campaigns

---

## 💰 Pending Costs — Upgrade When First Client Signs

| Tool | Current | Cost | Trigger |
|------|---------|------|---------|
| Instantly.ai | Free trial (expires Mar 23) | $47/mo (Growth) | First client campaign ready |
| Apollo.io | Free (50 exports/mo) | $49/mo (Basic) | First client campaign ready |
| Calendly | Free Basic | $10/mo | Webhook → Airtable needed |
| Airtable | Free (1,000 records) | $20/mo | 5+ clients |
| DocuSign/HelloSign | Free (limited) | $15-25/mo | High signing volume |

> **Current monthly burn:** ~$129/mo (Claude Pro $100 + Hostinger $19.99 + Google Workspace $6 + Claude API ~$3)
> **At first client launch:** ~$225/mo (+ Instantly $47 + Apollo $49)
> ⚠️ Hostinger renews Mar 19 at $19.99/mo — staying monthly for now

---

## 🚀 Post-MVP Expansion

### Voice Calling — Argus Books Meetings by Phone
- Argus calls positive replies within minutes, books meeting on calendar
- Tools: Bland.ai, Vapi.ai, Retell.ai
- Build when: 3+ active clients, proven email ROI

### Longer-Term
- Referral partner program (attorneys/accountants refer clients)
- White-label option
- Vertical-specific landing pages
- Case study machine (auto-generate from Airtable data)
- AI-personalized opening lines at scale (Clay + Claude)
