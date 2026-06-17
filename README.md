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
| **Hajiri Report** | Cross-tab attendance register (Nepali hajiri vivaran) — one row per employee, one column per day; shows present/absent/Saturday/holiday/leave codes; summary columns for OT, late-in, early-out; print-ready A3 landscape |
| **Leave Management** | Employee leave applications; approve/reject; annual leave allocation; BS datepicker |
| **Holiday Calendar** | Monthly BS calendar grid with public/festival/other holidays; working-day count |
| **Users** | Two tabs — **Global Users** (sortable columns: Att. ID, Emp ID, Name, Dept, Section, Shift; pagination, search/filter by org, CSV export, print) and **Device Employees** (pagination, migrate to global user, bulk delete) |
| **Devices** | Add / edit / delete ZKTeco devices; test TCP connectivity |
| **Device Backup** | Download full user + fingerprint backup as JSON |
| **Migrate** | Copy users and fingerprints between two devices |
| **Sync** | Compare device users vs DB; import unknown or push missing |
| **Pull Sessions** | History of every pull run (start, end, rows, status) |
| **Schedule** | View and edit the pull schedule — applies immediately, no restart |
| **Settings** | Three tabs: Org Hierarchy (Directorates → Departments → Sections → Units), Shifts & Shift Rules, Employee Org Assignment |

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
- **Holiday-aware**: days marked in the Holiday Calendar appear as "Holiday" or "Festival" in the Remark column; planned hours set to 00:00; excluded from working-day count
- **Leave-aware**: approved leave applications appear as "Leave" in the Remark column for days with no attendance punch
- **Remark priority**: Weekend → Holiday/Festival → Present (has punch) → Leave → Absent
- Summary totals row shows **Working Days**, Present, Absent, Weekend, Holiday, Festival, Leave, Misc, Total Days — with color coding
- **Print Single** — one employee; **Print All** — every employee, one page each
- Filter by directorate, department, section; search by name or ID
- Sorted by employee Attendance ID number; only employees linked to a Global User appear

### Leave Management

- Eight standard leave types following Nepal government rules: Home (13d/yr, carries forward up to 60), Sick (12d/yr, carries forward up to 45), Casual (12d/yr, no carry-forward), Maternity (98d), Paternity (15d), Mourning (13d), Study, Unpaid
- BS datepicker auto-calculates working days (skips Saturdays and holidays)
- Annual leave allocation for all employees in one click
- Approve / reject / delete applications with audit trail

### Holiday Calendar

- Monthly Bikram Sambat grid view
- Three holiday types: Public, Festival, Other
- Full CRUD: Add, Edit, and Delete holidays
- Shows working-day count and total holidays for the month
- **Fully linked to reports**: holidays automatically appear in the Monthly Report Remark column ("Holiday" or "Festival"), are excluded from working-day counts, and are counted in the summary totals row

### Daily Attendance Report

- Defaults to yesterday (previous working day)
- Shows present employees with first check-in time and department
- Department-wise absent list (excludes weekends and holidays)
- On-leave summary cross-referenced with approved leave applications

### Hajiri Report (Attendance Register)

The Hajiri Report is a cross-tab attendance register in traditional Nepali `hajiri vivaran` format.

**Grid layout**: one row per employee, one column per calendar day. Each cell shows a short code:

| Code | Nepali | Meaning |
|---|---|---|
| `√` | उपस्थित | Present (has punch) |
| `X` | अनुपस्थित | Absent (no punch, working day) |
| `शनि` | शनिबार | Saturday |
| `सा` | सार्वजनिक बिदा | Public Holiday |
| `उत्` | उत्सव बिदा | Festival Holiday |
| `रा` | राष्ट्रिय बिदा | National Holiday |
| `वै` | वैकल्पिक बिदा | Optional Holiday |
| `घ` | घर बिदा | Home Leave |
| `बि` | विरामी बिदा | Sick Leave |
| `अ` | आकस्मिक बिदा | Casual Leave |
| `म` | मातृत्व बिदा | Maternity Leave |
| `पि` | पितृत्व बिदा | Paternity Leave |
| `शो` | शोक बिदा | Mourning Leave |
| `अध्` | अध्ययन बिदा | Study Leave |

**Summary columns** on the right: उप. (Present), शनि (Sat), बिदा (Holiday), घ.बि. (Home Leave), बि.बि. (Sick Leave), अ.बि. (Casual Leave), अनु. (Absent), OT (overtime minutes).

**Filters**: BS year/month, department, section, employee type, name search.

**Print**: A3 landscape via browser print. Signature row (Prepared by / Checked by / Approved by) appears at the bottom.

#### Attendance Settlement (Option A architecture)

The Hajiri Report reads from `attendance_daily` — a pre-computed daily summary table. Raw punches stay in `attendance_logs` (source of truth) and are never modified.

After every device pull (scheduled or manual), the system automatically **settles** the last 7 days:

1. Reads `attendance_logs` grouped by NPT date per employee
2. Determines the status for each day using this priority:
   - **Saturday** → `SAT`
   - **Public/Festival/National/Optional Holiday** → `PH`/`FH`/`NH`/`OH`
   - **Has punches** → `P` (present), computes OT, late-in, early-out from shift
   - **Approved leave** → leave type code (`HOME`, `SICK`, etc.)
   - **No punch** → `A` (absent)
3. Upserts into `attendance_daily` with `ON CONFLICT DO UPDATE WHERE source != 'manual'`

Rows with `source = 'manual'` (created by HR via a future override UI) are **never overwritten** by the settlement.

---

## Quick Start (New Installation)

### 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 – 3.14 | 3.14 tested |
| PostgreSQL | 13+ | Must be running and accessible |
| Git | any | For cloning / updating |
| Network | — | Server must reach ZKTeco device subnet |

### 2. Clone and Install

**Windows (PowerShell):**
```powershell
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git C:\ZKTecePuller
cd C:\ZKTecePuller
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Ubuntu / Linux:**
```bash
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git ~/ZKTecePuller
cd ~/ZKTecePuller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure the Database

**Windows:**
```powershell
Copy-Item db_config.json.example db_config.json
notepad db_config.json
```

**Ubuntu / Linux:**
```bash
cp db_config.json.example db_config.json
nano db_config.json
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

### 5. Create Authentication Credentials

**Windows:**
```powershell
Copy-Item users.json.example users.json
notepad users.json
```

**Ubuntu / Linux:**
```bash
cp users.json.example users.json
nano users.json
```

Generate a bcrypt hash for each user's password:

**Windows:**
```powershell
.venv\Scripts\python.exe -c "import bcrypt; print(bcrypt.hashpw(b'your_password', bcrypt.gensalt(12)).decode())"
```

**Ubuntu / Linux:**
```bash
.venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'your_password', bcrypt.gensalt(12)).decode())"
```

Edit `users.json`:

```json
{
  "secret_key": "replace-with-a-long-random-string",
  "users": [
    {
      "id": 1,
      "username": "admin",
      "display_name": "Admin",
      "role": "admin",
      "password_hash": "<paste bcrypt hash here>"
    }
  ]
}
```

> `users.json` is gitignored — **never commit it**. Roles: `"admin"` (full access), `"user"` (read-only, future).

### 6. Start the Web UI

**Windows:**
```powershell
.\start_web.bat
```

**Ubuntu / Linux:**
```bash
# Fix line endings (REQUIRED if the repo was cloned or edited on Windows)
sed -i 's/\r//' start_web.sh

# Make executable (first time only)
chmod +x start_web.sh

# Run
./start_web.sh
```

Then open **http://localhost:8097** and log in with your credentials.

Add your ZKTeco devices via **Dashboard → Add Device**, then click **Pull** to import employees and attendance.

---

## Running on Ubuntu — Full Guide

### Why `start_web.sh` may fail

| Symptom | Cause | Fix |
|---|---|---|
| `/usr/bin/env: 'bash\r': No such file or directory` | Windows CRLF line endings | `sed -i 's/\r//' start_web.sh` |
| `Permission denied` | Not executable | `chmod +x start_web.sh` |
| `python: command not found` | Ubuntu uses `python3` | Script handles this automatically; or `sudo apt install python-is-python3` |
| `lsof: command not found` | lsof not installed | Script falls back to `fuser` (installed by default); or `sudo apt install lsof` |
| `ERROR: Virtual environment not found` | `.venv/` not created | Run `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| `ERROR: db_config.json not found` | Config file missing | `cp db_config.json.example db_config.json && nano db_config.json` |
| `ERROR: users.json not found` | Auth file missing | `cp users.json.example users.json && nano users.json` |

---

### Complete Step-by-Step (fresh Ubuntu server)

#### 1 — Install system dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv postgresql git
# Optional but useful:
sudo apt install -y python-is-python3
```

#### 2 — Start PostgreSQL

```bash
sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo systemctl status postgresql   # should show "active (running)"
```

#### 3 — Create the database

```bash
sudo -u postgres psql -c "CREATE DATABASE zkteco;"
# Optional: set a password for the postgres user
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'yourpassword';"
```

#### 4 — Clone and set up the app

```bash
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git ~/ZKTecePuller
cd ~/ZKTecePuller

# Fix line endings (if cloned on a Windows machine or via GitHub)
sed -i 's/\r//' start_web.sh

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 5 — Create config files

```bash
cp db_config.json.example db_config.json
nano db_config.json
```

Edit with your PostgreSQL details:
```json
{
  "host": "localhost",
  "port": 5432,
  "dbname": "zkteco",
  "user": "postgres",
  "password": "yourpassword"
}
```

```bash
cp users.json.example users.json
```

Generate a bcrypt password hash:
```bash
.venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'your_password', bcrypt.gensalt(12)).decode())"
```

Edit `users.json`:
```json
{
  "secret_key": "replace-with-a-long-random-string",
  "users": [
    {
      "id": 1,
      "username": "admin",
      "display_name": "Admin",
      "role": "admin",
      "password_hash": "<paste the hash above>"
    }
  ]
}
```

#### 6 — Run

```bash
chmod +x start_web.sh
./start_web.sh
```

Open **http://localhost:8097** in a browser.

---

### Manual startup (without the script)

If the script still does not work, run the app directly:

```bash
cd ~/ZKTecePuller
source .venv/bin/activate
python -m web.run_web --port 8097
```

Or with `python3` explicitly:

```bash
cd ~/ZKTecePuller
source .venv/bin/activate
python3 -m web.run_web --port 8097
```

---

### Run in background (keep running after SSH logout)

```bash
# Using nohup
nohup ./start_web.sh > ~/zkteco.log 2>&1 &
echo "PID: $!"

# Or using screen
screen -S zkteco
./start_web.sh
# Detach: Ctrl+A then D
# Reattach: screen -r zkteco
```

---

### Auto-start on boot (systemd)

```bash
sudo nano /etc/systemd/system/zkteco-web.service
```

Paste (replace `YOUR_USERNAME` and path):
```ini
[Unit]
Description=ZKTeco Attendance Web UI
After=network.target postgresql.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/ZKTecePuller
ExecStart=/home/YOUR_USERNAME/ZKTecePuller/.venv/bin/python -m web.run_web --port 8097
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable zkteco-web
sudo systemctl start zkteco-web
sudo systemctl status zkteco-web   # should show "active (running)"
```

View logs:
```bash
sudo journalctl -u zkteco-web -f
```

---

## Bulk-Migrating Device Employees to Global Users

After the first pull from your ZKTeco devices, the `employees` table is populated but the `global_users` table is empty. Reports, leaves, and balances all require Global Users. Use the script in `queries/import_global_users.sql` to create them in bulk instead of adding 600+ employees one by one.

### How it works

| Table | Role |
|---|---|
| `employees` | Raw device data — one row per device per person; pulled automatically |
| `global_users` | Your HR record — org, shift, bank number; created manually or via bulk import |
| `employees.global_user_id` | The link — nullable; set during pull if a match exists, or via this script |

If the same person is enrolled on 3 devices, there are 3 `employees` rows. The import creates **one** `global_users` row and links all 3 employees to it.

### Step-by-step

#### Step 1 — Pull from all devices first

Trigger a pull from every device via Dashboard → Pull button. This populates the `employees` table.

#### Step 2 — Open pgAdmin (Ubuntu) or any SQL client

```bash
# Install pgAdmin on Ubuntu if not available
sudo apt install pgadmin4-web
sudo /usr/pgadmin4/bin/setup-web.sh
# Then open: http://localhost/pgadmin4
```

Or use psql directly:
```bash
PGPASSWORD="your_password" psql -U postgres -d zkteco
```

#### Step 3 — Run the preview query (Step 0 — read-only, no changes)

Open `queries/import_global_users.sql` and run the **Step 0** block only.

```
att_id | chosen_name           | device_rows | devices               | action
-------+-----------------------+-------------+-----------------------+-------------
1      | Yadunathpoudel        | 1           | Gmoffice              | WILL IMPORT
2      | Badri Khatri          | 2           | Gmoffice, Testing     | WILL IMPORT
509    | Basudeb Rokaya        | 2           | Gmoffice, Testing     | SKIP (exists)
```

- **WILL IMPORT** — new global user will be created
- **SKIP (exists)** — already in global_users, will not be touched

#### Step 4 — Run Step 1 (INSERT) then Step 2 (LINK)

```bash
# Run the full file in one go
PGPASSWORD="your_password" psql -U postgres -d zkteco -f queries/import_global_users.sql
```

Or in pgAdmin: highlight each block individually → F5.

#### Step 5 — Verify

Step 3 of the script shows:
- Total employees / linked / still unlinked
- Total global users created
- Any users still missing a name (need manual edit)

#### Step 6 — Fill in org details

The imported global users have **name** and **Att. ID** set. Open **Users → Global Users**, use the Edit button on each row to fill in:
- Employee ID (HR/payroll ID)
- Department, Section, Unit
- Shift
- Phone, bank number

Use the column filters to sort by Department or find blanks quickly.

### Diagnostic queries (if Step 0 returns 0 rows)

```sql
-- Check: are all employees already linked?
SELECT
    count(*)                                    AS total_rows,
    count(global_user_id)                       AS already_linked,
    count(*) - count(global_user_id)            AS unlinked
FROM employees;

-- See actual values
SELECT id, user_id, name, global_user_id
FROM   employees
LIMIT  20;
```

If `unlinked = 0`, all employees were already linked during the last pull (because Global Users already existed with matching Att. IDs).

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

**Make the web UI start automatically on Ubuntu (systemd):**

```bash
sudo nano /etc/systemd/system/zkteco-web.service
```

Paste:
```ini
[Unit]
Description=ZKTeco Attendance Web UI
After=network.target postgresql.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/ZKTecePuller
ExecStart=/home/YOUR_USERNAME/ZKTecePuller/.venv/bin/python -m web.run_web --port 8097
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable zkteco-web
sudo systemctl start zkteco-web
# Check status:
sudo systemctl status zkteco-web
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
| `users.json` | Login credentials + session secret | **No** — gitignored | Copy from `users.json.example`; generate bcrypt hashes |
| `devices.json` | ZKTeco device list (legacy) | **No** — gitignored | Devices are now managed via web UI + DB |
| `config.py` | Schedule, company info, timezones | **Yes** | Edit and commit changes |

`db_config.json` and `users.json` **must be created** on every new machine. Everything else is either in git or in the database.

### Role-Based Access

Two roles are defined in `users.json`:

| Role | Access |
|---|---|
| `"admin"` | Full access — create, edit, delete, approve, all reports |
| `"user"` | Read-only (prepared for future general login) |

Add `"role": "admin"` or `"role": "user"` to each entry in `users.json`. All current users default to `"admin"` if the field is absent.

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
Go to **Reports → Hajiri Report** for the traditional Nepali cross-tab attendance register (A3 landscape, printable).

### Change the pull schedule
Go to **Schedule** in the web UI — edit and save. Takes effect immediately.

### Update the application

**Windows:**
```powershell
cd C:\ZKTecePuller
git pull
.\start_web.bat
```

**Ubuntu / Linux:**
```bash
cd ~/ZKTecePuller
git pull
./start_web.sh
# Or if running as a systemd service:
sudo systemctl restart zkteco-web
```

### Back up the database

**Windows:**
```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres -d zkteco -f "C:\backups\zkteco_$(Get-Date -Format 'yyyy-MM-dd').sql"
```

**Ubuntu / Linux:**
```bash
pg_dump -U postgres -d zkteco -f ~/backups/zkteco_$(date +%F).sql
```

---

## Database Schema

| Table | Key Columns |
|---|---|
| `devices` | id, name, ip_address, port, model, is_active, created_at/bs, created_by, updated_by |
| `employees` | id, device_id, uid, user_id, name, privilege, card, global_user_id (FK), created_at/bs |
| `global_users` | id, **global_user_id** (att. device ID), **employee_id** (HR ID), name, privilege, card, **bank_number**, **email**, **phone**, department_id, section_id, unit_id, **shift_id**, **fingerprint_data**, created_by, updated_by |
| `attendance_logs` | id, device_id, employee_id, uid, user_id, name, timestamp (UTC), bs_date, status, punch, punch_label — UNIQUE (device_id, uid, timestamp) |
| `pull_sessions` | id, device_id, started_at, completed_at, records_pulled, new_inserts, status, error_message, started_bs |
| `directorates` | id, name |
| `departments` | id, name, directorate_id |
| `sections` | id, name, department_id |
| `units` | id, name, section_id |
| `shifts` | id, name, start_time, end_time |
| `shift_rules` | id, shift_id, global_user_id / department_id / section_id / unit_id / directorate_id, from_date, to_date |
| `leave_types` | id, name, code, days_per_year, max_accumulate, carry_forward, is_paid |
| `leave_balances` | id, global_user_id, leave_type_id, bs_year, opening_balance, days_earned, days_taken |
| `leave_applications` | id, global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad, days, status, approved_by, created_by, updated_by |
| `holidays` | id, name, holiday_ad, holiday_bs, holiday_type, description, **holiday_type_id** (FK) |
| `holiday_types` | id, name, type_code (PUB/FEST/NAT/OPT/COMP), color_code, sort_order |
| `attendance_daily` | id, global_user_id, work_date, status_code, display_code, first_in, last_out, work_minutes, ot_minutes, late_in_minutes, early_out_min, source ('device'/'manual'), note, computed_at |

**Bold** columns were added in extended migrations (auto-applied on startup). All tables store a BS date column (`bs_date`, `created_bs`, etc.) alongside AD timestamps.

#### New columns added to existing tables (Phase 5 / Phase 6)

| Table | New Columns |
|---|---|
| `leave_types` | `display_code` (Nepali short code: घ/बि/अ/…), `color_code`, `sort_order`, `half_day_allowed`, `applies_to` |
| `leave_applications` | `is_half_day`, `half_day_part` |
| `leave_balances` | `carried_forward`, `annual_allocated` |
| `global_users` | `emp_type` (PERMANENT/CONTRACT/…), `emp_status` (ACTIVE/INACTIVE/…), `join_date`, `level_grade`, `designation` |
| `shifts` | `grace_late_in` (minutes), `grace_early_out` (minutes), `break_minutes` |

### Migration Notes (Upgrading from an Earlier Version)

All schema changes are additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — safe to run against an existing database. The web server applies them automatically on startup via `init_schema` (Phases 1–6). No manual SQL needed.

To verify the new columns exist:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'global_users'
ORDER BY ordinal_position;
```

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
├── start_web.bat           ← Windows: double-click to start web UI on port 8097
├── start_web.sh            ← Ubuntu/Linux: bash script to start web UI on port 8097
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
│       ├── reports_hajiri.html             ← Hajiri vivaran cross-tab register (A3 landscape print)
│       ├── leaves.html                     ← Leave management
│       ├── calendar.html                   ← Holiday calendar (BS grid)
│       ├── _macros.html                    ← Shared Jinja2 macros (paginate macro with ellipsis)
│       ├── users.html                      ← Global user list with pagination and search
│       ├── user_form.html                  ← Add / edit global user (org, shift, bank, etc.)
│       ├── settings.html                   ← Org hierarchy + shifts
│       ├── schedule.html                   ← Schedule viewer/editor
│       └── ...                             ← Other templates
│
├── db_config.json.example  ← Copy to db_config.json and set credentials
├── devices.json.example    ← Reference only; devices are managed via web UI
├── fresh_seed.sh           ← Ubuntu: reset + re-seed data for testing (Mode 1: partial reset + pull; Mode 2: full wipe + sample data)
├── requirements.txt
├── README.md
│
└── queries/
    └── import_global_users.sql  ← Bulk-create Global Users from device employees; 4-step (preview → insert → link → verify)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `db_config.json not found` | `Copy-Item db_config.json.example db_config.json` then edit credentials |
| `users.json not found` | `Copy-Item users.json.example users.json` then add bcrypt hashes and secret_key |
| `FATAL: database "zkteco" does not exist` | `createdb -U postgres zkteco` |
| `password authentication failed` | Check `password` in `db_config.json` |
| Login says "Invalid username or password" | Re-generate bcrypt hash — Windows: `.venv\Scripts\python.exe -c "import bcrypt; print(bcrypt.hashpw(b'pw', bcrypt.gensalt(12)).decode())"` · Linux: `.venv/bin/python -c "..."` |
| Web UI won't start on port 8097 | `start_web.bat` (Windows) or `./start_web.sh` (Linux) kills any existing process automatically |
| Pages show Internal Server Error after update | Restart the server (`start_web.bat` / `./start_web.sh`) — running old code |
| Leave / Calendar / Report pages give 500 | Restart server — `init_schema` will apply any missing columns |
| `AssertionError: SessionMiddleware must be installed` | Upgrade to latest code and restart — middleware ordering was fixed |
| `/login` returns 500 Internal Server Error | Starlette 1.2.1 changed the `TemplateResponse` API — upgrade to latest code (`git pull`) and restart |
| Daily report shows no data | Reports default to yesterday; pull data via Dashboard first |
| Device shows Offline on Dashboard | Port 4370 must be reachable from the server — check firewall / network |
| `Connection timed out` on pull | Verify `Test-NetConnection -ComputerName <ip> -Port 4370` succeeds |
| Monthly report shows no employees | Pull data from at least one device first, AND link employees to Global Users via the Users page |
| Hajiri Report shows no data | Trigger a device pull first — settlement runs automatically and populates `attendance_daily` |
| Hajiri Report shows wrong status (e.g., absent instead of holiday) | Check that the holiday is added in the Holiday Calendar for the correct BS year/month |
| `attendance_daily` not updating after pull | Check `logs/zkteco_puller.log` for `attendance_daily settled:` lines; errors appear as WARNING entries |
| Monthly report shows Holiday = 0 / Leave = 0 even after adding them | Restart the server to pick up the latest code fix; also verify the holiday/leave BS year matches the report month |
| Holiday or leave appears in wrong BS year | The `bs_to_ad` conversion is used at entry time — check that the year you typed in the form is 2083, not 2082 |
| `pg_dump` / `psql` not found (Windows) | Use full path: `C:\Program Files\PostgreSQL\16\bin\pg_dump.exe` |
| `pg_dump` not found (Ubuntu) | `sudo apt install postgresql-client` |
| Python package install fails (Windows) | Ensure `.venv\Scripts\activate` was run before `pip install` |
| Python package install fails (Ubuntu) | Ensure `source .venv/bin/activate` was run before `pip install` |
| `lsof` not found (Ubuntu) | `sudo apt install lsof` |
| `pywin32` error (service only) | Run `python .venv\Scripts\pywin32_postinstall.py -install` as Administrator |
| New global_users columns missing | Restart the server — `init_schema` adds them automatically via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` |

---

## License

MIT — free to use and modify.

---

*Built for [Janak Education Materials Center](http://www.janakedu.org.np) — ZKTeco biometric attendance automation.*
