# ArgusReach — Tech Stack & Infrastructure Plan

## Philosophy: Buy don't build. Automate everything possible.

---

## Email Infrastructure (CRITICAL — Do First)

### Domain Setup
- Primary domain: argusreach.com (owned, on GoDaddy)
- Sending subdomain: mail.argusreach.com (separate from main domain — protects reputation)
- Business email: vito@argusreach.com (for sales/client comms)
- Sending email: outreach@mail.argusreach.com (for cold sequences)

### Domain Warm-Up
- New domain = zero reputation = emails go to spam
- Must warm up over 2–4 weeks before sending real volume
- Tool: Instantly.ai (handles warm-up automatically)
- Cost: $37/month (Instantly Growth plan)

### Email Sending
- Tool: **Instantly.ai** — $47/month (Growth plan)
  - Handles warm-up, sequences, deliverability, tracking
  - Simple UI, no coding required
  - Connects to any email account
- Backup option: **Smartlead** ($39/month) — similar features

---

## Lead Sourcing

### Primary: Apollo.io (Free tier to start)
- 50 free exports/month on free plan
- Search by: title, company size, location, industry, revenue
- Verified email addresses
- Free plan is enough for MVP (first 1–2 clients)
- Paid: $49/month for 1,000 exports

### Secondary: LinkedIn (manual research, no scraping)
- Use Sales Navigator for advanced search ($79/month — defer until paying clients)
- Manual prospecting for first 20–30 leads is fine

### Enrichment (later): Clay.com
- Automates lead research + personalization at scale
- $149/month — add when volume demands it

---

## CRM / Pipeline Tracking

### MVP: Airtable (Free)
- Track: prospect name, company, email, sequence, status, response, notes
- Simple kanban view: Cold → Contacted → Replied → Meeting Booked → Client
- Free up to 1,200 records — enough for 6+ months
- We build the template once, Vito doesn't touch the backend

### Later: HubSpot CRM (Free tier)
- More powerful, scales better
- Free forever for basic CRM features
- Add when we have 5+ clients

---

## Workflow Automation

### monitor.py (Custom — ArgusReach Reply Monitor)
- Handles all current automation: inbox monitoring → AI classification → Telegram draft approval → Airtable sync → DNC management
- Runs as a systemd service on the VPS, checks every 10 minutes
- **n8n:** Self-hosted and available on the VPS but not currently used for active workflows. Planned for future use (e.g., Apollo → Airtable lead import automation). monitor.py handles everything for now.

### Make.com (backup)
- $9/month if n8n proves too complex for certain tasks

---

## Website

### Phase 1 (Now): Static landing page
- Built by Go — clean, professional, 11x.ai-inspired dark design
- Hosted on GitHub Pages (BotGob/argusreach-website)
- argusreach.com pointed here via GoDaddy DNS (CNAME)
- Content: headline, problem/solution, how it works, pricing, CTA (book a call)
- Calendar: Calendly (free) embedded for meeting booking

### Phase 2 (Month 2–3): Enhanced site
- Case studies / testimonials added
- Industry-specific landing pages
- Contact form with lead capture

---

## Meeting Booking
- **Calendly** — Vito's calendar link for discovery calls: `calendly.com/vito-argusreach/30min`
- ⚠️ **Action needed ~2026-03-22:** Free trial ends. Confirm whether Basic (free) plan is sufficient. If yes, downgrade; if reminders or multiple event types are needed, Standard is $10/month.

---

## Reporting (Client-Facing)
- Monthly report template built in Google Slides or Notion
- Auto-populated from Airtable data
- Metrics: contacts sent, open rate, reply rate, positive replies, meetings booked

---

## Monthly Cost Summary (MVP)

| Tool | Cost |
|------|------|
| Instantly.ai (email sending + warm-up) | $47/mo (Growth) |
| Apollo.io (lead sourcing) | $0 (free tier) |
| Airtable (CRM) | $0 (free tier) |
| Netlify (website hosting) | $0 (free tier) |
| Calendly (booking) | $0 free / $10/mo paid — ⚠️ trial ends ~2026-03-22 |
| n8n (automation) | $0 (self-hosted on VPS) |
| Claude API (already paying) | ~$20–50/mo |
| **TOTAL** | **~$57–87/month** |

Revenue from 1 client at Starter ($750) = **$663–693 profit/month immediately**

---

## Setup Order (Priority)

1. ✅ Offer defined
2. ✅ Email sequences written
3. ✅ GoDaddy DNS → argusreach.com pointed to GitHub Pages (BotGob/argusreach-website)
4. ✅ Google Workspace setup (vito@argusreach.com)
5. ✅ Instantly.ai account + domain warm-up active (vito@argusreach.com warming now)
6. ✅ Apollo.io free account + first prospect list (20 PT clinics loaded)
7. ✅ Airtable CRM template built and integrated with monitor
8. ✅ Landing page built + deployed (argusreach.com live)
9. ✅ Calendly setup (https://calendly.com/vito-argusreach/30min)
10. ⬜ First real outreach sent (PT Tampa Bay campaign awaiting Vito launch approval)
