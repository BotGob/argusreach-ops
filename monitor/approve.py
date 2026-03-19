#!/usr/bin/env python3
"""
ArgusReach — Approval Handler
Called by Gob (OpenClaw) when Vito approves or rejects a pending draft.

Usage:
  python3 approve.py approve <id_or_all>
  python3 approve.py reject <id_or_all>
  python3 approve.py list

Examples:
  python3 approve.py approve all
  python3 approve.py reject pt_tampa_bay_test:silvana@gmail.com:1773616700
"""

import json
import smtplib
import sys
import email as email_lib
import email.header
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / '.env')

PENDING_FILE = BASE_DIR / 'logs' / 'pending_approvals.json'
REPLY_LOG    = BASE_DIR / 'logs' / 'replies.json'
MONITOR_LOG  = BASE_DIR / 'logs' / 'monitor.log'

# Database integration (non-fatal if unavailable)
_DB_ENABLED = False
try:
    sys.path.insert(0, str(BASE_DIR.parent))
    from db.database import get_db, upsert_prospect, log_event, update_prospect_stage
    _DB_ENABLED = True
except Exception:
    pass


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [approve] {msg}"
    print(line)
    with open(MONITOR_LOG, 'a') as f:
        f.write(line + '\n')


def load_pending():
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text())
        except Exception:
            return []
    return []


def save_pending(pending):
    PENDING_FILE.write_text(json.dumps(pending, indent=2))


def log_reply(client_id, prospect_email, classification, draft, sent, notes=''):
    entry = {
        'ts': datetime.now().isoformat(),
        'client': client_id,
        'prospect': prospect_email,
        'classification': classification,
        'draft_preview': draft[:200] if draft else '',
        'sent': sent,
        'notes': notes,
    }
    replies = []
    if REPLY_LOG.exists():
        try:
            replies = json.loads(REPLY_LOG.read_text())
        except Exception:
            pass
    replies.append(entry)
    REPLY_LOG.write_text(json.dumps(replies, indent=2))


def db_write_approval(entry, approved: bool):
    """Write approval/rejection outcome back to database."""
    if not _DB_ENABLED:
        return
    try:
        cid          = entry['client_id']
        from_email   = entry['from_email']
        from_name    = entry.get('from_name', '')
        classification = entry.get('classification', 'other')
        campaign_id  = entry.get('instantly_campaign_id', '')

        pid = upsert_prospect(cid, campaign_id, from_email,
                              from_name.split()[0] if from_name else '',
                              from_name.split()[-1] if from_name and ' ' in from_name else '',
                              '', 'replied')

        # Stage map: classification → prospect stage after approval
        stage_map = {
            'positive':  'meeting_booking',
            'question':  'engaged',
            'not_now':   'not_now',
            'negative':  'dnc',
            'ooo':       'ooo',
            'other':     'replied_by_us',
        }

        if approved:
            stage = stage_map.get(classification, 'replied_by_us')
            log_event(cid, pid, 'draft_approved', {
                'classification': classification,
                'approved_by': 'vito',
                'approved_at': datetime.utcnow().isoformat(),
            })
            update_prospect_stage(pid, stage)
            log(f"[DB] logged draft_approved for {from_email} → stage: {stage}")
        else:
            log_event(cid, pid, 'draft_rejected', {
                'classification': classification,
                'rejected_by': 'vito',
                'rejected_at': datetime.utcnow().isoformat(),
            })
            # On rejection, keep whatever stage they're at — don't advance
            log(f"[DB] logged draft_rejected for {from_email} (stage unchanged)")
    except Exception as e:
        log(f"[DB] write failed (non-fatal): {e}")


def send_email(outreach_email, app_password, sender_name, to_email,
               subject, body, in_reply_to=None, references=None):
    msg = MIMEMultipart('alternative')
    msg['From']    = f'{sender_name} <{outreach_email}>'
    msg['To']      = to_email

    decoded_subject = email_lib.header.decode_header(subject)[0][0]
    if isinstance(decoded_subject, bytes):
        decoded_subject = decoded_subject.decode('utf-8', errors='ignore')
    msg['Subject'] = decoded_subject if decoded_subject.lower().startswith('re:') else f'Re: {decoded_subject}'

    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
    if references:
        msg['References'] = references

    msg.attach(MIMEText(body, 'plain'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as smtp:
        smtp.login(outreach_email, app_password)
        smtp.send_message(msg)


def send_client_booking_alert(entry):
    """
    For positive replies: email the client (firm) to watch their calendar.
    Only fires when the classification was 'positive' and there's a client_email set.
    """
    classification = entry.get('classification', '')
    client_email   = entry.get('client_email', '')
    if classification != 'positive' or not client_email:
        return

    prospect_display = entry.get('from_name') or entry['from_email']
    firm_name        = entry.get('firm_name', '')

    body = f"""Hi,

Quick heads up — we just sent a reply to {prospect_display} ({entry['from_email']}) on your behalf and included your booking link.

They indicated interest, so you may see a meeting land on your calendar soon.

Please reply to this email or log in to confirm when the meeting is officially booked. This helps us track your results accurately.

If the meeting doesn't materialize within a few days, no action needed — we'll continue the follow-up sequence.

— ArgusReach"""

    try:
        send_email(
            outreach_email=entry['outreach_email'],
            app_password=entry['app_password'],
            sender_name='ArgusReach',
            to_email=client_email,
            subject=f"[ArgusReach] Heads up — {prospect_display} may be booking",
            body=body,
        )
        log(f"Booking alert sent to {client_email} re: {entry['from_email']}")
    except Exception as e:
        log(f"Booking alert failed (non-fatal): {e}")


def do_approve(entry):
    draft = entry.get('draft', '')
    if not draft:
        print(f"  ⚠️  No draft for {entry['from_email']} — nothing to send")
        return False

    try:
        send_email(
            outreach_email=entry['outreach_email'],
            app_password=entry['app_password'],
            sender_name=entry['sender_name'],
            to_email=entry['from_email'],
            subject=entry['subject'],
            body=draft,
            in_reply_to=entry.get('in_reply_to'),
            references=entry.get('references'),
        )
        log(f"✅ Sent to {entry['from_email']} | client: {entry['firm_name']} ({entry['client_id']}) | class: {entry.get('classification','?')}")
        log_reply(entry['client_id'], entry['from_email'],
                  entry.get('classification', 'unknown'), draft, True, 'approved by Vito')

        # Write outcome to DB
        db_write_approval(entry, approved=True)

        # Log reply_sent to DB — this is what powers total sent count in reporting
        try:
            from db.database import get_db, upsert_prospect, log_event
            cid = entry['client_id']
            pid = upsert_prospect(cid, entry.get('instantly_campaign_id',''),
                                  entry['from_email'], '', '', '', 'replied_by_us')
            log_event(cid, pid, 'reply_sent', {
                'to': entry['from_email'],
                'classification': entry.get('classification', ''),
                'subject': entry.get('subject', ''),
            })
        except Exception as _e:
            log(f"[DB] reply_sent log failed (non-fatal): {_e}")

        # Alert client if positive reply
        send_client_booking_alert(entry)

        return True
    except Exception as e:
        log(f"❌ SMTP error sending to {entry['from_email']}: {e}")
        print(f"  ❌ Send failed: {e}")
        return False


def cmd_list():
    pending = load_pending()
    if not pending:
        print("No pending approvals.")
        return
    for p in pending:
        print(f"\n{'─'*60}")
        print(f"ID:       {p['id']}")
        print(f"Client:   {p['firm_name']} ({p['client_id']})")
        print(f"Campaign: {p.get('campaign_name', p.get('client_id',''))}")
        print(f"From:     {p.get('from_name','')} <{p['from_email']}>")
        print(f"Subject:  {p['subject']}")
        print(f"Class:    {p.get('classification','')}")
        print(f"Queued:   {p.get('queued_at','')}")
        print(f"\nDraft:\n{p.get('draft','(empty)')}")
    print(f"\n{'─'*60}")
    print(f"Total: {len(pending)} pending")


def cmd_approve(target):
    pending = load_pending()
    if not pending:
        print("Nothing pending.")
        return

    if target == 'all':
        targets = list(pending)
    else:
        targets = [p for p in pending if p['id'] == target or
                   p['from_email'].lower() == target.lower()]
        if not targets:
            print(f"No entry found matching: {target}")
            print("IDs available:")
            for p in pending:
                print(f"  {p['id']}  ({p['from_email']}) — {p.get('firm_name','')}")
            return

    sent_ids = set()
    for entry in targets:
        print(f"\n→ Approving: {entry.get('from_name') or entry['from_email']} | {entry.get('firm_name','')} | {entry.get('classification','?')}")
        if do_approve(entry):
            sent_ids.add(entry['id'])

    remaining = [p for p in pending if p['id'] not in sent_ids]
    save_pending(remaining)
    print(f"\n✅ Sent {len(sent_ids)}, {len(remaining)} remaining in queue.")


def cmd_reject(target):
    pending = load_pending()
    if not pending:
        print("Nothing pending.")
        return

    if target == 'all':
        rejected  = list(pending)
        remaining = []
    else:
        rejected  = [p for p in pending if p['id'] == target or
                     p['from_email'].lower() == target.lower()]
        remaining = [p for p in pending if p not in rejected]

    for entry in rejected:
        log(f"Rejected draft for {entry['from_email']} | client: {entry.get('firm_name','?')} ({entry['client_id']})")
        log_reply(entry['client_id'], entry['from_email'],
                  entry.get('classification', 'unknown'),
                  entry.get('draft', ''), False, 'rejected by Vito')
        db_write_approval(entry, approved=False)

    save_pending(remaining)
    print(f"✅ Rejected {len(rejected)}, {len(remaining)} remaining in queue.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    arg = sys.argv[2] if len(sys.argv) > 2 else 'all'

    if cmd == 'list':
        cmd_list()
    elif cmd == 'approve':
        cmd_approve(arg)
    elif cmd == 'reject':
        cmd_reject(arg)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python3 approve.py [list|approve|reject] [id|all]")
        sys.exit(1)
