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

    # Correct endpoint: /api/v2/campaigns/analytics returns array of all campaigns
    url = "https://api.instantly.ai/api/v2/campaigns/analytics"
    headers = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        all_stats = r.json()
    except Exception as e:
        print(f"  ❌ Instantly API error: {e}")
        return None

    # Find this campaign in the array
    data = next((c for c in all_stats if c.get("campaign_id") == campaign_id), None)
    if not data:
        print(f"  ⚠️  Campaign {campaign_id} not found in analytics response")
        return None

    leads_count  = data.get("leads_count", 0)
    emails_sent  = data.get("emails_sent_count", 0)
    opens        = data.get("open_count_unique", 0)
    clicks       = data.get("link_click_count_unique", 0)
    replies      = data.get("reply_count_unique", 0)
    status       = "active" if data.get("campaign_status") == 1 else "paused"

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
        # Support both multi-campaign array and legacy single-campaign fields
        campaigns = client.get("campaigns") or [{
            "instantly_campaign_id": client.get("instantly_campaign_id", ""),
            "campaign_name":         client.get("campaign_name", ""),
            "launch_date":           client.get("launch_date", ""),
            "active":                True,
        }]
        active_campaigns = [c for c in campaigns if c.get("active", True) and c.get("instantly_campaign_id")]
        if not active_campaigns:
            print(f"  ⚠️  {client['id']} — no active campaign IDs, skipping")
            continue
        print(f"\n→ {client['id']} ({client.get('firm_name', '')}) — {len(active_campaigns)} campaign(s)")
        for campaign in active_campaigns:
            sync_campaign_stats(
                campaign_id=campaign["instantly_campaign_id"],
                client_id=client["id"],
                campaign_name=campaign.get("campaign_name", ""),
                launch_date=campaign.get("launch_date", "")
            )

    print("\n✅ Sync complete.")


if __name__ == "__main__":
    sync_all_campaigns()
