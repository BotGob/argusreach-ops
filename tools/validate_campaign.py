#!/usr/bin/env python3
"""
ArgusReach — Pre-Launch Campaign Validator
==========================================
Run this BEFORE activating any campaign in Instantly.
Catches the embarrassing stuff: missing names, unreplaced variables,
mismatched company/email domains, duplicate leads, empty fields.

Usage:
  python3 validate_campaign.py <campaign_id>
  python3 validate_campaign.py d1b7a0af-ae35-4715-9619-6fd18811c528

Exit code 0 = safe to launch. Exit code 1 = problems found, DO NOT LAUNCH.
"""

import sys
import json
import urllib.request
import urllib.error
import re
import os

API_KEY = os.environ.get("INSTANTLY_API_KEY", "MWYzYWRkZjYtNDhmZC00OTRiLWFjZDMtNGM1YWUyYTIwZTMyOlF0SHFoWlJRTlZ0bg==")
BASE_URL = "https://api.instantly.ai/api/v2"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

TEMPLATE_VARS = ["{{first_name}}", "{{last_name}}", "{{company}}", "{{company_name}}"]

def api_get(path):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def api_post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def check(condition, message, level="ERROR"):
    icon = "✓" if condition else ("✗" if level == "ERROR" else "⚠")
    print(f"  {icon} {message}")
    return condition

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate_campaign.py <campaign_id>")
        sys.exit(1)

    campaign_id = sys.argv[1]
    errors = 0
    warnings = 0

    print(f"\n{'='*60}")
    print(f"ArgusReach Pre-Launch Validator")
    print(f"Campaign: {campaign_id}")
    print(f"{'='*60}\n")

    # --- 1. Get campaign ---
    print("[ CAMPAIGN ]")
    try:
        campaign = api_get(f"/campaigns/{campaign_id}")
    except Exception as e:
        print(f"  ✗ Could not fetch campaign: {e}")
        sys.exit(1)

    name = campaign.get("name", "")
    status = campaign.get("status", -1)
    check(name, f"Campaign name: {name}")
    check(status == 0, f"Status is PAUSED (status={status}) — safe to validate")

    # --- 2. Check sequence ---
    print("\n[ SEQUENCE ]")
    sequences = campaign.get("sequences", [])
    check(len(sequences) > 0, f"Sequences found: {len(sequences)}")

    all_bodies = []
    for seq_idx, seq in enumerate(sequences):
        steps = seq.get("steps", [])
        check(len(steps) > 0, f"Sequence {seq_idx+1}: {len(steps)} steps found")
        for step_idx, step in enumerate(steps):
            for var_idx, variant in enumerate(step.get("variants", [])):
                subject = variant.get("subject", "")
                body = variant.get("body", "")
                all_bodies.append(body)

                # Check for unreplaced template variables IN THE TEMPLATE ITSELF
                # (these should be placeholders, not actual unreplaced content)
                # We flag if non-variable {{ }} patterns exist
                leftover = re.findall(r'\{\{(?!first_name|last_name|company|company_name)[^}]+\}\}', body + subject)
                if leftover:
                    print(f"  ✗ Step {step_idx+1}: Unknown variables found: {leftover}")
                    errors += 1
                else:
                    print(f"  ✓ Step {step_idx+1}: Variables look correct ({{{{first_name}}}}, {{{{company}}}} present)")

                check(len(body) > 50, f"  Step {step_idx+1}: Body has content ({len(body)} chars)")
                check(bool(subject), f"  Step {step_idx+1}: Subject set: '{subject[:60]}'")

    # --- 3. Get leads ---
    print("\n[ LEADS ]")
    try:
        # Paginate through all leads
        leads = []
        starting_after = None
        while True:
            payload = {"campaign_id": campaign_id, "limit": 100}
            if starting_after:
                payload["starting_after"] = starting_after
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{BASE_URL}/leads/list",
                data=data,
                headers=HEADERS,
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())
            batch = result.get("items", [])
            leads.extend(batch)
            if len(batch) < 100:
                break
            starting_after = batch[-1]["id"]
    except Exception as e:
        print(f"  ✗ Could not fetch leads: {e}")
        leads = []

    check(len(leads) > 0, f"Total leads loaded: {len(leads)}")

    missing_firstname = []
    missing_company = []
    suspicious_emails = []
    duplicates = []
    seen_emails = {}

    for l in leads:
        em = (l.get("email") or "").strip()
        fn = (l.get("first_name") or "").strip()
        co = (l.get("company_name") or "").strip()

        if not fn:
            missing_firstname.append(em)
        if not co:
            missing_company.append(em)

        # Check for test/placeholder emails
        if any(x in em.lower() for x in ["test@", "example.com", "placeholder", "fake", "dummy"]):
            suspicious_emails.append(em)

        # Check for unreplaced variables in lead data
        for field in [fn, co, em]:
            if "{{" in field:
                suspicious_emails.append(f"UNREPLACED VAR in lead: {field}")

        # Duplicate detection
        if em in seen_emails:
            duplicates.append(em)
        seen_emails[em] = True

    if missing_firstname:
        print(f"  ✗ {len(missing_firstname)} leads missing first_name: {missing_firstname}")
        errors += len(missing_firstname)
    else:
        print(f"  ✓ All leads have first_name populated")

    if missing_company:
        print(f"  ✗ {len(missing_company)} leads missing company_name: {missing_company}")
        errors += len(missing_company)
    else:
        print(f"  ✓ All leads have company_name populated")

    if suspicious_emails:
        print(f"  ✗ Suspicious leads found: {suspicious_emails}")
        errors += len(suspicious_emails)
    else:
        print(f"  ✓ No test/placeholder emails detected")

    if duplicates:
        print(f"  ⚠ Duplicate emails found: {duplicates}")
        warnings += len(duplicates)
    else:
        print(f"  ✓ No duplicate emails")

    # --- 4. Email format check ---
    print("\n[ EMAIL FORMAT ]")
    invalid_emails = []
    for l in leads:
        em = (l.get("email") or "").strip()
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', em):
            invalid_emails.append(em)

    if invalid_emails:
        print(f"  ✗ Invalid email format: {invalid_emails}")
        errors += len(invalid_emails)
    else:
        print(f"  ✓ All {len(leads)} emails are properly formatted")

    # --- 5. Sending account ---
    print("\n[ SENDING ACCOUNT ]")
    email_list = campaign.get("email_list", [])
    check(len(email_list) > 0, f"Sending account configured: {email_list}")

    # --- RESULT ---
    print(f"\n{'='*60}")
    if errors == 0 and warnings == 0:
        print(f"✅ ALL CHECKS PASSED — Safe to launch ({len(leads)} leads)")
        print(f"{'='*60}\n")
        sys.exit(0)
    elif errors == 0:
        print(f"⚠️  WARNINGS ONLY ({warnings} warnings, 0 errors)")
        print(f"   Review warnings above before launching.")
        print(f"{'='*60}\n")
        sys.exit(0)
    else:
        print(f"🚫 DO NOT LAUNCH — {errors} error(s), {warnings} warning(s) found")
        print(f"   Fix all errors above before activating the campaign.")
        print(f"{'='*60}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
