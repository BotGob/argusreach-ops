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


@app.route("/webhooks/calendly", methods=["POST"])
def calendly_webhook():
    try:
        data       = request.get_json(force=True)
        event_type = data.get("event", "")
        payload    = data.get("payload", {})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    invitee        = payload.get("invitee", {})
    invitee_email  = invitee.get("email", "").lower()
    event_name     = payload.get("event_type", {}).get("name", "")
    start_time     = payload.get("scheduled_event", {}).get("start_time", "")

    if event_type == "invitee.created":
        # Look up prospect across all clients
        conn = get_db()
        rows = conn.execute(
            "SELECT id, client_id FROM prospects WHERE email = ? LIMIT 5",
            (invitee_email,)
        ).fetchall()
        conn.close()

        now = datetime.utcnow().isoformat()
        conn = get_db()
        for row in rows:
            pid       = row["id"]
            client_id = row["client_id"]
            meeting_id = str(uuid.uuid4())
            conn.execute("""
                INSERT OR IGNORE INTO meetings
                    (id, prospect_id, client_id, scheduled_at, status, source, invitee_email, created_at)
                VALUES (?, ?, ?, ?, 'scheduled', 'calendly', ?, ?)
            """, (meeting_id, pid, client_id, start_time, invitee_email, now))
            update_prospect_stage(pid, "meeting_booked")
            log_event(client_id, pid, "meeting_booked", {
                "event_name": event_name,
                "start_time": start_time,
                "source": "calendly"
            })
        conn.commit()
        conn.close()

        telegram_notify(
            f"📅 <b>Meeting booked!</b>\n{invitee_email}\n{event_name}\n{start_time}"
        )
        print(f"✅ Meeting booked: {invitee_email} @ {start_time}")

    elif event_type == "invitee.canceled":
        conn = get_db()
        conn.execute(
            "UPDATE meetings SET status='cancelled' WHERE invitee_email=? AND status='scheduled'",
            (invitee_email,)
        )
        conn.commit()
        conn.close()
        print(f"❌ Meeting cancelled: {invitee_email}")

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    print("🚀 ArgusReach webhook server starting on port 5055...")
    app.run(host="0.0.0.0", port=5055, debug=False)
