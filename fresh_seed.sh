#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# fresh_seed.sh  —  Reset ZKTeco data and re-seed for testing / verification
#
# Mode 1 — Partial Reset (recommended for production re-sync)
#   KEEP:  devices, global_users, org hierarchy, shifts, leave_types
#   CLEAR: attendance_logs, employees, pull_sessions,
#          leave_applications, leave_balances, holidays
#   THEN:  pull fresh attendance from every active device in the database
#
# Mode 2 — Full Wipe + Sample Data (standalone testing, no real devices)
#   WIPE:  every table in the database
#   SEED:  org structure → shifts → leave types → 1 sample device →
#          5 global users + linked employees → leave balances →
#          holidays (Public + Festival) → approved leave application →
#          current-month attendance (realistic weekday punches)
#
# Usage:
#   ./fresh_seed.sh              — interactive menu
#   ./fresh_seed.sh --mode 1     — partial reset (no prompt)
#   ./fresh_seed.sh --mode 2     — full wipe + seed (no prompt)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Parse CLI flags ───────────────────────────────────────────────────────────
CLI_MODE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) CLI_MODE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# ── Terminal colours ──────────────────────────────────────────────────────────
R='\033[0;31m'
Y='\033[1;33m'
G='\033[0;32m'
C='\033[0;36m'
B='\033[1;34m'
NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC}  $*"; }
warn() { echo -e "  ${Y}⚠${NC}  $*"; }
die()  { echo -e "  ${R}✗${NC}  $*"; exit 1; }
info() { echo -e "  ${C}→${NC}  $*"; }
hr()   { echo -e "${C}$(printf '─%.0s' $(seq 1 62))${NC}"; }

# ── Locate Python 3.10+ (prefer .venv) ───────────────────────────────────────
PYTHON=""
for _p in \
    "$SCRIPT_DIR/.venv/bin/python3" \
    "$SCRIPT_DIR/.venv/bin/python" \
    python3 python; do
    if command -v "$_p" &>/dev/null \
       && "$_p" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>/dev/null; then
        PYTHON="$(command -v "$_p" 2>/dev/null || echo "$_p")"
        break
    fi
done
[ -z "$PYTHON" ] && die "Python 3.10+ not found.
     Fix: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"

# ── Read db_config.json ───────────────────────────────────────────────────────
[ ! -f db_config.json ] && die "db_config.json not found.
     Fix: cp db_config.json.example db_config.json && nano db_config.json"

DB_HOST=$("$PYTHON" -c "import json;c=json.load(open('db_config.json'));print(c.get('host','localhost'))")
DB_PORT=$("$PYTHON" -c "import json;c=json.load(open('db_config.json'));print(c.get('port',5432))")
DB_NAME=$("$PYTHON" -c "import json;c=json.load(open('db_config.json'));print(c.get('dbname','zkteco'))")
DB_USER=$("$PYTHON" -c "import json;c=json.load(open('db_config.json'));print(c.get('user','postgres'))")
DB_PASS=$("$PYTHON" -c "import json;c=json.load(open('db_config.json'));print(c.get('password',''))")
export PGPASSWORD="$DB_PASS"

PG="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -v ON_ERROR_STOP=1"

# Test connection
$PG -c '\q' 2>/dev/null \
    || die "Cannot connect to PostgreSQL ($DB_NAME @ $DB_HOST:$DB_PORT).
     Check db_config.json and run: sudo systemctl start postgresql"
ok "PostgreSQL connection OK  ($DB_NAME @ $DB_HOST:$DB_PORT)"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${B}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${B}║       ZKTeco Attendance — Fresh Seed / Data Reset            ║${NC}"
echo -e "${B}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Mode selection ────────────────────────────────────────────────────────────
if [ -n "$CLI_MODE" ]; then
    MODE="$CLI_MODE"
else
    hr
    echo ""
    echo "  Select operation:"
    echo ""
    echo -e "  ${C}1)${NC} Partial Reset  ${Y}(recommended for production re-sync)${NC}"
    echo "       KEEP : devices, global_users, org hierarchy, shifts, leave_types"
    echo "       CLEAR: attendance_logs, employees, pull_sessions,"
    echo "              leave_applications, leave_balances, holidays"
    echo "       THEN : pull fresh from all active devices in the database"
    echo ""
    echo -e "  ${C}2)${NC} Full Wipe + Sample Data  ${Y}(standalone testing — no real devices)${NC}"
    echo "       WIPE : every table in the database"
    echo "       SEED : org → shifts → users → device → attendance → holidays → leaves"
    echo ""
    echo -e "  ${C}3)${NC} Abort"
    echo ""
    read -r -p "  Choice [1/2/3]: " MODE
fi

case "$MODE" in
    1|2) ;;
    3|*) echo "  Cancelled."; exit 0 ;;
esac

# ── Confirmation ──────────────────────────────────────────────────────────────
echo ""
if [ "$MODE" = "1" ]; then
    echo -e "  ${Y}The following tables will be permanently cleared:${NC}"
    echo "    • attendance_logs   • employees        • pull_sessions"
    echo "    • leave_applications  • leave_balances  • holidays"
    echo ""
    echo "  Devices, global_users, org hierarchy, shifts and leave_types are KEPT."
else
    echo -e "  ${R}ALL DATA in '$DB_NAME' will be permanently deleted — every table.${NC}"
    echo "  Sample test data will be inserted afterwards."
fi
echo ""
read -r -p "  Type 'yes' to continue: " CONFIRM
[ "$CONFIRM" != "yes" ] && { echo "  Cancelled."; exit 0; }

# ── Backup ────────────────────────────────────────────────────────────────────
hr
echo ""
info "Creating pre-reset backup..."
mkdir -p backups
BFILE="backups/pre_seed_$(date +%Y%m%d_%H%M%S).sql.gz"
if command -v pg_dump &>/dev/null; then
    PGPASSWORD="$DB_PASS" pg_dump \
        -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" \
        | gzip > "$BFILE" \
        && ok "Backup: $BFILE" \
        || warn "Backup failed — continuing anyway"
else
    warn "pg_dump not found — skipping backup  (sudo apt install postgresql-client)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# MODE 1 — Partial Reset + Pull
# ═════════════════════════════════════════════════════════════════════════════
if [ "$MODE" = "1" ]; then

    echo ""
    info "Truncating transactional tables..."
    $PG <<'EOSQL'
BEGIN;
TRUNCATE TABLE leave_applications    RESTART IDENTITY CASCADE;
TRUNCATE TABLE leave_balances        RESTART IDENTITY CASCADE;
TRUNCATE TABLE holidays              RESTART IDENTITY CASCADE;
TRUNCATE TABLE pull_sessions         RESTART IDENTITY CASCADE;
TRUNCATE TABLE attendance_logs       RESTART IDENTITY CASCADE;
TRUNCATE TABLE employees             RESTART IDENTITY CASCADE;
COMMIT;
EOSQL
    ok "Tables cleared: leave_applications, leave_balances, holidays, pull_sessions, attendance_logs, employees"

    echo ""
    info "Pulling from all active devices in the database..."
    echo "  (May take 30–120 s per device depending on record volume)"
    echo ""

    "$PYTHON" - <<'PYEOF'
import sys, os, json
sys.path.insert(0, os.getcwd())

try:
    import psycopg2, psycopg2.extras
    from datetime import datetime, timezone
    from config import DeviceConfig
    import puller as puller_mod
    import db as db_mod
except ImportError as e:
    print(f"  Import error: {e}")
    print("  Fix: source .venv/bin/activate && pip install -r requirements.txt")
    sys.exit(1)

PUNCH_LABELS = {0: 'Check-In', 1: 'Check-Out', 4: 'OT-In', 5: 'OT-Out'}
try:
    from config import PUNCH_LABELS as _PL
    PUNCH_LABELS = _PL
except Exception:
    pass

cfg  = json.load(open('db_config.json'))
conn = psycopg2.connect(**{k: cfg[k] for k in ('host','port','dbname','user','password') if k in cfg})

with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute("SELECT * FROM devices WHERE is_active = TRUE ORDER BY id")
    device_rows = [dict(r) for r in cur.fetchall()]

if not device_rows:
    print("  ⚠  No active devices in database.")
    print("  Add devices at http://localhost:8097/devices then re-run this script.")
    conn.close()
    sys.exit(0)

print(f"  Found {len(device_rows)} active device(s)\n")

def to_utc(ts):
    if ts is None:
        return ts
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('Asia/Kathmandu')
    except Exception:
        tz = timezone.utc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=tz)
    return ts.astimezone(timezone.utc)

total_users = total_records = total_new = 0
for row in device_rows:
    dev = DeviceConfig(
        name=row['name'],
        ip=row['ip_address'],
        port=int(row.get('port', 4370)),
        password=row.get('password', '') or '',
        model=row.get('model', '') or '',
        is_active=True,
        connection_timeout=int(row.get('connection_timeout', 10)),
    )
    print(f"  [{dev.name}]  {dev.ip}:{dev.port}", end='  ', flush=True)
    started = datetime.now(timezone.utc)
    sid = None
    try:
        db_id = db_mod.upsert_device(conn, dev)
        conn.commit()
        sid = db_mod.start_pull_session(conn, db_id, started)
        conn.commit()
        res = puller_mod.pull_device(dev)

        if not res.success:
            db_mod.complete_pull_session(conn, sid, 0, 0, 'failed', res.error)
            conn.commit()
            print(f"FAILED → {res.error}")
            continue

        for u in res.users:
            try:
                gu = db_mod.find_global_user_by_global_id(conn, str(u.user_id))
                if gu:
                    try:
                        setattr(u, 'global_user_id', gu['id'])
                    except Exception:
                        pass
                db_mod.upsert_employee(conn, db_id, u)
            except Exception:
                conn.rollback()

        emp_map   = db_mod.build_employee_map(conn, db_id)
        att_dicts = [
            {
                'uid':         a.uid,
                'user_id':     str(a.user_id),
                'timestamp':   to_utc(a.timestamp),
                'status':      int(a.status) if a.status is not None else None,
                'punch':       int(a.punch)  if a.punch  is not None else None,
                'punch_label': PUNCH_LABELS.get(
                    int(a.punch) if a.punch is not None else -1, 'Unknown'),
            }
            for a in res.attendance
        ]
        new = db_mod.insert_attendance_batch(conn, db_id, att_dicts, emp_map)
        db_mod.complete_pull_session(conn, sid, len(att_dicts), new, 'success')
        conn.commit()
        print(f"OK  →  {len(res.users)} users | {len(att_dicts)} records | {new} new")
        total_users   += len(res.users)
        total_records += len(att_dicts)
        total_new     += new

    except Exception as exc:
        conn.rollback()
        print(f"ERROR → {exc}")
        if sid:
            try:
                db_mod.complete_pull_session(conn, sid, 0, 0, 'failed', str(exc))
                conn.commit()
            except Exception:
                conn.rollback()

conn.close()
print(f"\n  ─── Pull complete ───")
print(f"  Users synced: {total_users}  |  Records pulled: {total_records}  |  New inserts: {total_new}")
PYEOF

fi  # ── end MODE 1 ────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
# MODE 2 — Full Wipe + Sample Seed
# ═════════════════════════════════════════════════════════════════════════════
if [ "$MODE" = "2" ]; then

    echo ""
    info "Truncating all tables..."
    $PG <<'EOSQL'
BEGIN;
-- Truncate leaf→root in FK order; CASCADE handles any remaining deps
TRUNCATE TABLE
    leave_applications,
    leave_balances,
    shift_rules,
    holidays,
    pull_sessions,
    attendance_logs,
    employees,
    leave_types,
    global_users,
    shifts,
    units,
    sections,
    departments,
    directorates,
    devices
RESTART IDENTITY CASCADE;
COMMIT;
EOSQL
    ok "All tables truncated"

    echo ""
    info "Inserting sample seed data..."
    echo ""

    "$PYTHON" - <<'PYEOF'
import sys, os, json
sys.path.insert(0, os.getcwd())

try:
    import psycopg2, psycopg2.extras
    from datetime import date, datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from nepali_utils import ad_to_bs_tuple, bs_to_ad, bs_month_info
except ImportError as e:
    print(f"  Import error: {e}")
    print("  Fix: source .venv/bin/activate && pip install -r requirements.txt")
    sys.exit(1)

NPT = ZoneInfo('Asia/Kathmandu')
cfg  = json.load(open('db_config.json'))
conn = psycopg2.connect(**{k: cfg[k] for k in ('host','port','dbname','user','password') if k in cfg})
cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

def ins(sql, *args):
    cur.execute(sql + " RETURNING id", args)
    return cur.fetchone()[0]

def run(sql, *args):
    cur.execute(sql, args)

# ── Current BS date ───────────────────────────────────────────────────────────
today  = date.today()
bs_t   = ad_to_bs_tuple(today)
if not bs_t:
    print("  ✗  Cannot determine current BS date — check nepali_utils installation")
    sys.exit(1)
bs_year, bs_mon, bs_day = bs_t
today_bs_str = f"{bs_year:04d}-{bs_mon:02d}-{bs_day:02d}"

mi = bs_month_info(bs_year, bs_mon)
if not mi:
    print("  ✗  Cannot get BS month info")
    sys.exit(1)
from_ad = date.fromisoformat(mi['first_ad'])
to_ad   = date.fromisoformat(mi['last_ad'])

print(f"  BS month: {bs_year}-{bs_mon:02d}  "
      f"({mi['first_ad']} → {mi['last_ad']}, {(to_ad-from_ad).days+1} days)")
print()

# ── Org hierarchy ─────────────────────────────────────────────────────────────
print("  [1/8] Org hierarchy ...", end=' ', flush=True)
dir_id  = ins("INSERT INTO directorates (name) VALUES (%s)",                       "Head Office")
dept_id = ins("INSERT INTO departments (name, directorate_id) VALUES (%s,%s)",     "Administration", dir_id)
sect_id = ins("INSERT INTO sections (name, department_id) VALUES (%s,%s)",         "IT Section",     dept_id)
unit_id = ins("INSERT INTO units (name, section_id) VALUES (%s,%s)",               "Dev Unit",       sect_id)
print("OK  (1 Directorate → 1 Department → 1 Section → 1 Unit)")

# ── Shift ─────────────────────────────────────────────────────────────────────
print("  [2/8] Shift ...", end=' ', flush=True)
shift_id = ins("INSERT INTO shifts (name, start_time, end_time) VALUES (%s,%s,%s)",
               "Day Shift", "10:00", "17:00")
print("OK  (Day Shift 10:00 – 17:00)")

# ── Leave types ───────────────────────────────────────────────────────────────
print("  [3/8] Leave types (Nepal Gov standard) ...", end=' ', flush=True)
LEAVE_TYPES = [
    # name,             code,     days/yr, max_acc, carry_fwd, paid
    ("Home Leave",       "HOME",    13,  60, True,  True),
    ("Sick Leave",       "SICK",    12,  45, True,  True),
    ("Casual Leave",     "CASUAL",  12,   0, False, True),
    ("Maternity Leave",  "MAT",     98,   0, False, True),
    ("Paternity Leave",  "PAT",     15,   0, False, True),
    ("Mourning Leave",   "MOURN",   13,   0, False, True),
    ("Study Leave",      "STUDY",    0,   0, False, True),
    ("Unpaid Leave",     "UNPAID",   0,   0, False, False),
]
lt_ids = {}
for name, code, dpyr, maxacc, cf, paid in LEAVE_TYPES:
    lt_ids[code] = ins(
        "INSERT INTO leave_types (name,code,days_per_year,max_accumulate,carry_forward,is_paid)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        name, code, dpyr, maxacc, cf, paid,
    )
print(f"OK  ({len(LEAVE_TYPES)} types)")

# ── Device ────────────────────────────────────────────────────────────────────
print("  [4/8] Sample device ...", end=' ', flush=True)
dev_id = ins(
    "INSERT INTO devices (name,ip_address,port,password,model,is_active)"
    " VALUES (%s,%s,%s,%s,%s,%s)",
    "Sample Device", "192.168.1.100", 4370, "", "ZKTeco K40", True,
)
print("OK  (192.168.1.100:4370 — connectivity not required for seed)")

# ── Global users + linked employees ──────────────────────────────────────────
print("  [5/8] Global users + employees ...", end=' ', flush=True)
SEED_USERS = [
    ("101", "EMP001", "Ram Bahadur Thapa"),
    ("102", "EMP002", "Sita Kumari Sharma"),
    ("103", "EMP003", "Hari Prasad Adhikari"),   # ← gets approved leave
    ("104", "EMP004", "Maya Devi Gurung"),
    ("105", "EMP005", "Krishna Bahadur Magar"),
]
gu_ids = []   # list of (global_user_db_id, att_id, name)
for att_id, emp_id, name in SEED_USERS:
    gid = ins(
        "INSERT INTO global_users"
        "  (global_user_id, employee_id, name, department_id, section_id, unit_id, shift_id, created_bs)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        att_id, emp_id, name, dept_id, sect_id, unit_id, shift_id, today_bs_str,
    )
    ins(
        "INSERT INTO employees (device_id, uid, user_id, name, global_user_id)"
        " VALUES (%s,%s,%s,%s,%s)",
        dev_id, int(att_id), att_id, name, gid,
    )
    gu_ids.append((gid, att_id, name))
print(f"OK  ({len(gu_ids)} users: IDs 101–105)")

# Build uid → employees.id map for attendance inserts
cur.execute("SELECT uid, id FROM employees WHERE device_id=%s", (dev_id,))
emp_db_map = {r['uid']: r['id'] for r in cur.fetchall()}

# ── Leave balances for current BS year ───────────────────────────────────────
print("  [6/8] Leave balances ...", end=' ', flush=True)
bal_count = 0
for name, code, dpyr, maxacc, cf, paid in LEAVE_TYPES:
    for gid, _, _ in gu_ids:
        try:
            ins(
                "INSERT INTO leave_balances"
                "  (global_user_id, leave_type_id, bs_year, opening_balance, days_earned)"
                " VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (global_user_id, leave_type_id, bs_year) DO NOTHING",
                gid, lt_ids[code], bs_year, 0, dpyr,
            )
            bal_count += 1
        except Exception:
            conn.rollback()
print(f"OK  ({bal_count} balance rows for {bs_year})")

# ── Holidays in current BS month ──────────────────────────────────────────────
print("  [7/8] Holidays ...", end=' ', flush=True)
SEED_HOLIDAYS = [
    (8,  "Test Public Holiday", "public"),
    (18, "Test Festival Day",   "festival"),
]
hol_days = set()
hol_ok = 0
for bsday, hname, htype in SEED_HOLIDAYS:
    h_bs = f"{bs_year:04d}-{bs_mon:02d}-{bsday:02d}"
    h_ad = bs_to_ad(h_bs)
    if h_ad:
        try:
            ins(
                "INSERT INTO holidays (name, holiday_ad, holiday_bs, holiday_type, description)"
                " VALUES (%s,%s,%s,%s,%s)",
                hname, h_ad, h_bs, htype, "Auto-seeded for testing",
            )
            hol_days.add(date.fromisoformat(h_ad))
            hol_ok += 1
        except Exception:
            conn.rollback()
print(f"OK  (day 8 = Public, day 18 = Festival of BS {bs_year}-{bs_mon:02d})")

# ── Approved leave: employee 3 (Hari), BS days 12–14 ────────────────────────
print("  [8/8] Leave application + attendance ...", end=' ', flush=True)
gid3       = gu_ids[2][0]
l_from_bs  = f"{bs_year:04d}-{bs_mon:02d}-12"
l_to_bs    = f"{bs_year:04d}-{bs_mon:02d}-14"
l_from_ad  = bs_to_ad(l_from_bs)
l_to_ad    = bs_to_ad(l_to_bs)
leave_days = set()
if l_from_ad and l_to_ad:
    ld = date.fromisoformat(l_from_ad)
    le = date.fromisoformat(l_to_ad)
    while ld <= le:
        leave_days.add(ld)
        ld += timedelta(days=1)
    try:
        ins(
            "INSERT INTO leave_applications"
            "  (global_user_id,leave_type_id,from_bs,to_bs,from_ad,to_ad,"
            "   days,reason,status,created_bs)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            gid3, lt_ids['SICK'],
            l_from_bs, l_to_bs, l_from_ad, l_to_ad,
            3.0, "Fever — sick leave test", "approved", today_bs_str,
        )
    except Exception as e:
        conn.rollback()
        print(f"(leave skipped: {e})", end=' ')

# ── Attendance for current BS month ──────────────────────────────────────────
# Employee patterns:
#   0 (Ram)   — present every weekday
#   1 (Sita)  — absent Tuesdays (isoweekday 2)
#   2 (Hari)  — on leave days 12–14, absent Wednesdays otherwise
#   3 (Maya)  — absent Mondays and Fridays
#   4 (Krishna)— present first 15 days only
att_absent_dow = {
    1: {1},        # Sita absent Tuesdays  (0=Mon … 6=Sun)
    2: {2},        # Hari absent Wednesdays
    3: {0, 4},     # Maya absent Mon + Fri
}
att_count = 0
d = from_ad
while d <= to_ad:
    dow        = d.weekday()          # 0=Mon … 6=Sun
    is_sat     = (dow == 5)           # Nepal weekend = Saturday
    is_holiday = (d in hol_days)

    if not is_sat and not is_holiday:
        for idx, (gid, att_id, name) in enumerate(gu_ids):
            # Employee 3 on leave
            if idx == 2 and d in leave_days:
                continue
            # Per-employee absent patterns
            if idx in att_absent_dow and dow in att_absent_dow[idx]:
                continue
            # Employee 5 only first 15 days of month
            if idx == 4 and d > from_ad + timedelta(days=14):
                continue

            uid = int(att_id)
            emp_db_id = emp_db_map.get(uid)

            # BS date for this AD day
            bs_d = ad_to_bs_tuple(d)
            bs_str = f"{bs_d[0]:04d}-{bs_d[1]:02d}-{bs_d[2]:02d}" if bs_d else ''

            # Deterministic minute offset per employee+day (no random module needed)
            seed = uid * 1000 + d.toordinal()
            ci_offset = (seed % 20) - 8          # -8 to +11 min around 10:00
            co_offset = ((seed * 7) % 22) - 5    # -5 to +16 min around 17:00

            ci_npt = datetime(d.year, d.month, d.day, 10, 0, tzinfo=NPT) + timedelta(minutes=ci_offset)
            co_npt = datetime(d.year, d.month, d.day, 17, 0, tzinfo=NPT) + timedelta(minutes=co_offset)

            for ts_utc, punch, label in [
                (ci_npt.astimezone(timezone.utc), 0, 'Check-In'),
                (co_npt.astimezone(timezone.utc), 1, 'Check-Out'),
            ]:
                try:
                    run(
                        "INSERT INTO attendance_logs"
                        "  (device_id,employee_id,uid,user_id,name,"
                        "   timestamp,status,punch,punch_label,bs_date)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (device_id,uid,timestamp) DO NOTHING",
                        dev_id, emp_db_id, uid, str(uid), name,
                        ts_utc, 0, punch, label, bs_str,
                    )
                    att_count += 1
                except Exception:
                    conn.rollback()
    d += timedelta(days=1)

print(f"OK  ({att_count} attendance records for {len(gu_ids)} employees)")

conn.commit()
cur.close()
conn.close()

# ── Seed summary ──────────────────────────────────────────────────────────────
print()
print("  ─── Seed summary ─────────────────────────────────────────")
print(f"  Org        : Head Office → Administration → IT Section → Dev Unit")
print(f"  Shift      : Day Shift (10:00 – 17:00)")
print(f"  Leave types: {len(LEAVE_TYPES)}")
print(f"  Device     : Sample Device @ 192.168.1.100:4370")
print()
print(f"  Global Users (Att. ID  →  Name):")
for gid, att_id, name in gu_ids:
    print(f"    {att_id}  {name}")
print()
print(f"  BS month   : {bs_year}-{bs_mon:02d}  ({mi['first_ad']} → {mi['last_ad']})")
print(f"  Holidays   : day 8 (Public), day 18 (Festival)")
if l_from_ad:
    print(f"  Leave      : Hari Prasad Adhikari  {l_from_bs} → {l_to_bs}  (approved, SICK)")
print(f"  Attendance : {att_count} records  (weekday punches, realistic variation)")
print("  ─────────────────────────────────────────────────────────")
PYEOF

fi  # ── end MODE 2 ────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
# Done — verification checklist
# ═════════════════════════════════════════════════════════════════════════════
echo ""
hr
echo ""
echo -e "  ${G}✓  Done!${NC}"
echo ""
echo "  Verification checklist:"
echo "  ─────────────────────────────────────────────────────────────────────"
echo "  □  Reports › Monthly  — select employee, check every Remark cell:"
echo "       Present (has punch) | Absent | Weekend (Sat) | Holiday | Festival | Leave"
echo "  □  Report footer row — check Working Days, Holiday, Festival, Leave counts"
echo "  □  Calendar           — verify day-8 and day-18 holidays are visible"
echo "  □  Leaves page        — approved sick leave shows correctly"
echo "  □  Reports: Leave days show 'Leave' not 'Absent' in Remark column"
echo "  □  Users page         — global users with Att.ID, Emp ID, org, shift"
echo "  □  Dashboard          — device card + recent punch count"
echo "  ─────────────────────────────────────────────────────────────────────"
echo ""
if [ "$MODE" = "1" ]; then
    echo "  Note: if no devices were active, add them at"
    echo "    http://localhost:8097/devices  then re-run:  ./fresh_seed.sh --mode 1"
fi
if [ "$MODE" = "2" ]; then
    echo "  Quick report URL (first employee):"
    echo "    http://localhost:8097/reports/monthly/view?emp_key=g1&bs_year=$(
        "$PYTHON" -c "from nepali_utils import ad_to_bs_tuple; from datetime import date; t=ad_to_bs_tuple(date.today()); print(t[0] if t else 2083)"
    )&bs_month=$(
        "$PYTHON" -c "from nepali_utils import ad_to_bs_tuple; from datetime import date; t=ad_to_bs_tuple(date.today()); print(t[1] if t else 3)"
    )"
fi
echo ""
