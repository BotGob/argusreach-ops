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
from dotenv import load_dotenv

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
load_dotenv(BASE_DIR / '.env')   # load .env before reading os.environ below

# ── DATABASE ──────────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(BASE_DIR.parent))
    from db.database import init_db as _init_db, log_event as _log_event, \
        upsert_prospect as _upsert_prospect, update_prospect_stage as _update_stage, \
        prospect_id as _prospect_id, set_follow_up_date as _set_follow_up_date, \
        get_due_followups as _get_due_followups, mark_follow_up_sent as _mark_follow_up_sent
    _DB_ENABLED = True
except Exception as _db_err:
    _DB_ENABLED = False
    print(f"[DB] Warning: database layer not available: {_db_err}")
CLIENTS_FILE    = BASE_DIR / 'clients.json'
LOG_DIR         = BASE_DIR / 'logs'
DNC_DIR         = BASE_DIR / 'dnc'
REPLY_LOG       = LOG_DIR / 'replies.json'
PENDING_FILE    = LOG_DIR / 'pending_approvals.json'
PROCESSED_FILE  = LOG_DIR / 'processed_ids.json'
MONITOR_LOG     = LOG_DIR / 'monitor.log'

LOG_DIR.mkdir(exist_ok=True)
DNC_DIR.mkdir(exist_ok=True)

# Init DB on startup
if _DB_ENABLED:
    try:
        _init_db()
    except Exception as _e:
        print(f"[DB] Init failed: {_e}")

# ── DATABASE ──────────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(BASE_DIR.parent))
    from db.database import init_db as _init_db, log_event, upsert_prospect, update_prospect_stage, prospect_id as _prospect_id
    _init_db()
    _DB_ENABLED = True
except Exception as _db_err:
    _DB_ENABLED = False
    def log_event(*a, **k): pass
    def upsert_prospect(*a, **k): return None
    def update_prospect_stage(*a, **k): pass
    def _prospect_id(c, e): return hashlib.md5(f"{c}:{e}".encode()).hexdigest()

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ.get('ARGUSREACH_BOT_TOKEN',
                                      '8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk')
TELEGRAM_CHAT_ID    = os.environ.get('ARGUSREACH_CHAT_ID', '-1003821840813')
ANTHROPIC_API_KEY   = os.environ.get('ANTHROPIC_API_KEY', '')

POLL_INTERVAL       = 600       # seconds between inbox checks (10 min)
MAX_PER_CLIENT      = 50        # hard cap per cycle (24h window, most filtered by dedup/spam)
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
    email_addr = email_addr.lower().strip()
    # Write to client-specific DNC
    p = dnc_path(client_id)
    with open(p, 'a') as f:
        f.write(email_addr + '\n')
    # Write to global DNC — protects all future clients from re-contacting this person
    global_dnc = BASE_DIR / 'dnc' / 'global.txt'
    global_dnc.parent.mkdir(exist_ok=True)
    with open(global_dnc, 'a') as f:
        f.write(email_addr + '\n')
    log(f"[DNC] Added {email_addr} to client DNC + global DNC")

# ── INSTANTLY INTEGRATION ─────────────────────────────────────────────────────
def instantly_pause_contact(prospect_email: str, campaign_id: str = None) -> bool:
    """
    NOTE: Pause is handled automatically by Instantly's built-in 'stop_on_reply' campaign setting.
    All campaigns MUST be created with stop_on_reply=true in the Instantly dashboard.
    This function is a no-op — kept as a hook for future API integration if needed.
    """
    log(f"[Instantly] Pause handled by stop_on_reply campaign setting for {prospect_email}")
    return True


def instantly_unsubscribe_contact(prospect_email: str) -> bool:
    """
    Add a prospect to Instantly's global blocklist via v2 API.
    Unsubscribe is also handled locally via our DNC list (dnc/<client_id>.txt).
    Falls back gracefully — never crashes monitor.
    """
    if not INSTANTLY_API_KEY:
        log("[Instantly] No API key — unsubscribe handled via local DNC list only")
        return False
    try:
        # v2 API: look up lead by email to get UUID, then blocklist
        headers = {
            "Authorization": f"Bearer {INSTANTLY_API_KEY}",
            "Content-Type": "application/json"
        }
        # Add to global blocklist — prevents emailing across ALL campaigns
        resp = requests.post(
            "https://api.instantly.ai/api/v2/blocklists/entries",
            headers=headers,
            json={"email": prospect_email, "reason": "unsubscribed"},
            timeout=10
        )
        if resp.status_code in (200, 201):
            log(f"[Instantly] Blocklisted: {prospect_email}")
            return True
        else:
            # Non-critical — local DNC list already handles this
            log(f"[Instantly] Blocklist note for {prospect_email}: {resp.status_code} (local DNC active)")
            return False
    except Exception as e:
        log(f"[Instantly] Blocklist error for {prospect_email}: {e}")
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
PROCESSED_ARCHIVE_FILE = LOG_DIR / 'processed_ids_archive.json'
PROCESSED_MAX_AGE_DAYS = 45  # keep 45 days in active file; archive the rest

def load_processed():
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text())
            # Support both old format (list of strings) and new format (dict of {hash: timestamp})
            if isinstance(data, list):
                return set(data)
            elif isinstance(data, dict):
                return set(data.keys())
        except Exception:
            return set()
    return set()

def save_processed(ids: set, timestamps: dict = None):
    """
    Save processed IDs with timestamps.
    Active file: last 45 days. Older entries moved to archive (never deleted — preserves history).
    Monthly reporting uses replies.json + DB, NOT processed_ids, so rotation is safe.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=PROCESSED_MAX_AGE_DAYS)

    # Load existing timestamped data
    existing = {}
    if PROCESSED_FILE.exists():
        try:
            data = json.loads(PROCESSED_FILE.read_text())
            if isinstance(data, dict):
                existing = data
        except Exception:
            pass

    # Add new IDs with current timestamp
    for id_ in ids:
        if id_ not in existing:
            existing[id_] = now.isoformat()

    # Split: active (recent) vs archive (old)
    active  = {k: v for k, v in existing.items() if v >= cutoff.isoformat()}
    archive = {k: v for k, v in existing.items() if v < cutoff.isoformat()}

    # Append old entries to archive
    if archive:
        existing_archive = {}
        if PROCESSED_ARCHIVE_FILE.exists():
            try:
                existing_archive = json.loads(PROCESSED_ARCHIVE_FILE.read_text())
            except Exception:
                pass
        existing_archive.update(archive)
        PROCESSED_ARCHIVE_FILE.write_text(json.dumps(existing_archive))

    PROCESSED_FILE.write_text(json.dumps(active))

def msg_fingerprint(from_email, subject, date_str, message_id=''):
    """Stable ID for a message to prevent double-processing.
    Includes Message-ID when available — prevents collision when same sender
    sends two different emails on the same day with the same subject."""
    return hashlib.sha256(f"{from_email}|{subject}|{date_str}|{message_id}".encode()).hexdigest()[:16]

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

def queue_pending(client, from_email, from_name, subject, draft, classification,
                  in_reply_to=None, references=None):
    pending = load_pending()
    # Dedup: if a pending entry already exists for this prospect, replace it but ALWAYS notify
    # (suppressing was causing silent re-queues with no Telegram alert — fixed 2026-03-16)
    is_new = True
    existing_idx = next((i for i, e in enumerate(pending) if e.get('from_email') == from_email and e.get('client_id') == client['id']), None)
    if existing_idx is not None:
        log(f"[{client['firm_name']}] Replacing existing pending entry for {from_email} (re-notifying)")
        pending.pop(existing_idx)
        is_new = True  # always notify so nothing sits silently
    entry = {
        'id': f"{client['id']}:{from_email}:{int(time.time())}",
        'client_id':             client['id'],
        'firm_name':             client['firm_name'],
        'campaign_name':         client.get('campaign_name', ''),
        'instantly_campaign_id': client.get('instantly_campaign_id', ''),
        'client_email':          client.get('client_email', ''),
        'outreach_email':        client['outreach_email'],
        'app_password':          client['app_password'],
        'sender_name':           client['sender_name'],
        'from_email':            from_email,
        'from_name':             from_name,
        'subject':               subject,
        'draft':                 draft,
        'classification':        classification,
        'queued_at':             datetime.now().isoformat(),
        'in_reply_to':           in_reply_to or '',
        'references':            references or in_reply_to or '',
    }
    pending.append(entry)
    save_pending(pending)
    return entry['id'], is_new

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
    Polls Telegram group for bot commands (/status, /pending).
    Uses a separate offset file so it never conflicts with OpenClaw's polling.
    APPROVE/REJECT are handled by OpenClaw — this only handles /commands.
    """
    offset_file = LOG_DIR / 'telegram_cmd_offset.json'
    try:
        offset = json.loads(offset_file.read_text())['offset'] if offset_file.exists() else 0
    except Exception:
        offset = 0

    try:
        resp = requests.get(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates',
            params={'offset': offset, 'timeout': 2, 'allowed_updates': ['message']},
            timeout=5
        )
        updates = resp.json().get('result', [])
    except Exception:
        return

    for update in updates:
        offset = update['update_id'] + 1
        msg = update.get('message', {})
        chat_id = str(msg.get('chat', {}).get('id', ''))
        text = msg.get('text', '').strip().lower()

        # Only respond to messages from our alerts group
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        if text in ('/status', '/status@argusreach_bot'):
            _send_status_to_telegram()
        elif text in ('/pending', '/pending@argusreach_bot'):
            _send_pending_to_telegram()
        elif msg.get('text', '').strip().upper().startswith('CYCLE '):
            # CYCLE <client_id> <Month Year>
            # e.g. CYCLE argusreach_test April 2026
            parts     = msg['text'].strip().split(None, 2)
            if len(parts) >= 3:
                cycle_client = parts[1]
                cycle_month  = parts[2]
                notify(f"⚙️ Starting monthly cycle for *{cycle_client}* — *{cycle_month}*...")
                try:
                    import subprocess
                    script = str(BASE_DIR / 'tools' / 'monthly_cycle.py')
                    subprocess.Popen(
                        ['python3', script, '--client', cycle_client, '--month', cycle_month],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except Exception as ce:
                    notify(f"❌ Cycle launch failed: `{str(ce)[:100]}`")

    if updates:
        offset_file.write_text(json.dumps({'offset': offset}))


def _send_status_to_telegram():
    """Format and send client status summary to the Telegram alerts group."""
    try:
        clients_data = json.loads(Path(CLIENTS_FILE).read_text())
        clients = clients_data.get('clients', clients_data) if isinstance(clients_data, dict) else clients_data
        active = [c for c in clients if c.get('active') and c.get('outreach_email') and not c.get('id','').startswith('_')]
    except Exception as e:
        notify(f"⚠️ /status error: `{e}`")
        return

    if not active:
        notify("📊 *Status* — No active clients.")
        return

    pending_all = load_pending()
    lines = [f"📊 *ArgusReach Status* — {len(active)} active client{'s' if len(active) != 1 else ''}"]

    for c in active:
        cid       = c['id']
        firm      = c['firm_name']
        campaign  = c.get('campaign_name', '—')
        launch    = c.get('launch_date', '—')
        pending_n = sum(1 for p in pending_all if p.get('client_id') == cid)

        # Load history
        h_path = LOG_DIR.parent / 'reports' / f"{cid}_history.json"
        history = json.loads(h_path.read_text()) if h_path.exists() else []
        last = history[-1] if history else None

        lines.append(f"\n*{firm}*")
        lines.append(f"Campaign: {campaign}")
        lines.append(f"Launch: {launch} · {len(history)} month{'s' if len(history) != 1 else ''} active")
        if last:
            lines.append(f"Last month: {last['contacts']} contacts · {last['positive']} positive · {last['meetings']} meetings")
        if pending_n:
            lines.append(f"⚠️ {pending_n} pending approval{'s' if pending_n != 1 else ''}")

    notify('\n'.join(lines))


def _send_pending_to_telegram():
    """Send list of pending approvals to Telegram group."""
    pending = load_pending()
    if not pending:
        notify("✅ No pending approvals.")
        return
    lines = [f"📋 *Pending Approvals* — {len(pending)} item{'s' if len(pending) != 1 else ''}"]
    for p in pending:
        lines.append(f"\n*{p.get('firm_name','?')}* — {p.get('classification','?').upper()}")
        lines.append(f"From: {p.get('from_name') or p.get('from_email')}")
        lines.append(f"→ APPROVE `{p['id']}` or REJECT `{p['id']}`")
    notify('\n'.join(lines))

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

def get_client_campaigns(client):
    """
    Return a list of campaign dicts for this client.
    Supports both legacy single-campaign fields and new multi-campaign 'campaigns' array.
    Each campaign dict has: instantly_campaign_id, campaign_name, prospects_csv, launch_date, active
    """
    # New format: explicit campaigns array
    if client.get('campaigns'):
        return [c for c in client['campaigns'] if c.get('active', True)]
    # Legacy format: single campaign fields
    return [{
        'instantly_campaign_id': client.get('instantly_campaign_id', ''),
        'campaign_name':         client.get('campaign_name', ''),
        'prospects_csv':         client.get('prospects_csv', ''),
        'launch_date':           client.get('launch_date', ''),
        'active':                True,
    }]


def load_prospect_emails(client):
    """
    Return a combined set of lowercase email addresses from ALL active campaigns for this client.
    Also returns a dict mapping email → campaign_id for accurate tracking.
    Returns (None, {}) if no prospect lists configured (disables the filter — processes all replies).
    """
    import csv as _csv
    all_emails = set()
    email_to_campaign = {}
    campaigns = get_client_campaigns(client)
    any_csv_found = False

    for campaign in campaigns:
        csv_path = campaign.get('prospects_csv')
        if not csv_path:
            continue
        p = Path(csv_path)
        if not p.is_absolute():
            p = BASE_DIR.parent / csv_path
        if not p.exists():
            log(f"[ProspectFilter] prospects_csv not found: {p}")
            continue
        any_csv_found = True
        cid = campaign.get('instantly_campaign_id', '')
        try:
            with open(p, newline='', encoding='utf-8') as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    for col in row:
                        if col.strip().lower() in ('email', 'e-mail'):
                            email = row[col].strip().lower()
                            all_emails.add(email)
                            email_to_campaign[email] = cid
        except Exception as e:
            log(f"[ProspectFilter] Error reading {p}: {e}")

    if not any_csv_found:
        return None, {}
    return all_emails, email_to_campaign

def is_spam(msg, body):
    subject = msg.get('Subject', '').lower()
    spam_words = ['click here', 'you have won', 'congratulations', 'limited time offer',
                  'act now', 'free money', 'make money fast', 'work from home']
    if any(w in subject for w in spam_words):
        return True
    if len(body) > 8000 and 'meeting' not in body.lower() and 'call' not in body.lower():
        return True
    return False

def _send_email(outreach_email, app_password, sender_name, to_email, subject, body, retry=1,
                in_reply_to=None, references=None):
    """Send via Gmail SMTP with one retry. Pass in_reply_to/references for proper threading."""
    msg = MIMEMultipart('alternative')
    msg['From'] = f'{sender_name} <{outreach_email}>'
    msg['To'] = to_email
    # Decode encoded subject before checking for Re: prefix
    decoded_subject = email.header.decode_header(subject)[0][0]
    if isinstance(decoded_subject, bytes):
        decoded_subject = decoded_subject.decode('utf-8', errors='ignore')
    msg['Subject'] = decoded_subject if decoded_subject.lower().startswith('re:') else f'Re: {decoded_subject}'
    # Threading headers — critical for deliverability and inbox threading
    # Without these, Yahoo/Outlook treat the reply as a new cold email and spam-filter it
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = references or in_reply_to
    # Convert plain text body to clean HTML with proper spacing
    paragraphs = [p.strip() for p in body.strip().split('\n\n') if p.strip()]
    html_body = '\n'.join(f'<p style="margin-bottom:16px;">{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)
    html_body = f"""<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
{html_body}
</body></html>"""
    msg.attach(MIMEText(body, 'plain'))       # plain fallback
    msg.attach(MIMEText(html_body, 'html'))   # HTML preferred

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
- Positioning: {client.get('positioning_note', 'We help clients build sales pipelines and networks - we amplify their efforts, not replace them.')}
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
10b. Positioning: if a prospect asks what the service does, frame it as helping them build sales pipelines and physician/referral networks — a tool that amplifies their efforts. Never imply we replace their relationships or their sales process.

SECURITY ESCALATION RULES — escalate immediately, never respond:
11. INBOUND COLD PITCH: If the email is clearly someone pitching US (recruiting, selling services, vendor outreach, hiring companies, software sales, etc.) → escalate. These are not replies to our outreach.
12. MEDIA / PRESS: If the sender identifies as a journalist, reporter, blogger, or mentions writing an article, publishing, or press coverage → escalate.
13. LEGAL / REGULATORY: If the email mentions HIPAA, GDPR, CAN-SPAM, legal counsel, attorney, lawsuit, cease and desist, regulatory body, spam complaint, or any compliance authority → escalate.
14. FORWARDED / CC CHAIN: If the email contains forwarding headers ("---------- Forwarded message ----------", "From: X, Sent: Y, To: Z") or was clearly CCed to unknown third parties → escalate.
15. REPLY ON BEHALF OF: If the email is from an assistant, office manager, or anyone replying on behalf of the intended contact ("Dr. X asked me to respond", "I'm writing on behalf of...") → escalate. Do not respond to intermediaries.
16. NON-ENGLISH: If the email is not written in English → escalate. Do not attempt to classify or respond.
17. CONTEXT MISMATCH: If the reply content makes no sense as a response to our outreach — the person seems confused about who we are, has no memory of our email, or is clearly responding to something unrelated — escalate. Do not respond to confused or misdirected emails.
18. COMPETITOR MENTION: If the prospect names a direct competitor or asks us to compare ourselves to another service → escalate. Never engage with competitive comparisons.
19. PERSONAL / SENSITIVE: If the email contains personal health information, financial account details, social security numbers, or other sensitive PII not appropriate for cold email context → escalate immediately.
20. MULTIPLE SENDERS: If the reply appears to come from a group address, mailing list, or has multiple Reply-To addresses → escalate.

FORMATTING RULES (mandatory):
- Write in plain text with double line breaks between paragraphs (they will be rendered as HTML)
- Signature must ALWAYS be on its own line at the end, separated by a blank line: "{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Calendly link always on its own line, never embedded mid-sentence
- No em dashes (use hyphens or rephrase)
- 2-4 sentences max before signature

RESPONSE TONE EXAMPLES (adapt — never copy verbatim):
- Positive: "[name],\n\nThanks for getting back to me. Happy to connect - grab any time here:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Question: "[name],\n\nGreat question - that is exactly what I would want to cover on a quick call. Here is my calendar:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Not now: "[name],\n\nNo problem at all - I will leave it with you. Reach out anytime when the timing is better.\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Negative/remove: "[name],\n\nUnderstood, removing you now - sorry for the interruption.\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"

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
        imaplib.IMAP4_SSL.port = 993
        mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=30)
        mail.login(client['outreach_email'], client['app_password'])
        mail.select('inbox')

        # Search since yesterday — IMAP SINCE is date-only; catches manually-read emails; dedup prevents double-processing
        since_date = (datetime.utcnow() - timedelta(days=1)).strftime('%d-%b-%Y')
        _, raw = mail.search(None, f'SINCE {since_date}')
        msg_ids = raw[0].split() if raw[0] else []

        if not msg_ids:
            log(f"{label} No messages in last 24h.")
            mail.logout()
            return new_processed

        log(f"{label} {len(msg_ids)} message(s) in last 24h.")

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
            subject      = msg.get('Subject', '(no subject)')
            date_str     = msg.get('Date', '')
            body         = get_body(msg)
            message_id   = msg.get('Message-ID', '')
            references   = msg.get('References', message_id)

            if not body.strip():
                mail.store(msg_id, '+FLAGS', '\\Seen')
                continue

            # Deduplication — skip if we've already processed this message
            fingerprint = msg_fingerprint(from_email, subject, date_str, message_id)
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

            # Filter: prospect list — only respond to people we actually emailed
            prospect_emails, email_to_campaign = load_prospect_emails(client)
            if prospect_emails is not None and from_email.lower() not in prospect_emails:
                # Only escalate if it looks like a genuine reply to our outreach (subject starts with Re:)
                # Warming emails, spam, and cold outreach have their own subject lines — skip silently
                decoded_subject = subject.strip()
                is_reply_subject = decoded_subject.lower().startswith('re:')
                if is_reply_subject:
                    log(f"{label} Unknown sender replied (Re: subject, not in prospect list): {from_email} — escalating")
                    notify(
                        f"⚠️ *{client['firm_name']}* — Unknown sender replied\n"
                        f"👤 {from_name or from_email} `<{from_email}>`\n"
                        f"📋 Subject: _{subject}_\n"
                        f"Not in prospect list — may be a prospect replying from a different email address. "
                        f"Check manually and add to DNC or prospect list as needed."
                    )
                else:
                    log(f"{label} Skipping — not in prospect list (non-reply subject): {from_email}")
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
            escalate           = result.get('escalate', False)
            sent               = False
            approval_id        = None
            is_new_notification = True

            # ── ESCALATION — human must review, no auto-response ever
            if escalate:
                # Save to pending_approvals so Gob can read body and draft a response
                esc_id, esc_is_new = queue_pending(client, from_email, from_name, subject,
                                       draft='', classification='escalated',
                                       in_reply_to=message_id, references=references)
                # Overwrite draft field with the raw email body so it's readable
                pending = load_pending()
                for entry in pending:
                    if entry.get('id') == esc_id:
                        entry['prospect_message'] = body[:1000]
                        entry['escalate_reason'] = result.get('escalate_reason', 'Unknown')
                        break
                save_pending(pending)

                if esc_is_new:
                    notify(
                        f"🚨 *{firm}* — ESCALATION\n"
                        f"From: {from_name or from_email}\n"
                        f"Reason: {result.get('escalate_reason', 'Unknown')}\n"
                        f"Subject: _{subject}_\n\n"
                        f"*Their message:*\n```\n{body[:600]}\n```\n\n"
                        f"→ Tell Gob to draft a response, then approve/send manually."
                    )
                log_reply(cid, from_email, 'escalated', '', False, result.get('escalate_reason', ''))
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # ── DATABASE: log classification event
            if _DB_ENABLED:
                try:
                    _campaign_id = email_to_campaign.get(from_email.lower(), client.get('instantly_campaign_id',''))
                    _pid = _upsert_prospect(cid, _campaign_id,
                                            from_email, '', '', '', 'replied')
                    _log_event(cid, _pid, 'classified', {
                        'classification': classification,
                        'subject': subject,
                        'from_name': from_name
                    })
                except Exception as _e:
                    log(f"[DB] classify log failed: {_e}")

            # ── INSTANTLY: pause sequence on any real reply
            if classification not in ('ooo',) and not escalate:
                instantly_pause_contact(
                    from_email,
                    campaign_id=email_to_campaign.get(from_email.lower(), client.get('instantly_campaign_id'))
                )

            # ── HANDLE RESPONSE
            if should_respond and draft:
                if classification == 'negative':
                    # Always add to DNC and unsubscribe from Instantly
                    add_dnc(cid, from_email)
                    instantly_unsubscribe_contact(from_email)   # platform-level unsubscribe
                    # Auto-send removal ack ONLY in automated mode
                    # In draft_approval mode, queue for human review like everything else
                    if client['mode'] == 'automated':
                        if not TEST_MODE:
                            try:
                                _send_email(client['outreach_email'], client['app_password'],
                                            client['sender_name'], from_email, subject, draft,
                                            in_reply_to=message_id, references=references)
                                sent = True
                            except Exception as e:
                                log(f"SMTP error (removal ack): {e}")
                                notify(f"⚠️ *{firm}* — Failed to send removal ack to {from_email}: `{str(e)[:100]}`")
                        else:
                            log(f"[TEST] Would send removal ack to {from_email}")
                    elif client['mode'] == 'draft_approval':
                        approval_id, is_new_notification = queue_pending(client, from_email, from_name,
                                                    subject, draft, classification,
                                                    in_reply_to=message_id, references=references)
                        log(f"{label} Negative queued for approval (draft_approval mode): {from_email}")

                elif client['mode'] == 'automated':
                    is_new_notification = True
                    if not TEST_MODE:
                        try:
                            _send_email(client['outreach_email'], client['app_password'],
                                        client['sender_name'], from_email, subject, draft,
                                        in_reply_to=message_id, references=references)
                            sent = True
                        except Exception as e:
                            log(f"SMTP error: {e}")
                            notify(f"⚠️ *{firm}* — Failed to send to {from_email}: `{str(e)[:100]}`")
                    else:
                        log(f"[TEST] Would auto-send to {from_email}")

                elif client['mode'] == 'draft_approval':
                    approval_id, is_new_notification = queue_pending(client, from_email, from_name,
                                                subject, draft, classification,
                                                in_reply_to=message_id, references=references)

            # ── TELEGRAM NOTIFICATION
            emoji = {'positive': '🎯', 'question': '❓', 'not_now': '📅',
                     'negative': '🚫', 'ooo': '🏖', 'other': '⚠️'}.get(classification, '📬')

            if is_new_notification:
                campaign_name = client.get('campaign_name', '')
                msg_lines = [
                    f"{emoji} *{firm}* — {classification.upper()}",
                    f"📋 Campaign: {campaign_name}" if campaign_name else None,
                    f"👤 From: {from_name or from_email} `<{from_email}>`",
                    f"_{result.get('reasoning', '')}_ ",
                ]
                msg_lines = [l for l in msg_lines if l is not None]

                if approval_id and draft:
                    msg_lines += [
                        f"\n*Draft ready:*",
                        f"```\n{draft[:500]}\n```",
                        f"→ Reply *APPROVE {approval_id}* or *REJECT {approval_id}*",
                    ]
                elif sent:
                    msg_lines.append("✅ Auto-sent")
                elif TEST_MODE:
                    msg_lines.append("🔬 Test mode — not sent")

                notify('\n'.join(msg_lines))
            else:
                log(f"{label} Suppressing duplicate notification for {from_email}")

            mail.store(msg_id, '+FLAGS', '\\Seen')
            new_processed.add(fingerprint)
            log_reply(cid, from_email, classification, draft, sent,
                      result.get('notify_reason', ''))

            # ── DATABASE: log send/queue outcome
            if _DB_ENABLED:
                try:
                    _pid2 = _prospect_id(cid, from_email)
                    if sent:
                        _log_event(cid, _pid2, 'reply_sent', {'to': from_email, 'subject': subject})
                        _update_stage(_pid2, 'replied_by_us')
                    elif approval_id:
                        _log_event(cid, _pid2, 'draft_queued', {
                            'classification': classification,
                            'approval_id': approval_id
                        })
                except Exception as _e:
                    log(f"[DB] outcome log failed: {_e}")

            # ── CLIENT BOOKING ALERT: for positive replies, notify client to watch their calendar
            client_email = client.get('client_email', '')
            if classification == 'positive' and sent and client_email and not TEST_MODE:
                try:
                    prospect_display = from_name if from_name else from_email
                    booking_alert = f"""<p>Hi,</p>
<p>Quick heads up — we just sent a reply to <strong>{prospect_display}</strong> ({from_email}) on your behalf and included your booking link.</p>
<p>They indicated interest, so you may see a meeting land on your calendar soon.</p>
<p><strong>Please reply to this email or log in to confirm when the meeting is officially booked.</strong> This helps us track your results accurately.</p>
<p>If the meeting doesn't materialize within a few days, no action needed — we'll continue the follow-up sequence.</p>
<br>
<p>— ArgusReach</p>"""
                    _send_email(
                        client['outreach_email'], client['app_password'],
                        'ArgusReach', client_email,
                        f"[ArgusReach] Heads up — {prospect_display} may be booking",
                        booking_alert
                    )
                    log(f"{label} Booking alert sent to client ({client_email}) re: {from_email}")
                except Exception as _be:
                    log(f"{label} Booking alert failed (non-fatal): {_be}")

            # ── DB: record prospect + events
            if _DB_ENABLED:
                try:
                    _pid = upsert_prospect(
                        cid,
                        email_to_campaign.get(from_email.lower(), client.get('instantly_campaign_id', '')),
                        from_email, '', '', '', 'replied'
                    )
                    log_event(cid, _pid, 'classified', {
                        'classification': classification,
                        'subject': subject
                    })
                    if approval_id:
                        log_event(cid, _pid, 'draft_queued', {'classification': classification})
                    if sent:
                        log_event(cid, _pid, 'reply_sent', {'to': from_email})
                        update_prospect_stage(_pid, 'replied_by_us')
                    # Store follow-up date for OOO and not_now
                    follow_up_date = result.get('follow_up_date')
                    if follow_up_date and classification in ('ooo', 'not_now'):
                        try:
                            _set_follow_up_date(_pid, follow_up_date)
                            log(f"[DB] Follow-up date set for {from_email}: {follow_up_date}")
                        except Exception as _fe:
                            log(f"[DB] follow_up_date set failed (non-fatal): {_fe}")
                except Exception as _dbe:
                    log(f"DB write error (non-fatal): {_dbe}")

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

def check_stale_pending():
    """Re-alert if any pending approvals have been sitting unreviewed for 4+ hours."""
    try:
        pending = load_pending()
        if not pending:
            return
        now = datetime.utcnow()
        stale = []
        for entry in pending:
            queued_at = entry.get('queued_at', '')
            if not queued_at:
                continue
            try:
                queued_dt = datetime.fromisoformat(queued_at)
                age_hours = (now - queued_dt).total_seconds() / 3600
                if age_hours >= 4:
                    stale.append((entry, age_hours))
            except Exception:
                continue
        if stale:
            lines = [f"⏳ *{len(stale)} approval(s) waiting 4+ hours — action needed:*\n"]
            for entry, age in stale:
                h = int(age)
                lines.append(
                    f"• *{entry.get('firm_name','?')}* — {entry.get('classification','?').upper()} "
                    f"from {entry.get('from_name') or entry.get('from_email','?')} "
                    f"({h}h ago)\n  → APPROVE {entry['id']} or REJECT {entry['id']}"
                )
            notify('\n'.join(lines))
            log(f"Stale pending reminder sent: {len(stale)} item(s)")
    except Exception as e:
        log(f"[Stale pending check] error (non-fatal): {e}")


def _draft_reengagement(client, prospect_email, prospect_first_name):
    """Use Claude to draft a brief re-engagement email for a not-now/OOO prospect."""
    if not ai:
        return None
    try:
        fname = prospect_first_name or prospect_email
        prompt = f"""You are drafting a brief, warm re-engagement email for {client['sender_name']} at {client['firm_name']}.

This prospect ({fname}, {prospect_email}) previously replied saying they were busy or not ready. Their follow-up date has now arrived.

Write a short, friendly re-engagement email (2-3 sentences max before signature). 
- Reference that some time has passed since you last connected
- Keep it low-pressure — no pushy language
- End with the booking link on its own line
- Tone: {client.get('tone', 'warm-professional')}

FORMATTING RULES:
- Plain text, double line breaks between paragraphs
- Signature on its own line: "{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Booking link on its own line: {client['calendly_link']}
- No em dashes

Return ONLY the email body text, no subject line, no commentary."""

        resp = ai.messages.create(
            model='claude-haiku-4-5',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log(f"[Follow-up] Draft generation failed: {e}")
        return None


def check_due_followups():
    """Alert when OOO or not-now prospects have hit their follow-up date.
    Generates a re-engagement draft and queues it for approval."""
    if not _DB_ENABLED:
        return
    try:
        due = _get_due_followups()
        if not due:
            return

        # Load all clients for lookup
        all_clients_data = json.loads(CLIENTS_FILE.read_text())
        client_map = {c['id']: c for c in all_clients_data.get('clients', [])}

        for prospect in due:
            cid   = prospect['client_id']
            email = prospect['email']
            fname = prospect.get('first_name', '') or email
            stage = prospect.get('stage', '')
            log(f"[Follow-up] {email} ({cid}) is due for follow-up (was: {stage})")

            client = client_map.get(cid)
            draft  = None

            if client and ai_budget_ok():
                draft = _draft_reengagement(client, email, fname)
                ai_tick()

            if client and draft:
                # Queue draft for Vito's approval
                approval_id, is_new = queue_pending(
                    client, email, fname,
                    f"Re: follow-up — {fname}",
                    draft, 'not_now'
                )
                notify(
                    f"📅 *Follow-up Due* — {client.get('firm_name', cid)}\n"
                    f"👤 {fname} `{email}`\n"
                    f"Previously replied not-now or OOO. Draft re-engagement ready:\n\n"
                    f"```\n{draft[:400]}\n```\n"
                    f"→ Reply *APPROVE {approval_id}* or *REJECT {approval_id}*"
                )
            else:
                # Fallback: plain alert if no client config or AI unavailable
                notify(
                    f"📅 *Follow-up Due* — {cid}\n"
                    f"👤 {fname} `{email}`\n"
                    f"Previously replied OOO or not-now. No draft available — re-engage manually."
                )

            _mark_follow_up_sent(prospect['id'])
    except Exception as e:
        log(f"[Follow-up] check failed (non-fatal): {e}")


def check_campaign_cycles(clients):
    """Alert when a client's campaign is >75% complete — time to build next month's batch."""
    if not _DB_ENABLED:
        return
    CYCLE_STATE = BASE_DIR / 'monitor' / 'logs' / 'cycle_state.json'

    def load_state():
        return json.loads(CYCLE_STATE.read_text()) if CYCLE_STATE.exists() else {}

    def save_state(s):
        CYCLE_STATE.parent.mkdir(parents=True, exist_ok=True)
        CYCLE_STATE.write_text(json.dumps(s, indent=2))

    state = load_state()

    for client in clients:
        cid         = client['id']
        firm        = client.get('firm_name', cid)
        campaign_id = client.get('instantly_campaign_id', '')
        if not campaign_id:
            continue
        key = f"{cid}:{campaign_id}"
        if key in state:
            continue  # Already alerted for this campaign

        try:
            conn  = get_db()
            total = conn.execute(
                "SELECT COUNT(*) FROM prospects WHERE client_id=? AND campaign_id=?",
                (cid, campaign_id)
            ).fetchone()[0]
            done  = conn.execute(
                "SELECT COUNT(*) FROM prospects WHERE client_id=? AND campaign_id=? AND stage=?",
                (cid, campaign_id, 'sequence_complete')
            ).fetchone()[0]
            conn.close()

            if total < 10:
                continue  # Not enough data yet
            pct = done / total * 100
            if pct >= 75:
                state[key] = datetime.utcnow().isoformat()
                save_state(state)
                log(f"[Cycle] {firm}: {done}/{total} ({pct:.0f}%) complete — alerting")
                notify(
                    f"📅 *Campaign Winding Down — {firm}*\n\n"
                    f"{done}/{total} contacts have completed the sequence ({pct:.0f}%).\n\n"
                    f"Time to build next month's batch.\n"
                    f"I'll handle it automatically — just confirm the next month name.\n\n"
                    f"Reply: *CYCLE {cid} [Month Year]* (e.g. CYCLE {cid} April 2026)"
                )
        except Exception as e:
            log(f"[Cycle] Check failed for {cid} (non-fatal): {e}")


def _auto_activate_client(client_id, campaign_id, firm_name):
    """If Instantly campaign is live but clients.json says inactive, auto-activate."""
    try:
        data = json.loads(CLIENTS_FILE.read_text())
        for c in data.get('clients', []):
            if c['id'] == client_id and not c.get('active'):
                c['active']      = True
                c['launch_date'] = c.get('launch_date') or datetime.utcnow().strftime('%Y-%m-%d')
                CLIENTS_FILE.write_text(json.dumps(data, indent=2))
                log(f"[AutoActivate] {firm_name} is live in Instantly — auto-activated in clients.json")
                notify(
                    f"✅ *{firm_name}* campaign detected as live in Instantly.\n"
                    f"Monitor is now watching for replies."
                )
                break
    except Exception as e:
        log(f"[AutoActivate] Failed for {client_id}: {e}")


def sync_instantly_stages(clients):
    """Pull lead statuses from Instantly and update prospect stages in DB.
    Also auto-activates clients when their Instantly campaign goes live.
    Instantly campaign status: 0=draft, 1=active, 2=paused, 3=completed
    Instantly lead status codes: 1=active, 2=paused, 3=replied, 4=unsubscribed, 5=bounced, 6=completed
    """
    if not _DB_ENABLED:
        return

    # Also check ALL clients (not just active) for auto-activation
    try:
        all_clients_data = json.loads(CLIENTS_FILE.read_text())
        all_clients      = all_clients_data.get('clients', [])
    except Exception:
        all_clients = []

    for c in all_clients:
        cid         = c['id']
        campaign_id = c.get('instantly_campaign_id', '')
        if not campaign_id or c.get('active'):
            continue  # Skip active clients and those without a campaign
        try:
            resp = requests.get(
                f'https://api.instantly.ai/api/v2/campaigns/{campaign_id}',
                headers={'Authorization': f'Bearer {INSTANTLY_API_KEY}'},
                timeout=10
            )
            if resp.status_code == 200:
                campaign_status = resp.json().get('status', 0)
                if campaign_status == 1:  # Active in Instantly
                    _auto_activate_client(cid, campaign_id, c.get('firm_name', cid))
        except Exception as e:
            log(f"[AutoActivate] Check failed for {cid}: {e}")

    STAGE_MAP = {
        6: 'sequence_complete',
        4: 'unsubscribed',
        5: 'bounced',
    }
    for client in clients:
        cid         = client['id']
        campaign_id = client.get('instantly_campaign_id', '')
        if not campaign_id:
            continue
        try:
            page_cursor = None
            processed   = 0
            while True:
                payload = {'campaign': campaign_id, 'limit': 100}
                if page_cursor:
                    payload['starting_after'] = page_cursor
                resp = requests.post(
                    'https://api.instantly.ai/api/v2/leads/list',
                    headers={'Authorization': f'Bearer {INSTANTLY_API_KEY}', 'Content-Type': 'application/json'},
                    json=payload, timeout=20
                )
                if resp.status_code != 200:
                    break
                data  = resp.json()
                leads = data.get('items', [])
                if not leads:
                    break
                for lead in leads:
                    status = lead.get('status')
                    email  = lead.get('email', '').lower().strip()
                    if not email or status not in STAGE_MAP:
                        continue
                    new_stage = STAGE_MAP[status]
                    pid = _prospect_id(cid, email)
                    try:
                        update_prospect_stage(pid, new_stage)
                    except Exception:
                        pass
                    # Add bounced/unsubscribed to DNC
                    if status in (4, 5):
                        add_dnc(cid, email)
                    processed += 1
                # Pagination
                if not data.get('next_starting_after'):
                    break
                page_cursor = data['next_starting_after']

            if processed:
                log(f"[Sync] {cid}: updated {processed} prospect stages from Instantly")
        except Exception as e:
            log(f"[Sync] Stage sync failed for {cid} (non-fatal): {e}")


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
        log(f"Daily digest: no replies processed today. Skipping notification.")
        return

    counts = {}
    for r in today_entries:
        c = r.get('classification', 'other')
        counts[c] = counts.get(c, 0) + 1

    pending = load_pending()
    actionable = counts.get('positive', 0) + counts.get('escalated', 0) + len(pending)

    # Only notify if there's something requiring action
    if actionable == 0:
        log(f"Daily digest: {len(today_entries)} replies processed, none requiring action. Skipping notification.")
        return

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
    clients = [c for c in data['clients'] if c.get('active', False)]
    # Safety: warn loudly if any client is in automated mode
    for c in clients:
        if c.get('mode') == 'automated':
            log(f"⚠️  WARNING: {c.get('firm_name', c['id'])} is in AUTOMATED mode — emails will send without approval")
            notify(f"⚠️ *WARNING:* `{c.get('firm_name', c['id'])}` is in *AUTOMATED mode*. Emails will send without your approval. Set mode to `draft_approval` in clients.json to require approval.")
    return clients

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def run():
    mode_tag = " [TEST MODE]" if TEST_MODE else ""
    log(f"ArgusReach Monitor v2 starting{mode_tag}")
    log(f"ArgusReach Monitor v2 started. Watching all active client inboxes, checking every {POLL_INTERVAL//60} min.")

    processed_ids = load_processed()

    while True:
        try:
            clients = load_clients()
            if not clients:
                log("No active clients. Waiting...")
            else:
                for client in clients:
                    try:
                        new_ids = process_client(client, processed_ids)
                        processed_ids.update(new_ids)
                    except Exception as client_err:
                        firm = client.get('firm_name', client.get('id', '?'))
                        log(f"[{firm}] ⚠️ Client processing error (skipping, others unaffected): {client_err}")
                        notify(f"⚠️ *{firm}* — Monitor error this cycle: `{str(client_err)[:150]}`\nOther clients unaffected. Will retry next cycle.")
                save_processed(processed_ids)

            check_telegram_commands()
            maybe_send_digest()
            check_due_followups()
            check_stale_pending()
            # Sync Instantly lead statuses to DB every hour + check cycle completion
            if hasattr(run, '_last_sync') and (datetime.utcnow() - run._last_sync).seconds < 3600:
                pass
            else:
                sync_instantly_stages(clients if clients else [])
                check_campaign_cycles(clients if clients else [])
                run._last_sync = datetime.utcnow()

        except Exception as e:
            log(f"Main loop error: {e}")

        # Write heartbeat so external health checks can verify monitor is alive
        try:
            heartbeat_file = LOG_DIR / 'monitor_heartbeat.txt'
            heartbeat_file.write_text(datetime.utcnow().isoformat())
        except Exception:
            pass

        log(f"Cycle complete. Next check in {POLL_INTERVAL // 60} min.\n")
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    if not ANTHROPIC_API_KEY:
        print("WARNING: ANTHROPIC_API_KEY not set. AI classification will not work.")
        print("Set it: export ANTHROPIC_API_KEY=sk-ant-...")
    run()
