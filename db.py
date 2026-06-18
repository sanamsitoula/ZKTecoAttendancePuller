import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from config import DeviceConfig, load_db_config

logger = logging.getLogger(__name__)


def _ts_to_bs(ts) -> str:
    """Convert a datetime to BS date string 'YYYY-MM-DD' in NPT. Returns '' on failure."""
    if ts is None:
        return ''
    try:
        import zoneinfo
        from nepali_utils import ad_to_bs as _a2b
        NPT = zoneinfo.ZoneInfo('Asia/Kathmandu')
        if hasattr(ts, 'tzinfo') and ts.tzinfo:
            ts = ts.astimezone(NPT)
        return _a2b(ts.strftime('%Y-%m-%d')) or ''
    except Exception:
        return ''


def _today_bs() -> str:
    """Return today's BS date string in NPT."""
    from datetime import datetime
    return _ts_to_bs(datetime.now())


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    ip_address  VARCHAR(45)  NOT NULL,
    port        INTEGER      NOT NULL DEFAULT 4370,
    password    VARCHAR(100) DEFAULT '',
    model       VARCHAR(100),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS employees (
    id          SERIAL PRIMARY KEY,
    device_id   INTEGER      NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    uid         INTEGER      NOT NULL,
    user_id     VARCHAR(50)  NOT NULL,
    name        VARCHAR(200),
    privilege   SMALLINT     DEFAULT 0,
    card        VARCHAR(50),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (device_id, uid)
);

CREATE TABLE IF NOT EXISTS attendance_logs (
    id          BIGSERIAL    PRIMARY KEY,
    device_id   INTEGER      NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    employee_id INTEGER      REFERENCES employees(id) ON DELETE SET NULL,
    uid         INTEGER      NOT NULL,
    user_id     VARCHAR(50),
    name        VARCHAR(200),
    timestamp   TIMESTAMPTZ  NOT NULL,
    status      SMALLINT,
    punch       SMALLINT,
    punch_label VARCHAR(20),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (device_id, uid, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_attendance_device_ts
    ON attendance_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_attendance_date
    ON attendance_logs (timestamp);

CREATE TABLE IF NOT EXISTS pull_sessions (
    id              BIGSERIAL   PRIMARY KEY,
    device_id       INTEGER     NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    records_pulled  INTEGER     DEFAULT 0,
    new_inserts     INTEGER     DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',
    error_message   TEXT
);

-- Additive migration: global_users table and link to employees
CREATE TABLE IF NOT EXISTS global_users (
    id              SERIAL PRIMARY KEY,
    global_user_id  VARCHAR(100) UNIQUE,
    name            VARCHAR(200),
    privilege       SMALLINT DEFAULT 0,
    card            VARCHAR(50),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS global_user_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'employees_global_user_id_fkey'
    ) THEN
        ALTER TABLE employees
            ADD CONSTRAINT employees_global_user_id_fkey
            FOREIGN KEY (global_user_id) REFERENCES global_users(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_employees_global_user_id
    ON employees (global_user_id);

-- BS date columns (additive migration)
ALTER TABLE attendance_logs ADD COLUMN IF NOT EXISTS bs_date VARCHAR(10) DEFAULT '';
ALTER TABLE pull_sessions    ADD COLUMN IF NOT EXISTS started_bs  VARCHAR(10) DEFAULT '';
ALTER TABLE pull_sessions    ADD COLUMN IF NOT EXISTS completed_bs VARCHAR(10) DEFAULT '';
ALTER TABLE employees        ADD COLUMN IF NOT EXISTS created_bs  VARCHAR(10) DEFAULT '';
ALTER TABLE employees        ADD COLUMN IF NOT EXISTS updated_bs  VARCHAR(10) DEFAULT '';
ALTER TABLE devices          ADD COLUMN IF NOT EXISTS created_bs  VARCHAR(10) DEFAULT '';
ALTER TABLE global_users     ADD COLUMN IF NOT EXISTS created_bs  VARCHAR(10) DEFAULT '';
ALTER TABLE global_users     ADD COLUMN IF NOT EXISTS updated_bs  VARCHAR(10) DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_attendance_bs_date ON attendance_logs (bs_date);

-- ── Departments ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS departments (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

ALTER TABLE global_users ADD COLUMN IF NOT EXISTS department_id INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'global_users_department_id_fkey'
    ) THEN
        ALTER TABLE global_users
            ADD CONSTRAINT global_users_department_id_fkey
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE SET NULL;
    END IF;
END$$;

-- ── Shifts ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shifts (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    start_time TIME NOT NULL,
    end_time   TIME NOT NULL
);

-- ── Shift Rules ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shift_rules (
    id             SERIAL PRIMARY KEY,
    shift_id       INTEGER NOT NULL REFERENCES shifts(id)       ON DELETE CASCADE,
    global_user_id INTEGER          REFERENCES global_users(id) ON DELETE CASCADE,
    department_id  INTEGER          REFERENCES departments(id)  ON DELETE CASCADE,
    from_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    to_date        DATE
);
CREATE INDEX IF NOT EXISTS idx_shift_rules_user ON shift_rules (global_user_id, from_date);
CREATE INDEX IF NOT EXISTS idx_shift_rules_dept ON shift_rules (department_id,  from_date);

-- ── Org Hierarchy ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS directorates (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE
);

ALTER TABLE departments ADD COLUMN IF NOT EXISTS directorate_id INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='departments_directorate_id_fkey') THEN
        ALTER TABLE departments ADD CONSTRAINT departments_directorate_id_fkey
            FOREIGN KEY (directorate_id) REFERENCES directorates(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS sections (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS units (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE
);

ALTER TABLE global_users ADD COLUMN IF NOT EXISTS section_id INTEGER;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS unit_id    INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='global_users_section_id_fkey') THEN
        ALTER TABLE global_users ADD CONSTRAINT global_users_section_id_fkey
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL;
    END IF;
END$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='global_users_unit_id_fkey') THEN
        ALTER TABLE global_users ADD CONSTRAINT global_users_unit_id_fkey
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL;
    END IF;
END$$;

ALTER TABLE shift_rules ADD COLUMN IF NOT EXISTS directorate_id INTEGER;
ALTER TABLE shift_rules ADD COLUMN IF NOT EXISTS section_id     INTEGER;
ALTER TABLE shift_rules ADD COLUMN IF NOT EXISTS unit_id        INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='shift_rules_directorate_id_fkey') THEN
        ALTER TABLE shift_rules ADD CONSTRAINT shift_rules_directorate_id_fkey
            FOREIGN KEY (directorate_id) REFERENCES directorates(id) ON DELETE CASCADE;
    END IF;
END$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='shift_rules_section_id_fkey') THEN
        ALTER TABLE shift_rules ADD CONSTRAINT shift_rules_section_id_fkey
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE;
    END IF;
END$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='shift_rules_unit_id_fkey') THEN
        ALTER TABLE shift_rules ADD CONSTRAINT shift_rules_unit_id_fkey
            FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE;
    END IF;
END$$;

-- ── Leave Management ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leave_types (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    code           VARCHAR(20)  NOT NULL UNIQUE,
    days_per_year  NUMERIC(5,1) NOT NULL DEFAULT 0,
    max_accumulate NUMERIC(5,1) NOT NULL DEFAULT 0,
    carry_forward  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_paid        BOOLEAN      NOT NULL DEFAULT TRUE,
    description    TEXT
);

CREATE TABLE IF NOT EXISTS leave_balances (
    id              SERIAL PRIMARY KEY,
    global_user_id  INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    leave_type_id   INTEGER NOT NULL REFERENCES leave_types(id)  ON DELETE CASCADE,
    bs_year         INTEGER NOT NULL,
    opening_balance NUMERIC(5,1) NOT NULL DEFAULT 0,
    days_earned     NUMERIC(5,1) NOT NULL DEFAULT 0,
    days_taken      NUMERIC(5,1) NOT NULL DEFAULT 0,
    UNIQUE (global_user_id, leave_type_id, bs_year)
);

CREATE TABLE IF NOT EXISTS leave_applications (
    id             SERIAL PRIMARY KEY,
    global_user_id INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    leave_type_id  INTEGER NOT NULL REFERENCES leave_types(id),
    from_bs        VARCHAR(10) NOT NULL,
    to_bs          VARCHAR(10) NOT NULL,
    from_ad        DATE NOT NULL,
    to_ad          DATE NOT NULL,
    days           NUMERIC(5,1) NOT NULL,
    reason         TEXT,
    status         VARCHAR(20)  NOT NULL DEFAULT 'pending',
    applied_bs     VARCHAR(10),
    applied_ad     DATE DEFAULT CURRENT_DATE,
    remarks        TEXT,
    approved_by    VARCHAR(100),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_leave_apps_user  ON leave_applications (global_user_id, from_ad);
CREATE INDEX IF NOT EXISTS idx_leave_apps_dates ON leave_applications (from_ad, to_ad, status);

-- ── Holiday Calendar ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS holidays (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    holiday_ad   DATE         NOT NULL,
    holiday_bs   VARCHAR(10)  NOT NULL,
    holiday_type VARCHAR(50)  NOT NULL DEFAULT 'public',
    description  TEXT,
    UNIQUE (holiday_ad)
);
CREATE INDEX IF NOT EXISTS idx_holidays_ad ON holidays (holiday_ad);
"""


_AUDIT_COLUMNS_SQL = """
-- ── Audit columns: track which app user created/updated/deleted records ─────
ALTER TABLE devices         ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE devices         ADD COLUMN IF NOT EXISTS updated_by  INTEGER;
ALTER TABLE global_users    ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE global_users    ADD COLUMN IF NOT EXISTS updated_by  INTEGER;
ALTER TABLE departments     ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE directorates    ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE sections        ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE units           ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE shifts          ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE shift_rules     ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE leave_types     ADD COLUMN IF NOT EXISTS created_by  INTEGER;
ALTER TABLE leave_applications ADD COLUMN IF NOT EXISTS created_by INTEGER;
ALTER TABLE leave_applications ADD COLUMN IF NOT EXISTS updated_by INTEGER;
ALTER TABLE holidays        ADD COLUMN IF NOT EXISTS created_by  INTEGER;
"""


_GLOBAL_USERS_EXTRA_SQL = """
-- ── Extended global_users fields ─────────────────────────────────────────────
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS employee_id      VARCHAR(50);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS bank_number      VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS email            VARCHAR(200);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS phone            VARCHAR(50);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS fingerprint_data TEXT;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS shift_id         INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'global_users_shift_id_fkey') THEN
        ALTER TABLE global_users
            ADD CONSTRAINT global_users_shift_id_fkey
            FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE SET NULL;
    END IF;
END$$;
CREATE INDEX IF NOT EXISTS idx_global_users_employee_id ON global_users (employee_id);
"""


_LEAVE_TYPES_SEED = """
INSERT INTO leave_types (name, code, days_per_year, max_accumulate, carry_forward, is_paid, description)
VALUES
  ('Home Leave',      'HOME',      13, 60, TRUE,  TRUE,  'Gharbidha Bida — 13 days/year, accumulates up to 60'),
  ('Sick Leave',      'SICK',      12, 45, TRUE,  TRUE,  'Birami Bida — 12 days/year, accumulates up to 45'),
  ('Casual Leave',    'CASUAL',    12,  0, FALSE, TRUE,  'Aakasmic Bida — 12 days/year, does not carry forward'),
  ('Maternity Leave', 'MATERNITY', 98,  0, FALSE, TRUE,  '98 days total'),
  ('Paternity Leave', 'PATERNITY', 15,  0, FALSE, TRUE,  '15 days'),
  ('Mourning Leave',  'MOURNING',  13,  0, FALSE, TRUE,  'Sog Bida — 13 days for immediate family'),
  ('Study Leave',     'STUDY',      0,  0, FALSE, TRUE,  'As sanctioned by management'),
  ('Unpaid Leave',    'UNPAID',     0,  0, FALSE, FALSE, 'Without pay')
ON CONFLICT (code) DO NOTHING;
"""


_PHASE5_SQL = """
-- ── Phase 5: holiday_types table, leave_types enhancements, leave_applications
--             half-day, leave_balances carry-forward, global_users employee
--             profile fields, shifts grace period ──────────────────────────────

CREATE TABLE IF NOT EXISTS holiday_types (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    type_code  VARCHAR(10)  NOT NULL UNIQUE,
    color_code VARCHAR(10)  DEFAULT '#ef4444',
    sort_order INTEGER      DEFAULT 0
);

ALTER TABLE holidays ADD COLUMN IF NOT EXISTS holiday_type_id INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'holidays_holiday_type_id_fkey'
    ) THEN
        ALTER TABLE holidays
            ADD CONSTRAINT holidays_holiday_type_id_fkey
            FOREIGN KEY (holiday_type_id) REFERENCES holiday_types(id) ON DELETE SET NULL;
    END IF;
END$$;

ALTER TABLE leave_types ADD COLUMN IF NOT EXISTS display_code     VARCHAR(10);
ALTER TABLE leave_types ADD COLUMN IF NOT EXISTS color_code       VARCHAR(10);
ALTER TABLE leave_types ADD COLUMN IF NOT EXISTS sort_order       INTEGER DEFAULT 0;
ALTER TABLE leave_types ADD COLUMN IF NOT EXISTS half_day_allowed BOOLEAN DEFAULT true;
ALTER TABLE leave_types ADD COLUMN IF NOT EXISTS applies_to       VARCHAR(20) DEFAULT 'ALL';

ALTER TABLE leave_applications ADD COLUMN IF NOT EXISTS is_half_day   BOOLEAN DEFAULT false;
ALTER TABLE leave_applications ADD COLUMN IF NOT EXISTS half_day_part VARCHAR(10);

ALTER TABLE leave_balances ADD COLUMN IF NOT EXISTS carried_forward  NUMERIC(5,1) DEFAULT 0;
ALTER TABLE leave_balances ADD COLUMN IF NOT EXISTS annual_allocated NUMERIC(5,1) DEFAULT 0;

ALTER TABLE global_users ADD COLUMN IF NOT EXISTS emp_type    VARCHAR(20) DEFAULT 'PERMANENT';
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS emp_status  VARCHAR(20) DEFAULT 'ACTIVE';
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS join_date   DATE;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS level_grade VARCHAR(50);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS designation VARCHAR(100);

ALTER TABLE shifts ADD COLUMN IF NOT EXISTS grace_late_in   INTEGER DEFAULT 0;
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS grace_early_out INTEGER DEFAULT 0;
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS break_minutes   INTEGER DEFAULT 0;
"""

_PHASE5_SEED_SQL = """
INSERT INTO holiday_types (name, type_code, color_code, sort_order) VALUES
    ('Public Holiday',   'PUB',  '#ef4444', 1),
    ('Festival Holiday', 'FEST', '#7c3aed', 2),
    ('National Holiday', 'NAT',  '#dc2626', 3),
    ('Optional Holiday', 'OPT',  '#16a34a', 4),
    ('Compensatory Off', 'COMP', '#2563eb', 5)
ON CONFLICT (type_code) DO NOTHING;

UPDATE holidays
   SET holiday_type_id = (SELECT id FROM holiday_types WHERE type_code = 'FEST')
 WHERE holiday_type = 'festival' AND holiday_type_id IS NULL;

UPDATE holidays
   SET holiday_type_id = (SELECT id FROM holiday_types WHERE type_code = 'PUB')
 WHERE holiday_type_id IS NULL;

UPDATE leave_types SET display_code='घ',   color_code='#2196F3', sort_order=1 WHERE code='HOME'      AND display_code IS NULL;
UPDATE leave_types SET display_code='बि',  color_code='#FF9800', sort_order=2 WHERE code='SICK'      AND display_code IS NULL;
UPDATE leave_types SET display_code='अ',   color_code='#e0a020', sort_order=3 WHERE code='CASUAL'    AND display_code IS NULL;
UPDATE leave_types SET display_code='म',   color_code='#E91E63', sort_order=4 WHERE code='MATERNITY' AND display_code IS NULL;
UPDATE leave_types SET display_code='पि',  color_code='#9C27B0', sort_order=5 WHERE code='PATERNITY' AND display_code IS NULL;
UPDATE leave_types SET display_code='शो',  color_code='#607D8B', sort_order=6 WHERE code='MOURNING'  AND display_code IS NULL;
UPDATE leave_types SET display_code='अध्', color_code='#00BCD4', sort_order=7 WHERE code='STUDY'     AND display_code IS NULL;
UPDATE leave_types SET display_code='X',   color_code='#F44336', sort_order=8 WHERE code='UNPAID'    AND display_code IS NULL;
"""

_ATTENDANCE_DAILY_SQL = """
-- ── Phase 6: attendance_daily pre-aggregated daily summary ───────────────────
-- Option A: attendance_logs remains source of truth; this table is populated
-- automatically after each device pull. Manual overrides use source='manual'
-- and are preserved across re-settlements.

CREATE TABLE IF NOT EXISTS attendance_daily (
    id              SERIAL PRIMARY KEY,
    global_user_id  INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    work_date       DATE    NOT NULL,
    status_code     VARCHAR(10),
    display_code    VARCHAR(10),
    first_in        TIME,
    last_out        TIME,
    work_minutes    INTEGER DEFAULT 0,
    ot_minutes      INTEGER DEFAULT 0,
    late_in_minutes INTEGER DEFAULT 0,
    early_out_min   INTEGER DEFAULT 0,
    source          VARCHAR(20) DEFAULT 'device',
    note            TEXT,
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (global_user_id, work_date)
);
CREATE INDEX IF NOT EXISTS idx_att_daily_user_date ON attendance_daily (global_user_id, work_date);
CREATE INDEX IF NOT EXISTS idx_att_daily_date      ON attendance_daily (work_date);
CREATE INDEX IF NOT EXISTS idx_att_daily_status    ON attendance_daily (status_code, work_date);
"""

_PHASE7_SQL = """
-- Phase 7: richer pull diagnostics + per-device UDP flag
ALTER TABLE pull_sessions ADD COLUMN IF NOT EXISTS error_detail TEXT;
ALTER TABLE devices       ADD COLUMN IF NOT EXISTS force_udp BOOLEAN NOT NULL DEFAULT FALSE;
"""


def get_connection():
    return psycopg2.connect(**load_db_config())


def init_schema(conn) -> None:
    # Phase 1: core DDL
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
    # Phase 2: audit columns migration (additive, safe to re-run)
    try:
        with conn.cursor() as cur:
            cur.execute(_AUDIT_COLUMNS_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Audit columns migration skipped: %s", e)
    # Phase 3: extended global_users columns
    try:
        with conn.cursor() as cur:
            cur.execute(_GLOBAL_USERS_EXTRA_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("global_users extra columns migration skipped: %s", e)
    # Phase 4: seed leave types
    try:
        with conn.cursor() as cur:
            cur.execute(_LEAVE_TYPES_SEED)
        conn.commit()
    except Exception as seed_err:
        conn.rollback()
        logger.warning("leave_types seed skipped: %s", seed_err)
    # Phase 5: holiday_types, leave_types enhancements, global_users profile fields
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE5_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 5 schema migration skipped: %s", e)
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE5_SEED_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 5 seed skipped: %s", e)
    # Phase 6: attendance_daily table
    try:
        with conn.cursor() as cur:
            cur.execute(_ATTENDANCE_DAILY_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("attendance_daily migration skipped: %s", e)
    # Phase 7: pull_sessions error_detail + devices force_udp
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE7_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 7 migration skipped: %s", e)
    logger.info("Database schema initialized.")


def upsert_device(conn, device: DeviceConfig) -> int:
    sql = """
        INSERT INTO devices (name, ip_address, port, password, model, is_active, created_bs)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            ip_address = EXCLUDED.ip_address,
            port       = EXCLUDED.port,
            model      = EXCLUDED.model,
            is_active  = EXCLUDED.is_active,
            created_bs = COALESCE(devices.created_bs, EXCLUDED.created_bs)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.name, device.ip, device.port,
            device.password, device.model, device.is_active,
            _today_bs(),
        ))
        return cur.fetchone()[0]


def upsert_employee(conn, device_id: int, user) -> int:
    # Accept optional global_user_id if present on user object/dict
    def user_value(key, default=None):
        if isinstance(user, dict):
            return user.get(key, default)
        return getattr(user, key, default)

    sql = """
        INSERT INTO employees (device_id, uid, user_id, name, privilege, card, global_user_id, created_bs, updated_bs)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (device_id, uid) DO UPDATE SET
            user_id        = EXCLUDED.user_id,
            name           = EXCLUDED.name,
            privilege      = EXCLUDED.privilege,
            card           = EXCLUDED.card,
            global_user_id = COALESCE(EXCLUDED.global_user_id, employees.global_user_id),
            updated_at     = NOW(),
            updated_bs     = EXCLUDED.updated_bs,
            created_bs     = COALESCE(employees.created_bs, EXCLUDED.created_bs)
        RETURNING id
    """
    with conn.cursor() as cur:
        privilege = user_value("privilege", 0)
        card = user_value("card")
        today = _today_bs()
        cur.execute(sql, (
            device_id,
            user_value("uid"),
            str(user_value("user_id")),
            user_value("name", "") or "",
            int(privilege) if privilege is not None else 0,
            str(card) if card else None,
            user_value("global_user_id"),
            today,
            today,
        ))
        return cur.fetchone()[0]


def find_global_user_by_global_id(conn, global_user_id: str):
    sql = "SELECT id, global_user_id, name, privilege, card FROM global_users WHERE global_user_id = %s"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (str(global_user_id),))
        row = cur.fetchone()
        return dict(row) if row else None


def link_employee_to_global_user(conn, device_id: int, uid: int, global_user_db_id: int) -> None:
    sql = "UPDATE employees SET global_user_id = %s WHERE device_id = %s AND uid = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (global_user_db_id, device_id, uid))


def build_employee_map(conn, device_id: int) -> dict:
    """Returns {uid: (employee_id, name)} for all employees on this device."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT uid, id, name FROM employees WHERE device_id = %s",
            (device_id,)
        )
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def insert_attendance_batch(
    conn,
    device_id: int,
    records: list,
    employee_map: dict,
) -> int:
    if not records:
        return 0

    rows = []
    for r in records:
        emp_info = employee_map.get(r["uid"])
        emp_id   = emp_info[0] if emp_info else None
        emp_name = emp_info[1] if emp_info else None
        rows.append((
            device_id,
            emp_id,
            r["uid"],
            r["user_id"],
            emp_name,
            r["timestamp"],
            r["status"],
            r["punch"],
            r["punch_label"],
            _ts_to_bs(r["timestamp"]),
        ))

    sql = """
        WITH ins AS (
            INSERT INTO attendance_logs
                (device_id, employee_id, uid, user_id, name,
                 timestamp, status, punch, punch_label, bs_date)
            VALUES %s
            ON CONFLICT (device_id, uid, timestamp) DO NOTHING
            RETURNING id
        )
        SELECT COUNT(*) FROM ins
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
        return cur.fetchone()[0]


def start_pull_session(conn, device_id: int, started_at: datetime) -> int:
    sql = """
        INSERT INTO pull_sessions (device_id, started_at, status, started_bs)
        VALUES (%s, %s, 'running', %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, started_at, _ts_to_bs(started_at)))
        return cur.fetchone()[0]


def complete_pull_session(
    conn,
    session_id: int,
    records_pulled: int,
    new_inserts: int,
    status: str,
    error_message=None,
    error_detail=None,
) -> None:
    sql = """
        UPDATE pull_sessions
        SET completed_at   = NOW(),
            completed_bs   = %s,
            records_pulled = %s,
            new_inserts    = %s,
            status         = %s,
            error_message  = %s,
            error_detail   = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (_today_bs(), records_pulled, new_inserts, status, error_message, error_detail, session_id))


def get_attendance_for_date(conn, date_str: str) -> list:
    """
    Returns attendance records for a given date (YYYY-MM-DD).
    Joins with employees to get names.
    """
    sql = """
        SELECT
            al.uid,
            al.user_id,
            COALESCE(al.name, e.name, 'Unknown') AS name,
            al.timestamp,
            al.punch,
            al.punch_label,
            d.name AS device_name
        FROM attendance_logs al
        LEFT JOIN LATERAL (
            SELECT name FROM employees
            WHERE device_id = al.device_id AND user_id = al.user_id
            ORDER BY id LIMIT 1
        ) e ON TRUE
        JOIN devices d ON al.device_id = d.id
        WHERE DATE(al.timestamp) = %s
        ORDER BY al.timestamp ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (date_str,))
        return [dict(row) for row in cur.fetchall()]


def get_daily_summary(conn, date_str: str) -> list:
    """
    Returns per-employee first check-in and last check-out for a date.
    """
    sql = """
        SELECT
            COALESCE(al.name, e.name, 'Unknown') AS name,
            al.user_id,
            MIN(al.timestamp) AS first_in,
            MAX(al.timestamp) AS last_out,
            COUNT(*) AS total_punches,
            STRING_AGG(DISTINCT d.name, ', ') AS devices
        FROM attendance_logs al
        LEFT JOIN LATERAL (
            SELECT name FROM employees
            WHERE device_id = al.device_id AND user_id = al.user_id
            ORDER BY id LIMIT 1
        ) e ON TRUE
        JOIN devices d ON al.device_id = d.id
        WHERE DATE(al.timestamp) = %s
        GROUP BY al.user_id, COALESCE(al.name, e.name, 'Unknown')
        ORDER BY first_in ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (date_str,))
        return [dict(row) for row in cur.fetchall()]


def get_attendance_summary_filtered(conn, from_date: str, to_date: str,
                                     device_id=None, name: str | None = None) -> list:
    """Per-employee summary over a date range with optional device/name filter.
    Returns each row with extra 'punches' key: list of {ts, label}."""
    where = ["DATE(al.timestamp) BETWEEN %s AND %s"]
    params: list = [from_date, to_date]
    if device_id:
        where.append("al.device_id = %s")
        params.append(device_id)
    if name:
        where.append(
            "(al.name ILIKE %s OR al.user_id ILIKE %s "
            "OR EXISTS (SELECT 1 FROM employees _e "
            "           WHERE _e.device_id = al.device_id AND _e.user_id = al.user_id "
            "           AND _e.name ILIKE %s))"
        )
        params += [f'%{name}%', f'%{name}%', f'%{name}%']
    sql = f"""
        SELECT
            COALESCE(al.name, e.name, 'Unknown') AS name,
            al.user_id,
            MIN(al.timestamp)  AS first_in,
            MAX(al.timestamp)  AS last_out,
            COUNT(*)           AS total_punches,
            STRING_AGG(DISTINCT d.name, ', ') AS devices,
            ARRAY_AGG(al.timestamp  ORDER BY al.timestamp) AS punch_times,
            ARRAY_AGG(al.punch_label ORDER BY al.timestamp) AS punch_labels
        FROM attendance_logs al
        LEFT JOIN LATERAL (
            SELECT name FROM employees
            WHERE device_id = al.device_id AND user_id = al.user_id
            ORDER BY id LIMIT 1
        ) e ON TRUE
        JOIN devices d ON al.device_id = d.id
        WHERE {" AND ".join(where)}
        GROUP BY al.user_id, COALESCE(al.name, e.name, 'Unknown')
        ORDER BY MIN(al.timestamp) ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, tuple(params))
        rows = [dict(row) for row in cur.fetchall()]
    for r in rows:
        times  = r.get('punch_times')  or []
        labels = r.get('punch_labels') or []
        r['punches'] = [
            {'ts': ts, 'label': lbl or '—'}
            for ts, lbl in zip(times, labels + [None] * len(times))
        ]
    return rows


# ── Convenience DB helpers for web UI ───────────────────────────────────────


def get_devices(conn):
    sql = "SELECT id, name, ip_address, port, password, model, is_active, force_udp, created_at FROM devices ORDER BY name"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def get_device(conn, device_id: int):
    sql = "SELECT id, name, ip_address, port, password, model, is_active, force_udp FROM devices WHERE id = %s"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (device_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_device(conn, device: dict, app_user_id: int = 0) -> int:
    sql = """
        INSERT INTO devices (name, ip_address, port, password, model, is_active, force_udp, created_bs, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            bool(device.get("force_udp", False)),
            _today_bs(), app_user_id or None, app_user_id or None,
        ))
        return cur.fetchone()[0]


def update_device(conn, device_id: int, device: dict, app_user_id: int = 0) -> None:
    sql = """
        UPDATE devices SET
            name = %s,
            ip_address = %s,
            port = %s,
            password = %s,
            model = %s,
            is_active = %s,
            force_udp = %s,
            updated_by = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            bool(device.get("force_udp", False)),
            app_user_id or None, device_id,
        ))


def delete_device(conn, device_id: int) -> None:
    sql = "DELETE FROM devices WHERE id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (device_id,))


def list_global_users(conn, search: str | None = None,
                      directorate_id: int | None = None,
                      department_id: int | None = None,
                      section_id: int | None = None,
                      unit_id: int | None = None):
    where = []
    params = []
    if search:
        where.append("""(
            gu.name ILIKE %s OR gu.global_user_id ILIKE %s OR gu.employee_id ILIKE %s
            OR gu.email ILIKE %s OR gu.phone ILIKE %s OR gu.bank_number ILIKE %s
            OR d.name ILIKE %s OR dr.name ILIKE %s OR s.name ILIKE %s OR u.name ILIKE %s
        )""")
        p = f'%{search}%'
        params += [p, p, p, p, p, p, p, p, p, p]
    if directorate_id:
        where.append("d.directorate_id = %s")
        params.append(directorate_id)
    if department_id:
        where.append("gu.department_id = %s")
        params.append(department_id)
    if section_id:
        where.append("gu.section_id = %s")
        params.append(section_id)
    if unit_id:
        where.append("gu.unit_id = %s")
        params.append(unit_id)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT
            gu.id, gu.global_user_id, gu.employee_id, gu.name,
            gu.privilege, gu.card, gu.bank_number, gu.email, gu.phone,
            gu.shift_id, sh.name AS shift_name,
            to_char(sh.start_time,'HH24:MI') AS shift_start,
            to_char(sh.end_time,  'HH24:MI') AS shift_end,
            gu.department_id, d.name  AS department_name,
            d.directorate_id,  dr.name AS directorate_name,
            gu.section_id,    s.name   AS section_name,
            gu.unit_id,       u.name   AS unit_name,
            gu.created_at, gu.updated_at, gu.created_bs
        FROM global_users gu
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        LEFT JOIN directorates dr ON dr.id = d.directorate_id
        LEFT JOIN sections     s  ON s.id  = gu.section_id
        LEFT JOIN units        u  ON u.id  = gu.unit_id
        LEFT JOIN shifts       sh ON sh.id = gu.shift_id
        {w}
        ORDER BY
            CASE WHEN gu.employee_id ~ '^[0-9]+$' THEN gu.employee_id::INTEGER ELSE NULL END NULLS LAST,
            gu.employee_id, gu.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def get_global_user(conn, db_id: int) -> dict | None:
    sql = """
        SELECT
            gu.id, gu.global_user_id, gu.employee_id, gu.name,
            gu.privilege, gu.card, gu.bank_number, gu.email, gu.phone,
            gu.shift_id, sh.name AS shift_name,
            gu.department_id, d.name AS department_name,
            d.directorate_id, dr.name AS directorate_name,
            gu.section_id, s.name AS section_name,
            gu.unit_id, u.name AS unit_name,
            gu.created_at, gu.updated_at
        FROM global_users gu
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        LEFT JOIN directorates dr ON dr.id = d.directorate_id
        LEFT JOIN sections     s  ON s.id  = gu.section_id
        LEFT JOIN units        u  ON u.id  = gu.unit_id
        LEFT JOIN shifts       sh ON sh.id = gu.shift_id
        WHERE gu.id = %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (db_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_global_user(conn, global_user_id: str, name: str, privilege: int = 0,
                        card: str | None = None, app_user_id: int = 0,
                        employee_id: str | None = None, bank_number: str | None = None,
                        email: str | None = None, phone: str | None = None,
                        department_id: int | None = None, section_id: int | None = None,
                        unit_id: int | None = None, shift_id: int | None = None,
                        fingerprint_data: str | None = None) -> int:
    sql = """
        INSERT INTO global_users
            (global_user_id, employee_id, name, privilege, card,
             bank_number, email, phone, department_id, section_id, unit_id,
             shift_id, fingerprint_data, created_bs, updated_bs, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        today = _today_bs()
        cur.execute(sql, (
            global_user_id, employee_id or None, name, int(privilege), card,
            bank_number or None, email or None, phone or None,
            department_id or None, section_id or None, unit_id or None,
            shift_id or None, fingerprint_data or None,
            today, today, app_user_id or None, app_user_id or None,
        ))
        return cur.fetchone()[0]


def update_global_user(conn, db_id: int, data: dict, app_user_id: int = 0) -> None:
    sql = """
        UPDATE global_users SET
            global_user_id   = %s,
            employee_id      = %s,
            name             = %s,
            privilege        = %s,
            card             = %s,
            bank_number      = %s,
            email            = %s,
            phone            = %s,
            department_id    = %s,
            section_id       = %s,
            unit_id          = %s,
            shift_id         = %s,
            updated_at       = NOW(),
            updated_bs       = %s,
            updated_by       = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            data.get('global_user_id'),
            data.get('employee_id') or None,
            data.get('name'),
            int(data.get('privilege', 0)),
            data.get('card') or None,
            data.get('bank_number') or None,
            data.get('email') or None,
            data.get('phone') or None,
            data.get('department_id') or None,
            data.get('section_id') or None,
            data.get('unit_id') or None,
            data.get('shift_id') or None,
            _today_bs(),
            app_user_id or None,
            db_id,
        ))


def delete_global_user(conn, global_user_id: int) -> None:
    sql = "DELETE FROM global_users WHERE id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (global_user_id,))


def get_employee_with_device(conn, emp_id: int):
    """Return employee row joined with device info (ip_address, port, password, name)."""
    sql = """
        SELECT e.id, e.uid, e.user_id, e.name, e.privilege, e.card, e.device_id,
               d.ip_address, d.port, d.password, d.model, d.name AS device_name, d.is_active
        FROM employees e
        JOIN devices d ON e.device_id = d.id
        WHERE e.id = %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (emp_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_employees_with_device(conn, emp_ids: list):
    """Return multiple employee rows joined with device info."""
    if not emp_ids:
        return []
    sql = """
        SELECT e.id, e.uid, e.user_id, e.name, e.privilege, e.card, e.device_id,
               d.ip_address, d.port, d.password, d.model, d.name AS device_name, d.is_active
        FROM employees e
        JOIN devices d ON e.device_id = d.id
        WHERE e.id = ANY(%s)
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (emp_ids,))
        return [dict(row) for row in cur.fetchall()]


def delete_employee_record(conn, emp_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM employees WHERE id = %s", (emp_id,))


def bulk_delete_employee_records(conn, emp_ids: list) -> int:
    if not emp_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM employees WHERE id = ANY(%s)", (emp_ids,))
        return cur.rowcount


def get_employee_daily_attendance(conn, device_id: int, user_id: str, from_date: str, to_date: str) -> list:
    """Per-day punch list for one employee in an AD date range.

    Returns list of dicts: {work_date, first_punch, last_punch, all_punches}
    Timestamps are returned already converted to NPT (Asia/Kathmandu).
    """
    sql = """
        SELECT
            DATE(timestamp AT TIME ZONE 'Asia/Kathmandu') AS work_date,
            MIN(timestamp AT TIME ZONE 'Asia/Kathmandu') AS first_punch,
            MAX(timestamp AT TIME ZONE 'Asia/Kathmandu') AS last_punch,
            ARRAY_AGG(
                (timestamp AT TIME ZONE 'Asia/Kathmandu')::time
                ORDER BY timestamp
            ) AS all_punch_times
        FROM attendance_logs
        WHERE device_id = %s AND user_id = %s
          AND DATE(timestamp AT TIME ZONE 'Asia/Kathmandu') BETWEEN %s AND %s
        GROUP BY DATE(timestamp AT TIME ZONE 'Asia/Kathmandu')
        ORDER BY work_date
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (device_id, user_id, from_date, to_date))
        return [dict(r) for r in cur.fetchall()]


def get_employees_for_device(conn, device_id: int) -> list:
    """Return all employees for a device ordered by name."""
    sql = "SELECT id, uid, user_id, name FROM employees WHERE device_id = %s ORDER BY name"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (device_id,))
        return [dict(r) for r in cur.fetchall()]


def get_employees_for_report(conn) -> list:
    """
    Return employees grouped by global identity for the monthly report picker.
    Employees linked to global_users are merged into one entry (all devices combined).
    Unlinked employees are grouped by normalised name.
    Each item: {key, display_name, company_id, global_id, devices:[{device_id,device_name,user_id}]}
    """
    sql = """
        SELECT
            COALESCE(gu.name, e.name, e.user_id) AS display_name,
            gu.id                                  AS global_id,
            gu.global_user_id                      AS company_id,
            dept.id                                AS department_id,
            dept.name                              AS department_name,
            dir.id                                 AS directorate_id,
            dir.name                               AS directorate_name,
            sect.id                                AS section_id,
            sect.name                              AS section_name,
            unt.id                                 AS unit_id,
            unt.name                               AS unit_name,
            e.device_id,
            e.user_id,
            d.name                                 AS device_name
        FROM employees e
        JOIN devices d ON e.device_id = d.id
        JOIN global_users gu   ON e.global_user_id    = gu.id
        LEFT JOIN departments dept ON gu.department_id   = dept.id
        LEFT JOIN directorates dir ON dept.directorate_id = dir.id
        LEFT JOIN sections    sect ON gu.section_id      = sect.id
        LEFT JOIN units       unt  ON gu.unit_id         = unt.id
        ORDER BY
            CASE WHEN gu.global_user_id ~ '^[0-9]+$'
                 THEN gu.global_user_id::INTEGER ELSE NULL END NULLS LAST,
            gu.global_user_id NULLS LAST,
            LOWER(COALESCE(gu.name, e.name, e.user_id)), d.name
    """
    from collections import OrderedDict
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]

    groups = OrderedDict()
    for r in rows:
        gid = r['global_id']
        key = f"g{gid}" if gid else f"n:{(r['display_name'] or '').lower().strip()}"
        if key not in groups:
            groups[key] = {
                'key':              key,
                'display_name':     r['display_name'] or '(Unknown)',
                'company_id':       r.get('company_id') or '',
                'global_id':        gid,
                'department_id':    r.get('department_id'),
                'department_name':  r.get('department_name') or '',
                'directorate_id':   r.get('directorate_id'),
                'directorate_name': r.get('directorate_name') or '',
                'section_id':       r.get('section_id'),
                'section_name':     r.get('section_name') or '',
                'unit_id':          r.get('unit_id'),
                'unit_name':        r.get('unit_name') or '',
                'devices':          [],
            }
        groups[key]['devices'].append({
            'device_id':   r['device_id'],
            'device_name': r['device_name'],
            'user_id':     str(r['user_id']),
        })
    return list(groups.values())


def get_employee_daily_attendance_multi(conn, device_user_pairs: list,
                                        from_date: str, to_date: str) -> list:
    """
    Multi-device attendance for one logical employee.
    Deduplicates punches within 60 seconds (same person, multiple readers).
    Returns: [{work_date, first_punch, last_punch,
               all_punch_times, all_punches_with_device}]
    """
    if not device_user_pairs:
        return []

    placeholders = " OR ".join(
        "(al.device_id = %s AND al.user_id = %s)" for _ in device_user_pairs
    )
    pair_params = [x for d, u in device_user_pairs for x in (int(d), str(u))]

    sql = f"""
        SELECT
            al.timestamp AT TIME ZONE 'Asia/Kathmandu' AS ts_npt,
            DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS work_date,
            d.name AS device_name
        FROM attendance_logs al
        JOIN devices d ON al.device_id = d.id
        WHERE ({placeholders})
          AND DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') BETWEEN %s AND %s
        ORDER BY ts_npt
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, pair_params + [from_date, to_date])
        rows = [dict(r) for r in cur.fetchall()]

    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['work_date']].append(r)

    result = []
    for work_date in sorted(by_date.keys()):
        day_rows = sorted(by_date[work_date], key=lambda x: x['ts_npt'])

        # 60-second deduplication
        deduped = []
        prev_ts = None
        for p in day_rows:
            ts = p['ts_npt']
            if prev_ts is None or (ts - prev_ts).total_seconds() >= 60:
                deduped.append(p)
                prev_ts = ts

        if not deduped:
            continue

        result.append({
            'work_date':  work_date,
            'first_punch': deduped[0]['ts_npt'],
            'last_punch':  deduped[-1]['ts_npt'],
            'all_punch_times': [p['ts_npt'].time() for p in deduped],
            'all_punches_with_device': [
                {'time': p['ts_npt'].time(), 'device_name': p['device_name']}
                for p in deduped
            ],
        })
    return result


def backfill_bs_dates(conn) -> int:
    """Backfill bs_date in attendance_logs for rows missing it. Safe to run repeatedly."""
    try:
        import zoneinfo
        from nepali_utils import ad_to_bs as _a2b
        NPT = zoneinfo.ZoneInfo('Asia/Kathmandu')
    except Exception:
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, timestamp FROM attendance_logs "
            "WHERE bs_date IS NULL OR bs_date = '' LIMIT 20000"
        )
        rows = cur.fetchall()

    if not rows:
        return 0

    updates = []
    for row_id, ts in rows:
        if ts:
            try:
                if hasattr(ts, 'tzinfo') and ts.tzinfo:
                    ts = ts.astimezone(NPT)
                bs = _a2b(ts.strftime('%Y-%m-%d'))
                if bs:
                    updates.append((bs, row_id))
            except Exception:
                pass

    if updates:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE attendance_logs SET bs_date = %s WHERE id = %s",
                updates,
                page_size=500,
            )
        logger.info("backfill_bs_dates: filled %d rows.", len(updates))
    conn.commit()
    return len(updates)


def get_pull_sessions(conn, limit: int = 100):
    sql = "SELECT ps.*, d.name as device_name FROM pull_sessions ps JOIN devices d ON ps.device_id = d.id ORDER BY ps.started_at DESC LIMIT %s"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (limit,))
        return [dict(row) for row in cur.fetchall()]


# ─── Departments ─────────────────────────────────────────────────────────────

def get_all_departments(conn) -> list:
    sql = """
        SELECT d.id, d.name, d.directorate_id, dr.name AS directorate_name
        FROM departments d
        LEFT JOIN directorates dr ON dr.id = d.directorate_id
        ORDER BY d.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]

def create_department(conn, name: str, app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO departments (name, created_by) VALUES (%s, %s) RETURNING id",
                    (name.strip(), app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]

def update_department(conn, dept_id: int, name: str):
    with conn.cursor() as cur:
        cur.execute("UPDATE departments SET name=%s WHERE id=%s", (name.strip(), dept_id))
    conn.commit()

def delete_department(conn, dept_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM departments WHERE id=%s", (dept_id,))
    conn.commit()


# ─── Shifts ──────────────────────────────────────────────────────────────────

def get_all_shifts(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT id, name,
                   to_char(start_time, 'HH24:MI') AS start_time,
                   to_char(end_time,   'HH24:MI') AS end_time
            FROM shifts ORDER BY start_time, name
        """)
        return [dict(r) for r in cur.fetchall()]

def create_shift(conn, name: str, start_time: str, end_time: str, app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO shifts (name, start_time, end_time, created_by) VALUES (%s, %s, %s, %s) RETURNING id",
            (name.strip(), start_time, end_time, app_user_id or None),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]

def update_shift(conn, shift_id: int, name: str, start_time: str, end_time: str):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE shifts SET name=%s, start_time=%s, end_time=%s WHERE id=%s",
            (name.strip(), start_time, end_time, shift_id),
        )
    conn.commit()

def delete_shift(conn, shift_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shifts WHERE id=%s", (shift_id,))
    conn.commit()


# ─── Shift Rules ─────────────────────────────────────────────────────────────

def get_all_shift_rules(conn) -> list:
    sql = """
        SELECT
            sr.id,
            sh.id   AS shift_id,
            sh.name AS shift_name,
            to_char(sh.start_time, 'HH24:MI') AS start_time,
            to_char(sh.end_time,   'HH24:MI') AS end_time,
            sr.from_date::text  AS from_date,
            sr.to_date::text    AS to_date,
            gu.name  AS employee_name,
            gu.id    AS global_user_id,
            d.name   AS department_name,
            d.id     AS department_id,
            dr.name  AS directorate_name,
            dr.id    AS directorate_id,
            s.name   AS section_name,
            s.id     AS section_id,
            u.name   AS unit_name,
            u.id     AS unit_id
        FROM shift_rules sr
        JOIN shifts sh       ON sh.id  = sr.shift_id
        LEFT JOIN global_users gu ON gu.id  = sr.global_user_id
        LEFT JOIN departments   d  ON d.id   = sr.department_id
        LEFT JOIN directorates  dr ON dr.id  = sr.directorate_id
        LEFT JOIN sections      s  ON s.id   = sr.section_id
        LEFT JOIN units         u  ON u.id   = sr.unit_id
        ORDER BY sr.from_date DESC, sr.id DESC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]

def create_shift_rule(conn, shift_id: int, from_date: str, to_date=None,
                      global_user_id=None, department_id=None,
                      directorate_id=None, section_id=None, unit_id=None,
                      app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO shift_rules
                (shift_id, from_date, to_date, global_user_id, department_id,
                 directorate_id, section_id, unit_id, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (int(shift_id), from_date, to_date or None,
              global_user_id, department_id, directorate_id, section_id, unit_id,
              app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]

def delete_shift_rule(conn, rule_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM shift_rules WHERE id=%s", (rule_id,))
    conn.commit()

def set_employee_org(conn, global_user_id: int, department_id=None,
                     section_id=None, unit_id=None):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE global_users
            SET department_id=%s, section_id=%s, unit_id=%s
            WHERE id=%s
        """, (department_id or None, section_id or None, unit_id or None, global_user_id))
    conn.commit()

def get_all_global_users_with_dept(conn) -> list:
    sql = """
        SELECT gu.id, gu.global_user_id, gu.global_user_id AS company_id,
               gu.employee_id, gu.name,
               gu.department_id, d.name  AS department_name,
               d.directorate_id, dr.name AS directorate_name,
               gu.section_id,   s.name   AS section_name,
               gu.unit_id,      u.name   AS unit_name
        FROM global_users gu
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        LEFT JOIN directorates dr ON dr.id = d.directorate_id
        LEFT JOIN sections     s  ON s.id  = gu.section_id
        LEFT JOIN units        u  ON u.id  = gu.unit_id
        ORDER BY LOWER(COALESCE(gu.name, gu.global_user_id, ''))
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def get_shift_calendar(conn, global_user_id: int, from_ad: str, to_ad: str) -> dict:
    """
    Returns {date_obj: {name, start_min, end_min}} for the date range.
    Priority: employee (5) > unit (4) > section (3) > department (2) > directorate (1).
    """
    if not global_user_id:
        return {}

    sql = """
        WITH emp_org AS (
            SELECT gu.department_id, gu.section_id, gu.unit_id,
                   d.directorate_id
            FROM global_users gu
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE gu.id = %(uid)s
        ),
        emp_rules AS (
            SELECT sh.start_time, sh.end_time, sh.name, sr.from_date, sr.to_date, 5 AS prio
            FROM shift_rules sr JOIN shifts sh ON sh.id = sr.shift_id
            WHERE sr.global_user_id = %(uid)s
              AND sr.from_date <= %(to_ad)s AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        ),
        unit_rules AS (
            SELECT sh.start_time, sh.end_time, sh.name, sr.from_date, sr.to_date, 4 AS prio
            FROM shift_rules sr JOIN shifts sh ON sh.id = sr.shift_id
            JOIN emp_org eo ON eo.unit_id IS NOT NULL AND eo.unit_id = sr.unit_id
            WHERE sr.from_date <= %(to_ad)s AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        ),
        sec_rules AS (
            SELECT sh.start_time, sh.end_time, sh.name, sr.from_date, sr.to_date, 3 AS prio
            FROM shift_rules sr JOIN shifts sh ON sh.id = sr.shift_id
            JOIN emp_org eo ON eo.section_id IS NOT NULL AND eo.section_id = sr.section_id
            WHERE sr.from_date <= %(to_ad)s AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        ),
        dept_rules AS (
            SELECT sh.start_time, sh.end_time, sh.name, sr.from_date, sr.to_date, 2 AS prio
            FROM shift_rules sr JOIN shifts sh ON sh.id = sr.shift_id
            JOIN emp_org eo ON eo.department_id IS NOT NULL AND eo.department_id = sr.department_id
            WHERE sr.from_date <= %(to_ad)s AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        ),
        dir_rules AS (
            SELECT sh.start_time, sh.end_time, sh.name, sr.from_date, sr.to_date, 1 AS prio
            FROM shift_rules sr JOIN shifts sh ON sh.id = sr.shift_id
            JOIN emp_org eo ON eo.directorate_id IS NOT NULL AND eo.directorate_id = sr.directorate_id
            WHERE sr.from_date <= %(to_ad)s AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        )
        SELECT * FROM emp_rules
        UNION ALL SELECT * FROM unit_rules
        UNION ALL SELECT * FROM sec_rules
        UNION ALL SELECT * FROM dept_rules
        UNION ALL SELECT * FROM dir_rules
        ORDER BY prio DESC, from_date DESC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, {'uid': global_user_id, 'from_ad': from_ad, 'to_ad': to_ad})
        rules = [dict(r) for r in cur.fetchall()]

    if not rules:
        return {}

    from datetime import date, timedelta

    def _to_min(t):
        if hasattr(t, 'hour'):
            return t.hour * 60 + t.minute
        h, m = str(t)[:5].split(':')
        return int(h) * 60 + int(m)

    def _to_date(x):
        if x is None or isinstance(x, date):
            return x
        return date.fromisoformat(str(x))

    from_d = date.fromisoformat(from_ad)
    to_d   = date.fromisoformat(to_ad)
    result = {}
    d = from_d
    while d <= to_d:
        for r in rules:
            fd = _to_date(r['from_date'])
            td = _to_date(r['to_date'])
            if fd <= d and (td is None or td >= d):
                result[d] = {
                    'name':      r['name'],
                    'start_min': _to_min(r['start_time']),
                    'end_min':   _to_min(r['end_time']),
                }
                break
        d += timedelta(days=1)
    return result


# ─── Directorates ─────────────────────────────────────────────────────────────

def get_all_directorates(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, name FROM directorates ORDER BY name")
        return [dict(r) for r in cur.fetchall()]

def create_directorate(conn, name: str, app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO directorates (name, created_by) VALUES (%s, %s) RETURNING id",
                    (name.strip(), app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]

def delete_directorate(conn, did: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM directorates WHERE id=%s", (did,))
    conn.commit()


# ─── Sections ─────────────────────────────────────────────────────────────────

def get_all_sections(conn) -> list:
    sql = """
        SELECT s.id, s.name, s.department_id,
               d.name AS department_name, d.directorate_id,
               dr.name AS directorate_name
        FROM sections s
        LEFT JOIN departments d  ON d.id  = s.department_id
        LEFT JOIN directorates dr ON dr.id = d.directorate_id
        ORDER BY dr.name NULLS LAST, d.name NULLS LAST, s.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]

def create_section(conn, name: str, department_id: int, app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO sections (name, department_id, created_by) VALUES (%s, %s, %s) RETURNING id",
                    (name.strip(), department_id, app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]

def delete_section(conn, sid: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sections WHERE id=%s", (sid,))
    conn.commit()


# ─── Units ────────────────────────────────────────────────────────────────────

def get_all_units(conn) -> list:
    sql = """
        SELECT u.id, u.name, u.section_id,
               s.name AS section_name, s.department_id,
               d.name AS department_name
        FROM units u
        LEFT JOIN sections     s ON s.id = u.section_id
        LEFT JOIN departments  d ON d.id = s.department_id
        ORDER BY d.name NULLS LAST, s.name NULLS LAST, u.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]

def create_unit(conn, name: str, section_id: int, app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO units (name, section_id, created_by) VALUES (%s, %s, %s) RETURNING id",
                    (name.strip(), section_id, app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]

def delete_unit(conn, uid: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM units WHERE id=%s", (uid,))
    conn.commit()


# ─── Leave Types ──────────────────────────────────────────────────────────────

def get_all_leave_types(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM leave_types ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def create_leave_type(conn, name: str, code: str, days_per_year: float,
                      max_accumulate: float, carry_forward: bool,
                      is_paid: bool, description: str = '') -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_types (name, code, days_per_year, max_accumulate,
                                     carry_forward, is_paid, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (name.strip(), code.strip().upper(), float(days_per_year),
              float(max_accumulate), bool(carry_forward), bool(is_paid), description or ''))
        row = cur.fetchone()
    conn.commit()
    return row[0]


def update_leave_type(conn, lt_id: int, name: str, days_per_year: float,
                      max_accumulate: float, carry_forward: bool,
                      is_paid: bool, description: str = ''):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE leave_types
            SET name=%s, days_per_year=%s, max_accumulate=%s,
                carry_forward=%s, is_paid=%s, description=%s
            WHERE id=%s
        """, (name.strip(), float(days_per_year), float(max_accumulate),
              bool(carry_forward), bool(is_paid), description or '', lt_id))
    conn.commit()


def delete_leave_type(conn, lt_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM leave_types WHERE id=%s", (lt_id,))
    conn.commit()


# ─── Leave Balances ───────────────────────────────────────────────────────────

def get_leave_balances(conn, bs_year: int) -> list:
    sql = """
        SELECT lb.*,
               gu.name           AS employee_name,
               gu.global_user_id AS company_id,
               gu.employee_id    AS employee_id,
               d.name            AS department_name,
               lt.name           AS leave_type_name, lt.code,
               (lb.opening_balance + lb.days_earned - lb.days_taken) AS available
        FROM leave_balances lb
        JOIN global_users gu ON gu.id = lb.global_user_id
        JOIN leave_types  lt ON lt.id = lb.leave_type_id
        LEFT JOIN departments d ON d.id = gu.department_id
        WHERE lb.bs_year = %s
        ORDER BY
            CASE WHEN gu.global_user_id ~ '^[0-9]+$'
                 THEN gu.global_user_id::INTEGER ELSE NULL END NULLS LAST,
            gu.name, lt.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (bs_year,))
        return [dict(r) for r in cur.fetchall()]


def get_employee_leave_balance(conn, global_user_id: int, bs_year: int) -> list:
    sql = """
        SELECT lt.id AS leave_type_id, lt.name AS leave_type_name, lt.code,
               lt.days_per_year, lt.carry_forward, lt.is_paid,
               COALESCE(lb.opening_balance, 0) AS opening_balance,
               COALESCE(lb.days_earned,     lt.days_per_year) AS days_earned,
               COALESCE(lb.days_taken,      0) AS days_taken,
               COALESCE(lb.opening_balance, 0) + COALESCE(lb.days_earned, lt.days_per_year)
                   - COALESCE(lb.days_taken, 0) AS available
        FROM leave_types lt
        LEFT JOIN leave_balances lb ON lb.leave_type_id = lt.id
            AND lb.global_user_id = %s AND lb.bs_year = %s
        ORDER BY lt.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_id, bs_year))
        return [dict(r) for r in cur.fetchall()]


def allocate_annual_leaves(conn, bs_year: int) -> int:
    """Create or refresh leave_balances for all employees for the given BS year.
    Returns count of rows upserted."""
    employees  = get_all_global_users_with_dept(conn)
    ltypes     = get_all_leave_types(conn)
    count      = 0
    for emp in employees:
        for lt in ltypes:
            opening = 0.0
            if lt['carry_forward'] and lt['days_per_year'] > 0:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT GREATEST(0,
                            COALESCE(opening_balance,0) + COALESCE(days_earned,0)
                            - COALESCE(days_taken,0))
                        FROM leave_balances
                        WHERE global_user_id=%s AND leave_type_id=%s AND bs_year=%s
                    """, (emp['id'], lt['id'], bs_year - 1))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        cap     = lt['max_accumulate'] or 999
                        opening = float(min(float(row[0]), cap))
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO leave_balances
                        (global_user_id, leave_type_id, bs_year, opening_balance, days_earned)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (global_user_id, leave_type_id, bs_year) DO UPDATE
                        SET days_earned = EXCLUDED.days_earned
                """, (emp['id'], lt['id'], bs_year, opening, float(lt['days_per_year'])))
            count += 1
    conn.commit()
    return count


# ─── Leave Applications ───────────────────────────────────────────────────────

def get_leave_applications(conn, global_user_id=None, status=None,
                            from_ad=None, to_ad=None, limit: int = 300,
                            department_id=None, section_id=None,
                            directorate_id=None) -> list:
    sql = """
        SELECT la.*,
               gu.name        AS employee_name,
               gu.global_user_id AS company_id,
               gu.employee_id AS employee_id,
               d.name         AS department_name,
               d.id           AS department_id,
               dir.name       AS directorate_name,
               sec.name       AS section_name,
               lt.name        AS leave_type_name,
               lt.code        AS leave_code,
               lt.display_code AS leave_display_code,
               lt.is_paid
        FROM leave_applications la
        JOIN global_users gu  ON gu.id  = la.global_user_id
        JOIN leave_types  lt  ON lt.id  = la.leave_type_id
        LEFT JOIN departments  d   ON d.id   = gu.department_id
        LEFT JOIN directorates dir ON dir.id = d.directorate_id
        LEFT JOIN sections     sec ON sec.id = gu.section_id
        WHERE 1=1
    """
    params: list = []
    if global_user_id:
        sql += " AND la.global_user_id = %s"
        params.append(global_user_id)
    if status:
        sql += " AND la.status = %s"
        params.append(status)
    if from_ad:
        sql += " AND la.to_ad >= %s"
        params.append(from_ad)
    if to_ad:
        sql += " AND la.from_ad <= %s"
        params.append(to_ad)
    if directorate_id:
        sql += " AND d.directorate_id = %s"
        params.append(directorate_id)
    if department_id:
        sql += " AND gu.department_id = %s"
        params.append(department_id)
    if section_id:
        sql += " AND gu.section_id = %s"
        params.append(section_id)
    sql += " ORDER BY la.created_at DESC LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_leaves_for_date(conn, date_ad: str) -> list:
    sql = """
        SELECT la.global_user_id, la.days, la.reason,
               gu.name AS employee_name, gu.global_user_id AS company_id,
               d.name  AS department_name,
               lt.name AS leave_type_name, lt.code
        FROM leave_applications la
        JOIN global_users gu ON gu.id = la.global_user_id
        JOIN leave_types  lt ON lt.id = la.leave_type_id
        LEFT JOIN departments d ON d.id = gu.department_id
        WHERE la.from_ad <= %s AND la.to_ad >= %s AND la.status = 'approved'
        ORDER BY d.name NULLS LAST, gu.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (date_ad, date_ad))
        return [dict(r) for r in cur.fetchall()]


def create_leave_application(conn, global_user_id: int, leave_type_id: int,
                              from_bs: str, to_bs: str,
                              from_ad: str, to_ad: str,
                              days: float, reason: str = '',
                              applied_bs: str = '',
                              status: str = 'pending',
                              app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_applications
                (global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad,
                 days, reason, applied_bs, applied_ad, status, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, %s)
            RETURNING id
        """, (global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad,
              float(days), reason or '', applied_bs or '', status,
              app_user_id or None, app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    if status == 'approved':
        _update_balance_taken(conn, global_user_id, leave_type_id, from_ad, float(days))
    return row[0]


def update_leave_status(conn, app_id: int, status: str,
                        remarks: str = '', approved_by: str = '',
                        app_user_id: int = 0):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT global_user_id, leave_type_id, from_ad, days, status AS old_status
            FROM leave_applications WHERE id=%s
        """, (app_id,))
        row = cur.fetchone()
    if not row:
        return
    g_id, lt_id, from_ad, days, old_status = row
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE leave_applications
            SET status=%s, remarks=%s, approved_by=%s, updated_by=%s
            WHERE id=%s
        """, (status, remarks or '', approved_by or '', app_user_id or None, app_id))
    conn.commit()
    # Maintain days_taken balance
    if old_status != 'approved' and status == 'approved':
        _update_balance_taken(conn, g_id, lt_id, from_ad, float(days))
    elif old_status == 'approved' and status != 'approved':
        _update_balance_taken(conn, g_id, lt_id, from_ad, -float(days))


def _update_balance_taken(conn, global_user_id: int, leave_type_id: int,
                           from_ad, delta: float):
    """Add delta (positive = more taken, negative = reversal) to leave balance days_taken."""
    try:
        from nepali_utils import ad_to_bs_tuple
        bs = ad_to_bs_tuple(from_ad)
        bs_year = bs[0] if bs else None
    except Exception:
        bs_year = None
    if not bs_year:
        return
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_balances (global_user_id, leave_type_id, bs_year, days_taken)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (global_user_id, leave_type_id, bs_year) DO UPDATE
                SET days_taken = GREATEST(0, leave_balances.days_taken + EXCLUDED.days_taken)
        """, (global_user_id, leave_type_id, bs_year, delta))
    conn.commit()


def delete_leave_application(conn, app_id: int):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT global_user_id, leave_type_id, from_ad, days, status
            FROM leave_applications WHERE id=%s
        """, (app_id,))
        row = cur.fetchone()
    if row:
        g_id, lt_id, from_ad, days, status = row
        if status == 'approved':
            _update_balance_taken(conn, g_id, lt_id, from_ad, -float(days))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM leave_applications WHERE id=%s", (app_id,))
    conn.commit()


def count_leave_working_days(from_ad: str, to_ad: str,
                              holiday_dates: list | None = None) -> float:
    """Count Mon–Fri non-holiday days between two AD dates inclusive.
    Nepal: Saturday is the only fixed weekly off day (isoweekday()%7 == 6)."""
    from datetime import date, timedelta
    hset = set()
    for h in (holiday_dates or []):
        if isinstance(h, str):
            hset.add(h)
        elif hasattr(h, 'isoformat'):
            hset.add(h.isoformat())
        elif isinstance(h, dict):
            v = h.get('holiday_ad')
            if v:
                hset.add(v.isoformat() if hasattr(v, 'isoformat') else str(v))
    from_d = date.fromisoformat(from_ad)
    to_d   = date.fromisoformat(to_ad)
    days   = 0.0
    cur    = from_d
    while cur <= to_d:
        if cur.isoweekday() % 7 != 6 and cur.isoformat() not in hset:
            days += 1.0
        cur += timedelta(days=1)
    return days


# ─── Holidays ─────────────────────────────────────────────────────────────────

def get_holidays(conn, from_ad: str | None = None, to_ad: str | None = None) -> list:
    sql    = "SELECT * FROM holidays WHERE 1=1"
    params: list = []
    if from_ad:
        sql += " AND holiday_ad >= %s"
        params.append(from_ad)
    if to_ad:
        sql += " AND holiday_ad <= %s"
        params.append(to_ad)
    sql += " ORDER BY holiday_ad"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def create_holiday(conn, name: str, holiday_ad: str, holiday_bs: str,
                   holiday_type: str = 'public', description: str = '',
                   app_user_id: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO holidays (name, holiday_ad, holiday_bs, holiday_type, description, created_by)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (name.strip(), holiday_ad, holiday_bs, holiday_type, description or '',
              app_user_id or None))
        row = cur.fetchone()
    conn.commit()
    return row[0]


def delete_holiday(conn, h_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM holidays WHERE id=%s", (h_id,))
    conn.commit()


def update_holiday(conn, h_id: int, name: str, holiday_ad: str, holiday_bs: str,
                   holiday_type: str = 'public', description: str = ''):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE holidays
               SET name=%s, holiday_ad=%s, holiday_bs=%s, holiday_type=%s, description=%s
             WHERE id=%s
        """, (name.strip(), holiday_ad, holiday_bs, holiday_type, description or '', h_id))
    conn.commit()


# ─── Daily Attendance Summary ─────────────────────────────────────────────────

def get_daily_attendance_summary(conn, date_ad: str) -> dict:
    from datetime import date as _date
    from nepali_utils import ad_to_bs_tuple

    d_obj     = _date.fromisoformat(date_ad)
    is_saturday = (d_obj.isoweekday() % 7 == 6)
    bs_t      = ad_to_bs_tuple(d_obj)
    date_bs   = f"{bs_t[0]}-{bs_t[1]:02d}-{bs_t[2]:02d}" if bs_t else ''

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM holidays WHERE holiday_ad = %s", (date_ad,))
        h_row   = cur.fetchone()
    holiday = dict(h_row) if h_row else None

    all_emps = get_all_global_users_with_dept(conn)

    sql_pres = """
        SELECT
            gu.id                 AS global_user_id,
            gu.name               AS emp_name,
            gu.global_user_id     AS company_id,
            dept.name             AS department_name,
            sect.name             AS section_name,
            MIN(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS first_punch,
            MAX(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS last_punch
        FROM attendance_logs al
        JOIN employees e   ON e.device_id = al.device_id AND e.user_id = al.user_id
        JOIN global_users gu ON gu.id = e.global_user_id
        LEFT JOIN departments dept ON dept.id = gu.department_id
        LEFT JOIN sections    sect ON sect.id = gu.section_id
        WHERE DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') = %s
          AND gu.id IS NOT NULL
        GROUP BY gu.id, gu.name, gu.global_user_id, dept.name, sect.name
        ORDER BY dept.name NULLS LAST, gu.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql_pres, (date_ad,))
        pres_rows = [dict(r) for r in cur.fetchall()]

    present_map: dict = {}
    for row in pres_rows:
        uid = row['global_user_id']
        if uid not in present_map:
            present_map[uid] = dict(row)
        else:
            e = present_map[uid]
            if row['first_punch'] and (not e['first_punch'] or row['first_punch'] < e['first_punch']):
                e['first_punch'] = row['first_punch']
            if row['last_punch'] and (not e['last_punch'] or row['last_punch'] > e['last_punch']):
                e['last_punch'] = row['last_punch']

    present_ids = set(present_map.keys())
    leave_rows  = get_leaves_for_date(conn, date_ad)
    on_leave_map = {r['global_user_id']: r for r in leave_rows}
    on_leave_ids = set(on_leave_map.keys())

    present_list  = sorted(present_map.values(), key=lambda x: (x['department_name'] or '', x['emp_name'] or ''))
    on_leave_list = [r for r in leave_rows if r['global_user_id'] not in present_ids]
    absent_list: list = []

    if not is_saturday and not holiday:
        for emp in all_emps:
            uid = emp['id']
            if uid in present_ids or uid in on_leave_ids:
                continue
            absent_list.append({
                'global_user_id': uid,
                'emp_name':       emp.get('name') or '(unknown)',
                'company_id':     emp.get('company_id') or '',
                'department_name': emp.get('department_name') or '',
                'section_name':   emp.get('section_name') or '',
            })

    dept_summary: dict = {}
    for emp in present_list:
        dn = emp['department_name'] or 'No Department'
        dept_summary.setdefault(dn, {'present': 0, 'absent': 0, 'on_leave': 0})
        dept_summary[dn]['present'] += 1
    for emp in on_leave_list:
        dn = emp.get('department_name') or 'No Department'
        dept_summary.setdefault(dn, {'present': 0, 'absent': 0, 'on_leave': 0})
        dept_summary[dn]['on_leave'] += 1
    for emp in absent_list:
        dn = emp.get('department_name') or 'No Department'
        dept_summary.setdefault(dn, {'present': 0, 'absent': 0, 'on_leave': 0})
        dept_summary[dn]['absent'] += 1

    return {
        'date_ad':        date_ad,
        'date_bs':        date_bs,
        'is_saturday':    is_saturday,
        'is_holiday':     bool(holiday),
        'holiday':        holiday,
        'total_employees': len(all_emps),
        'present':        present_list,
        'on_leave':       on_leave_list,
        'absent':         absent_list,
        'dept_summary':   dict(sorted(dept_summary.items())),
        'totals': {
            'present':  len(present_list),
            'on_leave': len(on_leave_list),
            'absent':   len(absent_list),
        },
    }


# ─── Monthly Attendance Summary ───────────────────────────────────────────────

def get_monthly_attendance_summary(conn, bs_year: int, bs_month: int) -> dict:
    from datetime import date as _date, timedelta
    from collections import defaultdict
    from nepali_utils import bs_month_info, ad_to_bs_tuple

    mi = bs_month_info(bs_year, bs_month)
    if not mi:
        return {}

    from_ad = mi['first_ad']
    to_ad   = mi['last_ad']

    holiday_rows = get_holidays(conn, from_ad, to_ad)
    holiday_map: dict = {}
    for h in holiday_rows:
        k = h['holiday_ad'].isoformat() if hasattr(h['holiday_ad'], 'isoformat') else str(h['holiday_ad'])
        holiday_map[k] = h

    all_emps = get_all_global_users_with_dept(conn)

    sql_att = """
        SELECT e.global_user_id,
               DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS work_date
        FROM attendance_logs al
        JOIN employees e ON e.device_id = al.device_id AND e.user_id = al.user_id
        WHERE DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') BETWEEN %s AND %s
          AND e.global_user_id IS NOT NULL
        GROUP BY e.global_user_id, work_date
    """
    with conn.cursor() as cur:
        cur.execute(sql_att, (from_ad, to_ad))
        att_rows = cur.fetchall()

    emp_attended: dict = defaultdict(set)
    for gid, wd in att_rows:
        emp_attended[gid].add(wd if isinstance(wd, _date) else _date.fromisoformat(str(wd)))

    leave_rows = get_leave_applications(conn, status='approved', from_ad=from_ad, to_ad=to_ad)
    emp_leaves: dict = defaultdict(list)
    for lr in leave_rows:
        fa = lr['from_ad'] if isinstance(lr['from_ad'], _date) else _date.fromisoformat(str(lr['from_ad']))
        ta = lr['to_ad']   if isinstance(lr['to_ad'],   _date) else _date.fromisoformat(str(lr['to_ad']))
        emp_leaves[lr['global_user_id']].append({
            'from': fa, 'to': ta,
            'type': lr['leave_type_name'], 'code': lr['leave_code'],
        })

    NEPAL_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    days_info: list = []
    d = _date.fromisoformat(from_ad)
    to_d = _date.fromisoformat(to_ad)
    while d <= to_d:
        dow        = d.isoweekday() % 7
        is_sat     = (dow == 6)
        ds         = d.isoformat()
        h          = holiday_map.get(ds)
        bs_t       = ad_to_bs_tuple(d)
        bs_day_num = bs_t[2] if bs_t else d.day
        days_info.append({
            'date':         d,
            'date_ad':      ds,
            'bs_day':       bs_day_num,
            'day_name':     NEPAL_DAYS[dow],
            'is_weekend':   is_sat,
            'is_holiday':   bool(h),
            'holiday_name': h['name'] if h else '',
            'holiday_type': h['holiday_type'] if h else '',
            'is_working':   not is_sat and not h,
        })
        d += timedelta(days=1)

    working_days_total = sum(1 for dd in days_info if dd['is_working'])

    emp_summaries: list = []
    for emp in all_emps:
        uid      = emp['id']
        attended = emp_attended.get(uid, set())
        leaves   = emp_leaves.get(uid, [])
        present  = 0
        absent   = 0
        on_leave = 0
        late     = 0
        leave_by_type: dict = defaultdict(float)

        for dd in days_info:
            if not dd['is_working']:
                continue
            d_obj = dd['date']
            on_lv = False
            for lv in leaves:
                if lv['from'] <= d_obj <= lv['to']:
                    on_lv = True
                    leave_by_type[lv['type']] += 1
                    break
            if d_obj in attended:
                present += 1
            elif on_lv:
                on_leave += 1
            else:
                absent += 1

        emp_summaries.append({
            'global_user_id':  uid,
            'emp_name':        emp.get('name') or '(unknown)',
            'company_id':      emp.get('company_id') or '',
            'department_name': emp.get('department_name') or '',
            'section_name':    emp.get('section_name') or '',
            'directorate_name': emp.get('directorate_name') or '',
            'unit_name':       emp.get('unit_name') or '',
            'present':         present,
            'absent':          absent,
            'on_leave':        on_leave,
            'working_days':    working_days_total,
            'leave_by_type':   dict(leave_by_type),
        })

    from nepali_utils import NEPALI_MONTHS
    return {
        'bs_year':        bs_year,
        'bs_month':       bs_month,
        'month_name':     NEPALI_MONTHS[bs_month] if 1 <= bs_month <= 12 else str(bs_month),
        'from_ad':        from_ad,
        'to_ad':          to_ad,
        'days':           days_info,
        'working_days':   working_days_total,
        'holiday_count':  sum(1 for dd in days_info if dd['is_holiday']),
        'weekend_count':  sum(1 for dd in days_info if dd['is_weekend']),
        'total_days':     len(days_info),
        'holidays':       holiday_rows,
        'employees':      emp_summaries,
    }


# ─── Attendance Daily Settlement ──────────────────────────────────────────────

_LEAVE_CODE_DISPLAY: dict = {
    'HOME':      'घ',
    'SICK':      'बि',
    'CASUAL':    'अ',
    'MATERNITY': 'म',
    'PATERNITY': 'पि',
    'MOURNING':  'शो',
    'STUDY':     'अध्',
    'UNPAID':    'X',
}


def get_punch_summary_for_global_user(conn, global_user_id: int,
                                      from_ad: str, to_ad: str) -> dict:
    """
    Returns {date_str: {first_in: time, last_out: time, punch_count: int}}
    grouped per day in Nepal time (Asia/Kathmandu).
    """
    sql = """
        SELECT
            (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date  AS work_date,
            MIN((al.timestamp AT TIME ZONE 'Asia/Kathmandu')::time) AS first_in,
            MAX((al.timestamp AT TIME ZONE 'Asia/Kathmandu')::time) AS last_out,
            COUNT(*) AS punch_count
        FROM attendance_logs al
        JOIN employees e ON al.employee_id = e.id
        WHERE e.global_user_id = %s
          AND (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date BETWEEN %s AND %s
        GROUP BY work_date
        ORDER BY work_date
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_id, from_ad, to_ad))
        return {str(r['work_date']): dict(r) for r in cur.fetchall()}


def settle_attendance_daily(conn, global_user_id: int,
                            from_ad: str, to_ad: str) -> int:
    """
    Compute daily attendance from attendance_logs and upsert into attendance_daily.
    Option A: non-destructive — rows with source='manual' are never overwritten.
    Returns number of days processed.
    """
    from datetime import date, timedelta

    punch_map   = get_punch_summary_for_global_user(conn, global_user_id, from_ad, to_ad)
    shift_cal   = get_shift_calendar(conn, global_user_id, from_ad, to_ad)
    holiday_map = {
        (h['holiday_ad'].isoformat() if hasattr(h['holiday_ad'], 'isoformat') else str(h['holiday_ad'])): h
        for h in get_holidays(conn, from_ad, to_ad)
    }

    # Build leave map {date_str: {code, display}}
    leave_map: dict = {}
    for la in get_leave_applications(conn, global_user_id=global_user_id,
                                     status='approved', from_ad=from_ad, to_ad=to_ad,
                                     limit=10000):
        lcode    = la.get('leave_code') or 'L'
        ldisplay = la.get('leave_display_code') or _LEAVE_CODE_DISPLAY.get(lcode, 'बि')
        ld = la['from_ad'] if isinstance(la['from_ad'], date) else date.fromisoformat(str(la['from_ad']))
        le = la['to_ad']   if isinstance(la['to_ad'],   date) else date.fromisoformat(str(la['to_ad']))
        while ld <= le:
            leave_map[ld.isoformat()] = {'code': lcode, 'display': ldisplay}
            ld += timedelta(days=1)

    def _t2m(t):
        if t is None:
            return None
        if hasattr(t, 'hour'):
            return t.hour * 60 + t.minute
        return None

    rows = []
    start = date.fromisoformat(from_ad)
    end   = date.fromisoformat(to_ad)
    d = start
    while d <= end:
        ds         = d.isoformat()
        nepal_dow  = d.isoweekday() % 7   # 0=Sun … 6=Sat
        is_weekend = (nepal_dow == 6)

        shift_info = (shift_cal or {}).get(d)
        si_min     = shift_info['start_min'] if shift_info else 600   # 10:00 default
        so_min     = shift_info['end_min']   if shift_info else 1020  # 17:00 default
        grace      = (shift_info.get('grace_late_in') or 0) if shift_info else 0

        holiday_entry = holiday_map.get(ds)
        is_holiday    = bool(holiday_entry)

        punch    = punch_map.get(ds)
        first_in = punch['first_in']  if punch else None
        last_out = punch['last_out']  if punch else None

        ci_min   = _t2m(first_in)
        co_min   = _t2m(last_out) if (last_out and last_out != first_in) else None
        work_min = (co_min - ci_min) if (ci_min is not None and co_min is not None and co_min > ci_min) else 0

        if is_weekend:
            status_code  = 'SAT'
            display_code = 'शनि'
        elif is_holiday:
            htype  = (holiday_entry or {}).get('holiday_type', 'public')
            htcode = (holiday_entry or {}).get('type_code', '')
            if htype == 'festival' or htcode == 'FEST':
                status_code  = 'FH'
                display_code = 'उत्'
            elif htcode == 'NAT':
                status_code  = 'NH'
                display_code = 'रा'
            elif htcode == 'OPT':
                status_code  = 'OH'
                display_code = 'वै'
            else:
                status_code  = 'PH'
                display_code = 'सा'
        elif punch:
            status_code  = 'P'
            display_code = '√'
        elif ds in leave_map:
            lv           = leave_map[ds]
            status_code  = lv['code']
            display_code = lv['display']
        else:
            status_code  = 'A'
            display_code = 'X'

        ot_min = late_in_min = early_out_min = 0
        if not is_weekend and not is_holiday and punch:
            if ci_min is not None and ci_min > si_min + grace:
                late_in_min = ci_min - si_min
            if co_min is not None:
                planned = so_min - si_min
                if co_min < so_min:
                    early_out_min = so_min - co_min
                elif work_min > planned:
                    ot_min = work_min - planned

        rows.append((
            global_user_id, d, status_code, display_code,
            first_in, last_out, work_min, ot_min, late_in_min, early_out_min,
        ))
        d += timedelta(days=1)

    if not rows:
        return 0

    upsert_sql = """
        INSERT INTO attendance_daily
            (global_user_id, work_date, status_code, display_code,
             first_in, last_out, work_minutes, ot_minutes,
             late_in_minutes, early_out_min, source, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'device', NOW())
        ON CONFLICT (global_user_id, work_date) DO UPDATE SET
            status_code     = EXCLUDED.status_code,
            display_code    = EXCLUDED.display_code,
            first_in        = EXCLUDED.first_in,
            last_out        = EXCLUDED.last_out,
            work_minutes    = EXCLUDED.work_minutes,
            ot_minutes      = EXCLUDED.ot_minutes,
            late_in_minutes = EXCLUDED.late_in_minutes,
            early_out_min   = EXCLUDED.early_out_min,
            computed_at     = NOW()
        WHERE attendance_daily.source != 'manual'
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, upsert_sql, rows, page_size=100)
    return len(rows)


def settle_all_attendance_daily(conn, from_ad: str, to_ad: str) -> dict:
    """
    Run settle_attendance_daily for every global_user who has punches in the range.
    Returns {'settled_days': n, 'users': m}.
    """
    sql = """
        SELECT DISTINCT e.global_user_id
        FROM attendance_logs al
        JOIN employees e ON al.employee_id = e.id
        WHERE e.global_user_id IS NOT NULL
          AND (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date BETWEEN %s AND %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (from_ad, to_ad))
        ids = [r[0] for r in cur.fetchall()]

    settled = 0
    for gid in ids:
        try:
            settled += settle_attendance_daily(conn, gid, from_ad, to_ad)
        except Exception as exc:
            logger.warning("settle_attendance_daily uid=%s: %s", gid, exc)
    return {'settled_days': settled, 'users': len(ids)}


def get_hajiri_data_from_logs(conn, global_user_ids: list,
                               from_ad: str, to_ad: str) -> dict:
    """
    Build an att_map dict directly from attendance_logs — no settlement required.
    Returns {global_user_id: {date_str: {status_code, display_code, first_in,
            last_out, work_minutes, ot_minutes, late_in_minutes, early_out_min}}}

    Uses 4 batch SQL queries (punches, holidays, leaves, shifts) — not N per-user.
    Status priority per day: Saturday > Holiday > Present > Leave > Absent.
    """
    from datetime import date, timedelta

    if not global_user_ids:
        return {}

    # ── 1. Batch punch query ──────────────────────────────────────────────────
    punch_sql = """
        SELECT
            e.global_user_id,
            (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date  AS work_date,
            MIN((al.timestamp AT TIME ZONE 'Asia/Kathmandu')::time) AS first_in,
            MAX((al.timestamp AT TIME ZONE 'Asia/Kathmandu')::time) AS last_out
        FROM attendance_logs al
        JOIN employees e
          ON al.device_id = e.device_id AND al.user_id = e.user_id
        WHERE e.global_user_id = ANY(%(uids)s)
          AND (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date
              BETWEEN %(from_ad)s AND %(to_ad)s
        GROUP BY e.global_user_id, work_date
        ORDER BY e.global_user_id, work_date
    """
    # punch_map: {global_user_id: {date_str: {first_in, last_out}}}
    punch_map: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(punch_sql, {'uids': global_user_ids,
                                'from_ad': from_ad, 'to_ad': to_ad})
        for r in cur.fetchall():
            gid = r['global_user_id']
            ds  = str(r['work_date'])
            punch_map.setdefault(gid, {})[ds] = {
                'first_in': r['first_in'],
                'last_out': r['last_out'],
            }

    # ── 2. Batch holiday query (with type_code) ───────────────────────────────
    holiday_sql = """
        SELECT h.id, h.holiday_ad, h.holiday_type, h.holiday_type_id,
               ht.type_code
        FROM holidays h
        LEFT JOIN holiday_types ht ON ht.id = h.holiday_type_id
        WHERE h.holiday_ad BETWEEN %s AND %s
    """
    holiday_map: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(holiday_sql, (from_ad, to_ad))
        for r in cur.fetchall():
            hd = r['holiday_ad']
            ds = hd.isoformat() if hasattr(hd, 'isoformat') else str(hd)
            holiday_map[ds] = dict(r)

    # ── 3. Batch leave query ──────────────────────────────────────────────────
    leave_sql = """
        SELECT la.global_user_id, la.from_ad, la.to_ad,
               lt.code AS leave_code,
               lt.display_code AS leave_display_code
        FROM leave_applications la
        JOIN leave_types lt ON lt.id = la.leave_type_id
        WHERE la.global_user_id = ANY(%s)
          AND la.status = 'approved'
          AND la.from_ad <= %s
          AND la.to_ad   >= %s
    """
    # leave_map_by_user: {global_user_id: {date_str: {code, display}}}
    leave_map_by_user: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(leave_sql, (global_user_ids, to_ad, from_ad))
        for r in cur.fetchall():
            gid    = r['global_user_id']
            lcode  = r['leave_code'] or 'L'
            ldisp  = r['leave_display_code'] or _LEAVE_CODE_DISPLAY.get(lcode, 'बि')
            ld = r['from_ad'] if isinstance(r['from_ad'], date) else date.fromisoformat(str(r['from_ad']))
            le = r['to_ad']   if isinstance(r['to_ad'],   date) else date.fromisoformat(str(r['to_ad']))
            while ld <= le:
                leave_map_by_user.setdefault(gid, {})[ld.isoformat()] = {
                    'code': lcode, 'display': ldisp,
                }
                ld += timedelta(days=1)

    # ── 4. Batch shift query (best-priority shift per user) ───────────────────
    shift_sql = """
        WITH uo AS (
            SELECT gu.id AS uid, gu.department_id, gu.section_id, gu.unit_id,
                   d.directorate_id
            FROM global_users gu
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE gu.id = ANY(%(uids)s)
        ),
        applicable AS (
            SELECT uo.uid AS gid, sr.shift_id, sr.from_date,
                CASE WHEN sr.global_user_id IS NOT NULL THEN 5
                     WHEN sr.unit_id        IS NOT NULL THEN 4
                     WHEN sr.section_id     IS NOT NULL THEN 3
                     WHEN sr.department_id  IS NOT NULL THEN 2
                     ELSE 1 END AS prio
            FROM uo
            JOIN shift_rules sr ON (
                sr.global_user_id = uo.uid
                OR (sr.unit_id        IS NOT NULL AND sr.unit_id        = uo.unit_id)
                OR (sr.section_id     IS NOT NULL AND sr.section_id     = uo.section_id)
                OR (sr.department_id  IS NOT NULL AND sr.department_id  = uo.department_id)
                OR (sr.directorate_id IS NOT NULL AND sr.directorate_id = uo.directorate_id)
            )
            WHERE sr.from_date <= %(to_ad)s
              AND (sr.to_date IS NULL OR sr.to_date >= %(from_ad)s)
        ),
        best AS (
            SELECT DISTINCT ON (gid) gid, shift_id
            FROM applicable
            ORDER BY gid, prio DESC, from_date DESC
        )
        SELECT b.gid AS global_user_id,
               s.start_time, s.end_time,
               COALESCE(s.grace_late_in,   0) AS grace_late_in,
               COALESCE(s.grace_early_out, 0) AS grace_early_out
        FROM best b
        JOIN shifts s ON s.id = b.shift_id
    """
    # shift_by_user: {global_user_id: {si_min, so_min, grace_late_in, grace_early_out}}
    shift_by_user: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(shift_sql, {'uids': global_user_ids,
                                'from_ad': from_ad, 'to_ad': to_ad})
        for r in cur.fetchall():
            gid = r['global_user_id']
            st  = r['start_time']
            et  = r['end_time']
            def _t2m_s(t):
                if t is None:
                    return None
                if hasattr(t, 'hour'):
                    return t.hour * 60 + t.minute
                # timedelta from midnight
                if hasattr(t, 'seconds'):
                    return t.seconds // 60
                return None
            shift_by_user[gid] = {
                'si_min':          _t2m_s(st) or 600,
                'so_min':          _t2m_s(et) or 1020,
                'grace_late_in':   int(r['grace_late_in']   or 0),
                'grace_early_out': int(r['grace_early_out'] or 0),
            }

    # ── 5. Build att_map over all users × all dates ───────────────────────────
    def _t2m(t):
        if t is None:
            return None
        if hasattr(t, 'hour'):
            return t.hour * 60 + t.minute
        return None

    result: dict = {}
    start = date.fromisoformat(from_ad)
    end   = date.fromisoformat(to_ad)

    for gid in global_user_ids:
        result[gid] = {}
        user_punches = punch_map.get(gid, {})
        user_leaves  = leave_map_by_user.get(gid, {})
        shift_info   = shift_by_user.get(gid)
        si_min    = shift_info['si_min']          if shift_info else 600
        so_min    = shift_info['so_min']          if shift_info else 1020
        grace     = shift_info['grace_late_in']   if shift_info else 0

        d = start
        while d <= end:
            ds         = d.isoformat()
            nepal_dow  = d.isoweekday() % 7   # 0=Sun … 6=Sat
            is_weekend = (nepal_dow == 6)

            holiday_entry = holiday_map.get(ds)
            is_holiday    = bool(holiday_entry)

            punch    = user_punches.get(ds)
            first_in = punch['first_in']  if punch else None
            last_out = punch['last_out']  if punch else None

            ci_min   = _t2m(first_in)
            co_min   = _t2m(last_out) if (last_out and last_out != first_in) else None
            work_min = (co_min - ci_min) if (ci_min is not None and co_min is not None and co_min > ci_min) else 0

            if is_weekend:
                status_code  = 'SAT'
                display_code = 'शनि'
            elif is_holiday:
                htype  = (holiday_entry or {}).get('holiday_type', 'public')
                htcode = (holiday_entry or {}).get('type_code', '') or ''
                if htype == 'festival' or htcode == 'FEST':
                    status_code  = 'FH'
                    display_code = 'उत्'
                elif htcode == 'NAT':
                    status_code  = 'NH'
                    display_code = 'रा'
                elif htcode in ('OPT', 'COMP'):
                    status_code  = 'OH'
                    display_code = 'वै'
                else:
                    status_code  = 'PH'
                    display_code = 'सा'
            elif punch:
                status_code  = 'P'
                display_code = '√'
            elif ds in user_leaves:
                lv           = user_leaves[ds]
                status_code  = lv['code']
                display_code = lv['display']
            else:
                status_code  = 'A'
                display_code = 'X'

            ot_min = late_in_min = early_out_min = 0
            if not is_weekend and not is_holiday and punch:
                if ci_min is not None and ci_min > si_min + grace:
                    late_in_min = ci_min - si_min
                if co_min is not None:
                    planned = so_min - si_min
                    if co_min < so_min:
                        early_out_min = so_min - co_min
                    elif work_min > planned:
                        ot_min = work_min - planned

            result[gid][ds] = {
                'status_code':      status_code,
                'display_code':     display_code,
                'first_in':         first_in,
                'last_out':         last_out,
                'work_minutes':     work_min,
                'ot_minutes':       ot_min,
                'late_in_minutes':  late_in_min,
                'early_out_min':    early_out_min,
            }
            d += timedelta(days=1)

    return result
