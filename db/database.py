#!/usr/bin/env python3
"""
ArgusReach — Database Layer
Single SQLite source of truth for all prospect, campaign, event, meeting, and revenue data.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "argusreach.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Migration-safe: add columns that may not exist in older DBs
    _migrations = [
        "ALTER TABLE events ADD COLUMN campaign_id TEXT",
        "ALTER TABLE campaigns ADD COLUMN icp_label TEXT",
        "ALTER TABLE campaigns ADD COLUMN prospects_csv TEXT",
        "ALTER TABLE prospects ADD COLUMN subsequence_count INTEGER DEFAULT 0",
        "ALTER TABLE prospects ADD COLUMN replied_at TEXT",
        "ALTER TABLE prospects ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE prospects ADD COLUMN timezone TEXT DEFAULT ''",
        "ALTER TABLE prospects ADD COLUMN call_status TEXT DEFAULT ''",
        "ALTER TABLE prospects ADD COLUMN called_at TEXT DEFAULT ''",
    ]
    for stmt in _migrations:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # Column already exists

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY,
        name TEXT,
        vertical TEXT,
        plan TEXT,
        status TEXT DEFAULT 'active',
        launch_date TEXT,
        instantly_campaign_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        client_id TEXT,
        name TEXT,
        instantly_campaign_id TEXT,
        icp_label TEXT,
        prospects_csv TEXT,
        status TEXT DEFAULT 'active',
        launch_date TEXT,
        leads_count INTEGER DEFAULT 0,
        emails_sent INTEGER DEFAULT 0,
        opens INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        replies INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS prospects (
        id TEXT PRIMARY KEY,
        client_id TEXT,
        campaign_id TEXT,
        email TEXT,
        first_name TEXT,
        last_name TEXT,
        company TEXT,
        stage TEXT DEFAULT 'added',
        follow_up_date TEXT,
        follow_up_sent INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    -- Migration: add follow_up columns if they don't exist yet
    CREATE INDEX IF NOT EXISTS idx_prospects_followup ON prospects(follow_up_date) WHERE follow_up_date IS NOT NULL;

    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        prospect_id TEXT,
        client_id TEXT,
        campaign_id TEXT,
        event_type TEXT,
        metadata TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS meetings (
        id TEXT PRIMARY KEY,
        client_id TEXT,
        prospect_id TEXT,
        prospect_email TEXT,
        prospect_name TEXT,
        meeting_date TEXT,
        scheduled_at TEXT,
        status TEXT DEFAULT 'confirmed',
        source TEXT DEFAULT 'manual',
        invitee_email TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS revenue (
        id TEXT PRIMARY KEY,
        client_id TEXT,
        stripe_payment_id TEXT,
        amount_cents INTEGER,
        plan TEXT,
        billing_period TEXT DEFAULT 'monthly',
        customer_email TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_events_client ON events(client_id);
    CREATE INDEX IF NOT EXISTS idx_events_prospect ON events(prospect_id);
    CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
    CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email);
    CREATE INDEX IF NOT EXISTS idx_prospects_client ON prospects(client_id);
    CREATE INDEX IF NOT EXISTS idx_prospects_stage ON prospects(stage);
    """)

    conn.commit()
    conn.close()


def prospect_id(client_id: str, email: str) -> str:
    return hashlib.md5(f"{client_id}:{email.lower()}".encode()).hexdigest()


def log_event(client_id: str, pid: str, event_type: str, metadata: dict = None, campaign_id: str = None):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO events (id, prospect_id, client_id, campaign_id, event_type, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pid, client_id, campaign_id,
         event_type,
         json.dumps(metadata) if metadata else None,
         datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def upsert_campaign(client_id: str, instantly_campaign_id: str, name: str = "",
                    icp_label: str = "", status: str = "draft",
                    launch_date: str = "", leads_count: int = 0,
                    prospects_csv: str = "") -> str:
    """Create or update a campaign record. Returns the campaign id (= instantly_campaign_id)."""
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO campaigns
            (id, client_id, name, instantly_campaign_id, icp_label, prospects_csv,
             status, launch_date, leads_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name        = COALESCE(NULLIF(excluded.name, ''), name),
            icp_label   = COALESCE(NULLIF(excluded.icp_label, ''), icp_label),
            prospects_csv = COALESCE(NULLIF(excluded.prospects_csv, ''), prospects_csv),
            status      = excluded.status,
            launch_date = COALESCE(NULLIF(excluded.launch_date, ''), launch_date),
            leads_count = CASE WHEN excluded.leads_count > 0 THEN excluded.leads_count ELSE leads_count END,
            updated_at  = excluded.updated_at
    """, (instantly_campaign_id, client_id, name, instantly_campaign_id,
          icp_label, prospects_csv, status, launch_date, leads_count, now, now))
    conn.commit()
    conn.close()
    return instantly_campaign_id


def get_campaigns_for_client(client_id: str) -> list:
    """Return all campaign rows for a client, ordered by launch_date desc."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM campaigns WHERE client_id = ? ORDER BY launch_date DESC, created_at DESC",
        (client_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_campaign_metrics(instantly_campaign_id: str, client_id: str) -> dict:
    """Per-campaign metrics: combines campaigns table (Instantly data) + events joined via prospects."""
    conn = get_db()

    # Top-line from campaigns table (synced from Instantly)
    row = conn.execute(
        "SELECT * FROM campaigns WHERE id = ? AND client_id = ?",
        (instantly_campaign_id, client_id)
    ).fetchone()

    leads_count  = 0
    emails_sent  = 0
    opens        = 0
    replies_inst = 0
    camp_status  = "unknown"
    if row:
        row = dict(row)
        leads_count  = row.get("leads_count", 0) or 0
        emails_sent  = row.get("emails_sent", 0) or 0
        opens        = row.get("opens", 0) or 0
        replies_inst = row.get("replies", 0) or 0
        camp_status  = row.get("status", "unknown")

    # Prospect count from DB (always accurate, even for DRAFT before Instantly syncs)
    leads_db = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM prospects WHERE client_id = ? AND campaign_id = ?",
        (client_id, instantly_campaign_id)
    ).fetchone()[0] or 0

    leads = max(leads_db, leads_count)

    # Classification breakdown via prospects.campaign_id join
    cls_rows = conn.execute("""
        SELECT json_extract(e.metadata,'$.classification') as cls,
               COUNT(DISTINCT e.prospect_id) as cnt
        FROM events e
        JOIN prospects p ON p.id = e.prospect_id
        WHERE p.campaign_id = ? AND p.client_id = ?
          AND e.event_type = 'classified'
          AND json_extract(e.metadata,'$.classification') IS NOT NULL
        GROUP BY cls
    """, (instantly_campaign_id, client_id)).fetchall()
    breakdown = {r[0]: r[1] for r in cls_rows if r[0]}

    meetings = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE client_id = ?", (client_id,)
    ).fetchone()[0]

    conn.close()

    total_classified = sum(breakdown.values())
    interested = breakdown.get("positive", 0) + breakdown.get("question", 0)
    reply_rate = f"{(total_classified / leads * 100):.1f}%" if leads > 0 else "-"

    return {
        "campaign_id":      instantly_campaign_id,
        "status":           camp_status,
        "leads":            leads,
        "emails_sent":      emails_sent,
        "opens":            opens,
        "total_classified": total_classified,
        "reply_interested": interested,
        "reply_not_now":    breakdown.get("not_now", 0),
        "reply_negative":   breakdown.get("negative", 0),
        "reply_escalated":  breakdown.get("escalated", 0),
        "meetings":         meetings,
        "reply_rate":       reply_rate,
    }


def get_client_consolidated_metrics(client_id: str, campaign_ids: list) -> dict:
    """Sum metrics across all campaigns for a client (consolidated view)."""
    if not campaign_ids:
        return {"leads": 0, "emails_sent": 0, "total_classified": 0,
                "reply_interested": 0, "reply_not_now": 0, "reply_negative": 0,
                "reply_escalated": 0, "meetings": 0, "reply_rate": "-"}
    all_m = [get_campaign_metrics(cid, client_id) for cid in campaign_ids]
    leads       = sum(m["leads"] for m in all_m)
    sent        = sum(m["emails_sent"] for m in all_m)
    classified  = sum(m["total_classified"] for m in all_m)
    interested  = sum(m["reply_interested"] for m in all_m)
    not_now     = sum(m["reply_not_now"] for m in all_m)
    negative    = sum(m["reply_negative"] for m in all_m)
    escalated   = sum(m["reply_escalated"] for m in all_m)
    meetings    = all_m[0]["meetings"] if all_m else 0  # meetings are client-level
    return {
        "leads":            leads,
        "emails_sent":      sent,
        "total_classified": classified,
        "reply_interested": interested,
        "reply_not_now":    not_now,
        "reply_negative":   negative,
        "reply_escalated":  escalated,
        "meetings":         meetings,
        "reply_rate":       f"{(classified / leads * 100):.1f}%" if leads > 0 else "-",
    }


def upsert_prospect(client_id: str, campaign_id: str, email: str,
                    first_name: str = "", last_name: str = "",
                    company: str = "", stage: str = "added",
                    phone: str = "", city: str = "", state: str = ""):
    pid = prospect_id(client_id, email)
    now = datetime.utcnow().isoformat()
    # Derive timezone from state
    tz = _state_to_timezone(state) if state else ""
    conn = get_db()
    conn.execute("""
        INSERT INTO prospects (id, client_id, campaign_id, email, first_name, last_name, company, stage, phone, timezone, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            stage      = excluded.stage,
            first_name = COALESCE(NULLIF(excluded.first_name,''), first_name),
            last_name  = COALESCE(NULLIF(excluded.last_name,''),  last_name),
            company    = COALESCE(NULLIF(excluded.company,''),    company),
            phone      = COALESCE(NULLIF(excluded.phone,''),      phone),
            timezone   = COALESCE(NULLIF(excluded.timezone,''),   timezone),
            updated_at = excluded.updated_at
    """, (pid, client_id, campaign_id, email.lower(), first_name, last_name, company, stage, phone, tz, now, now))
    conn.commit()
    conn.close()
    return pid


def _state_to_timezone(state: str) -> str:
    """Map US state abbreviation or name to IANA timezone. Best effort."""
    ET = "America/New_York"
    CT = "America/Chicago"
    MT = "America/Denver"
    PT = "America/Los_Angeles"
    STATE_TZ = {
        # Eastern
        "CT":"CT","DE":ET,"FL":ET,"GA":ET,"IN":ET,"KY":ET,"MA":ET,"MD":ET,"ME":ET,
        "MI":ET,"NC":ET,"NH":ET,"NJ":ET,"NY":ET,"OH":ET,"PA":ET,"RI":ET,"SC":ET,
        "TN":ET,"VA":ET,"VT":ET,"WV":ET,"DC":ET,
        # Central
        "AL":CT,"AR":CT,"IA":CT,"IL":CT,"KS":CT,"LA":CT,"MN":CT,"MO":CT,"MS":CT,
        "ND":CT,"NE":CT,"OK":CT,"SD":CT,"TX":CT,"WI":CT,
        # Mountain
        "AZ":"America/Phoenix","CO":MT,"ID":MT,"MT":MT,"NM":MT,"UT":MT,"WY":MT,
        # Pacific
        "CA":PT,"NV":PT,"OR":PT,"WA":PT,
        # Hawaii/Alaska
        "AK":"America/Anchorage","HI":"Pacific/Honolulu",
    }
    s = state.strip().upper()[:2]
    return STATE_TZ.get(s, ET)  # default Eastern


def update_prospect_stage(pid: str, stage: str):
    conn = get_db()
    conn.execute(
        "UPDATE prospects SET stage=?, updated_at=? WHERE id=?",
        (stage, datetime.utcnow().isoformat(), pid)
    )
    conn.commit()
    conn.close()


def set_follow_up_date(pid: str, follow_up_date: str):
    """Store OOO / not-now follow-up date on a prospect."""
    conn = get_db()
    conn.execute(
        "UPDATE prospects SET follow_up_date=?, follow_up_sent=0, updated_at=? WHERE id=?",
        (follow_up_date, datetime.utcnow().isoformat(), pid)
    )
    conn.commit()
    conn.close()


def get_due_followups(client_id: str = None) -> list:
    """Return prospects whose follow_up_date is today or past and not yet re-surfaced."""
    conn = get_db()
    today = datetime.utcnow().strftime('%Y-%m-%d')
    query = """
        SELECT * FROM prospects
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= ?
          AND follow_up_sent = 0
    """
    params = [today]
    if client_id:
        query += " AND client_id = ?"
        params.append(client_id)
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()
    return rows


def mark_follow_up_sent(pid: str):
    conn = get_db()
    conn.execute("UPDATE prospects SET follow_up_sent=1, updated_at=? WHERE id=?",
                 (datetime.utcnow().isoformat(), pid))
    conn.commit()
    conn.close()


def get_campaign_funnel(client_id: str, campaign_id: str) -> dict:
    """Return funnel breakdown for a campaign from DB prospect stages."""
    conn = get_db()
    rows = conn.execute("""
        SELECT stage, COUNT(*) as cnt
        FROM prospects
        WHERE client_id = ? AND campaign_id = ?
        GROUP BY stage
    """, (client_id, campaign_id)).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM prospects WHERE client_id = ? AND campaign_id = ?",
        (client_id, campaign_id)
    ).fetchone()[0]
    replied_events = conn.execute("""
        SELECT COUNT(DISTINCT p.id) FROM prospects p
        JOIN events e ON e.prospect_id = p.id
        WHERE p.client_id = ? AND p.campaign_id = ?
          AND e.event_type = 'classified'
    """, (client_id, campaign_id)).fetchone()[0]
    conn.close()

    stage_counts = {r[0]: r[1] for r in rows if r[0]}
    t1 = sum(stage_counts.get(s, 0) for s in ["touch_1_sent","touch_2_sent","touch_3_sent","replied","sequence_complete"])
    t2 = sum(stage_counts.get(s, 0) for s in ["touch_2_sent","touch_3_sent","replied","sequence_complete"])
    t3 = sum(stage_counts.get(s, 0) for s in ["touch_3_sent","replied","sequence_complete"])
    replied = max(stage_counts.get("replied", 0) + stage_counts.get("sequence_complete", 0), replied_events)
    complete = stage_counts.get("sequence_complete", 0)
    return {
        "total":             total,
        "touch_1_sent":      t1,
        "touch_2_sent":      t2,
        "touch_3_sent":      t3,
        "replied":           replied,
        "sequence_complete": complete,
    }


def get_client_funnel(client_id: str, campaign_ids: list) -> dict:
    """Sum funnel across all campaigns for a client."""
    totals = {"total": 0, "touch_1_sent": 0, "touch_2_sent": 0,
              "touch_3_sent": 0, "replied": 0, "sequence_complete": 0}
    for cid in campaign_ids:
        f = get_campaign_funnel(client_id, cid)
        for k in totals:
            totals[k] += f.get(k, 0)
    return totals


def sync_client_from_config(c: dict):
    """Sync client state from clients.json (master) into DB.
    Called automatically by save_clients() — DB is always a mirror, never the source of truth.
    """
    conn = get_db()
    # Add onboarding_status column if it doesn't exist yet (migration-safe)
    try:
        conn.execute("ALTER TABLE clients ADD COLUMN onboarding_status TEXT DEFAULT 'email_setup'")
        conn.commit()
    except Exception:
        pass  # Column already exists
    conn.execute("""
        INSERT INTO clients (id, name, vertical, plan, status, onboarding_status, launch_date, instantly_campaign_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name                = excluded.name,
            vertical            = excluded.vertical,
            plan                = excluded.plan,
            status              = excluded.status,
            onboarding_status   = excluded.onboarding_status,
            launch_date         = excluded.launch_date,
            instantly_campaign_id = excluded.instantly_campaign_id
    """, (
        c.get("id"),
        c.get("firm_name") or c.get("name") or c.get("id"),
        c.get("vertical"),
        c.get("plan", "unknown"),
        "active" if c.get("active") else "paused",
        c.get("onboarding_status", "email_setup"),
        c.get("launch_date", ""),
        c.get("instantly_campaign_id", ""),
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"✅ Database initialized at {DB_PATH}")
