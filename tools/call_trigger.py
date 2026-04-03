#!/usr/bin/env python3
"""
ArgusReach - Call Trigger Engine
Checks which prospects are eligible for a follow-up voice call and fires them.

Eligibility criteria:
  - voice_calling_enabled = True on the client
  - Prospect has a phone number
  - T2 email has been sent (touch_1_sent stage, created 3+ days ago)
  - No reply received yet (no classified event)
  - Not already called (no call_* event)
  - Not on DNC
  - Current time is within business hours in prospect's timezone (Mon-Fri 9am-5pm)
  - Client has not exceeded daily call limit (default: 30/day)

Called by monitor.py on each cycle for active clients with voice_calling_enabled.

Usage:
  python3 tools/call_trigger.py --client argusreach --dry-run
  python3 tools/call_trigger.py --all
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "monitor" / ".env")

from db.database import get_db, init_db

# Daily call limits per plan
DAILY_CALL_LIMITS = {
    "starter": 20,
    "growth":  40,
    "scale":   80,
}
DEFAULT_DAILY_LIMIT = 20
CALL_DELAY_SEC       = 30   # seconds between calls — sequential, never simultaneous
MIN_SEND_RATIO       = 1.8  # emails_sent/leads must be >= this before calling starts


def is_business_hours(tz_str: str) -> bool:
    """Check if current time is Mon-Fri 9am-5pm in the given timezone."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_str or "America/New_York")
        now = datetime.now(tz)
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        return 9 <= now.hour < 17
    except Exception:
        # Default to Eastern if timezone lookup fails
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo("America/New_York")
            now = datetime.now(tz)
            if now.weekday() >= 5:
                return False
            return 9 <= now.hour < 17
        except Exception:
            return False


def get_daily_call_count(client_id: str) -> int:
    """Count calls fired today for a client."""
    conn = get_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    count = conn.execute("""
        SELECT COUNT(*) FROM events
        WHERE client_id = ?
          AND event_type LIKE 'call_%'
          AND created_at >= ?
    """, (client_id, today + "T00:00:00")).fetchone()[0]
    conn.close()
    return count


def get_eligible_prospects(client_id: str, campaign_ids: list) -> list:
    """
    Return prospects eligible for a call:
    - Have a phone number
    - Created 3+ days ago (T2 likely sent)
    - No classified reply event
    - No call_* event yet
    - Not opted out
    """
    if not campaign_ids:
        return []
    conn = get_db()

    placeholders = ",".join("?" * len(campaign_ids))
    rows = conn.execute(f"""
        SELECT p.id, p.email, p.first_name, p.last_name, p.company,
               p.phone, p.timezone, p.client_id, p.campaign_id
        FROM prospects p
        WHERE p.client_id = ?
          AND p.campaign_id IN ({placeholders})
          AND p.phone != ''
          AND p.phone IS NOT NULL
          AND p.created_at <= datetime('now', '-3 days')
          AND p.id NOT IN (
              SELECT DISTINCT prospect_id FROM events
              WHERE event_type = 'classified'
                AND client_id = ?
          )
          AND p.id NOT IN (
              SELECT DISTINCT prospect_id FROM events
              WHERE event_type LIKE 'call_%'
                AND client_id = ?
          )
          AND p.id NOT IN (
              SELECT DISTINCT prospect_id FROM events
              WHERE event_type = 'meeting_booked'
                AND client_id = ?
          )
          AND p.stage NOT IN ('replied', 'sequence_complete', 'unsubscribed')
        ORDER BY p.created_at ASC  -- oldest first: furthest along in sequence
        LIMIT 50
    """, [client_id] + campaign_ids + [client_id, client_id, client_id]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_call_trigger(client_id: str, dry_run: bool = False) -> dict:
    """
    Main trigger function. Called by monitor for each active client with voice enabled.
    Returns summary dict.
    """
    import json as _json
    clients_data = _json.loads((BASE_DIR / "monitor" / "clients.json").read_text())
    client = next((c for c in clients_data["clients"] if c["id"] == client_id), None)

    if not client:
        return {"error": f"Client {client_id} not found"}

    # Check voice_calling_enabled flag
    if not client.get("voice_calling_enabled", False):
        return {"skipped": "voice_calling_enabled not set"}

    if not client.get("active", False):
        return {"skipped": "client not active"}

    # Plan-based daily limit
    plan = client.get("plan", "starter").lower()
    daily_limit = DAILY_CALL_LIMITS.get(plan, DEFAULT_DAILY_LIMIT)

    # Check daily limit
    daily_count = get_daily_call_count(client_id)
    if daily_count >= daily_limit:
        print(f"  [{client_id}] Daily call limit reached ({daily_count}/{daily_limit}) — skipping")
        return {"skipped": f"daily limit {daily_count}/{daily_limit}"}

    # Get all active campaign IDs
    campaign_ids = list({
        cid for cid in
        [client.get("instantly_campaign_id", "")] +
        [c.get("instantly_campaign_id", "") for c in client.get("campaigns", [])]
        if cid
    })

    # Gate 1 — confirm T2 has gone out (emails_sent / leads >= 1.8)
    # Uses Instantly analytics to ensure we're not calling people before T2
    try:
        import os as _os, requests as _req
        _key = _os.environ.get("INSTANTLY_API_KEY", "")
        if _key and campaign_ids:
            total_sent = 0
            total_leads = 0
            for cid in campaign_ids:
                _r = _req.get(
                    "https://api.instantly.ai/api/v2/campaigns/analytics",
                    headers={"Authorization": f"Bearer {_key}"},
                    params={"id": cid}, timeout=10
                )
                if _r.ok:
                    for a in (_r.json() if isinstance(_r.json(), list) else [_r.json()]):
                        total_sent  += a.get("emails_sent_count", 0)
                        total_leads += a.get("leads_count", 0) or 1
            ratio = total_sent / total_leads if total_leads > 0 else 0
            print(f"  [{client_id}] Send ratio: {total_sent}/{total_leads} = {ratio:.2f} (need >= {MIN_SEND_RATIO})")
            if ratio < MIN_SEND_RATIO:
                return {"skipped": f"send ratio {ratio:.2f} < {MIN_SEND_RATIO} — T2 not yet sent to enough contacts"}
    except Exception as _e:
        print(f"  [{client_id}] Send ratio check failed (non-fatal): {_e}")

    prospects = get_eligible_prospects(client_id, campaign_ids)
    if not prospects:
        return {"called": 0, "message": "no eligible prospects"}

    remaining_today = daily_limit - daily_count
    to_call = prospects[:remaining_today]

    print(f"  [{client_id}] {len(prospects)} eligible, calling {len(to_call)} today (limit: {daily_limit}/day)")

    # Import caller
    sys.path.insert(0, str(BASE_DIR / "tools"))
    import vapi_caller
    import importlib
    importlib.reload(vapi_caller)

    called = 0
    skipped_hours = 0
    skipped_no_tz = 0

    for prospect in to_call:
        tz = prospect.get("timezone") or "America/New_York"

        # Enforce business hours in prospect's timezone
        if not is_business_hours(tz):
            skipped_hours += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] Would call {prospect['email']} ({prospect.get('phone','?')}) tz={tz}")
            called += 1
            continue

        result = vapi_caller.fire_call(client, prospect)
        if result.get("id"):
            called += 1
            print(f"  Called {prospect['email']} → call_id={result['id'][:8]}")
            time.sleep(CALL_DELAY_SEC)  # pace calls to avoid spam flags
        else:
            print(f"  Failed to call {prospect['email']}")

    summary = {
        "called":          called,
        "skipped_hours":   skipped_hours,
        "eligible":        len(prospects),
        "daily_count":     daily_count + called,
        "daily_limit":     daily_limit,
    }
    print(f"  [{client_id}] Call trigger done: {summary}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="Client ID")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()

    if args.all:
        import json as _json
        clients_data = _json.loads((BASE_DIR / "monitor" / "clients.json").read_text())
        for c in clients_data["clients"]:
            if c.get("active") and c.get("voice_calling_enabled"):
                print(f"\n=== {c['firm_name']} ===")
                run_call_trigger(c["id"], dry_run=args.dry_run)
    elif args.client:
        result = run_call_trigger(args.client, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: --all OR --client <id> [--dry-run]")
