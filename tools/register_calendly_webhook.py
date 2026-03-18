#!/usr/bin/env python3
"""
Register ArgusReach webhook with Calendly.
Run once — or re-run to update the registered URL.

Usage:
    python3 tools/register_calendly_webhook.py

Requires CALENDLY_API_TOKEN in monitor/.env
"""

import os, sys, json, requests
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")

API_TOKEN    = os.environ.get("CALENDLY_API_TOKEN", "")
WEBHOOK_URL  = "https://hooks.argusreach.com/webhooks/calendly"
EVENTS       = ["invitee.created", "invitee.canceled"]

if not API_TOKEN:
    print("❌ CALENDLY_API_TOKEN not set in monitor/.env")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

# Get current user/org URI
me = requests.get("https://api.calendly.com/users/me", headers=headers).json()
user_uri = me.get("resource", {}).get("uri", "")
org_uri  = me.get("resource", {}).get("current_organization", "")
print(f"User: {me.get('resource', {}).get('name', '?')}")
print(f"Org:  {org_uri}")

# Check existing webhooks
existing = requests.get(
    "https://api.calendly.com/webhook_subscriptions",
    headers=headers,
    params={"organization": org_uri, "scope": "organization"}
).json()

for hook in existing.get("collection", []):
    if hook.get("callback_url") == WEBHOOK_URL:
        print(f"\n✅ Webhook already registered: {hook['uri']}")
        print(f"   Events: {hook['events']}")
        print(f"   State:  {hook['state']}")
        sys.exit(0)

# Register new webhook
resp = requests.post(
    "https://api.calendly.com/webhook_subscriptions",
    headers=headers,
    json={
        "url":          WEBHOOK_URL,
        "events":       EVENTS,
        "organization": org_uri,
        "user":         user_uri,
        "scope":        "organization",
    }
)

if resp.status_code in (200, 201):
    hook = resp.json().get("resource", {})
    signing_key = hook.get("signing_key", "")
    print(f"\n✅ Webhook registered!")
    print(f"   URI:     {hook.get('uri')}")
    print(f"   Events:  {hook.get('events')}")
    if signing_key:
        print(f"\n🔑 Signing key (add to monitor/.env as CALENDLY_WEBHOOK_SIGNING_KEY):")
        print(f"   {signing_key}")

        # Auto-write to .env
        env_file = BASE_DIR / "monitor" / ".env"
        content = env_file.read_text()
        if "CALENDLY_WEBHOOK_SIGNING_KEY" in content:
            import re
            content = re.sub(r"CALENDLY_WEBHOOK_SIGNING_KEY=.*", f"CALENDLY_WEBHOOK_SIGNING_KEY={signing_key}", content)
        else:
            content += f"\nCALENDLY_WEBHOOK_SIGNING_KEY={signing_key}\n"
        env_file.write_text(content)
        print(f"   ✅ Auto-written to monitor/.env")
else:
    print(f"\n❌ Failed: {resp.status_code}")
    print(resp.json())
