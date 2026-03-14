# ArgusReach — Client Email Infrastructure SOP

> **Owner:** Gob (COO)
> **Last updated:** 2026-03-14
> **Purpose:** Step-by-step process to set up a client's outreach email infrastructure before any campaign goes live. This happens during onboarding, after contract is signed and setup fee is paid.

---

## Quick-Start Reference Card

**Total time:** ~45-60 minutes (you + client on screen share)
**What you need before starting:**
- Access to client's **Google Workspace Admin** (admin.google.com) — ask client to either grant admin access or be on screen share
- Access to client's **domain registrar** (GoDaddy, Namecheap, etc.) — same as above
- Their primary business email (for Reply-To and DMARC reports)
- Instantly account open in another tab

**The 5 things you're doing:**
1. Add `outreach.[clientdomain].com` as a **Secondary Domain** in Google Workspace
2. Add 4 DNS records in GoDaddy: Verification TXT, MX, DKIM, SPF, DMARC
3. Create the outreach email user in Google Workspace
4. Connect it to Instantly and turn on warmup
5. Add the client to `monitor/clients.json` and restart the monitor

**Biggest mistakes to avoid:**
- ❌ "User alias domain" instead of "Secondary domain" — won't work, can't create a mailbox
- ❌ Skipping warmup — cold domain = spam folder guaranteed
- ❌ Launching campaign before warmup hits 85%+ health score in Instantly
- ❌ Forgetting to approve Instantly as trusted app in Google Workspace before connecting

**Full step-by-step below.**

---

## Overview

Every client needs a **dedicated outreach email address** on a subdomain of their primary domain. This protects their main business inbox from cold email deliverability issues.

**Standard setup:**  
- Outreach address: `[name]@outreach.[clientdomain].com`  
- Example: `james@outreach.mycompany.com`

**Timeline:** 2–3 weeks from setup to first send (warmup period)

---

## What the Client Needs to Do First

These require client action — cannot be done for them without admin credentials:

1. **Purchase an additional Google Workspace seat** (~$7–12/month, billed to client)
   - This is non-negotiable. The outreach address must be a real standalone mailbox.
   - Clients should budget this as part of their monthly cost.
   - If they push back: "This is standard practice for cold outreach. It keeps your main inbox clean and protects your domain reputation."

2. **Grant ArgusReach admin access to their Google Workspace** (preferred)  
   OR be available for a 30-min screen share onboarding call

> **Delivery model decision (Vito):** Default to requesting admin access. It's faster and lets Gob do everything. If client is uncomfortable, offer the guided screen share instead.

---

## Step-by-Step Setup (with admin access)

### Step 1: Add Outreach Subdomain in Google Workspace

1. Go to `admin.google.com` → **Account → Domains → Manage Domains**
2. Click **"Add a domain"**
3. Enter: `outreach.[clientdomain].com`
4. Select: **"Secondary domain"** ← (NOT "User alias domain")
5. Click "Add domain & start verification"

> ⚠️ Must be Secondary domain, not User alias domain. Alias domains can't have standalone mailboxes — Instantly requires real credentials.

### Step 2: Verify Domain Ownership in GoDaddy (or wherever DNS is managed)

Google will show a TXT record for verification. Add it in DNS:
- **Type:** TXT
- **Name:** `outreach` (or `@` if subdomain-level)
- **Value:** (provided by Google)
- **TTL:** lowest available

Return to Google Workspace and click "Verify."

### Step 3: Activate Gmail for the Subdomain

1. In Domains list, click **"Activate Gmail"** next to `outreach.[clientdomain].com`
2. Select the DNS provider (GoDaddy, Cloudflare, etc.)
3. Add the MX record:
   - **Type:** MX
   - **Name:** `outreach`
   - **Priority:** 1
   - **Value:** `SMTP.GOOGLE.COM`
   - **TTL:** lowest available

### Step 4: Set Up DKIM

1. In Google Workspace Admin → **Apps → Google Workspace → Gmail → Authenticate email**
2. Select `outreach.[clientdomain].com` from domain dropdown
3. Click **"Generate new record"** → leave at 2048 bits → **"Generate"**
4. Add the TXT record in DNS:
   - **Type:** TXT
   - **Name:** `google._domainkey.outreach`
   - **Value:** (long string starting with `v=DKIM1;k=rsa;p=...`)
5. Return to Google Workspace → click **"Start authentication"**
6. Wait for DKIM status to show "Authenticating email" ✅

### Step 5: Add SPF Record

In DNS:
- **Type:** TXT
- **Name:** `outreach`
- **Value:** `v=spf1 include:_spf.google.com ~all`
- **TTL:** lowest available

### Step 6: Add DMARC Record

In DNS:
- **Type:** TXT
- **Name:** `_dmarc.outreach`
- **Value:** `v=DMARC1; p=none; rua=mailto:vito@argusreach.com`
- **TTL:** lowest available

> Note: `rua` is where DMARC reports go. Use `vito@argusreach.com` to receive them centrally. After 30 days, consider changing `p=none` to `p=quarantine` once reputation is established.

### Step 7: Create the Outreach Mailbox

1. Google Workspace Admin → **Users → Add new user**
2. Fill in:
   - **First name:** [Client's first name or their brand name]
   - **Last name:** [Last name]
   - **Username:** `[name]` (just the local part)
   - **Domain:** select `outreach.[clientdomain].com` from dropdown
3. Set a strong password → save it securely (share with Vito via secure method)
4. Save the user

### Step 8: Connect to Instantly

> ⚠️ First-time setup: Instantly may need to be approved as a trusted app in Google Workspace Admin → Security → API Controls → Manage Third-Party App Access

1. Log into `app.instantly.ai` → **Accounts → Add New**
2. Under "Connect existing accounts" → **Google / Gmail / G-Suite**
3. Sign in with the new outreach mailbox credentials
4. Once connected, configure the account:
   - **Sender name:** [Client's full name]
   - **Reply-to:** [Client's primary business email]
   - **Daily campaign limit:** 20 (ramp up over time)
   - **Enable warmup:** ON
   - **Daily warmup limit:** 20
   - **Warmup reply rate:** 30–45%

### Step 9: Begin Warmup Period

- Warmup runs automatically in the background
- **Do not launch any real campaigns during warmup**
- Warmup duration: **minimum 2–3 weeks**
- Monitor the Instantly account health score — should climb toward 90%+ before going live
- Alert client: "Your outreach infrastructure is live and warming. We'll be ready to send in ~3 weeks."

---

## DNS Records Summary Checklist

| Record | Type | Name | Value |
|--------|------|------|-------|
| Domain verification | TXT | `outreach` | (Google-provided) |
| MX (Gmail routing) | MX | `outreach` | `SMTP.GOOGLE.COM` (priority 1) |
| DKIM | TXT | `google._domainkey.outreach` | `v=DKIM1;k=rsa;p=...` |
| SPF | TXT | `outreach` | `v=spf1 include:_spf.google.com ~all` |
| DMARC | TXT | `_dmarc.outreach` | `v=DMARC1; p=none; rua=mailto:vito@argusreach.com` |

---

## Verification Before Campaign Launch

Run this checklist before any cold email fires:

- [ ] DKIM status: "Authenticating" ✅ in Google Workspace
- [ ] MX, SPF, DMARC added in DNS ✅
- [ ] Instantly account connected and health score > 85% ✅
- [ ] Warmup running for minimum 2 weeks ✅
- [ ] Test email sent from outreach address → received in primary inbox (not spam) ✅
- [ ] Prospect list loaded in Airtable ✅
- [ ] Email sequences approved by client ✅
- [ ] clients.json entry created in monitor ✅
- [ ] `campaigns/[client_id]/prospects.csv` created with all prospect emails ✅ (required for prospect filter — monitor ignores all replies until this exists)

---

## Troubleshooting

**"User alias domain" vs "Secondary domain" confusion:**  
Always use Secondary domain for client outreach. User alias domains cannot create standalone mailboxes and won't work with Instantly authentication.

**Instantly can't connect via Google OAuth:**  
First approve Instantly as a trusted app in Google Workspace Admin → Security → API controls → Manage Third-Party App Access. Search by Client ID (shown on Instantly's connection screen).

**Warmup score stagnating below 70%:**  
Check that warmup is enabled and daily warmup limit > 10. Low score may indicate DNS issues — verify SPF/DKIM/DMARC are all passing using mail-tester.com.

**Client doesn't want to share admin credentials:**  
Schedule a 30-min screen share call. Walk them through each step using this SOP as the script. Budget ~30 min for the DNS/Google Workspace steps.

---

## Client Communication Templates

### After contract signed, before onboarding call:
> "Welcome aboard! Before we dive into your campaign, we need to set up a dedicated sending address for your outreach. You'll need to add one Google Workspace seat (~$7–12/mo) — I'll walk you through everything. Can you give me admin access to your Google Workspace? Or I can walk you through it on a quick screen share — 30 minutes tops."

### When warmup starts:
> "Your outreach infrastructure is live! We've set up [name]@outreach.[domain].com with full authentication (SPF, DKIM, DMARC) and it's now warming up in Instantly. We'll be ready to send in about 2–3 weeks. I'll update you when we're cleared to launch."

### When warmup is complete and ready to launch:
> "Good news — your sender reputation is looking strong. We're cleared to start sending. Your first batch will go out [date]. Daily volume starts at 20 emails and ramps up over time."
