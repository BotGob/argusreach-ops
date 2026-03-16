#!/usr/bin/env python3
"""
ArgusReach — Internal Ops Dashboard Generator
Reads from SQLite DB and generates a static HTML dashboard.
Run manually or via cron to refresh.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_db, init_db

OUTPUT = Path(__file__).parent / "dashboard.html"


def fetch_stats():
    conn = get_db()

    total_prospects = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    total_replies   = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='classified'").fetchone()[0]
    total_meetings  = conn.execute("SELECT COUNT(*) FROM meetings WHERE status='scheduled' OR status='completed'").fetchone()[0]
    total_revenue   = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM revenue").fetchone()[0]

    clients = conn.execute("""
        SELECT c.id, c.name, c.vertical, c.plan, c.status,
               ca.leads_count, ca.emails_sent, ca.replies, ca.opens
        FROM clients c
        LEFT JOIN campaigns ca ON ca.client_id = c.id
        WHERE c.status = 'active'
        ORDER BY c.name
    """).fetchall()

    meetings_per_client = {}
    for row in conn.execute("SELECT client_id, COUNT(*) as cnt FROM meetings GROUP BY client_id"):
        meetings_per_client[row["client_id"]] = row["cnt"]

    stage_breakdown = {}
    for row in conn.execute("""
        SELECT client_id, stage, COUNT(*) as cnt
        FROM prospects GROUP BY client_id, stage
    """):
        if row["client_id"] not in stage_breakdown:
            stage_breakdown[row["client_id"]] = {}
        stage_breakdown[row["client_id"]][row["stage"]] = row["cnt"]

    classification_totals = {}
    for row in conn.execute("""
        SELECT json_extract(metadata,'$.classification') as cls, COUNT(*) as cnt
        FROM events WHERE event_type='classified' AND metadata IS NOT NULL
        GROUP BY cls ORDER BY cnt DESC
    """):
        if row["cls"]:
            classification_totals[row["cls"]] = row["cnt"]

    recent_events = conn.execute("""
        SELECT e.created_at, e.client_id, e.event_type, e.metadata,
               p.email, p.first_name, p.last_name
        FROM events e
        LEFT JOIN prospects p ON p.id = e.prospect_id
        ORDER BY e.created_at DESC LIMIT 20
    """).fetchall()

    revenue_rows = conn.execute("""
        SELECT plan, customer_email, amount_cents, created_at
        FROM revenue ORDER BY created_at DESC LIMIT 10
    """).fetchall()

    conn.close()
    return {
        "total_prospects": total_prospects,
        "total_replies": total_replies,
        "total_meetings": total_meetings,
        "total_revenue_cents": total_revenue,
        "clients": [dict(r) for r in clients],
        "meetings_per_client": meetings_per_client,
        "stage_breakdown": stage_breakdown,
        "classification_totals": classification_totals,
        "recent_events": [dict(r) for r in recent_events],
        "revenue_rows": [dict(r) for r in revenue_rows],
    }


def render(stats):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    rev = f"${stats['total_revenue_cents']/100:,.2f}"

    # Summary cards
    cards_html = ""
    for label, value in [
        ("Prospects Tracked", f"{stats['total_prospects']:,}"),
        ("Replies Received",  f"{stats['total_replies']:,}"),
        ("Meetings Booked",   f"{stats['total_meetings']:,}"),
        ("Total Revenue",     rev),
    ]:
        cards_html += f"""
        <div class="card">
          <div class="card-label">{label}</div>
          <div class="card-value">{value}</div>
        </div>"""

    # Clients table
    client_rows = ""
    for c in stats["clients"]:
        cid       = c["id"]
        sent      = c.get("emails_sent") or 0
        replies   = c.get("replies") or 0
        leads     = c.get("leads_count") or 0
        meetings  = stats["meetings_per_client"].get(cid, 0)
        rr        = f"{replies/sent*100:.1f}%" if sent else "—"
        mr        = f"{meetings/sent*100:.1f}%" if sent else "—"
        stages    = stats["stage_breakdown"].get(cid, {})

        client_rows += f"""
        <tr>
          <td><b>{c['name']}</b></td>
          <td>{c.get('vertical','')}</td>
          <td>{c.get('plan','')}</td>
          <td>{leads:,}</td>
          <td>{sent:,}</td>
          <td>{replies:,}</td>
          <td>{rr}</td>
          <td>{meetings}</td>
          <td>{mr}</td>
          <td style="font-size:12px">{_stage_chips(stages)}</td>
        </tr>"""

    if not client_rows:
        client_rows = "<tr><td colspan='10' style='text-align:center;color:#666'>No active clients yet</td></tr>"

    # Classification breakdown
    cls_html = ""
    for cls, cnt in stats["classification_totals"].items():
        color = {"interested":"#22c55e","not_now":"#f59e0b","ooo":"#60a5fa",
                 "unsubscribe":"#ef4444","irrelevant":"#6b7280"}.get(cls, "#94a3b8")
        cls_html += f'<span class="chip" style="background:{color}22;color:{color};border:1px solid {color}44">{cls}: {cnt}</span>'
    if not cls_html:
        cls_html = "<span style='color:#666'>No data yet</span>"

    # Recent events
    events_html = ""
    for ev in stats["recent_events"]:
        meta = {}
        try:
            meta = json.loads(ev.get("metadata") or "{}")
        except:
            pass
        name  = f"{ev.get('first_name','')} {ev.get('last_name','')}".strip() or ev.get("email","")
        etype = ev.get("event_type","")
        color = {"reply_sent":"#22c55e","meeting_booked":"#a855f7","draft_queued":"#f59e0b",
                 "classified":"#60a5fa","draft_approved":"#22c55e","draft_rejected":"#ef4444"}.get(etype,"#94a3b8")
        ts_ev = (ev.get("created_at","") or "")[:16].replace("T"," ")
        detail = meta.get("classification") or meta.get("event_name") or ""
        events_html += f"""
        <tr>
          <td style="color:#666;font-size:12px">{ts_ev}</td>
          <td style="color:#94a3b8;font-size:12px">{ev.get('client_id','')}</td>
          <td><span class="chip" style="background:{color}22;color:{color};border:1px solid {color}44">{etype}</span></td>
          <td>{name}</td>
          <td style="color:#666;font-size:12px">{detail}</td>
        </tr>"""
    if not events_html:
        events_html = "<tr><td colspan='5' style='text-align:center;color:#666'>No events yet</td></tr>"

    # Revenue rows
    rev_html = ""
    for r in stats["revenue_rows"]:
        amt = f"${r.get('amount_cents',0)/100:,.2f}"
        ts_r = (r.get("created_at","") or "")[:10]
        rev_html += f"""
        <tr>
          <td>{ts_r}</td>
          <td>{r.get('customer_email','')}</td>
          <td>{r.get('plan','')}</td>
          <td style="color:#22c55e"><b>{amt}</b></td>
        </tr>"""
    if not rev_html:
        rev_html = "<tr><td colspan='4' style='text-align:center;color:#666'>No payments yet</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ArgusReach — Ops Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f1117;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:32px}}
  h1{{font-size:24px;font-weight:700;color:#fff;margin-bottom:4px}}
  .subtitle{{color:#666;font-size:13px;margin-bottom:32px}}
  h2{{font-size:15px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;margin:32px 0 12px}}
  .cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}}
  .card{{background:#1a1f2e;border:1px solid #2d3448;border-radius:12px;padding:20px 24px;min-width:180px;flex:1}}
  .card-label{{font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
  .card-value{{font-size:28px;font-weight:700;color:#fff}}
  table{{width:100%;border-collapse:collapse;background:#1a1f2e;border:1px solid #2d3448;border-radius:10px;overflow:hidden}}
  th{{background:#151922;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding:10px 14px;text-align:left;border-bottom:1px solid #2d3448}}
  td{{padding:10px 14px;border-bottom:1px solid #1e2535;font-size:13px;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#1e2535}}
  .chip{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;margin:2px}}
  .cls-row{{display:flex;flex-wrap:wrap;gap:8px;padding:16px;background:#1a1f2e;border:1px solid #2d3448;border-radius:10px}}
  footer{{margin-top:40px;color:#333;font-size:11px;text-align:center}}
</style>
</head>
<body>

<h1>⚡ ArgusReach — Ops Dashboard</h1>
<div class="subtitle">Generated {ts} · Auto-refreshed by cron</div>

<h2>Summary</h2>
<div class="cards">{cards_html}</div>

<h2>Active Clients</h2>
<table>
  <thead><tr>
    <th>Client</th><th>Vertical</th><th>Plan</th>
    <th>Leads</th><th>Sent</th><th>Replies</th><th>Reply Rate</th>
    <th>Meetings</th><th>Mtg Rate</th><th>Stages</th>
  </tr></thead>
  <tbody>{client_rows}</tbody>
</table>

<h2>Reply Classifications</h2>
<div class="cls-row">{cls_html}</div>

<h2>Recent Events</h2>
<table>
  <thead><tr><th>Time</th><th>Client</th><th>Event</th><th>Prospect</th><th>Detail</th></tr></thead>
  <tbody>{events_html}</tbody>
</table>

<h2>Revenue</h2>
<table>
  <thead><tr><th>Date</th><th>Email</th><th>Plan</th><th>Amount</th></tr></thead>
  <tbody>{rev_html}</tbody>
</table>

<footer>ArgusReach Internal Dashboard · {ts}</footer>
</body>
</html>"""


def _stage_chips(stages: dict) -> str:
    colors = {"interested":"#22c55e","meeting_booked":"#a855f7","replied":"#60a5fa",
              "not_now":"#f59e0b","unsubscribed":"#ef4444","added":"#475569"}
    out = ""
    for stage, cnt in sorted(stages.items(), key=lambda x: -x[1]):
        c = colors.get(stage, "#94a3b8")
        out += f'<span class="chip" style="background:{c}22;color:{c}">{stage}:{cnt}</span>'
    return out


if __name__ == "__main__":
    init_db()
    stats = fetch_stats()
    html  = render(stats)
    OUTPUT.write_text(html)
    print(f"✅ Dashboard written to {OUTPUT}")
