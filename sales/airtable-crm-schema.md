# ArgusReach — Airtable CRM Schema

## Overview
Two connected bases:
1. **Clients** — ArgusReach's own clients
2. **Prospects** — contacts being outreached on behalf of each client

---

## Base 1: CLIENTS

### Table: Accounts
| Field | Type | Notes |
|-------|------|-------|
| Client Name | Single line | e.g., "Bay Harbor Wealth Advisors" |
| Contact Name | Single line | e.g., "James K." |
| Email | Email | vito's point of contact |
| Phone | Phone | |
| Vertical | Single select | RIA, Insurance, PT, Mental Health, CRE, Professional Services |
| Plan | Single select | Starter, Growth, Scale |
| Status | Single select | Lead, Active, Paused, Churned |
| Monthly MRR | Currency | $750 / $1,500 / $2,500 |
| Setup Fee Paid | Checkbox | |
| Start Date | Date | |
| Contract End | Date | start + 3 months |
| Renewing | Checkbox | |
| Notes | Long text | |
| ICP Doc | Attachment or URL | Link to ICP doc |
| Sending Domain | Single line | e.g., outreach.bayharborwealth.com |
| Instantly Workspace | URL | Link to Instantly campaign |
| Calendar Link | URL | Client's Calendly/Cal.com |

### Table: Monthly Reports
| Field | Type | Notes |
|-------|------|-------|
| Client | Link to Accounts | |
| Month | Date | First of month |
| Emails Sent | Number | |
| Open Rate | Percent | |
| Positive Replies | Number | |
| Reply Rate | Percent | |
| Meetings Booked | Number | |
| Notes | Long text | |
| Report Sent | Checkbox | |

---

## Base 2: PROSPECT PIPELINE

### Table: Prospects
| Field | Type | Notes |
|-------|------|-------|
| First Name | Single line | |
| Last Name | Single line | |
| Email | Email | |
| Title | Single line | |
| Company | Single line | |
| LinkedIn | URL | |
| Location | Single line | |
| Vertical | Single select | matches client vertical |
| Client | Link to Accounts | which ArgusReach client this is for |
| Status | Single select | See status workflow below |
| Sequence | Single select | Which email sequence |
| Touch | Number | 1–5 |
| Last Contacted | Date | |
| Reply Type | Single select | Positive, Negative, OOO, No Reply |
| Reply Notes | Long text | What they said |
| Meeting Booked | Checkbox | |
| Meeting Date | Date | |
| Do Not Contact | Checkbox | |
| Source | Single select | Apollo, Manual, LinkedIn, Referral |
| Added Date | Date | |

### Status Workflow
```
New → Queued → Contacted (T1) → Followed Up (T2) → Followed Up (T3) → Followed Up (T4) → Completed
                                                  ↓
                                             Replied (+) → Meeting Booked → Closed (client reports)
                                             Replied (-) → DNC
                                             Unsubscribed → DNC
```

---

## Views to Create

### Client View
- **Active Clients** — filter: Status = Active
- **Revenue Dashboard** — group by Plan, sum MRR
- **Renewal Watch** — filter: Contract End within 30 days

### Prospect View
- **Hot Leads** — filter: Status = Replied (+) AND Meeting Booked = false
- **By Client** — group by Client
- **DNC List** — filter: Do Not Contact = true
- **This Month's Campaign** — filter: Last Contacted = this month

---

## Setup Notes
1. Create both bases in Airtable (free plan supports this)
2. Link Prospects.Client to Accounts.Client Name
3. Use Airtable automations:
   - When Prospect Reply Type = "Positive" → notify Vito via email
   - When Meeting Booked = true → update status to "Meeting Booked"
4. Optional: connect Instantly webhook to update Prospect status automatically

---

## Quick-Add Template (copy to Apollo export)
When importing from Apollo CSV, map:
- `First Name` → First Name
- `Last Name` → Last Name  
- `Email` → Email
- `Title` → Title
- `Company` → Company
- `City` + `State` → Location (combine)
- `LinkedIn URL` → LinkedIn
- Set Source = "Apollo", Status = "New"
