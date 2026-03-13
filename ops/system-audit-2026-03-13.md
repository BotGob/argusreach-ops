# ArgusReach System Audit — March 13, 2026
**Conducted by:** Gob (COO)
**Trigger:** Go-live test of PT Tampa Bay campaign revealed multiple critical bugs

---

## BUGS FOUND & FIXED DURING TEST

### 🔴 BUG 1 — Wrong variable syntax (CRITICAL)
- **What happened:** Templates used `{{first_name}}` and `{{company}}`. Instantly uses camelCase internally. Every variable rendered blank in 3 sent emails.
- **Affected:** Lisa Chase (bounced), Amy Welsh (2 emails), Carmine (1 email)
- **Fix:** Updated all templates to `{{firstName}}` and `{{companyName}}`
- **Prevention:** `validate_campaign.py` now hard-blocks any snake_case variable syntax

### 🔴 BUG 2 — Follow-ups fired immediately (CRITICAL)
- **What happened:** `delay: 4` without `delay_unit` caused Instantly to treat the delay as hours/minutes, not days. Amy got Touch 2 minutes after Touch 1. Silvana (test) same issue.
- **Fix:** All sequences now explicitly set `delay_unit: "days"` on every step
- **Prevention:** `validate_campaign.py` now hard-blocks any step with delay > 0 and missing/wrong delay_unit

### 🔴 BUG 3 — Plain text emails rendered as walls of text (CRITICAL)
- **What happened:** Email bodies used `\n\n` for paragraph breaks. Instantly sends HTML. Plain text line breaks don't render — everything jams into one block.
- **Fix:** All email bodies now use `<p>` HTML tags. Both campaign sequences updated.
- **Prevention:** Process rule — all email bodies must use HTML formatting. UI preview required before launch.

### 🟡 BUG 4 — Monitor lost escalation email body
- **What happened:** When AI escalated a reply, the email body was not stored anywhere. No way to draft a response.
- **Fix:** Escalations now saved to `pending_approvals.json` with full email body. Telegram notification includes prospect's message.
- **Status:** Fixed and restarted

### 🟡 BUG 5 — Monitor reply emails were plain text / jammed formatting
- **What happened:** AI-drafted replies sent as plain text with no spacing. Looked unprofessional.
- **Fix:** Monitor `_send_email()` now sends HTML with `<p>` tags, clean spacing
- **Status:** Fixed and restarted

### 🟡 BUG 6 — AI drafts had em dashes and no consistent signature
- **What happened:** AI replied with em dashes and no signature structure
- **Fix:** Prompt updated with mandatory formatting rules, signature template, no em dashes, Calendly on its own line
- **Status:** Fixed and restarted

---

## CURRENT SYSTEM STATE

### Campaign — PT Tampa Bay Test
| Setting | Value | Status |
|---|---|---|
| Campaign ID | d1b7a0af-ae35-4715-9619-6fd18811c528 | — |
| Status | PAUSED (2) | ✅ Correct — awaiting relaunch |
| stop_on_reply | TRUE | ✅ Reply = sequence stops immediately |
| Sending account | vito@argusreach.com | ✅ |
| Step 1 delay | 0 days | ✅ |
| Step 2 delay | 4 days (delay_unit=days) | ✅ Fixed |
| Step 3 delay | 5 days (delay_unit=days) | ✅ Fixed |
| Variables | {{firstName}}, {{companyName}} | ✅ Fixed |
| Email format | HTML with <p> tags | ✅ Fixed |
| Schedule | 9am–5pm Eastern, Mon–Fri | ✅ Set via UI |
| Clean leads | 18 leads (Lisa + Amy removed) | ✅ |

### Monitor — argusreach-monitor
| Setting | Value | Status |
|---|---|---|
| Process | Running (PID: active) | ✅ |
| Systemd service | DEAD (needs sudo to restart) | ⚠️ See open issues |
| Watching | vito@argusreach.com | ✅ |
| Mode | draft_approval | ✅ |
| Escalation body saved | Yes | ✅ Fixed |
| Reply format | HTML, clean spacing | ✅ Fixed |
| AI signature | Name + Title on own line | ✅ Fixed |
| Em dashes in drafts | Banned | ✅ Fixed |
| DNC list | Active | ✅ |
| Poll interval | Every 10 minutes | ✅ |

### Validator — validate_campaign.py
| Check | Status |
|---|---|
| Wrong variable syntax (snake_case) | ✅ Hard block |
| Unknown variables | ✅ Hard block |
| delay_unit missing or not "days" | ✅ Hard block (just added) |
| Missing first_name on leads | ✅ Hard block |
| Missing company_name on leads | ✅ Hard block |
| Test/placeholder emails | ✅ Hard block |
| Duplicate emails | ✅ Warning |
| Invalid email format | ✅ Hard block |
| Sending account configured | ✅ Check |
| stop_on_reply enabled | ❌ NOT CHECKED — see open issues |
| HTML body format check | ❌ NOT CHECKED — see open issues |

---

## ANSWER TO YOUR DIRECT QUESTION
**"Will a prospect get a follow-up if they already replied?"**

**No — stop_on_reply=TRUE on both campaigns.** The moment Instantly receives a reply from any email address, the sequence halts for that contact. They will not receive Touch 2 or Touch 3. This is confirmed active on the PT campaign. The monitor also calls `instantly_pause_contact()` as a second layer, though Instantly's native stop_on_reply is the primary gate.

---

## OPEN ISSUES (must fix before first real client)

### 🔴 CRITICAL
1. **Monitor systemd service is dead.** The monitor is running as an orphan process. If the server reboots, it dies and nobody knows. Need to fix the systemd service so it restarts automatically.
   - Action: Get elevated access or fix the service unit file to run as user

2. **Carmine (intake@sportsinjurypt.com) received a bad Touch 1** (blank variables). He is still in the real campaign at step 0 complete. He will receive Touch 2 in ~4 days from when Touch 1 was sent (March 13). Touch 2 will have correct variables.
   - Decision needed: Delete and re-add (gets clean Touch 1) vs. leave in (gets Touch 2 with correct variables)

### 🟡 IMPORTANT
3. **Validator does not check stop_on_reply.** A campaign could be launched without it.
   - Action: Add stop_on_reply=true check to `validate_campaign.py` as hard block

4. **Validator does not check HTML body format.** Plain text could slip through.
   - Action: Add check for `<p>` tags in email body

5. **UI test send required before every launch — not in validator.** Cannot be automated, but must be enforced as a process gate.
   - Action: Add to SOP as mandatory checkbox before activation

6. **Timezone shows as None via API** despite being set in UI. Known quirk — does not affect sending (UI setting is live). But validator should warn if timezone not confirmed.

7. **Real campaign leads include Carmine (contacted with bad email).** 17 leads have never been touched. 1 lead (Carmine) has a bad Touch 1 on record.

### 🟢 PROCESS ONLY
8. **Apollo free tier insufficient for client campaigns.** Only produces generic info@ emails. Apollo Basic ($49/mo) required before first real client to get verified owner emails by title.

9. **NeverBounce email verification not yet integrated.** Required before any real client campaign. Target <2% bounce rate. Add as mandatory step in validate flow.

---

## PERMANENT RULES — LOCKED IN

1. **Run `validate_campaign.py` before EVERY campaign activation** — no exceptions, forever
2. **Send test email via Instantly UI before EVERY activation** — verify variables AND formatting in real inbox
3. **All email bodies must use HTML `<p>` tags** — never plain text with `\n\n`
4. **All sequence steps with delay > 0 must set `delay_unit: "days"`** — explicitly, always
5. **Variables must use camelCase**: `{{firstName}}`, `{{companyName}}`, `{{lastName}}`
6. **No em dashes in any email copy** — outbound or reply — use hyphens
7. **stop_on_reply=true on every campaign** — non-negotiable, forever
8. **Timezone and Mon-Fri days set via Instantly UI** — API does not persist these correctly
9. **Monitor must be running and watching client inbox before campaign activation**
10. **All client campaigns require: secondary domain + dedicated Gmail seat** — never primary domain

---

## BEFORE RELAUNCHING PT TAMPA BAY

- [ ] Run `validate_campaign.py d1b7a0af-ae35-4715-9619-6fd18811c528`
- [ ] Confirm UI preview shows "Hey [Name]" and company filled in (already confirmed — ✅)
- [ ] Decide on Carmine: delete/re-add OR leave in
- [ ] Vito explicitly approves relaunch
- [ ] Activate via API: `POST /campaigns/{id}/activate`
