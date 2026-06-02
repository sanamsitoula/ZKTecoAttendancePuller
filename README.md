# ZKTeco Attendance Puller

A Python daemon that connects to ZKTeco biometric attendance devices, pulls employee and attendance data **5 times per day**, stores everything in **PostgreSQL**, and generates **daily attendance report images** automatically.

Runs as a proper **Windows Service** — starts automatically on server boot, survives reboots, and is managed via `services.msc` or PowerShell.

---

## Pull Schedule

| Time  | Notes            |
|-------|------------------|
| 06:20 | Morning open     |
| 07:20 | Shift start      |
| 09:20 | Late check-in    |
| 13:20 | After lunch      |
| 17:10 | End of day       |

Times are in **Nepal Time (Asia/Kathmandu, UTC+5:45)** as set by `SCHEDULER_TIMEZONE` in `.env`.

| Nepal Time | UTC Equivalent |
|------------|----------------|
| 06:20 NPT  | 00:35 UTC      |
| 07:20 NPT  | 01:35 UTC      |
| 09:20 NPT  | 03:35 UTC      |
| 13:20 NPT  | 07:35 UTC      |
| 17:10 NPT  | 11:25 UTC      |

---

## Features

- Pulls from **multiple ZKTeco devices** simultaneously
- Runs as a **Windows Service** with automatic startup
- **Idempotent** — re-running never creates duplicate records
- Stores employee names, user IDs, punch types (Check-In / Check-Out)
- Generates **daily PNG report** + **timeline chart** after every pull
- Full **audit trail** in `pull_sessions` table (per device, per run)
- Graceful error handling — one failed device never blocks the others
- Rotating log files in `logs/zkteco_puller.log`
- All credentials in a single `.env` file — change and restart to apply

---

## Devices Configured

| Name   | IP Address   | Port | Model    |
|--------|--------------|------|----------|
| Attn1  | 10.10.10.18  | 4370 | MB2000   |
| attn2  | 10.10.10.11  | 4370 | iFace302 |
| atn3   | 10.10.10.12  | 4370 | unknown  |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.14 tested and working |
| PostgreSQL | 13+ | Must be running and accessible from the server |
| Network | — | Server must reach `10.10.10.x` subnet |
| OS | Windows Server 2016+ or Windows 10+ | Required for Windows Service |

---

## Server Deployment — Step by Step

### Step 1 — Clone the Project

On the target server, open PowerShell and clone (or copy) the project:

```powershell
git clone https://github.com/sanamsitoula/ZKTecoAttendancePuller.git C:\ZKTecePuller
cd C:\ZKTecePuller
```

> If Git is not installed, copy the project folder to the server via RDP, file share, or USB.

---

### Step 2 — Configure Credentials

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit `.env` with your actual values:

```env
# Database — the only section you need to change for a new server
DB_HOST=192.168.1.10        # PostgreSQL server IP or hostname
DB_PORT=5432
DB_NAME=zkteco
DB_USER=postgres
DB_PASSWORD=your_strong_password

# Device passwords (leave empty if devices have no password)
DEVICE_PASSWORD_ATTN1=
DEVICE_PASSWORD_ATTN2=
DEVICE_PASSWORD_ATN3=

# Nepal timezone (UTC+5:45) — adjust if server is in a different timezone
DEVICE_TIMEZONE=Asia/Kathmandu
SCHEDULER_TIMEZONE=Asia/Kathmandu
```

> **Timezone:** Set to `Asia/Kathmandu` (Nepal, UTC+5:45). Other examples: `UTC` | `Asia/Dhaka` | `Asia/Karachi` | `Asia/Kolkata`

---

### Step 3 — Create the Database

On the PostgreSQL server, run:

```sql
CREATE DATABASE zkteco;
```

The Python app creates all tables automatically on first run.

---

### Step 4 — Verify Device Connectivity

From the server, confirm the ZKTeco devices are reachable:

```powershell
Test-NetConnection -ComputerName 10.10.10.18 -Port 4370
Test-NetConnection -ComputerName 10.10.10.11 -Port 4370
Test-NetConnection -ComputerName 10.10.10.12 -Port 4370
```

All should show `TcpTestSucceeded : True`.

---

### Step 5 — Install the Windows Service

Open PowerShell **as Administrator** and run:

```powershell
cd C:\ZKTecePuller
powershell -ExecutionPolicy Bypass -File install_service.ps1
```

This script will:
1. Install all Python dependencies (`pip install -r requirements.txt`)
2. Run the `pywin32` post-install step (required for service support)
3. Check that `.env` exists (prompts you if it does not)
4. Register `ZKTecoAttendancePuller` as a Windows Service
5. Set startup type to **Automatic** (starts on boot)
6. Start the service immediately

Expected output:
```
Python   : C:\Python314\python.exe
Project  : C:\ZKTecePuller

=== Step 1: Install Python dependencies ===
Dependencies installed.

=== Step 2: pywin32 post-install ===
pywin32 post-install complete.

=== Step 3: Check .env configuration ===
.env found.

=== Step 4: Register Windows Service ===
Installing service ZKTecoAttendancePuller
Service installed

=== Step 5: Start service ===
Service started.

Service : ZKTeco Attendance Puller
Status  : Running
Startup : Auto

=== Installation complete ===
Schedule : 06:20, 07:20, 09:20, 13:20, 17:10  (SCHEDULER_TIMEZONE in .env)
Logs     : C:\ZKTecePuller\logs\zkteco_puller.log
Reports  : C:\ZKTecePuller\reports\
```

---

### Step 6 — Verify the Service

In **Services** (`services.msc`):

```
ZKTeco Attendance Puller   Running   Automatic
```

Or via PowerShell:
```powershell
Get-Service ZKTecoAttendancePuller
```

Check the log:
```powershell
Get-Content C:\ZKTecePuller\logs\zkteco_puller.log -Tail 30
```

---

### Step 7 — Run a Manual Test Pull

To trigger one pull cycle immediately (without waiting for the schedule):

```powershell
cd C:\ZKTecePuller
python main.py --run-now
```

Expected output confirms devices connected, records inserted, and reports generated.

---

## Changing Database Credentials

1. Edit `.env` on the server:
   ```powershell
   notepad C:\ZKTecePuller\.env
   ```

2. Update `DB_HOST`, `DB_NAME`, `DB_USER`, or `DB_PASSWORD` as needed.

3. Restart the service to apply:
   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\ZKTecePuller\install_service.ps1 -Action restart
   ```

> Credentials are never stored in code — only in `.env`.

---

## Service Management

All commands require Administrator PowerShell.

```powershell
# Using the helper script
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action start
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action stop
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action restart
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action status
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action remove

# Or using built-in Windows commands
net start  ZKTecoAttendancePuller
net stop   ZKTecoAttendancePuller
sc query   ZKTecoAttendancePuller

# Or using python directly (advanced)
python windows_service.py start
python windows_service.py stop
python windows_service.py restart
python windows_service.py remove
python windows_service.py debug    # run in foreground for troubleshooting
```

---

## Modifying the Schedule

The schedule is defined in [config.py](config.py):

```python
SCHEDULE_TIMES = [
    (6,  20),   # 06:20
    (7,  20),   # 07:20
    (9,  20),   # 09:20
    (13, 20),   # 13:20
    (17, 10),   # 17:10
]
```

After editing, restart the service:
```powershell
powershell -ExecutionPolicy Bypass -File install_service.ps1 -Action restart
```

---

## Adding a New Device

1. Add a `DeviceConfig` entry in [config.py](config.py):

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

3. Restart the service — the new device is auto-registered in the `devices` table.

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

Generate reports for a past date manually:

```powershell
python main.py --report 2026-06-01
```

---

## Project Structure

```
ZKTecePuller/
├── main.py              ← Entry point + pull cycle orchestration
├── windows_service.py   ← Windows Service wrapper (install/start/stop)
├── install_service.ps1  ← PowerShell helper: one-command server setup
├── config.py            ← Devices, DB config, schedule, timezone
├── scheduler.py         ← APScheduler background scheduler setup
├── db.py                ← Schema, upsert, batch insert, pull sessions
├── puller.py            ← ZKTeco SDK connection via pyzk
├── report.py            ← Daily summary PNG + timeline chart generator
├── test_pull.py         ← Manual one-shot test runner
├── requirements.txt
├── .env.example         ← Template — copy to .env and fill in credentials
└── README.md
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Service won't start | Check `logs\zkteco_puller.log` — DB connection errors appear there |
| `Connection timed out` on device | Port 4370 must be open from server to device IP — check firewall |
| `fe_sendauth: no password supplied` | Set `DB_PASSWORD=` in `.env`, then restart service |
| `FATAL: database "zkteco" does not exist` | Run `CREATE DATABASE zkteco;` in psql/pgAdmin |
| Service starts but no pulls at schedule time | Verify `SCHEDULER_TIMEZONE` in `.env` matches the server's local time |
| `attendance records: 0` on Attn1 | Attn1 log may have been cleared on the device — normal; other devices have data |
| pywin32 error on service install | Run `python Scripts\pywin32_postinstall.py -install` as Administrator |
| Python 3.14 psycopg2 build error | Run `pip install psycopg2-binary --pre` for the pre-built wheel |
| Reports not generated | Ensure `matplotlib`, `pandas`, `Pillow` are installed (`pip install -r requirements.txt`) |
| Want to test without waiting for schedule | Run `python main.py --run-now` from the project directory |

---

## License

MIT — free to use and modify.

---

*Built for [beamlab.dev](https://beamlab.dev) — ZKTeco biometric attendance automation.*
