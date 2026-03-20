# ArgusReach Tools

Scripts used in the ArgusReach campaign workflow.

---

## monthly_cycle.py — Main Campaign Runner

Runs the full monthly campaign cycle for a client: Apollo search → DNC filter → NeverBounce verify → replacement loop → create Instantly campaign → load leads with personalization → write sequence.

```bash
python3 tools/monthly_cycle.py --client <client_id> --month "April 2026"
python3 tools/monthly_cycle.py --client <client_id> --month "April 2026" --dry-run
python3 tools/monthly_cycle.py --client <client_id> --month "April 2026" --skip-apollo  # use CSV instead
```

---

## monthly_report.py — Monthly Performance Report

Generates HTML report for a client. Pulls from SQLite DB (events, meetings, revenue). Vito reviews before sending manually.

```bash
python3 tools/monthly_report.py --client <client_id> --month "April 2026"
```

---

## dedupe.py — Prospect List Deduplication

Removes duplicates, DNC hits, and already-contacted prospects from a CSV before loading.

```bash
python3 tools/dedupe.py \
  --prospects exports/prospects.csv \
  --output exports/prospects-clean.csv \
  --dnc monitor/dnc/<client_id>.txt
```

---

## register_calendly_webhook.py — Calendly Webhook Setup

Run once when first client signs and Calendly Standard is active. Auto-registers webhook + writes signing key to .env.

```bash
python3 tools/register_calendly_webhook.py
```

---

## campaign_status.py — Check Instantly Campaign Status

Quick status check for a campaign.

---

## validate_campaign.py — Pre-Launch Validation

Validates campaign config before launch.

---

## status.py — System Status

Quick overview of all active clients and campaign states.

---

## build_prospect_list.py — Manual Prospect List Builder

For building prospect lists manually (skip-Apollo scenarios, test runs).

---

## import_prospects.py — Import CSV into DB

Imports a prospect CSV into the SQLite DB for a client.

---

*Personalization (city, title, state) is handled automatically by monthly_cycle.py via Apollo fields loaded as custom_variables in Instantly — no separate script needed.*
