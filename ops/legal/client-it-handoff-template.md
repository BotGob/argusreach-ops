# ArgusReach — IT Setup Handoff (Template)

> **For:** [CLIENT IT CONTACT NAME]
> **From:** Vito Resciniti, ArgusReach
> **Re:** Email infrastructure setup for outreach campaign
> **Estimated time:** 20–30 minutes

---

Hi [NAME],

[CLIENT COMPANY] is working with ArgusReach on a B2B outreach campaign. To do this properly, we need to set up a dedicated sending email address on a subdomain of your domain. This keeps your main company email completely separate and protected.

I need you to add 5 DNS records and create one new email mailbox. Everything is listed below — copy-paste ready. If anything is unclear, Vito can jump on a call with you.

---

## What We're Setting Up

A new email address: **[name]@outreach.[clientdomain].com**

This requires:
1. Adding 5 DNS records to your domain (GoDaddy / Cloudflare / wherever your DNS is managed)
2. Creating a new mailbox for that address (Google Workspace or Microsoft 365)

---

## Step 1: DNS Records to Add

Log into your DNS provider and add the following records exactly as shown. Replace `[clientdomain].com` with your actual domain.

**For GoDaddy / Namecheap / most registrars:**
Use just the subdomain part in the "Name" column (e.g. type `outreach`, not `outreach.[clientdomain].com`).

**For Cloudflare:**
Set each record to "DNS only" (grey cloud, NOT orange). Proxied records break email.

---

### Record 1 — MX (routes inbound email to your mailbox)

| Field | Value |
|-------|-------|
| Type | MX |
| Name | `outreach` |
| Value | `SMTP.GOOGLE.COM` *(or `mail.protection.outlook.com` for Microsoft 365)* |
| Priority | 1 |
| TTL | 600 (or lowest available) |

---

### Record 2 — SPF (proves we're authorized to send)

| Field | Value |
|-------|-------|
| Type | TXT |
| Name | `outreach` |
| Value | `v=spf1 include:_spf.google.com ~all` *(for Google)* |
| | `v=spf1 include:spf.protection.outlook.com ~all` *(for Microsoft 365)* |
| TTL | 600 |

---

### Record 3 — DKIM (cryptographic signature on every email)

> **Note:** You'll need to generate this from your Google Workspace or Microsoft 365 admin panel first, then add it here. Instructions below.

**For Google Workspace:**
1. Go to admin.google.com → Apps → Google Workspace → Gmail → Authenticate email
2. Select `outreach.[clientdomain].com` from the dropdown
3. Click "Generate new record" → 2048 bits → Generate
4. Copy the TXT value shown (long string starting with `v=DKIM1;k=rsa;p=...`)

**For Microsoft 365:**
1. Go to admin.microsoft.com → Settings → Domains → select your domain
2. Follow DKIM setup for the subdomain — Microsoft provides the CNAME records to add

| Field | Value |
|-------|-------|
| Type | TXT |
| Name | `google._domainkey.outreach` (Google) or per Microsoft instructions |
| Value | *(paste the long string from your admin panel)* |
| TTL | 600 |

---

### Record 4 — DMARC (policy for failed authentication)

| Field | Value |
|-------|-------|
| Type | TXT |
| Name | `_dmarc.outreach` |
| Value | `v=DMARC1; p=none; rua=mailto:vito@argusreach.com` |
| TTL | 600 |

---

## Step 2: Create the Outreach Mailbox

**Google Workspace:**
1. admin.google.com → Users → Add new user
2. First name: [CLIENT NAME], Last name: [LAST NAME]
3. Username: `[name]`, Domain: select `outreach.[clientdomain].com`
4. Set password, send credentials to Vito securely

**Microsoft 365:**
1. admin.microsoft.com → Users → Active users → Add a user
2. Username: `[name]@outreach.[clientdomain].com`
3. Set password, send credentials to Vito securely

---

## Step 3: Let Vito Know When Done

Once the DNS records are added and the mailbox is created, just reply to this email. ArgusReach will verify everything remotely (DNS records are publicly verifiable — no further access needed) and confirm within a few hours.

---

## Questions?

Vito is happy to jump on a call: [CALENDLY LINK]

Thanks for getting this done — it's the foundation for the whole campaign.

— Vito Resciniti
ArgusReach
vito@argusreach.com
