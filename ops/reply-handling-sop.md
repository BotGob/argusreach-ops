# SOP: Reply Handling — What Happens When a Prospect Says Yes

*This is the most important moment in the product. A hot reply means nothing if it's not handled fast and correctly.*

---

## The Golden Rule
**Respond to a positive reply within the same business day. Ideally within 2 hours.**

Interest cools fast. A prospect who said "sure, let's talk" on Tuesday morning and hears nothing until Thursday is already less interested. Speed is the product here.

---

## Step 1 — Classify the Reply

When a reply comes in, classify it immediately:

| Type | Definition | Action |
|------|-----------|--------|
| **Hot** | Expressed direct interest ("yes," "sure," "let's talk," "when are you free") | Route to client immediately |
| **Warm** | Asked a question or said "maybe" / "tell me more" | Respond to question, then offer booking |
| **Not Now** | "Busy right now," "reach out in Q3," "remind me in a few months" | Log with follow-up date, remove from sequence |
| **Negative** | "Not interested," "please remove me," "stop emailing" | Unsubscribe immediately, log as DNC |
| **OOO** | Out of office auto-reply | Note return date, resume sequence after |
| **Bounce** | Hard bounce (address invalid) | Remove from list, mark in Airtable |

---

## Step 2 — Hot Reply Flow

### 2a. Notify Client (within 1 hour)

Send client a notification via their preferred method (email, text — set during onboarding):

**Template:**

> Subject: Hot Reply — [Prospect Name] / [Company]
>
> [Client Name],
>
> Good news — [Prospect First Name] at [Company] replied to your outreach.
>
> Here's what they said:
> "[Paste exact reply]"
>
> I'd recommend responding quickly. Here's a suggested reply you can send directly:
>
> ---
> Hi [Prospect First Name],
>
> Great to hear from you. I have some availability [this week / next week] — [insert 2-3 specific time slots from client's calendar].
>
> Or feel free to grab a time directly: [Client's Calendly link]
>
> Looking forward to it.
>
> [Client Name]
> ---
>
> Let me know if you'd like me to adjust anything before you send.
>
> — ArgusReach

### 2b. Pause the Sequence

Immediately pause that prospect's sequence in whatever sending tool is being used. Do not let Touch 2 go out after a positive reply to Touch 1.

### 2c. Update Airtable

Mark prospect:
- Reply Type: Positive
- Status: Replied (+)
- Reply Notes: [summary of what they said]
- Date: [today]

### 2d. Follow Up If Client Doesn't Respond

If client hasn't replied to the hot lead notification within 4 hours during business hours, send a nudge:

> "[Client Name] — just checking you saw the note about [Prospect Name]. These go cold fast. Let me know if you want me to send the reply for you."

---

## Step 3 — Warm Reply Flow (prospect asked a question)

Draft a response on behalf of the client that:
1. Answers their question directly
2. Ends with a soft CTA to book a call

Send the draft to the client for approval before sending. If client doesn't respond within 2 hours, send the draft directly (if client has pre-authorized this).

---

## Step 4 — Not Now / Future Interest

1. Remove from current sequence immediately
2. Log in Airtable with:
   - Status: Not Now
   - Follow-up Date: [date they specified, or 60 days from now]
   - Notes: [what they said]
3. Set a calendar reminder for follow-up date
4. On follow-up date: send a single light-touch re-engagement email:

> Hi [First Name],
>
> I know timing wasn't right when we last spoke — wanted to check back in briefly.
>
> Is building a more proactive pipeline still something [Firm] is thinking about?
>
> [Name]

---

## Step 5 — Negative / Unsubscribe

1. Reply professionally if appropriate:
   > "Understood — I've removed you from our list. Sorry for the interruption."
   
   Do NOT argue, ask why, or try to re-engage.

2. Add to client's DNC list immediately
3. Mark in Airtable: DNC = true, Reply Type: Negative
4. Remove from all active sequences

---

## Step 6 — Meeting Confirmed

When a meeting is booked (via Calendly confirmation email or client reports it):

1. Update Airtable: Meeting Booked = true, Meeting Date = [date]
2. Log in client's monthly performance tracker
3. Send client a brief note:
   > "[Prospect Name] is confirmed for [date/time]. Good luck — let me know how it goes."
4. After the meeting, follow up with client:
   > "How did the call with [Prospect Name] go? Was it a fit?"
   
   Log outcome in Airtable for monthly reporting.

---

## Response Time Targets

| Reply Type | Notification to Client | Max Response Time |
|-----------|----------------------|-------------------|
| Hot | Within 1 hour | Same business day |
| Warm | Within 2 hours | Same business day |
| Not Now | Within 24 hours | Within 24 hours |
| Negative/DNC | Within 1 hour | Immediate unsubscribe |

---

## Key Principle

ArgusReach's value is not just getting replies — it's making sure those replies turn into meetings. A hot reply that dies because no one followed up is a failure. Own the entire process until the meeting is confirmed.
