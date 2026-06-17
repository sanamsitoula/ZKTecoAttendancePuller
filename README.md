# ZKTeco Attendance Puller

A Python application that connects to ZKTeco biometric attendance devices, pulls employee and attendance data on a configurable schedule, stores everything in **PostgreSQL**, and provides a full-featured **Web Management Console** with Bikram Sambat (Nepali calendar) support.

---

## Features

### Puller / Scheduler

- Pulls from **multiple ZKTeco devices** simultaneously
- **Embedded APScheduler** runs inside the web process — no separate Windows Service needed
- Configurable pull schedule (default: 5× per day in Nepal time)
- **Idempotent** — re-running never creates duplicate records
- Stores employee names, user IDs, punch types, and Bikram Sambat dates
- Full audit trail in `pull_sessions` table (per device, per run)
- Graceful error handling — one failed device never blocks others
- Rotating log files in `logs/zkteco_puller.log`

### Web Management Console

| Page | What it does |
|---|---|
| **Dashboard** | Live device status, today's punch count, recent attendance with BS dates |
| **Attendance** | Date-range filter (BS + AD), device/name search, Export Excel & PDF |
| **Monthly Report** | Per-employee 16-column ZKBioTime-style report; multi-device; 60-second dedup; filter by directorate/department/section |
| **Monthly Summary** | Aggregate present/absent/on-leave per employee for a BS month; print/PDF A4 landscape |
| **Daily Report** | Present employees with check-in times, department-wise absent list, on-leave list |
| **Leave Management** | Employee leave applications; approve/reject; annual leave allocation; BS datepicker |
| **Holiday Calendar** | Monthly BS calendar grid with public/festival/other holidays; working-day count |
| **Users** | Employee list with filter, sort, CSV export, per-row and bulk delete |
| **Devices** | Add / edit / delete ZKTeco devices; test TCP connectivity |
| **Device Backup** | Download full user + fingerprint backup as JSON |
| **Migrate** | Copy users and fingerprints between two devices |
| **Sync** | Compare device users vs DB; import unknown or push missing |
| **Pull Sessions** | History of every pull run (start, end, rows, status) |
| **Schedule** | View and edit the pull schedule — applies immediately, no restart |
| **Settings** | Manage directorates, departments, sections, units, shifts, and shift rules |

### Bikram Sambat (BS) Calendar

- Every timestamp in the UI shows the BS equivalent
- BS date picker on attendance filter (converts to/from AD automatically)
- BS dates stored in all database tables (`bs_date`, `started_bs`, etc.)
- Monthly report uses BS dates as row labels
- Requires `nepali-datetime` (included in `requirements.txt`)

### Monthly Attendance Report

- Groups punches from **all devices** for each employee into one report
- Deduplicates punches within **60 seconds** (same person, multiple readers)
- Device name shown in brackets: `10:02 (Main Gate)`
- 16 columns matching ZKBioTime format: Work Date, Planned In/Out, Work Time, Time In/Out, Break In/Out, Time, Actual, OT, LateIn, EarlyOut, EarlyIn, LateOut, Remark
- **Print Single** — one employee; **Print All** — every employee, one page each
- Filter by directorate, department, section; search by name or ID
- Sorted by employee ID number

### Leave Management

- Eight standard leave types following Nepal government rules: Home (13d/yr, carries forward up to 60), Sick (12d/yr, carries forward up to 45), Casual (12d/yr, no carry-forward), Maternity (98d), Paternity (15d), Mourning (13d), Study, Unpaid
- BS datepicker auto-calculates working days (skips Saturdays and holidays)
- Annual leave allocation for all employees in one click
- Approve / reject / delete applications with audit trail

### Holiday Calendar

- Monthly Bikram Sambat grid view
- Three holiday types: Public, Festival, Other
- Shows working-day count and total holidays for the month

### Daily Attendance Report

- Defaults to yesterday (previous working day)
- Shows present employees with first check-in time and department
- Department-wise absent list (excludes weekends and holidays)
- On-leave summary cross-referenced with approved leave applications

---

## Quick Start (New Installation)

### 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 – 3.13 | 3.13 tested |
| PostgreSQL | 13+ | Must be running and accessible |
| Git | any | For cloning / updating |
| Network | — | Server must reach ZKTeco device subnet |

### 2. Clone and Install

```powershell
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git C:\ZKTecePuller
cd C:\ZKTecePuller
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure the Database

```powershell
Copy-Item db_config.json.example db_config.json
notepad db_config.json
```

```json
{
  "host": "localhost",
  "port": 5432,
  "dbname": "zkteco",
  "user": "postgres",
  "password": "your_password_here"
}
```

> This is the **only file you need to create**. Everything else (devices, employees, schedule) is managed through the web UI and stored in the database.

### 4. Create the Database

In psql or pgAdmin:

```sql
CREATE DATABASE zkteco;
```

All tables are created automatically on first startup.

### 5. Start the Web UI

```powershell
.\start_web.bat
```

Then open: **http://localhost:8097**

Add your ZKTeco devices via **Dashboard → Add Device**, then click **Pull** to import employees and attendance.

---

## Migrating to Another PC

This is a **full migration** — moves all code, data, device configs, and employee records to a new machine. After migration the old PC can be decommissioned.

### On the SOURCE PC (current machine)

#### Step 1 — Export the database

```powershell
# Creates a complete SQL dump of all tables and data
pg_dump -U postgres -d zkteco -F p -f "C:\zkteco_backup.sql"
```

If `pg_dump` is not in PATH, use the full path:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres -d zkteco -F p -f "C:\zkteco_backup.sql"
```

You will be prompted for the postgres password.

#### Step 2 — Push all code to git

```powershell
cd C:\ZKTecePuller
git add -A
git commit -m "Pre-migration snapshot"
git push
```

#### Step 3 — Copy the backup file to the new PC

Transfer `C:\zkteco_backup.sql` via USB, network share, or any file transfer method.

---

### On the TARGET PC (new machine)

#### Step 1 — Install prerequisites

1. **Python 3.10+** — https://www.python.org/downloads/
   - During install: tick **"Add Python to PATH"**
2. **PostgreSQL 13+** — https://www.postgresql.org/download/windows/
   - Remember the `postgres` superuser password you set
3. **Git** — https://git-scm.com/download/win

#### Step 2 — Clone the repository

```powershell
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git C:\ZKTecePuller
cd C:\ZKTecePuller
```

#### Step 3 — Create the virtual environment and install dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

#### Step 4 — Create `db_config.json`

```powershell
Copy-Item db_config.json.example db_config.json
notepad db_config.json
```

Edit with the PostgreSQL credentials for this new machine:

```json
{
  "host": "localhost",
  "port": 5432,
  "dbname": "zkteco",
  "user": "postgres",
  "password": "your_new_pc_postgres_password"
}
```

> **This is the only file you need to change.** All device configs, employee data, and attendance records come from the database import in the next step.

#### Step 5 — Create the database and import data

```powershell
# Create empty database
& "C:\Program Files\PostgreSQL\16\bin\createdb.exe" -U postgres zkteco

# Import the backup (adjust path to where you copied zkteco_backup.sql)
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -d zkteco -f "C:\zkteco_backup.sql"
```

Both commands will ask for the postgres password.

> If PostgreSQL is installed in a different version folder, adjust the path (e.g., `PostgreSQL\15\bin\`).

#### Step 6 — Start the web UI

```powershell
cd C:\ZKTecePuller
.\start_web.bat
```

Open **http://localhost:8097** — all your devices, employees, and attendance data will be there.

#### Step 7 — Verify

- Dashboard should show all devices and today's attendance count
- Go to **Attendance** and check recent records are present
- Go to **Reports → Monthly** and verify employees appear
- Click **Pull** on any device to confirm network connectivity from the new PC

---

### After Migration — Optional Cleanup

**Make the web UI start automatically on Windows login:**

```powershell
# Create a scheduled task to start the web UI on logon
$action  = New-ScheduledTaskAction -Execute "C:\ZKTecePuller\.venv\Scripts\python.exe" -Argument "-m web.run_web --port 8097" -WorkingDirectory "C:\ZKTecePuller"
$trigger = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0)
Register-ScheduledTask -TaskName "ZKTecoWebUI" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
```

**Or install as Windows Service (optional, requires admin):**

```powershell
powershell -ExecutionPolicy Bypass -File install_service.ps1
```

---

## Company Configuration

Company name, address, and contact details shown in report headers and print layouts are set in `config.py`:

```python
COMPANY_NAME    = "JANAK EDUCATION MATERIALS CENTER"
COMPANY_ADDRESS = "SANOTHIMI, BHAKTAPUR"
COMPANY_EMAIL   = "info@janakedu.org.np"
COMPANY_WEBSITE = "www.janakedu.org.np"
```

Edit these lines directly in `config.py` — no restart needed for templates (Jinja2 reads from disk), but the web process must be restarted for Python-level changes.

---

## Pull Schedule

Times are in **Nepal Time (NPT, UTC+5:45)**. Edit via the web UI at `/schedule` — changes apply immediately.

| Nepal Time | Notes |
|---|---|
| 06:20 | Morning open |
| 07:20 | Shift start |
| 09:20 | Late check-in window |
| 13:20 | After lunch |
| 17:10 | End of day |

---

## Dynamic Configuration

| File | What it controls | In git? | Notes |
|---|---|---|---|
| `db_config.json` | PostgreSQL connection | **No** — gitignored | Copy from `db_config.json.example` |
| `devices.json` | ZKTeco device list (legacy) | **No** — gitignored | Devices are now managed via web UI + DB |
| `config.py` | Schedule, company info, timezones | **Yes** | Edit and commit changes |

`db_config.json` is the **only file** that needs to be created on a new machine. Everything else is either in git or in the database.

---

## Day-to-Day Operations

### Add / edit a device
Go to **Dashboard → Add Device** in the web UI. Changes are saved to the database immediately.

### Trigger an immediate pull
Click the **Pull** button on any device card on the Dashboard.

### View monthly attendance
Go to **Reports → Monthly** — select employee and BS month. Use **Print All** to print all employees in one go.  
Go to **Reports → Monthly Summary** for an aggregate present/absent/leave count per employee.  
Go to **Reports → Daily Report** for yesterday's attendance (change the date to view any day).

### Change the pull schedule
Go to **Schedule** in the web UI — edit and save. Takes effect immediately.

### Update the application

```powershell
cd C:\ZKTecePuller
git pull
# Restart the web UI (close the terminal window and rerun start_web.bat)
```

### Back up the database

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres -d zkteco -f "C:\backups\zkteco_$(Get-Date -Format 'yyyy-MM-dd').sql"
```

---

## Database Schema

```
devices           — id, name, ip_address, port, model, is_active, created_at, created_bs
employees         — id, device_id, uid, user_id, name, privilege, card, created_at, created_bs, updated_bs
global_users      — id, global_user_id, name, department_id, section_id, unit_id, ...
attendance_logs   — id, device_id, employee_id, uid, user_id, name, timestamp (UTC), bs_date,
                    status, punch, punch_label
                    UNIQUE (device_id, uid, timestamp)  ← idempotency key
pull_sessions     — id, device_id, started_at, completed_at, records_pulled, new_inserts,
                    status, error_message, started_bs, completed_bs
directorates      — id, name
departments       — id, name, directorate_id
sections          — id, name, department_id
units             — id, name, section_id
shifts            — id, name, start_time, end_time
shift_rules       — id, shift_id, global_user_id / department_id / section_id / unit_id / directorate_id, from_date, to_date
leave_types       — id, name, code, days_per_year, max_accumulate, carry_forward, is_paid
leave_balances    — id, global_user_id, leave_type_id, bs_year, opening_balance, days_earned, days_taken
leave_applications — id, global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad, days, status
holidays          — id, name, holiday_ad, holiday_bs, holiday_type, description
```

All tables store a Bikram Sambat date column (e.g. `bs_date`, `created_bs`) alongside the AD timestamp.

---

## Project Structure

```
ZKTecePuller/
├── config.py               ← Schedule, company info, timezones, punch labels
├── db.py                   ← Schema, migrations, upsert, attendance queries
├── puller.py               ← ZKTeco SDK: pull, push, delete, backup, migrate
├── main.py                 ← Pull cycle orchestration + CLI flags
├── nepali_utils.py         ← BS↔AD conversion, Jinja2 filters
├── device_manager.py       ← Device CRUD helpers
├── report.py               ← Daily PNG + timeline chart generator
├── scheduler.py            ← APScheduler (used standalone, not by web)
├── windows_service.py      ← Optional Windows Service wrapper
├── install_service.ps1     ← One-command service installer (Admin)
├── start_web.bat           ← Double-click to start web UI on port 8097
│
├── web/
│   ├── app.py              ← All FastAPI routes + embedded scheduler
│   ├── run_web.py          ← Uvicorn entry point
│   ├── helpers.py          ← Shared route utilities
│   ├── flash.py            ← Session flash messages
│   ├── static/
│   │   ├── style.css       ← All UI styles (responsive, print-ready)
│   │   └── bs-datepicker.js← Vanilla JS Bikram Sambat calendar picker
│   └── templates/
│       ├── base.html                       ← Layout, nav, org bar
│       ├── devices.html                    ← Dashboard + device cards
│       ├── attendance.html                 ← Punch log with BS date filter
│       ├── reports_monthly.html            ← Monthly report (list + individual)
│       ├── reports_monthly_print_all.html  ← Print-all employees page
│       ├── reports_monthly_summary.html    ← Monthly aggregate summary
│       ├── reports_daily.html              ← Daily attendance report
│       ├── leaves.html                     ← Leave management
│       ├── calendar.html                   ← Holiday calendar (BS grid)
│       ├── users.html                      ← Employee management
│       ├── settings.html                   ← Org hierarchy + shifts
│       ├── schedule.html                   ← Schedule viewer/editor
│       └── ...                             ← Other templates
│
├── db_config.json.example  ← Copy to db_config.json and set credentials
├── devices.json.example    ← Reference only; devices are managed via web UI
├── requirements.txt
└── README.md
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `db_config.json not found` | `Copy-Item db_config.json.example db_config.json` then edit credentials |
| `FATAL: database "zkteco" does not exist` | `createdb -U postgres zkteco` |
| `password authentication failed` | Check `password` in `db_config.json` |
| Web UI won't start on port 8097 | `start_web.bat` kills any existing process on that port automatically |
| Pages show Internal Server Error after update | Run `start_web.bat` to restart — the server is running old code |
| Leave / Calendar / Daily Report pages give 500 | Schema not applied yet — restart server with `start_web.bat` |
| Daily report shows no data | Reports default to yesterday; pull data via Dashboard first |
| Device shows Offline on Dashboard | Port 4370 must be reachable from the server — check firewall / network |
| `Connection timed out` on pull | Verify `Test-NetConnection -ComputerName <ip> -Port 4370` succeeds |
| Monthly report shows no employees | Pull data from at least one device first via Dashboard |
| `pg_dump` / `psql` not found | Use full path: `C:\Program Files\PostgreSQL\16\bin\pg_dump.exe` |
| Python package install fails | Ensure `.venv\Scripts\activate` was run before `pip install` |
| `pywin32` error (service only) | Run `python .venv\Scripts\pywin32_postinstall.py -install` as Administrator |

---

## License

MIT — free to use and modify.

---

*Built for [Janak Education Materials Center](http://www.janakedu.org.np) — ZKTeco biometric attendance automation.*
