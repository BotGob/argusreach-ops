# ArgusReach — Ops Backlog

> Active items only. Completed items removed — see flowchart changelog for history.
> Last updated: 2026-03-24

---

## 🔴 Vito — Action Required

### 1. Reach out to Carter Pope (Creekside Recovery, Atlanta)
Warm intro, friend relationship — highest probability first client. Atlanta down 25% YoY.

### 2. LLC filing — ArgusReach LLC
sunbiz.org — Florida — $125. Do before first client signs.

### 3. DocuSign or HelloSign — free account
Service agreement signing. Free tier is fine. Required before first paid client.

### 4. Instantly.ai — confirm Growth upgrade ($47/mo)
Trial expired March 23. Confirm upgrade happened. Without Growth, warmup limits apply.

### 5. Apollo.io — upgrade to Basic ($49/mo) when first client signs
Free tier (50 exports/mo) is insufficient for real campaigns.

### 6. NeverBounce API key
$0.003/email. Required for email validation in monthly_cycle.py (currently skipped without key).
Get at: neverbounce.com → sign up → API key.

### 7. Update Stripe payment links
Current links work but `STRIPE_WEBHOOK_SECRET` not confirmed. Update metadata to include `client_id` per client when first client signs.

### 8. Install DNS auto-poll systemd timer (one command)
Run on server:
```
sudo bash -c 'cp /tmp/argusreach-dns-poll.service /etc/systemd/system/ && cp /tmp/argusreach-dns-poll.timer /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now argusreach-dns-poll.timer'
```

### 9. Delete duplicate Instantly campaign
Campaign `25d4f3ab-6b82-43b0-ab6c-243ce1671de1` was created during testing when upload failed. Delete it manually in Instantly UI — only `7cd7c8d8-3663-4ced-add2-2e6be939750e` is the real one.

### 10. Add ADMIN_PASSWORD to monitor/.env
`ADMIN_PASSWORD=argusreach2026` — currently defaults in code, should be explicit in .env.

---

## 🔴 Pre-Launch Gates (Gob)

### 11. Fix PT sequence copy in Instantly
Test client (`argus_reach`) sequence is now correct generic B2B copy. But the old `pt_tampa_bay_test` campaign in Instantly still has wrong copy (references "mental health practices"). Delete or rewrite before reusing.

---

## 🟡 High Value — Build When First Client Signs

### 12. Ready-to-launch email button
Send "subscription payment link + all gates green" email from portal when all 6 checklist items pass. Blocked on Vito updating Stripe links with per-client metadata. Route skeleton exists — needs wiring to portal button and Stripe per-client links.

### 13. All-gates-green notification
When all 6 checklist items pass simultaneously, fire a Telegram alert: "Client X is ready to launch." Currently Vito must check manually.

### 14. Calendly event type setup (first client)
ArgusReach owns one Calendly account. Create one event type per client (`calendly.com/argusreach/[client-id]`). Client connects their Google/Outlook calendar. Full setup checklist in MEMORY.md. Upgrade to Standard ($10/mo) when first client signs.

### 15. Instantly open/click analytics
`GET /api/v2/analytics/campaign/summary` returns 401 on current plan — likely a plan restriction. Revisit when on Growth. Scaffolding in `get_client_metrics()` ready to wire in when endpoint works.

### 16. ArgusReach self-prospecting warmup
Set up `outreach@mail.argusreach.com` in Instantly for our own prospecting. Start warmup when first client signs.

---

## 🟢 Scale Features (3+ Clients)

### 17. Client-facing dashboard
Per-client read-only view: campaign stats, reply breakdown, meetings booked. Internal portal exists — client version needs separate auth + filtered data.

### 18. Clay.com — LinkedIn personalization
$149/mo. Apollo → Clay enriches → Instantly loads. True 1:1 personalization. We have `{{custom_intro}}` from website enrichment as a $0 alternative covering ~60-70% of the value. Revisit at 2-3 clients.

### 19. Monitor async processing (10+ clients)
Currently single-threaded. At 10+ active clients, one cycle could take 3-5+ minutes. Fix: thread pool per client inbox. Trigger: 8 active clients.

### 20. PostgreSQL migration
SQLite handles ~8 clients fine. Trigger: 8 active clients.

### 21. Voice calling — Argus books meetings by phone
Bland.ai / Vapi.ai — call positive replies within minutes. Trigger: 3+ clients.

### 22. Bitcoin payment acceptance
BTCPay Server (self-hosted). When payment infrastructure is being formalized.
