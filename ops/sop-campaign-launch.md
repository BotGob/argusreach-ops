# SOP: Campaign Launch — ArgusReach

**Trigger:** Client onboarding call complete, ICP approved, sequences approved.

---

## Step 1 — Build Prospect List (Apollo.io)

1. Log into Apollo.io
2. Navigate to **People** → **Search**
3. Apply filters from approved ICP document:
   - Job Titles (use exact match + broad variations)
   - Location (city, state, radius)
   - Company headcount
   - Industry
   - Any exclusion keywords
4. Review first 20 results manually — do they look right?
5. Export up to plan limit (Starter: 200, Growth: 500, Scale: 1,000+)
6. Download as CSV
7. Clean CSV in Google Sheets:
   - Remove obvious duplicates
   - Remove anyone with incomplete email
   - Remove anyone on client's do-not-contact list
   - Add "first name" column if not present (Instantly uses {{firstName}})

---

## Step 2 — Set Up Sending Infrastructure (Instantly.ai)

### New client (first campaign):
1. Create new workspace in Instantly for client
2. Add sending email account:
   - Preferred: subdomain of client's domain (e.g., `outreach.bayharborwealth.com`)
   - Alternative: ArgusReach subdomain (e.g., `[clientname].mail.argusreach.com`)
3. Configure DNS records:
   - SPF: `v=spf1 include:_spf.google.com ~all` (if using Gmail/Workspace)
   - DKIM: follow Instantly's domain authentication guide
   - DMARC: `v=DMARC1; p=quarantine; rua=mailto:dmarc@argusreach.com`
4. Enable **Warmup** — set to 20–30 emails/day warming, 14-day period
5. Do not launch cold outreach until warmup score ≥ 85

### Returning client (existing warmed domain):
- Skip to Step 3

---

## Step 3 — Build Campaign in Instantly

1. Create new campaign: `[ClientFirm] — [Month] — [Vertical]`
2. Import cleaned CSV of prospects — **the same CSV file must also be saved as the client's `prospects_csv` in clients.json BEFORE launch. Every lead in Instantly must exist in the CSV — the monitor uses this file to filter replies. Missing entries = missed replies.**
3. Configure sequence:
   - **Step 1** (Day 0): Initial outreach
   - **Step 2** (Day 3): Follow-up variant
   - **Step 3** (Day 7): Value-add touch
   - **Step 4** (Day 14): Final check-in
   - **Step 5** (Day 21): Soft breakup ("I'll leave the door open...")
4. Personalization variables: `{{firstName}}`, `{{company}}`, `{{customField_1}}` (trigger event)
5. Set send window: **Tue–Thu, 7–9 AM or 12–2 PM** client's time zone
6. Set daily send limit: 40–80/day depending on warmup score
7. Enable **Open Tracking** and **Reply Detection**
8. Set **Reply Action**: pause sequence on any reply (positive, negative, or OOO)

---

## Step 4 — Internal QA Before Launch

- [ ] Send test email to Go's test inbox — does it render correctly?
- [ ] Check sender name: should match client's name (e.g., "James K." not "james.k@")
- [ ] Check subject line: no ALL CAPS, no spam trigger words (free, guarantee, urgent)
- [ ] Read every touch out loud — does it sound human?
- [ ] Verify personalization tags are populating (not showing `{{firstName}}` literally)
- [ ] Check unsubscribe mechanism is present (Instantly auto-inserts footer)
- [ ] Confirm reply-to routes to monitored inbox
- [ ] Confirm all email bodies use HTML `<p style="margin-bottom:16px;">` tags — NOT plain `<p>` or plain text. The margin-bottom is required for proper spacing in Yahoo and Outlook which strip default paragraph margins.

---

## Step 4b — Run Campaign Validator (MANDATORY — NO EXCEPTIONS)

```bash
cd /home/argus/.openclaw/workspace/argusreach
python3 tools/validate_campaign.py [client_id]
```

This checks: prospect list format, required CSV columns, clients.json config, DNC conflicts, prospects_csv path, **AND cross-references every Instantly lead against the prospects.csv — any lead missing from the CSV will have their replies silently skipped by the monitor.** Do not proceed to Step 5 until this passes with zero errors.

---

## Step 5 — Client Final Approval

- Email client: "Sequences are ready for review. [Link to Google Doc with copy]"
- Wait for written go-ahead
- Do NOT launch without explicit client approval

---

## Step 6 — Launch

1. Set campaign to **Active** in Instantly
2. Note launch date and time in Airtable (client record)
3. Send launch confirmation to client:
   > "Your campaign is live. First emails are going out now. I'll send you an update in 3 days with early metrics. Any positive replies will come to you directly [via method]. Let me know if you have questions."

---

## Step 6b — Verify Sequence Timing After Activation (MANDATORY)

⚠️ Known Instantly issue: Touch 2 and Touch 3 have misfired prematurely in testing (firing within minutes instead of days). Root cause not fully confirmed — may be related to re-activation after pause, or prospect import timing.

Before considering the campaign live:
- [ ] Send a test contact through the sequence and confirm Touch 2 does NOT fire within the first hour
- [ ] Check Instantly → Campaign → Sequence steps — confirm delays show as "3 days" and "8 days" (not minutes or hours)
- [ ] If anything fires out of order → pause campaign immediately, delete test contact, investigate before re-activating with real prospects

**Until the Instantly campaign creation script is built, manual verification is required for every new campaign activation.**

---

## Step 7 — First 72 Hours Monitoring

- Check deliverability score in Instantly — should stay ≥ 80
- Watch for bounce rate > 5% → pause and clean list
- Watch for spam complaint → pause immediately, investigate sending domain
- Forward any positive replies to client same-day
- Flag any interesting "not now" replies for future re-engagement

---

## Step 8 — Ongoing Weekly

- Every Monday: pull weekly metrics (sent, opened, replied)
- Compile into client's monthly report doc
- If open rate drops below 25%: A/B test new subject line
- If positive reply rate drops below 2%: review body copy, try new angle
- Update Airtable prospect statuses: `New → Contacted → Replied → Booked → Closed (client reports)`

---

## Escalation / Problem Handling

| Problem | Action |
|---------|--------|
| Bounce rate > 10% | Pause campaign, re-clean list, check email verification |
| Spam complaint received | Pause campaign, investigate, remove domain if needed |
| Angry reply | Respond professionally: "I'm sorry to bother you — you've been removed." Remove from all sequences. |
| Client wants to pause | Pause in Instantly, confirm in writing, note pause date |
| Domain blacklisted | Stop immediately, set up new sending domain, check MX Toolbox |

---

---

## Meeting Booking Tracking

### Client Requirement — Calendly Setup (Onboarding Gate)
Every client must have a free Calendly Basic account before launch:
- One event type: "15-Minute Intro Call" (or equivalent)
- Connected to their calendar
- Link provided to ArgusReach for use in all sequences

Free Calendly Basic is sufficient for campaigns to function. Paid ($10/mo) is only needed for automated webhook tracking.

### Tracking Meetings Booked (Current Process)

ArgusReach has no automatic visibility into Calendly bookings on the client's account. Until the webhook integration is built:

**Client responsibility:** When you confirm a meeting with a prospect from ArgusReach outreach, email vito@argusreach.com with the prospect's name and email address. We will:
1. Mark them as "Meeting Booked" in Airtable
2. Add them to the DNC list so they are never contacted again
3. Include the meeting in the monthly activity summary

This prevents the risk of re-contacting someone who has already engaged.

**Why this matters:** `stop_on_reply` halts the current sequence when a prospect replies, but does not prevent re-enrollment in future sequences. Manual notification is the safeguard until the webhook is live.

### Tracking Meetings Booked (Planned — Calendly Webhook)
When Calendly paid is active:
- Calendly webhook fires on every new booking
- ArgusReach auto-updates Airtable status → "Meeting Booked"
- Prospect auto-added to DNC
- Telegram alert fires to Vito
- Zero reliance on client notification

---

**Owner:** Vito R. (ArgusReach)
**Last updated:** March 2026
