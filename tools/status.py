#!/usr/bin/env python3
"""
ArgusReach — Client Status Dashboard
Usage: python3 status.py [--client client_id]

Shows all active clients: months active, reply stats, last report, pending approvals.
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

BASE_DIR      = Path(__file__).parent.parent
CLIENTS_FILE  = BASE_DIR / 'monitor' / 'clients.json'
REPLY_LOG     = BASE_DIR / 'monitor' / 'logs' / 'replies.json'
PENDING_FILE  = BASE_DIR / 'monitor' / 'logs' / 'pending_approvals.json'
REPORTS_DIR   = BASE_DIR / 'reports'

RESET  = '\033[0m'
BOLD   = '\033[1m'
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
DIM    = '\033[2m'

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text()) if Path(path).exists() else default
    except Exception:
        return default

def load_clients():
    data = load_json(CLIENTS_FILE, {})
    return data.get('clients', data) if isinstance(data, dict) and 'clients' in data else data

def history_path(client_id):
    return REPORTS_DIR / f"{client_id}_history.json"

def load_history(client_id):
    return load_json(history_path(client_id), [])

def months_active(client):
    launch = client.get('launch_date')
    if not launch:
        h = load_history(client['id'])
        return len(h) if h else None
    try:
        ld = datetime.strptime(launch, '%Y-%m-%d').date()
        today = date.today()
        return (today.year - ld.year) * 12 + (today.month - ld.month) + 1
    except Exception:
        return None

def get_reply_stats(client_id, month_str=None):
    """Pull reply counts from replies.json for a client, optionally filtered to a month."""
    replies = load_json(REPLY_LOG, [])
    counts = {'positive': 0, 'not_now': 0, 'ooo': 0, 'negative': 0, 'escalated': 0, 'other': 0}
    for r in replies:
        if r.get('client') != client_id:
            continue
        if r.get('test_mode'):
            continue
        if month_str:
            try:
                ts = datetime.fromisoformat(r['ts'])
                if ts.strftime('%B %Y') != month_str:
                    continue
            except Exception:
                continue
        cls = r.get('classification', 'other')
        counts[cls] = counts.get(cls, 0) + 1
    return counts

def get_pending_count(client_id):
    pending = load_json(PENDING_FILE, [])
    return sum(1 for p in pending if p.get('client_id') == client_id)

def last_report_month(client_id):
    h = load_history(client_id)
    return h[-1]['month'] if h else None

def print_client_card(client, verbose=False):
    cid        = client['id']
    firm       = client['firm_name']
    active     = client.get('active', False)
    mode       = client.get('mode', '?')
    campaign   = client.get('campaign_name', '—')
    email      = client.get('client_email', '')
    launch     = client.get('launch_date', '')
    mo_active  = months_active(client)
    pending    = get_pending_count(cid)
    last_rpt   = last_report_month(cid)
    history    = load_history(cid)

    status_tag = f"{GREEN}● ACTIVE{RESET}" if active else f"{DIM}○ INACTIVE{RESET}"
    mo_str     = f"{mo_active} month{'s' if mo_active != 1 else ''} active" if mo_active else "not launched"
    pending_str = f"{RED}{pending} pending approval{'s' if pending != 1 else ''}{RESET}" if pending else f"{DIM}0 pending{RESET}"

    print(f"\n{BOLD}{firm}{RESET}  {status_tag}")
    print(f"  {DIM}id:{RESET} {cid}")
    print(f"  {DIM}campaign:{RESET} {campaign}")
    print(f"  {DIM}mode:{RESET} {mode}  |  {DIM}launch:{RESET} {launch or '—'}  |  {mo_str}")
    print(f"  {DIM}client email:{RESET} {email or f'{RED}NOT SET{RESET}'}")
    print(f"  {DIM}last report:{RESET} {last_rpt or f'{YELLOW}never{RESET}'}")
    print(f"  {pending_str}")

    if history:
        # Show last 3 months inline
        recent = history[-3:]
        print(f"\n  {CYAN}Campaign History:{RESET}")
        print(f"  {'Month':<18} {'Contacts':>9} {'Positive':>9} {'Meetings':>9}")
        print(f"  {'-'*48}")
        for entry in recent:
            tag = ' ← current' if entry == history[-1] else ''
            print(f"  {entry['month']:<18} {str(entry.get('contacts','—')):>9} {str(entry.get('positive','—')):>9} {str(entry.get('meetings','—')):>9}{tag}")
        if len(history) > 1:
            total_c = sum(e.get('contacts',0) for e in history if isinstance(e.get('contacts'),int))
            total_p = sum(e.get('positive',0) for e in history if isinstance(e.get('positive'),int))
            total_m = sum(e.get('meetings',0) for e in history if isinstance(e.get('meetings'),int))
            print(f"  {'TOTAL':<18} {str(total_c):>9} {str(total_p):>9} {str(total_m):>9}")

    # Reply stats from log (all time, non-test)
    stats = get_reply_stats(cid)
    total_replies = sum(stats.values())
    if total_replies > 0:
        print(f"\n  {CYAN}Reply Log (all time):{RESET}")
        for k, v in stats.items():
            if v > 0:
                print(f"    {k:<12} {v}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='ArgusReach client status dashboard')
    parser.add_argument('--client', default=None, help='Filter to specific client ID')
    parser.add_argument('--active-only', action='store_true', help='Show only active clients')
    args = parser.parse_args()

    clients = load_clients()
    if not clients:
        print("No clients found.")
        return

    if args.client:
        clients = [c for c in clients if c.get('id') == args.client]
        if not clients:
            print(f"Client '{args.client}' not found.")
            return

    if args.active_only:
        clients = [c for c in clients if c.get('active') and not c.get('_comment')]

    # Filter out pure example/template blocks (no outreach_email = not real)
    clients = [c for c in clients if c.get('id') and c.get('outreach_email') and not c.get('id','').startswith('_')]

    now = datetime.now().strftime('%Y-%m-%d %H:%M UTC')
    print(f"\n{BOLD}═══ ArgusReach — Client Status ═══{RESET}  {DIM}{now}{RESET}")
    print(f"{DIM}{'─'*50}{RESET}")

    active_clients   = [c for c in clients if c.get('active')]
    inactive_clients = [c for c in clients if not c.get('active')]

    if active_clients:
        print(f"\n{GREEN}{BOLD}ACTIVE CLIENTS ({len(active_clients)}){RESET}")
        for c in active_clients:
            print_client_card(c)

    if inactive_clients and not args.active_only:
        print(f"\n{DIM}{BOLD}INACTIVE / EXAMPLE ({len(inactive_clients)}){RESET}")
        for c in inactive_clients:
            print_client_card(c)

    # Summary
    total_pending = sum(get_pending_count(c['id']) for c in active_clients)
    print(f"\n{BOLD}{'─'*50}{RESET}")
    print(f"{BOLD}Active clients:{RESET} {len(active_clients)}")
    if total_pending:
        print(f"{RED}{BOLD}Total pending approvals:{RESET}{RED} {total_pending}{RESET}")
    print()

if __name__ == '__main__':
    main()
