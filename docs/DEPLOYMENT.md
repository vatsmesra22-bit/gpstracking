# Deployment on a company server

Goal: the dashboard runs 24×7 at `http://<server>:8501`, behind a password, and the report rebuilds itself at
10:00 and 15:00 from WhatsApp exports and visit reports dropped into a shared folder.

## What goes where

| | Git repo | Server package zip |
|---|---|---|
| Code, docs, `config/settings.yaml`, `deploy/`, `tests/` | ✅ | ✅ |
| `.env.example` | ✅ | ✅ |
| `.env` (passwords, account lock) | ❌ never commit | ✅ fill in on the server |
| `data/` (record IDs, team edits), `input/` (exports), `output/` | ❌ private data | ✅ copy once |
| `.venv/`, `logs/` | ❌ | ❌ created on the server |

- **First install:** use the server package zip. It carries the data collected so far.
- **Later updates:** use `git pull`. The data folders are never touched by Git.

## Live: indev.eko.in/gps-tracking (eko-deploy)

The live dashboard is https://indev.eko.in/gps-tracking/. It runs on the EKO R730 through the company's `eko-deploy`
tool, from `~/gps-tracking` on the server. The repo is `github.com/vatsmesra22-bit/gpstracking`. `eko-deploy.toml`,
`Dockerfile.eko` and `seed/` in that folder belong to eko-deploy.

To ship a change, push to `main`, then run on your PC (it asks for the server password):

```bash
ssh indev 'cd ~/gps-tracking && git pull --ff-only && eko-deploy deploy gps-tracking --no-prompt && eko-deploy status gps-tracking'
```

| Need | Command (on the server) |
|---|---|
| Go back to the previous release | `eko-deploy rollback gps-tracking` |
| App logs | `eko-deploy logs gps-tracking` |
| Releases and audit | `eko-deploy history gps-tracking` |
| Dashboard password | `eko-deploy secret set gps-tracking DASHBOARD_PASSWORD_SHA256` |

## Docker + nginx (Linux, own server)

This runs three containers:
- **dashboard** (Streamlit)
- **report**: rebuilds at 10:00 and 15:00 IST from the shared folder
- **nginx**: port 80 → dashboard, with WebSockets

Data stays in `input/ output/ data/ logs/` next to `docker-compose.yml`. It is never baked into the image.

```bash
cd ~/gps-tracking                             # wherever the repo was cloned
cp .env.example .env && nano .env             # DASHBOARD_PASSWORD_SHA256=..., OCR_ENABLED=true
mkdir -p input output data logs
sudo mkdir -p /srv/csp-exports                # shared folder the team drops exports into
sudo chown -R 1000:1000 input output data logs
docker compose up -d --build
docker compose run --rm report python scripts/scheduler.py --once   # first report now
```

| Setting (in `.env` or the shell) | Default | Meaning |
|---|---|---|
| `CSP_EXPORTS_DIR` | `/srv/csp-exports` | Host folder with WhatsApp exports and visit reports. It is mounted read-only as `/watch`. |
| `CSP_HTTP_PORT` | `80` | Port nginx listens on |
| `CSP_REPORT_TIMES` | `10:00,15:00` | Report times, in `CSP_TIMEZONE` |
| `CSP_RUN_ON_START` | `true` | Build a report as soon as the report container starts |
| `CSP_UID` / `CSP_GID` | `1000` | User that owns the data folders on the host |

- **Update the code:** run `git pull && docker compose up -d --build`. The data folders are untouched.
- **Logs:** run `docker compose logs -f report` or `docker compose logs -f dashboard`, or look in `logs/process.log`.
- **HTTPS:** add a `listen 443 ssl` server block with the company certificate to `deploy/docker/nginx.conf`, and
  publish port 443 in `docker-compose.yml`.

## 1. Prerequisites (IT)
- **Server:** Windows Server 2019+ or Linux (Ubuntu 22.04+/RHEL 8+). 2 vCPU, 4 GB RAM and 5 GB disk are enough.
- **Python:** 3.11 or 3.12 installed. On Windows, tick *Add python.exe to PATH*.
- **Shared folder:** create one (e.g. `\\fileserver\CSP-Exports` or `/srv/csp-exports`). The team will drop in:
  - WhatsApp exports: `Kiosk Team Eko*.zip`, `WhatsApp Chat*.zip`, `* DC.zip`
  - SBI visit reports: `Visit report*.xlsx`
- **Network:** port **8501** open on the office network only. For HTTPS and a name like
  `cspgps.eko.in`, put IIS/nginx in front as a reverse proxy to `http://127.0.0.1:8501`.
  Proxy WebSockets too (`/_stcore/stream`).

## 2. Install – Windows server
1. Unzip the server package to a **short path**, e.g. `C:\CSP-GPS`.
2. Double-click `Setup.bat` (it creates `.venv`, installs packages and runs the tests).
3. Edit `C:\CSP-GPS\.env`:
   ```
   DASHBOARD_PASSWORD=<choose one>          # or DASHBOARD_PASSWORD_SHA256=<hash>
   CSP_WATCH_FOLDER=\\fileserver\CSP-Exports
   ```
4. Open PowerShell **as administrator** and run:
   ```
   cd C:\CSP-GPS\deploy\windows
   powershell -ExecutionPolicy Bypass -File .\install_tasks.ps1
   ```
   This creates:
   - the task **CSP GPS Dashboard**, which starts at boot and restarts itself if it stops
   - the task **CSP GPS Report**, which runs daily at 10:00 and 15:00
   - a firewall rule for port 8501 (domain/private networks)
5. Open `http://<server-ip>:8501` and enter the password.

To remove everything later, run `deploy\windows\uninstall_tasks.ps1`.

## 2. Install – Linux server
```bash
sudo mkdir -p /opt/csp-gps && sudo chown $USER /opt/csp-gps
unzip CSP-GPS-Server-Package.zip -d /opt/csp-gps && cd /opt/csp-gps/CSP-GPS
bash deploy/linux/setup.sh
nano .env                                   # DASHBOARD_PASSWORD, CSP_WATCH_FOLDER=/srv/csp-exports
sudo bash deploy/linux/install_services.sh  # csp-dashboard.service + csp-report.timer
```

Check that it is running:

```bash
systemctl status csp-dashboard
```

```bash
systemctl list-timers csp-report.timer
```

## 3. Daily use
1. Someone drops the WhatsApp export (Export chat → Include media) and/or the app's visit report into the
   shared folder. There is no need to unzip or rename it.
2. At 10:00 and 15:00 the server imports new files, rebuilds `output/CSP_GPS_Issue_Report.xlsx`, and updates
   the dashboard, including the 🤖 Agent tab with the ready-to-send messages. Open dashboards refresh within
   a minute.
3. If you need it immediately, run the report by hand:
   - Windows: `scripts\daily_update.bat`
   - Linux: `sudo systemctl start csp-report.service`

Reading WhatsApp **Web** directly needs a desktop with the Claude app, the Claude in Chrome extension and a
logged-in WhatsApp Web (see `agent/DAILY_AGENT_TASK.md`). A rack server normally has none of these. That
desktop can instead write its captures into the shared folder, or the team keeps dropping exports.

## 4. Updates (new features)
1. Develop and test on a laptop:

   ```bash
   .venv\Scripts\python -m pytest
   ```

   ```bash
   .venv\Scripts\python -m ruff check .
   ```

   Then commit and push to the company Git repo.
2. Update the server:
   - **Windows**:
     1. `cd C:\CSP-GPS`
     2. `git pull`
     3. `.venv\Scripts\python -m pip install -r requirements.txt`
     4. Restart the task: Task Scheduler → *CSP GPS Dashboard* → End → Run.
   - **Linux**:
     1. `cd /opt/csp-gps/CSP-GPS && git pull`
     2. `.venv/bin/pip install -r requirements.txt`
     3. `sudo systemctl restart csp-dashboard`

Updates never touch `data/`, `input/`, `output/` or `.env`. Record IDs and the team's status edits are kept.

## 5. Backup
Back up these daily:
- `data/` (record IDs, OCR cache, team edits)
- `input/` (original exports)
- `.env`

`output/` can always be rebuilt with `process.py`.

## 6. Security checklist
- [ ] `DASHBOARD_PASSWORD` (or `_SHA256`) is set in `.env` on the server.
- [ ] Port 8501 is reachable only from the office network or VPN, or only through the HTTPS reverse proxy.
- [ ] `.env` and the data folders are readable only by the service account.
- [ ] Phone numbers are already masked in all outputs. Do not add raw exports to Git.
