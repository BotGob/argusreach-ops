#!/usr/bin/env python3
"""
ArgusReach — Relationship & DNC Deduplication Script
=====================================================
Cleans a prospect list by cross-referencing against two lists:

  1. Existing relationships  — people the client already knows.
     Sending a cold "let me introduce myself" email to someone they work
     with daily is embarrassing. These get removed silently before launch.

  2. DNC list  — explicit do-not-contacts and past unsubscribes.
     Hard removes. Non-negotiable.

Matching uses two passes:
  - Email exact match  (primary, strongest signal)
  - Name + Company fuzzy match  (catches formatting differences / missing emails)

Usage:
  python3 dedupe.py \\
    --prospects  exports/prospects.csv \\
    --output     exports/prospects-clean.csv \\
    --relationships clients/pt-clinic/relationships.csv \\
    --dnc        clients/pt-clinic/dnc.txt

All flags except --prospects and --output are optional.
Run with just --prospects and --output to validate format only.

Output files:
  [output].csv       — clean prospect list, ready for personalize.py
  [output]-log.csv   — every removed contact with reason + matched list
"""

import csv
import re
import sys
import argparse
from pathlib import Path
from difflib import SequenceMatcher


# ── Normalisation helpers ──────────────────────────────────────────────────

def norm_email(e):
    """Lowercase, strip whitespace. Returns '' if falsy."""
    return (e or "").strip().lower()

def norm_text(s):
    """Lowercase, strip punctuation and extra whitespace. For fuzzy matching."""
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)          # punctuation → space
    s = re.sub(r"\s+", " ", s).strip()
    return s

def similarity(a, b):
    """SequenceMatcher ratio between two normalised strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ── CSV column resolver ────────────────────────────────────────────────────

def get_col(row, *candidates):
    lowered = {k.strip().lower(): v for k, v in row.items()}
    for c in candidates:
        val = lowered.get(c.lower(), "")
        if val:
            return val.strip()
    return ""


# ── List loaders ───────────────────────────────────────────────────────────

def load_dnc(path):
    """
    Load a DNC file. Accepts:
      - .txt  one email per line
      - .csv  with any column containing 'email'
    Returns set of normalised emails.
    """
    p = Path(path)
    emails = set()
    if not p.exists():
        print(f"  ⚠  DNC file not found: {path} — skipping")
        return emails

    if p.suffix.lower() == ".txt":
        with open(p, encoding="utf-8") as f:
            for line in f:
                e = norm_email(line)
                if e and "@" in e:
                    emails.add(e)
    else:
        with open(p, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                e = norm_email(get_col(row, "email", "Email", "EMAIL",
                                       "email address", "Email Address"))
                if e:
                    emails.add(e)

    print(f"  DNC loaded:           {len(emails):>5} entries  ({p.name})")
    return emails


def load_relationships(path):
    """
    Load existing relationships CSV. Flexible format — only needs one of:
      email, name, company (will use whatever columns exist).
    Returns list of dicts: {email, name_norm, company_norm}
    """
    p = Path(path)
    records = []
    if not p.exists():
        print(f"  ⚠  Relationships file not found: {path} — skipping")
        return records

    with open(p, newline="", encoding="utf-8-sig") as f:
        # Handle single-column files (just emails)
        sample = f.read(512)
        f.seek(0)
        if "," not in sample.split("\n")[0]:
            # Plain email list
            for line in f:
                e = norm_email(line)
                if e and "@" in e:
                    records.append({"email": e, "name_norm": "", "company_norm": ""})
        else:
            reader = csv.DictReader(f)
            for row in reader:
                email   = norm_email(get_col(row, "email", "Email", "EMAIL",
                                             "email address"))
                fname   = get_col(row, "First Name", "first_name", "firstname")
                lname   = get_col(row, "Last Name", "last_name", "lastname")
                name    = get_col(row, "Name", "name") or f"{fname} {lname}".strip()
                company = get_col(row, "Company", "company", "Firm", "Practice",
                                  "Organization", "Account Name")
                records.append({
                    "email":        email,
                    "name_norm":    norm_text(name),
                    "company_norm": norm_text(company),
                })

    print(f"  Relationships loaded: {len(records):>5} entries  ({p.name})")
    return records


# ── Match logic ────────────────────────────────────────────────────────────

FUZZY_THRESHOLD = 0.82   # SequenceMatcher ratio for name+company match

def check_against_dnc(email_norm, dnc_emails):
    """Returns (matched: bool, reason: str)."""
    if email_norm and email_norm in dnc_emails:
        return True, f"Email exact match in DNC"
    return False, ""


def check_against_relationships(email_norm, name_norm, company_norm, relationships,
                                threshold=FUZZY_THRESHOLD):
    """
    Two-pass check:
      Pass 1 — email exact match
      Pass 2 — name + company fuzzy match (both must exceed threshold)
    Returns (matched: bool, reason: str, matched_record: dict|None)
    """
    for rel in relationships:
        # Pass 1: email
        if email_norm and rel["email"] and email_norm == rel["email"]:
            return True, "Email exact match in relationships", rel

        # Pass 2: fuzzy name + company (both required)
        if rel["name_norm"] and name_norm:
            name_score = similarity(name_norm, rel["name_norm"])
            if name_score >= FUZZY_THRESHOLD:
                if rel["company_norm"] and company_norm:
                    company_score = similarity(company_norm, rel["company_norm"])
                    if company_score >= FUZZY_THRESHOLD:
                        return (True,
                                f"Fuzzy match: name {name_score:.0%}, "
                                f"company {company_score:.0%}",
                                rel)
                elif not rel["company_norm"]:
                    return (True,
                            f"Fuzzy name match {name_score:.0%} (no company on file)",
                            rel)

    return False, "", None


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ArgusReach — Relationship & DNC Deduplication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--prospects",     required=True,
                        help="Apollo prospect CSV to clean")
    parser.add_argument("--output",        required=True,
                        help="Clean output CSV path")
    parser.add_argument("--relationships", default=None,
                        help="Client relationships CSV (existing contacts to exclude)")
    parser.add_argument("--dnc",           default=None,
                        help="DNC file (.txt one-per-line or .csv with email column)")
    parser.add_argument("--threshold",     type=float, default=FUZZY_THRESHOLD,
                        help=f"Fuzzy match threshold 0–1 (default: {FUZZY_THRESHOLD})")
    args = parser.parse_args()

    threshold = args.threshold

    print("\nArgusReach Deduplication")
    print(f"  Prospects:  {args.prospects}")
    print(f"  Output:     {args.output}")
    print()

    # Load DNC and relationships
    dnc_emails    = load_dnc(args.dnc)           if args.dnc           else set()
    relationships = load_relationships(args.relationships) \
                                                  if args.relationships else []

    if not dnc_emails and not relationships:
        print("  ℹ  No DNC or relationships provided — running format check only.\n")

    # Read prospects
    prospects_path = Path(args.prospects)
    if not prospects_path.exists():
        print(f"ERROR: Prospects file not found: {args.prospects}")
        sys.exit(1)

    with open(prospects_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        original_fields = list(reader.fieldnames or [])

    total = len(rows)
    print(f"\n  Prospects loaded:     {total:>5} rows\n")

    clean    = []
    removed  = []

    for row in rows:
        # Extract + normalise prospect fields
        fname   = get_col(row, "First Name", "first_name", "firstname")
        lname   = get_col(row, "Last Name",  "last_name",  "lastname")
        name    = get_col(row, "Name", "name") or f"{fname} {lname}".strip()
        company = get_col(row, "Company", "Account Name", "organization")
        email   = get_col(row, "Email", "email", "Work Email",
                          "email address", "Email Address")

        email_norm   = norm_email(email)
        name_norm    = norm_text(name)
        company_norm = norm_text(company)

        removed_flag   = False
        removal_reason = ""
        removal_list   = ""

        # Check DNC first
        if dnc_emails:
            matched, reason = check_against_dnc(email_norm, dnc_emails)
            if matched:
                removed_flag   = True
                removal_reason = reason
                removal_list   = "DNC"

        # Check relationships (only if not already flagged)
        if not removed_flag and relationships:
            matched, reason, rel = check_against_relationships(
                email_norm, name_norm, company_norm, relationships, threshold)
            if matched:
                removed_flag   = True
                removal_reason = reason
                removal_list   = "Existing relationship"

        if removed_flag:
            removed.append({
                **row,
                "_removed_reason": removal_reason,
                "_removed_list":   removal_list,
            })
        else:
            clean.append(row)

    # Write clean output
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=original_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(clean)

    # Write removal log
    log_path = Path(args.output).with_name(
        Path(args.output).stem + "-removed-log.csv"
    )
    if removed:
        log_fields = original_fields + ["_removed_reason", "_removed_list"]
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=log_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(removed)

    # Summary
    dnc_count = sum(1 for r in removed if r["_removed_list"] == "DNC")
    rel_count = sum(1 for r in removed if r["_removed_list"] == "Existing relationship")

    print(f"  ── Results ─────────────────────────────────")
    print(f"  Total prospects:      {total}")
    print(f"  Removed (DNC):        {dnc_count}")
    print(f"  Removed (existing):   {rel_count}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  Clean list:           {len(clean)}  → {args.output}")
    if removed:
        print(f"  Removal log:          {len(removed)}  → {log_path}")
    print()

    if removed:
        print("  Removed contacts:")
        for r in removed:
            fname = get_col(r, "First Name", "first_name", "firstname")
            lname = get_col(r, "Last Name",  "last_name",  "lastname")
            name  = f"{fname} {lname}".strip() or get_col(r, "Name", "name")
            co    = get_col(r, "Company", "Account Name")
            print(f"    ✗  {name} @ {co}  [{r['_removed_list']}]  — {r['_removed_reason']}")
        print()

    print("  Done. Pass exports/prospects-clean.csv to personalize.py.\n")


if __name__ == "__main__":
    main()
