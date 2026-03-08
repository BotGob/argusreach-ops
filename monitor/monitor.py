#!/usr/bin/env python3
"""
ArgusReach Reply Monitor
Monitors client outreach inboxes, classifies replies, drafts and sends responses.
Runs continuously, checks every 10 minutes.

Setup: python3 monitor.py
Config: clients.json
Logs: logs/replies.json
"""

import imaplib
import smtplib
import email
import email.utils
import json
import os
import time
import sys
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from anthropic import Anthropic

# ── CONFIG ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CLIENTS_FILE = BASE_DIR / 'clients.json'
LOG_FILE = BASE_DIR / 'logs' / 'replies.json'

TELEGRAM_BOT_TOKEN = os.environ.get('ARGUSREACH_BOT_TOKEN', '8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk')
TELEGRAM_CHAT_ID = os.environ.get('ARGUSREACH_CHAT_ID', '8135725412')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

POLL_INTERVAL_SECONDS = 600  # 10 minutes
LOOKBACK_HOURS = 12           # how far back to check for unread replies

# ── INIT ─────────────────────────────────────────────────────────────────────
(BASE_DIR / 'logs').mkdir(exist_ok=True)
ai = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── HELPERS ──────────────────────────────────────────────────────────────────
def load_clients():
    with open(CLIENTS_FILE) as f:
        data = json.load(f)
    return [c for c in data['clients'] if c.get('active', False)]

def notify_vito(text):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'},
            timeout=10
        )
    except Exception as e:
        print(f'Telegram notify failed: {e}')

def log_event(client_id, prospect_email, classification, draft, sent, notes=''):
    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except Exception:
        log = []
    log.append({
        'timestamp': datetime.now().isoformat(),
        'client': client_id,
        'prospect': prospect_email,
        'classification': classification,
        'draft_preview': draft[:200] if draft else '',
        'sent': sent,
        'notes': notes
    })
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)

def get_email_body(msg):
    """Extract plain text body from email message."""
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

def is_automated_sender(from_email):
    """Filter out delivery failures, OOO auto-replies, etc."""
    automated = ['mailer-daemon', 'postmaster', 'noreply', 'no-reply',
                 'donotreply', 'do-not-reply', 'bounce', 'notification']
    return any(a in from_email.lower() for a in automated)

# ── CORE LOGIC ───────────────────────────────────────────────────────────────
def classify_and_draft(reply_body, prospect_name, prospect_email, subject, client):
    """Use Claude to classify the reply and draft an appropriate response."""
    prompt = f"""You are managing outbound sales email for {client['sender_name']} at {client['firm_name']}.

CLIENT CONTEXT:
- Sender name: {client['sender_name']}
- Firm: {client['firm_name']}
- Industry vertical: {client['vertical']}
- Ideal client: {client['icp_summary']}
- Tone: {client.get('tone', 'warm-professional')}
- Compliance note: {client.get('compliance_note', 'none')}
- Calendly booking link: {client['calendly_link']}

PROSPECT WHO REPLIED:
- Name: {prospect_name}
- Email: {prospect_email}
- Subject: {subject}

THEIR REPLY:
---
{reply_body[:1500]}
---

Classify this reply and draft the ideal response.

Classification options:
- "positive": expressed interest, wants to connect, asked about scheduling
- "question": asked a genuine question before deciding
- "not_now": not right time but not a hard no (follow up later)
- "negative": not interested, asked to stop, clear rejection
- "ooo": out of office auto-reply
- "other": unclear, spam, unrelated

Response rules:
- Match {client['sender_name']}'s {client.get('tone', 'warm-professional')} tone exactly
- For positive: acknowledge their interest warmly, provide the Calendly link naturally, keep it brief
- For question: answer directly and concisely, then softly offer to connect
- For not_now: acknowledge graciously, say you'll check back in [timeframe they mentioned or 60 days]
- For negative: brief, gracious acknowledgment, remove them ("I've taken care of that")
- For ooo: no response needed, note return date if mentioned
- NEVER mention ArgusReach — you are {client['sender_name']}
- Sign off as {client['sender_name']}
- Keep responses short — 3-5 sentences max for positive/question

Return ONLY valid JSON, no markdown:
{{
  "classification": "positive|question|not_now|negative|ooo|other",
  "reasoning": "one sentence explanation",
  "should_respond": true,
  "draft_response": "the full email body to send",
  "notify_vito": true,
  "notify_reason": "why vito should know, or empty string",
  "follow_up_date": "YYYY-MM-DD if not_now, else null",
  "urgency": "high|medium|low"
}}"""

    try:
        response = ai.messages.create(
            model='claude-haiku-4-5',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = response.content[0].text.strip()
        # Strip markdown code blocks if present
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f'AI classification error: {e}')
        # Fallback: flag for manual review
        return {
            'classification': 'other',
            'reasoning': f'Classification failed: {str(e)[:100]}',
            'should_respond': False,
            'draft_response': '',
            'notify_vito': True,
            'notify_reason': 'Classification failed — needs manual review',
            'follow_up_date': None,
            'urgency': 'medium'
        }

def send_email(outreach_email, app_password, sender_name, to_email, subject, body):
    """Send email via Gmail SMTP."""
    msg = MIMEMultipart('alternative')
    msg['From'] = f'{sender_name} <{outreach_email}>'
    msg['To'] = to_email
    msg['Subject'] = subject if subject.lower().startswith('re:') else f'Re: {subject}'
    msg.attach(MIMEText(body, 'plain'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(outreach_email, app_password)
        smtp.send_message(msg)

# ── PER-CLIENT PROCESSING ────────────────────────────────────────────────────
def process_client(c):
    label = f"[{c['firm_name']}]"
    print(f"{datetime.now().strftime('%H:%M:%S')} {label} Checking inbox...")

    try:
        # Connect IMAP
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(c['outreach_email'], c['app_password'])
        mail.select('inbox')

        # Search for unread messages in the lookback window
        since_date = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).strftime('%d-%b-%Y')
        _, msg_ids_raw = mail.search(None, f'(SINCE {since_date} UNSEEN)')
        msg_ids = msg_ids_raw[0].split() if msg_ids_raw[0] else []

        if not msg_ids:
            print(f"{label} No new replies.")
            mail.logout()
            return

        print(f"{label} {len(msg_ids)} new message(s) found.")

        for msg_id in msg_ids:
            _, data = mail.fetch(msg_id, '(RFC822)')
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_name, from_email = email.utils.parseaddr(msg.get('From', ''))
            subject = msg.get('Subject', '(no subject)')
            body = get_email_body(msg)

            if not body.strip():
                continue

            if is_automated_sender(from_email):
                print(f"{label} Skipping automated sender: {from_email}")
                mail.store(msg_id, '+FLAGS', '\\Seen')
                continue

            print(f"{label} Processing reply from {from_name} <{from_email}>")

            # Classify and draft
            result = classify_and_draft(body, from_name, from_email, subject, c)
            classification = result['classification']
            draft = result.get('draft_response', '')
            should_respond = result.get('should_respond', False)
            sent = False

            # Decide whether to send
            if should_respond and draft:
                if classification == 'negative':
                    # Always auto-send removal acknowledgment
                    send_email(c['outreach_email'], c['app_password'],
                               c['sender_name'], from_email, subject, draft)
                    sent = True
                    print(f"{label} Auto-sent removal acknowledgment to {from_email}")

                elif c['mode'] == 'automated':
                    send_email(c['outreach_email'], c['app_password'],
                               c['sender_name'], from_email, subject, draft)
                    sent = True
                    print(f"{label} Auto-sent response to {from_email}")

                # draft_approval mode: draft is prepared but not sent
                # Vito gets it via Telegram to approve

            # Notify Vito
            if result.get('notify_vito') or classification in ('positive', 'question', 'other'):
                emoji = {'positive': '🎯', 'question': '❓', 'not_now': '📅',
                         'negative': '🚫', 'ooo': '🏖️', 'other': '⚠️'}.get(classification, '📬')

                notification = (
                    f"{emoji} *{c['firm_name']}* — {classification.upper()}\n"
                    f"From: {from_name or from_email}\n"
                    f"_{result['reasoning']}_\n"
                )

                if c['mode'] == 'draft_approval' and should_respond and draft and not sent:
                    notification += f"\n*Draft ready for approval:*\n```\n{draft[:600]}\n```\n"
                    notification += f"\n_Reply 'SEND {c['id']} {from_email}' to approve_"
                elif sent:
                    notification += f"\n✅ Auto-responded"

                notify_vito(notification)

            # Mark as read
            mail.store(msg_id, '+FLAGS', '\\Seen')

            # Log
            log_event(c['id'], from_email, classification, draft, sent,
                      result.get('notify_reason', ''))

        mail.logout()

    except imaplib.IMAP4.error as e:
        err = f"IMAP error for {c['outreach_email']}: {e}"
        print(err)
        notify_vito(f"⚠️ *{c['firm_name']}* inbox error: `{str(e)[:150]}`")
    except Exception as e:
        err = f"Unexpected error for {c['firm_name']}: {e}"
        print(err)
        notify_vito(f"⚠️ *{c['firm_name']}* monitor error: `{str(e)[:150]}`")

# ── MAIN LOOP ────────────────────────────────────────────────────────────────
def run():
    print(f"ArgusReach Reply Monitor starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    notify_vito("✅ *ArgusReach Monitor* started — watching all active client inboxes")

    while True:
        try:
            clients = load_clients()
            if not clients:
                print("No active clients configured. Waiting...")
            for c in clients:
                process_client(c)
        except Exception as e:
            print(f"Main loop error: {e}")
        print(f"Cycle complete. Next check in {POLL_INTERVAL_SECONDS // 60} min.\n")
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == '__main__':
    run()
