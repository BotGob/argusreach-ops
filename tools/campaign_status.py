#!/usr/bin/env python3
"""
ArgusReach — Campaign Status Overview
=======================================
Shows ALL campaigns across all clients — active, draft, paused, completed.
Use this as the authoritative reference before touching anything in Instantly.

Usage:
    python3 tools/campaign_status.py
    python3 tools/campaign_status.py --all    # include inactive clients too
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")

INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
HEADERS = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}

STATUS_MAP = {0: "DRAFT", 1: "ACTIVE", 2: "COMPLETED", 3: "PAUSED"}
STATUS_COLOR = {
    "ACTIVE":    "🟢",
    "DRAFT":     "⚪",
    "PAUSED":    "🟡",
    "COMPLETED": "✅",
    "UNKNOWN":   "❓",
}


def fetch_all_instantly_campaigns():
    """Fetch analytics for all campaigns from Instantly."""
    try:
        r = requests.get("https://api.instantly.ai/api/v2/campaigns/analytics",
                         headers=HEADERS, timeout=15)
        r.raise_for_status()
        return {c["campaign_id"]: c for c in r.json()}
    except Exception as e:
        print(f"⚠️  Could not fetch Instantly analytics: {e}")
        return {}


def fetch_all_instantly_campaign_list():
    """Fetch list of all campaigns (for status info)."""
    try:
        # Get all campaigns with pagination
        all_campaigns = {}
        params = {"limit": 100, "skip": 0}
        while True:
            r = requests.get("https://api.instantly.ai/api/v2/campaigns",
                             headers=HEADERS, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else data.get("items", [])
            for c in items:
                all_campaigns[c["id"]] = c
            if len(items) < 100:
                break
            params["skip"] += 100
        return all_campaigns
    except Exception as e:
        print(f"⚠️  Could not fetch campaign list: {e}")
        return {}


def main():
    show_all = "--all" in sys.argv

    with open(CLIENTS_FILE) as f:
        config = json.load(f)

    clients = [c for c in config.get("clients", [])
               if not c.get("id", "").startswith("_") and "example" not in c.get("id", "")]

    if not show_all:
        clients = [c for c in clients if c.get("active") or c.get("instantly_campaign_id")]

    print(f"\n{'='*70}")
    print(f"  ArgusReach — Campaign Status  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"{'='*70}")

    analytics = fetch_all_instantly_campaigns()
    campaign_list = fetch_all_instantly_campaign_list()

    registered_ids = set()

    for client in clients:
        cid = client.get("id", "")
        campaign_id = client.get("instantly_campaign_id", "")
        firm = client.get("firm_name", cid)
        active = client.get("active", False)
        client_status = "ACTIVE" if active else "PAUSED"

        print(f"\n  {'▶' if active else '⏸'}  {firm}  ({cid})")
        print(f"      Client status: {client_status}")

        if not campaign_id:
            print(f"      Campaign:      ⚠️  No campaign_id registered")
            print(f"      → Run: python3 tools/campaign_create.py {cid}")
            continue

        registered_ids.add(campaign_id)
        print(f"      Campaign ID:   {campaign_id}")
        print(f"      Campaign name: {client.get('campaign_name', '—')}")
        print(f"      Launch date:   {client.get('launch_date', '—')}")
        print(f"      Outreach:      {client.get('outreach_email', '—')}")

        # Instantly status
        camp_data = campaign_list.get(campaign_id, {})
        instantly_status_code = camp_data.get("status", -1)
        instantly_status = STATUS_MAP.get(instantly_status_code, f"UNKNOWN ({instantly_status_code})")
        icon = STATUS_COLOR.get(instantly_status, "❓")
        print(f"      Instantly:     {icon} {instantly_status}")

        # Stats
        stats = analytics.get(campaign_id, {})
        if stats:
            leads     = stats.get("leads_count", 0)
            sent      = stats.get("emails_sent_count", 0)
            replies   = stats.get("reply_count_unique", 0)
            completed = stats.get("completed_count", 0)
            rr = f"{replies/sent*100:.1f}%" if sent else "—"
            print(f"      Stats:         {leads} leads · {sent} sent · {replies} replies ({rr} reply rate) · {completed} completed")
        else:
            print(f"      Stats:         No data in Instantly yet")

        # Mismatch warning
        if active and instantly_status != "ACTIVE":
            print(f"      ⚠️  MISMATCH: clients.json says active=true but Instantly says {instantly_status}")
        if not active and instantly_status == "ACTIVE":
            print(f"      ⚠️  MISMATCH: clients.json says active=false but Instantly says ACTIVE")

    # Show any Instantly campaigns NOT registered in our system
    unregistered = [c for cid, c in campaign_list.items() if cid not in registered_ids]
    if unregistered:
        print(f"\n{'─'*70}")
        print(f"  ⚠️  UNREGISTERED CAMPAIGNS (in Instantly but not in clients.json)")
        print(f"  These should be deleted if they are old tests or failed launches.")
        for c in unregistered:
            status = STATUS_MAP.get(c.get("status", -1), "UNKNOWN")
            icon = STATUS_COLOR.get(status, "❓")
            print(f"\n    {icon} {c.get('name', 'Unnamed')}")
            print(f"       ID: {c.get('id', '—')}")
            print(f"       Status: {status}")
            print(f"       Created: {c.get('timestamp_created', '—')[:10]}")
            print(f"       → DELETE in Instantly UI if this is a test/failed campaign")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
