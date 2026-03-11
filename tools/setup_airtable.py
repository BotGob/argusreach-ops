#!/usr/bin/env python3
"""
ArgusReach — Airtable CRM Setup Script
Builds all tables and fields in an existing Airtable base.
Run once after creating the base manually in Airtable UI.
"""

import os
import json
import urllib.request
import urllib.error
from pathlib import Path

ENV_PATH = Path(__file__).parent.parent / "monitor" / ".env"

def load_env():
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

def api(method, path, body=None):
    token = os.environ["AIRTABLE_TOKEN"]
    url = f"https://api.airtable.com/v0{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  ERROR {e.code}: {err}")
        return None

def create_table(base_id, name, fields):
    print(f"  Creating table: {name}...", end=" ", flush=True)
    result = api("POST", f"/meta/bases/{base_id}/tables", {
        "name": name,
        "fields": fields
    })
    if result and result.get("id"):
        print(f"✓  (id: {result['id']})")
        return result["id"]
    else:
        print("FAILED")
        return None

def main():
    load_env()
    token = os.environ.get("AIRTABLE_TOKEN")
    base_id = os.environ.get("AIRTABLE_BASE_ID")

    if not token or not base_id:
        print("ERROR: AIRTABLE_TOKEN or AIRTABLE_BASE_ID not set in monitor/.env")
        return

    print(f"\nArgusReach CRM Setup")
    print(f"  Base ID: {base_id}\n")

    tables = [
        ("Clients", [
            {"name": "Practice Name",      "type": "singleLineText"},
            {"name": "Contact Name",       "type": "singleLineText"},
            {"name": "Email",              "type": "email"},
            {"name": "Phone",              "type": "phoneNumber"},
            {"name": "Vertical",           "type": "singleSelect", "options": {"choices": [
                {"name": "Physical Therapy",  "color": "blueBright"},
                {"name": "Insurance",         "color": "greenBright"},
                {"name": "Wealth Management", "color": "yellowBright"},
                {"name": "Mental Health",     "color": "purpleBright"},
                {"name": "RIA / Gov Entity",  "color": "tealBright"},
                {"name": "Other",             "color": "grayBright"},
            ]}},
            {"name": "Plan", "type": "singleSelect", "options": {"choices": [
                {"name": "Pilot",   "color": "purpleBright"},
                {"name": "Starter", "color": "blueBright"},
                {"name": "Growth",  "color": "greenBright"},
                {"name": "Scale",   "color": "yellowBright"},
            ]}},
            {"name": "Monthly Fee",  "type": "currency", "options": {"precision": 2, "symbol": "$"}},
            {"name": "Setup Fee",    "type": "currency", "options": {"precision": 2, "symbol": "$"}},
            {"name": "Start Date",   "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Status", "type": "singleSelect", "options": {"choices": [
                {"name": "Prospect",        "color": "yellowBright"},
                {"name": "Proposal Sent",   "color": "orangeBright"},
                {"name": "Onboarding",      "color": "blueBright"},
                {"name": "Active",          "color": "greenBright"},
                {"name": "Paused",          "color": "grayBright"},
                {"name": "Churned",         "color": "redBright"},
            ]}},
            {"name": "Outreach Email",  "type": "email"},
            {"name": "Calendly Link",   "type": "url"},
            {"name": "Notes",           "type": "multilineText"},
        ]),

        ("Prospects", [
            {"name": "First Name",          "type": "singleLineText"},
            {"name": "Last Name",           "type": "singleLineText"},
            {"name": "Title",               "type": "singleLineText"},
            {"name": "Practice / Company",  "type": "singleLineText"},
            {"name": "Email",               "type": "email"},
            {"name": "Phone",               "type": "phoneNumber"},
            {"name": "Address",             "type": "singleLineText"},
            {"name": "City",                "type": "singleLineText"},
            {"name": "State",               "type": "singleLineText"},
            {"name": "Specialty",           "type": "singleLineText"},
            {"name": "Client",              "type": "singleLineText"},
            {"name": "Status", "type": "singleSelect", "options": {"choices": [
                {"name": "Not Contacted",          "color": "grayBright"},
                {"name": "In Sequence",            "color": "blueBright"},
                {"name": "Opened",                 "color": "yellowBright"},
                {"name": "Replied — Interested",   "color": "greenBright"},
                {"name": "Replied — Not Now",      "color": "orangeBright"},
                {"name": "Meeting Booked",         "color": "tealBright"},
                {"name": "DNC",                    "color": "redBright"},
                {"name": "Existing Relationship",  "color": "purpleBright"},
            ]}},
            {"name": "Last Contacted",  "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Times Contacted", "type": "number", "options": {"precision": 0}},
            {"name": "Last Reply",      "type": "multilineText"},
            {"name": "Follow Up Date",  "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Meeting Date",    "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Source", "type": "singleSelect", "options": {"choices": [
                {"name": "Apollo",             "color": "blueBright"},
                {"name": "Client Warm List",   "color": "greenBright"},
                {"name": "Manual",             "color": "grayBright"},
            ]}},
            {"name": "Custom Opening",  "type": "multilineText"},
            {"name": "Notes",           "type": "multilineText"},
        ]),

        ("Touch Log", [
            {"name": "Prospect Email",   "type": "singleLineText"},
            {"name": "Prospect Name",    "type": "singleLineText"},
            {"name": "Client",           "type": "singleLineText"},
            {"name": "Date Sent",        "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Touch Number",     "type": "number", "options": {"precision": 0}},
            {"name": "Subject Line",     "type": "singleLineText"},
            {"name": "Outcome", "type": "singleSelect", "options": {"choices": [
                {"name": "Sent",                "color": "grayBright"},
                {"name": "Opened",              "color": "yellowBright"},
                {"name": "Clicked",             "color": "orangeBright"},
                {"name": "Replied — Positive",  "color": "greenBright"},
                {"name": "Replied — Negative",  "color": "redBright"},
                {"name": "Bounced",             "color": "redBright"},
                {"name": "Unsubscribed",        "color": "redBright"},
            ]}},
            {"name": "Reply Text",  "type": "multilineText"},
            {"name": "Notes",       "type": "multilineText"},
        ]),

        ("Campaigns", [
            {"name": "Campaign Name",         "type": "singleLineText"},
            {"name": "Client",                "type": "singleLineText"},
            {"name": "Status", "type": "singleSelect", "options": {"choices": [
                {"name": "Building",     "color": "grayBright"},
                {"name": "Warming Up",   "color": "yellowBright"},
                {"name": "Active",       "color": "greenBright"},
                {"name": "Paused",       "color": "orangeBright"},
                {"name": "Complete",     "color": "blueBright"},
            ]}},
            {"name": "Start Date",            "type": "date", "options": {"dateFormat": {"name": "us"}}},
            {"name": "Contacts Loaded",       "type": "number", "options": {"precision": 0}},
            {"name": "Emails Sent",           "type": "number", "options": {"precision": 0}},
            {"name": "Meetings Booked",       "type": "number", "options": {"precision": 0}},
            {"name": "Instantly Campaign ID", "type": "singleLineText"},
            {"name": "Notes",                 "type": "multilineText"},
        ]),
    ]

    created = []
    for name, fields in tables:
        tid = create_table(base_id, name, fields)
        if tid:
            created.append(name)

    print(f"\n{'─'*50}")
    print(f"  Created: {len(created)}/4 tables")
    for t in created:
        print(f"    ✓  {t}")
    print(f"\n  Open your CRM:")
    print(f"  https://airtable.com/{base_id}")
    print(f"\n  Note: Airtable created a default 'Table 1' — you can delete it manually.\n")

if __name__ == "__main__":
    main()
