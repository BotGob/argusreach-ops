#!/usr/bin/env python3
"""
ArgusReach — Shared Enrichment Module
======================================
Handles company website fetch + Claude Haiku personalized opener generation.
Used by both campaign_create.py (initial launch) and monthly_cycle.py (monthly refresh).

Single source of truth — any fix here propagates to both pipelines.
"""

import os
import re
import time

import requests


# ── Domain normalization ───────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace to plain text."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_domain(raw: str) -> str:
    """
    Normalize a domain or URL to a bare root token for fuzzy comparison.
      deepblue-inv.com          → deepblue
      smithadvisors.com         → smith
      smith-and-associates.com  → smithandassociates
    """
    d = raw.strip().lower()
    d = re.sub(r'^https?://', '', d)
    d = re.sub(r'^www\.', '', d)
    d = d.split('/')[0]
    d = re.sub(r'\.[a-z]{2,6}$', '', d)
    _SUFFIX_RE = r'[-]?(inc|llc|corp|advisors|advisor|group|partners|partner|investments|investment|inv|capital|mgmt|management|consulting|solutions|services)$'
    for _ in range(3):
        d = re.sub(_SUFFIX_RE, '', d)
    d = re.sub(r'[-\s]', '', d)
    return d


def _domain_confidence(email: str, website: str) -> str:
    """
    Compare email domain against website domain (both normalized).
    Returns 'EXACT', 'FUZZY', or 'NO_MATCH'.
    """
    try:
        if not email or '@' not in email:
            return 'NO_MATCH'
        email_domain = email.strip().lower().split('@')[1]
        ne = _normalize_domain(email_domain)
        nw = _normalize_domain(website)
        if not ne or not nw:
            return 'NO_MATCH'
        if ne == nw:
            return 'EXACT'
        shorter, longer = (ne, nw) if len(ne) <= len(nw) else (nw, ne)
        if len(shorter) >= 5 and shorter in longer:
            return 'FUZZY'
        return 'NO_MATCH'
    except Exception:
        return 'NO_MATCH'


# ── Per-contact enrichment ─────────────────────────────────────────────────────

_GENERIC_PHRASES = [
    "i noticed your company",
    "i came across your website",
    "i see that your company",
    "your organization",
    "your business",
]


def enrich_contact(contact: dict, anthropic_api_key: str = "") -> str:
    """
    Fetch a contact's company website and generate a personalized 1-sentence
    cold-email opener using Claude Haiku.

    Validates: domain confidence check → company name presence → generic-phrase rejection.
    Returns the sentence string, or "" on any failure (always non-fatal).
    """
    api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    website = (
        contact.get("organization_website_url", "")
        or contact.get("website_url", "")
        or contact.get("website", "")
        or ""
    ).strip()

    company    = contact.get("company", "") or contact.get("company_name", "")
    first_name = contact.get("first_name", "")
    email      = contact.get("email", "")

    if not website:
        return ""

    if not website.startswith(("http://", "https://")):
        website = "https://" + website

    # Step 1: Domain confidence
    confidence = _domain_confidence(email, website)
    if confidence == 'NO_MATCH':
        print(f"  [enrich] {company}: domain=NO_MATCH → skip")
        return ""

    # Step 2: Fetch homepage
    snippet = ""
    try:
        resp = requests.get(
            website, timeout=5,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
            allow_redirects=True,
        )
        if resp.ok:
            snippet = _strip_html(resp.text)[:500]
    except Exception:
        return ""

    if not snippet:
        return ""

    # Step 3: Company name presence check
    company_in_page = bool(company and company.lower() in snippet.lower())
    if confidence == 'FUZZY' and not company_in_page:
        print(f"  [enrich] {company}: domain=FUZZY, company_in_page=False → skip")
        return ""

    # Step 4: Claude Haiku — generate opener
    try:
        import anthropic as _anthropic
        aclient = _anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"Write a single natural sentence (15-25 words) to open a cold email to "
            f"{first_name} at {company}. "
            f"Use this recent context from their website: {snippet}. "
            f"Make it specific and relevant, not generic. Plain text only, no punctuation at end"
        )
        msg = aclient.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text.strip().rstrip(".!?")
    except Exception:
        return ""

    # Step 5: Generic phrase rejection
    if any(phrase in result.lower() for phrase in _GENERIC_PHRASES):
        print(f"  [enrich] {company}: intro_accepted=False (generic phrase detected)")
        return ""

    print(f"  [enrich] {company}: domain={confidence}, company_in_page={company_in_page}, intro_accepted=True")
    return result


def enrich_contacts(contacts: list, anthropic_api_key: str = "") -> list:
    """
    Enrich each contact with a personalized `custom_intro` opener.
    Adds key `custom_intro` to each contact dict (empty string on failure).
    Rate-limited to 0.3s between requests.
    """
    total = len(contacts)
    print(f"✨ Enriching {total} contacts with personalized intros...")
    for i, contact in enumerate(contacts, 1):
        contact["custom_intro"] = enrich_contact(contact, anthropic_api_key)
        if i % 10 == 0 or i == total:
            filled = sum(1 for c in contacts[:i] if c.get("custom_intro"))
            print(f"   {i}/{total} enriched ({filled} with intros so far)")
        time.sleep(0.3)
    filled_total = sum(1 for c in contacts if c.get("custom_intro"))
    print(f"✅ Enrichment complete: {filled_total}/{total} contacts have a custom intro")
    return contacts
