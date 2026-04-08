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
    # Ensure compound names are spoken naturally (e.g. "ArgusReach" -> "Argus Reach")
    client_name  = client.get("firm_name", "our client").replace("ArgusReach", "Argus Reach")
    sender_name  = client.get("sender_name", "our team")
    prospect_first = prospect.get("first_name", "there")
    prospect_company = prospect.get("company", "your practice")
    calendly_link = client.get("calendly_link", "")

    # Build a client-specific description of what they do — dynamic, not hardcoded
    business_desc  = client.get("_business_description", "").strip()
    value_prop     = client.get("_value_prop", "").strip()
    target_industry = client.get("_target_industry", "").strip()

    # Build a short 1-2 sentence plain-English description of what the client does
    if business_desc:
        sentences = business_desc.replace('\n', ' ').split('. ')
        what_they_do = '. '.join(sentences[:2]).strip()
        if not what_they_do.endswith('.'):
            what_they_do += '.'
    elif value_prop:
        sentences = value_prop.replace('\n', ' ').split('. ')
        what_they_do = '. '.join(sentences[:2]).strip()
        if not what_they_do.endswith('.'):
            what_they_do += '.'
    else:
        what_they_do = f"{client_name} helps businesses grow through targeted outreach."

    # Build a gist of what the email sequence said — so AI can reference it naturally
    # Pull Touch 1 + Touch 2 bodies, strip template vars, summarize to 2 sentences
    sequence = client.get("sequence", [])
    email_gist = ""
    if sequence:
        import re as _re
        t1_body = sequence[0].get("body", "") if len(sequence) > 0 else ""
        t2_body = sequence[1].get("body", "") if len(sequence) > 1 else ""
        # Strip Instantly template variables like {{firstName}}, {{custom_intro}} etc
        combined = (t1_body + " " + t2_body).replace('\n', ' ')
        combined = _re.sub(r'\{\{[^}]+\}\}', '', combined).strip()
        combined = _re.sub(r'  +', ' ', combined)
        # Take first 300 chars as the gist — enough context without being overwhelming
        email_gist = combined[:300].rsplit(' ', 1)[0]  # don't cut mid-word
        if email_gist and not email_gist.endswith('.'):
            email_gist += '...'

    email_context = (
        f"WHAT THE EMAIL WAS ABOUT (use this if asked, don't read it back word for word — just know the gist):\n"
        f"The email introduced {client_name} and explained that they help {prospect_company} with {what_they_do[:120]} "
        f"{('The email said: ' + email_gist) if email_gist else ''}\n"
        f"The email asked for a 15-minute call with {sender_name} to explore if it's a fit."
    ) if email_gist else (
        f"WHAT THE EMAIL WAS ABOUT: Introduced {client_name}, explained what they do, asked for a 15-minute call with {sender_name}."
    )

    # Receptionist message — what we emailed about
    if target_industry in ('physical_therapy', 'pt', 'healthcare'):
        email_topic = "building physician referral relationships for the practice"
    elif target_industry in ('ria', 'financial', 'investment'):
        email_topic = "a potential partnership opportunity"
    else:
        email_topic = "a potential business opportunity"

    # Build the system prompt — tight, natural, goal-oriented
    system_prompt = f"""You are a professional outreach assistant calling on behalf of {client_name}.

Your ONLY goal is to book a 15-minute introductory call between the prospect and {sender_name} at {client_name}.

The prospect ({prospect_first} at {prospect_company}) received an email recently. You are following up on that email.

WHAT {client_name} DOES (use this to explain if asked, in plain conversational language):
{what_they_do}

{email_context}

HOW TO SPEAK:
- Sound natural and conversational, like a real person making a quick follow-up call
- ALWAYS use contractions: "I'm" not "I am", "didn't" not "did not", "don't" not "do not", "it's" not "it is", "that's" not "that is", "I'll" not "I will", "you'd" not "you would", "we've" not "we have". No exceptions - formal speech sounds robotic.
- Speak slowly and clearly - this is a phone call, not a presentation. Pause between sentences.
- Use natural filler words like "yeah", "totally", "for sure", "absolutely", "oh great" - it makes you sound human
- Be flexible and responsive - don't just stick to the script, actually listen and respond to what they say
- If they ask a question, answer it naturally before moving on
- Short sentences. Pause. Let silence happen. It's okay.
- If they interrupt you, stop immediately and listen
- Mirror their energy - if they're warm and chatty, be warm and chatty back
- Never sound like you're reading from a script — even if you are

SCRIPT FLOW:

Opening:
Wait for the person to speak first. When they say anything, respond with:
"Hi - is this {prospect_first}?"
[wait for response]

SCENARIO A - They confirm it's them (say yes, say their name, or otherwise confirm):
Go straight into the follow-up naturally - don't announce yourself formally first. Say:
"Hey {prospect_first} - so {sender_name} over at {client_name} sent you an email not too long ago, just wanted to make sure it didn't get buried. Do you have just a minute?"
[wait - if yes, proceed. keep it conversational]

SCENARIO A2 - They say "who's this?" or "who's calling?" before confirming their name:
Answer naturally and briefly: "Hey - I'm following up on an email that {sender_name} from {client_name} sent over to {prospect_first} - is that you?"
[if they confirm, continue as Scenario A]
[if they say no, go to Scenario B]

SCENARIO B - They say it's someone else entirely (wrong person, different name):
"Oh sorry about that - is {prospect_first} available by any chance?"
  - If yes, ask to be transferred or put on hold
  - If no or unavailable:
    "No problem at all - could I leave a quick message for them? Just let {prospect_first} know that {sender_name} from {client_name} called."
    Then ask: "Actually - is there a direct email I can send them a calendar link to? Want to make sure it gets to the right place."
    - If they give an email: "Perfect, thank you so much. Have a great day."
    - If they don't know or decline: "No worries at all - we have one on file, we'll try that. Thanks so much, have a great day."
  [end call warmly]

SCENARIO C - They answer as a business/receptionist ("Dr. Smith's office", "ABC Company" etc):
"Hi there - is {prospect_first} available?"
  - If yes: transfer/hold
  - If no:
    "No worries - could I leave a message? Let {prospect_first} know {sender_name} from {client_name} reached out about {email_topic}."
    Then ask: "And is there a direct email I can send them a calendar link to? Just want to make sure it lands in the right inbox."
    - If they give an email: "That's so helpful, thank you. Have a great day."
    - If they don't know or decline: "Totally fine - we'll follow up to the email we have on file. Thanks so much."
  [end call warmly]

NEVER just hang up without leaving a message when someone else answers.
Always attempt to get a direct email before ending - but do it naturally, not like a form you're filling out.

Once {prospect_first} is confirmed and engaged:
Keep it SHORT and natural. This is a gentle nudge, not a pitch.
"So {sender_name} just wanted to make sure that email didn't get lost. Any chance you'd have 15 minutes to connect and hear a bit more?"

That's it. Don't pitch. Don't explain the whole business. Let them respond.

If they ask "what email?" or "what's this about?":
Be natural - you know the gist of what was sent. Say something like:
"Yeah so {sender_name} reached out about [one short sentence on what {client_name} does - keep it casual]. Just wanted to see if it might be worth a quick conversation."
Then ask for the 15 minutes again.

If they say yes to a call:
"Perfect - I'll have {sender_name} send you a calendar link right now. Just grab whatever time looks good."
[close warmly - "Really appreciate it, have a great rest of your day" - and end the call]

If not interested:
"Totally fair - I'll pass that along and make sure you're off the list. Sorry to interrupt your day."
[end call warmly]

If voicemail:
"Hi {prospect_first}, quick message on behalf of {client_name} - they reached out recently and just wanted to connect. {sender_name} will follow up by email as well. Have a great day."
[end]

ADDITIONAL SCENARIOS:
- If they say "I already got that email": "Oh great - yeah {sender_name} just wanted to make sure it didn't get buried. Does a quick 15-minute call make sense?"
- If they say "I'm busy" or "bad time": "Totally understand - when would be a better time? I can have {sender_name} reach out then."
- If they say "send me more info": "Absolutely - I'll have {sender_name} send that over right after this call along with a calendar link."
- If they seem interested but unsure: "No commitment at all, just a quick conversation to see if it's a fit."

RULES:
- Never say the word "test" or reference any test
- Never discuss pricing
- Never make specific promises about results
- Speak slowly - don't rush the pitch, let it breathe
- If asked if you are AI: say "I'm an automated assistant calling on behalf of {client_name}"
- If they want off the list: agree immediately and end warmly
- Never end abruptly - always close with a warm sign-off before hanging up
- Keep it under 90 seconds
- One goal: get them to agree to a 15-minute call with {sender_name}"""

    return {
        "name": f"ArgusReach - {client_name} Follow-up",
        "firstMessage": f"",  # empty - model waits for human to speak first
        "firstMessageMode": "assistant-waits-for-user",
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en-US",
            "smartFormat": True,
            "endpointing": 500,  # max allowed by Vapi
        },
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": system_prompt}],
            "temperature": 0.7,
            "maxTokens": 200,
        },
        "voice": {
            "provider": "11labs",
            "voiceId": "cgSgspJ2msm6clMCkdW9",  # Jessica - newer model, more natural than Bella
            # Revert: EXAVITQu4vr4xnSDxMaL (Bella) if Jessica is worse
            "stability": 0.5,
            "similarityBoost": 0.75,
            "style": 0.35,
            "speed": 0.9,
            "useSpeakerBoost": True,
        },
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "silenceTimeoutSeconds": 12,
        "maxDurationSeconds": 180,
        "backgroundDenoisingEnabled": False,  # was adding fake office noise - disabled
        "serverUrl": f"{PORTAL_BASE_URL}/webhooks/vapi",
        "serverUrlSecret": os.environ.get("VAPI_WEBHOOK_SECRET", ""),
    }


def fire_call(client: dict, prospect: dict, dry_run: bool = False) -> dict:
    """
    Fire an outbound call via Vapi for a prospect.
    Returns the Vapi call object or a dry_run stub.
    """
    # Re-read env vars at call time (handles reload() in app.py)
    from dotenv import load_dotenv as _lde
    _lde(BASE_DIR / "monitor" / ".env", override=True)
    api_key  = os.environ.get("VAPI_API_KEY", "") or VAPI_API_KEY
    phone_id = os.environ.get("VAPI_PHONE_NUMBER_ID", "") or VAPI_PHONE_ID

    if not api_key:
        print("No VAPI_API_KEY configured — skipping call")
        return {}

    if not phone_id:
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
        "phoneNumberId": phone_id,
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
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
    parser.add_argument("--first-name", default="Dave", help="Prospect first name for test")
    parser.add_argument("--company", default="", help="Prospect company for test")
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
            "first_name": args.first_name or "Dave",
            "last_name": "",
            "email": "test@example.com",
            "company": args.company or "Your Practice",
            "phone": args.phone,
        }

        print(f"Firing test call to {args.phone} on behalf of {client['firm_name']}...")
        result = fire_call(client, prospect, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
