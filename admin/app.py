#!/usr/bin/env python3
"""
ArgusReach - Admin Portal (port 5056)
Internal-only. Password protected. Vito's control panel.

Routes:
  GET  /              → dashboard
  GET  /clients       → all clients
  GET  /clients/new   → intake form
  POST /clients/new   → submit intake → creates client record
  GET  /clients/<id>  → client detail
  POST /clients/<id>/dnc     → upload DNC list CSV
  POST /clients/<id>/leads   → upload + prep prospect list
  GET  /campaigns     → live campaign status
  GET  /leads/<id>    → download cleaned lead list for client
"""

import csv
import io
import json
import os
import sys
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests
try:
    import dns.resolver as _dns_resolver
    _DNS_OK = True
except ImportError:
    _DNS_OK = False

try:
    from cryptography.fernet import Fernet as _Fernet
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False
from dotenv import load_dotenv
from flask import (Flask, Response, flash, redirect, render_template,
                   request, send_file, session, url_for)

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")
sys.path.insert(0, str(BASE_DIR))

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from db.database import get_db, init_db, sync_client_from_config
from db.generate_dashboard import fetch_stats, render as render_stats_html

CLIENTS_FILE  = BASE_DIR / "monitor" / "clients.json"
CAMPAIGNS_DIR = BASE_DIR / "campaigns"
DNC_DIR       = BASE_DIR / "monitor" / "dnc"
INTAKES_FILE  = BASE_DIR / "monitor" / "intakes" / "pending.json"
INSTANTLY_KEY  = os.environ.get("INSTANTLY_API_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "argusreach2026")
_CRED_KEY      = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")

# ── CREDENTIAL ENCRYPTION HELPERS ────────────────────────────────────────────
def _encrypt_credential(plaintext: str) -> str:
    """Encrypt a credential string with Fernet. Returns ciphertext or plaintext if no key."""
    if not _CRYPTO_OK or not _CRED_KEY:
        return plaintext
    try:
        f = _Fernet(_CRED_KEY.encode())
        return f.encrypt(plaintext.encode()).decode()
    except Exception:
        return plaintext


def _decrypt_credential(value: str) -> str:
    """Decrypt a Fernet-encrypted credential. Falls back to plaintext (backward compat)."""
    if not value:
        return value
    if not _CRYPTO_OK or not _CRED_KEY:
        return value
    try:
        f = _Fernet(_CRED_KEY.encode())
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value  # already plaintext or wrong key — return as-is


def _generate_setup_token(client: dict) -> str:
    """Generate a one-time setup token, store on client dict, return token string."""
    token   = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
    client["_setup_token"]         = token
    client["_setup_token_expires"] = expires
    client["_setup_token_used"]    = False
    return token


def _setup_token_url(token: str) -> str:
    base = os.environ.get("PORTAL_BASE_URL", "https://admin.argusreach.com")
    return f"{base}/setup/{token}"


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "argusreach-admin-secret-2026")

@app.template_filter("to_et")
def to_et_filter(dt_str):
    """Convert UTC ISO timestamp to Eastern Time for display."""
    if not dt_str: return ""
    try:
        import zoneinfo
        dt = datetime.fromisoformat(str(dt_str)[:19])
        et = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(zoneinfo.ZoneInfo("America/New_York"))
        return et.strftime("%Y-%m-%d %I:%M %p ET")
    except:
        return str(dt_str)[:16]


# ── SEQUENCE GENERATOR ────────────────────────────────────────────────────────

def _generate_sequence_from_intake(client: dict) -> list:
    """Auto-generate a 3-touch email sequence using Claude AI from intake data.
    Called immediately on intake approval so Vito sees a draft when he opens the client page.
    Falls back to template-based generation if API call fails.
    """
    import os, json as _json
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    sender     = client.get("sender_name", "Vito")
    title_role = client.get("title", "Founder")
    firm       = client.get("firm_name", "")
    calendly   = client.get("calendly_link", "").strip()
    sig        = f"{sender}\n{title_role}, {firm}"
    if calendly:
        sig += f"\n{calendly}"

    if api_key:
        try:
            import anthropic as _anthropic
            aclient = _anthropic.Anthropic(api_key=api_key)

            intake_context = f"""
Firm name: {firm}
Sender name / signer: {sender}
Sender title: {title_role}
Vertical / industry: {client.get('vertical','')}
Business description: {client.get('_business_description','')}
Value proposition: {client.get('_value_prop','')}
Differentiator (what makes them different): {client.get('_differentiator','')}
Client outcomes: {client.get('_outcomes','')}
Voice sample (client's own words — use this as style guide for Touch 1): {client.get('_voice_sample','')}
Target titles: {client.get('_target_titles','')}
Target locations: {client.get('_target_locations','')}
Target company size: {client.get('_target_company_size','')}
Success story: {client.get('_success_story','')}
Common prospect objection: {client.get('_prospect_objection','')}
Tone: {client.get('tone','warm-professional')}
Desired action: {client.get('_desired_action','book_call')}
Compliance note: {client.get('compliance_note','')}
Email signature to append: {sig}
""".strip()

            prompt = f"""You are writing a 3-touch cold email outreach sequence for a client of ArgusReach, a done-for-you outbound prospecting service.

Here is everything you know about this client:

{intake_context}

Write a 3-touch cold email sequence. Rules:
- Touch 1: Short cold intro (60-80 words max). Reference {{{{companyName}}}} naturally in the opening and use {{{{city}}}} to make it feel locally relevant. If a voice sample is provided, use it as your style guide — preserve their tone and phrasing. End with a single soft CTA (quick call?). Append the email signature exactly as provided.
- Touch 2: Follow-up 5 days later. Different angle — explain the mechanism or add a specific proof point. 50-70 words. Same signature.
- Touch 3: Final short close 5 days after Touch 2. 25-35 words. Respectful, leaves door open. Same signature.
- All touches: plain text only, no markdown, no bullet points, no em dashes (use hyphens), sound like a real human wrote it, not a template
- Available personalization tags: {{{{firstName}}}}, {{{{companyName}}}}, {{{{city}}}} — use all three naturally across the 3 touches

Respond with ONLY valid JSON in this exact format, no other text:
{{
  "touches": [
    {{"subject": "...", "body": "...", "delay_days": 0}},
    {{"subject": "...", "body": "...", "delay_days": 5}},
    {{"subject": "...", "body": "...", "delay_days": 5}}
  ]
}}"""

            resp = aclient.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            data = _json.loads(raw)
            touches = data.get("touches", [])
            if len(touches) == 3:
                app.logger.info(f"✅ Claude-generated sequence for {firm}")
                return touches
        except Exception as e:
            app.logger.warning(f"⚠️  Claude sequence generation failed ({e}), falling back to template")

    # Fallback: template-based generation
    app.logger.info(f"Using template sequence for {firm}")
    voice_sample = client.get("_voice_sample", "").strip()
    differentiator = client.get("_differentiator", "").strip()

    if voice_sample and len(voice_sample) > 40:
        t1_body = (
            voice_sample
            .replace("[First Name]", "{{firstName}}")
            .replace("[Last Name]",  "{{lastName}}")
            .replace("[Company]",    "{{companyName}}")
            .replace("[City]",       "{{city}}")
        )
        if sender.lower() not in t1_body.lower():
            t1_body += f"\n\n{sig}"
    else:
        vp = client.get("_value_prop","") or "help firms like yours build a consistent pipeline of new client meetings"
        t1_body = (
            f"Hi {{{{firstName}}}},\n\n"
            f"I came across {{{{companyName}}}} and wanted to reach out directly.\n\n"
            f"We {vp} - handling the full process so your team only gets involved when someone is ready to talk.\n\n"
            f"Would a quick call this week make sense?\n\n{sig}"
        )

    t2_body = (
        f"Hi {{{{firstName}}}},\n\nFollowing up on my last note.\n\n"
        f"{differentiator or 'Wanted to make sure this did not get buried.'}\n\n"
        f"Happy to walk you through it in 15 minutes.\n\n{sig}"
    )
    t3_body = (
        f"Hi {{{{firstName}}}},\n\nI'll keep this short - I know your inbox is full.\n\n"
        f"If this ever becomes a priority, feel free to reach out anytime.\n\n{sig}"
    )
    return [
        {"subject": "Quick question, {{firstName}}",        "body": t1_body, "delay_days": 0},
        {"subject": "Re: Quick question, {{firstName}}",    "body": t2_body, "delay_days": 5},
        {"subject": "Last note - {{companyName}}",          "body": t3_body, "delay_days": 5},
    ]


# ── WELCOME EMAIL ─────────────────────────────────────────────────────────────

def _send_welcome_email(client: dict, setup_url: str = ""):
    """Send a welcome/next-steps email to a newly approved client.
    Always sends FROM vito@argusreach.com - client sending account not set up yet at this stage.
    """
    to_email = client.get("client_email", "")
    if not to_email:
        app.logger.info("Welcome email skipped - no client_email set")
        return

    contact_name = client.get("_contact_name") or client.get("firm_name", "")
    firm_name    = client.get("firm_name", "")

    # Always send from vito@argusreach.com - client outreach account not configured yet
    from_email   = "vito@argusreach.com"
    app_password = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")
    sender_name  = "Vito Resciniti | ArgusReach"

    if not app_password:
        app.logger.warning(f"Welcome email skipped - ARGUSREACH_GMAIL_APP_PASS not set in .env")
        _notify_telegram(f"⚠️ Welcome email NOT sent to {to_email} for *{firm_name}* - `ARGUSREACH_GMAIL_APP_PASS` not configured in .env. Send manually.")
        return

    first_name = contact_name.split()[0] if contact_name else "there"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<div style="max-width:580px;margin:0 auto;padding:40px 24px;">

  <div style="margin-bottom:32px;">
    <span style="font-size:14px;font-weight:800;letter-spacing:-0.02em;color:#000;">ArgusReach</span>
  </div>

  <p style="font-size:15px;line-height:1.7;margin:0 0 16px;">Hi {first_name},</p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 24px;">Welcome - we've received your intake and we're already building your prospect list and outreach sequence. We'll send you the draft sequence shortly for your review before anything goes out.</p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 24px;">In the meantime, there are a few things we need from you to get everything ready:</p>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:28px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;"><strong>1. Set up your outreach email address</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;">We send outreach on your behalf from an email address you own and control. You'll need to create a dedicated email account - something like outreach@yourdomain.com. This keeps your main inbox completely separate from campaign activity.</p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;"><strong>Important:</strong> this needs to be a real mailbox, not an email alias or forwarding address. An alias won't work - we need a full account with its own login credentials.</p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;">Create a new user/mailbox through your existing Google Workspace or Microsoft 365 account (usually $6-$8/mo for an additional user). Don't have Google Workspace or Microsoft 365 yet? Let us know and we'll point you in the right direction.</p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 16px;"><strong>One more thing:</strong> once the account is set up, go into Gmail (or Outlook) settings and disable the auto-signature. Our sequences include your name and signature already - if Gmail adds its own on top, it looks inconsistent. Takes 30 seconds: Gmail → Settings → General → Signature → set to "No signature".</p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;">Once the account is ready, submit your credentials using the secure link below - it's encrypted end-to-end and the link expires after use:</p>
    {"<p style='text-align:left;margin:16px 0;'><a href='" + setup_url + "' style='background:#000;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:700;font-size:14px;'>Submit Email Credentials Securely →</a></p><p style='font-size:12px;color:#888;margin:0;'>This link expires in 7 days and can only be used once. If it expires, just reply and we'll send a new one.</p>" if setup_url else "<p style='font-size:14px;color:#444;margin:0;'>Once ready, reply to this email with your outreach address and we'll send you a secure submission link.</p>"}
  </div>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:28px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;"><strong>2. Email authentication setup (DNS)</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0;">We can't move forward to this step until step 1 is complete. Once we have your outreach email address, we'll send you the exact DNS records to add to your domain (SPF, DKIM, DMARC) - this is what ensures your emails land in inboxes, not spam. Your IT person or whoever manages your domain can handle it in about 10 minutes.</p>
  </div>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:28px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;"><strong>3. Do-not-contact list</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;">If there are specific people or companies you never want us to contact - existing clients, partners, competitors - reply with that list and we'll make sure they're excluded before a single email goes out.</p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0;">Best way to send it: include the email domain for each company (e.g. smithlaw.com). That blocks every person at that company, not just the ones you know by name. Individual email addresses work too - a spreadsheet or CRM export is fine.</p>
  </div>

  <div style="border-left:3px solid #e5e5e5;padding-left:16px;margin-bottom:32px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;color:#888;"><strong>4. Warm leads (optional)</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#888;margin:0;">If there are people you already have a relationship with - or anyone you'd like us to prioritize - send those over and we'll move them to the front of the list.</p>
  </div>

  <p style="font-size:15px;line-height:1.7;margin:0 0 8px;">Reply to this email with any of the above and we'll take it from there. We'll be back in touch shortly with your sequence draft, DNS records, and booking link.</p>

  <div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e5e5;">
    <p style="font-size:14px;line-height:1.6;margin:0;color:#444;">Vito Resciniti<br>Founder, ArgusReach<br><a href="mailto:vito@argusreach.com" style="color:#000;">vito@argusreach.com</a></p>
  </div>

</div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"{sender_name} <{from_email}>"
        msg["To"]      = to_email
        msg["Subject"] = f"Welcome to ArgusReach - next steps for {firm_name}"
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(from_email, app_password)
            smtp.send_message(msg)

        app.logger.info(f"Welcome email sent to {to_email} for client {client.get('id')}")
        _notify_telegram(f"📧 Welcome email sent to *{to_email}* for *{firm_name}*")

    except Exception as e:
        app.logger.error(f"Welcome email FAILED for {firm_name}: {e}")
        _notify_telegram(f"⚠️ Welcome email FAILED for *{firm_name}* → {to_email}\nError: `{str(e)[:120]}`\nPlease send manually.")


def _notify_telegram(msg: str):
    """Send a Telegram notification to Vito."""
    try:
        tg_token = os.environ.get("ARGUSREACH_BOT_TOKEN", "8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk")
        tg_chat  = os.environ.get("ARGUSREACH_CHAT_ID", "-1003821840813")
        requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json={"chat_id": tg_chat, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except Exception:
        pass


# ── AUTH ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["authed"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_clients():
    with open(CLIENTS_FILE) as f:
        return json.load(f)

def save_clients(config):
    """Write clients.json (master) then sync every client record to DB.
    DB client table is kept in sync automatically - never stale.
    """
    with open(CLIENTS_FILE, "w") as f:
        json.dump(config, f, indent=2)
    # Keep DB in sync - client state lives in clients.json, DB mirrors it
    for c in config.get("clients", []):
        if not c.get("id", "").startswith("_"):
            try:
                sync_client_from_config(c)
            except Exception as e:
                app.logger.warning(f"DB sync failed for {c.get('id')}: {e}")

def get_client_by_id(client_id):
    config = load_clients()
    for c in config.get("clients", []):
        if c.get("id") == client_id:
            return c, config
    return None, config

def get_client_metrics(client_id, instantly_campaign_id=None):
    """Single source of truth for all client metrics. Use everywhere."""
    conn = get_db()
    reply_rows = conn.execute("""
        SELECT json_extract(metadata,'$.classification') as cls, COUNT(DISTINCT prospect_id) as cnt
        FROM events WHERE event_type='classified' AND client_id=?
        GROUP BY cls
    """, (client_id,)).fetchall()
    replies_sent      = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='reply_sent' AND client_id=?", (client_id,)).fetchone()[0]
    rejected          = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='draft_rejected' AND client_id=?", (client_id,)).fetchone()[0]
    meetings          = conn.execute("SELECT COUNT(*) FROM meetings WHERE client_id=?", (client_id,)).fetchone()[0]
    revenue           = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM revenue WHERE client_id=?", (client_id,)).fetchone()[0]
    prospects_tracked = conn.execute("SELECT COUNT(DISTINCT prospect_id) FROM events WHERE event_type='classified' AND client_id=?", (client_id,)).fetchone()[0]
    conn.close()

    breakdown     = {r[0]: r[1] for r in reply_rows}
    total_replies = sum(breakdown.values())
    analytics     = fetch_instantly_analytics()
    a             = analytics.get(instantly_campaign_id or "", {})
    instantly_sent = a.get("emails_sent_count", 0)
    leads          = a.get("leads_count", 0)

    # Report buckets: Interested = positive + question, Not Now = not_now, Removed = negative
    interested = breakdown.get("positive", 0) + breakdown.get("question", 0)

    return {
        "leads":             leads,
        "instantly_sent":    instantly_sent,
        "replies_sent":      replies_sent,
        "total_sent":        instantly_sent + replies_sent,
        "replies_received":  total_replies,
        # Reporting buckets (for client reports + dashboard)
        "reply_interested":  interested,
        "reply_not_now":     breakdown.get("not_now", 0),
        "reply_negative":    breakdown.get("negative", 0),
        "reply_escalated":   breakdown.get("escalated", 0),
        # Operational detail (kept for internal use)
        "reply_positive":    breakdown.get("positive", 0),
        "reply_question":    breakdown.get("question", 0),
        "replies_ignored":   rejected,
        "meetings":          meetings,
        "revenue_cents":   revenue,
        "revenue":         f"${revenue/100:,.2f}",
        "prospects_tracked": prospects_tracked,
        "reply_rate":      f"{(total_replies/leads*100):.1f}%" if leads > 0 else "—",
    }

def fetch_instantly_analytics():
    if not INSTANTLY_KEY:
        return {}
    try:
        r = requests.get("https://api.instantly.ai/api/v2/campaigns/analytics",
                         headers={"Authorization": f"Bearer {INSTANTLY_KEY}"}, timeout=10)
        return {c["campaign_id"]: c for c in r.json()} if r.ok else {}
    except:
        return {}

def validate_campaign_id(campaign_id: str) -> tuple[bool, str]:
    """
    Validate a campaign ID against the Instantly API.
    Returns (is_valid, message).
    MUST be called before saving any campaign ID to clients.json.
    """
    if not campaign_id:
        return False, "Campaign ID is empty."
    if not INSTANTLY_KEY:
        return False, "No Instantly API key configured."
    try:
        r = requests.get(
            "https://api.instantly.ai/api/v2/campaigns/analytics",
            headers={"Authorization": f"Bearer {INSTANTLY_KEY}"},
            params={"id": campaign_id},
            timeout=10
        )
        if not r.ok:
            return False, f"Instantly API error: {r.status_code}"
        data = r.json()
        if not data:
            # ID not found in analytics - double-check via campaign list
            r2 = requests.get(
                "https://api.instantly.ai/api/v2/campaigns",
                headers={"Authorization": f"Bearer {INSTANTLY_KEY}"},
                params={"limit": 100},
                timeout=10
            )
            if r2.ok:
                ids = [c["id"] for c in r2.json().get("items", [])]
                if campaign_id not in ids:
                    return False, f"Campaign ID '{campaign_id}' not found in Instantly. Valid IDs: {ids}"
        return True, "OK"
    except Exception as e:
        return False, f"Validation error: {e}"

# Public email providers - never block by domain
_PUBLIC_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","outlook.com","aol.com","icloud.com",
    "me.com","msn.com","live.com","ymail.com","protonmail.com","mail.com",
}

def load_global_dnc():
    """Load the global DNC - anyone who unsubscribed from any ArgusReach campaign ever."""
    p = DNC_DIR / "global.txt"
    if not p.exists():
        return set()
    return {line.strip().lower() for line in p.read_text().splitlines()
            if line.strip() and not line.startswith('#')}

def load_dnc(client_id):
    """Load client DNC as flat set. Entries are emails or @domain.com blocks."""
    p = DNC_DIR / f"{client_id}.txt"
    if not p.exists():
        return set()
    return {line.strip().lower() for line in p.read_text().splitlines()
            if line.strip() and not line.startswith('#')}

def is_dnc_blocked(email, dnc_set):
    """Check exact email match OR @domain.com domain-level block."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return False
    domain = "@" + email.split("@")[1]
    return email in dnc_set or domain in dnc_set

def parse_dnc_input(raw_text):
    """
    Extract DNC entries from any messy text (CRM paste, CSV, Excel copy-paste).
    Returns a list of clean entries - either emails or @domain.com domain blocks.
    Ignores names, phone numbers, and other non-email/domain content.
    Never adds public email providers as domain blocks.
    """
    import re
    entries = []
    email_re = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    domain_re = re.compile(r'^@?([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})$')

    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Extract all emails from the line first
        found_emails = email_re.findall(line)
        if found_emails:
            for e in found_emails:
                entries.append(e.lower())
        else:
            # Check if the whole line is a domain entry (@domain.com or domain.com)
            m = domain_re.match(line)
            if m:
                domain = m.group(1).lower()
                if domain not in _PUBLIC_DOMAINS:
                    entries.append("@" + domain)
    return list(dict.fromkeys(entries))  # dedupe, preserve order

def append_dnc(client_id, raw_entries):
    """Append DNC entries (emails or @domain.com) to client DNC file, deduping."""
    p = DNC_DIR / f"{client_id}.txt"
    DNC_DIR.mkdir(exist_ok=True)
    existing = load_dnc(client_id)
    new_entries = [e.lower() for e in raw_entries if e.lower() not in existing]
    with open(p, "a") as f:
        for e in new_entries:
            f.write(e + "\n")
    return len(new_entries)

def prep_leads(client_id, raw_rows, warm=False):
    """
    Clean and validate a raw lead list:
    - Normalize column names
    - Remove blanks / invalid emails
    - Dedupe within list
    - Cross-reference against DNC
    Returns (clean_rows, stats_dict)
    """
    dnc = load_dnc(client_id) | load_global_dnc()  # client DNC + global unsubscribes
    seen = set()
    clean = []
    stats = {"total": 0, "invalid": 0, "dupes": 0, "dnc_hit": 0, "clean": 0}

    email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    for row in raw_rows:
        stats["total"] += 1
        # Normalize keys
        norm = {k.lower().strip().replace(" ", "_"): v.strip() for k, v in row.items()}
        email = (norm.get("email") or norm.get("email_address") or "").strip().lower()

        if not email or not email_re.match(email):
            stats["invalid"] += 1
            continue
        if email in seen:
            stats["dupes"] += 1
            continue
        if is_dnc_blocked(email, dnc):
            stats["dnc_hit"] += 1
            continue

        seen.add(email)
        clean.append({
            "email":        email,
            "first_name":   norm.get("first_name") or norm.get("firstname") or norm.get("first") or "",
            "last_name":    norm.get("last_name") or norm.get("lastname") or norm.get("last") or "",
            "company":      norm.get("company") or norm.get("company_name") or norm.get("organization") or "",
            "title":        norm.get("title") or norm.get("job_title") or "",
            "phone":        norm.get("phone") or norm.get("phone_number") or "",
            "warm":         "yes" if warm else (norm.get("warm") or ""),
            "notes":        norm.get("notes") or norm.get("personalization") or "",
        })
        stats["clean"] += 1

    return clean, stats


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    config = load_clients()
    clients = [c for c in config.get("clients", [])
               if not c.get("id","").startswith("_") and "example" not in c.get("id","")]
    analytics = fetch_instantly_analytics()

    conn = get_db()
    total_prospects = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    total_meetings  = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    total_revenue   = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM revenue").fetchone()[0]

    # Reply breakdown by classification
    reply_rows = conn.execute("""
        SELECT json_extract(metadata,'$.classification') as cls, COUNT(DISTINCT prospect_id) as cnt
        FROM events WHERE event_type='classified'
        GROUP BY cls
    """).fetchall()
    reply_breakdown = {r[0]: r[1] for r in reply_rows}
    reply_breakdown['interested'] = reply_breakdown.get('positive', 0) + reply_breakdown.get('question', 0)
    total_replies = sum(v for k, v in reply_breakdown.items() if k != 'interested')  # avoid double count

    # Replies we sent back (approved drafts that went out)
    replies_sent_db = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='reply_sent'").fetchone()[0]

    # Drafts rejected (we chose not to respond)
    drafts_rejected = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='draft_rejected'").fetchone()[0]
    conn.close()

    client_stats = []
    for c in clients:
        m = get_client_metrics(c["id"], c.get("instantly_campaign_id",""))
        client_stats.append({
            "id":               c["id"],
            "name":             c.get("firm_name", c["id"]),
            "vertical":         c.get("vertical",""),
            "plan":             c.get("plan",""),
            "active":           c.get("active", False),
            "onboarding_status": c.get("onboarding_status", "email_setup"),
            "campaign_name":    c.get("campaign_name","—"),
            **m,
        })

    # Eastern time for display
    import zoneinfo
    eastern = zoneinfo.ZoneInfo("America/New_York")
    generated_et = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p ET")

    return render_template("dashboard.html",
        clients=client_stats,
        total_prospects=total_prospects,
        total_replies=total_replies,
        reply_breakdown=reply_breakdown,
        replies_sent_db=replies_sent_db,
        drafts_rejected=drafts_rejected,
        total_meetings=total_meetings,
        total_revenue=f"${total_revenue/100:,.2f}",
        generated=generated_et,
    )


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def client_new():
    if request.method == "POST":
        f = request.form
        client_id = re.sub(r'[^a-z0-9_]', '_', f["id"].lower().strip())

        config = load_clients()
        existing_ids = [c.get("id") for c in config.get("clients",[])]
        if client_id in existing_ids:
            flash(f"Client ID '{client_id}' already exists.", "error")
            return render_template("client_new.html", form=f)

        new_client = {
            "id": client_id,
            "active": False,
            "mode": "draft_approval",
            "firm_name": f["firm_name"].strip(),
            "vertical": f["vertical"].strip(),
            "plan": f["plan"].strip(),
            "outreach_email": f["outreach_email"].strip(),
            "app_password": f.get("app_password","").strip(),
            "sender_name": f["sender_name"].strip(),
            "title": f.get("title","Founder").strip(),
            "client_email": f.get("client_email","").strip(),
            "calendly_link": f.get("calendly_link","").strip(),
            "instantly_campaign_id": "",
            "campaign_name": "",
            "contacts_per_month": int(f.get("contacts_per_month", 200)),
            "launch_date": "",
            "icp_summary": f.get("icp_summary","").strip(),
            "tone": f.get("tone","warm-professional").strip(),
            "compliance_note": f.get("compliance_note","").strip(),
            "positioning_note": f.get("positioning_note","").strip(),
            "prospects_csv": f"campaigns/{client_id}/prospects.csv",
        }

        config["clients"].append(new_client)
        save_clients(config)

        # Create campaign dir + empty DNC
        (CAMPAIGNS_DIR / client_id).mkdir(parents=True, exist_ok=True)
        (DNC_DIR / f"{client_id}.txt").touch()

        # Register in DB
        init_db()
        sync_client_from_config(new_client)

        flash(f"Client '{new_client['firm_name']}' created successfully.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    return render_template("client_new.html", form={})


@app.route("/clients/<client_id>")
@login_required
def client_detail(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    dnc = load_dnc(client_id)
    prospects_csv = BASE_DIR / client.get("prospects_csv", f"campaigns/{client_id}/prospects.csv")
    lead_count = 0
    if prospects_csv.exists():
        with open(prospects_csv) as f:
            lead_count = sum(1 for _ in csv.DictReader(f))

    conn = get_db()
    events = conn.execute("""
        SELECT e.created_at, e.event_type, e.metadata, p.email
        FROM events e LEFT JOIN prospects p ON p.id=e.prospect_id
        WHERE e.client_id=? ORDER BY e.created_at DESC LIMIT 20
    """, (client_id,)).fetchall()
    conn.close()

    metrics = get_client_metrics(client_id, client.get("instantly_campaign_id",""))

    # Load connection status from monitor
    conn_status_file = BASE_DIR / "monitor" / "logs" / "connection_status.json"
    conn_status = {}
    if conn_status_file.exists():
        try:
            conn_status = json.loads(conn_status_file.read_text()).get(client_id, {})
        except Exception:
            pass

    return render_template("client_detail.html",
        client=client,
        dnc_count=len(dnc),
        lead_count=lead_count,
        metrics=metrics,
        events=[dict(e) for e in events],
        conn_status=conn_status,
    )


@app.route("/clients/<client_id>/sequence", methods=["POST"])
@login_required
def save_sequence(client_id):
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))
    f = request.form
    client["sequence"] = [
        {"subject": f.get("t1_subject","").strip(), "body": f.get("t1_body","").strip(), "delay_days": 0},
        {"subject": f.get("t2_subject","").strip(), "body": f.get("t2_body","").strip(), "delay_days": int(f.get("t2_delay", 5))},
        {"subject": f.get("t3_subject","").strip(), "body": f.get("t3_body","").strip(), "delay_days": int(f.get("t3_delay", 5))},
    ]
    client["schedule"] = {
        "timezone": "America/New_York",
        "start_hour": int(f.get("start_hour", 8)),
        "end_hour":   int(f.get("end_hour", 17)),
        "send_days":  f.getlist("send_days") or ["monday","tuesday","wednesday","thursday","friday"],
    }
    save_clients(config)
    flash("Sequence and schedule saved.", "success")
    return redirect(url_for("client_detail", client_id=client_id))

@app.route("/clients/<client_id>/checklist", methods=["POST"])
@login_required
def save_checklist(client_id):
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        return ("not found", 404)
    f = request.form
    client["checklist"] = {
        "icp_reviewed":       f.get("icp_reviewed") == "1",
        "dns_verified":       f.get("dns_verified") == "1",
        "warmup_complete":    f.get("warmup_complete") == "1",
        "payment_confirmed":  f.get("payment_confirmed") == "1",
        "sequence_approved":  f.get("sequence_approved") == "1",
        "calendar_connected": f.get("calendar_connected") == "1",
    }
    save_clients(config)
    return ("ok", 200)

@app.route("/clients/<client_id>/go-live", methods=["POST"])
@login_required
def client_go_live(client_id):
    """Mark campaign as live after Vito has activated it in Instantly."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        return ("not found", 404)
    missing = []
    if not client.get("instantly_campaign_id"):
        missing.append("campaign ID (run Launch first)")
    if not client.get("calendly_link", "").strip():
        missing.append("booking link (set in Monitor & Client Settings)")
    if not client.get("outreach_email", "").strip():
        missing.append("outreach email address")
    if not client.get("app_password", "").strip():
        missing.append("email app password")
    if missing:
        flash(f"❌ Cannot go live — missing: {', '.join(missing)}", "error")
        return redirect(url_for("client_detail", client_id=client_id))
    client["active"] = True
    client["onboarding_status"] = None
    if not client.get("launch_date"):
        import zoneinfo
        client["launch_date"] = datetime.now(zoneinfo.ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    save_clients(config)
    # Notify via Telegram
    notify(f"🟢 *{client.get('firm_name')}* is now LIVE - monitor is watching, campaign active.")
    flash(f"✅ {client.get('firm_name')} is live. Monitor is now watching for replies.", "success")
    return redirect(url_for("client_detail", client_id=client_id))

@app.route("/clients/<client_id>/status", methods=["POST"])
@login_required
def client_status_update(client_id):
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        return ("not found", 404)
    client["onboarding_status"] = request.form.get("onboarding_status", client.get("onboarding_status","email_setup"))
    save_clients(config)
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/offboard", methods=["POST"])
@login_required
def client_offboard(client_id):
    """Pause campaign and mark client as offboarding. Does NOT delete data."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    client["active"]            = False
    client["onboarding_status"] = "offboarding"
    import zoneinfo
    client["offboard_date"] = datetime.now(zoneinfo.ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    save_clients(config)

    firm = client.get("firm_name", client_id)
    notify(
        f"⛔ *{firm}* marked for offboarding.\n\n"
        f"Manual steps:\n"
        f"1. Pause/deactivate campaign in Instantly\n"
        f"2. Cancel Stripe subscription\n"
        f"3. Send final monthly report\n"
        f"4. Remove email account from Instantly\n"
        f"5. Confirm with client all outreach has stopped"
    )
    flash(f"⛔ {firm} paused. Offboarding checklist sent to Telegram.", "success")
    return redirect(url_for("client_detail", client_id=client_id))

@app.route("/clients/<client_id>/update", methods=["POST"])
@login_required
def client_update(client_id):
    """Update campaign ID, activate/deactivate, set launch date - the fields that connect a client to Instantly."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.form
    if "instantly_campaign_id" in f:
        new_cid = f["instantly_campaign_id"].strip()
        if new_cid and new_cid != client.get("instantly_campaign_id", ""):
            valid, msg = validate_campaign_id(new_cid)
            if not valid:
                flash(f"❌ Campaign ID rejected - {msg}", "error")
                return redirect(url_for("client_detail", client_id=client_id))
        client["instantly_campaign_id"] = new_cid
    if "campaign_name" in f:
        client["campaign_name"] = f["campaign_name"].strip()
    if "launch_date" in f:
        client["launch_date"] = f["launch_date"].strip()
    if "active" in f:
        client["active"] = f["active"] == "true"
    if "calendly_event_slug" in f:
        client["calendly_event_slug"] = f["calendly_event_slug"].strip()
    if "calendly_link" in f:
        client["calendly_link"] = f["calendly_link"].strip()

    save_clients(config)
    flash("Client updated.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/campaigns/add", methods=["POST"])
@login_required
def campaign_add(client_id):
    """Add a new campaign to a client's campaigns array."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.form
    new_campaign = {
        "instantly_campaign_id": f.get("instantly_campaign_id", "").strip(),
        "campaign_name":         f.get("campaign_name", "").strip(),
        "prospects_csv":         f.get("prospects_csv", "").strip(),
        "launch_date":           f.get("launch_date", "").strip(),
        "active":                True,
    }
    if not new_campaign["instantly_campaign_id"]:
        flash("Campaign ID required.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    valid, msg = validate_campaign_id(new_campaign["instantly_campaign_id"])
    if not valid:
        flash(f"❌ Campaign ID rejected - {msg}", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    if "campaigns" not in client:
        client["campaigns"] = []
    client["campaigns"].append(new_campaign)

    # Also update legacy fields to match most recent campaign
    client["instantly_campaign_id"] = new_campaign["instantly_campaign_id"]
    client["campaign_name"]         = new_campaign["campaign_name"]
    client["launch_date"]           = new_campaign["launch_date"]

    save_clients(config)
    flash(f"Campaign '{new_campaign['campaign_name'] or new_campaign['instantly_campaign_id']}' added.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/campaigns/<campaign_id>/toggle", methods=["POST"])
@login_required
def campaign_toggle(client_id, campaign_id):
    """Activate or pause a specific campaign for a client."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client or "campaigns" not in client:
        flash("Client or campaigns not found.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    for c in client["campaigns"]:
        if c.get("instantly_campaign_id") == campaign_id:
            c["active"] = not c.get("active", True)
            status = "activated" if c["active"] else "paused"
            flash(f"Campaign {status}.", "success")
            break

    save_clients(config)
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/launch", methods=["POST"])
@login_required
def campaign_launch(client_id):
    """
    Launch a new campaign for a client.
    Runs: Apollo → DNC filter → NeverBounce (if key exists) → create Instantly campaign (DRAFT) → load leads → notify Vito.
    Runs in background thread so the portal stays responsive. Progress streamed via /clients/<id>/launch/status.
    """
    import threading, io, sys
    from datetime import datetime
    import zoneinfo

    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    month = request.form.get("month", "").strip()
    limit = int(request.form.get("limit", 200))
    skip_verify = not bool(os.environ.get("NEVERBOUNCE_API_KEY", ""))

    if not month:
        flash("Month is required.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    # Store log in a file so we can stream it
    log_path = BASE_DIR / "monitor" / "logs" / f"launch_{client_id}.log"
    log_path.write_text(f"[{datetime.now(zoneinfo.ZoneInfo('America/New_York')).strftime('%I:%M %p ET')}] Starting campaign launch for {client.get('firm_name')} - {month}\n")

    def run_in_background():
        try:
            sys.path.insert(0, str(BASE_DIR / "tools"))
            import monthly_cycle as mc
            import importlib
            importlib.reload(mc)  # ensure fresh state

            # Patch notify to write to log instead of Telegram (Telegram still fires from within mc)
            orig_stdout = sys.stdout
            sys.stdout = open(log_path, "a")

            mc.run_cycle(
                client_id=client_id,
                month_name=month,
                dry_run=False,
                skip_apollo=False,
                skip_verify=skip_verify,
            )
            sys.stdout.close()
            sys.stdout = orig_stdout

            with open(log_path, "a") as f:
                f.write(f"\n✅ DONE - Campaign created as DRAFT in Instantly. Review sequence and leads, then activate.\n")
                f.write("__COMPLETE__\n")
        except Exception as e:
            with open(log_path, "a") as f:
                f.write(f"\n❌ ERROR: {e}\n")
                f.write("__COMPLETE__\n")

    t = threading.Thread(target=run_in_background, daemon=True)
    t.start()

    flash(f"Campaign launch started for {month}. Building leads and creating campaign now - check progress below.", "success")
    return redirect(url_for("client_detail", client_id=client_id) + "?launch=1")


@app.route("/clients/<client_id>/launch/log")
@login_required
def campaign_launch_log(client_id):
    """Return current launch log as plain text for live polling."""
    log_path = BASE_DIR / "monitor" / "logs" / f"launch_{client_id}.log"
    if not log_path.exists():
        return "No launch in progress.", 200
    return log_path.read_text(), 200, {"Content-Type": "text/plain"}


@app.route("/clients/<client_id>/auto-check")
@login_required
def auto_check_gates(client_id):
    """Auto-verify DNS records and Instantly warmup score. Updates checklist in-place."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        return (json.dumps({"error": "Client not found"}), 404, {"Content-Type": "application/json"})

    outreach_email = client.get("outreach_email", "").strip()
    email_provider = client.get("_email_provider", "google")
    results = {}

    # ── DNS CHECK ────────────────────────────────────────────────────────────
    dns_result = {"spf": False, "dmarc": False, "dkim": False,
                  "spf_record": "", "dmarc_record": "", "dkim_record": "",
                  "error": None}

    if not outreach_email:
        dns_result["error"] = "No outreach email set — add it in Monitor & Client Settings first."
    elif not _DNS_OK:
        dns_result["error"] = "dnspython not installed on server."
    else:
        domain = outreach_email.split("@")[-1].lower()
        try:
            # SPF
            for r in _dns_resolver.resolve(domain, "TXT"):
                txt = r.to_text().strip('"')
                if txt.startswith("v=spf1"):
                    dns_result["spf"] = True
                    dns_result["spf_record"] = txt
                    break
        except Exception:
            pass
        try:
            # DMARC
            for r in _dns_resolver.resolve(f"_dmarc.{domain}", "TXT"):
                txt = r.to_text().strip('"')
                if "v=DMARC1" in txt:
                    dns_result["dmarc"] = True
                    dns_result["dmarc_record"] = txt
                    break
        except Exception:
            pass
        # DKIM — try provider-specific selectors first, then common fallbacks
        provider_selectors = {
            "google":    ["google"],
            "microsoft": ["selector1", "selector2"],
        }
        selectors = provider_selectors.get(email_provider, []) + ["google", "selector1", "selector2", "mail", "default", "dkim"]
        seen = []
        for sel in selectors:
            if sel in seen:
                continue
            seen.append(sel)
            try:
                for r in _dns_resolver.resolve(f"{sel}._domainkey.{domain}", "TXT"):
                    txt = r.to_text().strip('"')
                    if "v=DKIM1" in txt or "k=rsa" in txt or "p=" in txt:
                        dns_result["dkim"] = True
                        dns_result["dkim_record"] = (txt[:80] + "...") if len(txt) > 80 else txt
                        dns_result["dkim_selector"] = sel
                        break
                if dns_result["dkim"]:
                    break
            except Exception:
                continue

    dns_pass = dns_result["spf"] and dns_result["dmarc"] and dns_result["dkim"]
    results["dns"] = dns_result
    results["dns_pass"] = dns_pass

    # ── WARMUP CHECK ─────────────────────────────────────────────────────────
    warmup_result = {"score": None, "error": None, "status": None}
    warmup_pass = False

    if not outreach_email:
        warmup_result["error"] = "No outreach email set."
    elif not INSTANTLY_KEY:
        warmup_result["error"] = "INSTANTLY_API_KEY not configured."
    else:
        try:
            r = requests.get(
                "https://api.instantly.ai/api/v2/accounts",
                headers={"Authorization": f"Bearer {INSTANTLY_KEY}"},
                params={"limit": 100},
                timeout=10
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            matched = next((a for a in items if a.get("email", "").lower() == outreach_email.lower()), None)
            if matched:
                # warmup_score may be at top level or nested in warmup object
                score = matched.get("warmup_score")
                if score is None:
                    score = (matched.get("warmup") or {}).get("score")
                if score is None:
                    score = (matched.get("warmup") or {}).get("warmup_score")
                warmup_result["score"] = score
                warmup_result["status"] = matched.get("status")
                warmup_pass = score is not None and int(score) >= 85
            else:
                warmup_result["error"] = f"Account {outreach_email} not found in Instantly — has it been added?"
        except Exception as e:
            warmup_result["error"] = str(e)[:120]

    results["warmup"] = warmup_result
    results["warmup_pass"] = warmup_pass

    # ── AUTO-UPDATE CHECKLIST ─────────────────────────────────────────────────
    checklist = client.setdefault("checklist", {})
    import zoneinfo as _zi
    now_str = datetime.now(_zi.ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")

    if dns_pass:
        checklist["dns_verified"] = True
    if warmup_pass:
        checklist["warmup_complete"] = True

    # Store last check metadata on client record
    client["_gate_check"] = {
        "checked_at": now_str,
        "dns_pass": dns_pass,
        "warmup_score": warmup_result.get("score"),
        "warmup_pass": warmup_pass,
    }
    save_clients(config)

    results["checklist_updated"] = {
        "dns_verified": checklist.get("dns_verified", False),
        "warmup_complete": checklist.get("warmup_complete", False),
    }
    results["checked_at"] = now_str

    return (json.dumps(results), 200, {"Content-Type": "application/json"})


@app.route("/clients/<client_id>/dns-records")
@login_required
def dns_records(client_id):
    """Generate ready-to-paste DNS instructions block for the client's IT person."""
    client, _ = get_client_by_id(client_id)
    if not client:
        return ("Client not found", 404)

    outreach_email  = client.get("outreach_email", "")
    email_provider  = client.get("_email_provider", "google")
    domain          = outreach_email.split("@")[-1] if "@" in outreach_email else "[outreach-domain.com]"

    if email_provider == "microsoft":
        spf_include = "include:spf.protection.outlook.com"
        dkim_instructions = (
            "1. Sign in to Microsoft 365 Admin Center\n"
            "2. Go to Settings → Domains → select your domain\n"
            "3. Under DNS records, find the DKIM records (two CNAME records)\n"
            "4. Add both to your DNS, then enable DKIM in the Microsoft 365 Defender portal"
        )
    else:
        spf_include = "include:_spf.google.com"
        dkim_instructions = (
            "1. Sign in to Google Workspace Admin (admin.google.com)\n"
            "2. Go to Apps → Google Workspace → Gmail → Authenticate email\n"
            "3. Select your domain and click 'Generate new record'\n"
            "4. Copy the TXT record name and value, add to your DNS"
        )

    block = f"""DNS Records for {domain}
======================================
Add these records to your domain DNS ({domain}).
Your IT person or domain registrar (GoDaddy, Cloudflare, Namecheap, etc.) can do this in ~10 minutes.

──────────────────────────────────────
1. SPF — TXT record
   Name:  @  (or your root domain)
   Value: v=spf1 {spf_include} include:_spf.mlsend.com ~all

   Note: If you already have an SPF record, ADD the include values to it — don't create a second SPF record.

──────────────────────────────────────
2. DKIM — generated by your email provider
   {dkim_instructions}

──────────────────────────────────────
3. DMARC — TXT record
   Name:  _dmarc.{domain}
   Value: v=DMARC1; p=none; rua=mailto:vito@argusreach.com

──────────────────────────────────────

Once added, DNS propagates in a few hours. We verify everything on our end — no action needed from you after that.
Questions? Reply to this email and we'll walk you through it.
"""
    return block, 200, {"Content-Type": "text/plain"}


@app.route("/clients/<client_id>/payment-link", methods=["POST"])
@login_required
def generate_payment_link(client_id):
    """Generate a unique per-client Stripe subscription payment link with client_id in metadata."""
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    plan = client.get("plan", "starter")

    # Price IDs for recurring subscription products — set in .env after creating products in Stripe
    price_ids = {
        "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
        "growth":  os.environ.get("STRIPE_PRICE_GROWTH", ""),
        "scale":   os.environ.get("STRIPE_PRICE_SCALE", ""),
    }
    price_id = price_ids.get(plan, "")

    if not price_id:
        flash(
            f"❌ Stripe price ID for '{plan}' plan not configured. "
            f"Add STRIPE_PRICE_{plan.upper()} to monitor/.env after creating subscription products in Stripe.",
            "error"
        )
        return redirect(url_for("client_detail", client_id=client_id))

    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        flash("❌ STRIPE_SECRET_KEY not configured.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    try:
        import stripe as _stripe
        _stripe.api_key = stripe_key

        link = _stripe.PaymentLink.create(
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"client_id": client_id, "plan": plan, "firm_name": client.get("firm_name", "")},
            subscription_data={
                "metadata": {"client_id": client_id, "plan": plan}
            },
            after_completion={
                "type": "redirect",
                "redirect": {"url": "https://argusreach.com"}
            },
        )
        payment_url = link.url
        # Save to client record so it appears in the portal
        config = load_clients()
        for c in config["clients"]:
            if c.get("id") == client_id:
                c["stripe_payment_link"] = payment_url
                break
        save_clients(config)
        flash(f"✅ Payment link generated: {payment_url}", "success")
        app.logger.info(f"Stripe payment link created for {client_id}: {payment_url}")
    except Exception as e:
        flash(f"❌ Stripe error: {e}", "error")

    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/dnc", methods=["POST"])
@login_required
def upload_dnc(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.files.get("dnc_file")
    if not f:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    content = f.read().decode("utf-8", errors="ignore")
    # parse_dnc_input handles CSV, plain text, messy CRM paste, emails, and @domain.com entries
    entries = parse_dnc_input(content)
    added = append_dnc(client_id, entries)
    flash(f"DNC list imported: {added} new entries added ({len(entries)-added} already on list).", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/leads", methods=["POST"])
@login_required
def upload_leads(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.files.get("leads_file")
    warm = request.form.get("warm") == "yes"
    if not f or not f.filename:
        flash("No file selected. Please choose a CSV file before uploading.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    try:
        content = f.read().decode("utf-8", errors="ignore")
    except Exception as e:
        flash(f"Could not read file: {e}", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    if not content.strip():
        flash("File is empty.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    try:
        reader = csv.DictReader(io.StringIO(content))
        raw_rows = list(reader)
    except Exception as e:
        flash(f"Could not parse CSV: {e}. Make sure it's a valid CSV file.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    clean_rows, stats = prep_leads(client_id, raw_rows, warm=warm)

    # Save clean CSV
    out_dir = CAMPAIGNS_DIR / client_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "prospects.csv"

    # If file exists, append (keeping header)
    existing = []
    if out_path.exists():
        with open(out_path) as ef:
            existing = list(csv.DictReader(ef))
        existing_emails = {r["email"].lower() for r in existing}
        clean_rows = [r for r in clean_rows if r["email"] not in existing_emails]

    all_rows = existing + clean_rows
    fields = ["email","first_name","last_name","company","title","phone","warm","notes"]
    with open(out_path, "w", newline="") as of:
        writer = csv.DictWriter(of, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    msg = (f"Lead prep complete: {stats['total']} uploaded → "
           f"{stats['clean']} clean · {stats['dupes']} dupes · "
           f"{stats['dnc_hit']} DNC hits · {stats['invalid']} invalid. "
           f"prospects.csv now has {len(all_rows)} total leads.")
    flash(msg, "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/leads/<client_id>/download")
@login_required
def download_leads(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))
    path = BASE_DIR / client.get("prospects_csv", f"campaigns/{client_id}/prospects.csv")
    if not path.exists():
        flash("No leads file found.", "error")
        return redirect(url_for("client_detail", client_id=client_id))
    return send_file(path, as_attachment=True,
                     download_name=f"{client_id}_prospects_{datetime.now().strftime('%Y%m%d')}.csv")


@app.route("/campaigns")
@login_required
def campaigns():
    config = load_clients()
    clients = [c for c in config.get("clients",[])
               if not c.get("id","").startswith("_") and "example" not in c.get("id","")]
    analytics = fetch_instantly_analytics()

    rows = []
    registered_ids = set()
    for c in clients:
        cid = c.get("instantly_campaign_id","")
        a = analytics.get(cid, {})
        instantly_status = {0:"DRAFT",1:"ACTIVE",2:"COMPLETED"}.get(a.get("campaign_status",-1),"—")
        registered_ids.add(cid)
        m = get_client_metrics(c["id"], cid)
        rows.append({
            "client_id":        c["id"],
            "firm":             c.get("firm_name",""),
            "campaign_id":      cid,
            "campaign_name":    c.get("campaign_name","—"),
            "client_active":    c.get("active", False),
            "instantly_status": instantly_status,
            "mismatch":         (c.get("active") and instantly_status != "ACTIVE") or
                                (not c.get("active") and instantly_status == "ACTIVE"),
            **m,
        })

    # Unregistered campaigns - pull live list and cross-reference
    unregistered = []
    live_campaign_ids = set()
    try:
        r = requests.get("https://api.instantly.ai/api/v2/campaigns",
                         headers={"Authorization": f"Bearer {INSTANTLY_KEY}"},
                         params={"limit": 100}, timeout=10)
        if r.ok:
            live_campaigns = r.json().get("items", [])
            live_campaign_ids = {c["id"] for c in live_campaigns}
            for camp in live_campaigns:
                if camp.get("id") not in registered_ids:
                    unregistered.append({
                        "id": camp.get("id",""),
                        "name": camp.get("name",""),
                        "status": {0:"DRAFT",1:"ACTIVE",2:"COMPLETED"}.get(camp.get("status",-1),"UNKNOWN"),
                        "created": (camp.get("timestamp_created","") or "")[:10],
                    })
    except:
        pass

    # Flag any rows where campaign ID doesn't exist in Instantly at all
    for row in rows:
        if row["campaign_id"] and live_campaign_ids and row["campaign_id"] not in live_campaign_ids:
            row["id_invalid"] = True
            row["mismatch"] = True
        else:
            row["id_invalid"] = False

    return render_template("campaigns.html", rows=rows, unregistered=unregistered)


@app.route("/pipeline")
@login_required
def pipeline():
    conn = get_db()
    stages = ["added","emailed","replied","replied_by_us","meeting_booked","closed_won","closed_lost","unsubscribed"]
    
    config = load_clients()
    clients = [c for c in config.get("clients",[]) if not c.get("id","").startswith("_") and "example" not in c.get("id","")]
    
    data = []
    for client in clients:
        cid = client["id"]
        stage_counts = {}
        for row in conn.execute("SELECT stage, COUNT(*) as cnt FROM prospects WHERE client_id=? GROUP BY stage", (cid,)):
            stage_counts[row["stage"]] = row["cnt"]
        
        recent = conn.execute("""
            SELECT e.created_at, e.event_type, e.metadata, p.email, p.first_name, p.company
            FROM events e LEFT JOIN prospects p ON p.id=e.prospect_id
            WHERE e.client_id=? ORDER BY e.created_at DESC LIMIT 10
        """, (cid,)).fetchall()
        
        data.append({
            "id": cid,
            "name": client.get("firm_name", cid),
            "stage_counts": stage_counts,
            "total": sum(stage_counts.values()),
            "recent": [dict(r) for r in recent],
        })
    conn.close()
    return render_template("pipeline.html", data=data, stages=stages)


@app.route("/stats")
@login_required
def stats():
    return render_template("stats.html")


@app.route("/stats/data")
@login_required
def stats_data():
    """Generate and serve the stats dashboard fresh on every page load."""
    try:
        stats = fetch_stats()
        html  = render_stats_html(stats)
        return html, 200, {"Content-Type": "text/html"}
    except Exception as e:
        return f"<p style='color:#fff;font-family:sans-serif;padding:40px'>Stats error: {e}</p>", 500


@app.route("/flowchart")
@login_required
def flowchart():
    return render_template("flowchart.html")


@app.route("/flowchart/data")
@login_required
def flowchart_data():
    """Serve the flowchart HTML directly from the server."""
    path = BASE_DIR / "ops" / "master-flowchart.html"
    if path.exists():
        return path.read_text(), 200, {"Content-Type": "text/html"}
    return "<p>Flowchart not found.</p>", 404


@app.route("/backlog")
@login_required
def backlog():
    backlog_path = BASE_DIR / "ops" / "backlog.md"
    content = backlog_path.read_text() if backlog_path.exists() else "No backlog file found."
    return render_template("backlog.html", content=content)


@app.route("/reports")
@login_required
def reports_list():
    reports_dir = BASE_DIR / "reports"
    files = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.html"), reverse=True):
            files.append({"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")})
    return render_template("reports.html", files=files)


@app.route("/reports/<filename>")
@login_required
def view_report(filename):
    reports_dir = BASE_DIR / "reports"
    path = reports_dir / filename
    if not path.exists() or not path.suffix == ".html":
        flash("Report not found.", "error")
        return redirect(url_for("reports_list"))
    return path.read_text()


def load_intakes():
    if not INTAKES_FILE.exists():
        return []
    return json.loads(INTAKES_FILE.read_text())

def save_intakes(data):
    INTAKES_FILE.write_text(json.dumps(data, indent=2))

# ── PUBLIC CLIENT INTAKE FORM (no login required) ────────────────────────────
@app.route("/intake", methods=["GET", "POST"])
def intake():
    if request.method == "POST":
        f = request.form
        submission = {
            "id":                   datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "submitted_at":         datetime.utcnow().isoformat(),
            "status":               "pending",
            # Identity
            "firm_name":            f.get("firm_name","").strip(),
            "contact_name":         f.get("contact_name","").strip(),
            "contact_title":        f.get("contact_title","").strip(),
            "contact_email":        f.get("contact_email","").strip(),
            "contact_phone":        f.get("contact_phone","").strip(),
            "business_address":     f.get("business_address","").strip(),
            "website":              f.get("website","").strip(),
            "vertical":             (f.get("vertical_other","").strip() if f.get("vertical","").strip() == "Other" else f.get("vertical","").strip()),
            # What they do
            "business_description": f.get("business_description","").strip(),
            "differentiator":       f.get("differentiator","").strip(),
            "outcomes":             f.get("outcomes","").strip(),
            "best_referral_sources":f.get("best_referral_sources","").strip(),
            "prior_outreach":       f.get("prior_outreach","").strip(),
            # Targeting
            "plan":                 f.get("plan","starter").strip(),
            "meeting_format":       ",".join(request.form.getlist("meeting_format")),
            "office_address":       f.get("office_address","").strip(),
            "success_story":        f.get("success_story","").strip(),
            "prospect_objection":   f.get("prospect_objection","").strip(),
            "target_locations":     f.get("target_locations","").strip(),
            "target_titles":        f.get("target_titles","").strip(),
            "target_company_type":  f.get("target_company_type","").strip(),
            "target_industry":      ",".join(request.form.getlist("target_industry")),
            "target_seniority":     ",".join(request.form.getlist("target_seniority")),
            "target_company_size":  ",".join(request.form.getlist("target_company_size")),
            "monthly_capacity":     f.get("monthly_capacity","").strip(),
            "dnc_notes":            f.get("dnc_notes","").strip(),
            "dnc_emails":           f.get("dnc_emails","").strip(),
            "icp_summary":          f.get("icp_summary","").strip(),
            # Voice & message
            "value_prop":           f.get("value_prop","").strip(),
            "voice_sample":         f.get("voice_sample","").strip(),
            "tone":                 f.get("tone","warm-professional").strip(),
            "compliance_note":      f.get("compliance_note","").strip(),
            # Campaign
            "calendar_type":        f.get("calendar_type","google").strip(),
            "desired_action":       f.get("desired_action","book_call").strip(),
            "has_existing_list":    f.get("has_existing_list","no").strip(),
            # Meta
            "how_heard":            f.get("how_heard","").strip(),
            "notes":                f.get("notes","").strip(),
        }
        intakes = load_intakes()
        intakes.append(submission)
        save_intakes(intakes)

        # Notify Vito via Telegram
        _notify_telegram(
            f"📋 *New Client Intake Submitted*\n\n"
            f"*{submission['firm_name']}*\n"
            f"{submission['contact_name']} · {submission['contact_email']}\n"
            f"Vertical: {submission['vertical']}\n\n"
            f"Review at: https://admin.argusreach.com/intakes"
        )

        # PRG pattern — redirect to GET so browser reload doesn't resubmit
        return redirect(url_for("intake_thanks", name=submission["contact_name"]))

    return render_template("intake_form.html")

@app.route("/intake/thanks")
def intake_thanks():
    name = request.args.get("name", "")
    return render_template("intake_thanks.html", name=name)


# ── SECURE CREDENTIAL SETUP (public — token IS the auth) ─────────────────────

@app.route("/setup/<token>", methods=["GET", "POST"])
def setup_credentials(token):
    """Client-facing secure credential submission. No login needed — token is the auth."""
    config  = load_clients()
    client  = next((c for c in config["clients"]
                    if c.get("_setup_token") == token and not c.get("_setup_token_used")), None)

    # Token not found or already used
    if not client:
        # Check if it was already used (so we can show a different message)
        used_client = next((c for c in config["clients"]
                            if c.get("_setup_token") == token and c.get("_setup_token_used")), None)
        if used_client:
            return render_template("setup_credential.html",
                                   state="used", firm=used_client.get("firm_name", ""))
        return render_template("setup_credential.html", state="invalid", firm="")

    # Check expiry
    import zoneinfo as _zi
    expires_str = client.get("_setup_token_expires", "")
    if expires_str:
        try:
            expires_dt = datetime.fromisoformat(expires_str)
            if datetime.utcnow() > expires_dt:
                return render_template("setup_credential.html",
                                       state="expired", firm=client.get("firm_name", ""))
        except Exception:
            pass

    firm       = client.get("firm_name", "")
    contact    = client.get("_contact_name", firm)
    first_name = contact.split()[0] if contact else "there"
    provider   = client.get("_email_provider", "google")

    if request.method == "GET":
        return render_template("setup_credential.html",
                               state="form", firm=firm, first_name=first_name,
                               provider=provider, token=token)

    # POST — process submission
    outreach_email = request.form.get("outreach_email", "").strip().lower()
    app_password   = request.form.get("app_password", "").strip()
    confirm_pass   = request.form.get("confirm_password", "").strip()

    errors = []
    if not outreach_email or "@" not in outreach_email:
        errors.append("Please enter a valid email address.")
    if not app_password:
        errors.append("App password is required.")
    if app_password and confirm_pass and app_password != confirm_pass:
        errors.append("App passwords don't match — please re-enter.")

    if errors:
        return render_template("setup_credential.html",
                               state="form", firm=firm, first_name=first_name,
                               provider=provider, token=token, errors=errors,
                               outreach_email=outreach_email)

    # Save encrypted credentials
    client["outreach_email"]       = outreach_email
    client["app_password"]         = _encrypt_credential(app_password)
    client["_setup_token_used"]    = True
    import zoneinfo as _zi2
    client["_credentials_received_at"] = datetime.now(_zi2.ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M %p ET")

    # Advance onboarding status
    if client.get("onboarding_status") in (None, "email_setup"):
        client["onboarding_status"] = "dns_pending"

    save_clients(config)

    # Alert Vito
    _notify_telegram(
        f"🔐 *{firm}* submitted email credentials securely\n"
        f"📧 Outreach email: `{outreach_email}`\n"
        f"→ Generate DNS records + send follow-up email when ready"
    )

    app.logger.info(f"Credentials received for {client['id']} via secure form ({outreach_email})")
    return render_template("setup_credential.html", state="success", firm=firm, first_name=first_name)


@app.route("/clients/<client_id>/resend-setup-link", methods=["POST"])
@login_required
def resend_setup_link(client_id):
    """Regenerate setup token and resend email to client."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    to_email = client.get("client_email", "")
    if not to_email:
        flash("No client email on file — update it in settings first.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    # Generate fresh token
    token = _generate_setup_token(client)
    save_clients(config)

    # Send email
    setup_url  = _setup_token_url(token)
    firm_name  = client.get("firm_name", "")
    contact    = client.get("_contact_name", firm_name)
    first_name = contact.split()[0] if contact else "there"
    app_pass   = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")

    if app_pass:
        try:
            html = f"""<!DOCTYPE html><html><body style="font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;max-width:580px;margin:0 auto;padding:40px 24px;">
<div style="margin-bottom:32px;"><span style="font-size:14px;font-weight:800;">ArgusReach</span></div>
<p style="font-size:15px;line-height:1.7;">Hi {first_name},</p>
<p style="font-size:15px;line-height:1.7;">Here's a new secure link to submit your outreach email credentials. The previous link has been deactivated.</p>
<p style="font-size:15px;line-height:1.7;">This link expires in 7 days and can only be used once:</p>
<p style="text-align:center;margin:32px 0;">
  <a href="{setup_url}" style="background:#000;color:#fff;padding:14px 28px;border-radius:6px;text-decoration:none;font-weight:700;font-size:15px;">Submit Credentials Securely →</a>
</p>
<p style="font-size:13px;color:#666;">Or copy this link: {setup_url}</p>
<div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e5e5;">
<p style="font-size:14px;color:#444;margin:0;">Vito Resciniti<br>Founder, ArgusReach<br>vito@argusreach.com</p>
</div></body></html>"""
            msg = MIMEMultipart("alternative")
            msg["From"]    = "Vito Resciniti | ArgusReach <vito@argusreach.com>"
            msg["To"]      = to_email
            msg["Subject"] = f"New credential setup link — {firm_name}"
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
                smtp.login("vito@argusreach.com", app_pass)
                smtp.send_message(msg)
            flash(f"✅ New setup link sent to {to_email}. Valid for 7 days.", "success")
            _notify_telegram(f"🔗 New setup link sent to *{firm_name}* ({to_email})")
        except Exception as e:
            flash(f"Token regenerated but email failed: {e}", "error")
            _notify_telegram(f"🔗 Setup token regenerated for *{firm_name}* — email failed, send manually:\n{setup_url}")
    else:
        flash("Token regenerated. ARGUSREACH_GMAIL_APP_PASS not set — send link manually.", "error")
        _notify_telegram(f"🔗 Setup token regenerated for *{firm_name}* — send manually:\n`{setup_url}`")

    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/intakes")
@login_required
def intakes_list():
    intakes = load_intakes()
    pending = [i for i in intakes if i.get("status") == "pending"]
    return render_template("intakes_list.html", intakes=pending, all_intakes=intakes)


@app.route("/intakes/<intake_id>/approve", methods=["GET", "POST"])
@login_required
def intake_approve(intake_id):
    intakes = load_intakes()
    intake = next((i for i in intakes if i["id"] == intake_id), None)
    if not intake:
        flash("Intake not found.", "error")
        return redirect(url_for("intakes_list"))

    if request.method == "POST":
        f = request.form
        client_id = re.sub(r'[^a-z0-9_]', '_', f["id"].lower().strip())
        config = load_clients()
        existing_ids = [c.get("id") for c in config.get("clients", [])]
        if client_id in existing_ids:
            flash(f"Client ID '{client_id}' already exists.", "error")
            return render_template("intake_approve.html", intake=intake, form=f)

        plan = f.get("plan","starter")
        contacts_map = {"starter": 200, "growth": 500, "scale": 1000}
        new_client = {
            "id":                    client_id,
            "active":                False,
            "onboarding_status":     "email_setup",
            "mode":                  "draft_approval",
            "firm_name":             intake["firm_name"],
            "vertical":              intake["vertical"],
            "plan":                  plan,
            "outreach_email":        f.get("outreach_email","vito@argusreach.com").strip(),
            "sender_name":           f.get("sender_name","Vito Resciniti").strip(),
            "title":                 f.get("title","Founder").strip(),
            "client_email":          intake["contact_email"],
            "calendly_link":         intake.get("calendly_link","").strip(),
            "instantly_campaign_id": "",
            "campaign_name":         "",
            "contacts_per_month":    int(f.get("contacts_per_month", contacts_map.get(plan, 200))),
            "launch_date":           "",
            "icp_summary":           intake.get("icp_summary",""),
            "tone":                  intake.get("tone","warm-professional"),
            "compliance_note":       intake.get("compliance_note",""),
            "positioning_note":      f.get("positioning_note",""),
            "prospects_csv":         f"campaigns/{client_id}/prospects.csv",
            # Full intake context - used by monthly_cycle.py for Apollo search + sequence writing
            "_intake_id":            intake_id,
            "_contact_name":         intake.get("contact_name",""),
            "_contact_title":        intake.get("contact_title",""),
            "plan":                   intake.get("plan","starter"),
            "_meeting_format":        intake.get("meeting_format","any"),
            "_office_address":        intake.get("office_address",""),
            "_success_story":        intake.get("success_story",""),
            "_prospect_objection":   intake.get("prospect_objection",""),
            "_target_locations":     intake.get("target_locations",""),
            "_target_titles":        intake.get("target_titles",""),
            "_target_company_type":  intake.get("target_company_type",""),
            "_monthly_capacity":     intake.get("monthly_capacity",""),
            "_value_prop":           intake.get("value_prop",""),
            "_differentiator":       intake.get("differentiator",""),
            "_outcomes":             intake.get("outcomes",""),
            "_best_referral_sources":intake.get("best_referral_sources",""),
            "_voice_sample":         intake.get("voice_sample",""),
            "_business_description": intake.get("business_description",""),
            "_prior_outreach":       intake.get("prior_outreach",""),
            "_dnc_notes":            intake.get("dnc_notes",""),
            "_dnc_emails":           intake.get("dnc_emails",""),
            "_target_industry":      intake.get("target_industry",""),
            "_target_seniority":     intake.get("target_seniority",""),
            "_target_company_size":  intake.get("target_company_size",""),
            "_desired_action":       intake.get("desired_action","book_call"),
            "_has_existing_list":    intake.get("has_existing_list","no"),
            "_website":              intake.get("website",""),
            "_email_provider":       intake.get("email_provider","google"),
            "_dns_provider":         intake.get("dns_provider",""),
            # Sequence (written by Gob, reviewed + approved by Vito before launch)
            "sequence": [
                {"subject": "", "body": "", "delay_days": 0},
                {"subject": "", "body": "", "delay_days": 5},
                {"subject": "", "body": "", "delay_days": 5},
            ],
            # Campaign schedule
            "schedule": {
                "timezone": "America/New_York",
                "start_hour": 8,
                "end_hour": 17,
                "send_days": ["monday","tuesday","wednesday","thursday","friday"],
            },
            # Pre-launch checklist state (persists between sessions)
            "checklist": {
                "icp_reviewed":       False,
                "dns_verified":       False,
                "warmup_complete":    False,
                "payment_confirmed":  False,
                "sequence_approved":  False,
                "calendar_connected": False,
            },
        }

        # Auto-generate sequence from intake data — visible immediately when Vito opens client
        new_client["sequence"] = _generate_sequence_from_intake(new_client)

        config["clients"].append(new_client)
        save_clients(config)
        (CAMPAIGNS_DIR / client_id).mkdir(parents=True, exist_ok=True)
        (DNC_DIR / f"{client_id}.txt").touch()
        init_db()
        sync_client_from_config(new_client)

        # Auto-load DNC emails/domains from intake using smart parser
        dnc_raw = intake.get("dnc_emails", "")
        if dnc_raw.strip():
            dnc_entries = parse_dnc_input(dnc_raw)
            if dnc_entries:
                append_dnc(client_id, dnc_entries)
                app.logger.info(f"Auto-loaded {len(dnc_entries)} DNC entries from intake for {client_id}")

        # Mark intake as approved
        for i in intakes:
            if i["id"] == intake_id:
                i["status"] = "approved"
                i["client_id"] = client_id
        save_intakes(intakes)

        # Generate secure credential setup token + send welcome email
        try:
            setup_token = _generate_setup_token(new_client)
            save_clients(config)  # persist token to clients.json
            setup_url   = _setup_token_url(setup_token)
            _send_welcome_email(new_client, setup_url=setup_url)
        except Exception as _we:
            app.logger.warning(f"Welcome email failed (non-fatal): {_we}")

        flash(f"Client '{new_client['firm_name']}' created from intake.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    return render_template("intake_approve.html", intake=intake, form={})


@app.route("/meetings/log", methods=["POST"])
@login_required
def log_meeting():
    """Manually log a meeting booking - for client-confirmed meetings that didn't come through webhook."""
    f = request.form
    client_id    = f.get("client_id","").strip()
    prospect_email = f.get("prospect_email","").strip()
    prospect_name  = f.get("prospect_name","").strip()
    meeting_date   = f.get("meeting_date","").strip()
    notes          = f.get("notes","").strip()

    if not client_id or not prospect_email:
        flash("Client and prospect email required.", "error")
        return redirect(url_for("dashboard"))

    import hashlib
    meeting_id = hashlib.md5(f"{client_id}:{prospect_email}:{meeting_date}".encode()).hexdigest()[:16]
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO meetings (id, client_id, prospect_email, prospect_name, meeting_date, status, source, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (meeting_id, client_id, prospect_email, prospect_name, meeting_date, 'confirmed', 'manual', notes, datetime.utcnow().isoformat()))
    conn.commit()

    # Update prospect stage
    pid = conn.execute("SELECT id FROM prospects WHERE client_id=? AND email=?", (client_id, prospect_email)).fetchone()
    if pid:
        conn.execute("UPDATE prospects SET stage='meeting_booked' WHERE id=?", (pid[0],))
        conn.commit()
    conn.close()

    flash(f"Meeting logged for {prospect_name or prospect_email}.", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/intakes/<intake_id>/dismiss", methods=["POST"])
@login_required
def intake_dismiss(intake_id):
    intakes = load_intakes()
    for i in intakes:
        if i["id"] == intake_id:
            i["status"] = "dismissed"
    save_intakes(intakes)
    flash("Intake dismissed.", "success")
    return redirect(url_for("intakes_list"))


@app.route("/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    init_db()
    print("🚀 ArgusReach Admin Portal starting on port 5056...")
    app.run(host="0.0.0.0", port=5056, debug=False)
