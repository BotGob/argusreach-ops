# ArgusReach — Client Onboarding Checklist

## Pre-Intake (internal)
- [ ] Post-call email sent (service agreement link + $500 Stripe link + intake form link)
- [ ] Confirm payment cleared + agreement signed before approving intake
- [ ] Approve intake in portal → welcome email auto-fires
- [ ] Research client's firm: website, LinkedIn, recent news, reviews

---

## Onboarding Process (async — no call required)

### 1. Business Overview (10 min)
- What does [CLIENT] do, exactly — services, specialties, differentiators?
- Who are their 3 best current clients? What do those clients have in common?
- What's their average deal size / LTV per client?
- What does a "qualified lead" mean to them? What disqualifies someone?

### 2. ICP Refinement (15 min)
- Titles: Who is the decision-maker?
- Company size: Revenue, headcount, geography?
- Trigger events: What signals mean someone is ready to buy?
- Pain points: What are prospects usually struggling with before they find [CLIENT]?
- Exclusions: Competitors, current clients, do-not-contact lists?

### 3. Voice & Tone (10 min)
- How do they communicate in real life? Formal, conversational, direct?
- Any words or phrases they always use — or never use?
- Are there compliance sensitivities? (important for RIAs)
- What does a good "warm intro" sound like in their world?
- Review 2–3 sample email drafts together — adjust on call if needed

### 4. Logistics (10 min)
- Sending identity: Name, email, firm name that will appear on outreach
- **Calendly setup (Gob handles — required before launch):**
  - Gob creates event types based on meeting formats from intake (phone / video / in-person their office / in-person my office)
  - All types live under one booking page: `calendly.com/argusreach/[client-slug]`
  - Client connects their Google or Outlook calendar (~2 min — sent in follow-up email)
  - Prospect picks meeting type + time. Booking lands on client's calendar with full context.
- Communication preference: how do they want to receive hot replies?
  - [ ] Email forward
  - [ ] Telegram/text notification
  - [ ] Slack
- Reporting preference: client receives monthly activity summary by email (standard)
- Do-not-contact list: upload any existing contacts we must skip

---

## After Email + App Password Received (Gob's work)
- [ ] Link outreach email + app password to client record in clients.json
- [ ] Link sending account in Instantly → enable warmup immediately
- [ ] Send Vito warmup kickoff email text to forward to client (template: ops/templates/warmup-kickoff-email.md)
- [ ] Generate DNS records for client domain
- [ ] Run Apollo → DNC → NeverBounce → load leads into Instantly DRAFT
- [ ] Write 3-touch sequence → load into Instantly DRAFT
- [ ] Create Calendly event types based on meeting formats from intake
- [ ] Vito sends follow-up email: DNS records + sequence draft + Calendly link

## Pre-Launch Gates (Vito confirms all before activating)
- [ ] DNS propagated and verified (SPF/DKIM/DMARC passing)
- [ ] Warmup score ≥ 85% in Instantly (~2-3 weeks)
- [ ] Sequence approved by client
- [ ] Calendar connected by client
- [ ] Subscription payment received → Vito sends ready-to-launch email (template: ops/templates/ready-to-launch-email.md)
- [ ] Payment confirmed in Stripe → Vito checks all boxes in portal pre-launch checklist → activates in Instantly
- [ ] Tells Gob → Gob sets active: true in clients.json

## Post-Call (internal, within 24 hours)

### Relationships & DNC Collection
- [ ] Ask client: "Who do you already have a relationship with in your target market?" (existing referral partners, colleagues, current patients/clients, anyone they know personally in the space)
- [ ] Client sends relationships list — can be a CSV or even a plain list of emails/names. Save to `clients/[client_id]/relationships.csv`
- [ ] Ask client for any explicit do-not-contact names (competitors, past bad experiences, etc.). Save to `dnc/[client_id].txt`
- [ ] Confirm: any warm contacts they want us to REACH OUT TO (not exclude) go in the warm contacts list — these are separate from relationships

### ICP Document
- [ ] Finalize ICP in writing and send to client for approval
- [ ] Confirm geographic radius and any sub-markets
- [ ] Build Apollo.io search with confirmed filters
- [ ] Export first batch (200–1,000 based on plan) — verify quality

### Email Sequences
- [ ] Write 3-touch sequence (one version per touch — no A/B testing)
- [ ] Apply client voice — read against their LinkedIn posts, website copy
- [ ] Compliance review (especially for RIAs): no performance promises
- [ ] Send sequence drafts to client via email for review
- [ ] Allow 48 hours for feedback; incorporate revisions

### Deduplication (run before personalization)
- [ ] Run `dedupe.py` against the raw prospect list:
  ```
  python3 argusreach/tools/dedupe.py \
    --prospects exports/[client]-prospects.csv \
    --output exports/[client]-deduped.csv \
    --relationships clients/[client_id]/relationships.csv \
    --dnc dnc/[client_id].txt
  ```
- [ ] Review the removal log (`-removed-log.csv`) — send to client so they can see what was protected
- [ ] Confirm clean count is still sufficient for the plan volume. If too many removed, discuss with client.

### AI Personalization
- [ ] Run `personalize.py` on the **deduped** list (not the raw Apollo export):
  ```
  python argusreach/tools/personalize.py \
    --input exports/[client]-prospects.csv \
    --output exports/[client]-enriched.csv \
    --client "[2-3 sentence description of client and what they do]" \
    --limit 10
  ```
- [ ] Review the 10 test outputs — check tone, accuracy, naturalness
- [ ] If approved, run full list (remove `--limit` flag)
- [ ] For compliance-sensitive clients (RIAs): send sample of 10-15 custom_opening values to their compliance team for spot-check approval
- [ ] Confirm `{{custom_opening}}` is mapped correctly in the email sequence template

### Infrastructure
- [ ] Client sends outreach email address + app password → Gob links to client record in clients.json
- [ ] Gob generates DNS records (SPF, DKIM, DMARC) for client's domain
- [ ] DNS records sent to client in follow-up email alongside sequence + Calendly link
- [ ] Set up tracking (open/click) in Instantly
- [ ] Load enriched CSV (with custom_opening) into Instantly campaign

### Launch
- [ ] Client approval received in writing (email is fine)
- [ ] Campaign set to launch Monday or Tuesday (avoid Friday launches)
- [ ] Notify client: "First emails go out [DATE] — you'll receive a summary by [DATE+3]"
- [ ] Monitor first 48 hours for any deliverability issues or bounce spikes

---

## Week 1 Monitor
- [ ] Verify first emails sent in Instantly — confirm delivery, no bounce spike
- [ ] Monitor replies.json — flag any positive replies to Vito immediately (same day)
- [ ] Confirm Calendly link in sequence is correct and working
- [ ] Check Telegram alerts group — confirm monitor is firing notifications correctly
- [ ] Note: no client-facing check-in in Week 1 — next client touchpoint is monthly report

---

## Monthly Reporting

Use `tools/monthly_report.py` — do not use this template manually.

```
python3 tools/monthly_report.py --client [client_id] --month "Month YYYY"
```

Report includes: contacts reached, positive replies, not-now, meetings booked, unsubscribes, campaign history table, what worked, what's changing. Sent automatically to client_email in clients.json.

---

## Offboarding (end of engagement or cancellation)

- [ ] Final performance report delivered
- [ ] All prospect data returned to client or deleted per their preference
- [ ] Sending domain decommissioned or transferred
- [ ] DocuSign termination agreement executed
- [ ] Case study offer made (anonymous, with client permission)
