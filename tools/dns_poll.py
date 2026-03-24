#!/usr/bin/env python3
"""
DNS Auto-Poll — runs every 4 hours via systemd timer.
Checks SPF/DKIM/DMARC for any client in dns_pending onboarding status.
Auto-checks the dns_verified gate and fires Telegram alert when passing.
Zero Claude usage — pure DNS lookups.
"""
import sys, os, json, subprocess, time, requests
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
from dotenv import load_dotenv
load_dotenv(BASE_DIR / "monitor" / ".env")

CLIENTS_FILE = BASE_DIR / "monitor" / "clients.json"
TG_TOKEN = os.environ.get("ARGUSREACH_BOT_TOKEN", "8588914878:AAEQnZNXWx9_j2llD-Yw0sWwjegXu-pruCk")
TG_CHAT  = os.environ.get("ARGUSREACH_CHAT_ID", "-1003821840813")


def notify(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception:
        pass


def dns_lookup(qtype, name):
    """Run a DNS TXT lookup. Returns list of records or []."""
    try:
        result = subprocess.run(
            ["dig", "+short", qtype, name],
            capture_output=True, text=True, timeout=10
        )
        lines = [l.strip().strip('"') for l in result.stdout.strip().splitlines() if l.strip()]
        return lines
    except Exception:
        return []


def check_dns(domain, email_provider="google", dkim_selector=None):
    """Check SPF, DKIM, DMARC for a domain. Returns dict with pass/fail per record."""
    result = {"spf": False, "dkim": False, "dmarc": False, "spf_record": None, "dkim_selector": None}

    # SPF
    spf_records = dns_lookup("TXT", domain)
    for rec in spf_records:
        if "v=spf1" in rec:
            result["spf_record"] = rec
            include = "_spf.google.com" if email_provider != "microsoft" else "spf.protection.outlook.com"
            if include in rec or "include:_spf.google.com" in rec or "include:spf.protection.outlook.com" in rec:
                result["spf"] = True
            break

    # DMARC
    dmarc = dns_lookup("TXT", f"_dmarc.{domain}")
    for rec in dmarc:
        if "v=DMARC1" in rec:
            result["dmarc"] = True
            break

    # DKIM — try common selectors
    selectors = []
    if dkim_selector:
        selectors.append(dkim_selector)
    if email_provider == "google":
        selectors += ["google", "selector1", "selector2", "mail", "default"]
    else:
        selectors += ["selector1", "selector2", "mail", "default"]

    for sel in selectors:
        dkim_records = dns_lookup("TXT", f"{sel}._domainkey.{domain}")
        if any("v=DKIM1" in r for r in dkim_records):
            result["dkim"] = True
            result["dkim_selector"] = sel
            break

    return result


def load_clients():
    with open(CLIENTS_FILE) as f:
        return json.load(f)


def save_clients(config):
    with open(CLIENTS_FILE, "w") as f:
        json.dump(config, f, indent=2)


def main():
    config = load_clients()
    clients = config.get("clients", [])

    # Only check clients who need DNS verification
    targets = [
        c for c in clients
        if c.get("outreach_email")
        and not c.get("checklist", {}).get("dns_verified")
        and c.get("onboarding_status") in ("dns_pending", "warming_up", "pending_review", "ready_to_launch")
        and not c.get("active")
    ]

    if not targets:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] DNS poll: no clients awaiting DNS verification.")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] DNS poll: checking {len(targets)} client(s)...")
    changed = False

    for client in targets:
        email = client.get("outreach_email", "")
        if "@" not in email:
            continue
        domain = email.split("@")[-1]
        provider = client.get("_email_provider", "google")
        firm = client.get("firm_name", client["id"])

        print(f"  Checking {domain} for {firm}...")
        dns = check_dns(domain, email_provider=provider)
        spf_pass   = dns["spf"]
        dkim_pass  = dns["dkim"]
        dmarc_pass = dns["dmarc"]
        all_pass   = spf_pass and dmarc_pass  # DKIM optional if not yet generated

        print(f"    SPF={spf_pass} DKIM={dkim_pass} DMARC={dmarc_pass}")

        if all_pass and not client.get("checklist", {}).get("dns_verified"):
            # Auto-check gate
            c2 = next((x for x in config["clients"] if x.get("id") == client["id"]), None)
            if c2:
                c2.setdefault("checklist", {})["dns_verified"] = True
                if dns.get("dkim_selector"):
                    c2["_dkim_selector"] = dns["dkim_selector"]
                changed = True
                notify(
                    f"🔒 *{firm}* DNS verified automatically\n"
                    f"SPF ✅ DKIM {'✅' if dkim_pass else '⚠️ (not yet)'} DMARC ✅\n"
                    f"→ DNS gate auto-checked in portal"
                )
                print(f"    ✅ DNS gate auto-checked for {firm}")
        elif not all_pass:
            print(f"    ⏳ Not yet passing for {firm} (SPF={spf_pass}, DMARC={dmarc_pass})")
        else:
            print(f"    Already verified for {firm}")

        time.sleep(1)  # be kind to DNS

    if changed:
        save_clients(config)
        print("Clients saved.")


if __name__ == "__main__":
    main()
