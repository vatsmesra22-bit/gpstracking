# Changelog

## 1.12.0 – 2026-10-29 – CSP Locations tab

### Added
- **📍 CSP Locations** tab. It shows the lat/long each CSP sent, with:
  - a Google Maps link
  - the CSP's complaints (total / pending)
  - its latest completed SBI app visit
  - a check flag for points outside India or missing
  Phone numbers are masked, and each CSP is shown once (latest row).
- Location lists (.csv / .xlsx with CSP Code + Latitude/Longitude, or one "Lat, Long" column) can be added with the
  upload box. They are saved in `input/csp_locations/`. Code is in `src/csp_gps/locations.py`, and the merged list is
  written to `output/csp_locations.csv`.

## 1.11.1 – 2026-10-29

### Changed
- Overview shows Total, Pending and Solved cards (total = pending + solved); the list below is still only the pending complaints.

## 1.11.0 – 2026-10-29 – low score 85, numbers reconciled

### Changed
- **Low score is now below 85** (it was 80). The cutoff is one constant, `LOW_SCORE` in `visits.py`, used by the
  dashboard cards, the date-wise table, the score bands and the Excel App Visits sheet.
- Executive Summary: the first figure is now "Complaints (repeats removed)", the same number the dashboard shows. The
  old total including repeats / forwards is kept as a separate row.
- A blank or "-" circle in the visit report is shown as `Not Available`.

### Checked
- Pending, solved, total and CSP counts match across the dashboard, the Daily Update sheet, `run_summary.json` and
  `agent_brief.json`. Visit totals by date, circle and DC each add up to "Visits done".

## 1.10.0 – 2026-10-29 – each CSP counted once

### Changed
- App visit totals count **each CSP once**, as the manager counts them. A CSP visited on two days is one visit done.
  Score 0 now lists only CSPs that have no completed visit at all. The dashboard, Excel and `run_summary.json`
  (`visits_done`, `visits_score_zero`) all use the same rule.
- App Visits tab: removed the "Compliance done" and "CSPs visited" cards (the second was the same number as
  "Visits done").

## 1.9.0 – 2026-10-29 – upload box

### Added
- **📤 Naya data daalo** box in the dashboard sidebar (on when `CSP_UPLOADS=1`, as set in `eko-deploy.toml`). It
  takes WhatsApp exports (.zip / .txt) and SBI visit reports (.xlsx / .csv), checks each file, skips one that is
  already there, and rebuilds the report at once. Uploads are logged in `logs/uploads.log`
  (`src/csp_gps/uploads.py`).
- The upload limit is 10 MB, matching the indev nginx route.

## 1.8.0 – 2026-10-29 – pending-only overview, more privacy

### Changed
- **Overview tab** now shows only the pending complaints: one count card plus the full list, most urgent first. The
  other overview cards and lists were removed. The full detail stays in the Complaints tab.
- **Masking** now also hides Aadhaar, PAN, bank account numbers and e-mail addresses, not only phone numbers.
- **CSP onboarding / replacement forms** pasted in the group (Aadhaar, PAN, Gram Panchayat …) are no longer counted as
  complaints.

## 1.7.0 – 2026-10-26 – score 0 visits

### Changed
- A visit with **score 0** was recorded in the app but not completed. It is no longer counted as a done visit, and
  it never auto-closes a complaint. Example: the 1–25 Oct report has 201 visits; 2 have score 0, so 199 are done.
- When the same visit is repeated with different scores, the best-score row is the one counted.

### Added
- `visits.csv` has a new `Visit Done` column (Yes / No). `run_summary.json` has new `visits_done` and
  `visits_score_zero` keys.
- Dashboard App Visits tab: the card now reads "Visits done", and a new "Score 0 (not done)" card opens the list.
- Excel App Visits sheet: a "Score 0 (not done)" count, a list of the score-0 visits, and a `Visit Done` column.

## 1.6.0 – 2026-10-21 – Docker + nginx

### Added
- `Dockerfile`, `docker-compose.yml` and `deploy/docker/nginx.conf`. Three containers:
  - dashboard
  - report (10:00 and 15:00 IST)
  - nginx on port 80, with WebSockets
- `scripts/scheduler.py`: runs `process.py --auto-import` at `CSP_REPORT_TIMES` (default `10:00,15:00`), or once with `--once`.
- The Docker image includes Tesseract, so screenshots are read by OCR on the server (`OCR_ENABLED` in `.env`).

## 1.5.0 – 2026-10-21 – branded login, v2 ready

### Added
- New **login page**: official Eko logo + KIOSK, "Eko Bharat Ventures · Since 2007", gold "Complaints" title,
  styled password box and button.
- **Sign out** button under the header (shown when a password is set). It clears the session and goes back to login.
- `CLAUDE.md`: project notes and rules for AI agents, including the version 2 plan.
- `docs/ROADMAP.md`: version 2 ideas (more agentic) and how to add a feature.
- GitHub Actions CI (`.github/workflows/tests.yml`): lint + tests on Windows and Linux, Python 3.11 and 3.12.

### Changed
- `pyproject.toml` version is now 1.5.0, matching this changelog.
- Removed `plotly` (graphs were removed from the dashboard). Streamlit 1.60 or newer is now required.
- The synthetic data generator creates its own folders, so tests run on a fresh clone.

## 1.4.0 – 2026-10-21 – server deployment

### Added
- Dashboard **password login**: `DASHBOARD_PASSWORD` or `DASHBOARD_PASSWORD_SHA256` in `.env`. No password means
  open, for local use only.
- `deploy/windows/`:
  - `install_tasks.ps1` (dashboard at boot, report at 10:00/15:00, firewall rule)
  - `start_dashboard.bat`
  - `uninstall_tasks.ps1`
- `deploy/linux/`:
  - `setup.sh`, `install_services.sh`
  - systemd units `csp-dashboard.service`, `csp-report.service`, `csp-report.timer`
- `docs/DEPLOYMENT.md`: install, daily use, updates via `git pull`, backup and a security checklist.

## 1.3.0 – 2026-10-21 – agentic

### Added
- **Auto-close.** When the SBI CSP Visit App report shows a visit for the complaint's CSP on or after the
  complaint date, the agent marks the complaint Resolved and records the visit date and score as evidence.
  Statuses set by hand still win.
- **Agent brief** (`output/agent_brief.json` / `.md`) after every run:
  - what changed since the last run (new, closed, auto-closed, newly needing escalation)
  - **ready-to-send WhatsApp drafts**: a daily summary for the manager, CSP lists for tech support and the
    SBI coordinator (one line per CSP), and requests to DCs for missing CSP IDs
  - the agent never sends anything itself
- Dashboard **🤖 Agent** tab with these changes and the drafts, each with a copy button.
- The scheduled agent reads WhatsApp screenshots itself (error text, distance, lat/long) and adds them as
  `[Screenshot] …` lines, so Tesseract is no longer needed for WhatsApp Web captures. Its summary now
  reports the agent brief.
- `agent_contacts` in `config/settings.yaml` (who the drafts are addressed to).
- The dashboard refreshes itself within a minute when new data is written.
- App Visits shows the period, a visit-date filter and a date-wise table with a per-day drill-down (also in Excel).

## 1.2.0 – 2026-10-20

### Added
- **SBI CSP Visit App reports** (`Visit report*.xlsx`):
  - auto-imported to `input/visit_reports/`, written to `output/visits.csv`
  - new Excel sheet **App Visits**
  - repeated rows counted once
- Each complaint shows whether the same CSP was later visited in the app (*App Visit After Complaint*,
  *App Visit Score*). Pending complaints with such a visit get "App visit recorded – confirm and close".
- Dashboard **🗓 App Visits** tab:
  - visits, CSPs, DC users, average and low scores, compliance
  - "complaint CSPs – visited in the app or not"
  - circle, DC and score lists
  - searchable visit table
- Priority on every complaint (🔴 Escalate · 🟣 Confirm & close · 🟠 Follow up · 🔵 New · 🟢 Solved); lists are sorted by it.
- Optional `config/dc_users.csv` (`username,dc_name`) to show DC names for app usernames.

### Changed
- Dashboard: tabs moved to a top navigation bar, one font everywhere (Plus Jakarta Sans), charts replaced by
  simple ranked lists.

## 1.1.0 – 2026-10-20

### Added
- DC one-to-one chats as a source (e.g. "Anil Sharma DC.zip"); auto-import pattern `* DC.zip`.
- New category **Location Error in Visit App** ("visit location error", "location issue").
- Daily-report items marked `(Offline)` count as visits not done in the app.
- Mapping problems count as complaints: "location map", "mapping issue", "remapping".
- Several CSP codes listed above one shared request become one record per CSP.
- Dashboard:
  - 5 cards, including **Escalate now**
  - "Who reported?" chart
  - Chat and Reported-by filters
  - compact "new today" box with a full-list expander
  - district chart only for districts that were actually written
- Excel opens on a simple **Daily Update** sheet.

### Fixed
- Forwarded copies of earlier complaints (e.g. 23 messages re-forwarded on 12 Oct) are flagged as duplicates.
- "Visit done offline" no longer marks a complaint as solved.
- The exporter's own questions without a CSP code ("You: Location me aapne kya update kiya hai") are not complaints.
- An empty export is reported as "empty export – export the chat again".

### Data
- Group export up to 20 Oct and Anil Sharma DC chat. Totals:
  - 66 unique complaints (40 group, 26 Anil Sharma DC), 57 CSPs, 26 possible duplicates flagged
  - 59 pending, 52 to escalate, 7 solved
- "Vijay Maharashtra DC.zip" was empty.

## 1.0.0 – 2026-10-16

### Added
- Pipeline for WhatsApp exports:
  - Android, iOS and Web-copy formats, `.jsonl` web captures, loose images
  - reply quotes linked from `chat.md`
  - safe zip extraction
- Complaint rules tuned on the real "Kiosk Team Eko" export:
  - unlabelled CSP codes (`1A85xxxx`) and misspelt distances ("Miter")
  - multi-CSP lists split into one record per CSP
  - check-ins, notices, form fields and resolution replies excluded
  - "app visit failed" cases included
- Status from quoted replies: "Issue resolved" → Resolved, "Ok" → In Progress, plus escalation requests.
- Suggested Action, Next Step (follow up after 2 days, escalate after 5), Days Pending and Added On.
- 7-sheet Excel workbook with formulas, dropdowns, conditional formatting and charts. Team edits are kept across runs.
- Streamlit dashboard: simple language, Eko Kiosk colours, "Aaj ki nayi complaints", "What to do now", photo viewer, Excel/CSV export.
- Auto-import of new exports from Downloads; `Update Report.bat`; `scripts/daily_update.bat`.
- Scheduled agent (`agent/DAILY_AGENT_TASK.md`) at 10:00 and 15:00. It reads WhatsApp Web via Claude in Chrome, read-only, behind an account lock.
- Standard layout:
  - `config/settings.yaml` and `.env` / `.env.example`
  - `pyproject.toml`, `requirements-dev.txt`, ruff lint
  - `docs/`, `Setup.bat`
- 20 tests (parser formats, extraction rules, reply linking, end-to-end on synthetic data, workbook structure, re-run stability).

### Data
- First real run: 5,541 messages, 1,471 of them from 22 Sep–15 Oct 2026, giving 22 complaints.
