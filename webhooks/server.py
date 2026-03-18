#!/usr/bin/env python3
"""
ArgusReach — Webhook Server (port 5055)
Handles Stripe payment events and Calendly booking events.
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")
sys.path.insert(0, str(BASE_DIR.parent))

from argusreach.db.database import get_db, init_db, log_event, update_prospect_stage, prospect_id

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TELEGRAM_BOT_TOKEN    = os.environ.get("ARGUSREACH_BOT_TOKEN", "")
TELEGRAM_CHAT_ID      = os.environ.get("ARGUSREACH_CHAT_ID", "")

app = Flask(__name__)


def telegram_notify(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram notify failed: {e}")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.utcnow().isoformat()})


@app.route("/health/monitor")
def monitor_health():
    """Check if the monitor is alive by reading its heartbeat file."""
    heartbeat_file = BASE_DIR / "monitor" / "logs" / "monitor_heartbeat.txt"
    max_stale_minutes = 35  # monitor runs every 10 min; alert if silent for 35+

    if not heartbeat_file.exists():
        return jsonify({"status": "unknown", "reason": "No heartbeat file found — monitor may not have run yet"}), 503

    try:
        last_beat = datetime.fromisoformat(heartbeat_file.read_text().strip())
        age_minutes = (datetime.utcnow() - last_beat).total_seconds() / 60
        if age_minutes > max_stale_minutes:
            return jsonify({
                "status": "stale",
                "last_beat": last_beat.isoformat(),
                "age_minutes": round(age_minutes, 1),
                "reason": f"Monitor last cycled {age_minutes:.0f} min ago — may be stuck or crashed"
            }), 503
        return jsonify({
            "status": "alive",
            "last_beat": last_beat.isoformat(),
            "age_minutes": round(age_minutes, 1)
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/webhooks/stripe", methods=["POST"])
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except Exception as e:
        print(f"Stripe webhook error: {e}")
        return jsonify({"error": str(e)}), 400

    if event.get("type") == "checkout.session.completed":
        session        = event["data"]["object"]
        amount_cents   = session.get("amount_total", 0)
        customer_email = session.get("customer_details", {}).get("email", "")
        meta           = session.get("metadata", {})
        plan           = meta.get("plan", "unknown")
        client_id      = meta.get("client_id", "")

        now = datetime.utcnow().isoformat()
        conn = get_db()
        conn.execute("""
            INSERT OR IGNORE INTO revenue
                (id, client_id, stripe_payment_id, amount_cents, plan, billing_period, customer_email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), client_id, session.get("id", ""),
              amount_cents, plan, "monthly", customer_email, now))
        conn.commit()
        conn.close()

        amount_fmt = f"${amount_cents/100:.2f}"
        telegram_notify(f"💰 <b>New payment!</b>\n{plan} — {amount_fmt}\n{customer_email}")
        print(f"✅ Payment logged: {plan} {amount_fmt} from {customer_email}")

    return jsonify({"status": "ok"})


CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
CALENDLY_WEBHOOK_SIGNING_KEY = os.environ.get("CALENDLY_WEBHOOK_SIGNING_KEY", "")


def _load_clients():
    try:
        return json.loads(CLIENTS_FILE.read_text()).get("clients", [])
    except Exception:
        return []


def _identify_client_from_calendly(event_type_name: str, event_type_slug: str, invitee_email: str):
    """
    Identify which ArgusReach client this booking belongs to.

    Priority:
    1. Match by calendly_event_slug in clients.json (most reliable — set during onboarding)
    2. Match by calendly_event_name containing firm_name
    3. Fall back to prospect email lookup in DB (catches ArgusReach own sales calls)
    Returns (client_id, firm_name) or (None, None)
    """
    clients = _load_clients()

    # 1. Match by explicit slug stored in clients.json
    for c in clients:
        if c.get("calendly_event_slug") and c["calendly_event_slug"] == event_type_slug:
            return c["id"], c.get("firm_name", c["id"])

    # 2. Match by event type name containing firm_name
    for c in clients:
        firm = c.get("firm_name", "")
        if firm and firm.lower() in event_type_name.lower():
            return c["id"], firm

    # 3. Fall back to DB prospect lookup (covers ArgusReach own sales / unknown)
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT client_id FROM prospects WHERE email=? LIMIT 1",
            (invitee_email.lower(),)
        ).fetchone()
        conn.close()
        if row:
            client_id = row["client_id"]
            for c in clients:
                if c["id"] == client_id:
                    return client_id, c.get("firm_name", client_id)
            return client_id, client_id
    except Exception:
        pass

    return None, None


def _format_meeting_time(iso_time: str) -> str:
    """Format ISO timestamp to readable ET time."""
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        # Format nicely
        return dt.strftime("%a %b %-d at %-I:%M %p UTC")
    except Exception:
        return iso_time


@app.route("/webhooks/calendly", methods=["POST"])
def calendly_webhook():
    """
    Handle Calendly webhook events.
    Maps bookings to ArgusReach clients via event type slug/name.
    Updates DB: meetings table, prospect stage, events log.
    Alerts Vito via Telegram with full context.
    """
    # Optional signature verification
    if CALENDLY_WEBHOOK_SIGNING_KEY:
        import hmac, hashlib
        sig = request.headers.get("Calendly-Webhook-Signature", "")
        body = request.get_data()
        expected = hmac.new(
            CALENDLY_WEBHOOK_SIGNING_KEY.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(f"sha256={expected}", sig):
            print("Calendly signature mismatch")
            return jsonify({"error": "invalid signature"}), 401

    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    event_kind = data.get("event", "")
    payload    = data.get("payload", {})

    # Extract all fields from Calendly payload
    invitee         = payload.get("invitee", {})
    invitee_email   = invitee.get("email", "").lower().strip()
    invitee_name    = invitee.get("name", "")
    cancel_url      = invitee.get("cancel_url", "")
    reschedule_url  = invitee.get("reschedule_url", "")

    evt             = payload.get("event", {})
    start_time      = evt.get("start_time", "")
    end_time        = evt.get("end_time", "")
    location        = evt.get("location", {}).get("join_url") or evt.get("location", {}).get("location", "")

    event_type_info = payload.get("event_type", {})
    event_type_name = event_type_info.get("name", "")
    event_type_slug = event_type_info.get("slug", "")

    now = datetime.utcnow().isoformat()

    if event_kind == "invitee.created":
        client_id, firm_name = _identify_client_from_calendly(
            event_type_name, event_type_slug, invitee_email
        )

        # Look up prospect in DB
        conn = get_db()
        prospect_row = conn.execute(
            "SELECT id FROM prospects WHERE client_id=? AND email=?",
            (client_id or "", invitee_email)
        ).fetchone() if client_id else None

        if not prospect_row:
            # Try without client_id constraint (covers ArgusReach own sales)
            prospect_row = conn.execute(
                "SELECT id, client_id FROM prospects WHERE email=? LIMIT 1",
                (invitee_email,)
            ).fetchone()
            if prospect_row and not client_id:
                client_id = prospect_row["client_id"] if "client_id" in prospect_row.keys() else client_id

        pid = prospect_row["id"] if prospect_row else None
        conn.close()

        # Log meeting to DB
        import hashlib as _hl
        meeting_id = _hl.md5(f"{invitee_email}:{start_time}:{client_id}".encode()).hexdigest()[:16]
        conn = get_db()
        conn.execute("""
            INSERT OR REPLACE INTO meetings
                (id, client_id, prospect_id, prospect_email, prospect_name,
                 meeting_date, scheduled_at, status, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', 'calendly', ?)
        """, (meeting_id, client_id or "argusreach_sales", pid,
              invitee_email, invitee_name,
              start_time[:10], start_time, now))

        # Update prospect stage + log event
        if pid:
            update_prospect_stage(pid, "meeting_booked")
            log_event(client_id or "argusreach_sales", pid, "meeting_booked", {
                "event_type": event_type_name,
                "start_time": start_time,
                "location":   location,
                "source":     "calendly",
                "meeting_id": meeting_id,
            })

        conn.commit()
        conn.close()

        # Build Telegram alert
        time_str  = _format_meeting_time(start_time)
        client_label = f"{firm_name}" if firm_name else "ArgusReach (sales call)"
        loc_line  = f"\n📍 {location}" if location else ""
        prospect_label = invitee_name or invitee_email

        telegram_notify(
            f"📅 <b>Meeting Booked!</b>\n"
            f"👤 {prospect_label} — <code>{invitee_email}</code>\n"
            f"🏢 Client: {client_label}\n"
            f"🕐 {time_str}{loc_line}\n"
            f"{'✅ Prospect record updated' if pid else '⚠️ Prospect not found in DB — log manually'}"
        )
        print(f"✅ Meeting booked: {invitee_email} @ {start_time} → client: {client_id}")

    elif event_kind == "invitee.canceled":
        conn = get_db()
        conn.execute(
            "UPDATE meetings SET status='cancelled' WHERE prospect_email=? AND status='confirmed'",
            (invitee_email,)
        )
        conn.commit()

        # Update prospect stage back
        prospect_row = conn.execute(
            "SELECT id, client_id FROM prospects WHERE email=? LIMIT 1",
            (invitee_email,)
        ).fetchone()
        if prospect_row:
            update_prospect_stage(prospect_row["id"], "replied_by_us")
            log_event(prospect_row["client_id"], prospect_row["id"], "meeting_cancelled", {
                "source": "calendly"
            })
        conn.close()

        telegram_notify(
            f"❌ <b>Meeting Cancelled</b>\n"
            f"👤 {invitee_name or invitee_email}\n"
            f"🕐 Was: {_format_meeting_time(start_time)}\n"
            f"Prospect stage reset to replied_by_us."
        )
        print(f"❌ Meeting cancelled: {invitee_email}")

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    print("🚀 ArgusReach webhook server starting on port 5055...")
    app.run(host="0.0.0.0", port=5055, debug=False)
