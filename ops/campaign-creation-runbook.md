# Campaign Creation Runbook
> Last updated: 2026-03-20. This is the exact process, in order. No steps are optional.

---

## What Gob Does (Automated)

### Step 1 — Intake Approved
When Vito approves an intake in the portal:
- `clients.json` entry created (`active: false`)
- DB client record created
- Welcome email sent to client
- Prospect CSV path reserved: `campaigns/<client_id>/prospects.csv`

### Step 2 — Sequence Written
Gob writes a 3-touch plain text sequence from intake data:
- Touch 1 (delay: 0 days) — cold intro, hook based on ICP pain
- Touch 2 (delay: 3 days) — soft follow-up, social proof
- Touch 3 (delay: 7 days) — final note, low pressure

All plain text. No HTML. No Calendly link (test) or with Calendly link (live).

### Step 3 — Instantly Campaign Created via API
```
POST https://api.instantly.ai/api/v2/campaigns
```
Payload includes:
- `name`: `ArgusReach — {firm_name} — {Month Year}`
- `campaign_schedule`: business hours, Mon-Fri, timezone `America/Detroit` (Eastern)
- `stop_on_reply`: true
- `sequences`: full 3-step sequence embedded
- Status: **DRAFT (0)** — never activated by code

### Step 4 — Sending Account Linked via API
```
PATCH https://api.instantly.ai/api/v2/campaigns/{campaign_id}
body: {"email_list": ["outreach@clientdomain.com"]}
```
**NOT** `POST /campaigns/{id}/mailaccounts` — that endpoint is deprecated/404.

### Step 5 — Leads Loaded via API
```
POST https://api.instantly.ai/api/v2/leads  (one at a time, or batch)
body: {"campaign": campaign_id, "email": ..., "first_name": ..., "last_name": ..., "company_name": ..., "skip_if_in_workspace": false}
```
**NOT** `/api/v2/leads/batch` — that endpoint is 404. Use individual POST per lead.
DNC check runs before loading.

### Step 6 — clients.json Updated
- `instantly_campaign_id` set
- `campaign_name` set
- `launch_date` set
- `active` stays `false`

### Step 7 — Vito Notified via Telegram
Alert sent: "Campaign ready in Instantly — review sequence and delays, then activate."

---

## What Vito Does (Manual, in Instantly)

### Step 8 — Review in Instantly
1. Open campaign in Instantly UI
2. Review sequence copy (all 3 touches)
3. Confirm email delays are days not minutes
4. Confirm sending account is correct
5. Review any sending limits

### Step 9 — Activate
Hit the activate/launch button in Instantly. That's it.

---

## What Gob Does After Activation

### Step 10 — Update clients.json
Once Vito confirms it's live:
- Set `active: true` in clients.json
- Monitor picks up the client on next cycle

---

## Known API Quirks (as of 2026-03-20)

| What | Correct endpoint | Broken endpoint |
|------|-----------------|-----------------|
| Link sending account | `PATCH /v2/campaigns/{id}` with `email_list` | `POST /v2/campaigns/{id}/mailaccounts` (404) |
| Load leads | `POST /v2/leads` (individual) | `POST /v2/leads/batch` (404) |
| Timezone in schedule | `"America/Detroit"` | `"America/New_York"` (rejected) |

---

## For Tests (No Apollo)
Skip Steps: Apollo pull, NeverBounce validation.
Instead: manually provide prospect CSV or list of test emails.
Everything else runs identically.
