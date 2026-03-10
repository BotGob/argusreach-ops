# ArgusReach Tools

Scripts used in the ArgusReach campaign workflow.

---

## personalize.py — AI Icebreaker Generator

Reads an Apollo.io CSV export and generates a **unique opening line for every prospect** using Claude Haiku. The result drops into your email sequence as `{{custom_opening}}` — the first 1-2 sentences before the standard pitch.

### What it does

For each prospect it knows: name, title, company, industry, location. Claude writes a natural sentence or two that references their specific situation — role, company type, industry, market context — without fabricating anything we can't verify.

**Example output for a PT prospect:**
> Running a physical therapy practice in Clearwater and building a referral pipeline at the same time is a tough balance — most of the physicians in your area aren't going to find you on their own.

**Example output for a wealth management prospect:**
> Most advisors in Tampa built their book entirely on referrals, which works — until the network plateaus and there's no systematic way to replace it.

These are not generic. Two people with different titles, companies, and locations get different openers.

### Cost

~$0.0003 per prospect using Claude Haiku. For 200 prospects: ~$0.06. For 1,000: ~$0.30. Negligible.

---

### Setup

Requires the `ANTHROPIC_API_KEY` in `monitor/.env`. Same key used by `monitor.py`.

```bash
# Add to monitor/.env:
ANTHROPIC_API_KEY=sk-ant-...

pip install anthropic  # already installed on the server
```

> ⚠️ **Pending:** Vito needs to add the Anthropic API key to `monitor/.env` before this script can run. Once added, it's ready to go.

---

### Usage

```bash
python3 personalize.py \
  --input  exports/prospects.csv \
  --output exports/prospects-enriched.csv \
  --client "Bay Harbor Wealth Advisors — a Tampa-based RIA specializing in
            HNW individuals and business owners navigating major wealth
            transitions including business exits, estate planning, and
            portfolio management."
```

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | required | Path to Apollo CSV export |
| `--output` | required | Where to save the enriched CSV |
| `--client` | required | 2-3 sentence description of who is sending and why |
| `--delay` | 0.3 | Seconds between API calls |
| `--limit` | none | Process only first N rows (for testing) |
| `--model` | claude-3-5-haiku-20241022 | Override the Claude model |

**Test run first (5 rows):**

```bash
python3 personalize.py \
  --input exports/prospects.csv \
  --output exports/test-5.csv \
  --client "..." \
  --limit 5
```

Review the 5 outputs. If they look good, run the full list.

---

### Using the output in email sequences

The enriched CSV has a `custom_opening` column. Your email template's first line should be:

```
{{custom_opening}}

[rest of your standard sequence...]
```

In Instantly.ai, map `custom_opening` to a personalization variable. In manual sending, the CSV is ready to copy-paste.

---

### Approval workflow

You do **not** approve every individual email. Instead:

1. **Approve the template once** — the standard sequence body, CTA, sign-off
2. **Approve the instructions once** — review the `--client` description you're giving Claude
3. **Spot-check 10-15 outputs** before launching — scan the `custom_opening` column in the enriched CSV

If the spot-check looks clean, the batch is good. For compliance-sensitive clients (RIAs), send them the spot-check sample for sign-off. One review, not 200.

---

### Column mapping (Apollo export)

The script handles Apollo's common column name variations automatically:

| Data | Apollo columns tried |
|------|---------------------|
| First name | `First Name`, `first_name`, `firstname` |
| Last name | `Last Name`, `last_name`, `lastname` |
| Title | `Title`, `Job Title`, `position` |
| Company | `Company`, `Account Name`, `organization` |
| Industry | `Industry`, `industry`, `sector` |
| City | `City`, `city` |
| State | `State`, `state`, `region` |
| Keywords | `Keywords`, `Technologies`, `Notes` |

If your Apollo export uses different headers, rename the columns in the CSV before running.

---

### Quality notes

- Output quality depends on data quality. A row with no title, company, or industry will get a generic opener. Apollo exports with good data get good openers.
- Keywords/Technologies columns from Apollo (e.g. "Salesforce, HubSpot") give Claude more context and improve output.
- The `--client` description matters a lot. Be specific about who the sender is and what they do. Vague description → vague openers.
