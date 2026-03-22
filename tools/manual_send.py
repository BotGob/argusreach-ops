#!/usr/bin/env python3
"""
manual_send.py — Send a reply email manually with full DB logging.

Usage:
  python3 tools/manual_send.py <client_id> <to_email> <subject> <body_file_or_text>

Example:
  python3 tools/manual_send.py argusreach djholleran@gmail.com "Re: Quick question" "Dave,\n\nHere is my calendar..."

Always logs: reply_received + draft_approved events to DB with correct client_id + campaign_id.
"""

import sys, json, smtplib, argparse
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from db.database import log_event, upsert_prospect, update_prospect_stage

CLIENTS_FILE = BASE_DIR / 'monitor' / 'clients.json'

def load_client(client_id):
    data = json.loads(CLIENTS_FILE.read_text())
    return next((c for c in data['clients'] if c.get('id') == client_id), None)

def send_email(outreach_email, app_password, sender_name, to_email, subject, body,
               in_reply_to=None, references=None):
    msg = MIMEMultipart()
    msg['From'] = f"{sender_name} <{outreach_email}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
    if references:
        msg['References'] = f"{references} {in_reply_to}".strip()
    msg.attach(MIMEText(body, 'plain'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(outreach_email, app_password)
        s.sendmail(outreach_email, to_email, msg.as_string())

def main():
    parser = argparse.ArgumentParser(description='Manual email send with DB logging')
    parser.add_argument('client_id', help='Client ID (e.g. argusreach)')
    parser.add_argument('to_email', help='Recipient email')
    parser.add_argument('subject', help='Email subject')
    parser.add_argument('body', help='Email body text (or path to .txt file)')
    parser.add_argument('--first-name', default='', help='Prospect first name')
    parser.add_argument('--last-name', default='', help='Prospect last name')
    parser.add_argument('--company', default='', help='Prospect company')
    parser.add_argument('--classification', default='manual', help='Reply classification')
    parser.add_argument('--in-reply-to', default=None, help='Message-ID to thread reply')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be sent without sending')
    args = parser.parse_args()

    client = load_client(args.client_id)
    if not client:
        print(f"❌ Client '{args.client_id}' not found")
        sys.exit(1)

    # Load body from file if it looks like a path
    body = args.body
    if body.endswith('.txt') and Path(body).exists():
        body = Path(body).read_text()

    # Replace booking link placeholder
    calendly = client.get('calendly_link', '')
    if calendly:
        body = body.replace('[BOOKING_LINK]', calendly)

    campaign_id = client.get('instantly_campaign_id', '')

    print(f"\n{'='*50}")
    print(f"CLIENT:   {client.get('firm_name')} ({args.client_id})")
    print(f"CAMPAIGN: {campaign_id}")
    print(f"FROM:     {client.get('outreach_email')}")
    print(f"TO:       {args.to_email}")
    print(f"SUBJECT:  {args.subject}")
    print(f"BODY:\n{body}")
    print(f"{'='*50}\n")

    if args.dry_run:
        print("🔬 Dry run — not sent. Remove --dry-run to send.")
        sys.exit(0)

    confirm = input("Send this email? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        sys.exit(0)

    # Send
    send_email(
        client['outreach_email'], client['app_password'],
        client.get('sender_name', ''), args.to_email, args.subject, body,
        in_reply_to=args.in_reply_to
    )
    print(f"✅ Sent to {args.to_email}")

    # Log to DB
    pid = upsert_prospect(
        args.client_id, campaign_id, args.to_email,
        args.first_name, args.last_name, args.company, stage='replied'
    )
    log_event(args.client_id, pid, 'draft_approved', {
        'classification': args.classification,
        'note': f'manual send via manual_send.py — subject: {args.subject}'
    })
    print(f"✅ Logged to DB — client: {args.client_id}, campaign: {campaign_id}, pid: {pid}")

if __name__ == '__main__':
    main()
