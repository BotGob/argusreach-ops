# Offline To-Do Guide
*Historical reference — initial setup guide written before ArgusReach infrastructure was live.*
*As of 2026-03-14: Apollo ✅, Airtable ✅, Anthropic API ✅, Instantly ✅ are all done. DocuSign and LLC filing still pending.*

---

## 1. Apollo.io — Free Account
**Why:** This is where we pull prospect lists. When a client signs, I search Apollo for contacts matching their ideal customer profile and export them.

**Steps:**
1. Go to **apollo.io**
2. Click "Get started for free" 
3. Sign up with **vito@argusreach.com** (use Google sign-in if offered — easier)
4. Skip any onboarding that asks you to invite teammates or install extensions
5. You're in. Don't worry about setting anything up — I'll configure it when you're back.

**Free plan gives you:** 50 verified contact exports/month. Fine for now.

---

## 2. Airtable — Free Account
**Why:** This is our CRM. Tracks clients, their prospects, campaign progress, and meeting outcomes.

**Steps:**
1. Go to **airtable.com**
2. Click "Sign up for free"
3. Sign up with **vito@argusreach.com**
4. When it asks what you want to build — skip or pick anything, doesn't matter
5. Once you're in, go to your account settings (top right, your name → Account)
6. Find **API key** or **Developer Hub** → create a personal access token with full scope
7. Copy that token and save it somewhere (Notes app, email to yourself) — I'll need it when I'm back to auto-build the CRM structure

**Free plan gives you:** Plenty for where we are.

---

## 3. Anthropic API Key
**Why:** This is what powers monitor.py — the 24/7 inbox watcher that reads prospect replies, decides if they're interested, and drafts responses. Without this key, monitor.py can't classify replies.

**Steps:**
1. Go to **console.anthropic.com**
2. Sign in or create an account (use vito@argusreach.com)
3. You'll need to add a credit card and add some credit — **$5 is more than enough to start** (monitor.py costs roughly $0.10/day)
4. Once in, click **API Keys** in the left sidebar
5. Click **Create Key** — name it "ArgusReach Monitor"
6. Copy the key immediately — it starts with `sk-ant-...` — you only see it once
7. Save it somewhere safe (Notes app, email to yourself)
8. When I'm back online, just paste it to me and I'll wire it into monitor.py in 2 minutes

---

## 4. DocuSign or HelloSign — Free Account
**Why:** When a client agrees to work with us, they need to sign a service agreement. This lets you send a digital contract they can sign on their phone or computer. Professional and legally binding.

**Which one:** Either works. HelloSign (now called **Dropbox Sign**) has a slightly more generous free tier.

**Steps (Dropbox Sign):**
1. Go to **sign.dropbox.com**
2. Click "Try for free"
3. Sign up with **vito@argusreach.com**
4. Free plan gives you **3 signature requests/month** — fine until you have paying clients
5. You don't need to set anything up now. When you have a client ready to sign, I'll walk you through it step by step.

**Alternative (DocuSign):**
1. Go to **docusign.com**
2. Click "Start free trial"
3. Sign up with vito@argusreach.com
4. Same idea — you won't use it until you have a client

---

## 5. LLC Filing — ArgusReach LLC (Florida)
**Why:** Right now you're operating as a sole proprietor. An LLC protects your personal assets if something ever goes wrong with a client, and it looks more professional on contracts. Not urgent — but do it within the next few weeks.

**Cost:** $125 filing fee to the state of Florida

**Steps:**
1. Go to **dos.fl.gov/sunbiz** (Florida's official business filing site)
2. Click **"E-File Articles of Organization"** (that's the LLC filing)
3. Fill out the form:
   - **LLC Name:** ArgusReach LLC
   - **Principal Address:** Your home address or a PO Box is fine
   - **Registered Agent:** You can be your own registered agent — just use your name and home address. (A registered agent just receives legal mail on behalf of the company.)
   - **Authorized Person:** Your name and address
4. Pay the $125 fee by credit card
5. You'll get a confirmation. Processing takes a few business days.

**After filing:**
- Go to **irs.gov** and search "EIN application" — apply for a free Employer Identification Number (EIN). Takes 5 minutes online, you get it instantly. This is like a Social Security number for your business.
- Once you have EIN: update your Stripe account from individual → business (Stripe dashboard → Settings → Business)

---

## PT Pitch Tomorrow — Quick Reminders
- Lead with questions, not features. "How are you currently getting new referral relationships?" is worth more than any slide.
- If they're interested: send them **argusreach.com/overview** after the conversation
- If they want to book a real call: **calendly.com/vito-argusreach/30min**
- Pricing when asked: $500 setup + $750/month, 3-month minimum
- Don't oversell. You're talking to a friend. Plant the seed, follow up.

---

*For current status and active tasks, see ops/backlog.md.*
