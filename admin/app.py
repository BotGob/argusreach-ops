#!/usr/bin/env python3
"""
ArgusReach — Admin Portal (port 5056)
Internal-only. Password protected. Vito's control panel.

Routes:
  GET  /              → dashboard
  GET  /clients       → all clients
  GET  /clients/new   → intake form
  POST /clients/new   → submit intake → creates client record
  GET  /clients/<id>  → client detail
  POST /clients/<id>/dnc     → upload DNC list CSV
  POST /clients/<id>/leads   → upload + prep prospect list
  GET  /campaigns     → live campaign status
  GET  /leads/<id>    → download cleaned lead list for client
"""

import csv
import io
import json
import os
import sys
import hashlib
import re
from datetime import datetime
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import (Flask, Response, flash, redirect, render_template,
                   request, send_file, session, url_for)

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / "monitor" / ".env")
sys.path.insert(0, str(BASE_DIR))

from db.database import get_db, init_db, sync_client_from_config

CLIENTS_FILE  = BASE_DIR / "monitor" / "clients.json"
CAMPAIGNS_DIR = BASE_DIR / "campaigns"
DNC_DIR       = BASE_DIR / "monitor" / "dnc"
INSTANTLY_KEY = os.environ.get("INSTANTLY_API_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "argusreach2026")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "argusreach-admin-secret-2026")


# ── AUTH ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["authed"] = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Wrong password")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_clients():
    with open(CLIENTS_FILE) as f:
        return json.load(f)

def save_clients(config):
    with open(CLIENTS_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_client_by_id(client_id):
    config = load_clients()
    for c in config.get("clients", []):
        if c.get("id") == client_id:
            return c, config
    return None, config

def fetch_instantly_analytics():
    if not INSTANTLY_KEY:
        return {}
    try:
        r = requests.get("https://api.instantly.ai/api/v2/campaigns/analytics",
                         headers={"Authorization": f"Bearer {INSTANTLY_KEY}"}, timeout=10)
        return {c["campaign_id"]: c for c in r.json()} if r.ok else {}
    except:
        return {}

def load_dnc(client_id):
    p = DNC_DIR / f"{client_id}.txt"
    if not p.exists():
        return set()
    return {line.strip().lower() for line in p.read_text().splitlines() if line.strip()}

def append_dnc(client_id, emails):
    p = DNC_DIR / f"{client_id}.txt"
    DNC_DIR.mkdir(exist_ok=True)
    existing = load_dnc(client_id)
    new_emails = [e for e in emails if e.lower() not in existing]
    with open(p, "a") as f:
        for e in new_emails:
            f.write(e.lower() + "\n")
    return len(new_emails)

def prep_leads(client_id, raw_rows, warm=False):
    """
    Clean and validate a raw lead list:
    - Normalize column names
    - Remove blanks / invalid emails
    - Dedupe within list
    - Cross-reference against DNC
    Returns (clean_rows, stats_dict)
    """
    dnc = load_dnc(client_id)
    seen = set()
    clean = []
    stats = {"total": 0, "invalid": 0, "dupes": 0, "dnc_hit": 0, "clean": 0}

    email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    for row in raw_rows:
        stats["total"] += 1
        # Normalize keys
        norm = {k.lower().strip().replace(" ", "_"): v.strip() for k, v in row.items()}
        email = (norm.get("email") or norm.get("email_address") or "").strip().lower()

        if not email or not email_re.match(email):
            stats["invalid"] += 1
            continue
        if email in seen:
            stats["dupes"] += 1
            continue
        if email in dnc:
            stats["dnc_hit"] += 1
            continue

        seen.add(email)
        clean.append({
            "email":        email,
            "first_name":   norm.get("first_name") or norm.get("firstname") or norm.get("first") or "",
            "last_name":    norm.get("last_name") or norm.get("lastname") or norm.get("last") or "",
            "company":      norm.get("company") or norm.get("company_name") or norm.get("organization") or "",
            "title":        norm.get("title") or norm.get("job_title") or "",
            "phone":        norm.get("phone") or norm.get("phone_number") or "",
            "warm":         "yes" if warm else (norm.get("warm") or ""),
            "notes":        norm.get("notes") or norm.get("personalization") or "",
        })
        stats["clean"] += 1

    return clean, stats


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    config = load_clients()
    clients = [c for c in config.get("clients", [])
               if not c.get("id","").startswith("_") and "example" not in c.get("id","")]
    analytics = fetch_instantly_analytics()

    conn = get_db()
    total_prospects = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    total_replies   = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='classified'").fetchone()[0]
    total_meetings  = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    total_revenue   = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM revenue").fetchone()[0]
    conn.close()

    client_stats = []
    for c in clients:
        cid = c.get("instantly_campaign_id","")
        a = analytics.get(cid, {})
        client_stats.append({
            "id": c["id"],
            "name": c.get("firm_name", c["id"]),
            "vertical": c.get("vertical",""),
            "plan": c.get("plan",""),
            "active": c.get("active", False),
            "leads": a.get("leads_count", 0),
            "sent": a.get("emails_sent_count", 0),
            "replies": a.get("reply_count_unique", 0),
            "campaign_name": c.get("campaign_name","—"),
        })

    return render_template("dashboard.html",
        clients=client_stats,
        total_prospects=total_prospects,
        total_replies=total_replies,
        total_meetings=total_meetings,
        total_revenue=f"${total_revenue/100:,.2f}",
        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    )


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def client_new():
    if request.method == "POST":
        f = request.form
        client_id = re.sub(r'[^a-z0-9_]', '_', f["id"].lower().strip())

        config = load_clients()
        existing_ids = [c.get("id") for c in config.get("clients",[])]
        if client_id in existing_ids:
            flash(f"Client ID '{client_id}' already exists.", "error")
            return render_template("client_new.html", form=f)

        new_client = {
            "id": client_id,
            "active": False,
            "mode": "draft_approval",
            "firm_name": f["firm_name"].strip(),
            "vertical": f["vertical"].strip(),
            "plan": f["plan"].strip(),
            "outreach_email": f["outreach_email"].strip(),
            "app_password": f.get("app_password","").strip(),
            "sender_name": f["sender_name"].strip(),
            "title": f.get("title","Founder").strip(),
            "client_email": f.get("client_email","").strip(),
            "calendly_link": f.get("calendly_link","").strip(),
            "instantly_campaign_id": "",
            "campaign_name": "",
            "contacts_per_month": int(f.get("contacts_per_month", 200)),
            "launch_date": "",
            "icp_summary": f.get("icp_summary","").strip(),
            "tone": f.get("tone","warm-professional").strip(),
            "compliance_note": f.get("compliance_note","").strip(),
            "positioning_note": f.get("positioning_note","").strip(),
            "prospects_csv": f"campaigns/{client_id}/prospects.csv",
        }

        config["clients"].append(new_client)
        save_clients(config)

        # Create campaign dir + empty DNC
        (CAMPAIGNS_DIR / client_id).mkdir(parents=True, exist_ok=True)
        (DNC_DIR / f"{client_id}.txt").touch()

        # Register in DB
        init_db()
        sync_client_from_config(new_client)

        flash(f"Client '{new_client['firm_name']}' created successfully.", "success")
        return redirect(url_for("client_detail", client_id=client_id))

    return render_template("client_new.html", form={})


@app.route("/clients/<client_id>")
@login_required
def client_detail(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    dnc = load_dnc(client_id)
    prospects_csv = BASE_DIR / client.get("prospects_csv", f"campaigns/{client_id}/prospects.csv")
    lead_count = 0
    if prospects_csv.exists():
        with open(prospects_csv) as f:
            lead_count = sum(1 for _ in csv.DictReader(f))

    conn = get_db()
    events = conn.execute("""
        SELECT e.created_at, e.event_type, e.metadata, p.email
        FROM events e LEFT JOIN prospects p ON p.id=e.prospect_id
        WHERE e.client_id=? ORDER BY e.created_at DESC LIMIT 20
    """, (client_id,)).fetchall()
    conn.close()

    analytics = fetch_instantly_analytics()
    cid = client.get("instantly_campaign_id","")
    stats = analytics.get(cid, {})

    return render_template("client_detail.html",
        client=client,
        dnc_count=len(dnc),
        lead_count=lead_count,
        stats=stats,
        events=[dict(e) for e in events]
    )


@app.route("/clients/<client_id>/dnc", methods=["POST"])
@login_required
def upload_dnc(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.files.get("dnc_file")
    if not f:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    content = f.read().decode("utf-8", errors="ignore")
    emails = []
    # Support CSV or plain text (one email per line)
    if "," in content or content.strip().startswith('"'):
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            for v in row.values():
                v = v.strip().lower()
                if "@" in v:
                    emails.append(v)
    else:
        emails = [line.strip().lower() for line in content.splitlines() if "@" in line]

    added = append_dnc(client_id, emails)
    flash(f"DNC list imported: {added} new entries added ({len(emails)-added} already on list).", "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/clients/<client_id>/leads", methods=["POST"])
@login_required
def upload_leads(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))

    f = request.files.get("leads_file")
    warm = request.form.get("warm") == "yes"
    if not f:
        flash("No file uploaded.", "error")
        return redirect(url_for("client_detail", client_id=client_id))

    content = f.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    raw_rows = list(reader)

    clean_rows, stats = prep_leads(client_id, raw_rows, warm=warm)

    # Save clean CSV
    out_dir = CAMPAIGNS_DIR / client_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "prospects.csv"

    # If file exists, append (keeping header)
    existing = []
    if out_path.exists():
        with open(out_path) as ef:
            existing = list(csv.DictReader(ef))
        existing_emails = {r["email"].lower() for r in existing}
        clean_rows = [r for r in clean_rows if r["email"] not in existing_emails]

    all_rows = existing + clean_rows
    fields = ["email","first_name","last_name","company","title","phone","warm","notes"]
    with open(out_path, "w", newline="") as of:
        writer = csv.DictWriter(of, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    msg = (f"Lead prep complete: {stats['total']} uploaded → "
           f"{stats['clean']} clean · {stats['dupes']} dupes · "
           f"{stats['dnc_hit']} DNC hits · {stats['invalid']} invalid. "
           f"prospects.csv now has {len(all_rows)} total leads.")
    flash(msg, "success")
    return redirect(url_for("client_detail", client_id=client_id))


@app.route("/leads/<client_id>/download")
@login_required
def download_leads(client_id):
    client, _ = get_client_by_id(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("dashboard"))
    path = BASE_DIR / client.get("prospects_csv", f"campaigns/{client_id}/prospects.csv")
    if not path.exists():
        flash("No leads file found.", "error")
        return redirect(url_for("client_detail", client_id=client_id))
    return send_file(path, as_attachment=True,
                     download_name=f"{client_id}_prospects_{datetime.now().strftime('%Y%m%d')}.csv")


@app.route("/campaigns")
@login_required
def campaigns():
    config = load_clients()
    clients = [c for c in config.get("clients",[])
               if not c.get("id","").startswith("_") and "example" not in c.get("id","")]
    analytics = fetch_instantly_analytics()

    rows = []
    registered_ids = set()
    for c in clients:
        cid = c.get("instantly_campaign_id","")
        a = analytics.get(cid, {})
        instantly_status = {0:"DRAFT",1:"ACTIVE",2:"COMPLETED"}.get(a.get("campaign_status",-1),"—")
        registered_ids.add(cid)
        rows.append({
            "client_id": c["id"],
            "firm": c.get("firm_name",""),
            "campaign_id": cid,
            "campaign_name": c.get("campaign_name","—"),
            "client_active": c.get("active",False),
            "instantly_status": instantly_status,
            "leads": a.get("leads_count",0),
            "sent": a.get("emails_sent_count",0),
            "replies": a.get("reply_count_unique",0),
            "mismatch": (c.get("active") and instantly_status != "ACTIVE") or
                        (not c.get("active") and instantly_status == "ACTIVE"),
        })

    # Unregistered campaigns
    unregistered = []
    try:
        r = requests.get("https://api.instantly.ai/api/v2/campaigns",
                         headers={"Authorization": f"Bearer {INSTANTLY_KEY}"}, timeout=10)
        if r.ok:
            all_camps = r.json() if isinstance(r.json(), list) else []
            for camp in all_camps:
                if camp.get("id") not in registered_ids:
                    unregistered.append({
                        "id": camp.get("id",""),
                        "name": camp.get("name",""),
                        "status": {0:"DRAFT",1:"ACTIVE",2:"COMPLETED"}.get(camp.get("status",-1),"UNKNOWN"),
                        "created": (camp.get("timestamp_created","") or "")[:10],
                    })
    except:
        pass

    return render_template("campaigns.html", rows=rows, unregistered=unregistered)


@app.route("/stats")
@login_required
def stats():
    """Embed the ops dashboard HTML inside the portal."""
    dash_path = BASE_DIR / "db" / "dashboard.html"
    if dash_path.exists():
        content = dash_path.read_text()
        # Strip <html>/<body> wrapper so it embeds cleanly in iframe
    return render_template("stats.html")


@app.route("/flowchart")
@login_required
def flowchart():
    return render_template("flowchart.html")


@app.route("/backlog")
@login_required
def backlog():
    backlog_path = BASE_DIR / "ops" / "backlog.md"
    content = backlog_path.read_text() if backlog_path.exists() else "No backlog file found."
    return render_template("backlog.html", content=content)


@app.route("/reports")
@login_required
def reports_list():
    reports_dir = BASE_DIR / "reports"
    files = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.html"), reverse=True):
            files.append({"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")})
    return render_template("reports.html", files=files)


@app.route("/reports/<filename>")
@login_required
def view_report(filename):
    reports_dir = BASE_DIR / "reports"
    path = reports_dir / filename
    if not path.exists() or not path.suffix == ".html":
        flash("Report not found.", "error")
        return redirect(url_for("reports_list"))
    return path.read_text()


@app.route("/health")
def health():
    return {"status": "ok", "ts": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    init_db()
    print("🚀 ArgusReach Admin Portal starting on port 5056...")
    app.run(host="0.0.0.0", port=5056, debug=False)
