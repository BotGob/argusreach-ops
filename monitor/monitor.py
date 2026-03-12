#!/usr/bin/env python3
"""
ArgusReach Reply Monitor v2
────────────────────────────
Monitors client outreach inboxes, classifies replies with AI, auto-responds
or queues drafts for approval, and sends Vito a nightly digest.

Run:    python3 monitor.py
Test:   python3 monitor.py --test       (connects, classifies, never sends)
Logs:   logs/monitor.log, logs/replies.json, logs/pending_approvals.json
"""

import argparse
import imaplib
import smtplib
import email
import email.utils
import json
import os
import sys
import time
import hashlib
import threading
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import requests
from anthropic import Anthropic

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
CLIENTS_FILE    = BASE_DIR / 'clients.json'
LOG_DIR         = BASE_DIR / 'logs'
DNC_DIR         = BASE_DIR / 'dnc'
REPLY_LOG       = LOG_DIR / 'replies.json'
PENDING_FILE    = LOG_DIR / 'pending_approvals.json'
PROCESSED_FILE  = LOG_DIR / 'processed_ids.json'
MONITOR_LOG     = LOG_DIR / 'monitor.log'

LOG_DIR.mkdir(exist_ok=True)
DNC_DIR.mkdir(exist_ok=True)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ.get('ARGUSREACH_BOT_TOKEN',
                                      '8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk')
TELEGRAM_CHAT_ID    = os.environ.get('ARGUSREACH_CHAT_ID', '8135725412')
ANTHROPIC_API_KEY   = os.environ.get('ANTHROPIC_API_KEY', '')

POLL_INTERVAL       = 600       # seconds between inbox checks (10 min)
MAX_PER_CLIENT      = 15        # hard cap per cycle
MAX_AI_CALLS_DAY    = 100       # daily Claude budget
DIGEST_HOUR         = 18        # 24h local hour to send daily digest (6pm)
AI_MODEL            = 'claude-haiku-4-5-20251001'  # updated 2026-03-11

# ── INTEGRATION KEYS (loaded from .env) ──────────────────────────────────────
INSTANTLY_API_KEY   = os.environ.get('INSTANTLY_API_KEY', '')
AIRTABLE_TOKEN      = os.environ.get('AIRTABLE_TOKEN', '')
AIRTABLE_BASE_ID    = os.environ.get('AIRTABLE_BASE_ID', '')

# ── ARGS ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--test', action='store_true',
                    help='Test mode: reads and classifies, never sends emails')
ARGS = parser.parse_args()
TEST_MODE = ARGS.test

# ── LOGGING ───────────────────────────────────────────────────────────────────
def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    with open(MONITOR_LOG, 'a') as f:
        f.write(line + '\n')

# ── AI CLIENT ─────────────────────────────────────────────────────────────────
ai = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

_ai_day   = datetime.now().date()
_ai_count = 0

def ai_budget_ok():
    global _ai_day, _ai_count
    today = datetime.now().date()
    if today != _ai_day:
        _ai_day, _ai_count = today, 0
    return _ai_count < MAX_AI_CALLS_DAY

def ai_tick():
    global _ai_count
    _ai_count += 1

# ── DNC (DO NOT CONTACT) ──────────────────────────────────────────────────────
def dnc_path(client_id):
    return DNC_DIR / f'{client_id}.txt'

def is_dnc(client_id, email_addr):
    p = dnc_path(client_id)
    if not p.exists():
        return False
    return email_addr.lower().strip() in p.read_text().lower()

def add_dnc(client_id, email_addr):
    p = dnc_path(client_id)
    with open(p, 'a') as f:
        f.write(email_addr.lower().strip() + '\n')
    log(f"[DNC] Added {email_addr} for client {client_id}")

# ── INSTANTLY INTEGRATION ─────────────────────────────────────────────────────
def instantly_pause_contact(prospect_email: str, campaign_id: str = None) -> bool:
    """
    Pause a prospect in Instantly so no further touches go out after a reply.
    Set client['instantly_campaign_id'] in clients.json to enable per-client targeting.
    Falls back gracefully if key not set — logs warning, never crashes monitor.
    """
    if not INSTANTLY_API_KEY:
        log("[Instantly] No API key configured — skipping auto-pause")
        return False
    try:
        payload = {"api_key": INSTANTLY_API_KEY, "email": prospect_email}
        if campaign_id:
            payload["campaign_id"] = campaign_id
        resp = requests.post(
            "https://api.instantly.ai/api/v1/lead/pause",
            json=payload,
            timeout=10
        )
        if resp.status_code == 200:
            log(f"[Instantly] Paused contact: {prospect_email}")
            return True
        else:
            log(f"[Instantly] Pause failed for {prospect_email}: {resp.status_code} {resp.text[:120]}")
            return False
    except Exception as e:
        log(f"[Instantly] Error pausing {prospect_email}: {e}")
        return False


def instantly_unsubscribe_contact(prospect_email: str) -> bool:
    """
    Permanently unsubscribe a prospect in Instantly (DNC at platform level).
    Called in addition to our local DNC list for negative/unsubscribe replies.
    """
    if not INSTANTLY_API_KEY:
        return False
    try:
        resp = requests.post(
            "https://api.instantly.ai/api/v1/lead/unsubscribe",
            json={"api_key": INSTANTLY_API_KEY, "email": prospect_email},
            timeout=10
        )
        if resp.status_code == 200:
            log(f"[Instantly] Unsubscribed: {prospect_email}")
            return True
        else:
            log(f"[Instantly] Unsubscribe failed for {prospect_email}: {resp.status_code} {resp.text[:120]}")
            return False
    except Exception as e:
        log(f"[Instantly] Error unsubscribing {prospect_email}: {e}")
        return False


# ── AIRTABLE INTEGRATION ───────────────────────────────────────────────────────
# Status map: monitor classification → Airtable Prospect Status field value
_AIRTABLE_STATUS_MAP = {
    'positive':  'Replied — Interested',
    'question':  'Replied — Interested',
    'not_now':   'Replied — Not Now',
    'negative':  'DNC',
    'ooo':       'In Sequence',   # keep in sequence, follow up after return date
    'other':     'In Sequence',
    'escalated': 'In Sequence',   # human will handle — don't change status
}

def _airtable_find_prospect(prospect_email: str) -> str | None:
    """Return Airtable record ID for a prospect by email, or None if not found."""
    if not AIRTABLE_TOKEN or not AIRTABLE_BASE_ID:
        return None
    try:
        formula = f"LOWER({{Email}})=LOWER('{prospect_email}')"
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/Prospects"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"},
            params={"filterByFormula": formula, "maxRecords": 1},
            timeout=10
        )
        if resp.status_code == 200:
            records = resp.json().get("records", [])
            return records[0]["id"] if records else None
        return None
    except Exception as e:
        log(f"[Airtable] Find error for {prospect_email}: {e}")
        return None


def airtable_sync_reply(client_id: str, prospect_email: str,
                         classification: str, reply_text: str,
                         follow_up_date: str = None) -> bool:
    """
    Update the Prospect record in Airtable after a reply is classified.
    Creates a Touch Log entry as well.
    Falls back gracefully — never crashes monitor.
    """
    if not AIRTABLE_TOKEN or not AIRTABLE_BASE_ID:
        log("[Airtable] No token/base configured — skipping sync")
        return False

    record_id = _airtable_find_prospect(prospect_email)
    if not record_id:
        log(f"[Airtable] Prospect not found: {prospect_email} — skipping sync")
        return False

    status = _AIRTABLE_STATUS_MAP.get(classification, 'In Sequence')
    today  = datetime.now().strftime('%Y-%m-%d')

    fields = {
        "Status":         status,
        "Last Reply":     reply_text[:500] if reply_text else "",
        "Last Contacted": today,
    }
    if follow_up_date:
        fields["Follow Up Date"] = follow_up_date
    if classification in ('negative',):
        fields["Status"] = "DNC"

    try:
        url  = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/Prospects/{record_id}"
        resp = requests.patch(
            url,
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}",
                     "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=10
        )
        if resp.status_code == 200:
            log(f"[Airtable] Updated prospect {prospect_email} → {status}")
            # Also log to Touch Log table
            _airtable_log_touch(client_id, prospect_email, classification, reply_text)
            return True
        else:
            log(f"[Airtable] Update failed {prospect_email}: {resp.status_code} {resp.text[:120]}")
            return False
    except Exception as e:
        log(f"[Airtable] Sync error for {prospect_email}: {e}")
        return False


def _airtable_log_touch(client_id: str, prospect_email: str,
                         classification: str, reply_text: str):
    """Append a row to Touch Log table for audit trail."""
    if not AIRTABLE_TOKEN or not AIRTABLE_BASE_ID:
        return
    outcome_map = {
        'positive':  'Replied — Positive',
        'question':  'Replied — Positive',
        'not_now':   'Replied — Negative',
        'negative':  'Replied — Negative',
        'ooo':       'Sent',
        'other':     'Sent',
        'escalated': 'Sent',
    }
    try:
        requests.post(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/Touch Log",
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}",
                     "Content-Type": "application/json"},
            json={"fields": {
                "Prospect Email": prospect_email,
                "Client":         client_id,
                "Date Sent":      datetime.now().strftime('%Y-%m-%d'),
                "Outcome":        outcome_map.get(classification, 'Sent'),
                "Reply Text":     (reply_text or '')[:500],
            }},
            timeout=10
        )
    except Exception as e:
        log(f"[Airtable] Touch log error: {e}")


# ── PROCESSED ID DEDUPLICATION ────────────────────────────────────────────────
def load_processed():
    if PROCESSED_FILE.exists():
        try:
            return set(json.loads(PROCESSED_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_processed(ids: set):
    # Keep only last 10k to prevent unbounded growth
    trimmed = list(ids)[-10000:]
    PROCESSED_FILE.write_text(json.dumps(trimmed))

def msg_fingerprint(from_email, subject, date_str):
    """Stable ID for a message to prevent double-processing."""
    return hashlib.sha256(f"{from_email}|{subject}|{date_str}".encode()).hexdigest()[:16]

# ── PENDING APPROVALS ─────────────────────────────────────────────────────────
def load_pending():
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text())
        except Exception:
            return []
    return []

def save_pending(pending):
    PENDING_FILE.write_text(json.dumps(pending, indent=2))

def queue_pending(client, from_email, from_name, subject, draft, classification):
    pending = load_pending()
    entry = {
        'id': f"{client['id']}:{from_email}:{int(time.time())}",
        'client_id': client['id'],
        'firm_name': client['firm_name'],
        'outreach_email': client['outreach_email'],
        'app_password': client['app_password'],
        'sender_name': client['sender_name'],
        'from_email': from_email,
        'from_name': from_name,
        'subject': subject,
        'draft': draft,
        'classification': classification,
        'queued_at': datetime.now().isoformat(),
    }
    pending.append(entry)
    save_pending(pending)
    return entry['id']

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def notify(text):
    if TEST_MODE:
        log(f"[TEST] Telegram would send: {text[:120]}")
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram error: {e}")

def check_telegram_commands():
    """
    Approval/rejection is handled by Go (OpenClaw) directly via the pending_approvals.json file.
    Vito tells Go "approve" or "reject" in plain English in the main chat.
    This function is intentionally a no-op — bot polling removed to avoid conflicts with OpenClaw.
    """
    pass

# ── EMAIL UTILS ───────────────────────────────────────────────────────────────
def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get('Content-Disposition', ''))
            if ct == 'text/plain' and 'attachment' not in disp:
                try:
                    return part.get_payload(decode=True).decode('utf-8', errors='ignore')
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode('utf-8', errors='ignore')
        except Exception:
            pass
    return ''

def is_automated(from_email):
    skip = ['mailer-daemon', 'postmaster', 'noreply', 'no-reply',
            'donotreply', 'do-not-reply', 'bounce@', 'notification',
            'feedback@', 'reports@', 'alerts@', 'support@', 'daemon@']
    return any(s in from_email.lower() for s in skip)

def is_genuine_reply(msg):
    """Real replies have In-Reply-To or References headers. Spam doesn't."""
    return bool(msg.get('In-Reply-To') or msg.get('References'))

def is_spam(msg, body):
    subject = msg.get('Subject', '').lower()
    spam_words = ['click here', 'you have won', 'congratulations', 'limited time offer',
                  'act now', 'free money', 'make money fast', 'work from home']
    if any(w in subject for w in spam_words):
        return True
    if len(body) > 8000 and 'meeting' not in body.lower() and 'call' not in body.lower():
        return True
    return False

def _send_email(outreach_email, app_password, sender_name, to_email, subject, body, retry=1):
    """Send via Gmail SMTP with one retry."""
    msg = MIMEMultipart('alternative')
    msg['From'] = f'{sender_name} <{outreach_email}>'
    msg['To'] = to_email
    msg['Subject'] = subject if subject.lower().startswith('re:') else f'Re: {subject}'
    msg.attach(MIMEText(body, 'plain'))

    for attempt in range(1 + retry):
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as smtp:
                smtp.login(outreach_email, app_password)
                smtp.send_message(msg)
            return True
        except Exception as e:
            if attempt < retry:
                log(f"SMTP failed (attempt {attempt+1}), retrying in 5s: {e}")
                time.sleep(5)
            else:
                raise

# ── REPLY LOG ─────────────────────────────────────────────────────────────────
def log_reply(client_id, prospect_email, classification, draft, sent, notes=''):
    try:
        data = json.loads(REPLY_LOG.read_text()) if REPLY_LOG.exists() else []
    except Exception:
        data = []
    data.append({
        'ts': datetime.now().isoformat(),
        'client': client_id,
        'prospect': prospect_email,
        'classification': classification,
        'draft_preview': (draft or '')[:200],
        'sent': sent,
        'notes': notes,
        'test_mode': TEST_MODE,
    })
    REPLY_LOG.write_text(json.dumps(data, indent=2))

# ── AI CLASSIFICATION ─────────────────────────────────────────────────────────
def classify_and_draft(reply_body, from_name, from_email, subject, client):
    if not ai:
        return _fallback_result('No ANTHROPIC_API_KEY configured')

    prompt = f"""You are a reply routing assistant for {client['sender_name']} at {client['firm_name']}.

YOUR SOLE JOB: Classify this reply and draft a brief, safe response that routes interested prospects to a calendar booking. Nothing else.

CLIENT CONTEXT:
- Sender: {client['sender_name']}, {client['firm_name']}
- Vertical: {client['vertical']}
- Tone: {client.get('tone', 'warm-professional')}
- Compliance notes: {client.get('compliance_note', 'none')}
- Booking link: {client['calendly_link']}
- ICP: {client.get('icp_summary', '')}

PROSPECT: {from_name or from_email} ({from_email})
SUBJECT: {subject}

THEIR REPLY:
---
{reply_body[:2000]}
---

ABSOLUTE RULES — any violation → set should_respond=false, escalate=true:
1. NEVER answer domain questions (investment, insurance, clinical, legal, compliance, market predictions, product details)
2. NEVER make promises, guarantees, or commitments
3. NEVER discuss pricing, fees, or contract terms
4. NEVER speak negatively about anyone
5. NEVER mention other clients
6. If aggressive, threatening, legal-sounding, or contains a complaint → DO NOT respond, escalate immediately
7. If uncertain about ANYTHING → do not respond, escalate
8. Responses do ONE thing: acknowledge warmly and offer the booking link
9. Keep responses to 2–4 sentences max
10. You are {client['sender_name']} — never mention ArgusReach or any AI tool

RESPONSE TONE EXAMPLES (adapt — never copy verbatim):
- Positive: "Thanks for getting back to me, [name]. Happy to connect — grab any time here: {client['calendly_link']}"
- Question needing expertise: "Great question — that's exactly what I'd want to cover in person. Here's my calendar: {client['calendly_link']}"
- Not now: "No problem at all — I'll leave it with you. Reach out anytime when the timing is better."
- Negative/remove: "Understood, removing you now — sorry for the interruption."

Return ONLY valid JSON (no markdown, no commentary):
{{
  "classification": "positive|question|not_now|negative|ooo|other",
  "reasoning": "one sentence max",
  "should_respond": true,
  "escalate": false,
  "escalate_reason": "",
  "draft_response": "full 2-4 sentence response or empty if escalate=true",
  "notify_vito": true,
  "notify_reason": "brief reason",
  "follow_up_date": null,
  "urgency": "high|medium|low"
}}"""

    try:
        ai_tick()
        response = ai.messages.create(
            model=AI_MODEL,
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith('```'):
            raw = '\n'.join(raw.split('\n')[1:])
            if raw.endswith('```'):
                raw = raw[:-3]
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        log(f"AI JSON parse error: {e}")
        return _fallback_result('AI returned invalid JSON')
    except Exception as e:
        log(f"AI call error: {e}")
        return _fallback_result(str(e)[:120])

def _fallback_result(reason):
    return {
        'classification': 'other',
        'reasoning': reason,
        'should_respond': False,
        'escalate': True,
        'escalate_reason': f'Classification failed: {reason}',
        'draft_response': '',
        'notify_vito': True,
        'notify_reason': 'Manual review needed',
        'follow_up_date': None,
        'urgency': 'medium',
    }

# ── PER-CLIENT PROCESSING ─────────────────────────────────────────────────────
def process_client(client, processed_ids):
    cid  = client['id']
    firm = client['firm_name']
    label = f"[{firm}]"

    log(f"{label} Checking inbox...")
    new_processed = set()

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(client['outreach_email'], client['app_password'])
        mail.select('inbox')

        # Fetch ALL unseen — more reliable than date-filtering (catches up after downtime)
        _, raw = mail.search(None, 'UNSEEN')
        msg_ids = raw[0].split() if raw[0] else []

        if not msg_ids:
            log(f"{label} No unread messages.")
            mail.logout()
            return new_processed

        log(f"{label} {len(msg_ids)} unread message(s).")

        if len(msg_ids) > MAX_PER_CLIENT:
            notify(
                f"⚠️ *{firm}* — {len(msg_ids)} unread emails found (cap: {MAX_PER_CLIENT}).\n"
                f"Processing first {MAX_PER_CLIENT}. Check inbox directly."
            )
            msg_ids = msg_ids[:MAX_PER_CLIENT]

        for msg_id in msg_ids:
            _, data = mail.fetch(msg_id, '(RFC822)')
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_name, from_email = email.utils.parseaddr(msg.get('From', ''))
            subject   = msg.get('Subject', '(no subject)')
            date_str  = msg.get('Date', '')
            body      = get_body(msg)

            if not body.strip():
                mail.store(msg_id, '+FLAGS', '\\Seen')
                continue

            # Deduplication — skip if we've already processed this message
            fingerprint = msg_fingerprint(from_email, subject, date_str)
            if fingerprint in processed_ids:
                log(f"{label} Skipping duplicate: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                continue

            # Filter: automated senders
            if is_automated(from_email):
                log(f"{label} Skipping automated sender: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # Filter: must be a genuine reply to our outreach
            if not is_genuine_reply(msg):
                log(f"{label} Skipping — not a reply to our outreach: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # Filter: spam signals
            if is_spam(msg, body):
                log(f"{label} Skipping spam from: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # Filter: DNC list
            if is_dnc(cid, from_email):
                log(f"{label} Skipping DNC contact: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # AI budget check
            if not ai_budget_ok():
                notify(
                    f"⚠️ Daily AI limit ({MAX_AI_CALLS_DAY} calls) reached. "
                    f"Remaining replies will process tomorrow."
                )
                log("Daily AI cap reached.")
                break

            log(f"{label} Processing: {from_name} <{from_email}>")
            result        = classify_and_draft(body, from_name, from_email, subject, client)
            classification = result['classification']
            draft         = result.get('draft_response', '')
            should_respond = result.get('should_respond', False)
            escalate      = result.get('escalate', False)
            sent          = False
            approval_id   = None

            # ── ESCALATION — human must review, no auto-response ever
            if escalate:
                notify(
                    f"🚨 *{firm}* — ESCALATION\n"
                    f"From: {from_name or from_email}\n"
                    f"Reason: {result.get('escalate_reason', 'Unknown')}\n"
                    f"Subject: _{subject}_\n\n"
                    f"*Do not reply until you have reviewed this manually.*"
                )
                log_reply(cid, from_email, 'escalated', '', False, result.get('escalate_reason', ''))
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # ── INSTANTLY: pause sequence on any real reply
            if classification not in ('ooo',) and not escalate:
                instantly_pause_contact(
                    from_email,
                    campaign_id=client.get('instantly_campaign_id')
                )

            # ── AIRTABLE: sync reply classification
            airtable_sync_reply(
                cid, from_email, classification, body[:500],
                follow_up_date=result.get('follow_up_date')
            )

            # ── HANDLE RESPONSE
            if should_respond and draft:
                if classification == 'negative':
                    # Unsubscribe — always auto-send removal ack and add to DNC
                    add_dnc(cid, from_email)
                    instantly_unsubscribe_contact(from_email)   # platform-level unsubscribe
                    if not TEST_MODE:
                        try:
                            _send_email(client['outreach_email'], client['app_password'],
                                        client['sender_name'], from_email, subject, draft)
                            sent = True
                        except Exception as e:
                            log(f"SMTP error (removal ack): {e}")
                            notify(f"⚠️ *{firm}* — Failed to send removal ack to {from_email}: `{str(e)[:100]}`")
                    else:
                        log(f"[TEST] Would send removal ack to {from_email}")

                elif client['mode'] == 'automated':
                    if not TEST_MODE:
                        try:
                            _send_email(client['outreach_email'], client['app_password'],
                                        client['sender_name'], from_email, subject, draft)
                            sent = True
                        except Exception as e:
                            log(f"SMTP error: {e}")
                            notify(f"⚠️ *{firm}* — Failed to send to {from_email}: `{str(e)[:100]}`")
                    else:
                        log(f"[TEST] Would auto-send to {from_email}")

                elif client['mode'] == 'draft_approval':
                    approval_id = queue_pending(client, from_email, from_name,
                                                subject, draft, classification)

            # ── TELEGRAM NOTIFICATION
            emoji = {'positive': '🎯', 'question': '❓', 'not_now': '📅',
                     'negative': '🚫', 'ooo': '🏖', 'other': '⚠️'}.get(classification, '📬')

            msg_lines = [
                f"{emoji} *{firm}* — {classification.upper()}",
                f"From: {from_name or from_email}",
                f"_{result.get('reasoning', '')}_ ",
            ]

            if approval_id and draft:
                msg_lines += [
                    f"\n*Draft ready:*",
                    f"```\n{draft[:500]}\n```",
                    f"→ Tell Go: *approve* or *reject* this reply",
                ]
            elif sent:
                msg_lines.append("✅ Auto-sent")
            elif TEST_MODE:
                msg_lines.append("🔬 Test mode — not sent")

            notify('\n'.join(msg_lines))

            mail.store(msg_id, '+FLAGS', '\\Seen')
            new_processed.add(fingerprint)
            log_reply(cid, from_email, classification, draft, sent,
                      result.get('notify_reason', ''))

        mail.logout()

    except imaplib.IMAP4.error as e:
        log(f"IMAP error {firm}: {e}")
        notify(f"⚠️ *{firm}* IMAP error: `{str(e)[:150]}`")
    except Exception as e:
        log(f"Error processing {firm}: {e}")
        notify(f"⚠️ *{firm}* monitor error: `{str(e)[:150]}`")

    return new_processed

# ── DAILY DIGEST ──────────────────────────────────────────────────────────────
_last_digest_day = None

def maybe_send_digest():
    global _last_digest_day
    now = datetime.now()
    if now.hour < DIGEST_HOUR:
        return
    today = now.date()
    if _last_digest_day == today:
        return
    _last_digest_day = today

    try:
        data = json.loads(REPLY_LOG.read_text()) if REPLY_LOG.exists() else []
    except Exception:
        data = []

    # Filter to today's entries
    today_str = today.isoformat()
    today_entries = [r for r in data if r.get('ts', '').startswith(today_str)]

    if not today_entries:
        notify(f"📊 *Daily Digest — {today_str}*\nNo replies processed today.")
        return

    counts = {}
    for r in today_entries:
        c = r.get('classification', 'other')
        counts[c] = counts.get(c, 0) + 1

    pending = load_pending()
    lines = [
        f"📊 *Daily Digest — {today_str}*",
        f"Total replies processed: {len(today_entries)}",
        "",
    ]
    for k, v in sorted(counts.items()):
        emoji = {'positive': '🎯', 'question': '❓', 'not_now': '📅',
                 'negative': '🚫', 'escalated': '🚨', 'ooo': '🏖'}.get(k, '•')
        lines.append(f"{emoji} {k.capitalize()}: {v}")

    if pending:
        lines += ["", f"⏳ Pending approvals: {len(pending)}",
                  "Reply `PENDING` to review drafts waiting for your approval."]

    notify('\n'.join(lines))
    log("Daily digest sent.")

# ── LOAD CLIENTS ──────────────────────────────────────────────────────────────
def load_clients():
    data = json.loads(CLIENTS_FILE.read_text())
    return [c for c in data['clients'] if c.get('active', False)]

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def run():
    mode_tag = " [TEST MODE]" if TEST_MODE else ""
    log(f"ArgusReach Monitor v2 starting{mode_tag}")
    notify(f"✅ *ArgusReach Monitor v2* started{mode_tag}\nWatching all active client inboxes · checking every {POLL_INTERVAL//60} min")

    processed_ids = load_processed()

    while True:
        try:
            clients = load_clients()
            if not clients:
                log("No active clients. Waiting...")
            else:
                for client in clients:
                    new_ids = process_client(client, processed_ids)
                    processed_ids.update(new_ids)
                save_processed(processed_ids)

            check_telegram_commands()
            maybe_send_digest()

        except Exception as e:
            log(f"Main loop error: {e}")

        log(f"Cycle complete. Next check in {POLL_INTERVAL // 60} min.\n")
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    if not ANTHROPIC_API_KEY:
        print("WARNING: ANTHROPIC_API_KEY not set. AI classification will not work.")
        print("Set it: export ANTHROPIC_API_KEY=sk-ant-...")
    run()
