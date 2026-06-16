# ZKTeco Attendance Puller

A Python daemon that connects to ZKTeco biometric attendance devices, pulls employee and attendance data **5 times per day**, stores everything in **PostgreSQL**, and generates **daily attendance report images** automatically.

Runs as a proper **Windows Service** — starts automatically on server boot, survives reboots, and is managed via `services.msc` or PowerShell.

A built-in **Web Management Console** (FastAPI + Jinja2) lets you view attendance, manage devices, sync employees, migrate fingerprints between devices, and more — all from a browser.

---

## Features

### Puller Service

- Pulls from **multiple ZKTeco devices** simultaneously
- Runs as a **Windows Service** with automatic startup on boot
- **Dynamic configuration** — add/remove devices or change DB credentials by editing JSON files, no restart needed
- **Idempotent** — re-running never creates duplicate records
- Stores employee names, user IDs, and punch types (Check-In / Check-Out)
- Generates **daily PNG report** + **timeline chart** after every pull
- Full **audit trail** in `pull_sessions` table (per device, per run)
- Graceful error handling — one failed device never blocks others
- Rotating log files in `logs/zkteco_puller.log`

### Web Management Console

- **Dashboard** — live device status, today's attendance count, recent punches
- **Nepali calendar (BS)** — every AD date/time shows its Bikram Sambat equivalent throughout the entire UI
- **Attendance** — searchable punch log with BS dates; filter by device, employee name, and date range
- **Users** — filter by device, name/ID search, date, sort; export CSV; print; per-row and bulk delete with confirmation
- **Device backup** — download a complete user + fingerprint template backup (JSON) per device
- **Migrate between devices** — copy users and fingerprint templates from one ZKTeco device to another
- **Sync** — compare device users vs. database; import unknown users or push missing users to device
- **Pull sessions** — full history of every pull run with start/end times and row counts
- **Live ping status** — dashboard pings every device in parallel on each page load; shows Online/Offline badge, response time (ms), and last-checked timestamp (NPT + BS)
- **Devices** — add, edit, enable/disable ZKTeco devices; test connectivity

---

## Pull Schedule

Times are in **Nepal Time (Asia/Kathmandu, UTC+5:45)**.

| Nepal Time | UTC   | Notes         |
|------------|-------|---------------|
| 06:20 NPT  | 00:35 | Morning open  |
| 07:20 NPT  | 01:35 | Shift start   |
| 09:20 NPT  | 03:35 | Late check-in |
| 13:20 NPT  | 07:35 | After lunch   |
| 17:10 NPT  | 11:25 | End of day    |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.14 tested and working |
| PostgreSQL | 13+ | Must be running and accessible from the server |
| Network | — | Server must reach the ZKTeco device subnet |
| OS | Windows Server 2016+ or Windows 10+ | Required for Windows Service |

---

## Server Deployment — Step by Step

### Step 1 — Clone the Project

On the target server, open PowerShell and clone (or copy) the project:

```powershell
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git C:\ZKTecePuller
cd C:\ZKTecePuller
```

> If Git is not installed, copy the project folder via RDP, file share, or USB.

---

### Step 2 — Create a Virtual Environment and Install Dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

### Step 3 — Configure Devices

```powershell
Copy-Item devices.json.example devices.json
notepad devices.json
```

Edit `devices.json` — one object per ZKTeco device:

```json
[
  {
    "name": "MainEntrance",
    "ip": "10.10.10.18",
    "port": 4370,
    "password": "",
    "model": "MB2000",
    "is_active": true,
    "connection_timeout": 10
  },
  {
    "name": "OfficeFloor2",
    "ip": "10.10.10.19",
    "port": 4370,
    "password": "1234",
    "model": "iFace302",
    "is_active": true,
    "connection_timeout": 15
  }
]
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Unique label shown in the web UI |
| `ip` | Yes | IP address of the ZKTeco device |
| `port` | No | TCP port — default `4370` |
| `password` | No | Device password — leave `""` if none |
| `model` | No | Device model name (informational) |
| `is_active` | No | `false` to skip without deleting — default `true` |
| `connection_timeout` | No | TCP timeout in seconds — default `10` |

---

### Step 4 — Configure Database

```powershell
Copy-Item db_config.json.example db_config.json
notepad db_config.json
```

```json
{
  "host": "192.168.1.10",
  "port": 5432,
  "dbname": "zkteco",
  "user": "postgres",
  "password": "your_strong_password"
}
```

---

### Step 5 — Set Timezone

```powershell
Copy-Item .env.example .env
notepad .env
```

```env
DEVICE_TIMEZONE=Asia/Kathmandu
SCHEDULER_TIMEZONE=Asia/Kathmandu
```

> Other examples: `UTC` | `Asia/Dhaka` | `Asia/Karachi` | `Asia/Kolkata`

---

### Step 6 — Create the Database

On the PostgreSQL server, run:

```sql
CREATE DATABASE zkteco;
```

All tables are created automatically on first run.

---

### Step 7 — Verify Device Connectivity

From the server, confirm the ZKTeco devices are reachable:

```powershell
Test-NetConnection -ComputerName 10.10.10.18 -Port 4370
```

Should show `TcpTestSucceeded : True` for each device.

---

### Step 8 — Install the Windows Service

Open PowerShell **as Administrator** and run:

```powershell
cd C:\ZKTecePuller
powershell -ExecutionPolicy Bypass -File install_service.ps1
```

This script will:
1. Install all Python dependencies
2. Run the `pywin32` post-install step (required for service support)
3. Check that configuration files exist
4. Register `ZKTecoAttendancePuller` as a Windows Service with **Automatic** startup
5. Start the service immediately

Expected output:
```
=== Step 1: Install Python dependencies ===
Dependencies installed.

=== Step 2: pywin32 post-install ===
pywin32 post-install complete.

=== Step 3: Check configuration files ===
devices.json found.
db_config.json found.

=== Step 4: Register Windows Service ===
Installing service ZKTecoAttendancePuller
Service installed

=== Step 5: Start service ===
Service started.

Service : ZKTeco Attendance Puller
Status  : Running
Startup : Auto

=== Installation complete ===
Schedule : 06:20, 07:20, 09:20, 13:20, 17:10 NPT
Logs     : C:\ZKTecePuller\logs\zkteco_puller.log
Reports  : C:\ZKTecePuller\reports\
```

---

### Step 9 — Start the Web Management Console

Double-click `start_web.bat` — it kills any process already on port 8097, activates the virtual environment, and starts the web UI:

```bat
start_web.bat
```

Then open a browser and go to:

```
http://localhost:8097
```

To start manually from PowerShell:

```powershell
.venv\Scripts\activate
python -m web.run_web --port 8097
```

> The web UI runs **separately** from the Windows Service. The service handles scheduled pulls; the web UI is a management console you open on demand.

---

### Step 10 — Verify Everything is Working

Check the service status:
```powershell
Get-Service ZKTecoAttendancePuller
```

Tail the log:
```powershell
Get-Content C:\ZKTecePuller\logs\zkteco_puller.log -Tail 30
```

Trigger one immediate pull without waiting for schedule:
```powershell
cd C:\ZKTecePuller
python main.py --run-now
```

---

## Web UI — Pages and Features

### Dashboard (`/`)
Shows active device count, today's total punches, and recent attendance events with BS dates.

Every time the dashboard loads, all configured devices are **pinged in parallel** (TCP connect to IP:port). The Devices table shows:
- **Online** (green) / **Offline** (red) badge per device
- Response time in milliseconds (when online)
- Timestamp of the last ping check (NPT + BS)

A filter dropdown lets you view only Online or only Offline devices at a glance.

### Attendance (`/attendance`)
Full punch log for all devices. Filter by device, employee name, and date. Every timestamp shows both NPT (Nepal Time) and its Bikram Sambat equivalent. Client-side search on the table.

### Users (`/users`)

Filter bar with:

| Filter | Description |
|---|---|
| Device | Show employees from one device only |
| Name / ID | Case-insensitive search on name or badge ID |
| Created date | Filter by import date |
| Sort by | Name, User ID, UID, Date added, or Device |
| Order | Ascending or descending |

**Export CSV** — downloads filtered list with both AD and BS date columns.

**Print** — hides filter bar, checkboxes, and buttons for a clean printed output.

**Delete** — per-row delete with confirmation modal; removes from the physical device and database.

**Bulk delete** — multi-select checkboxes to delete multiple employees at once.

### Device Backup (`/devices/<id>/backup`)
Downloads a complete JSON backup of all users and fingerprint templates from a device. Accessible via the **Download Backup** button on the Device Users page.

### Migrate (`/migrate`)
Copies users and fingerprint templates from one ZKTeco device to another:

1. Select the source device and load its user list.
2. Tick the users to migrate (or select all).
3. Select the target device.
4. Submit — the result page shows per-user action (created/updated) and fingerprint transfer counts.

### Sync (`/devices/<id>/sync`)
Compares live device users against the database:
- **Device Only** — users on the terminal not yet in the DB (can be imported)
- **DB Only** — employees in the DB missing from this terminal (can be pushed)

### Devices (`/devices`)
Add, edit, or remove ZKTeco devices. Test connectivity (TCP check to IP:port). Changes are saved to the database and reflected in `devices.json` for the service.

### Pull Sessions (`/pull-sessions`)
Full history of every scheduled and manual pull run: start time (NPT + BS), end time, records pulled, new inserts, and status.

---

## Nepali Calendar (Bikram Sambat)

All dates and timestamps in the web UI show the Bikram Sambat (BS) equivalent alongside the AD date.

| Location | AD display | BS display |
|---|---|---|
| Attendance punch log | `2026-06-16 20:15` | `Ashar 02, 2083 20:15 BS` |
| Users — created date | `2026-06-16 14:30` | `Ashar 02, 2083 14:30 BS` |
| Pull sessions — started/completed | `2026-06-16 14:30` | `Ashar 02, 2083 14:30 BS` |
| Dashboard — recent punches | `2026-06-16 20:15` | `Ashar 02, 2083 20:15 BS` |
| Export CSV | AD datetime | Includes BS datetime column |

All times are shown in **Nepal Time (NPT, UTC+5:45)**. Timestamps are stored as UTC in PostgreSQL and converted for display.

**Requires:** `nepali-datetime` — included in `requirements.txt`.

---

## Dynamic Configuration

All devices and database credentials live in plain JSON files — no code changes or restarts needed for most updates.

| File | Purpose | Restart needed? |
|---|---|---|
| `devices.json` | ZKTeco device list — add, remove, enable/disable | No — reloaded every pull cycle |
| `db_config.json` | PostgreSQL connection — host, port, dbname, user, password | No — reloaded every connection |
| `.env` | Scheduler timezone (`SCHEDULER_TIMEZONE`, `DEVICE_TIMEZONE`) | Yes |

Both JSON files are in `.gitignore` — they stay on the server and are never committed to git.

---

## Day-to-Day Operations

### Add a New Device

Edit `devices.json` and append a new entry — **no restart needed**:

```json
{
  "name": "Warehouse",
  "ip": "10.10.10.22",
  "port": 4370,
  "password": "",
  "model": "SpeedFace-V5L",
  "is_active": true,
  "connection_timeout": 10
}
```

The next pull cycle (or `python main.py --run-now`) picks it up automatically.

### Disable a Device Temporarily

Set `"is_active": false` in `devices.json` — no restart needed.

### Change Database Credentials

Edit `db_config.json` — **no restart needed**. The new credentials are used on the very next pull cycle.

### Modify the Pull Schedule

Edit `SCHEDULE_TIMES` in [config.py](config.py) and restart the service:

```python
SCHEDULE_TIMES = [
    (6,  20),   # 06:20 NPT
    (7,  20),   # 07:20 NPT
    (9,  20),   # 09:20 NPT
    (13, 20),   # 13:20 NPT
    (17, 10),   # 17:10 NPT
]
```

```powershell
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action restart
```

### Generate a Report for a Past Date

```powershell
python main.py --report 2026-06-01
```

### Trigger an Immediate Pull

```powershell
python main.py --run-now
```

---

## Service Management

All commands require an **Administrator** PowerShell session.

```powershell
# Helper script (recommended)
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action start
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action stop
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action restart
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action status
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action remove

# Built-in Windows commands
net start  ZKTecoAttendancePuller
net stop   ZKTecoAttendancePuller
sc query   ZKTecoAttendancePuller

# Direct Python (advanced / debug)
python windows_service.py start
python windows_service.py stop
python windows_service.py restart
python windows_service.py remove
python windows_service.py debug    # run in foreground, Ctrl+C to stop
```

---

## Database Schema

```
devices
├── id, name, ip_address, port, password, model, is_active, created_at

employees
├── id, device_id → devices.id
├── uid (device hardware slot), user_id (badge ID), name, privilege, card
└── UNIQUE (device_id, uid)

attendance_logs
├── id, device_id, employee_id → employees.id
├── uid, user_id, name, timestamp (UTC), status, punch, punch_label
└── UNIQUE (device_id, uid, timestamp)   ← idempotency key

pull_sessions
└── id, device_id, started_at, completed_at, records_pulled, new_inserts, status, error_message
```

### Punch Type Codes

| Code | Label     |
|------|-----------|
| 0    | Check-In  |
| 1    | Check-Out |
| 2    | Break-Out |
| 3    | Break-In  |
| 4    | OT-In     |
| 5    | OT-Out    |
| 255  | Check-In  |

---

## Report Images

After each pull cycle, two images are saved in `reports/`:

| File | Description |
|---|---|
| `reports/YYYY-MM-DD.png` | Daily summary: employee name, first check-in, last check-out, duration, device |
| `reports/YYYY-MM-DD_timeline.png` | Timeline scatter chart: every punch plotted per employee per hour |

---

## Project Structure

```
ZKTecePuller/
├── main.py                  ← Entry point + pull cycle orchestration
├── windows_service.py       ← Windows Service wrapper (install/start/stop)
├── install_service.ps1      ← PowerShell helper: one-command server setup
├── start_web.bat            ← Double-click launcher: kills port 8097, starts web UI
├── config.py                ← load_devices() + load_db_config() + scheduler config
├── scheduler.py             ← APScheduler background scheduler (5×/day)
├── db.py                    ← Schema, upsert, batch insert, pull sessions, employee helpers
├── puller.py                ← ZKTeco SDK: pull, push, delete, backup, migrate
├── device_manager.py        ← Device CRUD helpers used by web UI
├── nepali_utils.py          ← Bikram Sambat conversion + Jinja2 filters
├── report.py                ← Daily summary PNG + timeline chart generator
├── test_pull.py             ← Manual one-shot test runner
│
├── web/
│   ├── app.py               ← FastAPI routes (all web UI pages and API endpoints)
│   ├── run_web.py           ← Uvicorn entry point (supports --port / --host flags)
│   ├── helpers.py           ← Shared utility functions for routes
│   ├── flash.py             ← Session-based flash messages
│   ├── static/
│   │   └── style.css        ← All UI styles (responsive, print-ready)
│   └── templates/
│       ├── base.html        ← Shared layout + nav + client-side table filter JS
│       ├── devices.html     ← Device list + status
│       ├── device_form.html ← Add / edit device form
│       ├── device_users.html← Live terminal user list + backup download
│       ├── users.html       ← Employee list with filters, bulk delete, export CSV
│       ├── user_form.html   ← Add / edit employee form
│       ├── attendance.html  ← Attendance punch log with filters
│       ├── sync.html        ← Device vs. DB comparison
│       ├── migrate.html     ← Fingerprint/user migration form
│       ├── migrate_result.html ← Migration result per user
│       ├── pull_sessions.html  ← Pull run history
│       ├── pull_result.html    ← Single pull run detail
│       ├── bulk_enroll.html    ← Bulk enroll form
│       ├── bulk_result.html    ← Bulk enroll result
│       └── schedule.html    ← Scheduler status
│
├── devices.json             ← [CREATE ON SERVER] ZKTeco device list — gitignored
├── devices.json.example     ← Template: copy to devices.json and edit
├── db_config.json           ← [CREATE ON SERVER] DB connection — gitignored
├── db_config.json.example   ← Template: copy to db_config.json and edit
├── .env                     ← [CREATE ON SERVER] Timezone settings — gitignored
├── .env.example             ← Template: copy to .env and set timezones
├── requirements.txt
└── README.md
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Service won't start | Check `logs\zkteco_puller.log` — DB or `devices.json` errors appear there |
| `devices.json not found` | Run `Copy-Item devices.json.example devices.json` and edit it |
| `db_config.json not found` | Run `Copy-Item db_config.json.example db_config.json` and edit it |
| `Connection timed out` on device | Port 4370 must be open from server to device IP — check firewall |
| `FATAL: database "zkteco" does not exist` | Run `CREATE DATABASE zkteco;` in psql or pgAdmin |
| `password authentication failed` | Check `password` field in `db_config.json` |
| Service starts but no pulls at schedule time | Check `SCHEDULER_TIMEZONE` in `.env` — must match the server's local timezone |
| `attendance records: 0` on a device | Device log may have been cleared — normal; other devices still pull |
| pywin32 error on service install | Run `python Scripts\pywin32_postinstall.py -install` as Administrator |
| Python 3.14 psycopg2 build error | Run `pip install psycopg2-binary --pre` for the pre-built wheel |
| Reports not generated | Ensure matplotlib, pandas, Pillow are installed: `pip install -r requirements.txt` |
| Web UI port already in use | `start_web.bat` kills the existing process on port 8097 automatically |
| Fingerprint migration shows 0 ok / N failed | Ensure both devices are online and the source backup contains template data |

---

## License

MIT — free to use and modify.

---

*Built for [beamlab.dev](https://beamlab.dev) — ZKTeco biometric attendance automation.*
