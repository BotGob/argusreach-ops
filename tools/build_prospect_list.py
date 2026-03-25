#!/usr/bin/env python3
"""
ArgusReach — Prospect List Builder
===================================
Full automated pipeline:
  1. Search Apollo for contacts matching client ICP
  2. Verify emails via NeverBounce
  3. Add bad emails to client + global DNC
  4. Write clean list to campaigns/<client_id>/prospects.csv
  5. Load clean list into Instantly campaign

Usage:
  python3 tools/build_prospect_list.py --client <client_id> [--limit 200] [--dry-run]

Requirements (env vars in monitor/.env):
  APOLLO_API_KEY       - Apollo.io API key (paid plan for email reveals)
  NEVERBOUNCE_API_KEY  - NeverBounce API key
  INSTANTLY_API_KEY    - Already set
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent
CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
CAMPAIGNS_DIR = BASE_DIR / "campaigns"
DNC_GLOBAL   = BASE_DIR / "monitor" / "dnc" / "global.txt"
ENV_FILE     = BASE_DIR / "monitor" / ".env"

# ── Load env ───────────────────────────────────────────────────────────────────
def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    os.environ.update(env)

load_env()

APOLLO_API_KEY      = os.environ.get("APOLLO_API_KEY", "")
NEVERBOUNCE_API_KEY = os.environ.get("NEVERBOUNCE_API_KEY", "")
INSTANTLY_API_KEY   = os.environ.get("INSTANTLY_API_KEY", "")

# ── Helpers ────────────────────────────────────────────────────────────────────
def load_clients():
    return json.loads(CLIENTS_FILE.read_text())

def get_client(client_id):
    config = load_clients()
    for c in config.get("clients", []):
        if c["id"] == client_id:
            return c
    return None

def load_dnc(client_id):
    """Return set of all DNC emails (global + client-specific)."""
    dnc = set()
    for f in [DNC_GLOBAL, BASE_DIR / "monitor" / "dnc" / f"{client_id}.txt"]:
        if f.exists():
            for line in f.read_text().splitlines():
                e = line.strip().lower()
                if e:
                    dnc.add(e)
    return dnc

def add_to_dnc(emails, client_id=None):
    """Add emails to global DNC and optionally client DNC."""
    for target in [DNC_GLOBAL]:
        if client_id:
            targets = [DNC_GLOBAL, BASE_DIR / "monitor" / "dnc" / f"{client_id}.txt"]
        else:
            targets = [DNC_GLOBAL]
        for f in targets:
            f.parent.mkdir(parents=True, exist_ok=True)
            existing = set(f.read_text().splitlines()) if f.exists() else set()
            new_entries = set(e.lower() for e in emails) - existing
            if new_entries:
                with open(f, "a") as fh:
                    for e in sorted(new_entries):
                        fh.write(e + "\n")
        break

# ── Step 1: Apollo Search ──────────────────────────────────────────────────────
def search_apollo(client, limit=200):
    """Search Apollo for contacts matching client ICP."""
    if not APOLLO_API_KEY:
        print("⚠️  No APOLLO_API_KEY set. Cannot search Apollo.")
        return []

    # ── Parse ICP fields from client config ────────────────────────────────────
    # Locations: textarea, one per line
    locations_raw = client.get("_target_locations", "") or client.get("_target_geography", "")
    locations = [l.strip() for l in locations_raw.splitlines() if l.strip()]

    # Titles: comma-separated — filter to actual job titles only (not company descriptors)
    titles_raw = [t.strip() for t in client.get("_target_titles", "").split(",") if t.strip()]
    titles = [t for t in titles_raw if len(t.split()) <= 4 and not any(
        w in t.lower() for w in ["clinic", "firm", "office", "company", "practice", "services"]
    )]

    # Industry: comma-separated values from intake checkboxes
    industries = [i.strip() for i in client.get("_target_industry", "").split(",") if i.strip()]
    INDUSTRY_MAP = {
        "healthcare":         ["hospital & health care", "medical practice", "health, wellness and fitness"],
        "physical_therapy":   ["health, wellness and fitness", "medical practice"],
        "accounting":         ["accounting"],
        "legal":              ["law practice", "legal services"],
        "real_estate":        ["real estate"],
        "financial_services": ["financial services", "investment management", "wealth management"],
        "insurance":          ["insurance"],
        "banking":            ["banking", "financial services"],
        "construction":       ["construction", "building materials"],
        "technology":         ["computer software", "information technology and services"],
        "marketing":          ["marketing and advertising", "public relations and communications"],
        "retail":             ["retail"],
        "hospitality":        ["hospitality", "restaurants", "food & beverages"],
        "nonprofit":          ["nonprofit organization management"],
    }
    industry_tags = []
    for ind in industries:
        industry_tags.extend(INDUSTRY_MAP.get(ind, []))
    industry_tags = list(dict.fromkeys(industry_tags))  # dedupe, preserve order

    # Seniority: comma-separated values from intake checkboxes
    seniorities = [s.strip() for s in client.get("_target_seniority", "").split(",") if s.strip()]
    SENIORITY_MAP = {
        "owner_founder": ["owner", "founder", "partner", "c_suite"],
        "c_suite":       ["c_suite", "founder"],
        "vp_director":   ["vp", "director"],
        "manager":       ["manager", "senior"],
        "any":           [],
    }
    seniority_levels = []
    if "any" not in seniorities:
        for s in seniorities:
            seniority_levels.extend(SENIORITY_MAP.get(s, []))
        seniority_levels = list(dict.fromkeys(seniority_levels))

    # Company size: comma-separated values from intake checkboxes
    sizes = [s.strip() for s in client.get("_target_company_size", "").split(",") if s.strip()]
    SIZE_MAP = {
        "1-10":    "1,10",
        "11-50":   "11,50",
        "51-200":  "51,200",
        "201-500": "201,500",
    }
    employee_ranges = [SIZE_MAP[s] for s in sizes if s in SIZE_MAP]

    print(f"🔍 Searching Apollo:")
    print(f"   Titles:     {titles or '(none — broad search)'}")
    print(f"   Locations:  {locations}")
    print(f"   Industries: {industries}")
    print(f"   Seniority:  {seniorities or 'any'}")
    print(f"   Sizes:      {sizes or 'any'}")
    print(f"   Limit:      {limit}")

    contacts = []
    seen_emails = set()
    page = 1
    max_pages = 20

    while len(contacts) < limit and page <= max_pages:
        payload = {
            "per_page": 100,
            "page": page,
            "contact_email_status": ["verified", "likely to engage"],
        }
        if titles:
            payload["person_titles"] = titles
        if locations:
            payload["person_locations"] = locations
        if employee_ranges:
            payload["organization_num_employees_ranges"] = employee_ranges
        if industry_tags:
            payload["q_organization_industry_tag_name"] = ", ".join(industry_tags)
        if seniority_levels:
            payload["person_seniorities"] = seniority_levels

        try:
            resp = requests.post(
                "https://api.apollo.io/v1/mixed_people/search",
                headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_API_KEY},
                json=payload,
                timeout=30
            )
            if resp.status_code == 429:
                print("⏳ Apollo rate limit — waiting 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            people = resp.json().get("people", [])
            if not people:
                print(f"   Apollo returned no more results at page {page}")
                break
            for p in people:
                email = p.get("email", "").lower().strip()
                if not email or email == "email_not_unlocked@domain.com":
                    continue
                if email in seen_emails:
                    continue
                seen_emails.add(email)
                contacts.append({
                    "first_name":                p.get("first_name", ""),
                    "last_name":                 p.get("last_name", ""),
                    "email":                     email,
                    "company":                   (p.get("organization") or {}).get("name", ""),
                    "title":                     p.get("title", ""),
                    "city":                      p.get("city", ""),
                    "state":                     p.get("state", ""),
                    "linkedin_url":              p.get("linkedin_url", ""),
                    "organization_website_url":  (p.get("organization") or {}).get("website_url", ""),
                })
                if len(contacts) >= limit:
                    break
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"❌ Apollo error: {e}")
            break

    print(f"✅ Apollo returned {len(contacts)} contacts with emails")
    return contacts


# ── Step 2: NeverBounce Verification ──────────────────────────────────────────
def verify_emails(contacts):
    """Verify emails via NeverBounce. Returns (clean, bad) lists."""
    if not NEVERBOUNCE_API_KEY:
        print("⚠️  No NEVERBOUNCE_API_KEY set — skipping verification (not safe for production).")
        return contacts, []

    emails = [c["email"] for c in contacts]
    print(f"📧 Verifying {len(emails)} emails via NeverBounce...")

    # Use bulk verification job
    try:
        # Create job
        resp = requests.post(
            "https://api.neverbounce.com/v4/jobs/create",
            json={
                "key": NEVERBOUNCE_API_KEY,
                "input_location": "supplied",
                "input": [{"id": i, "email": e} for i, e in enumerate(emails)],
                "filename": f"argusreach_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
                "auto_start": True,
                "auto_parse": True,
            },
            timeout=30
        )
        resp.raise_for_status()
        job_id = resp.json().get("job_id")
        print(f"   Job created: {job_id} — polling for completion...")

        # Poll until done
        for _ in range(60):
            time.sleep(10)
            status_resp = requests.get(
                "https://api.neverbounce.com/v4/jobs/status",
                params={"key": NEVERBOUNCE_API_KEY, "job_id": job_id},
                timeout=15
            )
            job_status = status_resp.json().get("job_status", "")
            if job_status == "complete":
                break
            print(f"   Status: {job_status}...")
        else:
            print("⚠️  NeverBounce job timed out — returning unverified")
            return contacts, []

        # Download results
        results_resp = requests.get(
            "https://api.neverbounce.com/v4/jobs/download",
            params={"key": NEVERBOUNCE_API_KEY, "job_id": job_id},
            timeout=30
        )
        lines = results_resp.text.strip().splitlines()
        reader = csv.DictReader(lines)

        # VALID statuses we keep; everything else is bad
        GOOD_STATUSES = {"valid", "catchall"}
        BAD_STATUSES  = {"invalid", "disposable", "unknown"}

        good_emails = set()
        bad_emails  = set()
        for row in reader:
            email  = row.get("email", "").lower().strip()
            status = row.get("result", "").lower()
            if status in GOOD_STATUSES:
                good_emails.add(email)
            elif status in BAD_STATUSES:
                bad_emails.add(email)
            # catchall/unknown — keep but could filter later

        clean   = [c for c in contacts if c["email"] in good_emails]
        removed = [c for c in contacts if c["email"] in bad_emails]

        print(f"✅ NeverBounce: {len(clean)} valid, {len(removed)} removed")
        return clean, removed

    except Exception as e:
        print(f"❌ NeverBounce error: {e}")
        return contacts, []


# ── Step 3: DNC Filter ─────────────────────────────────────────────────────────
def filter_dnc(contacts, client_id):
    """Remove any contacts matching DNC lists."""
    dnc = load_dnc(client_id)
    clean   = [c for c in contacts if c["email"].lower() not in dnc]
    removed = [c for c in contacts if c["email"].lower() in dnc]
    if removed:
        print(f"🚫 DNC filter removed {len(removed)} contacts")
    return clean


# ── Step 4: Write prospects.csv ────────────────────────────────────────────────
def write_prospects_csv(contacts, client_id):
    """Write clean contact list to campaigns/<client_id>/prospects.csv"""
    out_path = CAMPAIGNS_DIR / client_id / "prospects.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["first_name", "last_name", "email", "company", "title", "city", "state", "linkedin_url", "organization_website_url"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in contacts:
            writer.writerow({k: c.get(k, "") for k in fieldnames})
    print(f"💾 Wrote {len(contacts)} contacts to {out_path}")
    return out_path


# ── Step 5: Load into Instantly ───────────────────────────────────────────────
def load_to_instantly(contacts, campaign_id, dry_run=False):
    """Bulk-add clean contacts to Instantly campaign."""
    if not INSTANTLY_API_KEY:
        print("❌ No INSTANTLY_API_KEY")
        return
    if not campaign_id:
        print("⚠️  No campaign_id set on client — skipping Instantly load.")
        print("   Create the campaign in Instantly first, then update clients.json.")
        return

    print(f"🚀 Loading {len(contacts)} contacts to Instantly campaign {campaign_id}...")

    if dry_run:
        print("   [DRY RUN] — skipping actual API call")
        return

    # Batch in groups of 50
    BATCH = 50
    loaded = 0
    for i in range(0, len(contacts), BATCH):
        batch = contacts[i:i+BATCH]
        leads = [
            {
                "campaign": campaign_id,
                "email": c["email"],
                "first_name": c.get("first_name", ""),
                "last_name": c.get("last_name", ""),
                "company_name": c.get("company", ""),
                "skip_if_in_workspace": True,
            }
            for c in batch
        ]
        try:
            resp = requests.post(
                "https://api.instantly.ai/api/v2/leads/batch",
                headers={
                    "Authorization": f"Bearer {INSTANTLY_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={"leads": leads},
                timeout=30
            )
            if resp.status_code == 429:
                print("   Rate limited — waiting 30s...")
                time.sleep(30)
                continue
            resp.raise_for_status()
            loaded += len(batch)
            print(f"   Loaded {loaded}/{len(contacts)}...")
            time.sleep(1)
        except Exception as e:
            print(f"❌ Instantly error on batch {i//BATCH + 1}: {e}")

    print(f"✅ Done — {loaded} contacts loaded to Instantly")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ArgusReach Prospect List Builder")
    parser.add_argument("--client",  required=True, help="Client ID from clients.json")
    parser.add_argument("--limit",   type=int, default=200, help="Max contacts to pull (default 200)")
    parser.add_argument("--dry-run", action="store_true", help="Skip Instantly load")
    parser.add_argument("--skip-apollo", action="store_true", help="Skip Apollo (use existing prospects.csv)")
    parser.add_argument("--skip-verify", action="store_true", help="Skip NeverBounce (test mode)")
    args = parser.parse_args()

    client = get_client(args.client)
    if not client:
        print(f"❌ Client '{args.client}' not found in clients.json")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"ArgusReach Prospect Builder — {client['firm_name']}")
    print(f"{'='*60}\n")

    # Step 1: Apollo
    if args.skip_apollo:
        csv_path = CAMPAIGNS_DIR / args.client / "prospects.csv"
        if not csv_path.exists():
            print(f"❌ No prospects.csv found at {csv_path}")
            sys.exit(1)
        with open(csv_path) as f:
            contacts = list(csv.DictReader(f))
        print(f"📂 Loaded {len(contacts)} contacts from existing prospects.csv")
    else:
        contacts = search_apollo(client, limit=args.limit)
        if not contacts:
            print("⚠️  No contacts returned from Apollo. Check API key and plan.")
            print("   For free plan testing, use --skip-apollo and add contacts manually.")
            sys.exit(0)

    # Step 2: NeverBounce verification
    if args.skip_verify:
        print("⏭️  Skipping email verification (test mode)")
        bad_contacts = []
    else:
        contacts, bad_contacts = verify_emails(contacts)
        if bad_contacts:
            add_to_dnc([c["email"] for c in bad_contacts], args.client)
            print(f"🚫 Added {len(bad_contacts)} bad emails to DNC")

    # Step 3: DNC filter
    contacts = filter_dnc(contacts, args.client)

    if not contacts:
        print("⚠️  No contacts remaining after filtering. Nothing to load.")
        sys.exit(0)

    # Step 4: Write CSV
    write_prospects_csv(contacts, args.client)

    # Step 5: Load to Instantly
    campaign_id = client.get("instantly_campaign_id", "")
    load_to_instantly(contacts, campaign_id, dry_run=args.dry_run)

    print(f"\n✅ Pipeline complete — {len(contacts)} clean contacts ready")
    print(f"   Review at: https://admin.argusreach.com/clients/{args.client}\n")


if __name__ == "__main__":
    main()
