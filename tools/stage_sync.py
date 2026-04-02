#!/usr/bin/env python3
"""
ArgusReach - Prospect Stage Sync
Pulls lead stage data from Instantly API and updates the prospects DB.
Builds a funnel: Touch 1 sent -> Touch 2 -> Touch 3 -> Replied -> Complete

Usage:
  python3 tools/stage_sync.py --client argusreach --campaign fb7ebd23-...
  python3 tools/stage_sync.py --all   # sync all active clients/campaigns
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "monitor" / ".env")

import requests
from db.database import get_db, init_db

INSTANTLY_KEY = os.environ.get("INSTANTLY_API_KEY", "")


def sync_lead_stages(client_id: str, campaign_id: str) -> dict:
    """
    Fetch all leads for a campaign from Instantly and update prospect stages in DB.
    Returns funnel summary dict.
    """
    if not INSTANTLY_KEY:
        print("No INSTANTLY_API_KEY — skipping stage sync")
        return {}

    headers = {"Authorization": f"Bearer {INSTANTLY_KEY}", "Content-Type": "application/json"}
    leads = []
    starting_after = None
    limit = 100

    print(f"  Syncing stages for campaign {campaign_id[:8]}...")
    while True:
        try:
            payload = {"campaign_id": campaign_id, "limit": limit}
            if starting_after:
                payload["starting_after"] = starting_after
            r = requests.post(
                "https://api.instantly.ai/api/v2/leads/list",
                headers=headers,
                json=payload,
                timeout=15
            )
            if not r.ok:
                print(f"  Instantly API error {r.status_code}: {r.text[:100]}")
                break
            data = r.json()
            page = data.get("items", [])
            if not page:
                break
            leads.extend(page)
            # Cursor-based pagination
            starting_after = data.get("next_starting_after")
            if not starting_after or len(page) < limit:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"  Stage sync error: {e}")
            break

    print(f"  Fetched {len(leads)} leads from Instantly")

    conn = get_db()

    # Ensure subsequence_count and replied_at columns exist
    for stmt in [
        "ALTER TABLE prospects ADD COLUMN subsequence_count INTEGER DEFAULT 0",
        "ALTER TABLE prospects ADD COLUMN replied_at TEXT",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # Already exists

    updated = 0
    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if not email:
            continue

        # Instantly fields available:
        # - timestamp_last_touch: set when at least 1 email was sent
        # - email_reply_count: > 0 means they replied
        # - timestamp_last_reply: when they last replied
        # - lt_interest_status: -1 = unsubscribed/negative, 1 = interested
        # - status: 1 = active, 2 = paused, -1 = unsubscribed
        reply_count   = int(lead.get("email_reply_count") or 0)
        reply_time    = lead.get("timestamp_last_reply") or ""
        last_touch    = lead.get("timestamp_last_touch") or ""
        interest      = int(lead.get("lt_interest_status") or 0)
        status        = int(lead.get("status") or 1)
        # Use reply_count as proxy for subsequence depth (not perfect but best available)
        # email_reply_count tracks emails RECEIVED, not sent — use last_touch as sent proxy
        subseq = reply_count  # fallback — will refine when Instantly exposes sent count per lead

        # Determine stage from available signals
        if status == -1:
            stage = "sequence_complete"  # unsubscribed = finished sequence interaction
        elif reply_time:
            stage = "replied"
        elif last_touch:
            # At least 1 email sent - we can't distinguish T1/T2/T3 without sequence step data
            # Use a placeholder that we'll upgrade when Instantly exposes step count
            stage = "touch_1_sent"
        else:
            stage = "added"

        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE prospects
            SET stage = ?,
                subsequence_count = ?,
                replied_at = CASE WHEN ? != '' THEN ? ELSE replied_at END,
                updated_at = ?
            WHERE client_id = ? AND campaign_id = ? AND email = ?
        """, (stage, subseq, reply_time, reply_time, now, client_id, campaign_id, email))
        updated += 1

    conn.commit()

    # Update last_synced_at on campaign
    try:
        conn.execute(
            "UPDATE campaigns SET updated_at = ? WHERE id = ? AND client_id = ?",
            (datetime.utcnow().isoformat(), campaign_id, client_id)
        )
        conn.commit()
    except Exception:
        pass

    conn.close()
    print(f"  Updated {updated} prospects")

    return get_campaign_funnel(client_id, campaign_id)


def get_campaign_funnel(client_id: str, campaign_id: str) -> dict:
    """
    Return funnel breakdown for a campaign from DB.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT stage, COUNT(*) as cnt
        FROM prospects
        WHERE client_id = ? AND campaign_id = ?
        GROUP BY stage
    """, (client_id, campaign_id)).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM prospects WHERE client_id = ? AND campaign_id = ?",
        (client_id, campaign_id)
    ).fetchone()[0]

    # Also count replied via events (covers cases where monitor classified before stage sync)
    replied_events = conn.execute("""
        SELECT COUNT(DISTINCT p.id) FROM prospects p
        JOIN events e ON e.prospect_id = p.id
        WHERE p.client_id = ? AND p.campaign_id = ?
          AND e.event_type = 'classified'
    """, (client_id, campaign_id)).fetchone()[0]

    conn.close()

    stage_counts = {r[0]: r[1] for r in rows if r[0]}

    # T1/T2/T3 = cumulative (T2 means they got T1 too)
    t1 = (stage_counts.get("touch_1_sent", 0) +
          stage_counts.get("touch_2_sent", 0) +
          stage_counts.get("touch_3_sent", 0) +
          stage_counts.get("replied", 0) +
          stage_counts.get("sequence_complete", 0))
    t2 = (stage_counts.get("touch_2_sent", 0) +
          stage_counts.get("touch_3_sent", 0) +
          stage_counts.get("replied", 0) +
          stage_counts.get("sequence_complete", 0))
    t3 = (stage_counts.get("touch_3_sent", 0) +
          stage_counts.get("replied", 0) +
          stage_counts.get("sequence_complete", 0))
    replied = max(stage_counts.get("replied", 0) + stage_counts.get("sequence_complete", 0), replied_events)
    complete = stage_counts.get("sequence_complete", 0)

    return {
        "total":             total,
        "touch_1_sent":      t1,
        "touch_2_sent":      t2,
        "touch_3_sent":      t3,
        "replied":           replied,
        "sequence_complete": complete,
    }


def get_client_funnel(client_id: str, campaign_ids: list) -> dict:
    """Sum funnel across all campaigns for a client."""
    totals = {"total": 0, "touch_1_sent": 0, "touch_2_sent": 0,
              "touch_3_sent": 0, "replied": 0, "sequence_complete": 0}
    for cid in campaign_ids:
        f = get_campaign_funnel(client_id, cid)
        for k in totals:
            totals[k] += f.get(k, 0)
    return totals


def sync_all_active():
    """Sync stages for all active clients and campaigns."""
    clients_file = BASE_DIR / "monitor" / "clients.json"
    data = json.loads(clients_file.read_text())
    for client in data.get("clients", []):
        if not client.get("active"):
            continue
        cid = client["id"]
        campaigns = client.get("campaigns", [])
        if not campaigns and client.get("instantly_campaign_id"):
            campaigns = [{"instantly_campaign_id": client["instantly_campaign_id"]}]
        for camp in campaigns:
            camp_id = camp.get("instantly_campaign_id", "")
            if not camp_id:
                continue
            result = sync_lead_stages(cid, camp_id)
            print(f"  {client['firm_name']} / {camp_id[:8]}: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", help="Client ID")
    parser.add_argument("--campaign", help="Campaign ID")
    parser.add_argument("--all", action="store_true", help="Sync all active clients")
    args = parser.parse_args()

    init_db()

    if args.all:
        sync_all_active()
    elif args.client and args.campaign:
        result = sync_lead_stages(args.client, args.campaign)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: --all OR --client <id> --campaign <id>")
