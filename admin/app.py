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
UPLOADS_DIR   = BASE_DIR / "monitor" / "intakes" / "uploads"
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

# Session timeout — expire after 1 hour of inactivity
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

@app.before_request
def make_session_permanent():
    """Make session permanent so PERMANENT_SESSION_LIFETIME applies."""
    session.permanent = True

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
- Touch 1: Short cold intro (60-80 words max, not counting {{{{custom_intro}}}}). Touch 1 must start with {{{{custom_intro}}}} on its own line followed by a blank line. This variable will be populated at send time with a personalized opener based on the prospect's company. Write Touch 1 assuming {{{{custom_intro}}}} will provide the opening hook — so the rest of Touch 1 should flow naturally after a personalized sentence. Reference {{{{companyName}}}} and use {{{{city}}}} to make it feel locally relevant. If a voice sample is provided, use it as your style guide — preserve their tone and phrasing. End with a single soft CTA (quick call?). Append the email signature exactly as provided.
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


# ── ALL-GATES HELPERS ─────────────────────────────────────────────────────────

_ALL_GATES = ("icp_reviewed", "dns_verified", "warmup_complete", "payment_confirmed", "sequence_approved", "calendar_connected")


def check_all_gates_and_alert(client, save_fn):
    """Check if all 6 pre-launch gates are True; fire Telegram alert once when all green.
    Resets the alerted flag when any gate falls back to False so re-completion fires again.
    save_fn() must persist the updated client dict (e.g. lambda: save_clients(config)).
    """
    checklist  = client.get("checklist", {})
    all_green  = all(checklist.get(g) for g in _ALL_GATES)
    if all_green:
        client["onboarding_status"] = "ready_to_launch"
        if not client.get("launch_ready_alerted"):
            firm = client.get("firm_name", client.get("id", "Client"))
            cid  = client.get("id", "")
            _notify_telegram(
                f"🚀 *{firm}* is ready to launch!\n\n"
                f"All 6 gates are green. Head to the portal to send the ready-to-launch email.\n"
                f"https://admin.argusreach.com/clients/{cid}"
            )
            client["launch_ready_alerted"] = True
        save_fn()
    else:
        # Reset ready_to_launch status if any gate drops (unless already live)
        if not client.get("active") and client.get("onboarding_status") == "ready_to_launch":
            client["onboarding_status"] = "warming_up"
        client["launch_ready_alerted"] = False
        save_fn()


def _auto_generate_stripe_link(client_id, client, config):
    """Auto-generate a per-client Stripe payment link and save to client record.
    Called at intake approval. Skips silently if price ID not configured.
    """
    plan     = client.get("plan", "starter")
    price_id = os.environ.get(f"STRIPE_PRICE_{plan.upper()}", "")
    if not price_id:
        app.logger.warning(f"Auto Stripe link skipped for {client_id}: STRIPE_PRICE_{plan.upper()} not set in .env")
        return None
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        app.logger.warning(f"Auto Stripe link skipped for {client_id}: STRIPE_SECRET_KEY not set")
        return None
    try:
        import stripe as _stripe
        _stripe.api_key = stripe_key
        firm_name = client.get("firm_name", "")
        link = _stripe.PaymentLink.create(
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"client_id": client_id, "plan": plan, "firm_name": firm_name},
            subscription_data={"metadata": {"client_id": client_id, "plan": plan}},
            after_completion={"type": "redirect", "redirect": {"url": "https://argusreach.com"}},
        )
        client["stripe_payment_link"] = link.url
        save_clients(config)
        app.logger.info(f"Stripe payment link auto-generated for {client_id}: {link.url}")
        return link.url
    except Exception as e:
        app.logger.warning(f"Auto Stripe link failed for {client_id} (non-fatal): {e}")
        return None


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
    # Leads: use our DB prospect count (accurate even before Instantly analytics are available)
    # Instantly analytics endpoint returns [] for DRAFT campaigns — DB is always correct
    leads_db          = conn.execute("SELECT COUNT(DISTINCT id) FROM prospects WHERE client_id=?", (client_id,)).fetchone()[0]
    prospects_tracked = conn.execute("SELECT COUNT(DISTINCT prospect_id) FROM events WHERE event_type='classified' AND client_id=?", (client_id,)).fetchone()[0]
    conn.close()

    breakdown     = {r[0]: r[1] for r in reply_rows if r[0]}
    # total_replies = unique prospects who replied (each prospect counted once regardless of how
    # many classified events they have — avoids inflation from re-classifications)
    total_replies = conn.execute(
        "SELECT COUNT(DISTINCT prospect_id) FROM events WHERE event_type='classified' AND client_id=?",
        (client_id,)
    ).fetchone()[0] if False else sum(breakdown.values())  # keep grouped sum for now — each prospect only has one classification event per message

    # Instantly analytics: emails_sent_count only (unreliable for leads_count, DRAFT returns empty)
    analytics      = fetch_instantly_analytics()
    a              = analytics.get(instantly_campaign_id or "", {})
    instantly_sent = a.get("emails_sent_count", 0)
    leads          = leads_db  # authoritative — DB never returns 0 for loaded prospects

    # Report buckets: Interested = positive + question + approved escalations
    # Approved escalations keep their 'escalated' classification tag — count them in interested
    conn2 = get_db()
    approved_escalations = conn2.execute(
        "SELECT COUNT(DISTINCT prospect_id) FROM events WHERE event_type='draft_approved' "
        "AND client_id=? AND json_extract(metadata,'$.original_classification')='escalated'",
        (client_id,)
    ).fetchone()[0]
    conn2.close()
    interested = breakdown.get("positive", 0) + breakdown.get("question", 0) + approved_escalations

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
        "revenue_cents":     revenue,
        "revenue":           f"${revenue/100:,.2f}",
        "prospects_tracked": prospects_tracked,
        "reply_rate":        f"{(total_replies/leads*100):.1f}%" if leads > 0 else "—",
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

def parse_uploaded_file(file_storage):
    """
    Parse a CSV or Excel file upload into a list of rows (list of dicts).
    Returns (rows, error_string). error_string is None on success.
    """
    import csv, io
    filename = file_storage.filename.lower()
    try:
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            try:
                import openpyxl
            except ImportError:
                return [], "openpyxl not installed — Excel files not supported. Upload a CSV instead."
            wb = openpyxl.load_workbook(file_storage, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return [], "File is empty."
            headers = [str(h).strip() if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
            return [dict(zip(headers, [str(c) if c is not None else "" for c in row])) for row in rows[1:]], None
        else:
            content = file_storage.read().decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(content))
            return [dict(row) for row in reader], None
    except Exception as e:
        return [], f"Could not parse file: {e}"

def extract_dnc_from_rows(rows):
    """Pull email addresses and domains from parsed file rows."""
    import re
    email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    domain_re = re.compile(r'^[a-z0-9.-]+\.[a-z]{2,}$')
    entries = []
    for row in rows:
        for val in row.values():
            v = str(val).strip().lower()
            if email_re.match(v):
                entries.append(v)
            elif domain_re.match(v) and "." in v:
                entries.append(v)
    return list(set(entries))

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

    # Live warmup score from Instantly — check on every profile load, auto-gate at ≥85%
    warmup_live = {"score": None, "status": None, "error": None}
    outreach_email = client.get("outreach_email","")
    if outreach_email:
        try:
            _inst_key = os.environ.get("INSTANTLY_API_KEY","")
            _r = requests.get(
                "https://api.instantly.ai/api/v2/accounts?limit=50",
                headers={"Authorization": f"Bearer {_inst_key}"},
                timeout=8
            )
            if _r.ok:
                _accounts = _r.json().get("items", [])
                _acct = next((a for a in _accounts if a.get("email","").lower() == outreach_email.lower()), None)
                if _acct:
                    _score = _acct.get("stat_warmup_score")
                    _wstatus = _acct.get("warmup_status")  # 0=off,1=on
                    warmup_live = {"score": _score, "status": _wstatus, "error": None}
                    # Auto-check gate if score ≥ 85 and not already checked
                    if _score is not None and int(_score) >= 85 and not client.get("checklist", {}).get("warmup_complete"):
                        config2 = load_clients()
                        c2 = next((x for x in config2["clients"] if x.get("id") == client_id), None)
                        if c2:
                            c2.setdefault("checklist", {})["warmup_complete"] = True
                            save_clients(config2)
                            check_all_gates_and_alert(c2, lambda: save_clients(config2))
                            client["checklist"] = c2["checklist"]
                            _notify_telegram(f"🌡️ *{client.get('firm_name')}* warmup hit {_score}/100 — gate auto-checked ✅")
                            app.logger.info(f"Warmup gate auto-checked for {client_id} (score={_score})")
                else:
                    warmup_live["error"] = f"{outreach_email} not found in Instantly"
        except Exception as _we:
            warmup_live["error"] = str(_we)[:80]

    # Monitor health check — read heartbeat timestamp
    monitor_status = {"running": False, "last_seen": None, "age_minutes": None}
    heartbeat_file = BASE_DIR / "monitor" / "logs" / "monitor_heartbeat.txt"
    if heartbeat_file.exists():
        try:
            import zoneinfo as _zi3
            from datetime import timezone as _tz
            ts_str = heartbeat_file.read_text().strip()
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            now_utc = datetime.now(_tz.utc)
            age_minutes = (now_utc - ts).total_seconds() / 60
            ts_et = ts.astimezone(_zi3.ZoneInfo("America/New_York"))
            monitor_status = {
                "running": age_minutes < 30,
                "last_seen": ts_et.strftime("%-I:%M %p ET"),
                "age_minutes": round(age_minutes),
            }
        except Exception:
            pass

    return render_template("client_detail.html",
        client=client,
        dnc_count=len(dnc),
        lead_count=lead_count,
        metrics=metrics,
        events=[dict(e) for e in events],
        conn_status=conn_status,
        monitor_status=monitor_status,
        warmup_live=warmup_live,
    )


def _push_sequence_to_instantly(client):
    """Push the client's current sequence to their Instantly campaign. Returns (ok, message)."""
    campaign_id = client.get("instantly_campaign_id","")
    if not campaign_id:
        return False, "No campaign ID on file — launch a campaign first."
    seq_raw = client.get("sequence", [])
    if not seq_raw or not any(s.get("subject") for s in seq_raw):
        return False, "Sequence is empty — write it in the portal first."

    # Convert to Instantly format — plain text only (HTML corrupts in Instantly editor)
    steps = []
    for i, s in enumerate(seq_raw):
        steps.append({
            "type": "email",
            "delay": s.get("delay_days", 0) if i > 0 else 0,
            "delay_unit": "days",
            "pre_delay_unit": "days",
            "variants": [{"subject": s.get("subject",""), "body": s.get("body","")}]
        })

    inst_key = os.environ.get("INSTANTLY_API_KEY","")
    headers  = {"Authorization": f"Bearer {inst_key}", "Content-Type": "application/json"}
    try:
        r = requests.patch(
            f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}",
            headers=headers,
            json={"sequences": [{"steps": steps}]},
            timeout=15
        )
        if r.ok:
            return True, f"Sequence pushed to Instantly ({len(steps)} touches)."
        else:
            return False, f"Instantly API error: {r.status_code} — {r.text[:120]}"
    except Exception as e:
        return False, f"Push failed: {str(e)[:120]}"


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

    # Auto-push to Instantly if campaign exists
    push_to_instantly = f.get("push_instantly") == "1"
    if push_to_instantly and client.get("instantly_campaign_id"):
        ok, msg = _push_sequence_to_instantly(client)
        flash(f"Sequence saved. {'✅ ' if ok else '⚠️ '}{msg}", "success" if ok else "error")
    else:
        flash("Sequence and schedule saved.", "success")

    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/sequence/push", methods=["POST"])
@login_required
def push_sequence(client_id):
    """Push current portal sequence to Instantly campaign (standalone action)."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))
    ok, msg = _push_sequence_to_instantly(client)
    flash(f"{'✅ ' if ok else '⚠️ '}{msg}", "success" if ok else "error")
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
    check_all_gates_and_alert(client, lambda: save_clients(config))
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
    skip_verify = not bool(os.environ.get("NEVERBOUNCE_API_KEY", ""))

    if not month:
        flash("Month is required.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    # ── Server-side gate check (UI disable is not enough) ────────────────────
    test_override = request.form.get("test_override") == "1"
    if not test_override:
        checklist     = client.get("checklist", {})
        missing_gates = [g for g in _ALL_GATES if not checklist.get(g)]
        if missing_gates:
            flash(f"❌ Cannot launch — gates not green: {', '.join(missing_gates)}", "error")
            return redirect(url_for("client_detail", client_id=client_id))

    # Store log in a file so we can stream it
    log_path = BASE_DIR / "monitor" / "logs" / f"launch_{client_id}.log"
    log_path.write_text(f"[{datetime.now(zoneinfo.ZoneInfo('America/New_York')).strftime('%I:%M %p ET')}] Starting campaign launch for {client.get('firm_name')} - {month}\n")

    def run_in_background():
        orig_stdout = sys.stdout
        try:
            sys.path.insert(0, str(BASE_DIR / "tools"))
            import monthly_cycle as mc
            import importlib
            importlib.reload(mc)  # ensure fresh state

            # Redirect stdout to log file — Telegram alerts still fire from within mc
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
            try:
                if sys.stdout != orig_stdout:
                    sys.stdout.close()
            except Exception:
                pass
            sys.stdout = orig_stdout
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
    check_all_gates_and_alert(client, lambda: save_clients(config))

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


@app.route("/clients/<client_id>/send-launch-email", methods=["POST"])
@login_required
def send_launch_email(client_id):
    """Send the ready-to-launch email with the per-client Stripe payment link."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    # Verify all 6 gates
    checklist    = client.get("checklist", {})
    missing_gates = [g for g in _ALL_GATES if not checklist.get(g)]
    if missing_gates:
        flash(f"❌ Cannot send launch email — gates not yet green: {', '.join(missing_gates)}", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    # Verify Stripe payment link
    payment_link = client.get("stripe_payment_link", "")
    if not payment_link:
        flash("Payment link not generated yet.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    contact_name = client.get("_contact_name") or client.get("firm_name", "")
    first_name   = contact_name.split()[0] if contact_name else "there"
    plan         = client.get("plan", "starter")
    plan_display = {
        "starter": "Starter \u2014 $750/mo",
        "growth":  "Growth \u2014 $1,500/mo",
        "scale":   "Scale \u2014 $2,500/mo",
    }.get(plan, f"{plan.title()} Plan")
    to_email = client.get("client_email", "")
    firm     = client.get("firm_name", "")

    if not to_email:
        flash("No client email on file.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<div style="max-width:580px;margin:0 auto;padding:40px 24px;">

  <div style="margin-bottom:32px;">
    <span style="font-size:14px;font-weight:800;letter-spacing:-0.02em;color:#000;">ArgusReach</span>
  </div>

  <p style="font-size:15px;line-height:1.7;margin:0 0 16px;">Hi {first_name},</p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 24px;">Good news - everything is in place:</p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 8px;">\u2705 Your outreach email is authenticated and warmed up</p>
  <p style="font-size:15px;line-height:1.7;margin:0 0 8px;">\u2705 Your prospect list is built and verified</p>
  <p style="font-size:15px;line-height:1.7;margin:0 0 8px;">\u2705 Your sequence is approved</p>
  <p style="font-size:15px;line-height:1.7;margin:0 0 28px;">\u2705 Your booking link is live</p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 20px;">We're ready to launch. The only remaining step is your subscription payment to kick off your first month.</p>

  <p style="font-size:16px;font-weight:700;margin:0 0 12px;">{plan_display}</p>
  <p style="text-align:left;margin:0 0 28px;">
    <a href="{payment_link}" style="background:#000;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:700;font-size:14px;">Pay Now \u2192</a>
  </p>

  <p style="font-size:15px;line-height:1.7;margin:0 0 24px;">Once payment is confirmed we'll activate your campaign and your first emails will go out within 24 hours.</p>

  <div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e5e5;">
    <p style="font-size:14px;line-height:1.6;margin:0;color:#444;">\u2014 Vito Resciniti<br>Founder, ArgusReach<br><a href="mailto:vito@argusreach.com" style="color:#000;">vito@argusreach.com</a></p>
  </div>

</div>
</body>
</html>"""

    app_password = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")
    if not app_password:
        flash("ARGUSREACH_GMAIL_APP_PASS not set — cannot send email.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = "Vito Resciniti | ArgusReach <vito@argusreach.com>"
        msg["To"]      = to_email
        msg["Subject"] = "Ready to launch - one last step"
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login("vito@argusreach.com", app_password)
            smtp.send_message(msg)
    except Exception as e:
        flash(f"Email failed: {e}", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    import zoneinfo as _zi_le
    client["launch_email_sent_at"] = datetime.now(_zi_le.ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M %p ET")
    save_clients(config)
    _notify_telegram(f"🚀 Ready-to-launch email sent to *{to_email}* for *{firm}*")
    flash(f"✅ Ready-to-launch email sent to {to_email}.", "success")
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


@app.route("/system")
@login_required
def system_status():
    import zoneinfo as _zi
    import subprocess
    eastern = _zi.ZoneInfo("America/New_York")
    generated = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p ET")
    services = []

    # 1. Instantly
    try:
        r = requests.get("https://api.instantly.ai/api/v2/accounts",
                         headers={"Authorization": f"Bearer {INSTANTLY_KEY}"}, timeout=8)
        if r.ok:
            data = r.json()
            accounts = data.get("items", data) if isinstance(data, dict) else data
            warmup = sum(1 for a in accounts if a.get("warmup_status") == 1) if isinstance(accounts, list) else "?"
            total  = len(accounts) if isinstance(accounts, list) else "?"
            services.append({"name": "Instantly", "status": "ok", "detail": f"{total} accounts · {warmup} warming up", "note": "Email sending + warmup"})
        else:
            services.append({"name": "Instantly", "status": "error", "detail": f"API returned {r.status_code}", "note": "Check API key or plan status"})
    except Exception as e:
        services.append({"name": "Instantly", "status": "error", "detail": str(e)[:80], "note": None})

    # 2. Apollo
    apollo_key = os.environ.get("APOLLO_API_KEY", "")
    if apollo_key:
        try:
            r = requests.post("https://api.apollo.io/v1/auth/health",
                              json={"api_key": apollo_key}, timeout=8)
            if r.ok and r.json().get("is_logged_in"):
                services.append({"name": "Apollo", "status": "ok", "detail": "Authenticated · Prospect sourcing active", "note": "Upgrade to Basic ($49/mo) at first client"})
            else:
                services.append({"name": "Apollo", "status": "warn", "detail": "Key set but auth check failed", "note": None})
        except Exception as e:
            services.append({"name": "Apollo", "status": "error", "detail": str(e)[:80], "note": None})
    else:
        services.append({"name": "Apollo", "status": "error", "detail": "APOLLO_API_KEY not configured", "note": None})

    # 3. NeverBounce
    nb_key = os.environ.get("NEVERBOUNCE_API_KEY", "")
    if nb_key:
        try:
            r = requests.get("https://api.neverbounce.com/v4/account/info",
                             params={"key": nb_key}, timeout=8)
            if r.ok and r.json().get("status") == "success":
                info = r.json().get("credits_info", {})
                paid = info.get("paid_credits_remaining", 0)
                free = info.get("free_credits_remaining", 0)
                services.append({"name": "NeverBounce", "status": "ok", "detail": f"{paid} paid credits · {free} free credits remaining", "note": "Pay-as-you-go · $0.008/email"})
            else:
                services.append({"name": "NeverBounce", "status": "warn", "detail": "Key set but API check failed", "note": None})
        except Exception as e:
            services.append({"name": "NeverBounce", "status": "error", "detail": str(e)[:80], "note": None})
    else:
        services.append({"name": "NeverBounce", "status": "error", "detail": "NEVERBOUNCE_API_KEY not configured", "note": None})

    # 4. Stripe
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if stripe_key:
        try:
            import stripe as _stripe
            _stripe.api_key = stripe_key
            bal = _stripe.Balance.retrieve()
            mode = "🔴 Live" if not stripe_key.startswith("sk_test") else "🟡 Test"
            price_keys = [k for k in ["STRIPE_PRICE_STARTER","STRIPE_PRICE_GROWTH","STRIPE_PRICE_SCALE"] if os.environ.get(k)]
            services.append({"name": "Stripe", "status": "ok", "detail": f"{mode} mode · {len(price_keys)}/3 Price IDs configured", "note": "Webhook secret configured ✅"})
        except Exception as e:
            services.append({"name": "Stripe", "status": "error", "detail": str(e)[:80], "note": None})
    else:
        services.append({"name": "Stripe", "status": "error", "detail": "STRIPE_SECRET_KEY not configured", "note": None})

    # 5. Calendly
    cal_token = os.environ.get("CALENDLY_API_TOKEN", "")
    cal_secret = os.environ.get("CALENDLY_WEBHOOK_SECRET", "")
    if cal_token:
        services.append({"name": "Calendly", "status": "ok" if cal_secret else "warn",
                         "detail": "API token configured" + (" · Webhook secret configured" if cal_secret else " · Webhook secret missing"),
                         "note": "Register webhook to get secret"})
    else:
        services.append({"name": "Calendly", "status": "warn", "detail": "CALENDLY_API_TOKEN not configured", "note": "Add when first client signs"})

    # 6. Telegram
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        services.append({"name": "Telegram", "status": "ok", "detail": f"Bot configured · Chat ID set", "note": "Alerts active"})
    else:
        services.append({"name": "Telegram", "status": "error", "detail": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing", "note": None})

    # 7. Claude / Anthropic
    claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if claude_key:
        services.append({"name": "Claude (Anthropic)", "status": "ok", "detail": "API key configured", "note": "Sequence writing + reply classification + custom_intro"})
    else:
        services.append({"name": "Claude (Anthropic)", "status": "error", "detail": "ANTHROPIC_API_KEY not configured", "note": None})

    # 8. Database
    try:
        conn = get_db()
        counts = {}
        for t in ["clients","prospects","events","meetings","revenue"]:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        conn.close()
        detail = " · ".join(f"{v} {k}" for k,v in counts.items())
        services.append({"name": "Database (SQLite)", "status": "ok", "detail": detail, "note": None})
    except Exception as e:
        services.append({"name": "Database (SQLite)", "status": "error", "detail": str(e)[:80], "note": None})

    # 9. Monitor service
    try:
        log_path = BASE_DIR / "monitor" / "logs" / "monitor.log"
        if log_path.exists():
            lines = log_path.read_text().strip().splitlines()
            last = next((l for l in reversed(lines) if l.strip()), "")
            import re as _re
            m = _re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", last)
            if m:
                from datetime import timezone
                last_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age_min = int((datetime.now(timezone.utc) - last_dt).total_seconds() / 60)
                status = "ok" if age_min < 15 else "warn"
                services.append({"name": "Monitor Service", "status": status,
                                  "detail": f"Last cycle {age_min}m ago · {last[:60]}", "note": None})
            else:
                services.append({"name": "Monitor Service", "status": "warn", "detail": "Log exists but no timestamp found", "note": None})
        else:
            services.append({"name": "Monitor Service", "status": "error", "detail": "Log file not found", "note": None})
    except Exception as e:
        services.append({"name": "Monitor Service", "status": "error", "detail": str(e)[:80], "note": None})

    # 10. DNS Poll Timer
    try:
        result = subprocess.run(["systemctl", "is-active", "argusreach-dns-poll.timer"],
                                capture_output=True, text=True, timeout=5)
        active = result.stdout.strip() == "active"
        services.append({"name": "DNS Poll Timer", "status": "ok" if active else "warn",
                         "detail": "Active · Checks SPF/DKIM/DMARC every 4h" if active else "Timer not active",
                         "note": None})
    except Exception as e:
        services.append({"name": "DNS Poll Timer", "status": "warn", "detail": str(e)[:80], "note": None})

    return render_template("system.html", services=services, generated=generated)


@app.route("/clients/<client_id>/generate-report", methods=["GET", "POST"])
@login_required
def generate_report(client_id):
    import calendar as _cal
    client, config = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    # Default month = current month
    now = datetime.now()
    default_month = now.strftime("%B %Y")

    if request.method == "POST":
        month_str  = request.form.get("month", default_month).strip()
        working    = [l.strip() for l in request.form.get("working", "").splitlines() if l.strip()]
        changing   = [l.strip() for l in request.form.get("changing", "").splitlines() if l.strip()]
        next_month = request.form.get("next_month", "").strip()

        # Pull stats from DB
        try:
            dt = datetime.strptime(month_str, "%B %Y")
        except ValueError:
            flash("Invalid month format. Use e.g. March 2026", "error")
            return redirect(url_for("generate_report", client_id=client_id))

        last_day = _cal.monthrange(dt.year, dt.month)[1]
        start    = dt.strftime("%Y-%m-01")
        end      = f"{dt.year:04d}-{dt.month:02d}-{last_day:02d}"

        conn = get_db()
        prospects = conn.execute(
            "SELECT COUNT(DISTINCT id) FROM prospects WHERE client_id=? AND date(created_at) BETWEEN ? AND ?",
            (client_id, start, end)
        ).fetchone()[0]
        reply_rows = conn.execute("""
            SELECT json_extract(metadata,'$.classification') as cls, COUNT(DISTINCT prospect_id) as cnt
            FROM events WHERE event_type='classified' AND client_id=?
              AND date(created_at) BETWEEN ? AND ?
            GROUP BY cls
        """, (client_id, start, end)).fetchall()
        bd = {r[0]: r[1] for r in reply_rows if r[0]}
        meetings = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE client_id=? AND date(created_at) BETWEEN ? AND ?",
            (client_id, start, end)
        ).fetchone()[0]
        conn.close()

        stats = {
            "prospects":        prospects,
            "reply_interested": bd.get("positive", 0) + bd.get("question", 0),
            "reply_not_now":    bd.get("not_now", 0),
            "reply_negative":   bd.get("negative", 0),
            "reply_escalated":  bd.get("escalated", 0),
            "meetings":         meetings,
            "emails_sent":      None,
        }

        # Try Instantly for emails_sent
        campaign_id = client.get("instantly_campaign_id", "")
        if campaign_id and INSTANTLY_KEY:
            try:
                r = requests.get(
                    "https://api.instantly.ai/api/v2/leads",
                    headers={"Authorization": f"Bearer {INSTANTLY_KEY}"},
                    params={"campaign": campaign_id, "limit": 1},
                    timeout=10
                )
                if r.ok:
                    a = r.json()
                    stats["emails_sent"] = a.get("total", None)
            except Exception:
                pass

        notes = {
            "working":    working or ["Sequence delivered without issues."],
            "changing":   changing or ["Monitoring performance for adjustments next cycle."],
            "next_month": next_month or "Continuing current campaign with any optimizations applied.",
        }

        # Load/update history
        import sys as _sys, json as _json
        history_path = BASE_DIR / "reports" / f"{client_id}_history.json"
        history = _json.loads(history_path.read_text()) if history_path.exists() else []
        is_launch = len(history) == 0
        existing  = next((i for i, e in enumerate(history) if e["month"] == month_str), None)
        entry = {
            "month":            month_str,
            "launch":           is_launch,
            "prospects":        stats["prospects"],
            "reply_interested": stats["reply_interested"],
            "reply_not_now":    stats["reply_not_now"],
            "meetings":         stats["meetings"],
        }
        if existing is not None:
            history[existing] = entry
        else:
            history.append(entry)
        history_path.write_text(_json.dumps(history, indent=2))

        # Import report builder from tools
        _sys.path.insert(0, str(BASE_DIR))
        from tools.monthly_report import build_report_html
        html = build_report_html(client, month_str, stats, notes, history=history)

        safe_month = month_str.replace(" ", "-")
        out_path   = BASE_DIR / "reports" / f"{client_id}_{safe_month}.html"
        out_path.write_text(html)

        # Store latest report filename on client record for inline banner
        client["latest_report_file"] = out_path.name
        client["latest_report_month"] = month_str
        for i, c in enumerate(config.get("clients", [])):
            if c.get("id") == client_id:
                config["clients"][i] = client
                break
        save_clients(config)

        flash(f"report_ready:{out_path.name}", "report")
        return redirect(url_for("client_detail", client_id=client_id))

    return render_template_string("""
{% extends 'base.html' %}
{% block title %}Generate Report — {{ client.firm_name }}{% endblock %}
{% block content %}
<div style="max-width:640px">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
    <a href="{{ url_for('client_detail', client_id=client.id) }}" style="color:var(--muted);font-size:12px;text-decoration:none">← {{ client.firm_name }}</a>
  </div>
  <h1 style="margin:0 0 6px">Generate Monthly Report</h1>
  <p style="color:var(--muted);font-size:13px;margin:0 0 32px">All stats are pulled automatically from the database. Fill in the narrative sections below.</p>

  <form method="POST">
    <div style="margin-bottom:20px">
      <label style="font-size:12px;font-weight:600;color:var(--muted);display:block;margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase">Reporting Month</label>
      <input name="month" type="text" value="{{ default_month }}" placeholder="March 2026"
        style="width:100%;background:var(--bg2);border:1px solid #334155;border-radius:6px;padding:10px 14px;color:var(--text);font-size:14px">
    </div>

    <div style="margin-bottom:20px">
      <label style="font-size:12px;font-weight:600;color:var(--muted);display:block;margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase">What Worked <span style="font-weight:400;text-transform:none">(one item per line)</span></label>
      <textarea name="working" rows="4" placeholder="Subject lines performed well&#10;Strong open rates in the healthcare segment"
        style="width:100%;background:var(--bg2);border:1px solid #334155;border-radius:6px;padding:10px 14px;color:var(--text);font-size:14px;resize:vertical;font-family:inherit"></textarea>
    </div>

    <div style="margin-bottom:20px">
      <label style="font-size:12px;font-weight:600;color:var(--muted);display:block;margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase">What We're Adjusting <span style="font-weight:400;text-transform:none">(one item per line)</span></label>
      <textarea name="changing" rows="4" placeholder="Testing a shorter Touch 2&#10;Refining the ICP to focus on mid-size firms"
        style="width:100%;background:var(--bg2);border:1px solid #334155;border-radius:6px;padding:10px 14px;color:var(--text);font-size:14px;resize:vertical;font-family:inherit"></textarea>
    </div>

    <div style="margin-bottom:28px">
      <label style="font-size:12px;font-weight:600;color:var(--muted);display:block;margin-bottom:6px;letter-spacing:0.06em;text-transform:uppercase">Next Month Focus</label>
      <textarea name="next_month" rows="3" placeholder="Continuing current volume with refined targeting. Will A/B test the opening line on Touch 1."
        style="width:100%;background:var(--bg2);border:1px solid #334155;border-radius:6px;padding:10px 14px;color:var(--text);font-size:14px;resize:vertical;font-family:inherit"></textarea>
    </div>

    <button type="submit" style="background:#4ade80;color:#000;font-weight:700;font-size:14px;padding:12px 28px;border:none;border-radius:6px;cursor:pointer">
      📊 Generate Report
    </button>
    <a href="{{ url_for('client_detail', client_id=client.id) }}" style="margin-left:16px;font-size:13px;color:var(--muted);text-decoration:none">Cancel</a>
  </form>
</div>
{% endblock %}
""", client=client, default_month=default_month)


@app.route("/reports")
@login_required
def reports_list():
    reports_dir = BASE_DIR / "reports"
    files = []
    config = load_clients()
    clients_map = {c["id"]: c for c in config.get("clients", [])}
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.html"), reverse=True):
            # Parse client_id from filename: client_id_Month-Year.html
            parts    = f.stem.split("_", 1)
            cid      = parts[0] if parts else ""
            client_c = clients_map.get(cid, {})
            files.append({
                "name":       f.name,
                "size":       f.stat().st_size,
                "modified":   datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
                "client_id":  cid,
                "firm_name":  client_c.get("firm_name", cid),
                "sent":       client_c.get(f"report_sent_{f.stem}", False),
            })
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


@app.route("/reports/<filename>/send", methods=["POST"])
@login_required
def send_report(filename):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    reports_dir = BASE_DIR / "reports"
    path = reports_dir / filename
    if not path.exists():
        flash("Report not found.", "error")
        return redirect(url_for("reports_list"))

    # Identify client from filename
    cid    = filename.split("_")[0]
    client, config = get_client_by_id(cid)
    if not client:
        flash("Client not found for this report.", "error")
        return redirect(url_for("reports_list"))

    to_email = client.get("client_email", "")
    if not to_email:
        flash("Client email not set — cannot send.", "error")
        return redirect(url_for("reports_list"))

    firm      = client.get("firm_name", cid)
    month_str = filename.replace(f"{cid}_", "").replace(".html", "").replace("-", " ")
    subject   = f"ArgusReach — Monthly Report — {firm} — {month_str}"
    html_body = path.read_text()

    sender_email    = "vito@argusreach.com"
    sender_app_pass = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"ArgusReach <{sender_email}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_app_pass)
            smtp.sendmail(sender_email, to_email, msg.as_string())

        # Mark as sent on client record
        client[f"report_sent_{path.stem}"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        config_all = load_clients()
        for i, c in enumerate(config_all.get("clients", [])):
            if c.get("id") == cid:
                config_all["clients"][i] = client
                break
        save_clients(config_all)

        flash(f"✅ Report sent to {to_email}", "success")
    except Exception as e:
        flash(f"❌ Failed to send: {e}", "error")

    # Return to client profile if we can identify the client, else reports list
    if cid:
        return redirect(url_for("client_detail", client_id=cid))
    return redirect(url_for("reports_list"))


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
            # Targeting
            "plan":                 f.get("plan","starter").strip(),
            "meeting_format":       ",".join(request.form.getlist("meeting_format")),
            "office_address":       f.get("office_address","").strip(),
            "success_story":        f.get("success_story","").strip(),
            "prospect_objection":   f.get("prospect_objection","").strip(),
            "target_locations":     f.get("target_locations","").strip(),
            "target_titles":        f.get("target_titles","").strip(),
            "target_industry":      ",".join(request.form.getlist("target_industry")),
            "target_seniority":     ",".join(request.form.getlist("target_seniority")),
            "target_company_size":  ",".join(request.form.getlist("target_company_size")),
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
            # Meta
            "notes":                f.get("notes","").strip(),
        }

        # Handle file uploads (DNC file + existing contact list)
        intake_id = submission["id"]
        uploads_path = UPLOADS_DIR / intake_id
        for field_name in ("dnc_file", "existing_list_file"):
            uploaded = request.files.get(field_name)
            if uploaded and uploaded.filename:
                uploads_path.mkdir(parents=True, exist_ok=True)
                ext = Path(uploaded.filename).suffix.lower() or ".csv"
                save_path = uploads_path / f"{field_name}{ext}"
                uploaded.save(str(save_path))
                submission[f"_{field_name}_path"] = str(save_path)

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

    # Auto-add to Instantly + enable warmup immediately
    warmup_status = "⏳ starting"
    try:
        import requests as _req
        _inst_key = os.environ.get("INSTANTLY_API_KEY", "")
        _plain_pass = _decrypt_credential(client["app_password"])
        _first = (client.get("sender_name") or "Vito").split()[0]
        _last  = (client.get("sender_name") or "Vito Resciniti").split()[-1]
        _add = _req.post(
            "https://api.instantly.ai/api/v2/accounts",
            headers={"Authorization": f"Bearer {_inst_key}", "Content-Type": "application/json"},
            json={
                "email": outreach_email,
                "first_name": _first,
                "last_name": _last,
                "provider_code": 2 if client.get("_email_provider") == "microsoft" else 1,
                "smtp_host": "smtp.office365.com" if client.get("_email_provider") == "microsoft" else "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_username": outreach_email, "smtp_password": _plain_pass,
                "imap_host": "outlook.office365.com" if client.get("_email_provider") == "microsoft" else "imap.gmail.com",
                "imap_port": 993,
                "imap_username": outreach_email, "imap_password": _plain_pass,
            }, timeout=15
        )
        if _add.ok or "already" in _add.text.lower():
            _warm = _req.patch(
                f"https://api.instantly.ai/api/v2/accounts/{outreach_email}",
                headers={"Authorization": f"Bearer {_inst_key}", "Content-Type": "application/json"},
                json={"warmup_enabled": True}, timeout=15
            )
            warmup_status = "✅ running" if _warm.ok else f"⚠️ warmup failed ({_warm.status_code})"
        else:
            warmup_status = f"⚠️ add failed ({_add.status_code})"
        app.logger.info(f"Instantly warmup for {outreach_email}: {warmup_status}")
    except Exception as _we:
        warmup_status = f"⚠️ error: {str(_we)[:80]}"
        app.logger.warning(f"Warmup auto-enable failed for {outreach_email}: {_we}")

    # Alert Vito
    _notify_telegram(
        f"🔐 *{firm}* submitted email credentials securely\n"
        f"📧 Outreach email: `{outreach_email}`\n"
        f"🌡️ Instantly warmup: {warmup_status}\n"
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


@app.route("/clients/<client_id>/send-followup-email", methods=["POST"])
@login_required
def send_followup_email(client_id):
    """Generate and send the DNS + sequence + Calendly follow-up email to client."""
    config = load_clients()
    client = next((c for c in config["clients"] if c.get("id") == client_id), None)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    to_email  = client.get("client_email", "")
    firm      = client.get("firm_name", "")
    contact   = client.get("_contact_name") or firm
    first     = contact.split()[0] if contact else "there"
    calendly  = client.get("calendly_link") or f"https://calendly.com/argusreach/{client_id}"
    sequence  = client.get("sequence", [])
    checklist = client.get("checklist", {})
    override  = request.form.get("override") == "1"

    if not to_email:
        flash("No client email on file — update it in settings first.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    if not client.get("outreach_email"):
        flash("No outreach email on file — client must submit credentials first.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    # DNS warning check
    dns_ok = checklist.get("dns_verified", False)
    dns_warning = "" if dns_ok else (
        "<div style='background:#2d1a00;border:1px solid #92400e;border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:13px;color:#fbbf24'>"
        "⚠️ <strong>DNS not yet verified</strong> — sending this email before DNS is set up is fine; "
        "it includes the DNS records for the client to add. Just make sure you paste the actual records in."
        "</div>"
    )

    # Build sequence HTML
    seq_html = ""
    for i, touch in enumerate(sequence):
        delay = touch.get("delay_days", 0)
        if i == 0:
            day_label = "Sends on day 1"
        else:
            day_label = f"Sends {delay} day{'s' if delay != 1 else ''} after Touch {i}"
        seq_html += f"""
  <div style="background:#f9f9f9;border-left:3px solid #4ade80;padding:14px;margin-bottom:14px;border-radius:0 6px 6px 0;">
    <p style="font-size:11px;color:#888;margin:0 0 4px;text-transform:uppercase;letter-spacing:.05em">Touch {i+1} — {day_label}</p>
    <p style="font-size:13px;font-weight:700;margin:0 0 8px;color:#111">Subject: {touch.get('subject','')}</p>
    <p style="font-size:13px;line-height:1.7;color:#333;margin:0;white-space:pre-wrap">{touch.get('body','')}</p>
  </div>"""

    if not seq_html:
        seq_html = "<p style='color:#888;font-size:13px'>Sequence not yet written — add it in the portal before sending this email.</p>"

    outreach_domain = client["outreach_email"].split("@")[-1] if "@" in client.get("outreach_email","") else "[your-domain.com]"
    provider = client.get("_email_provider","google")
    spf_include = "include:_spf.google.com" if provider != "microsoft" else "include:spf.protection.outlook.com"

    if provider == "microsoft":
        dkim_row = """<tr style="border-bottom:1px solid #e5e5e5">
          <td style="padding:10px 12px;color:#888;font-size:12px;white-space:nowrap;vertical-align:top">DKIM</td>
          <td style="padding:10px 12px;font-size:12px;color:#333">2 CNAME records — get these from <strong>Microsoft 365 Admin Center → Settings → Domains → your domain → DNS records</strong>. Once added, enable DKIM in the Microsoft 365 Defender portal.</td>
        </tr>"""
    else:
        dkim_row = """<tr style="border-bottom:1px solid #e5e5e5">
          <td style="padding:10px 12px;color:#888;font-size:12px;white-space:nowrap;vertical-align:top">DKIM</td>
          <td style="padding:10px 12px;font-size:12px;color:#333">1 TXT record — get this from <strong>Google Workspace Admin → Apps → Google Workspace → Gmail → Authenticate email → Generate new record</strong>. Copy the TXT name and value, add to your DNS.</td>
        </tr>"""

    dns_block = f"""<table style="width:100%;border-collapse:collapse;border:1px solid #e5e5e5;border-radius:6px;overflow:hidden;font-family:sans-serif">
  <thead>
    <tr style="background:#f4f4f4">
      <th style="text-align:left;padding:8px 12px;font-size:11px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #e5e5e5;width:60px">Type</th>
      <th style="text-align:left;padding:8px 12px;font-size:11px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #e5e5e5">Details</th>
    </tr>
  </thead>
  <tbody>
    <tr style="border-bottom:1px solid #e5e5e5">
      <td style="padding:10px 12px;color:#888;font-size:12px;white-space:nowrap;vertical-align:top">SPF</td>
      <td style="padding:10px 12px;font-size:12px;color:#333">
        <strong>Host:</strong> @ &nbsp;&nbsp; <strong>Value:</strong> <code style="background:#f4f4f4;padding:1px 5px;border-radius:3px">v=spf1 {spf_include} ~all</code><br>
        <span style="font-size:11px;color:#aaa">If you already have an SPF record, add the include value to it — don't create a second one.</span>
      </td>
    </tr>
    {dkim_row}
    <tr>
      <td style="padding:10px 12px;color:#888;font-size:12px;white-space:nowrap;vertical-align:top">DMARC</td>
      <td style="padding:10px 12px;font-size:12px;color:#333">
        <strong>Host:</strong> <code style="background:#f4f4f4;padding:1px 5px;border-radius:3px">_dmarc.{outreach_domain}</code> &nbsp;&nbsp;
        <strong>Value:</strong> <code style="background:#f4f4f4;padding:1px 5px;border-radius:3px">v=DMARC1; p=none; rua=mailto:vito@argusreach.com</code>
      </td>
    </tr>
  </tbody>
</table>
<p style="font-size:12px;color:#888;margin:8px 0 0;">Add these to <strong>{outreach_domain}</strong> at your DNS provider (GoDaddy, Cloudflare, Namecheap, etc.). Usually 10 minutes — DNS propagates within a few hours.</p>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#fff;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<div style="max-width:580px;margin:0 auto;padding:40px 24px;">
  <div style="margin-bottom:32px;"><span style="font-size:14px;font-weight:800;letter-spacing:-0.02em;color:#000;">ArgusReach</span></div>
  <p style="font-size:15px;line-height:1.7;margin:0 0 16px;">Hi {first},</p>
  <p style="font-size:15px;line-height:1.7;margin:0 0 24px;">We're ready on our end. Here's everything you need to action before we go live.</p>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:28px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;"><strong>1. DNS records for your IT person</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 10px;">Have whoever manages your domain add the following records. This ensures your outreach emails land in inboxes — not spam. Should take about 10 minutes on their end, then a few hours to propagate.</p>
    {dns_block}
  </div>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:28px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 12px;"><strong>2. Your outreach sequence — please review</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 16px;">These are the three emails we'll send on your behalf. Read through and reply with any edits — or just say "looks good" and we're ready.</p>
    {seq_html}
    <p style="font-size:12px;color:#aaa;margin:4px 0 0;">Note: {{{{custom_intro}}}}, {{{{firstName}}}}, {{{{companyName}}}}, {{{{city}}}} are personalized per recipient at send time.</p>
  </div>

  <div style="border-left:3px solid #4ade80;padding-left:16px;margin-bottom:32px;">
    <p style="font-size:15px;font-weight:700;margin:0 0 8px;"><strong>3. Connect your calendar</strong></p>
    <p style="font-size:14px;line-height:1.7;color:#444;margin:0 0 12px;">We've set up your booking page. Click the link below and connect your calendar — takes about 2 minutes. Interested prospects will book directly onto your calendar from here.</p>
    <p style="text-align:left;margin:0;"><a href="{calendly}" style="background:#000;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:700;font-size:14px;">Connect Your Calendar →</a></p>
  </div>

  <p style="font-size:15px;line-height:1.7;margin:0 0 8px;">Your sending address is already warming up in the background — this takes 2–3 weeks. Once warmup is complete and everything above is done, I'll send you the subscription payment link to kick things off.</p>
  <p style="font-size:15px;line-height:1.7;margin:16px 0 0;">Any questions, just reply here.</p>

  <div style="margin-top:40px;padding-top:24px;border-top:1px solid #e5e5e5;">
    <p style="font-size:14px;line-height:1.6;margin:0;color:#444;">Vito Resciniti<br>Founder, ArgusReach<br><a href="mailto:vito@argusreach.com" style="color:#000;">vito@argusreach.com</a></p>
  </div>
</div></body></html>"""

    app_password = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")
    if not app_password:
        flash("ARGUSREACH_GMAIL_APP_PASS not set — cannot send email.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = "Vito Resciniti | ArgusReach <vito@argusreach.com>"
        msg["To"]      = to_email
        msg["Subject"] = f"Your sequence + next setup steps — {firm}"
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login("vito@argusreach.com", app_password)
            smtp.send_message(msg)
        _notify_telegram(f"📨 Follow-up email sent to *{to_email}* for *{firm}*\n{'⚠️ DNS not verified — records in email for client to add' if not dns_ok else '✅ DNS verified'}")
        flash(f"Follow-up email sent to {to_email}.", "success")
    except Exception as e:
        flash(f"Email failed: {e}", "error")

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
            "plan":                  plan,  # from approval form — Vito can override intake value
            "outreach_email":        "",   # set during credential submission — NEVER default to vito@argusreach.com
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
            # NOTE: no second "plan" key here — form value above is the authoritative source
            "_meeting_format":        intake.get("meeting_format","any"),
            "_office_address":        intake.get("office_address",""),
            "_success_story":        intake.get("success_story",""),
            "_prospect_objection":   intake.get("prospect_objection",""),
            "_target_locations":     intake.get("target_locations",""),
            "_target_titles":        intake.get("target_titles",""),
            "_value_prop":           intake.get("value_prop",""),
            "_differentiator":       intake.get("differentiator",""),
            "_outcomes":             intake.get("outcomes",""),
            "_voice_sample":         intake.get("voice_sample",""),
            "_business_description": intake.get("business_description",""),
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

        # Auto-load DNC emails/domains from intake text field
        dnc_raw = intake.get("dnc_emails", "")
        if dnc_raw.strip():
            dnc_entries = parse_dnc_input(dnc_raw)
            if dnc_entries:
                append_dnc(client_id, dnc_entries)
                app.logger.info(f"Auto-loaded {len(dnc_entries)} DNC entries from intake text for {client_id}")

        # Auto-load DNC from uploaded file (if present)
        dnc_file_path = intake.get("_dnc_file_path", "")
        if dnc_file_path and Path(dnc_file_path).exists():
            try:
                with open(dnc_file_path, "rb") as fh:
                    class _FakeStorage:
                        filename = Path(dnc_file_path).name
                        def read(self): return fh.read()
                    rows, err = parse_uploaded_file(_FakeStorage())
                if err:
                    app.logger.warning(f"DNC file parse error for {client_id}: {err}")
                else:
                    file_entries = extract_dnc_from_rows(rows)
                    if file_entries:
                        append_dnc(client_id, file_entries)
                        app.logger.info(f"Auto-loaded {len(file_entries)} DNC entries from uploaded file for {client_id}")
            except Exception as e:
                app.logger.warning(f"DNC file processing failed for {client_id}: {e}")

        # Store existing contact list (if uploaded) — saved for Vito to review before launch
        existing_list_path = intake.get("_existing_list_file_path", "")
        if existing_list_path and Path(existing_list_path).exists():
            try:
                import shutil
                dest_dir = CAMPAIGNS_DIR / client_id
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / ("existing_contacts" + Path(existing_list_path).suffix)
                shutil.copy2(existing_list_path, dest)
                # Count rows for logging
                with open(existing_list_path, "rb") as fh:
                    class _FakeStorage2:
                        filename = Path(existing_list_path).name
                        def read(self): return fh.read()
                    rows, err = parse_uploaded_file(_FakeStorage2())
                row_count = len(rows) if not err else "?"
                app.logger.info(f"Existing contact list saved for {client_id}: {row_count} rows → {dest}")
                # Store path reference on client record
                new_client["_existing_contacts_csv"] = str(dest)
                save_clients(config)
            except Exception as e:
                app.logger.warning(f"Existing list processing failed for {client_id}: {e}")

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

        # Auto-generate per-client Stripe payment link
        _auto_generate_stripe_link(client_id, new_client, config)

        # Retroactively attribute any untracked $500 setup fee payment to this client.
        # The $500 Stripe link is generic (no client_id at time of payment — client pays
        # before intake approval). Match by customer email + amount + no client_id yet.
        try:
            client_email = new_client.get("client_email", "")
            if client_email:
                conn = get_db()
                conn.execute("""
                    UPDATE revenue
                    SET client_id = ?
                    WHERE (client_id IS NULL OR client_id = '')
                      AND amount_cents = 50000
                      AND LOWER(customer_email) = LOWER(?)
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (client_id, client_email))
                updated = conn.execute("SELECT changes()").fetchone()[0]
                conn.commit()
                conn.close()
                if updated:
                    app.logger.info(f"Attributed $500 setup fee payment to client {client_id} ({client_email})")
        except Exception as _rve:
            app.logger.warning(f"Setup fee attribution failed (non-fatal): {_rve}")

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
