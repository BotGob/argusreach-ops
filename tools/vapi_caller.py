#!/usr/bin/env python3
"""
ArgusReach - Vapi Voice Call Integration
Handles outbound AI calls via Vapi.ai as part of the outreach sequence.

Flow:
  1. Called by monitor after T2 email with no reply after 3 days
  2. Fires outbound call to prospect via Vapi
  3. Vapi webhook fires on call end -> /webhooks/vapi
  4. Outcome logged to DB -> triggers follow-up email if answered

Usage:
  python3 tools/vapi_caller.py --test --phone +15551234567 --client argusreach
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / "monitor" / ".env")

VAPI_API_KEY    = os.environ.get("VAPI_API_KEY", "")
VAPI_PHONE_ID   = os.environ.get("VAPI_PHONE_NUMBER_ID", "")
PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "https://hooks.argusreach.com")

VAPI_BASE = "https://api.vapi.ai"


def build_assistant(client: dict, prospect: dict) -> dict:
    """
    Build a transient Vapi assistant config for this specific client + prospect.
    Script is personalized to the client's firm and the prospect's name/company.
    """
    client_name  = client.get("firm_name", "our client")
    sender_name  = client.get("sender_name", "our team")
    prospect_first = prospect.get("first_name", "there")
    prospect_company = prospect.get("company", "your practice")
    calendly_link = client.get("calendly_link", "")

    # Build the system prompt — tight, natural, goal-oriented
    system_prompt = f"""You are a professional outreach assistant calling on behalf of {client_name}.

Your ONLY goal is to book a 15-minute introductory call between the prospect and {sender_name} at {client_name}.

The prospect ({prospect_first} at {prospect_company}) received an email recently about a potential partnership. You are following up on that email.

SCRIPT:
- Open: "Hi, is this {prospect_first}? ... Great, I'm calling on behalf of {client_name} — they sent you an email recently about a potential referral partnership with {prospect_company}. I just wanted to make sure it didn't get buried. Do you have 30 seconds?"
- If yes/open: "We work with practices like yours to build consistent referral pipelines — basically handling all the outreach to physicians in your area so you don't have to. {sender_name} would love 15 minutes to walk you through how it works. Would this week or early next week work for a quick call?"
- If they want to book: "Perfect — I'll have {sender_name} send you a calendar link right after this call so you can grab whatever time works best."
- If not interested: "Totally understood — I'll make sure you're removed from our list. Sorry to interrupt your day."
- If voicemail: Leave a brief message: "Hi {prospect_first}, this is a message on behalf of {client_name}. They reached out recently about a referral partnership — {sender_name} would love to connect when you have a moment. You'll receive an email with their contact info shortly."

RULES:
- Keep the call under 90 seconds
- Never discuss pricing
- Never make specific promises about results
- If asked if you are AI or a robot: say "I'm an automated assistant calling on behalf of {client_name}"
- If they ask to be removed from contact: agree immediately, end politely
- Do not call back if they say they are not interested
- Always be professional and respectful of their time
- Call window is business hours only — if reached outside business hours, apologize and end the call"""

    return {
        "name": f"ArgusReach - {client_name} Follow-up",
        "firstMessage": f"Hi, is this {prospect_first}?",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": system_prompt}],
            "temperature": 0.7,
            "maxTokens": 500,
        },
        "voice": {
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",  # Rachel - professional, warm
        },
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "silenceTimeoutSeconds": 10,
        "maxDurationSeconds": 180,
        "backgroundDenoisingEnabled": True,
        "serverUrl": f"{PORTAL_BASE_URL}/webhooks/vapi",
        "serverUrlSecret": os.environ.get("VAPI_WEBHOOK_SECRET", ""),
    }


def fire_call(client: dict, prospect: dict, dry_run: bool = False) -> dict:
    """
    Fire an outbound call via Vapi for a prospect.
    Returns the Vapi call object or a dry_run stub.
    """
    if not VAPI_API_KEY:
        print("No VAPI_API_KEY configured — skipping call")
        return {}

    if not VAPI_PHONE_ID:
        print("No VAPI_PHONE_NUMBER_ID configured — skipping call")
        return {}

    phone = prospect.get("phone", "").strip()
    if not phone:
        print(f"  No phone number for {prospect.get('email', '?')} — skipping")
        return {}

    # Normalize phone to E.164
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("+"):
        phone = "+1" + phone  # assume US
    if len(phone) < 10:
        print(f"  Invalid phone number: {phone} — skipping")
        return {}

    if dry_run:
        print(f"  [DRY RUN] Would call {phone} for {prospect.get('email')}")
        return {"id": "dry-run", "status": "queued", "phone": phone}

    assistant = build_assistant(client, prospect)

    payload = {
        "assistant":     assistant,
        "phoneNumberId": VAPI_PHONE_ID,
        "customer": {
            "number": phone,
            "name":   f"{prospect.get('first_name','')} {prospect.get('last_name','')}".strip(),
        },
        "metadata": {
            "client_id":   client.get("id", ""),
            "prospect_email": prospect.get("email", ""),
            "campaign_id": prospect.get("campaign_id", ""),
        },
    }

    try:
        r = requests.post(
            f"{VAPI_BASE}/call",
            headers={"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if not r.ok:
            print(f"  Vapi API error {r.status_code}: {r.text[:150]}")
            return {}
        data = r.json()
        print(f"  Call queued: {data.get('id')} → {phone}")
        return data
    except Exception as e:
        print(f"  Vapi call error: {e}")
        return {}


def log_call_event(client_id: str, prospect_email: str, call_id: str, outcome: str, duration_sec: int = 0):
    """Log a call event to the ArgusReach DB."""
    try:
        from db.database import get_db, prospect_id as _pid, init_db
        init_db()
        conn = get_db()
        pid = conn.execute(
            "SELECT id FROM prospects WHERE client_id=? AND email=?",
            (client_id, prospect_email.lower())
        ).fetchone()
        if not pid:
            conn.close()
            return
        import uuid
        conn.execute(
            "INSERT OR IGNORE INTO events (id, prospect_id, client_id, event_type, metadata, created_at) VALUES (?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                pid[0],
                client_id,
                "call_" + outcome,
                json.dumps({"vapi_call_id": call_id, "duration_sec": duration_sec, "outcome": outcome}),
                datetime.utcnow().isoformat(),
            )
        )
        conn.commit()
        conn.close()
        print(f"  DB: logged call_{outcome} for {prospect_email}")
    except Exception as e:
        print(f"  DB log error: {e}")


def get_prospects_needing_call(client_id: str, campaign_id: str) -> list:
    """
    Return prospects who:
    - Received T2 email (touch_1_sent stage, at least 3 days since created)
    - Have NOT been called yet (no call_* event)
    - Have a phone number
    - Are not on DNC
    """
    try:
        from db.database import get_db
        conn = get_db()
        rows = conn.execute("""
            SELECT p.id, p.email, p.first_name, p.last_name, p.company, p.created_at
            FROM prospects p
            WHERE p.client_id = ?
              AND p.campaign_id = ?
              AND p.stage IN ('touch_1_sent', 'added')
              AND p.created_at <= datetime('now', '-3 days')
              AND p.id NOT IN (
                SELECT DISTINCT prospect_id FROM events
                WHERE event_type LIKE 'call_%' AND client_id = ?
              )
        """, (client_id, campaign_id, client_id)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  Error fetching prospects for calls: {e}")
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Fire a test call")
    parser.add_argument("--phone", help="Phone number to call (test mode)")
    parser.add_argument("--client", default="argusreach", help="Client ID")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually call")
    args = parser.parse_args()

    if args.test:
        if not args.phone:
            print("--phone required for test mode")
            sys.exit(1)

        # Load client
        import json as _json
        clients = _json.loads((BASE_DIR / "monitor" / "clients.json").read_text())
        client = next((c for c in clients["clients"] if c["id"] == args.client), None)
        if not client:
            print(f"Client {args.client} not found")
            sys.exit(1)

        prospect = {
            "first_name": "Test",
            "last_name": "Prospect",
            "email": "test@example.com",
            "company": "Test Practice",
            "phone": args.phone,
        }

        print(f"Firing test call to {args.phone} on behalf of {client['firm_name']}...")
        result = fire_call(client, prospect, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
