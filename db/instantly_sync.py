#!/usr/bin/env python3
"""
ArgusReach — Instantly API Sync
Pulls campaign stats from Instantly and writes to local DB.
Run manually or via cron.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_db, init_db, sync_client_from_config

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")

INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"


def sync_campaign_stats(campaign_id: str, client_id: str, campaign_name: str = "", launch_date: str = ""):
    if not INSTANTLY_API_KEY:
        print("  ⚠️  No INSTANTLY_API_KEY — skipping")
        return None

    url = f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}"
    headers = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ Instantly API error for {campaign_id}: {e}")
        return None

    # Extract stats — Instantly v2 nests stats under campaign object
    stats = data.get("campaign_stats", data.get("stats", {}))
    leads_count  = data.get("leads_count") or stats.get("total_leads", 0)
    emails_sent  = stats.get("emails_sent", 0)
    opens        = stats.get("unique_opens", stats.get("opens", 0))
    clicks       = stats.get("unique_clicks", stats.get("clicks", 0))
    replies      = stats.get("total_replies", stats.get("replies", 0))
    status       = data.get("status_summary", "active")

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO campaigns
            (id, client_id, name, instantly_campaign_id, status, launch_date,
             leads_count, emails_sent, opens, clicks, replies, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            leads_count  = excluded.leads_count,
            emails_sent  = excluded.emails_sent,
            opens        = excluded.opens,
            clicks       = excluded.clicks,
            replies      = excluded.replies,
            status       = excluded.status,
            updated_at   = excluded.updated_at
    """, (
        campaign_id, client_id,
        campaign_name or data.get("name", ""),
        campaign_id, status, launch_date,
        leads_count, emails_sent, opens, clicks, replies,
        now, now
    ))
    conn.commit()
    conn.close()

    print(f"  ✅ {campaign_name or campaign_id}: {leads_count} leads, {emails_sent} sent, {replies} replies")
    return data


def sync_all_campaigns():
    init_db()

    with open(CLIENTS_FILE) as f:
        config = json.load(f)

    clients = [c for c in config.get("clients", []) if not c.get("id", "").startswith("_")]
    active  = [c for c in clients if c.get("active")]

    print(f"Syncing {len(active)} active client(s)...")

    for client in clients:
        sync_client_from_config(client)

    for client in active:
        campaign_id = client.get("instantly_campaign_id", "")
        if not campaign_id:
            print(f"  ⚠️  {client['id']} — no campaign ID, skipping")
            continue
        print(f"\n→ {client['id']} ({client.get('firm_name', '')})")
        sync_campaign_stats(
            campaign_id=campaign_id,
            client_id=client["id"],
            campaign_name=client.get("campaign_name", ""),
            launch_date=client.get("launch_date", "")
        )

    print("\n✅ Sync complete.")


if __name__ == "__main__":
    sync_all_campaigns()
