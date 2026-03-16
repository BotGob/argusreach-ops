#!/usr/bin/env python3
"""
ArgusReach — Monthly Client Report Generator
Usage: python3 monthly_report.py --client pt_tampa_bay_test --month "March 2026"

Pulls client config from clients.json, prompts for stats, generates HTML report,
sends to client email, saves copy to reports/ directory.
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent
CLIENTS_FILE = BASE_DIR / 'monitor' / 'clients.json'
REPORTS_DIR  = BASE_DIR / 'reports'
REPORTS_DIR.mkdir(exist_ok=True)

def history_path(client_id):
    return REPORTS_DIR / f"{client_id}_history.json"

def load_history(client_id):
    p = history_path(client_id)
    return json.loads(p.read_text()) if p.exists() else []

def save_history(client_id, history):
    history_path(client_id).write_text(json.dumps(history, indent=2))

# ── Load clients ───────────────────────────────────────────────────────────────
def load_clients():
    with open(CLIENTS_FILE) as f:
        data = json.load(f)
    return data['clients'] if isinstance(data, dict) and 'clients' in data else data

# ── HTML template ──────────────────────────────────────────────────────────────
def build_timeline_html(history):
    if not history:
        return ''
    rows = ''
    total_contacts = total_positive = total_meetings = 0
    for i, entry in enumerate(history):
        is_current = (i == len(history) - 1)
        bg = '#f0fdf4' if is_current else 'transparent'
        border = 'border-left:3px solid #4ade80;' if is_current else 'border-left:3px solid transparent;'
        label = ' <span style="font-size:0.65rem;background:#dcfce7;color:#15803d;padding:1px 6px;border-radius:99px;font-weight:600;letter-spacing:0.05em;">THIS MONTH</span>' if is_current else ''
        launch_tag = ' <span style="font-size:0.65rem;background:#e0f2fe;color:#0369a1;padding:1px 6px;border-radius:99px;font-weight:600;">LAUNCH</span>' if entry.get('launch') else ''
        contacts = entry.get('contacts', '—')
        positive = entry.get('positive', '—')
        meetings = entry.get('meetings', '—')
        if isinstance(contacts, int): total_contacts += contacts
        if isinstance(positive, int): total_positive += positive
        if isinstance(meetings, int): total_meetings += meetings
        rows += f"""<tr style="background:{bg};{border}">
          <td style="padding:10px 12px;font-size:0.8rem;font-weight:{'600' if is_current else '400'};color:#111827;white-space:nowrap;">{entry['month']}{launch_tag}{label}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#374151;text-align:center;">{contacts}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#374151;text-align:center;">{positive}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#15803d;font-weight:600;text-align:center;">{meetings}</td>
        </tr>"""
    months_active = len(history)
    return f"""
  <div style="padding:0 40px 32px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">Campaign History &nbsp;·&nbsp; Active {months_active} month{'s' if months_active != 1 else ''}</div>
    <table style="width:100%;border-collapse:collapse;font-size:0.8rem;">
      <thead>
        <tr style="border-bottom:1px solid #e5e7eb;">
          <th style="padding:6px 12px;text-align:left;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Month</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Contacts</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Positive</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Meetings</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
      <tfoot>
        <tr style="border-top:2px solid #e5e7eb;">
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;">Total</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;text-align:center;">{total_contacts}</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;text-align:center;">{total_positive}</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#15803d;text-align:center;">{total_meetings}</td>
        </tr>
      </tfoot>
    </table>
  </div>"""

def build_report_html(client, month, stats, notes, history=None):
    firm        = client['firm_name']
    campaign    = client.get('campaign_name', 'Campaign')
    sender_name = client.get('sender_name', 'Vito Resciniti')
    year        = datetime.now().year

    positive    = stats['positive']
    not_now     = stats['not_now']
    meetings    = stats['meetings']
    contacts    = stats['contacts']
    unsubs      = stats['unsubs']
    working     = notes['working']
    changing    = notes['changing']
    next_month  = notes['next_month']

    working_items  = ''.join(f'<li style="margin-bottom:8px;color:#374151;">{w}</li>' for w in working)
    changing_items = ''.join(f'<li style="margin-bottom:8px;color:#374151;">{c}</li>' for c in changing)
    timeline_html  = build_timeline_html(history or [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>ArgusReach — Monthly Report — {month}</title>
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

<div style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">

  <!-- Header -->
  <div style="background:#0f0f0f;padding:32px 40px;">
    <div style="font-size:0.7rem;letter-spacing:0.18em;text-transform:uppercase;color:#555;font-family:'Courier New',monospace;margin-bottom:8px;">ArgusReach</div>
    <div style="font-size:1.4rem;font-weight:700;color:#ffffff;letter-spacing:-0.02em;">Monthly Activity Report</div>
    <div style="font-size:0.875rem;color:#888;margin-top:6px;">{firm} &nbsp;·&nbsp; {month}</div>
  </div>

  <!-- Campaign info -->
  <div style="padding:24px 40px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">
    <table style="width:100%;font-size:0.8rem;color:#6b7280;">
      <tr>
        <td style="padding:2px 0;"><span style="color:#9ca3af;">Campaign</span></td>
        <td style="text-align:right;font-weight:600;color:#111827;">{campaign}</td>
      </tr>
      <tr>
        <td style="padding:2px 0;"><span style="color:#9ca3af;">Reporting Period</span></td>
        <td style="text-align:right;font-weight:600;color:#111827;">{month}</td>
      </tr>
      <tr>
        <td style="padding:2px 0;"><span style="color:#9ca3af;">Status</span></td>
        <td style="text-align:right;"><span style="background:#dcfce7;color:#15803d;font-size:0.7rem;font-weight:600;padding:2px 8px;border-radius:99px;letter-spacing:0.05em;">ACTIVE</span></td>
      </tr>
    </table>
  </div>

  <!-- Stats grid -->
  <div style="padding:32px 40px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:20px;">Activity This Month</div>

    <table style="width:100%;border-collapse:separate;border-spacing:0 8px;">
      <tr>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{contacts}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Contacts reached</div>
        </td>
        <td style="width:12px;"></td>
        <td style="background:#f0fdf4;border-radius:6px;padding:16px 20px;border:1px solid #bbf7d0;">
          <div style="font-size:1.75rem;font-weight:800;color:#15803d;letter-spacing:-0.03em;">{meetings}</div>
          <div style="font-size:0.78rem;color:#166534;margin-top:2px;">Meetings booked</div>
        </td>
      </tr>
      <tr>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{positive}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Positive replies</div>
        </td>
        <td style="width:12px;"></td>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{not_now}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Follow-up later</div>
        </td>
      </tr>
    </table>

    {f'<div style="margin-top:8px;background:#f9fafb;border-radius:6px;padding:12px 20px;font-size:0.78rem;color:#9ca3af;">{unsubs} unsubscribe(s)</div>' if unsubs > 0 else ''}
  </div>

  {timeline_html}

  <!-- What worked -->
  <div style="padding:0 40px 24px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">What Worked</div>
    <ul style="margin:0;padding-left:20px;line-height:1.7;">
      {working_items}
    </ul>
  </div>

  <!-- What we're changing -->
  <div style="padding:0 40px 24px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">What We're Adjusting</div>
    <ul style="margin:0;padding-left:20px;line-height:1.7;">
      {changing_items}
    </ul>
  </div>

  <!-- Next month -->
  <div style="padding:20px 40px;background:#f9fafb;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:8px;">Next Month</div>
    <p style="margin:0;font-size:0.875rem;color:#374151;line-height:1.7;">{next_month}</p>
  </div>

  <!-- Sign-off -->
  <div style="padding:28px 40px;">
    <p style="margin:0 0 4px;font-size:0.875rem;color:#374151;">Questions? Reply to this email anytime.</p>
    <p style="margin:0;font-size:0.875rem;font-weight:600;color:#111827;">{sender_name}</p>
    <p style="margin:0;font-size:0.78rem;color:#9ca3af;">ArgusReach &nbsp;·&nbsp; <a href="mailto:vito@argusreach.com" style="color:#9ca3af;text-decoration:none;">vito@argusreach.com</a></p>
  </div>

  <!-- Footer -->
  <div style="padding:16px 40px;background:#f9fafb;border-top:1px solid #e5e7eb;text-align:center;">
    <p style="margin:0;font-size:0.7rem;color:#d1d5db;">© {year} ArgusReach &nbsp;·&nbsp; Tampa Bay, FL &nbsp;·&nbsp; <a href="https://argusreach.com" style="color:#d1d5db;text-decoration:none;">argusreach.com</a></p>
  </div>

</div>
</body>
</html>"""


# ── Send email ─────────────────────────────────────────────────────────────────
def send_report(client, to_email, subject, html_body):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f"ArgusReach <{client['outreach_email']}>"
    msg['To']      = to_email
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(client['outreach_email'], client['app_password'])
        smtp.sendmail(client['outreach_email'], to_email, msg.as_string())
    print(f"✅ Report sent to {to_email}")


# ── Prompt for stats ───────────────────────────────────────────────────────────
def prompt_stats():
    print("\n── Monthly Stats ──────────────────────────────")
    contacts = int(input("Contacts reached this month: "))
    positive = int(input("Positive replies: "))
    not_now  = int(input("Not now / follow later: "))
    meetings = int(input("Meetings booked: "))
    unsubs   = int(input("Unsubscribes: "))

    print("\n── What Worked (enter items one per line, blank line to finish) ──")
    working = []
    while True:
        line = input("> ").strip()
        if not line: break
        working.append(line)

    print("\n── What We're Adjusting Next Month ──")
    changing = []
    while True:
        line = input("> ").strip()
        if not line: break
        changing.append(line)

    print("\n── Next Month Summary (one paragraph) ──")
    next_month = input("> ").strip()

    return (
        {'contacts': contacts, 'positive': positive, 'not_now': not_now,
         'meetings': meetings, 'unsubs': unsubs},
        {'working': working or ['Sequence delivered without issues.'],
         'changing': changing or ['Monitoring performance for adjustments next cycle.'],
         'next_month': next_month or 'Continuing current campaign with any optimizations applied.'}
    )


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Generate and send monthly ArgusReach client report')
    parser.add_argument('--client',  required=True,  help='Client ID from clients.json')
    parser.add_argument('--month',   required=True,  help='Report month e.g. "March 2026"')
    parser.add_argument('--to',      default=None,   help='Override recipient email')
    parser.add_argument('--preview', action='store_true', help='Save HTML only, do not send')
    args = parser.parse_args()

    clients = load_clients()
    client  = next((c for c in clients if c['id'] == args.client), None)
    if not client:
        print(f"❌ Client '{args.client}' not found in clients.json")
        sys.exit(1)

    to_email = args.to or client.get('client_email')
    if not to_email and not args.preview:
        to_email = input(f"Recipient email for {client['firm_name']}: ").strip()

    print(f"\n📊 Building report for: {client['firm_name']} — {args.month}")
    stats, notes = prompt_stats()

    # Load history, append this month, save
    history = load_history(args.client)
    is_launch = len(history) == 0
    # Update existing month entry if re-running, otherwise append
    existing = next((i for i, e in enumerate(history) if e['month'] == args.month), None)
    entry = {
        'month':    args.month,
        'launch':   is_launch,
        'contacts': stats['contacts'],
        'positive': stats['positive'],
        'not_now':  stats['not_now'],
        'meetings': stats['meetings'],
        'unsubs':   stats['unsubs'],
    }
    if existing is not None:
        history[existing] = entry
    else:
        history.append(entry)
    save_history(args.client, history)
    print(f"📁 History updated ({len(history)} month{'s' if len(history) != 1 else ''})")

    html = build_report_html(client, args.month, stats, notes, history=history)

    # Save copy
    safe_month = args.month.replace(' ', '-')
    out_path = REPORTS_DIR / f"{args.client}_{safe_month}.html"
    out_path.write_text(html)
    print(f"💾 Saved: {out_path}")

    if args.preview:
        print("👁  Preview mode — not sent. Open the file to review.")
        return

    subject = f"ArgusReach — Monthly Report — {client['firm_name']} — {args.month}"
    send_report(client, to_email, subject, html)


if __name__ == '__main__':
    main()
