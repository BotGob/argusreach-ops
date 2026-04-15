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
from ai.provider import generate_json as ai_generate_json, generate_text as ai_generate_text

try:
    from cryptography.fernet import Fernet as _Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
load_dotenv(BASE_DIR / '.env')   # load .env before reading os.environ below

# ── DATABASE ──────────────────────────────────────────────────────────────────
_DB_ENABLED = False
try:
    sys.path.insert(0, str(BASE_DIR.parent))
    from db.database import (
        init_db as _init_db, get_db,
        log_event, upsert_prospect, update_prospect_stage,
        prospect_id as _prospect_id,
        set_follow_up_date as _set_follow_up_date,
        get_due_followups as _get_due_followups,
        mark_follow_up_sent as _mark_follow_up_sent,
    )
    _init_db()
    _DB_ENABLED = True
except Exception as _db_err:
    print(f"[DB] Warning: database layer not available: {_db_err}")
    def log_event(*a, **k): pass
    def upsert_prospect(*a, **k): return None
    def update_prospect_stage(*a, **k): pass
    def get_db(): return None
    def _set_follow_up_date(*a, **k): pass
    def _get_due_followups(*a, **k): return []
    def _mark_follow_up_sent(*a, **k): pass
    def _prospect_id(c, e): return hashlib.md5(f"{c}:{e}".encode()).hexdigest()

# Backwards-compat aliases used in older sections of this file
_log_event        = log_event
_upsert_prospect  = upsert_prospect
_update_stage     = update_prospect_stage

CLIENTS_FILE              = BASE_DIR / 'clients.json'
LOG_DIR                   = BASE_DIR / 'logs'
DNC_DIR                   = BASE_DIR / 'dnc'
REPLY_LOG                 = LOG_DIR / 'replies.json'
PENDING_FILE              = LOG_DIR / 'pending_approvals.json'
PROCESSED_FILE            = LOG_DIR / 'processed_ids.json'
MONITOR_LOG               = LOG_DIR / 'monitor.log'
COMPLETED_CAMPAIGNS_FILE  = LOG_DIR / 'completed_campaigns.json'

LOG_DIR.mkdir(exist_ok=True)
DNC_DIR.mkdir(exist_ok=True)

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
_CRED_KEY           = os.environ.get('CREDENTIAL_ENCRYPTION_KEY', '')

# ── CREDENTIAL DECRYPTION ─────────────────────────────────────────────────────
def _write_connection_status(client_id: str, status: str, error: str):
    """Write IMAP connection status to a log file so the portal can display it."""
    import zoneinfo as _zi
    status_file = LOG_DIR / 'connection_status.json'
    try:
        data = json.loads(status_file.read_text()) if status_file.exists() else {}
        data[client_id] = {
            'status': status,  # 'ok' or 'error'
            'error': error,
            'checked_at': datetime.now(_zi.ZoneInfo('America/New_York')).strftime('%Y-%m-%d %I:%M %p ET'),
        }
        status_file.write_text(json.dumps(data, indent=2))
    except Exception:
        pass  # non-fatal


def _get_app_password(client: dict) -> str:
    """Return decrypted app_password. Falls back to plaintext (backward compat)."""
    raw = client.get('app_password', '')
    if not raw:
        return raw
    # Only attempt Fernet decrypt if value looks like ciphertext (starts with gAAAA)
    if _FERNET_AVAILABLE and _CRED_KEY and raw.startswith('gAAAA'):
        try:
            f = _Fernet(_CRED_KEY.encode())
            return f.decrypt(raw.encode()).decode()
        except Exception as e:
            log(f"[WARN] Failed to decrypt app_password: {e} — falling back to plaintext")
    return raw

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

    # Atomic write — prevents corruption if monitor crashes mid-write
    tmp = PROCESSED_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(active))
    tmp.replace(PROCESSED_FILE)

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

def _resolve_campaign(client, from_email, email_to_campaign):
    """Return (campaign_id, campaign_name) for a specific prospect email.
    Uses email_to_campaign lookup (built from Instantly API per-campaign).
    Falls back to root client fields for legacy single-campaign clients.
    """
    cid = email_to_campaign.get(from_email.lower(), '')
    if cid:
        for camp in client.get('campaigns', []):
            if camp.get('instantly_campaign_id') == cid:
                return cid, camp.get('campaign_name', '')
    # Fallback
    return client.get('instantly_campaign_id', ''), client.get('campaign_name', '')


def queue_pending(client, from_email, from_name, subject, draft, classification,
                  in_reply_to=None, references=None, confidence=None,
                  campaign_id=None, campaign_name=None):
    """Queue a reply for Vito's approval.

    campaign_id / campaign_name: per-prospect campaign context from email_to_campaign lookup.
    Falls back to root client fields if not provided (legacy / single-campaign clients).
    """
    pending = load_pending()
    # Dedup: if entry already exists for this prospect, update silently — do NOT re-notify
    is_new = True
    existing_idx = next((i for i, e in enumerate(pending) if e.get('from_email') == from_email and e.get('client_id') == client['id']), None)
    if existing_idx is not None:
        log(f"[{client['firm_name']}] Pending entry already exists for {from_email} — updating silently, no duplicate alert")
        pending.pop(existing_idx)
        is_new = False  # suppress Telegram re-notification

    # Resolve per-prospect campaign context (multi-campaign aware)
    resolved_campaign_id   = campaign_id   or client.get('instantly_campaign_id', '')
    resolved_campaign_name = campaign_name or client.get('campaign_name', '')

    entry = {
        'id': f"{client['id']}:{from_email}:{int(time.time())}",
        'client_id':             client['id'],
        'firm_name':             client['firm_name'],
        'campaign_name':         resolved_campaign_name,
        'instantly_campaign_id': resolved_campaign_id,
        'client_email':          client.get('client_email', ''),
        'outreach_email':        client['outreach_email'],
        # app_password intentionally NOT stored here — looked up from clients.json at send time
        'sender_name':           client['sender_name'],
        'from_email':            from_email,
        'from_name':             from_name,
        'subject':               subject,
        'draft':                 draft,
        'classification':        classification,
        'confidence':            confidence,
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
            # Support both old field names (contacts/positive) and new (prospects/reply_interested)
            lcontacts  = last.get('prospects', last.get('contacts', '—'))
            lpositive  = last.get('reply_interested', last.get('positive', '—'))
            lmeetings  = last.get('meetings', '—')
            lines.append(f"Last month: {lcontacts} contacts · {lpositive} interested · {lmeetings} meetings")
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




def _normalize_text(value: str) -> str:
    import re as _re
    return _re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def _best_fallback_prospect_match(client: dict, from_name: str, from_email: str):
    """Conservative fallback match by name/company when sender email differs."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT email, first_name, last_name, company, stage FROM prospects WHERE client_id=?",
            (client['id'],)
        ).fetchall()
        conn.close()
    except Exception:
        return None

    sender_tokens = set(_normalize_text(from_name).split())
    email_local = (from_email or '').split('@', 1)[0].replace('.', ' ').replace('_', ' ')
    sender_tokens |= set(_normalize_text(email_local).split())

    for email, first, last, company, stage in rows:
        hay = ' '.join([first or '', last or '', company or ''])
        hay_tokens = set(_normalize_text(hay).split())
        overlap = sender_tokens & hay_tokens
        if len(overlap) >= 2:
            return {
                'email': email,
                'first_name': first,
                'last_name': last,
                'company': company,
                'stage': stage,
                'overlap': sorted(overlap),
            }
    return None

def load_prospect_emails(client):
    """
    Return a combined set of lowercase email addresses from ALL active campaigns for this client.
    Sources (in order): local prospects.csv → Instantly API leads.
    Returns (set, dict) always — empty set if nothing found (filter still enforced).
    Never returns None — filter is ALWAYS active when a campaign_id exists.
    """
    import csv as _csv
    all_emails = set()
    email_to_campaign = {}
    campaigns = get_client_campaigns(client)

    for campaign in campaigns:
        cid = campaign.get('instantly_campaign_id', '')

        # Source 1: local CSV
        csv_path = campaign.get('prospects_csv')
        if csv_path:
            p = Path(csv_path)
            if not p.is_absolute():
                p = BASE_DIR.parent / csv_path
            if p.exists():
                try:
                    with open(p, newline='', encoding='utf-8') as f:
                        reader = _csv.DictReader(f)
                        for row in reader:
                            for col in row:
                                if col.strip().lower() in ('email', 'e-mail'):
                                    e = row[col].strip().lower()
                                    if e and not is_warmup_domain(e):
                                        all_emails.add(e)
                                        email_to_campaign[e] = cid
                except Exception as ex:
                    log(f"[ProspectFilter] Error reading CSV {p}: {ex}")

        # Source 2: Instantly API — pull live lead list (authoritative)
        # Uses POST /api/v2/leads/list with cursor pagination (GET endpoint is 404)
        if cid and INSTANTLY_API_KEY:
            try:
                page_size = 100
                starting_after = None
                while True:
                    payload = {"campaign_id": cid, "limit": page_size}
                    if starting_after:
                        payload["starting_after"] = starting_after
                    resp = requests.post(
                        "https://api.instantly.ai/api/v2/leads/list",
                        headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=15
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    items = data.get("items", [])
                    for item in items:
                        e = item.get("email", "").strip().lower()
                        if e and not is_warmup_domain(e):
                            all_emails.add(e)
                            email_to_campaign[e] = cid
                    next_cursor = data.get("next_starting_after")
                    if not next_cursor or len(items) < page_size:
                        break
                    starting_after = next_cursor
            except Exception as ex:
                log(f"[ProspectFilter] Instantly API fetch failed for {cid}: {ex}")

    if all_emails:
        log(f"[ProspectFilter] {client.get('firm_name')} — {len(all_emails)} known prospects loaded")
    else:
        # No prospects found anywhere — if campaign exists, still enforce filter (empty = block all unknown)
        if any(c.get('instantly_campaign_id') for c in campaigns):
            log(f"[ProspectFilter] WARNING: No prospects found for {client.get('firm_name')} but campaign exists — all unknown senders will be skipped")

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

def is_warmup(msg, from_email):
    """Detect Instantly warmup emails — should be silently skipped, never queued."""
    import re, email as _email_lib
    raw_subject = msg.get('Subject', '')
    # Decode MIME-encoded subject (e.g. =?utf-8?q?...?=) before pattern matching
    try:
        parts = _email_lib.header.decode_header(raw_subject)
        subject = ''
        for part, enc in parts:
            if isinstance(part, bytes):
                subject += part.decode(enc or 'utf-8', errors='replace')
            else:
                subject += part
    except Exception:
        subject = raw_subject

    # Instantly warmup subjects contain "Micro Warmup" or "Warmup" tags
    if 'micro warmup' in subject.lower() or 'warmup' in subject.lower():
        return True
    # Instantly injects 7-char uppercase alphanumeric tracking codes (e.g. 3WXDVXJ, HHPBJHJ)
    if re.search(r'\b[A-Z0-9]{7}\b', subject):
        return True
    # Known warmup network domains — updated as new ones are seen
    if is_warmup_domain(from_email):
        return True
    return False


# Centralized warmup domain list — used by is_warmup AND load_prospect_emails
WARMUP_DOMAINS = [
    'arcmailnetworkpro.com', 'popitmarketing.com', 'heythinkitfirst.com',
    'mandategewinnen.de', 'danielyip.com', 'twodevecommerce.com', 'marketcommand.cfd',
    'briehost.com', 'userservicecenter.online', 'trymooreintelligent-solutions.com',
    'fomoaiconnect.cfd', 'acquireleadlabs.online', 'leadspezialist.de',
    'ritarikunta.com', 'airbyteflow.com', 'successfactorconsulting.com',
    'structuredsolutionshelp.help',
    # Added 2026-03-20 — confirmed warmup senders from Instantly test campaign
    'powersquareshift.com', 'torontocreator.website', 'draventarflowstep.com',
    'appprovelocityknowledgeco.com', 'dotfaf.com', 'lunargarden.shop',
    'sangerkhandesign.com',
    # TLD patterns common in warmup networks
]

def is_warmup_domain(email_addr: str) -> bool:
    """Check if an email address belongs to a known warmup network domain.
    Only blocks specific known domains — never TLD patterns (too broad, risks blocking real prospects)."""
    addr = email_addr.strip().lower()
    domain = addr.split('@')[-1] if '@' in addr else addr
    return any(d in domain for d in WARMUP_DOMAINS)

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
    if not client.get('calendly_link', '').strip():
        log(f"[Draft] calendly_link not set for {client.get('id')} — escalating instead of generating broken draft")
        return {
            'classification': 'other', 'reasoning': 'calendly_link not configured',
            'should_respond': False, 'escalate': True,
            'escalate_reason': 'calendly_link is not set in clients.json. Set it in the portal before monitor can draft responses.',
            'draft_response': '', 'notify_vito': True,
            'notify_reason': 'Config gap: calendly_link missing', 'follow_up_date': None, 'urgency': 'high'
        }

    sender_name = client.get('sender_name') or client.get('firm_name') or 'the team'
    firm_name   = client.get('firm_name') or client.get('sender_name') or 'our firm'
    vertical    = client.get('vertical') or 'professional services'

    prompt = f"""You are a reply routing assistant for {sender_name} at {firm_name}.

YOUR SOLE JOB: Classify this reply and draft a brief, safe response that routes interested prospects to a calendar booking. Nothing else.

CLIENT CONTEXT:
- Sender: {sender_name}, {firm_name}
- Vertical: {vertical}
- Tone: {client.get('tone', 'warm-professional')}
- Compliance notes: {client.get('compliance_note', 'none')}
- Positioning: {client.get('positioning_note', 'We help clients build sales pipelines and networks - we amplify their efforts, not replace them.')}
- Booking link: {client.get('calendly_link', '')}
- Meeting format: {client.get('_meeting_format', 'any')}
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
- Positive (in-person): "[name],\n\nThanks for getting back to me. Happy to connect - grab a time here and I will come to you:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Positive (phone): "[name],\n\nThanks for getting back to me. Happy to connect - grab a time here and I will give you a call:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Positive (zoom/any): "[name],\n\nThanks for getting back to me. Happy to connect - grab any time here:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Question (in-person): "[name],\n\nGreat question - that is exactly what I would want to cover when we meet. Grab a time and I will come to you:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Question (phone/zoom/any): "[name],\n\nGreat question - that is exactly what I would want to cover on a quick call. Here is my calendar:\n\n{client['calendly_link']}\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
Use the meeting format context above to pick the right tone. Never write "video call" or "Zoom" unless meeting_format is zoom.
- Not now: "[name],\n\nNo problem at all - I will leave it with you. Reach out anytime when the timing is better.\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"
- Negative/remove: "[name],\n\nUnderstood, removing you now - sorry for the interruption.\n\n{client['sender_name']}\n{client.get('title', 'Founder')}, {client['firm_name']}"

Return ONLY valid JSON (no markdown, no commentary):
{{
  "classification": "positive|question|not_now|negative|ooo|other",
  "confidence": 85,
  "reasoning": "one sentence max",
  "should_respond": true,
  "escalate": false,
  "escalate_reason": "",
  "draft_response": "full 2-4 sentence response or empty if escalate=true",
  "notify_vito": true,
  "notify_reason": "brief reason",
  "follow_up_date": null,
  "urgency": "high|medium|low"
}}

confidence: integer 0-100. How certain are you this classification is correct?
  95-100 = unmistakably clear
  75-94  = clear with minor ambiguity
  50-74  = plausible but uncertain — VITO SHOULD REVIEW CAREFULLY
  0-49   = very ambiguous — always escalate"""

    try:
        ai_tick()
        result = ai_generate_json('reply', prompt, max_tokens=600)
        raw = json.dumps(result)
        if raw.startswith('```'):
            raw = '\n'.join(raw.split('\n')[1:])
            if raw.endswith('```'):
                raw = raw[:-3]
        result = json.loads(raw.strip())

        # Substitute known placeholders in draft before returning
        draft = result.get('draft_response', '') or ''
        calendly = client.get('calendly_link', '')
        if calendly:
            draft = draft.replace('[BOOKING_LINK]', calendly)
            draft = draft.replace('[CALENDLY_LINK]', calendly)
            draft = draft.replace('[CALENDAR_LINK]', calendly)
        result['draft_response'] = draft

        # Block send if any unfilled placeholders remain
        import re as _re
        remaining = _re.findall(r'\[[A-Z_]{3,}\]', draft)
        if remaining:
            log(f"[Draft] Unfilled placeholders detected: {remaining} — escalating")
            result['should_respond'] = False
            result['escalate'] = True
            result['escalate_reason'] = f"Draft has unfilled placeholders: {remaining}. Set {[p.lower().strip('[]') for p in remaining]} in clients.json."

        return result
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
        mail.login(client['outreach_email'], _get_app_password(client))
        _write_connection_status(client['id'], 'ok', '')
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

        # Load prospect list ONCE per client — not per message (avoids Instantly API hammering)
        prospect_emails, email_to_campaign = load_prospect_emails(client)

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

            # Filter: Instantly warmup emails — never queue these
            if is_warmup(msg, from_email):
                log(f"{label} Skipping warmup email from: {from_email} — {subject[:60]}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # Filter: spam signals
            if is_spam(msg, body):
                log(f"{label} Skipping spam from: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                new_processed.add(fingerprint)
                continue

            # Filter: prospect list — ONLY process emails from known prospects
            # (loaded once before this loop — see above)
            if from_email.lower() not in prospect_emails:
                decoded_subject = subject.strip()
                is_reply_subject = decoded_subject.lower().startswith('re:')
                if is_reply_subject and not is_warmup(msg, from_email):
                    fallback = _best_fallback_prospect_match(client, from_name or '', from_email)
                    thread_preview = (body_text or '')[:800].strip()
                    context_lines = [
                        f"👤 {from_name or from_email} `<{from_email}>`",
                        f"📋 Subject: _{subject}_",
                        f"🧵 Thread preview: {thread_preview or '(no body text extracted)'}",
                    ]
                    if fallback:
                        context_lines.append(
                            f"🔎 Possible prospect match: {fallback['first_name'] or ''} {fallback['last_name'] or ''} <{fallback['email']}> / {fallback['company'] or ''}".strip()
                        )
                        context_lines.append(f"🧠 Match evidence: {', '.join(fallback.get('overlap', [])) or 'name/company overlap'}")
                    log(f"{label} Unknown sender replied (not in prospect list): {from_email} — flagging")
                    notify(
                        f"⚠️ *{client['firm_name']}* — Unknown sender replied\n"
                        + '\n'.join(context_lines) + '\n'
                        f"Not in our prospect list. May be replying from a different address. Check manually."
                    )
                else:
                    log(f"{label} Skipping — not a known prospect: {from_email} | {subject[:60]}")
                mail.store(msg_id, '+FLAGS', '\Seen')
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
                _cid, _cname = _resolve_campaign(client, from_email, email_to_campaign)
                esc_id, esc_is_new = queue_pending(client, from_email, from_name, subject,
                                       draft='', classification='escalated',
                                       in_reply_to=message_id, references=references,
                                       campaign_id=_cid, campaign_name=_cname)
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
                # DB: log classified event for escalations too — required for accurate metrics
                if _DB_ENABLED:
                    try:
                        _campaign_id = email_to_campaign.get(from_email.lower(), client.get('instantly_campaign_id',''))
                        _pid = _upsert_prospect(cid, _campaign_id, from_email, '', '', '', 'replied')
                        _log_event(cid, _pid, 'classified', {
                            'classification': 'escalated',
                            'subject': subject,
                            'from_name': from_name
                        })
                    except Exception as _e:
                        log(f"[DB] escalation classify log failed: {_e}")
                # If escalation is a config gap, don't fingerprint — monitor retries after fix
                escalate_reason = result.get('escalate_reason', '')
                is_config_gap = any(x in escalate_reason.lower() for x in [
                    'calendly_link', 'not set', 'not configured', 'missing', 'config gap'
                ])
                if is_config_gap:
                    log(f"{label} Config gap escalation for {from_email} — NOT marking processed, will retry after fix")
                else:
                    mail.store(msg_id, '+FLAGS', '\\Seen')
                    new_processed.add(fingerprint)
                continue

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
                                _send_email(client['outreach_email'], _get_app_password(client),
                                            client['sender_name'], from_email, subject, draft,
                                            in_reply_to=message_id, references=references)
                                sent = True
                            except Exception as e:
                                log(f"SMTP error (removal ack): {e}")
                                notify(f"⚠️ *{firm}* — Failed to send removal ack to {from_email}: `{str(e)[:100]}`")
                        else:
                            log(f"[TEST] Would send removal ack to {from_email}")
                    elif client['mode'] == 'draft_approval':
                        _cid, _cname = _resolve_campaign(client, from_email, email_to_campaign)
                        approval_id, is_new_notification = queue_pending(client, from_email, from_name,
                                                    subject, draft, classification,
                                                    in_reply_to=message_id, references=references,
                                                    campaign_id=_cid, campaign_name=_cname)
                        log(f"{label} Negative queued for approval (draft_approval mode): {from_email}")

                elif client['mode'] == 'automated':
                    is_new_notification = True
                    if not TEST_MODE:
                        try:
                            _send_email(client['outreach_email'], _get_app_password(client),
                                        client['sender_name'], from_email, subject, draft,
                                        in_reply_to=message_id, references=references)
                            sent = True
                        except Exception as e:
                            log(f"SMTP error: {e}")
                            notify(f"⚠️ *{firm}* — Failed to send to {from_email}: `{str(e)[:100]}`")
                    else:
                        log(f"[TEST] Would auto-send to {from_email}")

                elif client['mode'] == 'draft_approval':
                    _cid, _cname = _resolve_campaign(client, from_email, email_to_campaign)
                    approval_id, is_new_notification = queue_pending(client, from_email, from_name,
                                                subject, draft, classification,
                                                in_reply_to=message_id, references=references,
                                                confidence=result.get('confidence'),
                                                campaign_id=_cid, campaign_name=_cname)

            # ── TELEGRAM NOTIFICATION
            emoji = {'positive': '🎯', 'question': '❓', 'not_now': '📅',
                     'negative': '🚫', 'ooo': '🏖', 'other': '⚠️'}.get(classification, '📬')

            if is_new_notification:
                # Resolve campaign name from per-prospect campaign_id (multi-campaign aware)
                _reply_campaign_id = email_to_campaign.get(from_email.lower(), '')
                campaign_name = ''
                if _reply_campaign_id:
                    # Look up name from campaigns array, fall back to root campaign_name
                    for _camp in client.get('campaigns', []):
                        if _camp.get('instantly_campaign_id') == _reply_campaign_id:
                            campaign_name = _camp.get('campaign_name', '')
                            break
                if not campaign_name:
                    campaign_name = client.get('campaign_name', '')
                confidence = result.get('confidence')
                if confidence is not None:
                    try:
                        confidence = int(confidence)
                        conf_display = f" ({confidence}%{'⚠️ REVIEW' if confidence < 75 else ''})"
                    except Exception:
                        conf_display = ''
                else:
                    conf_display = ''
                msg_lines = [
                    f"{emoji} *{firm}* — {classification.upper()}{conf_display}",
                    f"📋 Campaign: {campaign_name}" if campaign_name else None,
                    f"👤 From: {from_name or from_email} `<{from_email}>`",
                    f"_{result.get('reasoning', '')}_ ",
                ]
                msg_lines = [l for l in msg_lines if l is not None]

                # Show the prospect's original email so Vito can reference it
                body_preview = body.strip()[:400] if body else ""
                if body_preview:
                    msg_lines += [
                        f"\n*Their email:*",
                        f"```\n{body_preview}\n```",
                    ]

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

            # ── CLIENT BOOKING ALERT + FOLLOW-UP TIMER: for positive replies
            client_email = client.get('client_email', '')
            if classification == 'positive' and sent and client_email and not TEST_MODE:
                try:
                    prospect_display = from_name if from_name else from_email
                    booking_alert = (
                        f"Hi,\n\n"
                        f"Quick heads up - we just sent a reply to {prospect_display} ({from_email}) "
                        f"on your behalf with your booking link included.\n\n"
                        f"They indicated interest - you may see a meeting land on your calendar soon. "
                        f"No action needed on your end.\n\n"
                        f"- ArgusReach"
                    )
                    _send_email(
                        client['outreach_email'], _get_app_password(client),
                        'ArgusReach', client_email,
                        f"[ArgusReach] Heads up - {prospect_display} may be booking",
                        booking_alert
                    )
                    log(f"{label} Booking alert sent to client ({client_email}) re: {from_email}")
                except Exception as _be:
                    log(f"{label} Booking alert failed (non-fatal): {_be}")

            # ── SET FOLLOW-UP TIMER: if positive reply sent, set 3-business-day follow-up
            # in case prospect doesn't book. Monitor will auto-nudge if no Calendly booking received.
            if classification == 'positive' and sent and _DB_ENABLED:
                try:
                    from datetime import date as _date, timedelta as _td
                    def _add_biz_days(d, n):
                        while n > 0:
                            d += _td(days=1)
                            if d.weekday() < 5:
                                n -= 1
                        return d
                    fu_date = _add_biz_days(_date.today(), 3).isoformat()
                    _pid_pos = upsert_prospect(
                        cid,
                        email_to_campaign.get(from_email.lower(), client.get('instantly_campaign_id', '')),
                        from_email, '', '', '', 'positive'
                    )
                    _set_follow_up_date(_pid_pos, fu_date)
                    log(f"{label} Follow-up timer set for {from_email} - nudge if no booking by {fu_date}")
                except Exception as _fe:
                    log(f"{label} Follow-up timer set failed (non-fatal): {_fe}")

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
                        log_event(cid, _pid, 'draft_queued', {
                            'classification': classification,
                            'confidence': result.get('confidence'),
                            'approval_id': approval_id,
                        })
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
        # Successful cycle — reset consecutive failure counter
        _consecutive_failures[client['id']] = 0

    except imaplib.IMAP4.error as e:
        err_str = str(e).lower()
        log(f"IMAP error {firm}: {e}")
        if 'authenticate' in err_str or 'invalid credentials' in err_str or 'login' in err_str:
            # Auth errors always alert immediately
            _consecutive_failures[client['id']] = 0
            notify(f"🔐 *{firm}* — bad app password\nIMAP authentication failed for `{client.get('outreach_email','')}`\nAsk the client to resubmit credentials via the portal resend link.")
        else:
            _consecutive_failures[client['id']] = _consecutive_failures.get(client['id'], 0) + 1
            if _consecutive_failures[client['id']] >= 2:
                notify(f"⚠️ *{firm}* IMAP error: `{str(e)[:150]}`")
            else:
                log(f"[{firm}] IMAP error (consecutive={_consecutive_failures[client['id']]}, suppressed until 2nd): {e}")
        _write_connection_status(client['id'], 'error', str(e)[:200])
    except Exception as e:
        log(f"Error processing {firm}: {e}")
        _consecutive_failures[client['id']] = _consecutive_failures.get(client['id'], 0) + 1
        if _consecutive_failures[client['id']] >= 2:
            notify(f"⚠️ *{firm}* monitor error: `{str(e)[:150]}`")
        else:
            log(f"[{firm}] Monitor error (consecutive={_consecutive_failures[client['id']]}, suppressed until 2nd): {e}")
        _write_connection_status(client['id'], 'error', str(e)[:200])

    return new_processed

# ── DAILY DIGEST ──────────────────────────────────────────────────────────────
_last_digest_day = None

# ── CONSECUTIVE FAILURE TRACKING ──────────────────────────────────────────────
# Only alert after 2+ consecutive failures per client — suppresses one-off timeouts
_consecutive_failures: dict = {}  # client_id -> int

def check_stale_pending():
    """Re-alert if any pending approvals have been sitting unreviewed for 4+ hours.
    Rate-limited to once per 24 hours to prevent alert flooding."""
    STALE_STATE_FILE = LOG_DIR / 'stale_reminder_state.json'
    STALE_COOLDOWN_HOURS = 6
    try:
        pending = load_pending()
        if not pending:
            # Clear state when queue is empty
            if STALE_STATE_FILE.exists():
                STALE_STATE_FILE.unlink()
            return

        now = datetime.utcnow()

        # Check cooldown — only fire once per 24h
        if STALE_STATE_FILE.exists():
            try:
                state = json.loads(STALE_STATE_FILE.read_text())
                last_sent = datetime.fromisoformat(state.get('last_sent', '2000-01-01'))
                hours_since = (now - last_sent).total_seconds() / 3600
                if hours_since < STALE_COOLDOWN_HOURS:
                    return  # Too soon — skip
            except Exception:
                pass  # Corrupt state, proceed

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
            # Save cooldown state
            STALE_STATE_FILE.write_text(json.dumps({'last_sent': now.isoformat()}))
    except Exception as e:
        log(f"[Stale pending check] error (non-fatal): {e}")


def _draft_reengagement(client, prospect_email, prospect_first_name):
    """Use Claude to draft a brief re-engagement email for a not-now/OOO prospect."""
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

        return ai_generate_text('reengagement', prompt, max_tokens=300).strip()
    except Exception as e:
        log(f"[Follow-up] Draft generation failed: {e}")
        return None


def _has_calendly_booking(client_id, prospect_email):
    """Check if a Calendly booking was received for this prospect."""
    if not _DB_ENABLED:
        return False
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT id FROM meetings WHERE client_id=? AND prospect_email=? LIMIT 1",
            (client_id, prospect_email.lower())
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def check_due_followups():
    """Alert when prospects have hit their follow-up date.
    - positive (no booking): auto-send nudge from client outreach email, no approval needed
    - not_now / OOO: draft re-engagement, queue for approval
    """
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

            # ── POSITIVE NO-BOOKING: check if they actually booked via Calendly
            if stage == 'positive':
                if _has_calendly_booking(cid, email):
                    log(f"[Follow-up] {email} already booked - skipping nudge")
                    _mark_follow_up_sent(prospect['id'])
                    continue
                # No booking — queue draft for Vito's approval. NEVER auto-send.
                client = client_map.get(cid)
                if client:
                    calendly_link = client.get('calendly_link', '')
                    nudge = (
                        f"Hi {fname},\n\n"
                        f"Just wanted to make sure my last email didn't get buried - "
                        f"still happy to connect if the timing works.\n\n"
                        f"{calendly_link}\n\n"
                        f"{client.get('sender_name', '')}\n"
                        f"{client.get('title', 'Founder')}, {client.get('firm_name', '')}"
                    )
                    _fu_cid = prospect.get('campaign_id', '')
                    _fu_cname = next((c.get('campaign_name','') for c in client.get('campaigns',[]) if c.get('instantly_campaign_id') == _fu_cid), client.get('campaign_name',''))
                    approval_id, is_new = queue_pending(
                        client, email, fname,
                        f"Re: follow-up — {fname}",
                        nudge, 'positive',
                        campaign_id=_fu_cid, campaign_name=_fu_cname
                    )
                    if is_new:
                        notify(
                            f"📬 *Follow-up Nudge Ready for Review* - {client.get('firm_name', cid)}\n"
                            f"👤 {fname} `{email}`\n"
                            f"Previously interested but hasn't booked yet. Draft queued — approve or edit before sending.\n"
                            f"Approval ID: `{approval_id}`"
                        )
                        log(f"[Follow-up] Nudge draft queued for approval: {email} ({cid})")
                    else:
                        log(f"[Follow-up] Nudge already pending for {email} — skipping duplicate")
                _mark_follow_up_sent(prospect['id'])
                continue

            client = client_map.get(cid)
            draft  = None

            if client and ai_budget_ok():
                draft = _draft_reengagement(client, email, fname)
                ai_tick()

            if client and draft:
                # Queue draft for Vito's approval
                _fu_cid2 = prospect.get('campaign_id', '')
                _fu_cname2 = next((c.get('campaign_name','') for c in client.get('campaigns',[]) if c.get('instantly_campaign_id') == _fu_cid2), client.get('campaign_name',''))
                approval_id, is_new = queue_pending(
                    client, email, fname,
                    f"Re: follow-up — {fname}",
                    draft, 'not_now',
                    campaign_id=_fu_cid2, campaign_name=_fu_cname2
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
        cid  = client['id']
        firm = client.get('firm_name', cid)
        # Loop ALL active campaigns (multi-campaign aware)
        for _camp in get_client_campaigns(client):
            campaign_id = _camp.get('instantly_campaign_id', '')
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
                    camp_label = _camp.get('campaign_name', campaign_id[:8])
                    log(f"[Cycle] {firm} / {camp_label}: {done}/{total} ({pct:.0f}%) complete — alerting")
                    notify(
                        f"📅 *Campaign Winding Down — {firm}*\n\n"
                        f"*{camp_label}*: {done}/{total} contacts have completed the sequence ({pct:.0f}%).\n\n"
                        f"Time to build next month's batch.\n"
                        f"I'll handle it automatically — just confirm the next month name.\n\n"
                        f"Reply: *CYCLE {cid} [Month Year]* (e.g. CYCLE {cid} April 2026)"
                    )
            except Exception as e:
                log(f"[Cycle] Check failed for {cid}/{campaign_id[:8]} (non-fatal): {e}")


def _auto_activate_client(client_id, campaign_id, firm_name):
    """Notify Vito that campaign is live in Instantly — manual Mark Campaign Live required in portal.
    Auto-activation disabled: Vito is the last gate before monitor starts watching.
    """
    try:
        data = json.loads(CLIENTS_FILE.read_text())
        for c in data.get('clients', []):
            if c['id'] == client_id and not c.get('active'):
                log(f"[AutoActivate] {firm_name} campaign is live in Instantly — waiting for Vito to Mark Campaign Live in portal")
                notify(
                    f"🟡 *{firm_name}* campaign is live in Instantly.\n\n"
                    f"Go to the portal and hit *Mark Campaign Live* to start the monitor."
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
        cid = client['id']
        # Loop ALL active campaigns (multi-campaign aware)
        for _camp in get_client_campaigns(client):
            campaign_id = _camp.get('instantly_campaign_id', '')
            if not campaign_id:
                continue
            _sync_one_campaign_stages(cid, campaign_id)


def _sync_one_campaign_stages(cid, campaign_id):
    """Sync Instantly lead statuses to DB for a single campaign."""
    STAGE_MAP = {6: 'sequence_complete', 4: 'unsubscribed', 5: 'bounced'}
    try:
        page_cursor = None
        processed   = 0
        while True:
            # Use GET /api/v2/leads with campaign param — /leads/list does NOT reliably
            # filter by campaign (returns workspace-level leads per MEMORY.md)
            params = {'campaign': campaign_id, 'limit': 100}
            if page_cursor:
                params['starting_after'] = page_cursor
            resp = requests.get(
                'https://api.instantly.ai/api/v2/leads',
                headers={'Authorization': f'Bearer {INSTANTLY_API_KEY}'},
                params=params, timeout=20
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
            log(f"[Sync] {cid}/{campaign_id[:8]}: updated {processed} prospect stages from Instantly")
    except Exception as e:
        log(f"[Sync] Stage sync failed for {cid}/{campaign_id[:8]} (non-fatal): {e}")


def check_campaign_completions(clients):
    """
    Check if any active client's Instantly campaign has status=3 (completed).
    Fires a Telegram alert once per campaign via completed_campaigns.json dedup.
    Non-fatal: exception on one client does not stop the cycle.
    """
    if not INSTANTLY_API_KEY:
        return

    # Load already-alerted campaign IDs
    alerted = []
    try:
        if COMPLETED_CAMPAIGNS_FILE.exists():
            alerted = json.loads(COMPLETED_CAMPAIGNS_FILE.read_text())
    except Exception as e:
        log(f"[CampaignComplete] Failed to load completed_campaigns.json: {e}")
        alerted = []

    changed = False

    for client in clients:
        cid  = client['id']
        firm = client.get('firm_name', cid)

        # Loop ALL active campaigns (multi-campaign aware)
        for _camp in get_client_campaigns(client):
            campaign_id   = _camp.get('instantly_campaign_id', '')
            campaign_name = _camp.get('campaign_name', campaign_id)

            if not campaign_id:
                continue
            if campaign_id in alerted:
                continue  # Already alerted — skip silently

            try:
                resp = requests.get(
                    f'https://api.instantly.ai/api/v2/campaigns/{campaign_id}',
                    headers={'Authorization': f'Bearer {INSTANTLY_API_KEY}'},
                    timeout=10
                )
                if resp.status_code != 200:
                    log(f"[CampaignComplete] API error for {firm} ({campaign_id}): {resp.status_code}")
                    continue

                data = resp.json()
                status = data.get('status', -1)
                api_name = data.get('name', campaign_name)

                if status != 3:
                    continue  # Not completed — skip

                log(f"[CampaignComplete] Campaign completed: {firm} — {api_name} ({campaign_id})")
                notify(
                    f"🏁 *Campaign Complete — {firm}*\n\n"
                    f"All prospects have been reached for the current campaign.\n\n"
                    f"*Campaign:* {api_name}\n"
                    f"*Client:* {firm}\n\n"
                    f"Next steps:\n"
                    f"• Reply with campaign details to build the next batch\n"
                    f"• Or close out the campaign if the client is offboarding\n\n"
                    f"Run: `python3 tools/monthly_cycle.py --client {cid} --month \"Month YYYY\"` to launch the next cycle."
                )

                alerted.append(campaign_id)
                changed = True

            except Exception as e:
                log(f"[CampaignComplete] Error checking {firm}/{campaign_id[:8]} (non-fatal): {e}")

    if changed:
        try:
            COMPLETED_CAMPAIGNS_FILE.write_text(json.dumps(alerted, indent=2))
        except Exception as e:
            log(f"[CampaignComplete] Failed to save completed_campaigns.json: {e}")


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
def validate_all_campaign_ids(clients: list) -> list[str]:
    """
    Validate every active client's campaign ID against the Instantly API.
    Returns list of error strings (empty = all good).
    Called on startup and each cycle. Any mismatch blocks processing and alerts Vito.
    """
    if not INSTANTLY_API_KEY:
        return []
    errors = []
    try:
        r = requests.get(
            "https://api.instantly.ai/api/v2/campaigns",
            headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}"},
            params={"limit": 100},
            timeout=15
        )
        if not r.ok:
            log(f"[CampaignValidation] Instantly API unavailable ({r.status_code}) — skipping validation this cycle")
            return []
        live_ids = {c["id"]: c["name"] for c in r.json().get("items", [])}
    except Exception as e:
        log(f"[CampaignValidation] Could not fetch campaigns from Instantly: {e}")
        return []

    for c in clients:
        firm = c.get("firm_name", c.get("id", "?"))
        # Validate ALL campaigns in the campaigns array (multi-campaign aware)
        client_campaigns = get_client_campaigns(c)
        if not client_campaigns or not any(x.get('instantly_campaign_id') for x in client_campaigns):
            errors.append(f"{firm}: no campaign ID set")
            continue
        for camp in client_campaigns:
            cid = camp.get('instantly_campaign_id', '')
            if not cid:
                continue
            if cid not in live_ids:
                errors.append(
                    f"{firm}: campaign ID '{cid}' NOT FOUND in Instantly. "
                    f"Valid IDs: {list(live_ids.keys())}"
                )
            else:
                log(f"[CampaignValidation] ✓ {firm} / {camp.get('campaign_name', cid[:8])} → '{live_ids[cid]}'")
    return errors

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
                # ── CAMPAIGN ID VALIDATION — block processing on mismatch ──
                id_errors = validate_all_campaign_ids(clients)
                if id_errors:
                    for err in id_errors:
                        log(f"[CampaignValidation] ❌ {err}")
                    notify(
                        "🚨 *CAMPAIGN ID MISMATCH — Monitor halted this cycle*\n\n"
                        + "\n".join(f"• {e}" for e in id_errors)
                        + "\n\nFix clients.json or the admin portal before next cycle."
                    )
                    # Skip all processing this cycle — do not touch real data with wrong IDs
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
            check_campaign_completions(clients if clients else [])
            # Sync Instantly lead statuses to DB every 15 min + check cycle completion
            if hasattr(run, '_last_sync') and (datetime.utcnow() - run._last_sync).seconds < 900:
                pass
            else:
                sync_instantly_stages(clients if clients else [])
                check_campaign_cycles(clients if clients else [])
                run._last_sync = datetime.utcnow()

            # Voice call trigger — fires calls for clients with voice_calling_enabled
            try:
                voice_clients = [c for c in (clients or []) if c.get('voice_calling_enabled') and c.get('active')]
                if voice_clients:
                    sys.path.insert(0, str(BASE_DIR.parent / 'tools'))
                    import call_trigger as _ct
                    import importlib as _il; _il.reload(_ct)
                    for vc in voice_clients:
                        _ct.run_call_trigger(vc['id'], dry_run=False)
            except Exception as _ve:
                log(f"Voice call trigger error (non-fatal): {_ve}")

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
