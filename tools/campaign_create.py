#!/usr/bin/env python3
"""
ArgusReach — Campaign Creation Script
======================================
Creates a new Instantly campaign for a client, loads prospects from CSV,
registers everything in the DB, and runs the validator.

Usage:
    python3 tools/campaign_create.py <client_id>

Example:
    python3 tools/campaign_create.py pt_tampa_bay

What it does (in order):
    1. Loads client config from clients.json
    2. Checks no active campaign already exists for this client (prevents duplicates)
    3. Creates campaign in Instantly with correct name, schedule, stop_on_reply
    4. Uploads sequence steps (from client's sequence file)
    5. Loads prospects from CSV into campaign
    6. Runs validate_campaign.py to confirm everything is correct
    7. Registers campaign in the DB (campaigns table)
    8. Prints the campaign ID and updates clients.json with it

IMPORTANT: Campaign is created in DRAFT (paused) state.
You must manually activate it in Instantly UI after review.
"""

import csv
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── Enrichment: imported from shared module ────────────────────────────────────
# Do NOT duplicate enrichment logic here — edit tools/enrichment.py instead.
from enrichment import enrich_contacts as enrich_prospects, _strip_html  # noqa: F401
BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")

INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
API_BASE = "https://api.instantly.ai/api/v2"

HEADERS = {
    "Authorization": f"Bearer {INSTANTLY_API_KEY}",
    "Content-Type": "application/json",
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def api(method, path, **kwargs):
    r = requests.request(method, f"{API_BASE}{path}", headers=HEADERS, timeout=20, **kwargs)
    if not r.ok:
        raise RuntimeError(f"Instantly API {method} {path} → {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def load_clients():
    with open(CLIENTS_FILE) as f:
        return json.load(f)


def save_clients(config):
    with open(CLIENTS_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_client(client_id):
    config = load_clients()
    for c in config.get("clients", []):
        if c.get("id") == client_id:
            return c, config
    return None, config


def campaign_name(client):
    """Enforced naming convention: ArgusReach — [Firm] — [Mon YYYY]"""
    month = datetime.now().strftime("%B %Y")
    return f"ArgusReach — {client['firm_name']} — {month}"


# ── GUARDS ────────────────────────────────────────────────────────────────────

def check_no_duplicate(client):
    """Prevent creating a second active campaign for the same client."""
    existing_id = client.get("instantly_campaign_id", "")
    if not existing_id:
        return
    try:
        data = api("GET", f"/campaigns/{existing_id}")
        name = data.get("name", "")
        status = data.get("status", -1)
        status_label = {0: "DRAFT", 1: "ACTIVE", 2: "COMPLETED"}.get(status, f"status={status}")
        print(f"\n⚠️  WARNING: Client already has a campaign registered:")
        print(f"   Name:   {name}")
        print(f"   ID:     {existing_id}")
        print(f"   Status: {status_label}")
        answer = input("\nContinue and create a NEW campaign anyway? (yes/no): ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            sys.exit(0)
    except Exception as e:
        print(f"   (Could not fetch existing campaign: {e} — proceeding)")


# ── SEQUENCE LOADER ───────────────────────────────────────────────────────────

def sequence_to_instantly_steps(steps_raw):
    """Convert clients.json sequence format to Instantly API steps format.
    IMPORTANT: Always plain text — no HTML wrapping. Instantly handles formatting.
    HTML in sequences breaks delivery and looks unprofessional."""
    steps = []
    for i, s in enumerate(steps_raw):
        body = s.get("body", "")
        # Strip any accidental HTML tags — sequences must be plain text
        if body and "<" in body:
            import re as _re
            body = _re.sub(r"<[^>]+>", "", body)
            body = body.replace("&nbsp;", " ").replace("&amp;", "&").strip()
        steps.append({
            "type": "email",
            "delay": s.get("delay_days", 0) if i > 0 else 0,
            "delay_unit": "days",
            "pre_delay_unit": "days",
            "variants": [{"subject": s.get("subject", ""), "body": body}]
        })
    return steps


def load_sequence(client):
    """
    Load sequence steps from client record (clients.json).
    Falls back to sequence.json in campaigns folder, then placeholder.
    Priority: clients.json sequence > sequence.json file > placeholder
    """
    # 1. Try clients.json sequence (set via portal editor)
    seq_raw = client.get("sequence", [])
    if seq_raw and any(s.get("subject") for s in seq_raw):
        steps = sequence_to_instantly_steps(seq_raw)
        print(f"   ✅ Loaded {len(steps)} sequence steps from client record (portal)")
        return steps

    # 2. Fall back to sequence.json file
    seq_file = BASE_DIR / "campaigns" / client["id"] / "sequence.json"
    if seq_file.exists():
        with open(seq_file) as f:
            steps_raw = json.load(f)
        steps = sequence_to_instantly_steps(steps_raw)
        print(f"   ✅ Loaded {len(steps)} sequence steps from sequence.json")
        return steps

    # 3. Placeholder
    print(f"   ⚠️  No sequence found in client record or sequence.json")
    print(f"   → Campaign will be created with a placeholder step.")
    return [{
        "type": "email",
        "delay": 0,
        "delay_unit": "days",
        "pre_delay_unit": "days",
        "variants": [{
            "subject": "PLACEHOLDER — update in Instantly",
            "body": "<p>PLACEHOLDER — write sequence in portal before launching.</p>"
        }]
    }]


# ── PROSPECT LOADER ───────────────────────────────────────────────────────────

def load_prospects(client):
    csv_path = BASE_DIR / client.get("prospects_csv", f"campaigns/{client['id']}/prospects.csv")
    if not csv_path.exists():
        print(f"   ✗ prospects.csv not found at {csv_path}")
        sys.exit(1)
    prospects = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            prospects.append(row)
    print(f"   ✅ Loaded {len(prospects)} prospects from {csv_path.name}")
    return prospects


def upload_prospects(campaign_id, prospects, client):
    """Upload prospects to Instantly campaign one at a time (batch endpoint is 404)."""
    uploaded = 0
    skipped  = 0
    failed   = 0
    for p in prospects:
        email = (p.get("email") or p.get("Email") or "").strip()
        if not email:
            skipped += 1
            continue
        payload = {
            "campaign": campaign_id,          # must be "campaign" not "campaign_id"
            "email": email,
            "first_name": p.get("first_name") or p.get("First Name") or p.get("firstName") or "",
            "last_name":  p.get("last_name")  or p.get("Last Name")  or p.get("lastName")  or "",
            "company_name": p.get("company") or p.get("Company") or p.get("company_name") or "",
            "skip_if_in_workspace": False,
            "custom_variables": {
                "title":        p.get("title", ""),
                "city":         p.get("city", ""),
                "state":        p.get("state", ""),
                "custom_intro": p.get("custom_intro", ""),
            },
        }
        try:
            api("POST", "/leads", json=payload)
            uploaded += 1
        except Exception as e:
            print(f"   ⚠️  Failed to upload {email}: {e}")
            failed += 1
        time.sleep(0.3)

    print(f"   ✅ Uploaded {uploaded} prospects ({skipped} skipped — no email, {failed} failed)")
    return uploaded


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/campaign_create.py <client_id>")
        sys.exit(1)

    client_id = sys.argv[1]
    client, config = get_client(client_id)
    if not client:
        print(f"✗ Client '{client_id}' not found in clients.json")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"ArgusReach — Campaign Creator")
    print(f"Client: {client['firm_name']} ({client_id})")
    print(f"{'='*60}")

    # 1. Guard against duplicates
    print("\n[ DUPLICATE CHECK ]")
    check_no_duplicate(client)

    # 2. Enforce campaign name convention
    name = campaign_name(client)
    print(f"\n[ CAMPAIGN ]")
    print(f"   Name: {name}")

    # 3. Load sequence
    print("\n[ SEQUENCE ]")
    sequence_steps = load_sequence(client)

    # 4. Load prospects
    print("\n[ PROSPECTS ]")
    prospects = load_prospects(client)

    # 5. Create campaign in Instantly (DRAFT state — status not set = draft)
    print("\n[ CREATING IN INSTANTLY ]")
    sending_accounts = client.get("outreach_email", "")
    payload = {
        "name": name,
        "campaign_schedule": {
            "schedules": [{
                "name": "Default",
                "timing": {"from": "08:00", "to": "17:00"},
                "days": {"1": True, "2": True, "3": True, "4": True, "5": True},
                "timezone": client.get("timezone", "America/Detroit")
            }]
        },
        "sequences": [{"steps": sequence_steps}],
        "stop_on_reply": True,
        "daily_limit": client.get("daily_limit", 30),
        "email_list": [sending_accounts] if isinstance(sending_accounts, str) else sending_accounts,
    }

    try:
        result = api("POST", "/campaigns", json=payload)
        campaign_id = result.get("id", "")
        print(f"   ✅ Campaign created: {campaign_id}")
        print(f"   ✅ Name: {name}")
        print(f"   ✅ Status: DRAFT (paused — activate in Instantly UI after review)")
    except RuntimeError as e:
        print(f"   ✗ Failed to create campaign: {e}")
        sys.exit(1)

    # 6. Enrich prospects with personalized intros, then upload
    print("\n[ ENRICHING PROSPECTS ]")
    prospects = enrich_prospects(prospects)

    print("\n[ UPLOADING PROSPECTS ]")
    time.sleep(2)  # let Instantly register the campaign
    upload_prospects(campaign_id, prospects, client)

    print("\n[ PRE-LOADING PROSPECTS INTO DB ]")
    try:
        sys.path.insert(0, str(BASE_DIR))
        from db.database import upsert_prospect, init_db
        init_db()
        loaded = 0
        skipped = 0
        for p in prospects:
            email = (p.get("email") or p.get("Email") or "").strip().lower()
            if not email:
                skipped += 1
                continue
            upsert_prospect(
                client_id   = client_id,
                campaign_id = campaign_id,
                email       = email,
                first_name  = p.get("first_name") or p.get("First Name") or p.get("firstName") or "",
                last_name   = p.get("last_name")  or p.get("Last Name")  or p.get("lastName")  or "",
                company     = p.get("company")    or p.get("Company")    or p.get("company_name") or "",
                stage       = "added",
            )
            loaded += 1
        print(f"   ✅ {loaded} prospects pre-loaded into DB ({skipped} skipped — no email)")
        print(f"   → Visible in portal, cross-campaign DNC active from day 1")
    except Exception as e:
        print(f"   ⚠️  DB pre-load failed: {e} (not critical — contacts still in Instantly)")

    # 7. Update clients.json with new campaign_id and campaign_name
    print("\n[ UPDATING CLIENTS.JSON ]")
    launch_date_str = datetime.now().strftime("%Y-%m-%d")
    for c in config.get("clients", []):
        if c.get("id") == client_id:
            c["instantly_campaign_id"] = campaign_id
            c["campaign_name"] = name
            c["launch_date"] = launch_date_str
            # Add to campaigns array (multi-campaign support)
            new_campaign_entry = {
                "instantly_campaign_id": campaign_id,
                "campaign_name":         name,
                "icp_label":             c.get("vertical", ""),
                "prospects_csv":         c.get("prospects_csv", f"campaigns/{client_id}/prospects.csv"),
                "launch_date":           launch_date_str,
                "active":                True,
            }
            if "campaigns" not in c:
                c["campaigns"] = []
            # Avoid duplicates
            existing_ids = {x.get("instantly_campaign_id") for x in c["campaigns"]}
            if campaign_id not in existing_ids:
                c["campaigns"].append(new_campaign_entry)
            break
    save_clients(config)
    print(f"   ✅ clients.json updated with campaign_id: {campaign_id} (campaigns array updated)")

    # 8. Register in DB
    print("\n[ REGISTERING IN DATABASE ]")
    try:
        sys.path.insert(0, str(BASE_DIR))
        from db.database import get_db, init_db
        init_db()
        now = datetime.utcnow().isoformat()
        conn = get_db()
        conn.execute("""
            INSERT INTO campaigns
                (id, client_id, name, instantly_campaign_id, status, launch_date,
                 leads_count, emails_sent, opens, clicks, replies, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'draft', ?, ?, 0, 0, 0, 0, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, status='draft', updated_at=excluded.updated_at
        """, (campaign_id, client_id, name, campaign_id,
              datetime.now().strftime("%Y-%m-%d"), len(prospects), now, now))
        conn.commit()
        conn.close()
        print(f"   ✅ Campaign registered in ArgusReach DB")
    except Exception as e:
        print(f"   ⚠️  DB registration failed: {e} (not critical — sync will catch it)")

    # 9. Run validator
    print("\n[ RUNNING VALIDATOR ]")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "tools" / "validate_campaign.py"), campaign_id],
        cwd=str(BASE_DIR),
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("⚠️  Validator found issues — fix before activating.")
    else:
        print("✅ Validator passed.")

    # 10. Summary
    print(f"\n{'='*60}")
    print(f"✅ Campaign created successfully")
    print(f"   Client:      {client['firm_name']}")
    print(f"   Campaign ID: {campaign_id}")
    print(f"   Name:        {name}")
    print(f"   Prospects:   {len(prospects)}")
    print(f"\n⚡ NEXT STEPS:")
    print(f"   1. Review sequence copy in Instantly UI")
    print(f"   2. Verify sending account: {client.get('outreach_email')}")
    print(f"   3. Activate campaign in Instantly when ready")
    print(f"   4. Update clients.json: set active=true")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
