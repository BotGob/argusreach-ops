#!/usr/bin/env python3
"""
ArgusReach — Monthly Campaign Cycle Manager
=============================================
Handles the full month-over-month campaign lifecycle:

  1. Detects when current month's campaign is winding down (>75% sequence_complete)
  2. Pulls all previously contacted emails from DB → exclusion list
  3. Searches Apollo for fresh contacts, auto-refills until target is met
  4. Verifies emails via NeverBounce
  5. Filters DNC
  6. Creates new Instantly campaign with client's sequence template
  7. Loads contacts to new campaign
  8. Alerts Vito: "Month N+1 ready — review and hit GO"

Usage:
  python3 tools/monthly_cycle.py --client <client_id> --month "April 2026"
  python3 tools/monthly_cycle.py --check-all   # check all active clients for cycle readiness
  python3 tools/monthly_cycle.py --client <client_id> --month "April 2026" --dry-run

Sequence template: campaigns/<client_id>/sequence_template.json
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
BASE_DIR      = Path(__file__).resolve().parent.parent
CLIENTS_FILE  = BASE_DIR / "monitor" / "clients.json"
CAMPAIGNS_DIR = BASE_DIR / "campaigns"
DNC_GLOBAL    = BASE_DIR / "monitor" / "dnc" / "global.txt"
ENV_FILE      = BASE_DIR / "monitor" / ".env"
CYCLE_STATE   = BASE_DIR / "monitor" / "logs" / "cycle_state.json"

# ── Load env ───────────────────────────────────────────────────────────────────
def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

APOLLO_API_KEY      = os.environ.get("APOLLO_API_KEY", "")
NEVERBOUNCE_API_KEY = os.environ.get("NEVERBOUNCE_API_KEY", "")
INSTANTLY_API_KEY   = os.environ.get("INSTANTLY_API_KEY", "")

# ── Client helpers ─────────────────────────────────────────────────────────────
def load_all_clients():
    return json.loads(CLIENTS_FILE.read_text())

def get_client(client_id):
    for c in load_all_clients().get("clients", []):
        if c["id"] == client_id:
            return c
    return None

def save_client_campaign(client_id, campaign_id, campaign_name):
    """Update clients.json with new campaign ID and name."""
    data = load_all_clients()
    for c in data.get("clients", []):
        if c["id"] == client_id:
            c["instantly_campaign_id"] = campaign_id
            c["campaign_name"]         = campaign_name
            c["active"]                = False   # stays inactive until Vito hits GO
            break
    CLIENTS_FILE.write_text(json.dumps(data, indent=2))

# ── Cycle state (prevents duplicate alerts) ───────────────────────────────────
def load_cycle_state():
    return json.loads(CYCLE_STATE.read_text()) if CYCLE_STATE.exists() else {}

def save_cycle_state(state):
    CYCLE_STATE.parent.mkdir(parents=True, exist_ok=True)
    CYCLE_STATE.write_text(json.dumps(state, indent=2))

def mark_cycle_alerted(client_id, campaign_id):
    state = load_cycle_state()
    state[f"{client_id}:{campaign_id}"] = datetime.utcnow().isoformat()
    save_cycle_state(state)

def already_alerted(client_id, campaign_id):
    state = load_cycle_state()
    return f"{client_id}:{campaign_id}" in state

# ── DB: get all previously contacted emails for client ────────────────────────
def get_contacted_emails(client_id):
    """Return set of all emails ever contacted for this client (from DB)."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from db.database import get_db
        conn   = get_db()
        rows   = conn.execute(
            "SELECT DISTINCT email FROM prospects WHERE client_id=?", (client_id,)
        ).fetchall()
        conn.close()
        return {r["email"].lower().strip() for r in rows if r["email"]}
    except Exception as e:
        print(f"⚠️  Could not load contacted emails from DB: {e}")
        return set()

# ── DB: check campaign completion rate ────────────────────────────────────────
def get_completion_stats(client_id, campaign_id):
    """Return dict with total, completed, pct_complete for a campaign."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from db.database import get_db
        conn  = get_db()
        total = conn.execute(
            "SELECT COUNT(*) FROM prospects WHERE client_id=? AND campaign_id=?",
            (client_id, campaign_id)
        ).fetchone()[0]
        done  = conn.execute(
            "SELECT COUNT(*) FROM prospects WHERE client_id=? AND campaign_id=? AND stage=?",
            (client_id, campaign_id, "sequence_complete")
        ).fetchone()[0]
        conn.close()
        pct = (done / total * 100) if total > 0 else 0
        return {"total": total, "completed": done, "pct": round(pct, 1)}
    except Exception as e:
        return {"total": 0, "completed": 0, "pct": 0, "error": str(e)}

# ── DNC helpers ───────────────────────────────────────────────────────────────
def load_dnc(client_id):
    dnc = set()
    for f in [DNC_GLOBAL, BASE_DIR / "monitor" / "dnc" / f"{client_id}.txt"]:
        if f.exists():
            for line in f.read_text().splitlines():
                e = line.strip().lower()
                if e:
                    dnc.add(e)
    return dnc

def add_to_dnc(emails, client_id):
    targets = [DNC_GLOBAL, BASE_DIR / "monitor" / "dnc" / f"{client_id}.txt"]
    for f in targets:
        f.parent.mkdir(parents=True, exist_ok=True)
        existing = set(f.read_text().splitlines()) if f.exists() else set()
        new_e    = {e.lower() for e in emails} - existing
        if new_e:
            with open(f, "a") as fh:
                for e in sorted(new_e):
                    fh.write(e + "\n")

# ── Apollo search with auto-refill ────────────────────────────────────────────
def search_apollo(client, target, exclude_emails):
    """Search Apollo, skipping already-contacted, auto-refilling until target met."""
    if not APOLLO_API_KEY:
        print("⚠️  No APOLLO_API_KEY — skipping Apollo search")
        return []

    titles     = [t.strip() for t in client.get("_target_titles", "").split(",") if t.strip()]
    geography  = client.get("_target_geography", "")
    contacts   = []
    seen_emails = set(exclude_emails)  # start with full exclusion list
    page        = 1
    max_pages   = 20  # safety limit

    print(f"🔍 Apollo search — need {target} fresh contacts (excluding {len(exclude_emails)} already contacted)")

    while len(contacts) < target and page <= max_pages:
        payload = {
            "api_key":    APOLLO_API_KEY,
            "per_page":   100,
            "page":       page,
            "person_titles": titles or ["Doctor", "Physician"],
            "contact_email_status": ["verified", "likely to engage"],
        }
        if geography:
            payload["person_locations"] = [geography]

        try:
            resp = requests.post(
                "https://api.apollo.io/v1/mixed_people/search",
                headers={"Content-Type": "application/json"},
                json=payload, timeout=30
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

            new_this_page = 0
            for p in people:
                email = p.get("email", "").lower().strip()
                if not email or email == "email_not_unlocked@domain.com":
                    continue
                if email in seen_emails:
                    continue  # skip already contacted or already added
                seen_emails.add(email)
                contacts.append({
                    "first_name":   p.get("first_name", ""),
                    "last_name":    p.get("last_name", ""),
                    "email":        email,
                    "company":      (p.get("organization") or {}).get("name", ""),
                    "title":        p.get("title", ""),
                    "city":         p.get("city", ""),
                    "state":        p.get("state", ""),
                    "linkedin_url": p.get("linkedin_url", ""),
                })
                new_this_page += 1
                if len(contacts) >= target:
                    break

            print(f"   Page {page}: +{new_this_page} new contacts ({len(contacts)}/{target} total)")
            page += 1
            time.sleep(1)

        except Exception as e:
            print(f"❌ Apollo error page {page}: {e}")
            break

    print(f"✅ Apollo: {len(contacts)} fresh contacts (after exclusions)")
    return contacts[:target]

# ── NeverBounce verification ───────────────────────────────────────────────────
def verify_emails(contacts):
    if not NEVERBOUNCE_API_KEY:
        print("⚠️  No NEVERBOUNCE_API_KEY — skipping verification")
        return contacts, []

    print(f"📧 Verifying {len(contacts)} emails via NeverBounce...")
    emails = [c["email"] for c in contacts]

    try:
        resp = requests.post(
            "https://api.neverbounce.com/v4/jobs/create",
            json={
                "key": NEVERBOUNCE_API_KEY,
                "input_location": "supplied",
                "input": [{"id": i, "email": e} for i, e in enumerate(emails)],
                "filename": f"argusreach_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
                "auto_start": True,
                "auto_parse": True,
            }, timeout=30
        )
        job_id = resp.json().get("job_id")
        print(f"   Job {job_id} — polling...")

        for _ in range(60):
            time.sleep(10)
            s = requests.get(
                "https://api.neverbounce.com/v4/jobs/status",
                params={"key": NEVERBOUNCE_API_KEY, "job_id": job_id}, timeout=15
            ).json()
            if s.get("job_status") == "complete":
                break

        results_text = requests.get(
            "https://api.neverbounce.com/v4/jobs/download",
            params={"key": NEVERBOUNCE_API_KEY, "job_id": job_id}, timeout=30
        ).text

        good = set()
        bad  = set()
        for row in csv.DictReader(results_text.strip().splitlines()):
            email  = row.get("email", "").lower().strip()
            status = row.get("result", "").lower()
            if status in ("valid", "catchall"):
                good.add(email)
            elif status in ("invalid", "disposable", "unknown"):
                bad.add(email)

        clean   = [c for c in contacts if c["email"] in good]
        removed = [c for c in contacts if c["email"] in bad]
        print(f"✅ NeverBounce: {len(clean)} valid, {len(removed)} removed")
        return clean, removed

    except Exception as e:
        print(f"❌ NeverBounce error: {e}")
        return contacts, []

# ── Load sequence template ─────────────────────────────────────────────────────
def load_sequence_template(client_id):
    """Load sequence template from campaigns/<client_id>/sequence_template.json"""
    path = CAMPAIGNS_DIR / client_id / "sequence_template.json"
    if path.exists():
        return json.loads(path.read_text())
    return None

def save_sequence_template(client_id, steps):
    """Save sequence template for future months."""
    path = CAMPAIGNS_DIR / client_id / "sequence_template.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"steps": steps}, indent=2))
    print(f"💾 Sequence template saved to {path}")

# ── Create Instantly campaign ──────────────────────────────────────────────────
def create_instantly_campaign(client, month_name, sequence_steps=None):
    """Create a new Instantly campaign for the given month."""
    firm     = client["firm_name"]
    name     = f"ArgusReach — {firm} — {month_name}"
    timezone = client.get("send_timezone", "America/New_York")

    payload = {
        "name": name,
        "campaign_schedule": {
            "schedules": [{
                "name":   "Business Hours",
                "timing": {"from": "08:00", "to": "17:00"},
                "days":   {"1": True, "2": True, "3": True, "4": True, "5": True},
                "timezone": timezone
            }]
        },
        "stop_on_reply":   True,
        "track_settings":  ["open_lead_clicked"],
    }

    if sequence_steps:
        payload["sequences"] = [{"steps": sequence_steps}]

    resp = requests.post(
        "https://api.instantly.ai/api/v2/campaigns",
        headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=20
    )
    resp.raise_for_status()
    data = resp.json()
    campaign_id = data["id"]
    print(f"✅ Instantly campaign created: {name} ({campaign_id})")
    return campaign_id, name

# ── Add sending account to campaign ───────────────────────────────────────────
def add_sending_account(campaign_id, outreach_email):
    """Link the client's outreach email to the new campaign."""
    try:
        resp = requests.post(
            f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}/mailaccounts",
            headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"},
            json={"email": outreach_email}, timeout=15
        )
        if resp.status_code == 200:
            print(f"✅ Sending account {outreach_email} linked to campaign")
        else:
            print(f"⚠️  Could not auto-link sending account ({resp.status_code}) — link manually in Instantly")
    except Exception as e:
        print(f"⚠️  Sending account link failed (link manually): {e}")

# ── Load contacts into Instantly ──────────────────────────────────────────────
def load_to_instantly(contacts, campaign_id, dry_run=False):
    if dry_run:
        print(f"   [DRY RUN] Would load {len(contacts)} contacts to {campaign_id}")
        return
    print(f"🚀 Loading {len(contacts)} contacts to Instantly...")
    loaded = 0
    for i in range(0, len(contacts), 50):
        batch = contacts[i:i+50]
        leads = [{
            "campaign":             campaign_id,
            "email":                c["email"],
            "first_name":           c.get("first_name", ""),
            "last_name":            c.get("last_name", ""),
            "company_name":         c.get("company", ""),
            "skip_if_in_workspace": True,
        } for c in batch]
        try:
            resp = requests.post(
                "https://api.instantly.ai/api/v2/leads/batch",
                headers={"Authorization": f"Bearer {INSTANTLY_API_KEY}", "Content-Type": "application/json"},
                json={"leads": leads}, timeout=30
            )
            if resp.status_code == 429:
                time.sleep(30)
                continue
            resp.raise_for_status()
            loaded += len(batch)
            print(f"   {loaded}/{len(contacts)} loaded...")
            time.sleep(1)
        except Exception as e:
            print(f"❌ Batch load error: {e}")
    print(f"✅ {loaded} contacts loaded to Instantly")

# ── Write prospects CSV ────────────────────────────────────────────────────────
def write_csv(contacts, client_id, month_name):
    slug     = month_name.lower().replace(" ", "_")
    out_path = CAMPAIGNS_DIR / client_id / f"prospects_{slug}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields   = ["first_name","last_name","email","company","title","city","state","linkedin_url"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in contacts:
            w.writerow({k: c.get(k, "") for k in fields})
    print(f"💾 Saved {len(contacts)} contacts → {out_path}")
    return out_path

# ── Telegram notification ──────────────────────────────────────────────────────
def notify(msg):
    bot_token = os.environ.get("ARGUSREACH_BOT_TOKEN", "8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk")
    chat_id   = os.environ.get("ARGUSREACH_CHAT_ID", "-1003821840813")
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception:
        pass

# ── Check all active clients for cycle readiness ──────────────────────────────
def check_all_clients():
    """Check all active clients — alert if campaign is >75% complete."""
    data    = load_all_clients()
    clients = [c for c in data.get("clients", []) if c.get("active")]

    if not clients:
        print("No active clients.")
        return

    for client in clients:
        cid         = client["id"]
        firm        = client["firm_name"]
        campaign_id = client.get("instantly_campaign_id", "")
        if not campaign_id:
            continue

        stats = get_completion_stats(cid, campaign_id)
        total = stats["total"]
        pct   = stats["pct"]

        print(f"{firm}: {stats['completed']}/{total} complete ({pct}%)")

        if pct >= 75 and total >= 10:
            if already_alerted(cid, campaign_id):
                print(f"  → Alert already sent, skipping")
                continue

            mark_cycle_alerted(cid, campaign_id)
            notify(
                f"📅 *Campaign Winding Down — {firm}*\n\n"
                f"{stats['completed']}/{total} contacts have completed the sequence ({pct}%).\n\n"
                f"Time to build next month's batch.\n"
                f"Run: `python3 tools/monthly_cycle.py --client {cid} --month \"[Next Month]\"` "
                f"to build and load the next campaign automatically."
            )
            print(f"  ⚠️  Alert sent — campaign is {pct}% complete")

# ── Main pipeline ─────────────────────────────────────────────────────────────
def run_cycle(client_id, month_name, dry_run=False, skip_apollo=False, skip_verify=False):
    client = get_client(client_id)
    if not client:
        print(f"❌ Client '{client_id}' not found")
        sys.exit(1)

    firm   = client["firm_name"]
    target = client.get("contacts_per_month", 200)

    print(f"\n{'='*60}")
    print(f"ArgusReach Monthly Cycle — {firm} — {month_name}")
    print(f"{'='*60}\n")

    # Step 1: Load exclusion list from DB
    print("📋 Loading previously contacted emails from database...")
    already_contacted = get_contacted_emails(client_id)
    print(f"   {len(already_contacted)} emails to exclude")

    # Step 2: Apollo search (with auto-refill, exclusion)
    if skip_apollo:
        # Use existing CSV
        slug    = month_name.lower().replace(" ", "_")
        csv_path = CAMPAIGNS_DIR / client_id / f"prospects_{slug}.csv"
        if not csv_path.exists():
            print(f"❌ No prospects CSV found at {csv_path}")
            sys.exit(1)
        with open(csv_path) as f:
            contacts = list(csv.DictReader(f))
        print(f"📂 Loaded {len(contacts)} contacts from {csv_path}")
    else:
        contacts = search_apollo(client, target, already_contacted)
        if not contacts:
            print("⚠️  Apollo returned no contacts. Check API key, plan, and ICP configuration.")
            sys.exit(0)

    # Step 3: NeverBounce verify
    if skip_verify:
        print("⏭️  Skipping email verification (test mode)")
        bad = []
    else:
        contacts, bad = verify_emails(contacts)
        if bad:
            add_to_dnc([c["email"] for c in bad], client_id)
            print(f"🚫 {len(bad)} bad emails added to DNC")

    # Step 4: DNC filter
    dnc    = load_dnc(client_id)
    before = len(contacts)
    contacts = [c for c in contacts if c["email"] not in dnc]
    removed  = before - len(contacts)
    if removed:
        print(f"🚫 DNC filter removed {removed} contacts")

    if not contacts:
        print("⚠️  No contacts remaining after filtering.")
        sys.exit(0)

    print(f"\n✅ {len(contacts)} clean contacts ready for {month_name}")

    # Step 5: Write CSV
    write_csv(contacts, client_id, month_name)

    # Step 6: Create new Instantly campaign
    sequence = load_sequence_template(client_id)
    if not sequence:
        print("⚠️  No sequence template found — campaign will be created without sequence steps.")
        print(f"   Add template to: campaigns/{client_id}/sequence_template.json")
        print("   You can add the sequence manually in Instantly after campaign creation.")

    if not dry_run:
        campaign_id, campaign_name = create_instantly_campaign(
            client, month_name,
            sequence_steps=sequence.get("steps") if sequence else None
        )

        # Link sending account
        if client.get("outreach_email"):
            add_sending_account(campaign_id, client["outreach_email"])

        # Step 7: Load contacts
        load_to_instantly(contacts, campaign_id, dry_run=dry_run)

        # Step 8: Update clients.json with new campaign
        save_client_campaign(client_id, campaign_id, campaign_name)

        # Step 9: Alert Vito
        msg = (
            f"✅ *{firm} — {month_name} Campaign Ready*\n\n"
            f"*{len(contacts)} fresh contacts loaded*\n"
            f"Campaign: `{campaign_name}`\n\n"
            f"*Before hitting GO in Instantly, review:*\n"
            f"• Sequence copy and tone\n"
            f"• Email delays between steps (should be 7+ days)\n"
            f"• Sending schedule and account\n\n"
            f"When ready: activate campaign in Instantly → "
            f"monitor will resume automatically.\n\n"
            f"Admin portal: https://admin.argusreach.com/clients/{client_id}"
        )
        notify(msg)
        print(f"\n🔔 Vito notified via Telegram")
        print(f"\n{'='*60}")
        print(f"✅ CYCLE COMPLETE — {month_name}")
        print(f"   Campaign ID: {campaign_id}")
        print(f"   Contacts:    {len(contacts)}")
        print(f"   Next step:   Review sequence in Instantly, then hit GO")
        print(f"{'='*60}\n")
    else:
        print(f"\n[DRY RUN] Would create campaign '{firm} — {month_name}' with {len(contacts)} contacts")

# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ArgusReach Monthly Campaign Cycle Manager")
    parser.add_argument("--client",       help="Client ID")
    parser.add_argument("--month",        help='Month name e.g. "April 2026"')
    parser.add_argument("--check-all",    action="store_true", help="Check all active clients for cycle readiness")
    parser.add_argument("--dry-run",      action="store_true", help="Build list but skip Instantly")
    parser.add_argument("--skip-apollo",  action="store_true", help="Skip Apollo, use existing CSV")
    parser.add_argument("--skip-verify",  action="store_true", help="Skip NeverBounce (test mode)")
    args = parser.parse_args()

    if args.check_all:
        check_all_clients()
    elif args.client and args.month:
        run_cycle(
            args.client, args.month,
            dry_run=args.dry_run,
            skip_apollo=args.skip_apollo,
            skip_verify=args.skip_verify
        )
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
