# ArgusReach — Ops Backlog

> Active items only. Completed items live in the flowchart changelog.
> Last updated: 2026-03-18

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

### 6. ✅ Stripe webhook — DONE (2026-03-17)
### 7. ✅ Calendly webhook — DONE (2026-03-18)

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

### 11. ✅ Calendly client-side limitation — SOLVED (2026-03-18)
ArgusReach owns the Calendly account with per-client event types. Webhook live. Upgrade to Standard ($10/mo) when first client signs.

### 12. ArgusReach self-prospecting domain warm-up
Set up outreach@mail.argusreach.com in Instantly. Start warmup when first client signs.

---

## 🟢 Scale Features (3+ Clients)

### 32. Pre-load prospects into DB at campaign launch
When contacts are loaded to Instantly, also write them to the `prospects` table in our DB. Currently prospects only appear in the DB after they reply. Pre-loading enables: full prospect list visible in portal before replies, accurate total-contacted counts, better reporting at scale. Trigger: before second client onboarded.



### Clay.com — LinkedIn activity personalization
$149/mo. Enriches each prospect with LinkedIn activity, recent posts, company news, job changes.
Enables true 1:1 personalization in sequences (e.g. "I saw your post about X last week").
Currently we use name/company/title/city from Apollo — good but not 1:1.
Clay sits between Apollo and Instantly: Apollo exports → Clay enriches → Instantly loads.
**Trigger:** First paying client signed. This is a differentiator worth paying for early.



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

### 18. Monthly report auto-generate ✅ DONE
Cron runs 1st of each month at 9am ET. Generates reports for all active clients, saves to reports/, alerts Vito to review before sending manually.

### 19. Campaign completion notification
When Instantly finishes all prospects in a campaign (status → completed), send Vito a Telegram alert to renew the lead list or close it out. No current hook.

### 20. Welcome email to new client on onboarding ✅ DONE
Auto-sends on intake approval. Covers next steps: intake form, IT email setup, sequence review, launch timeline.

### 21. Service agreement / DocuSign
No signed contract flow in the system. Client signs → record stored. Free tier HelloSign or DocuSign. Required before first paid client.

### 22. Reports tab
Admin portal Reports tab exists but empty — no reports have ever been generated. Populates automatically once `monthly_report.py` runs for the first time.

### 23. Cross-client warm lead tracking ✅ DONE
Global DNC now protects against re-contacting unsubscribes across all clients.

### 24. Monitor health check timer ✅ DONE
Healthcheck timer installed and running.

---

## 🟢 Scale Features — Added 2026-03-17

### 25. Monitor async processing (10+ clients)
Currently single-threaded — processes client inboxes sequentially. At 10+ active clients with busy campaigns, one cycle could take 3-5+ minutes. Fix: run each client inbox check in a thread pool. Trigger: when we hit 8 active clients.

### 26. PostgreSQL migration (10+ clients)
SQLite handles concurrent reads/writes fine up to ~8 clients. Beyond that, lock contention will cause errors. Migration path: swap connection string in database.py, migrate schema + data with one-time script. Already designed for this — 2 hours of work when needed. Trigger: 8 active clients.

### 27. Apollo paid API + automated lead sourcing script
Upgrade to Apollo Basic ($49/mo) when first client signs. Build sourcing script that takes ICP parameters (title, geography, company size) and auto-exports a contact list directly into the campaign folder. Eliminates manual CSV exports entirely. Walk Vito through API key setup when ready.

### 28. Admin portal — multi-campaign UI ✅ DONE
client_detail.html now shows all campaigns in a table, Add Campaign form (collapsible), and Pause/Activate toggles per campaign. Legacy single-campaign clients display correctly.

### 29. Reply from alternate email address — smarter matching
Unknown senders currently escalated to Vito (good). Future improvement: fuzzy-match on name or domain against the prospect list to suggest likely matches. Reduces manual lookup burden at scale.

### 30. processed_ids archive cleanup policy
Archive file grows indefinitely (by design — never delete history). At 2+ years of operation, review and set a hard archive limit (e.g., keep 2 years). Not urgent — file is small, just document the policy.

---

## 🟡 Design / Polish — Added 2026-03-18

### 31. Update intake form to match argusreach.com style
The public intake form at admin.argusreach.com/intake should look like it belongs to the ArgusReach brand — not a generic internal tool. First impression matters.

**Reference:** argusreach.com — dark bg #0f0f0f, Inter font, green accent #4ade80, card borders rgba(255,255,255,0.07), clean section headers, minimal spacing.

**Priority:** Before first real client sees the form. Currently functional but visually inconsistent with the brand.
