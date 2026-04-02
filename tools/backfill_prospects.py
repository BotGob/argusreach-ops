#!/usr/bin/env python3
"""
One-time backfill: load PT campaign prospects into the DB.
The PT campaign was launched before DB pre-load existed.
Run once: python3 tools/backfill_prospects.py
"""
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from db.database import upsert_prospect, init_db

PT_CAMPAIGN_ID  = "fb7ebd23-2dce-42c2-87ac-c492b036a48b"
RIA_CAMPAIGN_ID = "1328f93f-20cb-4836-a695-4e7099966e48"
CLIENT_ID       = "argusreach"

init_db()

# ── PT campaign prospects ─────────────────────────────────────────────────────
pt_csv = BASE_DIR / "campaigns" / "argusreach" / "prospects.csv"
pt_loaded = 0
if pt_csv.exists():
    with open(pt_csv) as f:
        for row in csv.DictReader(f):
            email = row.get("email","").strip().lower()
            if not email:
                continue
            upsert_prospect(
                client_id  = CLIENT_ID,
                campaign_id= PT_CAMPAIGN_ID,
                email      = email,
                first_name = row.get("first_name",""),
                last_name  = row.get("last_name",""),
                company    = row.get("company",""),
                stage      = "added",
            )
            pt_loaded += 1
    print(f"✅ PT campaign: {pt_loaded} prospects backfilled (campaign: {PT_CAMPAIGN_ID})")
else:
    print(f"❌ PT CSV not found: {pt_csv}")

# ── RIA campaign — check what's already there ─────────────────────────────────
from db.database import get_db
conn = get_db()
ria_count = conn.execute(
    "SELECT COUNT(*) FROM prospects WHERE client_id=? AND campaign_id=?",
    (CLIENT_ID, RIA_CAMPAIGN_ID)
).fetchone()[0]
total = conn.execute(
    "SELECT COUNT(*) FROM prospects WHERE client_id=?",
    (CLIENT_ID,)
).fetchone()[0]
conn.close()

print(f"📊 RIA campaign in DB: {ria_count} prospects")
print(f"📊 Total prospects in DB for argusreach: {total}")
print(f"\nExpected: 25 (PT) + 42 (RIA) = 67")
