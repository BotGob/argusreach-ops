#!/usr/bin/env python3
"""
ArgusReach — AI Personalization Script
=======================================
Reads an Apollo.io CSV export and generates a unique icebreaker opening
line for each prospect using Claude Haiku (fast, cheap, ~$0.0002/prospect).

The output CSV includes all original columns plus a `custom_opening` column
ready to merge into your email sequence as {{custom_opening}}.

Usage:
  python personalize.py \\
    --input  exports/prospects.csv \\
    --output exports/prospects-enriched.csv \\
    --client "Bay Harbor Wealth Advisors — a Tampa-based RIA that helps
              HNW individuals and business owners navigate major wealth
              transitions. We specialize in post-exit planning, estate
              strategy, and portfolio management."

Optional flags:
  --limit 5       Only process first 5 rows (for testing)
  --delay 0.3     Seconds between API calls (default 0.3)
  --model         Override the Claude model (default: claude-haiku-4-5)

The generated opening is 1-2 sentences. It references the prospect's
role, company type, industry, or location — something specific enough
to feel human, but it never fabricates details we don't have.
"""

import csv
import sys
import time
import os
import argparse
from pathlib import Path

# ── Load .env from monitor/ directory ──────────────────────────────────────
ENV_PATH = Path(__file__).parent.parent / "monitor" / ".env"

def load_env(path=ENV_PATH):
    if path.exists():
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


# ── Column resolver (handles Apollo's inconsistent headers) ────────────────
def get_col(row, *candidates):
    """Return first matching column value (case-insensitive). Empty string if none."""
    lowered = {k.strip().lower(): v for k, v in row.items()}
    for c in candidates:
        val = lowered.get(c.lower(), "")
        if val:
            return val.strip()
    return ""


# ── Prompt builder ─────────────────────────────────────────────────────────
def build_prompt(client_desc, name, title, company, industry, city, state, keywords=""):
    location = ", ".join(filter(None, [city, state]))
    details = "\n".join(filter(None, [
        f"- Name: {name}" if name else None,
        f"- Title: {title}" if title else None,
        f"- Company: {company}" if company else None,
        f"- Industry: {industry}" if industry else None,
        f"- Location: {location}" if location else None,
        f"- Keywords/context: {keywords}" if keywords else None,
    ]))

    return f"""You are writing the opening 1-2 sentences of a cold outreach email.

PROSPECT:
{details}

SENDER (who is reaching out and why):
{client_desc}

Write 1-2 natural sentences to open the email. These appear BEFORE the main pitch.

Rules:
- Sound like a real person wrote it — not AI
- Reference something genuinely specific: their role, company type, industry, market situation, or location
- Never claim you saw their LinkedIn post, recent news, or anything you can't verify
- Don't start with "I", "Hi", or the prospect's name (the greeting is separate)
- No sycophancy ("I'm so impressed by your work...")
- Professional but warm — not corporate-stiff
- 1-2 sentences only. No more.

Output ONLY the opening sentences. No explanation, no quotes, no extra text."""


# ── Core generation ────────────────────────────────────────────────────────
def generate_opening(client, anthropic_client, model, name, title, company,
                     industry, city, state, keywords="", retries=3):
    prompt = build_prompt(client, name, title, company, industry, city, state, keywords)

    for attempt in range(retries):
        try:
            msg = anthropic_client.messages.create(
                model=model,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            return msg.content[0].text.strip()
        except Exception as e:
            err = str(e)
            if "overloaded" in err.lower() or "rate" in err.lower():
                wait = 2 ** attempt
                print(f" [rate limit, waiting {wait}s]", end="", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed after {retries} attempts")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="ArgusReach — AI Personalization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--input",  required=True, help="Apollo CSV export path")
    parser.add_argument("--output", required=True, help="Enriched output CSV path")
    parser.add_argument("--client", required=True,
                        help="Who is doing the outreach and what they do (2-3 sentences)")
    parser.add_argument("--delay",  type=float, default=0.3,
                        help="Seconds between API calls (default: 0.3)")
    parser.add_argument("--limit",  type=int, default=None,
                        help="Only process first N rows — useful for testing")
    parser.add_argument("--model",  default="claude-haiku-4-5",
                        help="Claude model to use (default: claude-haiku-4-5)")
    args = parser.parse_args()

    # Load env + validate API key
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment or monitor/.env")
        sys.exit(1)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)

    # Read input CSV
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        original_fields = list(reader.fieldnames or [])

    if not rows:
        print("ERROR: Input CSV is empty.")
        sys.exit(1)

    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    print(f"\nArgusReach Personalization")
    print(f"  Input:  {input_path.name}  ({total} prospects)")
    print(f"  Output: {args.output}")
    print(f"  Model:  {args.model}")
    print(f"  Est. cost: ~${total * 0.0003:.3f} (Claude Haiku)")
    print()

    enriched = []
    success = 0
    errors = 0

    for i, row in enumerate(rows):
        first  = get_col(row, "First Name", "first_name", "firstname")
        last   = get_col(row, "Last Name", "last_name", "lastname")
        name   = f"{first} {last}".strip() or get_col(row, "Name", "name")
        title  = get_col(row, "Title", "Job Title", "title", "position")
        company= get_col(row, "Company", "Account Name", "organization", "company")
        industry = get_col(row, "Industry", "industry", "sector")
        city   = get_col(row, "City", "city")
        state  = get_col(row, "State", "state", "region")
        keywords = get_col(row, "Keywords", "keywords", "Technologies", "Notes", "notes")

        label = f"{name or '(no name)'} — {title or '?'} @ {company or '?'}"
        print(f"  [{i+1:>3}/{total}] {label[:72]}", end=" ... ", flush=True)

        try:
            opening = generate_opening(
                client=args.client,
                anthropic_client=client,
                model=args.model,
                name=name, title=title, company=company,
                industry=industry, city=city, state=state,
                keywords=keywords
            )
            row["custom_opening"] = opening
            print("✓")
            success += 1
        except Exception as e:
            row["custom_opening"] = ""
            print(f"FAILED — {e}")
            errors += 1

        enriched.append(row)

        if i < total - 1:
            time.sleep(args.delay)

    # Write output
    out_fields = original_fields + (
        ["custom_opening"] if "custom_opening" not in original_fields else []
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched)

    print()
    print(f"Done.  {success}/{total} enriched  ·  {errors} errors")
    print(f"Saved: {args.output}")
    if errors:
        print(f"Note:  {errors} rows have blank custom_opening — "
              "review and fill manually or re-run just those rows.")


if __name__ == "__main__":
    main()
