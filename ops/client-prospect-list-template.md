# ArgusReach — Client Prospect List Template

## How to Use This

If you have existing contacts you'd like us to prioritize or include in your campaign, fill out this spreadsheet and email it to vito@argusreach.com.

These contacts will be reached out to FIRST — before any cold prospecting — because a prior connection means significantly higher response rates.

---

## CSV Template Fields

| Field | Required | Description |
|-------|----------|-------------|
| first_name | Yes | Prospect's first name |
| last_name | Yes | Prospect's last name |
| email | Yes | Business email address |
| company | Yes | Their company name |
| title | Yes | Their job title |
| phone | No | Direct phone (not used for outreach, reference only) |
| connection_type | Yes | How the client knows them (see options below) |
| connection_detail | Recommended | Specific context — event name, who referred them, etc. |
| last_interaction | No | Approximate date of last contact (MM/YYYY) |
| notes | Recommended | Anything specific to reference or be aware of |
| priority | No | High / Medium / Low — defaults to High for all client-provided contacts |

---

## Connection Type Options

| Value | Meaning | Example opener we write |
|-------|---------|------------------------|
| `met_in_person` | Met at an event, conference, or meeting | "Great connecting at [event]..." |
| `linkedin` | LinkedIn connection, no in-person meeting | "We're connected on LinkedIn and I wanted to reach out directly..." |
| `referral` | Someone referred them | "Our mutual connection [Name] suggested I reach out..." |
| `former_client` | Previously worked together | "It's been a while since we worked together at [context]..." |
| `old_lead` | Showed interest previously, went cold | "We spoke briefly [timeframe] ago about [topic]..." |
| `colleague` | Former colleague or industry peer | "We've crossed paths in the [industry] world..." |
| `cold` | No connection — treat same as Apollo prospect | Standard cold outreach |

---

## Example Rows

```csv
first_name,last_name,email,company,title,phone,connection_type,connection_detail,last_interaction,notes,priority
Thomas,Whitfield,t.whitfield@whitfieldco.com,Whitfield Holdings,CEO,,met_in_person,Tampa Bay Business Journal event March 2025,03/2025,Mentioned thinking about estate planning after business sale,High
Sarah,Chen,s.chen@gulfcoastcpa.com,Gulf Coast CPA,Partner,,referral,Referred by David Rodriguez at Suncoast Bank,,"David said she has several business-owner clients who might need wealth management",High
Michael,Russo,m.russo@russolaw.com,Russo & Associates,Managing Partner,,linkedin,,01/2024,Former colleague from Raymond James days — reconnecting,Medium
```

---

## What Happens After You Send the List

1. Go cleans and validates the data (removes duplicates, checks email format)
2. Contacts imported to Airtable under your campaign, tagged `source: client-provided`
3. Grouped by connection type — each group gets a slightly different email opener
4. Outreach goes to these contacts FIRST in your campaign sequence
5. Cold Apollo prospects fill remaining volume up to your plan limit

---

## Important: CAN-SPAM Compliance

By sending us this list, you confirm that:
- You have a legitimate business reason to contact each person
- These are professional contacts, not purchased lists
- You will honor any unsubscribe requests immediately

We add an unsubscribe mechanism to all outreach automatically. Any opt-out is logged and honored permanently.

---

## Data Handling

- Your list is stored only in your Airtable client record
- It is never shared with other clients or used for any purpose other than your campaign
- Upon request or at end of engagement, all data is deleted from our systems
- See your service agreement Section 6 (Confidentiality) and Section 7 (Data)

---

## Template Download

Save the following as `[YourFirm]-prospect-list.csv` and email to vito@argusreach.com:

```
first_name,last_name,email,company,title,phone,connection_type,connection_detail,last_interaction,notes,priority
```
