# Data dictionary – Detailed Issue Tracker / `output/issues.csv`

`Not Available` = not stated in the message or screenshot. `Needs Verification` = something was seen but could not be confirmed. A blank number means "not reported", never zero.

## Main columns (required by the report)

| Column | Meaning |
|---|---|
| Record ID | Stable ID (`GPS-00001`). Never reused or renumbered |
| Message Date / Message Time | Original WhatsApp timestamp (phone local time, see *Timezone*) |
| Group/Chat | WhatsApp group name |
| Reported By | Sender as shown in WhatsApp, phone numbers masked |
| CSP Name / CSP ID | As written in the message (CSP ID usually like `1A851811`) |
| State / District / Village/Branch | Only when explicitly written, e.g. `District - Saharsa` |
| Issue Type | Primary category (see ARCHITECTURE.md for the list and priority) |
| Issue Description | Message text with placeholders removed |
| Actual Location / Planned Location | Coordinates only when labelled current/actual or planned/CSP |
| Distance Mismatch (metres) | Distance the DC reported or the app showed (km converted to m) |
| Visit Status | `Not Completed / Not Updated` or `Completed` when stated |
| Evidence Filename | Photos linked to the complaint (clickable in Excel) |
| Message Reference | Source file and line, e.g. `.../chat.txt#L7347` |
| Duplicate Flag | `Unique`, `Possible Duplicate`, or `Original (has possible duplicates)` |
| Confidence | High / Medium / Low, from how specific and complete the evidence is |
| Resolution Status | Open, In Progress, Escalated, Resolved, Closed - Not an Issue (dropdown; editable) |
| Assigned To / Resolution Notes | For the team to fill in (kept across runs) |

## Action columns

| Column | Meaning |
|---|---|
| Suggested Action | What to do for this kind of problem (`config/settings.yaml`) |
| Next Step | `New - take suggested action`, `Follow up - pending N days`, `Escalate - pending N days`, `Done - confirm with DC`, ... |
| Days Pending | Days since the complaint while not resolved |
| Added On | Date the complaint first appeared in the report |

## Supporting columns

| Column | Meaning |
|---|---|
| Reporter Role | Guessed from the sender name (`DC` → District Coordinator) or from the reporter directory |
| Timezone | Timezone assumed for the timestamps |
| All Issue Categories | Every category the message matched |
| Duplicate Of / Duplicate Reason | Which record it may repeat and why |
| Needs Verification / Verification Priority / Verification Reasons | What a person should check, and how urgently |
| Field Sources | Where each extracted value came from (Message, Screenshot OCR, reporter directory) |
| Other Coordinates (unlabelled) | Coordinates seen without an actual/planned label |
| Distance Text Seen | The exact text the distance was read from |
| Images Count / OCR Status / Screenshot OCR Text | Photo evidence and what OCR read from it |
| Resolution Evidence | The reply or later message the status was taken from |
| Auto Resolution Status | Status before any manual edit |
| Evidence Paths | Relative paths under `output/` |
| Data Source | `WhatsApp export`, `WhatsApp Web capture`, `Image only (no chat)`, or `SYNTHETIC TEST DATA` |

## Workbook sheets

| Sheet | Content |
|---|---|
| Executive Summary | KPIs (live formulas), status, issue types, daily trend, processing summary, charts |
| Detailed Issue Tracker | All columns above; filters, frozen header, dropdowns, colour rules |
| Issue Category Analysis | Counts, share, verification and duplicate counts, distances per category |
| District and CSP Analysis | Per district and per CSP ID |
| Resolution Tracker | Status × category, and unresolved cases with Next Step and Suggested Action |
| Data Quality and Verification | Completeness checks, input files, records to verify |
| Evidence Index | Every image, its hash, OCR status and link status |

## visits.csv – SBI CSP Visit App report

| Column | Meaning |
|---|---|
| CSP Code, Visit Date, Circle, Branch Code, DC User, DC Name | As in the app's visit report (`username` → DC User) |
| Visit Score | 0–100 score from the app |
| Visit Done | `Yes` if the score is above 0 (or missing). `No` for score 0: the visit was recorded but not completed, so it is not counted and never closes a complaint |
| Compliance Completed, Closures Completed | YES / NO from the app |
| Duplicate Row | `Yes` = the same visit (CSP + date + app user) repeated in a report. The best-score row is counted once |
| Source File | Which report file the row came from |
