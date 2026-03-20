# Client Email Setup SOP
> Last updated: 2026-03-20
> Internal reference — not sent to clients.

---

## Overview

Every client needs a **dedicated outreach email address** — a real mailbox (NOT an alias) on their primary domain, set up through Google Workspace or Microsoft 365.

**Standard setup:**
- Address: `outreach@[clientdomain].com` (or similar — first name works too)
- Provider: Google Workspace (~$6/mo) or Microsoft 365 (~$6-8/mo)
- Auth: Client generates an app password and sends it to vito@argusreach.com

**We do NOT:**
- Request admin access to their Google Workspace
- Set up subdomains (not needed — real mailbox on main domain is correct)
- Schedule screen share calls for this step

**Client does it themselves.** We send clear instructions in the welcome email.

---

## What the Client Does

1. Adds a Google Workspace or M365 seat for the outreach address (~$6-8/mo)
2. Creates the mailbox (e.g., `outreach@theirfirm.com`)
3. Generates an app password (Google: Account → Security → 2-Step → App Passwords)
4. Emails the address + app password to vito@argusreach.com

---

## What Gob Does (after receiving email + app password)

1. Links the email and app password to the client record in `clients.json`
2. Generates DNS records for their domain:

| Record | Type | Name | Value |
|--------|------|------|-------|
| SPF | TXT | `@` | `v=spf1 include:_spf.google.com ~all` (or M365 equivalent) |
| DMARC | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:vito@argusreach.com` |
| DKIM | TXT | `google._domainkey` | (pulled from Google Workspace Admin → Apps → Gmail → Authenticate email) |

3. Sends DNS records to Vito → Vito includes in follow-up email to client alongside sequence + Calendly link

---

## DNS Setup (Client's IT Handles)

Client forwards DNS records to whoever manages their domain (GoDaddy, Cloudflare, Namecheap, etc.). IT adds the records — typically 10 minutes of work.

**Common registrar paths:**
- **GoDaddy:** Login → My Products → DNS → Manage
- **Cloudflare:** Login → Select domain → DNS tab
- **Namecheap:** Login → Domain List → Manage → Advanced DNS

Gob verifies DNS propagation remotely (DNS is public) — no access needed from client.

---

## Verification

Once DNS has propagated (usually 1-4 hours), Gob verifies:
```bash
dig TXT outreach@[clientdomain].com   # SPF check
```
Or use mail-tester.com — send a test email, verify score ≥ 9/10.

---

## Connecting to Instantly

After email + DNS verified:
- Gob links the sending account via Instantly API: `PATCH /v2/campaigns/{id}` with `{"email_list": ["outreach@clientdomain.com"]}`
- Instantly connects using the app password stored in clients.json

---

## Troubleshooting

**Client can't generate app password:**
- Google: must have 2-Step Verification enabled first. Account → Security → 2-Step Verification → turn on.
- Then: Account → Security → App Passwords → select "Mail" + "Windows Computer" → Generate.
- M365: Account → Security → Additional security verification → App passwords.

**DNS not propagating:**
- Wait up to 4 hours. If still not propagating, verify records were added to the correct domain (not a subdomain).
- Use https://dnschecker.org to check from multiple locations.

**Instantly connection fails:**
- Verify app password is correct (no spaces — copy/paste exactly).
- Verify 2FA is enabled on the Google account (required for app passwords).
- Verify it's a real mailbox, not an alias.

---

## Client Communication

### Welcome email (auto-fires on intake approval)
Instructs client to: create outreach email, generate app password, send both to vito@argusreach.com.
Template location: handled by app.py `send_welcome_email()`

### Follow-up email (Vito sends manually)
Sent after client provides email + app password. Contains: DNS records, sequence draft, Calendly link.
Template: `ops/templates/followup-dns-sequence-calendly.md`
