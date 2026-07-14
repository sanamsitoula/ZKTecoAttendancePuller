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
| **Monthly Summary** | Aggregate present/absent/on-leave per employee for a BS month; sorted by Att. ID (numeric); print/PDF A4 landscape |
| **Daily Attendance** | Present/absent/on-leave for any BS date; name & dept filter; punch-count badge with click-to-expand punch modal; AD date alongside BS; print/PDF/Excel |
| **Absent Report** | Day-wise absent employee list; name/dept filter; print/PDF/Excel |
| **Dept Attendance** | Department-wise present/absent/on-leave summary with per-dept expandable drilldown (employee lists with check-in/out times); % present; print/PDF/Excel |
| **Hajiri Report** | Cross-tab attendance register (Nepali hajiri vivaran) — one row per employee, one column per day; shows present/absent/Saturday/holiday/leave codes; summary columns for OT, late-in, early-out; print-ready A3 landscape |
| **Leave Management** | Employee leave applications; approve/reject; annual leave allocation; BS datepicker |
| **Leave Opening Balance** | Set per-employee opening leave balance for each leave type per BS year; editable grid with all leave types as columns |
| **Kaaj / Field Duty** | Log paid or unpaid field-visit days per employee; shown with color badge in monthly report; filter by year/month/department/type |
| **Manual Attendance** | Add individual punch records directly into `attendance_logs`; bulk upload via Excel (.xlsx); download sample Excel template |
| **Holiday Calendar** | Monthly BS calendar grid with public/festival/other holidays; working-day count |
| **Users** | Two tabs — **Global Users** (sortable columns: Att. ID, Emp ID, Name, Dept, Section, Shift; server-side pagination, search/filter by org, CSV export, print) and **Device Employees** (default sort by Att. ID/UID numeric order; independent server-side pagination via `emp_page` param, migrate to global user, bulk delete) |
| **Devices** | Add / edit / delete ZKTeco devices; per-device Force UDP toggle and Connection Timeout; test TCP connectivity |
| **Device Backup** | Download full user + fingerprint backup as JSON |
| **Migrate** | Copy users and fingerprints between two devices |
| **Sync** | Compare device users vs DB; import unknown or push missing |
| **Pull Sessions** | History of every pull run (start, end, rows, status, full error traceback); diagnostic table explaining common timeout causes |
| **Schedule** | View and edit the pull schedule — applies immediately, no restart |
| **Settings** | Three tabs: Org Hierarchy (Directorates → Departments → Sections → Units), Shifts & Shift Rules, Employee Org Assignment |
| **Audit Log** | Who changed what and when, across every administrative table (devices, Global Users, org hierarchy, shifts/shift rules, leave types/balances/applications, holidays, kaaj, company settings, web users, payroll, pull schedule, auto-attend rules) — filter by table, action, user, record ID, or date range; expand any row to see before/after values |

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
- **Leave-aware**: approved leave applications appear with **leave type name and color** (e.g., "SL — Sick Leave" in red, "HL — Home Leave" in green) in the Remark column
- **Kaaj-aware**: field duty days (paid/unpaid) appear as "Kaaj (Paid)" or "Kaaj (Unpaid)" with a blue/gray row background
- **Manual remarks**: per-day notes entered via the Attendance Day Remarks feature appear in a Note column (no-print)
- **Remark priority**: Weekend → Holiday/Festival → Present + Kaaj → Kaaj (no punch) → Leave → Absent
- Leave type legend shown above the table with color-coded badges
- Summary totals row shows **Working Days**, Present, Absent, Weekend, Holiday, Festival, Leave, Kaaj, Misc, Total Days — with per-type leave breakdown
- **Print Single** — one employee; **Print All** — every employee, one page each
- Filter by directorate, department, section; search by name or ID
- Sorted by employee Att. ID number (numeric); only employees linked to a Global User appear

### Leave Management

- Ten leave types: Home (13d/yr), Sick (12d/yr), Casual (12d/yr, no carry-forward), Maternity (98d), Paternity (15d), Mourning (13d), Study, Unpaid, **Kaaj Paid**, **Kaaj Unpaid**
- Each leave type has a **color code** — shown as colored badges in all reports (monthly, daily, dept-attendance)
- BS datepicker auto-calculates working days (skips Saturdays and holidays)
- Annual leave allocation for all employees in one click
- Approve / reject / delete applications with audit trail
- **Opening Balance** — set per-employee opening balance + earned days per leave type per BS year
- **Kaaj / Field Duty** — dedicated page to log field visits (paid/unpaid) with reason and approver; separate from the leave application flow

### Holiday Calendar

- Monthly Bikram Sambat grid view
- Three holiday types: Public, Festival, Other
- Full CRUD: Add, Edit, and Delete holidays
- Shows working-day count and total holidays for the month
- **Fully linked to reports**: holidays automatically appear in the Monthly Report Remark column ("Holiday" or "Festival"), are excluded from working-day counts, and are counted in the summary totals row

### Daily Attendance Report (`/reports/daily`)

- Defaults to **NPT today** (Asia/Kathmandu) — no more UTC offset issues
- BS date picker with AD date shown alongside; name and department search filter
- **Present employees** table: Att. ID, Department, Section, Check-In, Check-Out, Hours worked; **sorted by Att. ID (numeric)**
- **Punch count badge** — click to open a modal showing every individual punch with AD time, BS time, and label (Check-In / Check-Out / etc.)
- Department-wise absent list (excludes Saturdays and holidays automatically); **sorted by Att. ID (numeric)**
- On-leave summary cross-referenced with approved leave applications; **sorted by Att. ID (numeric)**
- Print / Download PDF (html2pdf, client-side) / Export Excel (4-sheet: Present, Absent, On Leave, Dept Summary)
- All attendance timestamps use `Asia/Kathmandu` timezone — punches before 05:45 are correctly assigned to the same calendar day
- **Deduplication**: groups by `attendance_logs.user_id` so one person on multiple devices, or with multiple employee records sharing the same user_id, is always counted once — matching the Attendance page count exactly
- **Unlinked employees** (registered on a device but not yet linked to a Global User) appear in the Present list using their device name as a fallback; absent list remains Global User-based

### Day-wise Absent Report (`/reports/absent`)

- Select any BS date (defaults to NPT today)
- Search by employee name or department
- Shows: Name, Att. ID, Department, Section; **sorted by Att. ID (numeric)**
- Uses the **same deduplication logic as the Daily Attendance report** — present count and absent list are always consistent
- Absent employees are determined from Global Users minus present and on-leave
- Not generated for Saturdays or holidays (banner shown instead)
- Print / Download PDF / Export Excel

### Department Attendance Report (`/reports/dept-attendance`)

- Select any BS date (defaults to NPT today)
- Uses the **same deduplication logic as the Daily Attendance report** — all counts match across reports
- **Summary table**: Department → Present / On Leave / Absent / Total / % Present
- **Per-department drilldown**: expandable section for each department showing
  - Present employees (Name, Att. ID, Section, Check-In, Check-Out) — **sorted by Att. ID (numeric)**
  - On-leave employees (Name, Att. ID, Leave Type) — **sorted by Att. ID (numeric)**
  - Absent employees (Name, Att. ID, Section) — **sorted by Att. ID (numeric)**
- Print / Download PDF / Export Excel (3 sheets: Dept Summary, Present by Dept, Absent by Dept)

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

#### Data Architecture

All four reports read **directly from `attendance_logs`** — no pre-computation or settlement step is needed.

```
ZKTeco Devices  →  attendance_logs  (single source of truth)
                         │
                         ├── Daily Attendance      (single day — present/absent/on-leave)
                         ├── Absent Report         (day-wise absent list)
                         ├── Dept Attendance       (dept summary + drilldown)
                         ├── Monthly Report        (per-employee 16-col)
                         ├── Monthly Summary       (aggregate totals)
                         └── Hajiri Report         (cross-tab register, any past month)
```

Status codes are computed on-the-fly for each page load using this day-level priority:

| Priority | Condition | Code | Display |
|---|---|---|---|
| 1 | Saturday | `SAT` | शनि |
| 2 | Public Holiday | `PH` | सा |
| 2 | Festival Holiday | `FH` | उत् |
| 2 | National / Optional Holiday | `NH` / `OH` | रा / वै |
| 3 | Has punch records | `P` | √ |
| 4 | Approved leave | leave code | घ / बि / अ … |
| 5 | No punch, working day | `A` | X |

The `attendance_daily` table still exists as an **optional cache** (populated by `settle_month.py`) but no reports depend on it.

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
##
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

## CLI Utility Scripts

Three scripts in the project root handle historical data operations. Run them from inside the virtual environment.

### `pull_month.py` — Pull device data for a past BS month

Connects to all active devices (loaded from the database), pulls all stored records, filters to the requested BS month range in Nepal time, and inserts them into `attendance_logs`.

```bash
# All devices
python pull_month.py 2083 2

# One specific device
python pull_month.py 2083 2 --device GateMiddle
python pull_month.py 2083 2 --device Gmoffice
```

Use this when you need to load attendance data for a past month that was never pulled, or when a device was offline and missed the scheduled pulls.

### `report_month.py` — Verify data in attendance_logs

Queries `attendance_logs` day-by-day for a BS month and prints employee counts and punch totals. Useful to confirm data is present before viewing reports.

```bash
python report_month.py 2083 2
```

Output shows a table of dates → employees present → total punches, plus the browser URLs for both Monthly and Hajiri reports.

### `settle_month.py` — Pre-compute attendance_daily cache (optional)

Runs the settlement engine for a BS month and writes results to `attendance_daily`. This table is no longer required by any report, but pre-computing it can speed up Hajiri Report rendering for large organisations.

```bash
python settle_month.py 2083 2
```

### Typical workflow for a missing past month

```bash
# Step 1: pull raw punches from devices into attendance_logs
python pull_month.py 2083 2

# Step 2: verify data is there
python report_month.py 2083 2

# Step 3: open in browser — no further steps needed
# /reports/hajiri?bs_year=2083&bs_month=2
# /reports/monthly?bs_year=2083&bs_month=2
```

---

## Day-to-Day Operations

### Add / edit a device
Go to **Dashboard → Add Device** in the web UI. Changes are saved to the database immediately.

| Field | Default | Notes |
|---|---|---|
| Force UDP protocol | Off | Enable for iFace302 and older models that timeout on TCP |
| Connection timeout | 10s | Increase to 30–60 if device is slow or on a high-latency link |
| Password | blank | Must match the device's **Comm Key** (Menu → Comm → Comm Key); default is 0 (leave blank) |

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
| `devices` | id, name, ip_address, port, password, model, is_active, **force_udp**, **connection_timeout**, created_at/bs, created_by, updated_by |
| `employees` | id, device_id, uid, user_id, name, privilege, card, global_user_id (FK), created_at/bs |
| `global_users` | id, **global_user_id** (att. device ID), **employee_id** (HR ID), name, privilege, card, **bank_number**, **email**, **phone**, department_id, section_id, unit_id, **shift_id**, **fingerprint_data**, created_by, updated_by |
| `attendance_logs` | id, device_id, employee_id, uid, user_id, name, timestamp (UTC), bs_date, status, punch, punch_label — UNIQUE (device_id, uid, timestamp) |
| `pull_sessions` | id, device_id, started_at, completed_at, records_pulled, new_inserts, status, error_message, **error_detail** (full traceback), started_bs, completed_bs |
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
| `kaaj_records` | id, global_user_id, ad_date, bs_date, is_paid, reason, approved_by, created_by, created_at, updated_at — UNIQUE (global_user_id, ad_date) |
| `attendance_day_remarks` | id, global_user_id, ad_date, bs_date, remark_text, created_by, created_at — UNIQUE (global_user_id, ad_date) |
| `audit_log` | id, table_name, record_id, action (INSERT/UPDATE/DELETE), changed_by, old_data (JSONB), new_data (JSONB), changed_at — see **Audit Log** section below |

**Bold** columns were added in extended migrations (auto-applied on startup). All tables store a BS date column (`bs_date`, `created_bs`, etc.) alongside AD timestamps.

#### New columns added to existing tables (Phase 5 / Phase 6 / Phase 7 / Phase 8 / Phase 10)

| Table | New Columns |
|---|---|
| `leave_types` | `display_code` (Nepali short code: घ/बि/अ/…), `color_code`, `sort_order`, `half_day_allowed`, `applies_to` |
| `leave_applications` | `is_half_day`, `half_day_part` |
| `leave_balances` | `carried_forward`, `annual_allocated` |
| `global_users` | `emp_type` (PERMANENT/CONTRACT/…), `emp_status` (ACTIVE/INACTIVE/…), `join_date`, `level_grade`, `designation` |
| `shifts` | `grace_late_in` (minutes), `grace_early_out` (minutes), `break_minutes` |
| `pull_sessions` | `error_detail` TEXT (full Python traceback on failure), `completed_bs` |
| `devices` | `force_udp` BOOLEAN DEFAULT FALSE (use UDP protocol for iFace302 etc.), `connection_timeout` INTEGER DEFAULT 10 |
| `attendance_logs` | `source` VARCHAR(20) DEFAULT 'device' (tracks manual vs device punches), `manual_note` TEXT |

### Migration Notes (Upgrading from an Earlier Version)

All schema changes are additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — safe to run against an existing database. The web server applies them automatically on startup via `init_schema` (Phases 1–10). No manual SQL needed.

To verify the new columns exist:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'global_users'
ORDER BY ordinal_position;
```

---

## Audit Log

Every write (insert, update, delete) to the administrative/HR/config tables is
automatically recorded — table name, record ID, action, **who made the
change**, and the full before/after row as JSON. View it in the web UI under
**System → Audit Log**, filterable by table, action, user, record ID, or date
range; expand any row's **View** link to see the before/after values side by
side.

### How it works

This is implemented at the **database level** with a single generic trigger
function (`fn_audit_log`), not scattered across application code — so it
catches every write regardless of *how* it happened (web UI, a CLI script
like `create_employee_logins.py`, or a manual `psql` session), and it's
impossible to forget to add logging when a new mutation is added later.

- One shared `audit_log` table stores every entry.
- `fn_audit_log()` is attached as an `AFTER INSERT OR UPDATE OR DELETE`
  trigger to each covered table. It captures the old row (for updates/
  deletes), the new row (for inserts/updates), and figures out "who" from
  the row's own `created_by` / `updated_by` / `deleted_by` columns — so no
  extra plumbing is needed to tell the trigger which user is acting.
- Audit logging is wrapped in its own exception handler: if anything about
  logging an entry ever fails, the triggering write still succeeds. Audit
  logging can never break or roll back a real operation.

### Covered tables

`devices`, `global_users`, `departments`, `sections`, `units`, `directorates`,
`shifts`, `shift_rules`, `leave_types`, `leave_balances`, `leave_applications`,
`holidays`, `holiday_types`, `kaaj_records`, `attendance_day_remarks`,
`company_settings`, `web_users`, `pull_schedule`, `auto_attend_rules`,
`payroll_salary_structures`, `payroll_runs`, `payroll_items`,
`payroll_holiday_ot_rules`.

**Deliberately not row-audited:** `attendance_logs`, `attendance_daily`,
`employees`, and `pull_sessions`. These are bulk-written by every device pull
(potentially thousands of rows, several times a day) — row-level auditing
there would balloon `audit_log`'s size and add trigger overhead to the pull
hot path for little practical benefit. `pull_sessions` already **is** the
audit trail for pulls (see the Pull Sessions page), and attendance rows carry
their own `created_at`/`source` columns.

### Adding audit coverage to a new table

Since this is fully automatic once wired up, extending it to a new table is
a one-line addition — add the table name to `_AUDITED_TABLES` in `db.py`,
and the next server restart (`init_schema`) attaches the trigger
automatically. No new INSERT/UPDATE/DELETE code needs to change.

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
│       ├── leave_opening_balance.html      ← Per-employee leave opening balance grid
│       ├── kaaj.html                       ← Kaaj / Field Duty log
│       ├── manual_attendance.html          ← Manual attendance add / bulk Excel upload
│       ├── calendar.html                   ← Holiday calendar (BS grid)
│       ├── _macros.html                    ← Shared Jinja2 macros (paginate macro with ellipsis)
│       ├── users.html                      ← Global user list with pagination and search
│       ├── user_form.html                  ← Add / edit global user (org, shift, bank, etc.)
│       ├── settings.html                   ← Org hierarchy + shifts
│       ├── schedule.html                   ← Schedule viewer/editor
│       └── ...                             ← Other templates
│
├── pull_month.py           ← CLI: pull device data for a past BS month into attendance_logs
├── report_month.py         ← CLI: verify attendance_logs data for a BS month; show day-by-day summary
├── settle_month.py         ← CLI: pre-compute attendance_daily cache for a BS month (optional)
├── db_config.json.example  ← Copy to db_config.json and set credentials
├── devices.json.example    ← Reference only; devices are managed via web UI
├── fresh_seed.sh           ← Ubuntu: reset + re-seed data for testing
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
| `Connection timed out` on pull (TCP) | Run in PowerShell: `Test-NetConnection -ComputerName <ip> -Port 4370` — if `TcpTestSucceeded: False`, port is blocked by firewall |
| `Connection timed out` on pull (TCP succeeds but pull still times out) | Device accepts TCP socket but ignores ZKTeco protocol — enable **Force UDP** on the device in Edit Device |
| iFace302 always times out | iFace302 defaults to UDP; pyzk defaults to TCP — go to Edit Device → tick **Force UDP protocol** |
| Pull times out even with Force UDP | Another ZKTeco client (ZKTime, ZKAccess) is already connected — close it, then **power-cycle the device** (unplug 10s) and try again |
| Pull times out on large device (many records) | Increase **Connection Timeout** in Edit Device to 30–60 seconds |
| Monthly report shows no employees | Pull data from at least one device first, AND link employees to Global Users via the Users page |
| Hajiri Report shows no data for current month | Trigger a device pull first — the report reads `attendance_logs` directly |
| Hajiri Report shows no data for a past month | Run `python pull_month.py <bs_year> <bs_month>` to pull historical records from devices |
| Hajiri Report shows wrong status (e.g., absent instead of holiday) | Check that the holiday is added in the Holiday Calendar for the correct BS year/month |
| `pull_month.py` shows 0 records in range | Device memory may have been cleared; the device no longer holds records for that month |
| Monthly report shows Holiday = 0 / Leave = 0 even after adding them | Restart the server to pick up the latest code fix; also verify the holiday/leave BS year matches the report month |
| Holiday or leave appears in wrong BS year | The `bs_to_ad` conversion is used at entry time — check that the year you typed in the form is 2083, not 2082 |
| `pg_dump` / `psql` not found (Windows) | Use full path: `C:\Program Files\PostgreSQL\16\bin\pg_dump.exe` |
| `pg_dump` not found (Ubuntu) | `sudo apt install postgresql-client` |
| Python package install fails (Windows) | Ensure `.venv\Scripts\activate` was run before `pip install` |
| Python package install fails (Ubuntu) | Ensure `source .venv/bin/activate` was run before `pip install` |
| `lsof` not found (Ubuntu) | `sudo apt install lsof` |
| `pywin32` error (service only) | Run `python .venv\Scripts\pywin32_postinstall.py -install` as Administrator |
| `ERROR: No matching distribution found for pywin32` (Ubuntu/Linux) | `pywin32` is Windows-only; `requirements.txt` marks it `; sys_platform == 'win32'` so `pip` skips it elsewhere — make sure you're on a version of `requirements.txt` that includes this marker (`git pull`), then re-run `pip install -r requirements.txt` |
| `error: externally-managed-environment` on `pip install` (Ubuntu/Debian) | You're installing into system Python, not the venv — run `source .venv/bin/activate` first, then `pip install -r requirements.txt` |
| New global_users columns missing | Restart the server — `init_schema` adds them automatically via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` |

---

## License

MIT — free to use and modify.

---

*Built for [Janak Education Materials Center](http://www.janakedu.org.np) — ZKTeco biometric attendance automation.*
