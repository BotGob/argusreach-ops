#!/usr/bin/env python3
"""
ArgusReach — Approval Sender
=============================
Called by Gob when Vito approves a pending draft.
Reads the approval entry by ID, sends with proper threading headers,
then removes it from the queue.

Usage:
    python3 send_approval.py <approval_id>
    python3 send_approval.py pt_tampa_bay_test:vresciniti27@gmail.com:1773593675
"""

import sys
import json
import smtplib
import email.header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PENDING_FILE = Path(__file__).parent.parent / "monitor" / "logs" / "pending_approvals.json"


def load_pending():
    if not PENDING_FILE.exists():
        return []
    return json.loads(PENDING_FILE.read_text())


def save_pending(pending):
    PENDING_FILE.write_text(json.dumps(pending, indent=2))


def send_reply(entry):
    outreach_email = entry["outreach_email"]
    app_password   = entry["app_password"]
    sender_name    = entry["sender_name"]
    to_email       = entry["from_email"]
    subject        = entry["subject"]
    body           = entry["draft"]
    in_reply_to    = entry.get("in_reply_to", "")
    references     = entry.get("references", in_reply_to)

    # Decode subject if encoded
    decoded = email.header.decode_header(subject)[0][0]
    if isinstance(decoded, bytes):
        decoded = decoded.decode("utf-8", errors="ignore")
    reply_subject = decoded if decoded.lower().startswith("re:") else f"Re: {decoded}"

    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{sender_name} <{outreach_email}>"
    msg["To"]      = to_email
    msg["Subject"] = reply_subject

    # Threading headers — critical for deliverability
    # Without these, Yahoo/Outlook treat replies as new cold emails → spam filter
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = f"{references} {in_reply_to}".strip() if references != in_reply_to else in_reply_to

    # Plain text
    msg.attach(MIMEText(body, "plain"))

    # HTML
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    html_body = "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)
    html = f"""<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;">
{html_body}
</body></html>"""
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(outreach_email, app_password)
        smtp.send_message(msg)

    print(f"✅ Sent to {to_email}")
    if in_reply_to:
        print(f"   In-Reply-To: {in_reply_to[:60]}")
    else:
        print(f"   ⚠️  No In-Reply-To header — original Message-ID not captured (reply may land in spam)")


def main():
    if len(sys.argv) < 2:
        # No ID given — show pending and exit
        pending = load_pending()
        if not pending:
            print("No pending approvals.")
        for i, e in enumerate(pending):
            print(f"\n[{i}] {e['id']}")
            print(f"    From: {e['from_email']}")
            print(f"    Classification: {e['classification']}")
            print(f"    Draft: {e['draft'][:100]}...")
        sys.exit(0)

    approval_id = sys.argv[1]
    pending = load_pending()

    entry = next((e for e in pending if e["id"] == approval_id), None)
    if not entry:
        print(f"❌ Approval ID not found: {approval_id}")
        sys.exit(1)

    send_reply(entry)

    # Remove from queue
    remaining = [e for e in pending if e["id"] != approval_id]
    save_pending(remaining)
    print(f"✅ Removed from queue. {len(remaining)} pending remaining.")


if __name__ == "__main__":
    main()
