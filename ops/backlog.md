# ArgusReach — Ops Backlog

> Active items only. Completed items removed — see git log for history.
> Last updated: 2026-03-26

---

## 🔴 Vito — Action Required

### 1. Reach out to Carter Pope (Creekside Recovery, Atlanta)
Warm intro, friend relationship — highest probability first client. Atlanta down 25% YoY.

### 2. ~~LLC filing — ArgusReach LLC~~ ✅ DONE (2026-03-25)
Filed sunbiz.org. Order ID 264427972. Pending "Active" status (1-3 business days). Check sunbiz.org for confirmation.

### 3. HelloSign — free account
Service agreement signing. Required before first paid client signs.

### ~~4. Instantly.ai — Growth upgrade~~ ✅ DONE 2026-03-26
Upgraded to Growth plan (monthly, ~$59/mo). Renews 2026-04-26. Campaign sending active.

### 5. Apollo.io — upgrade to Basic ($49/mo) when first client signs
Free tier (50 exports/mo) is insufficient for 200+ contacts/month.

### 6. Self-campaign — Complete launch
- Check `sequence_approved` box in portal
- Check `payment_confirmed` box (skip real payment — self-campaign)
- Hit Create Campaign → review DRAFT in Instantly → activate

---

## 🟡 High Value — Build When First Client Signs

### 7. Calendly — upgrade to Standard ($10/mo) when first client signs
Free tier limits scheduling features. Create per-client event type in Calendly at intake approval, paste link into approval form before approving.

### 8. Instantly open/click analytics
`GET /api/v2/analytics/campaign/summary` returns 401 on current plan. Revisit when on Growth. Scaffolding in `get_client_metrics()` ready to wire in.

### 9. M365 client support — test DKIM instructions
DKIM instructions for Microsoft 365 clients are written and in the follow-up email. Not yet tested with a real M365 client. Verify when first M365 client onboards.

---

## 🟢 Scale Features (3+ Clients)

### 10. Client-facing dashboard
Per-client read-only view: campaign stats, reply breakdown, meetings booked. Internal portal exists — client version needs separate auth + filtered data.

### 11. Clay.com — LinkedIn personalization
$149/mo. Apollo → Clay enriches → Instantly loads. We have `{{custom_intro}}` from website enrichment as a $0 alternative (~60-70% of the value). Revisit at 2-3 clients.

### 12. Monitor async processing (10+ clients)
Currently single-threaded. At 10+ active clients, one cycle could take 3-5+ minutes. Fix: thread pool per client inbox. Trigger: 8 active clients.

### 13. PostgreSQL migration
SQLite handles ~8 clients fine. Trigger: 8 active clients.

### 14. Voice calling — Argus books meetings by phone
Bland.ai / Vapi.ai — call positive replies within minutes. Trigger: 3+ clients.

### 15. Calendly API auto-detection
Auto-check "Calendar Connected" gate when client connects calendar via Calendly API. Requires Calendly OAuth or client API token. Manual checkbox fine for now.
