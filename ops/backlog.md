# ArgusReach — Ops Backlog

> Active items only. Completed items live in the flowchart changelog.
> Last updated: 2026-03-16

---

## 🔴 Vito — Action Required

### 1. Reach out to Creekside Recovery Residences (Carter Pope)
Warm intro, friend relationship — highest probability first client. Atlanta down 25% YoY. Message drafted.

### 2. DocuSign or HelloSign — free account
Need for service agreement signing before first client. Free tier is fine to start.

### 3. LLC filing — ArgusReach LLC
sunbiz.org — Florida — $125. Do before first client signs.

### 4. Instantly.ai — upgrade to Growth ($47/mo) by March 23
Free trial expires March 23. Don't wait — upgrade now.

### 5. Apollo.io — upgrade to Basic ($49/mo) when first client signs
Free tier (50 exports/mo) insufficient for client campaigns.

### 6. Stripe webhook secret + Stripe secret key
Add to monitor/.env so Stripe payments auto-log:
- `STRIPE_SECRET_KEY=sk_live_...`
- `STRIPE_WEBHOOK_SECRET=whsec_...`
Then register: https://hooks.argusreach.com/webhooks/stripe in Stripe Dashboard → Developers → Webhooks

### 7. Calendly webhook registration
Register: https://hooks.argusreach.com/webhooks/calendly in Calendly → Integrations → Webhooks

---

## 🔴 Pre-Launch Gates (Gob)

### 8. Fix PT Tampa Bay sequence copy in Instantly
Sequence says "mental health practices" — wrong vertical. Must rewrite all 3 touches before any real prospect enrolled.

### 9. Run timers setup
```
sudo bash /home/argus/.openclaw/workspace/argusreach/ops/setup-timers.sh
```
Installs hourly systemd timers for Instantly sync + dashboard refresh.

---

## 🟡 High Value — Build After First Client Live

### 10. Campaign creation script (sequence.json → Instantly API)
Fully automate campaign setup from a sequence.json file. Currently `campaign_create.py` handles leads + structure but sequence must be written manually in Instantly UI first.

### 11. Calendly webhook — client-side limitation
For client campaigns (their Calendly), bookings go to their calendar — no visibility. Interim: client emails vito@argusreach.com when meeting confirms. Long-term: provide ArgusReach Calendly link and own the webhook.

### 12. ArgusReach self-prospecting domain warm-up
Set up outreach@mail.argusreach.com in Instantly. Start warmup when first client signs.

---

## 🟢 Scale Features (3+ Clients)

### 13. Client-facing dashboard
Per-client read-only view: campaign stats, reply breakdown, meetings booked. Internal portal exists — client version needs auth + filtering.

### 14. HubSpot CRM migration
At 5+ clients, migrate from SQLite DB to HubSpot for CRM layer.

### 15. Lead sourcing automation (Clay.com)
$149/mo — replaces manual Apollo exports at 3+ simultaneous campaigns.

### 16. Bitcoin payment acceptance
BTCPay Server (self-hosted) — when payment infrastructure is being formalized.

### 17. Voice calling — Argus books meetings by phone
Bland.ai / Vapi.ai — call positive replies within minutes, book meeting on calendar. Build at 3+ clients.

---

## 🟡 Backlog — Added 2026-03-16

### 18. Monthly report auto-send
`tools/monthly_report.py` works and sends to client. Needs a systemd timer to run on the 1st of each month. Holding at MVP — Vito wants to review before sending.

### 19. Campaign completion notification
When Instantly finishes all prospects in a campaign (status → completed), send Vito a Telegram alert to renew the lead list or close it out. No current hook.

### 20. Welcome email to new client on onboarding
When intake is approved and client is created, auto-send a welcome/onboarding email to the client. Currently nothing is sent.

### 21. Service agreement / DocuSign
No signed contract flow in the system. Client signs → record stored. Free tier HelloSign or DocuSign. Required before first paid client.

### 22. Reports tab
Admin portal Reports tab exists but empty — no reports have ever been generated. Populates automatically once `monthly_report.py` runs for the first time.

### 23. Cross-client warm lead tracking ✅ DONE
Global DNC now protects against re-contacting unsubscribes across all clients.

### 24. Monitor health check timer
Needs `sudo bash ops/setup-timers.sh` re-run to install the new healthcheck timer.
