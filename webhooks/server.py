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


def _log_stripe_revenue(payment_id: str, amount_cents: int, plan: str,
                         customer_email: str, client_id: str, billing_period: str = "monthly"):
    """Write a revenue row to DB. Safe to call from any Stripe event handler."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT OR IGNORE INTO revenue
            (id, client_id, stripe_payment_id, amount_cents, plan, billing_period, customer_email, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), client_id, payment_id,
          amount_cents, plan, billing_period, customer_email, now))
    conn.commit()
    conn.close()


# Map Stripe price amounts (cents) to plan names — fallback when metadata is missing
_PRICE_PLAN_MAP = {
    50000:  "setup_fee",   # $500 setup fee (one-time)
    75000:  "starter",     # $750/mo — 200 prospects
    150000: "growth",      # $1,500/mo — 500 prospects
    250000: "scale",       # $2,500/mo — 1,000 prospects
}


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

    event_type = event.get("type", "")

    # ── First payment via checkout (one-time or first month of subscription) ──
    if event_type == "checkout.session.completed":
        session        = event["data"]["object"]
        amount_cents   = session.get("amount_total", 0)
        customer_email = session.get("customer_details", {}).get("email", "")
        meta           = session.get("metadata", {})
        plan           = meta.get("plan", "") or _PRICE_PLAN_MAP.get(amount_cents, "unknown")
        client_id      = meta.get("client_id", "")

        _log_stripe_revenue(session.get("id", ""), amount_cents, plan,
                            customer_email, client_id)

        amount_fmt = f"${amount_cents/100:.2f}"
        telegram_notify(
            f"💰 <b>New payment!</b>\n"
            f"Plan: {plan} — {amount_fmt}\n"
            f"Email: {customer_email}"
        )
        print(f"✅ Checkout payment logged: {plan} {amount_fmt} from {customer_email}")

        # Auto-set payment_confirmed gate and check all gates
        if client_id:
            try:
                clients = _load_clients()
                for c in clients:
                    if c.get("id") == client_id:
                        c.setdefault("checklist", {})["payment_confirmed"] = True
                        _save_clients(clients)
                        print(f"✅ payment_confirmed gate set for {client_id}")
                        _check_all_gates_webhook(client_id, clients)
                        break
            except Exception as _ge:
                print(f"payment gate update failed (non-fatal): {_ge}")

    # ── Recurring monthly charge (subscription renewal) ──
    elif event_type == "invoice.paid":
        invoice        = event["data"]["object"]
        # Skip $0 invoices (trials, etc.)
        amount_cents   = invoice.get("amount_paid", 0)
        if amount_cents == 0:
            return jsonify({"status": "ok"})

        customer_email = invoice.get("customer_email", "")
        invoice_id     = invoice.get("id", "")
        sub_id         = invoice.get("subscription", "")

        # Derive plan from line items
        lines      = invoice.get("lines", {}).get("data", [])
        plan       = "unknown"
        client_id  = ""
        for line in lines:
            price_amt = line.get("amount", 0)
            plan      = _PRICE_PLAN_MAP.get(price_amt, plan)
            meta      = line.get("metadata", {})
            client_id = meta.get("client_id", client_id)

        _log_stripe_revenue(invoice_id, amount_cents, plan,
                            customer_email, client_id, "monthly_renewal")

        amount_fmt = f"${amount_cents/100:.2f}"
        telegram_notify(
            f"🔄 <b>Subscription renewed!</b>\n"
            f"Plan: {plan} — {amount_fmt}\n"
            f"Email: {customer_email}\n"
            f"Sub: <code>{sub_id}</code>"
        )
        print(f"✅ Renewal logged: {plan} {amount_fmt} from {customer_email}")

        # Auto-set payment_confirmed gate and check all gates
        if client_id:
            try:
                clients = _load_clients()
                for c in clients:
                    if c.get("id") == client_id:
                        c.setdefault("checklist", {})["payment_confirmed"] = True
                        _save_clients(clients)
                        print(f"✅ payment_confirmed gate set for {client_id}")
                        _check_all_gates_webhook(client_id, clients)
                        break
            except Exception as _ge:
                print(f"payment gate update failed (non-fatal): {_ge}")

    # ── Payment failed — alert Vito + auto-pause campaign after 2nd failed attempt ──
    elif event_type == "invoice.payment_failed":
        invoice        = event["data"]["object"]
        customer_email = invoice.get("customer_email", "")
        amount_cents   = invoice.get("amount_due", 0)
        sub_id         = invoice.get("subscription", "")
        attempt        = invoice.get("attempt_count", 1)

        # Auto-pause campaign on 2nd+ failed attempt — don't work for free
        paused_firm = ""
        if attempt >= 2:
            try:
                clients = _load_clients()
                for c in clients:
                    # Match ONLY by client_email (the billing contact) — never by outreach_email
                    # (outreach_email is our Gmail account, not the client's billing address)
                    if c.get("client_email", "").lower() == customer_email.lower():
                        c["active"] = False
                        paused_firm = c.get("firm_name", c["id"])
                        _save_clients(clients)
                        # Mirror to DB so clients table stays in sync
                        try:
                            sys.path.insert(0, str(BASE_DIR))
                            from argusreach.db.database import sync_client_from_config
                            sync_client_from_config(c)
                        except Exception as _dbe:
                            print(f"DB sync after auto-pause failed (non-fatal): {_dbe}")
                        print(f"⛔ Auto-paused {paused_firm} — payment failed attempt {attempt}")
                        break
            except Exception as e:
                print(f"Auto-pause failed (non-fatal): {e}")

        pause_note = f"\n⛔ <b>Campaign auto-paused</b> — {paused_firm}" if paused_firm else \
                     "\nStripe will retry. Campaign still active."

        telegram_notify(
            f"⚠️ <b>Payment failed!</b>\n"
            f"Email: {customer_email}\n"
            f"Amount due: ${amount_cents/100:.2f}\n"
            f"Attempt #{attempt} — Sub: <code>{sub_id}</code>\n"
            f"Reach out to client immediately.{pause_note}"
        )
        print(f"⚠️ Payment failed: {customer_email} ${amount_cents/100:.2f} (attempt {attempt})")

    # ── Subscription cancelled ──
    elif event_type == "customer.subscription.deleted":
        sub            = event["data"]["object"]
        customer_email = sub.get("customer_email", "")
        sub_id         = sub.get("id", "")
        cancel_reason  = sub.get("cancellation_details", {}).get("reason", "unknown")

        telegram_notify(
            f"❌ <b>Subscription cancelled</b>\n"
            f"Email: {customer_email}\n"
            f"Sub: <code>{sub_id}</code>\n"
            f"Reason: {cancel_reason}\n"
            f"Pause monitor and follow up with client."
        )
        print(f"❌ Subscription cancelled: {customer_email}")

    return jsonify({"status": "ok"})


CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
CALENDLY_WEBHOOK_SIGNING_KEY = os.environ.get("CALENDLY_WEBHOOK_SIGNING_KEY", "")

_GATES = ("icp_reviewed", "dns_verified", "warmup_complete", "payment_confirmed",
          "sequence_approved", "calendar_connected")


def _load_clients():
    try:
        return json.loads(CLIENTS_FILE.read_text()).get("clients", [])
    except Exception:
        return []


def _save_clients(clients: list):
    CLIENTS_FILE.write_text(json.dumps({"clients": clients}, indent=2))


def _check_all_gates_webhook(client_id: str, clients: list):
    """Check all 6 pre-launch gates for client_id. Fire Telegram alert if all green (once)."""
    client = next((c for c in clients if c.get("id") == client_id), None)
    if not client:
        return
    checklist = client.get("checklist", {})
    all_green = all(checklist.get(g) for g in _GATES)
    if all_green:
        client["onboarding_status"] = "ready_to_launch"
        if not client.get("launch_ready_alerted"):
            firm = client.get("firm_name", client_id)
            telegram_notify(
                f"🚀 {firm} is ready to launch!\n\n"
                f"All 6 gates are green. Head to the portal to send the ready-to-launch email.\n"
                f"https://admin.argusreach.com/clients/{client_id}"
            )
            client["launch_ready_alerted"] = True
            print(f"✅ All-gates Telegram alert fired for {client_id}")
        _save_clients(clients)
    elif not all_green and client.get("launch_ready_alerted") is not False:
        if not client.get("active") and client.get("onboarding_status") == "ready_to_launch":
            client["onboarding_status"] = "warming_up"
        client["launch_ready_alerted"] = False
        _save_clients(clients)


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
        # Identify client first so we don't cancel meetings across clients
        cancel_client_id, _ = _identify_client_from_calendly(
            event_type_name, event_type_slug, invitee_email
        )
        conn = get_db()
        if cancel_client_id:
            conn.execute(
                "UPDATE meetings SET status='cancelled' WHERE prospect_email=? AND client_id=? AND status='confirmed'",
                (invitee_email, cancel_client_id)
            )
        else:
            conn.execute(
                "UPDATE meetings SET status='cancelled' WHERE prospect_email=? AND status='confirmed'",
                (invitee_email,)
            )
        conn.commit()

        # Update prospect stage back — filter by client_id when available
        if cancel_client_id:
            prospect_row = conn.execute(
                "SELECT id FROM prospects WHERE email=? AND client_id=? LIMIT 1",
                (invitee_email, cancel_client_id)
            ).fetchone()
        else:
            prospect_row = conn.execute(
                "SELECT id, client_id FROM prospects WHERE email=? LIMIT 1",
                (invitee_email,)
            ).fetchone()

        if prospect_row:
            update_prospect_stage(prospect_row["id"], "replied_by_us")
            log_event(cancel_client_id or prospect_row.get("client_id", ""), prospect_row["id"], "meeting_cancelled", {
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


# ── VAPI WEBHOOK ─────────────────────────────────────────────────────────────

@app.route("/webhooks/vapi", methods=["POST"])
def vapi_webhook():
    """Handle Vapi call lifecycle events and trigger follow-up emails."""
    import hmac, hashlib
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    # Optional webhook secret verification
    vapi_secret = os.environ.get("VAPI_WEBHOOK_SECRET", "")
    if vapi_secret:
        sig = request.headers.get("x-vapi-signature", "")
        expected = hmac.new(vapi_secret.encode(), request.data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return jsonify({"error": "Invalid signature"}), 401

    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    event_type   = data.get("message", {}).get("type") or data.get("type", "")
    call         = data.get("message", {}).get("call") or data.get("call") or {}
    call_id      = call.get("id", "")
    metadata     = call.get("metadata") or {}
    client_id    = metadata.get("client_id", "")
    prospect_email = metadata.get("prospect_email", "")
    duration_sec = int(call.get("endedAt", 0) and call.get("startedAt", 0) and 0) or 0

    # Try to get duration from seconds
    try:
        from datetime import datetime as _dt
        started = call.get("startedAt", "")
        ended   = call.get("endedAt", "")
        if started and ended:
            duration_sec = int((_dt.fromisoformat(ended.replace("Z","+00:00")) -
                               _dt.fromisoformat(started.replace("Z","+00:00"))).total_seconds())
    except Exception:
        pass

    print(f"[Vapi] event={event_type} call={call_id[:8] if call_id else '?'} client={client_id} prospect={prospect_email}")

    # Only process end-of-call events
    if event_type not in ("end-of-call-report", "call-ended", "end_of_call_report"):
        return jsonify({"status": "ok"})

    # Determine outcome
    end_reason  = call.get("endedReason", "").lower()
    transcript  = data.get("message", {}).get("transcript", "") or ""

    # Classify outcome
    if "voicemail" in end_reason or "no-answer" in end_reason or "no_answer" in end_reason:
        outcome = "voicemail"
    elif any(w in transcript.lower() for w in ["not interested", "remove", "unsubscribe", "stop calling", "take me off"]):
        outcome = "not_interested"
    elif any(w in transcript.lower() for w in ["yes", "sure", "sounds good", "book", "calendar", "schedule", "interested", "tell me more"]):
        outcome = "interested"
    elif duration_sec > 15:
        outcome = "answered"
    else:
        outcome = "no_answer"

    print(f"[Vapi] outcome={outcome} duration={duration_sec}s")

    # Log to DB
    if client_id and prospect_email:
        try:
            sys.path.insert(0, str(BASE_DIR))
            conn = get_db()
            pid_row = conn.execute(
                "SELECT id FROM prospects WHERE client_id=? AND email=?",
                (client_id, prospect_email.lower())
            ).fetchone()
            if pid_row:
                conn.execute(
                    "INSERT OR IGNORE INTO events (id, prospect_id, client_id, event_type, metadata, created_at) VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), pid_row[0], client_id, f"call_{outcome}",
                     json.dumps({"vapi_call_id": call_id, "duration_sec": duration_sec, "outcome": outcome, "end_reason": end_reason}),
                     datetime.utcnow().isoformat())
                )
                conn.commit()
                # Update stage if interested
                if outcome == "interested":
                    update_prospect_stage(pid_row[0], "replied")
            conn.close()
        except Exception as e:
            print(f"[Vapi] DB log error: {e}")

    # Write to DNC if prospect opted out on the call
    if outcome == "not_interested" and client_id and prospect_email:
        try:
            dnc_global = BASE_DIR / "monitor" / "dnc" / "global.txt"
            dnc_client = BASE_DIR / "monitor" / "dnc" / f"{client_id}.txt"
            for dnc_file in [dnc_global, dnc_client]:
                dnc_file.parent.mkdir(parents=True, exist_ok=True)
                existing = set(dnc_file.read_text().splitlines()) if dnc_file.exists() else set()
                if prospect_email.lower() not in existing:
                    with open(dnc_file, "a") as f:
                        f.write(prospect_email.lower() + "\n")
            print(f"[Vapi] DNC: {prospect_email} added to global + client DNC")
        except Exception as e:
            print(f"[Vapi] DNC write error: {e}")

    # Send follow-up email for answered calls (interested or general answer)
    if outcome in ("answered", "interested", "voicemail") and client_id and prospect_email:
        try:
            import json as _json
            clients_data = _json.loads((BASE_DIR / "monitor" / "clients.json").read_text())
            client = next((c for c in clients_data["clients"] if c["id"] == client_id), None)
            if client:
                _send_vapi_followup_email(client, prospect_email, outcome)
        except Exception as e:
            print(f"[Vapi] Follow-up email error: {e}")

    # Telegram alert for interested calls
    if outcome == "interested":
        try:
            import json as _json
            clients_data = _json.loads((BASE_DIR / "monitor" / "clients.json").read_text())
            client = next((c for c in clients_data["clients"] if c["id"] == client_id), None)
            firm = client["firm_name"] if client else client_id
            telegram_notify(
                f"📞 <b>Call - Interested!</b>\n"
                f"👤 {prospect_email}\n"
                f"🏢 {firm}\n"
                f"⏱ {duration_sec}s\n"
                f"Follow-up email sent with Calendly link."
            )
        except Exception as e:
            print(f"[Vapi] Telegram alert error: {e}")

    return jsonify({"status": "ok", "outcome": outcome})


def _send_vapi_followup_email(client: dict, prospect_email: str, outcome: str):
    """Send a follow-up email after a Vapi call."""
    from_email   = "vito@argusreach.com"
    app_password = os.environ.get("ARGUSREACH_GMAIL_APP_PASS", "")
    if not app_password:
        print("[Vapi] No ARGUSREACH_GMAIL_APP_PASS - skipping follow-up email")
        return

    sender_name  = client.get("sender_name", "Vito Resciniti")
    firm_name    = client.get("firm_name", "ArgusReach")
    calendly     = client.get("calendly_link", "https://calendly.com/vito-argusreach/30min")
    client_email = client.get("outreach_email") or from_email

    if outcome == "voicemail":
        subject = f"Quick note from {firm_name}"
        body = f"""Hi,

I just left you a brief voicemail - wanted to make sure you had my contact info.

I'm reaching out on behalf of {firm_name} regarding a potential referral partnership. They'd love 15 minutes to walk you through how they help practices like yours.

Here's their calendar if you'd like to find a time: {calendly}

No pressure at all - happy to connect whenever works for you.

{sender_name}
{firm_name}"""
    else:
        subject = f"Great connecting - {firm_name}"
        body = f"""Hi,

Thanks for taking a moment on the phone.

As mentioned, {firm_name} would love to set up a quick 15-minute call to show you exactly how they can help. Here's the calendar link to grab whatever time works best:

{calendly}

Looking forward to connecting.

{sender_name}
{firm_name}"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{sender_name} <{from_email}>"
        msg["To"]      = prospect_email
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(from_email, app_password)
            smtp.sendmail(from_email, prospect_email, msg.as_string())
        print(f"[Vapi] Follow-up email sent to {prospect_email} (outcome: {outcome})")
    except Exception as e:
        print(f"[Vapi] Email send error: {e}")


if __name__ == "__main__":
    init_db()
    print("🚀 ArgusReach webhook server starting on port 5055...")
    app.run(host="0.0.0.0", port=5055, debug=False)
