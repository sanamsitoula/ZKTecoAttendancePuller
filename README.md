# ZKTeco Attendance Puller

A Python daemon that connects to ZKTeco biometric attendance devices, pulls employee and attendance data **4 times per day**, stores everything in **PostgreSQL**, and generates **daily attendance report images** automatically.

---

## Features

- Pulls from **multiple ZKTeco devices** simultaneously
- Runs on schedule: **00:00 / 06:00 / 12:00 / 18:00 UTC** (4×/day via APScheduler)
- **Idempotent** — re-running never creates duplicate records
- Stores employee names, user IDs, punch types (Check-In / Check-Out)
- Generates **daily PNG report** + **timeline chart** after every pull
- Full **audit trail** in `pull_sessions` table (per device, per run)
- Graceful error handling — one failed device never blocks the others
- Rotating log files (`logs/zkteco_puller.log`)

---

## Devices Configured

| Name   | IP Address   | Port | Model    | Status      |
|--------|--------------|------|----------|-------------|
| Attn1  | 10.10.10.18  | 4370 | MB2000   | Connected   |
| attn2  | 10.10.10.11  | 4370 | iFace302 | Connected   |
| atn3   | 10.10.10.12  | 4370 | unknown  | Connected   |

All three devices respond on TCP port 4370 (verified via ping and socket test).

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.14 tested and working |
| PostgreSQL | 13+ | Must be running and accessible |
| Network | — | Machine must reach `10.10.10.x` subnet |

---

## Step-by-Step Setup

### Step 1 — Verify Device Connectivity

```cmd
ping 10.10.10.18
ping 10.10.10.11
ping 10.10.10.12
```

All should reply. Then verify TCP port 4370 is open:

```python
python -c "
import socket
for ip in ['10.10.10.18','10.10.10.11','10.10.10.12']:
    s = socket.socket()
    s.settimeout(5)
    r = s.connect_ex((ip, 4370))
    s.close()
    print(ip, '4370 =>', 'OPEN' if r==0 else 'CLOSED')
"
```

Expected output:
```
10.10.10.18 4370 => OPEN
10.10.10.11 4370 => OPEN
10.10.10.12 4370 => OPEN
```

### Step 2 — Clone and Install

```cmd
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git
cd ZKTecoAttendancePuller
pip install -r requirements.txt
```

> **Note for Python 3.14 on Windows:** psycopg2-binary 2.9.12+ includes a pre-built wheel for Python 3.14. If the install fails, run:
> ```cmd
> pip install psycopg2-binary --pre
> ```

### Step 3 — Configure Environment

```cmd
copy .env.example .env
```

Edit `.env` with your actual values:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=zkteco
DB_USER=postgres
DB_PASSWORD=your_postgres_password

# Device passwords (leave empty if device has no password set)
DEVICE_PASSWORD_ATTN1=
DEVICE_PASSWORD_ATTN2=
DEVICE_PASSWORD_ATN3=

# Timezone of the physical devices (for timestamp localisation)
# Examples: UTC, Asia/Kathmandu, Asia/Dhaka, Asia/Karachi
DEVICE_TIMEZONE=UTC
```

### Step 4 — Create the Database

```sql
-- Run in psql or pgAdmin
CREATE DATABASE zkteco;
```

The Python app creates all tables automatically on first run.

### Step 5 — Run a Test Pull

```cmd
python test_pull.py
```

This will:
1. Check TCP port 4370 on all 3 devices
2. Connect via ZKTeco SDK and pull users + attendance
3. Write to PostgreSQL (devices, employees, attendance_logs, pull_sessions)
4. Generate today's report PNG in `reports/`

Expected output:
```
=======================================================
  ZKTeco Attendance Puller — Connection Test
=======================================================

[1] TCP Port Check (port 4370)
    Attn1     10.10.10.18:4370  =>  ✓ OPEN
    attn2     10.10.10.11:4370  =>  ✓ OPEN
    atn3      10.10.10.12:4370  =>  ✓ OPEN

[2] ZKTeco SDK Connection + Data Pull
    Attn1     (MB2000)    =>  ✓  707 users, 0 attendance records
    attn2     (iFace302)  =>  ✓  707 users, 194 attendance records
    atn3      (unknown)   =>  ✓  707 users, 48680 attendance records

[3] Full Pull Cycle (DB write + report generation)
    DB schema: ✓ ready
    ...

[4] Verification Queries
    devices            : 3 rows
    employees          : 707 rows
    attendance_logs    : 48874 rows
    pull_sessions (ok) : 3 rows
    today's records    : 194 rows

✓ All checks complete. See reports/ for generated PNG images.
```

### Step 6 — Start the Daemon (Scheduler)

```cmd
python main.py
```

The process stays running and fires pulls at **00:00, 06:00, 12:00, 18:00 UTC** every day.

#### Optional: Run One Cycle Immediately

```cmd
python main.py --run-now
```

#### Optional: Generate Reports for a Past Date

```cmd
python main.py --report 2026-06-01
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

| Code | Label      |
|------|------------|
| 0    | Check-In   |
| 1    | Check-Out  |
| 2    | Break-Out  |
| 3    | Break-In   |
| 4    | OT-In      |
| 5    | OT-Out     |
| 255  | Check-In   |

---

## Report Images

After each pull cycle, two images are saved in `reports/`:

| File | Description |
|---|---|
| `reports/YYYY-MM-DD.png` | Daily summary table: employee name, first check-in, last check-out, duration, device(s) |
| `reports/YYYY-MM-DD_timeline.png` | Timeline scatter chart: every punch plotted per employee per hour |

---

## Adding a New Device

1. Add a `DeviceConfig` entry in `config.py`:

```python
DeviceConfig(
    name="NewDevice",
    ip="10.10.10.20",
    port=4370,
    password=os.getenv("DEVICE_PASSWORD_NEWDEVICE", ""),
    model="SpeedFace-V5L",
),
```

2. Add to `.env`:

```env
DEVICE_PASSWORD_NEWDEVICE=
```

3. Restart `main.py` — the new device is auto-registered in the `devices` table.

---

## Running as a Windows Service (Optional)

Use NSSM (Non-Sucking Service Manager) to run as a background Windows service:

```cmd
nssm install ZKTecoAttendancePuller "C:\Python314\python.exe" "D:\claude_project\ZKTecePuller\main.py"
nssm set ZKTecoAttendancePuller AppDirectory "D:\claude_project\ZKTecePuller"
nssm start ZKTecoAttendancePuller
```

---

## Project Structure

```
ZKTecoAttendancePuller/
├── main.py          ← Entry point + pull cycle orchestration
├── config.py        ← Device list, DB config, schedule hours
├── db.py            ← Schema, upsert, batch insert, pull sessions
├── puller.py        ← ZKTeco SDK connection via pyzk
├── report.py        ← Daily summary PNG + timeline chart generator
├── scheduler.py     ← APScheduler 4×/day cron setup
├── test_pull.py     ← Manual one-shot test runner
├── requirements.txt
├── .env.example
└── README.md
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `Connection timed out` | Check firewall — port 4370 must be open from this machine to the device |
| `fe_sendauth: no password supplied` | Set `DB_PASSWORD` in `.env` |
| `OperationalError: FATAL database not found` | Create the DB: `CREATE DATABASE zkteco;` |
| `attendance records: 0` on Attn1 | Attn1 log may have been cleared — this is normal; other devices have data |
| Report images not generated | Ensure `matplotlib`, `pandas`, `Pillow` are installed |
| Python 3.14 psycopg2 build error | Run `pip install psycopg2-binary --pre` for the pre-built wheel |

---

## License

MIT — free to use and modify.

---

*Built for [beamlab.dev](https://beamlab.dev) — ZKTeco biometric attendance automation.*
