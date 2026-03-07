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
- Tool: **Instantly.ai** — $37/month
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

### n8n (Self-hosted on VPS — already have it)
- Free since we host it ourselves
- Connects everything: Apollo → Airtable → Instantly → Telegram alerts
- Automates: new lead added → enriched → loaded into sequence → Vito notified of replies
- Go builds and maintains all workflows

### Make.com (backup)
- $9/month if n8n proves too complex for certain tasks

---

## Website

### Phase 1 (Now): Static landing page
- Built by Go — clean, professional, 11x.ai-inspired dark design
- Hosted on Netlify (free)
- argusreach.com pointed here via GoDaddy DNS
- Content: headline, problem/solution, how it works, pricing, CTA (book a call)
- Calendar: Calendly (free) embedded for meeting booking

### Phase 2 (Month 2–3): Enhanced site
- Case studies / testimonials added
- Industry-specific landing pages
- Contact form with lead capture

---

## Meeting Booking
- **Calendly** (free) — Vito's calendar link for discovery calls
- Embed in website + include in email signatures

---

## Reporting (Client-Facing)
- Monthly report template built in Google Slides or Notion
- Auto-populated from Airtable data
- Metrics: contacts sent, open rate, reply rate, positive replies, meetings booked

---

## Monthly Cost Summary (MVP)

| Tool | Cost |
|------|------|
| Instantly.ai (email sending + warm-up) | $37/mo |
| Apollo.io (lead sourcing) | $0 (free tier) |
| Airtable (CRM) | $0 (free tier) |
| Netlify (website hosting) | $0 (free tier) |
| Calendly (booking) | $0 (free tier) |
| n8n (automation) | $0 (self-hosted on VPS) |
| Claude API (already paying) | ~$20–50/mo |
| **TOTAL** | **~$57–87/month** |

Revenue from 1 client at Starter ($750) = **$663–693 profit/month immediately**

---

## Setup Order (Priority)

1. ✅ Offer defined
2. ✅ Email sequences written
3. ⬜ GoDaddy DNS → point argusreach.com to Netlify
4. ⬜ Google Workspace setup (vito@argusreach.com)
5. ⬜ Instantly.ai account + domain warm-up start
6. ⬜ Apollo.io free account + first prospect list
7. ⬜ Airtable CRM template built
8. ⬜ Landing page built + deployed
9. ⬜ Calendly setup + embedded
10. ⬜ First outreach sent
