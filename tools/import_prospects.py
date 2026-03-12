#!/usr/bin/env python3
"""
ArgusReach — Prospect Import Tool
───────────────────────────────────
Loads a prospect CSV (from Apollo, client list, or manual) into Airtable
and creates Touch Log entry for each contact when outreach starts.

Usage:
    python3 import_prospects.py --csv leads.csv --client bay_harbor_wealth
    python3 import_prospects.py --csv leads.csv --client bay_harbor_wealth --dry-run

CSV columns accepted (flexible — fuzzy matched):
    First Name, Last Name, Email, Title, Company/Practice, Phone, City, State, Source

After import, all prospects appear in Airtable with Status = "Not Contacted".
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

ENV_PATH = Path(__file__).parent.parent / "monitor" / ".env"

def load_env():
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

def airtable(method, table, body=None, params=None):
    token   = os.environ["AIRTABLE_TOKEN"]
    base_id = os.environ["AIRTABLE_BASE_ID"]
    url     = f"https://api.airtable.com/v0/{base_id}/{table}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.request(method, url, headers=headers,
                                json=body, params=params, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json()
        else:
            print(f"  Airtable error {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  Request error: {e}")
        return None

# Fuzzy column mapper — maps any reasonable header to our standard field name
FIELD_MAP = {
    "first name":    "First Name",
    "firstname":     "First Name",
    "first":         "First Name",
    "last name":     "Last Name",
    "lastname":      "Last Name",
    "last":          "Last Name",
    "email":         "Email",
    "email address": "Email",
    "title":         "Title",
    "job title":     "Title",
    "company":       "Practice / Company",
    "practice":      "Practice / Company",
    "organization":  "Practice / Company",
    "company name":  "Practice / Company",
    "firm":          "Practice / Company",
    "phone":         "Phone",
    "phone number":  "Phone",
    "mobile":        "Phone",
    "city":          "City",
    "state":         "State",
    "source":        "Source",
}

def map_headers(headers):
    mapping = {}
    for i, h in enumerate(headers):
        key = h.strip().lower()
        if key in FIELD_MAP:
            mapping[FIELD_MAP[key]] = i
    return mapping

def parse_csv(path):
    rows = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        headers = next(reader)
        col_map = map_headers(headers)

        if "Email" not in col_map:
            print(f"ERROR: No email column found. Headers: {headers}")
            sys.exit(1)

        for row in reader:
            if not row:
                continue
            record = {}
            for field, idx in col_map.items():
                val = row[idx].strip() if idx < len(row) else ""
                if val:
                    record[field] = val
            if record.get("Email"):
                rows.append(record)

    return rows

def import_prospects(csv_path, client_id, dry_run=False):
    load_env()

    if not os.environ.get("AIRTABLE_TOKEN"):
        print("ERROR: AIRTABLE_TOKEN not set in monitor/.env")
        sys.exit(1)

    prospects = parse_csv(csv_path)
    print(f"\nArgusReach Prospect Import")
    print(f"  File:   {csv_path}")
    print(f"  Client: {client_id}")
    print(f"  Rows:   {len(prospects)}")
    print(f"  Mode:   {'DRY RUN' if dry_run else 'LIVE'}\n")

    if not prospects:
        print("No valid rows found. Check CSV format.")
        return

    imported = 0
    skipped  = 0
    errors   = 0

    for i, p in enumerate(prospects):
        email = p.get("Email", "").lower().strip()

        # Check for duplicate
        existing = airtable("GET", "Prospects",
                            params={"filterByFormula": f"LOWER({{Email)}}='{email}'",
                                    "maxRecords": 1})
        if existing and existing.get("records"):
            print(f"  [SKIP] {email} — already in Airtable")
            skipped += 1
            continue

        fields = {
            "First Name":          p.get("First Name", ""),
            "Last Name":           p.get("Last Name", ""),
            "Email":               email,
            "Title":               p.get("Title", ""),
            "Practice / Company":  p.get("Practice / Company", ""),
            "Phone":               p.get("Phone", ""),
            "City":                p.get("City", ""),
            "State":               p.get("State", ""),
            "Client":              client_id,
            "Status":              "Not Contacted",
            "Source":              p.get("Source", "Apollo"),
            "Times Contacted":     0,
        }
        # Remove empty fields
        fields = {k: v for k, v in fields.items() if v != "" and v != 0 or k in ("Times Contacted",)}

        if dry_run:
            print(f"  [DRY]  Would import: {fields.get('First Name','')} {fields.get('Last Name','')} <{email}>")
            imported += 1
            continue

        result = airtable("POST", "Prospects", body={"fields": fields})
        if result and result.get("id"):
            print(f"  [OK]   {fields.get('First Name','')} {fields.get('Last Name','')} <{email}>")
            imported += 1
        else:
            print(f"  [ERR]  Failed to import {email}")
            errors += 1

        # Airtable rate limit: 5 req/sec
        if (i + 1) % 5 == 0:
            time.sleep(1)

    print(f"\n{'─'*50}")
    print(f"  Imported: {imported}")
    print(f"  Skipped:  {skipped} (duplicates)")
    print(f"  Errors:   {errors}")
    print(f"\n  View in Airtable:")
    print(f"  https://airtable.com/{os.environ['AIRTABLE_BASE_ID']}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import prospects from CSV into Airtable")
    parser.add_argument("--csv",      required=True, help="Path to CSV file")
    parser.add_argument("--client",   required=True, help="Client ID (matches clients.json)")
    parser.add_argument("--dry-run",  action="store_true", help="Preview without writing to Airtable")
    args = parser.parse_args()

    import_prospects(args.csv, args.client, dry_run=args.dry_run)
