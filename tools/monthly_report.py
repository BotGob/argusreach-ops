#!/usr/bin/env python3
"""
ArgusReach - Monthly Client Report Generator
Usage: python3 monthly_report.py --client pt_tampa_bay_test --month "March 2026"
       python3 monthly_report.py --client pt_tampa_bay_test --month "March 2026" --preview

Pulls all stats automatically from DB + Instantly API.
Only asks Vito for narrative (what worked, adjustments, next month).
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

import requests

BASE_DIR     = Path(__file__).parent.parent
CLIENTS_FILE = BASE_DIR / 'monitor' / 'clients.json'
REPORTS_DIR  = BASE_DIR / 'reports'
REPORTS_DIR.mkdir(exist_ok=True)
ENV_FILE     = BASE_DIR / 'monitor' / '.env'

def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
_load_env()

INSTANTLY_API_KEY = os.environ.get('INSTANTLY_API_KEY', '')
SENDER_EMAIL      = 'vito@argusreach.com'
SENDER_APP_PASS   = os.environ.get('ARGUSREACH_GMAIL_APP_PASS', '')


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_db():
    sys.path.insert(0, str(BASE_DIR))
    from db.database import get_db as _get_db
    return _get_db()


def get_month_bounds(month_str):
    """Return (start, end) ISO date strings for a given month like 'March 2026'."""
    dt = datetime.strptime(month_str, '%B %Y')
    import calendar
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    start = dt.strftime('%Y-%m-01')
    end   = f"{dt.year:04d}-{dt.month:02d}-{last_day:02d}"
    return start, end


def pull_db_stats(client_id, month_str):
    """Pull all numeric stats from DB for the given client + month."""
    conn = get_db()
    start, end = get_month_bounds(month_str)

    # Prospects loaded this month
    prospects = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM prospects WHERE client_id=? AND date(created_at) BETWEEN ? AND ?",
        (client_id, start, end)
    ).fetchone()[0]

    # Replies by classification this month
    reply_rows = conn.execute("""
        SELECT json_extract(metadata,'$.classification') as cls, COUNT(DISTINCT prospect_id) as cnt
        FROM events
        WHERE event_type='classified' AND client_id=?
          AND date(created_at) BETWEEN ? AND ?
        GROUP BY cls
    """, (client_id, start, end)).fetchall()
    breakdown = {r[0]: r[1] for r in reply_rows if r[0]}

    # Meetings this month
    meetings = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE client_id=? AND date(created_at) BETWEEN ? AND ?",
        (client_id, start, end)
    ).fetchone()[0]

    conn.close()

    return {
        'prospects':        prospects,
        'reply_interested': breakdown.get('positive', 0) + breakdown.get('question', 0),
        'reply_not_now':    breakdown.get('not_now', 0),
        'reply_negative':   breakdown.get('negative', 0),
        'reply_escalated':  breakdown.get('escalated', 0),
        'meetings':         meetings,
    }


def pull_instantly_analytics(campaign_id):
    """Pull emails_sent_count + emails_read_count (opens) from Instantly analytics API.
    Returns dict with 'sent' and 'open_count', or empty dict on failure."""
    if not INSTANTLY_API_KEY or not campaign_id:
        return {}
    try:
        resp = requests.get(
            'https://api.instantly.ai/api/v2/campaigns/analytics',
            headers={'Authorization': f'Bearer {INSTANTLY_API_KEY}'},
            params={'id': campaign_id},
            timeout=15
        )
        if not resp.ok:
            return {}
        data = resp.json()
        a = {}
        if isinstance(data, list):
            a = next((x for x in data if x.get('campaign_id') == campaign_id), {})
        elif isinstance(data, dict):
            a = data if data.get('campaign_id') == campaign_id else data.get(campaign_id, {})
        sent  = a.get('emails_sent_count') or 0
        opens = a.get('emails_read_count') or 0
        return {
            'sent':       sent or None,
            'open_count': opens or None,
            'open_rate':  f"{opens/sent*100:.1f}%" if sent > 0 else None,
        }
    except Exception as e:
        print(f"  ⚠️  Instantly analytics error: {e}")
    return {}


def pull_instantly_sent(campaign_id):
    """Legacy wrapper - returns emails_sent_count only."""
    return pull_instantly_analytics(campaign_id).get('sent')


# ── History ────────────────────────────────────────────────────────────────────

def history_path(client_id):
    return REPORTS_DIR / f"{client_id}_history.json"

def load_history(client_id):
    p = history_path(client_id)
    return json.loads(p.read_text()) if p.exists() else []

def save_history(client_id, history):
    history_path(client_id).write_text(json.dumps(history, indent=2))


# ── HTML report ────────────────────────────────────────────────────────────────

def build_timeline_html(history):
    if not history:
        return ''
    rows = ''
    total_prospects = total_interested = total_meetings = 0
    for i, entry in enumerate(history):
        is_current = (i == len(history) - 1)
        bg     = '#f0fdf4' if is_current else 'transparent'
        border = 'border-left:3px solid #4ade80;' if is_current else 'border-left:3px solid transparent;'
        label  = ' <span style="font-size:0.65rem;background:#dcfce7;color:#15803d;padding:1px 6px;border-radius:99px;font-weight:600;letter-spacing:0.05em;">THIS MONTH</span>' if is_current else ''
        launch = ' <span style="font-size:0.65rem;background:#e0f2fe;color:#0369a1;padding:1px 6px;border-radius:99px;font-weight:600;">LAUNCH</span>' if entry.get('launch') else ''
        p  = entry.get('prospects', '—')
        m  = entry.get('meetings', '—')
        r  = entry.get('reply_interested', '—')
        if isinstance(p, int): total_prospects  += p
        if isinstance(r, int): total_interested += r
        if isinstance(m, int): total_meetings   += m
        rows += f"""<tr style="background:{bg};{border}">
          <td style="padding:10px 12px;font-size:0.8rem;font-weight:{'600' if is_current else '400'};color:#111827;white-space:nowrap;">{entry['month']}{launch}{label}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#374151;text-align:center;">{p}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#374151;text-align:center;">{r}</td>
          <td style="padding:10px 12px;font-size:0.8rem;color:#15803d;font-weight:600;text-align:center;">{m}</td>
        </tr>"""
    months = len(history)
    return f"""
  <div style="padding:0 40px 32px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">Campaign History &nbsp;·&nbsp; Active {months} month{'s' if months != 1 else ''}</div>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #e5e7eb;">
          <th style="padding:6px 12px;text-align:left;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Month</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Prospects</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Interested</th>
          <th style="padding:6px 12px;text-align:center;font-size:0.65rem;color:#9ca3af;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;">Meetings</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
      <tfoot>
        <tr style="border-top:2px solid #e5e7eb;">
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;">Total</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;text-align:center;">{total_prospects}</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#111827;text-align:center;">{total_interested}</td>
          <td style="padding:10px 12px;font-size:0.78rem;font-weight:700;color:#15803d;text-align:center;">{total_meetings}</td>
        </tr>
      </tfoot>
    </table>
  </div>"""


def build_report_html(client, month, stats, notes, history=None):
    firm        = client.get('firm_name', 'Client')
    campaign    = client.get('campaign_name', 'Campaign')
    sender_name = client.get('sender_name', 'Vito Resciniti')
    year        = datetime.now().year

    prospects  = stats['prospects']
    sent       = stats.get('emails_sent', '—')
    open_count = stats.get('open_count')
    open_rate  = stats.get('open_rate')
    interested = stats['reply_interested']
    not_now    = stats['reply_not_now']
    meetings   = stats['meetings']

    working_items  = ''.join(f'<li style="margin-bottom:8px;color:#374151;">{w}</li>' for w in notes['working'])
    changing_items = ''.join(f'<li style="margin-bottom:8px;color:#374151;">{c}</li>' for c in notes['changing'])
    timeline_html  = build_timeline_html(history or [])

    open_rate_cell = f"""
        <td style="width:12px;"></td>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{open_rate}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Open rate ({open_count} opens)</div>
        </td>""" if open_rate else ''

    sent_row = f"""
      <tr>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{sent}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Emails sent</div>
        </td>
        {open_rate_cell}
      </tr>
      <tr>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{not_now}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Follow up later</div>
        </td>
      </tr>""" if sent != '—' else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>ArgusReach - Monthly Report - {month}</title>
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
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{prospects}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Prospects reached</div>
        </td>
        <td style="width:12px;"></td>
        <td style="background:#f0fdf4;border-radius:6px;padding:16px 20px;border:1px solid #bbf7d0;">
          <div style="font-size:1.75rem;font-weight:800;color:#15803d;letter-spacing:-0.03em;">{meetings}</div>
          <div style="font-size:0.78rem;color:#166534;margin-top:2px;">Meetings booked</div>
        </td>
      </tr>
      <tr>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{interested}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Ready to connect</div>
        </td>
        <td style="width:12px;"></td>
        <td style="background:#f9fafb;border-radius:6px;padding:16px 20px;">
          <div style="font-size:1.75rem;font-weight:800;color:#111827;letter-spacing:-0.03em;">{not_now}</div>
          <div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">Follow up later</div>
        </td>
      </tr>
      {sent_row}
    </table>
  </div>

  {timeline_html}

  <!-- What worked -->
  <div style="padding:0 40px 24px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">What Worked</div>
    <ul style="margin:0;padding-left:20px;line-height:1.7;">{working_items}</ul>
  </div>

  <!-- What we're adjusting -->
  <div style="padding:0 40px 24px;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;">What We're Adjusting</div>
    <ul style="margin:0;padding-left:20px;line-height:1.7;">{changing_items}</ul>
  </div>

  <!-- Next month -->
  <div style="padding:20px 40px;background:#f9fafb;border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
    <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#9ca3af;margin-bottom:8px;">Next Month</div>
    <p style="margin:0;font-size:0.875rem;color:#374151;line-height:1.7;">{notes['next_month']}</p>
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
    msg['From']    = f"ArgusReach <{SENDER_EMAIL}>"
    msg['To']      = to_email
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(SENDER_EMAIL, SENDER_APP_PASS)
        smtp.sendmail(SENDER_EMAIL, to_email, msg.as_string())
    print(f"✅ Report sent to {to_email}")


# ── Narrative prompts ──────────────────────────────────────────────────────────

def prompt_narrative(client, stats):
    """Show auto-pulled stats, ask only for narrative inputs."""
    print("\n── Auto-Pulled Stats ──────────────────────────────────────────")
    print(f"  Prospects reached:  {stats['prospects']}")
    print(f"  Emails sent:        {stats.get('emails_sent', 'N/A (no campaign ID)')}")
    print(f"  Ready to connect:   {stats['reply_interested']}")
    print(f"  Follow up later:    {stats['reply_not_now']}")
    print(f"  Meetings booked:    {stats['meetings']}")
    print("──────────────────────────────────────────────────────────────")
    input("\nPress Enter to continue to narrative inputs...")

    print("\n── What Worked (one item per line, blank line to finish) ──────")
    working = []
    while True:
        line = input("> ").strip()
        if not line: break
        working.append(line)

    print("\n── What We're Adjusting Next Month ────────────────────────────")
    changing = []
    while True:
        line = input("> ").strip()
        if not line: break
        changing.append(line)

    print("\n── Next Month Focus (one paragraph) ───────────────────────────")
    next_month = input("> ").strip()

    return {
        'working':    working or ['Sequence delivered without issues.'],
        'changing':   changing or ['Monitoring performance for adjustments next cycle.'],
        'next_month': next_month or 'Continuing current campaign with any optimizations applied.',
    }


# ── Clients ────────────────────────────────────────────────────────────────────

def load_clients():
    with open(CLIENTS_FILE) as f:
        data = json.load(f)
    return data['clients'] if isinstance(data, dict) and 'clients' in data else data


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate ArgusReach monthly client report')
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

    print(f"\n📊 Building report for: {client['firm_name']} - {args.month}")
    print("  Pulling stats from DB...")

    # Pull all stats automatically
    stats = pull_db_stats(args.client, args.month)

    # Pull emails sent + open rate from Instantly
    campaign_id = client.get('instantly_campaign_id', '')
    if campaign_id:
        print("  Pulling analytics from Instantly...")
        inst = pull_instantly_analytics(campaign_id)
        stats['emails_sent'] = inst.get('sent')
        stats['open_count']  = inst.get('open_count')
        stats['open_rate']   = inst.get('open_rate')
        print(f"  ✅ Instantly: {stats['emails_sent']} sent, {stats['open_rate'] or '—'} open rate")
    else:
        stats['emails_sent'] = None
        stats['open_count']  = None
        stats['open_rate']   = None

    # Get narrative from Vito
    notes = prompt_narrative(client, stats)

    # Update history
    history  = load_history(args.client)
    is_launch = len(history) == 0
    existing = next((i for i, e in enumerate(history) if e['month'] == args.month), None)
    entry = {
        'month':            args.month,
        'launch':           is_launch,
        'prospects':        stats['prospects'],
        'reply_interested': stats['reply_interested'],
        'reply_not_now':    stats['reply_not_now'],
        'meetings':         stats['meetings'],
    }
    if existing is not None:
        history[existing] = entry
    else:
        history.append(entry)
    save_history(args.client, history)
    print(f"📁 History updated ({len(history)} month{'s' if len(history) != 1 else ''})")

    html = build_report_html(client, args.month, stats, notes, history=history)

    safe_month = args.month.replace(' ', '-')
    out_path   = REPORTS_DIR / f"{args.client}_{safe_month}.html"
    out_path.write_text(html)
    print(f"💾 Saved: {out_path}")

    if args.preview:
        print("👁  Preview mode - not sent.")
        return

    subject = f"ArgusReach - Monthly Report - {client['firm_name']} - {args.month}"
    send_report(client, to_email, subject, html)


if __name__ == '__main__':
    main()
