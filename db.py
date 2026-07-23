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

_PHASE8_SQL = """
-- Phase 8: per-device connection timeout
ALTER TABLE devices ADD COLUMN IF NOT EXISTS connection_timeout INTEGER NOT NULL DEFAULT 10;
"""

_WEB_USERS_SQL = """
-- Phase 9: web_users login table and audit log
CREATE TABLE IF NOT EXISTS web_users (
    id              SERIAL       PRIMARY KEY,
    global_user_id  INTEGER      REFERENCES global_users(id) ON DELETE SET NULL,
    username        VARCHAR(100) NOT NULL,
    password_hash   VARCHAR(200) NOT NULL,
    display_name    VARCHAR(200),
    role            VARCHAR(20)  NOT NULL DEFAULT 'viewer',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    last_login_ip   VARCHAR(45),
    must_change_pwd BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by      INTEGER,
    updated_by      INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_users_username
    ON web_users (LOWER(username));
CREATE INDEX IF NOT EXISTS idx_web_users_global_user
    ON web_users (global_user_id);

CREATE TABLE IF NOT EXISTS web_user_audit_log (
    id           BIGSERIAL    PRIMARY KEY,
    web_user_id  INTEGER      REFERENCES web_users(id) ON DELETE SET NULL,
    username     VARCHAR(100),
    action       VARCHAR(50)  NOT NULL,
    ip_address   VARCHAR(45),
    user_agent   TEXT,
    details      JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_web_audit_user
    ON web_user_audit_log (web_user_id);
CREATE INDEX IF NOT EXISTS idx_web_audit_ts
    ON web_user_audit_log (created_at DESC);
"""


def get_connection():
    conn = psycopg2.connect(**load_db_config())
    # Thread the current web request's IP/user-agent into this connection's
    # session, for the audit_log trigger (fn_audit_log()) to pick up via
    # current_setting('app.request_ip'/'app.user_agent', true). Silently a
    # no-op outside a web request (CLI scripts, the scheduler) — those still
    # get audited, just without IP/UA, same as before this existed.
    try:
        from web.helpers import current_request_ip, current_user_agent
        ip = current_request_ip.get()
        ua = current_user_agent.get()
        if ip or ua:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.request_ip', %s, false), set_config('app.user_agent', %s, false)",
                            (ip or '', ua or ''))
    except Exception:
        pass
    return conn


_PHASE10_SQL = """
-- Phase 10: kaaj (field visit), per-day remarks, manual attendance source

ALTER TABLE attendance_logs ADD COLUMN IF NOT EXISTS source      VARCHAR(20) DEFAULT 'device';
ALTER TABLE attendance_logs ADD COLUMN IF NOT EXISTS manual_note TEXT;

CREATE TABLE IF NOT EXISTS kaaj_records (
    id              SERIAL PRIMARY KEY,
    global_user_id  INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    ad_date         DATE NOT NULL,
    bs_date         VARCHAR(10),
    is_paid         BOOLEAN NOT NULL DEFAULT TRUE,
    reason          TEXT,
    approved_by     VARCHAR(100),
    created_by      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (global_user_id, ad_date)
);
CREATE INDEX IF NOT EXISTS idx_kaaj_user_date ON kaaj_records (global_user_id, ad_date);
CREATE INDEX IF NOT EXISTS idx_kaaj_date      ON kaaj_records (ad_date);

CREATE TABLE IF NOT EXISTS attendance_day_remarks (
    id              SERIAL PRIMARY KEY,
    global_user_id  INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    ad_date         DATE NOT NULL,
    bs_date         VARCHAR(10),
    remark_text     TEXT NOT NULL,
    created_by      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (global_user_id, ad_date)
);
CREATE INDEX IF NOT EXISTS idx_day_remark_user_date ON attendance_day_remarks (global_user_id, ad_date);

-- Seed Kaaj as a leave type if not present
INSERT INTO leave_types (name, code, days_per_year, max_accumulate, carry_forward, is_paid,
                         description, display_code, color_code, sort_order)
VALUES ('Kaaj (Field Duty)', 'KAAJ_PAID',   0, 0, FALSE, TRUE,  'Official field visit – paid',   'का',  '#0369a1', 9)
ON CONFLICT (code) DO NOTHING;

INSERT INTO leave_types (name, code, days_per_year, max_accumulate, carry_forward, is_paid,
                         description, display_code, color_code, sort_order)
VALUES ('Kaaj (Unpaid)',     'KAAJ_UNPAID', 0, 0, FALSE, FALSE, 'Official field visit – unpaid', 'काX', '#64748b', 10)
ON CONFLICT (code) DO NOTHING;
"""


_PHASE11_SQL = """
-- Phase 11: Enhanced global_users fields and company_settings table

-- Add new fields to global_users for HR perspective
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS usertype          VARCHAR(20) DEFAULT 'PERMANENT';
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS appointment_date DATE;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS dob               DATE;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS gender            VARCHAR(10);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS profilepic_url    VARCHAR(500);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS documents         JSONB;

-- Create company_settings table
CREATE TABLE IF NOT EXISTS company_settings (
    id              SERIAL PRIMARY KEY,
    company_name    VARCHAR(200) NOT NULL DEFAULT 'Janak Education',
    logo_url        VARCHAR(500),
    address         TEXT,
    phone           VARCHAR(50),
    email           VARCHAR(200),
    website         VARCHAR(200),
    pan_number      VARCHAR(50),
    fiscal_year_bs  VARCHAR(10),
    created_by      INTEGER,
    updated_by      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default company settings only if the table is truly empty.
-- (ON CONFLICT DO NOTHING has no effect here since there's no unique
-- constraint to conflict against — this WHERE NOT EXISTS guard is what
-- actually makes this a one-time seed instead of inserting a fresh
-- duplicate default row on every server restart.)
INSERT INTO company_settings (company_name, address, phone, email, website)
SELECT 'Janak Education', 'Kathmandu, Nepal', '+977-XXXXXXXX', 'info@janakeducation.edu.np', 'https://janakeducation.edu.np'
WHERE NOT EXISTS (SELECT 1 FROM company_settings);
"""


_PHASE12_AUTO_ATTEND_SQL = """
-- Phase 12: auto_attend_rules table

CREATE TABLE IF NOT EXISTS auto_attend_rules (
    id                      SERIAL PRIMARY KEY,
    user_id                 VARCHAR(50)  NOT NULL,
    device_ids              INTEGER[]    NOT NULL DEFAULT '{1,2}',
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,

    -- Check-in window
    checkin_start           TIME NOT NULL DEFAULT '08:57:00',
    checkin_end             TIME NOT NULL DEFAULT '08:59:49',
    -- Mon-Fri under date.weekday()'s Mon=0..Sun=6 (the convention auto_attend.py
    -- actually checks against — {1,2,3,4,5} here would mean Tue-Sat, not Mon-Fri).
    checkin_days            INTEGER[] NOT NULL DEFAULT '{0,1,2,3,4}',

    -- Check-out window
    checkout_start          TIME NOT NULL DEFAULT '17:19:00',
    checkout_end            TIME NOT NULL DEFAULT '17:27:00',
    checkout_days           INTEGER[] NOT NULL DEFAULT '{0,1,2,3,4}',

    -- Source tag for attendance_logs
    source_tag              VARCHAR(20) NOT NULL DEFAULT 'auto_attend',

    -- Timezone
    timezone                VARCHAR(50) NOT NULL DEFAULT 'Asia/Kathmandu',

    -- Schedule (when the service fires the job)
    schedule_hour           SMALLINT NOT NULL DEFAULT 8,
    schedule_minute         SMALLINT NOT NULL DEFAULT 56,
    checkout_schedule_hour  SMALLINT NOT NULL DEFAULT 17,
    checkout_schedule_minute SMALLINT NOT NULL DEFAULT 18,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_attend_user ON auto_attend_rules (user_id, is_active);

-- Seed default rule for user 258 on devices 1,2 if no rules exist
INSERT INTO auto_attend_rules (user_id, device_ids)
SELECT '258', '{1,2}'
WHERE NOT EXISTS (SELECT 1 FROM auto_attend_rules LIMIT 1);

-- Fix the column default on tables created before the Mon-Fri day-of-week
-- bug was found: {1,2,3,4,5} was Tue-Sat under date.weekday()'s Mon=0..Sun=6,
-- not Mon-Fri as intended. This only corrects the DEFAULT for future inserts
-- that don't specify the column — existing rows are a data fix, not schema.
ALTER TABLE auto_attend_rules ALTER COLUMN checkin_days  SET DEFAULT '{0,1,2,3,4}';
ALTER TABLE auto_attend_rules ALTER COLUMN checkout_days SET DEFAULT '{0,1,2,3,4}';
"""


_PHASE13_PAYROLL_SQL = """
-- Phase 13: Payroll, overtime & tax

CREATE TABLE IF NOT EXISTS payroll_salary_structures (
    id             SERIAL PRIMARY KEY,
    global_user_id INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    basic_salary   NUMERIC(12,2) NOT NULL DEFAULT 0,
    allowances     NUMERIC(12,2) NOT NULL DEFAULT 0,
    daily_hours    NUMERIC(4,1)  NOT NULL DEFAULT 8,
    ot_multiplier  NUMERIC(4,2)  NOT NULL DEFAULT 1.5,
    marital        VARCHAR(10)   NOT NULL DEFAULT 'single',
    other_deductions NUMERIC(12,2) NOT NULL DEFAULT 0,
    effective_bs   VARCHAR(10)   DEFAULT '',
    is_active      BOOLEAN       NOT NULL DEFAULT TRUE,
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (global_user_id)
);

CREATE TABLE IF NOT EXISTS payroll_runs (
    id           SERIAL PRIMARY KEY,
    bs_year      INTEGER NOT NULL,
    bs_month     INTEGER NOT NULL,
    period_index INTEGER NOT NULL DEFAULT 1,
    working_days INTEGER NOT NULL DEFAULT 30,
    status       VARCHAR(20) NOT NULL DEFAULT 'draft',
    note         TEXT,
    created_by   VARCHAR(100),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bs_year, bs_month)
);

CREATE TABLE IF NOT EXISTS payroll_items (
    id               SERIAL PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    global_user_id   INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    present_days     NUMERIC(5,1) NOT NULL DEFAULT 0,
    ot_hours         NUMERIC(6,2) NOT NULL DEFAULT 0,
    ot_manual        BOOLEAN      NOT NULL DEFAULT FALSE,
    earned_basic     NUMERIC(12,2) NOT NULL DEFAULT 0,
    earned_allowance NUMERIC(12,2) NOT NULL DEFAULT 0,
    ot_pay           NUMERIC(12,2) NOT NULL DEFAULT 0,
    other_earnings   NUMERIC(12,2) NOT NULL DEFAULT 0,
    gross            NUMERIC(12,2) NOT NULL DEFAULT 0,
    taxable_this     NUMERIC(12,2) NOT NULL DEFAULT 0,
    taxable_ytd      NUMERIC(12,2) NOT NULL DEFAULT 0,
    tax              NUMERIC(12,2) NOT NULL DEFAULT 0,
    other_deductions NUMERIC(12,2) NOT NULL DEFAULT 0,
    net_pay          NUMERIC(12,2) NOT NULL DEFAULT 0,
    detail           JSONB,
    UNIQUE (run_id, global_user_id)
);
CREATE INDEX IF NOT EXISTS idx_payroll_items_run  ON payroll_items (run_id);
CREATE INDEX IF NOT EXISTS idx_payroll_items_user ON payroll_items (global_user_id);
"""


_PHASE14_HOLIDAY_OT_SQL = """
-- Phase 14: holiday-overtime premium rules (scoped by employee / dept / section)
CREATE TABLE IF NOT EXISTS payroll_holiday_ot_rules (
    id             SERIAL PRIMARY KEY,
    global_user_id INTEGER REFERENCES global_users(id) ON DELETE CASCADE,
    department_id  INTEGER REFERENCES departments(id)  ON DELETE CASCADE,
    section_id     INTEGER REFERENCES sections(id)     ON DELETE CASCADE,
    multiplier     NUMERIC(4,2) NOT NULL DEFAULT 1.5,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hot_rules_user ON payroll_holiday_ot_rules (global_user_id);
CREATE INDEX IF NOT EXISTS idx_hot_rules_dept ON payroll_holiday_ot_rules (department_id);
CREATE INDEX IF NOT EXISTS idx_hot_rules_sec  ON payroll_holiday_ot_rules (section_id);
"""


_PHASE15_PULL_SCHEDULE_SQL = """
-- Phase 15: DB-backed pull schedule (replaces editing SCHEDULE_TIMES in config.py)
CREATE TABLE IF NOT EXISTS pull_schedule (
    id         SERIAL PRIMARY KEY,
    hour       INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    minute     INTEGER NOT NULL CHECK (minute BETWEEN 0 AND 59),
    label      VARCHAR(100),
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hour, minute)
);
"""


_PHASE17_AUDIT_LOG_SQL = """
-- Phase 17: generic audit log — one table + one trigger function, attached
-- to every administrative/HR/config table so every insert/update/delete is
-- traceable to a user, with before/after values.
--
-- Deliberately NOT attached to attendance_logs, attendance_daily, employees,
-- or pull_sessions: these are bulk-written by every device pull (thousands
-- of rows, several times a day) and already have their own tracking
-- (pull_sessions IS the audit trail for pulls; attendance rows carry their
-- own created_at/source). Row-level auditing there would balloon storage
-- and slow down the pull hot path for little practical benefit.
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    table_name  VARCHAR(100) NOT NULL,
    record_id   INTEGER,
    action      VARCHAR(10)  NOT NULL,
    changed_by  INTEGER,
    old_data    JSONB,
    new_data    JSONB,
    changed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_agent TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_log_table_record ON audit_log (table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at    ON audit_log (changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_changed_by    ON audit_log (changed_by);

CREATE OR REPLACE FUNCTION fn_audit_log() RETURNS TRIGGER AS $body$
DECLARE
    v_old JSONB;
    v_new JSONB;
    v_record_id INTEGER;
    v_changed_by INTEGER;
    v_ip VARCHAR(45);
    v_ua TEXT;
BEGIN
    BEGIN
        -- Set by the app via SET SESSION app.request_ip / app.user_agent right after each
        -- request-scoped connection is opened (web/app.py middleware -> db.get_connection()).
        -- missing_ok=true so writes from outside the web app (scripts, migrations) are still
        -- audited, just without IP/UA.
        v_ip := NULLIF(current_setting('app.request_ip', true), '');
        v_ua := NULLIF(current_setting('app.user_agent', true), '');

        IF TG_OP = 'DELETE' THEN
            v_old := to_jsonb(OLD);
            v_record_id  := NULLIF(v_old->>'id', '')::INTEGER;
            v_changed_by := COALESCE(NULLIF(v_old->>'deleted_by', '')::INTEGER,
                                      NULLIF(v_old->>'updated_by', '')::INTEGER,
                                      NULLIF(v_old->>'created_by', '')::INTEGER);
            INSERT INTO audit_log (table_name, record_id, action, changed_by, old_data, new_data, ip_address, user_agent)
            VALUES (TG_TABLE_NAME, v_record_id, TG_OP, v_changed_by, v_old, NULL, v_ip, v_ua);
        ELSIF TG_OP = 'UPDATE' THEN
            v_old := to_jsonb(OLD);
            v_new := to_jsonb(NEW);
            v_record_id  := NULLIF(v_new->>'id', '')::INTEGER;
            v_changed_by := COALESCE(NULLIF(v_new->>'updated_by', '')::INTEGER,
                                      NULLIF(v_new->>'created_by', '')::INTEGER);
            INSERT INTO audit_log (table_name, record_id, action, changed_by, old_data, new_data, ip_address, user_agent)
            VALUES (TG_TABLE_NAME, v_record_id, TG_OP, v_changed_by, v_old, v_new, v_ip, v_ua);
        ELSE
            v_new := to_jsonb(NEW);
            v_record_id  := NULLIF(v_new->>'id', '')::INTEGER;
            v_changed_by := COALESCE(NULLIF(v_new->>'created_by', '')::INTEGER,
                                      NULLIF(v_new->>'updated_by', '')::INTEGER);
            INSERT INTO audit_log (table_name, record_id, action, changed_by, old_data, new_data, ip_address, user_agent)
            VALUES (TG_TABLE_NAME, v_record_id, TG_OP, v_changed_by, NULL, v_new, v_ip, v_ua);
        END IF;
    EXCEPTION WHEN OTHERS THEN
        -- Audit logging must never break the actual write.
        NULL;
    END;
    RETURN COALESCE(NEW, OLD);
END;
$body$ LANGUAGE plpgsql;
"""

_AUDITED_TABLES = [
    'devices', 'global_users', 'employees', 'departments', 'sections', 'units', 'directorates',
    'shifts', 'shift_rules', 'leave_types', 'leave_balances', 'leave_applications',
    'holidays', 'holiday_types', 'kaaj_records', 'attendance_day_remarks',
    'company_settings', 'web_users', 'pull_schedule', 'auto_attend_rules',
    'payroll_salary_structures', 'payroll_runs', 'payroll_items', 'payroll_holiday_ot_rules',
    'fiscal_years', 'payroll_tax_slab_sets', 'payroll_tax_slab_bands',
    'payroll_heads', 'payroll_employee_heads',
    'payroll_deduction_types', 'payroll_employee_deductions',
    'payroll_attendance_snapshot',
    'payroll_item_heads', 'payroll_item_deductions',
]


_PHASE16_EMPLOYEE_PROFILE_SQL = """
-- Phase 16: extended identity / profile fields on global_users, matched
-- against the external HR "employee" table schema (adds the fields that
-- table has and global_users didn't).
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS name_nep VARCHAR(200);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS citizenship_no VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS national_id_card_no VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS full_address TEXT;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS local_body VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS state VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS ward_no VARCHAR(20);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS pan_no VARCHAR(50);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS bank_name VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS bank_branch VARCHAR(100);
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS initial_appointment_date DATE;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS retirement_date DATE;
ALTER TABLE global_users ADD COLUMN IF NOT EXISTS is_technical BOOLEAN NOT NULL DEFAULT FALSE;
"""


_PHASE18_FISCAL_YEARS_SQL = """
-- Phase 18: fiscal years as a real, status-tracked table (not a hardcoded
-- BS-month-4 constant), and tax slabs as fiscal-year-scoped data instead of
-- a Python dict. See payroll_plan.md Section 5 & 6.1.

CREATE TABLE IF NOT EXISTS fiscal_years (
    id             SERIAL PRIMARY KEY,
    fiscal_year_bs VARCHAR(10)  NOT NULL UNIQUE,   -- e.g. '2082/83'
    start_bs       VARCHAR(10)  NOT NULL,
    end_bs         VARCHAR(10)  NOT NULL,
    start_ad       DATE         NOT NULL,
    end_ad         DATE         NOT NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'upcoming',  -- upcoming|active|closed|locked
    created_by     INTEGER,
    updated_by     INTEGER,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fiscal_years_status ON fiscal_years (status);
CREATE INDEX IF NOT EXISTS idx_fiscal_years_range   ON fiscal_years (start_ad, end_ad);

CREATE TABLE IF NOT EXISTS payroll_tax_slab_sets (
    id              SERIAL PRIMARY KEY,
    fiscal_year_id  INTEGER NOT NULL REFERENCES fiscal_years(id) ON DELETE CASCADE,
    marital_status  VARCHAR(10) NOT NULL DEFAULT 'single',  -- single|married|ALL
    is_ssf_adjusted BOOLEAN NOT NULL DEFAULT FALSE,
    source_note     TEXT,
    is_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
    created_by      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fiscal_year_id, marital_status)
);
CREATE INDEX IF NOT EXISTS idx_tax_slab_sets_fy ON payroll_tax_slab_sets (fiscal_year_id);

CREATE TABLE IF NOT EXISTS payroll_tax_slab_bands (
    id           SERIAL PRIMARY KEY,
    slab_set_id  INTEGER NOT NULL REFERENCES payroll_tax_slab_sets(id) ON DELETE CASCADE,
    band_order   INTEGER NOT NULL,
    band_width   NUMERIC(14,2),   -- NULL = remainder / open-ended top band
    rate_percent NUMERIC(5,2) NOT NULL,
    UNIQUE (slab_set_id, band_order)
);

-- payroll_runs: link each run to the fiscal year it belongs to, so lock
-- state (fiscal_years.status) governs whether a run may still be edited.
ALTER TABLE payroll_runs ADD COLUMN IF NOT EXISTS fiscal_year_id INTEGER REFERENCES fiscal_years(id);
CREATE INDEX IF NOT EXISTS idx_payroll_runs_fy ON payroll_runs (fiscal_year_id);

-- company_settings: default shift window, replacing the SI_MIN/SO_MIN
-- literals hardcoded three times in web/app.py.
ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS default_shift_start_min INTEGER NOT NULL DEFAULT 600;
ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS default_shift_end_min   INTEGER NOT NULL DEFAULT 1020;
"""


_PHASE19_IDENTITY_SNAPSHOT_SQL = """
-- Phase 19: identity & audit unification (payroll_plan.md Section 3).
--
-- payroll_items is a point-in-time record (a generated payslip) — per
-- Section 3.2 it must carry a denormalized snapshot of the employee's
-- master identity (global_users.employee_id + name) AS OF GENERATION TIME,
-- separate from the live global_user_id FK, so a later correction to an
-- employee's master ID or a name change never silently rewrites what a
-- historical payslip displays.
--
-- NOTE: global_users.employee_id is populated for only ~2 of 495 employees
-- today (confirmed live), so this snapshot will be NULL for most rows until
-- that data is filled in — that's expected and honest, not a bug. The
-- report/payslip *display* switch to employee_id is deliberately deferred
-- (see Section 2 Q10) until the master ID data is actually populated.
ALTER TABLE payroll_items ADD COLUMN IF NOT EXISTS employee_id_snapshot VARCHAR(50);
ALTER TABLE payroll_items ADD COLUMN IF NOT EXISTS employee_name_snapshot VARCHAR(200);
"""


_PHASE20_SALARY_HEADS_SQL = """
-- Phase 20: salary head catalog (payroll_plan.md Section 2.1/2.2), replacing
-- the flat payroll_salary_structures.basic_salary/allowances pair with a
-- proper per-employee, per-head breakdown. Schema only in this phase — wiring
-- into payroll run generation happens in Phase 8.

CREATE TABLE IF NOT EXISTS payroll_heads (
    id               SERIAL PRIMARY KEY,
    code             VARCHAR(30)  NOT NULL UNIQUE,
    name             VARCHAR(100) NOT NULL,
    category         VARCHAR(20)  NOT NULL DEFAULT 'earning',
    calc_type        VARCHAR(20)  NOT NULL DEFAULT 'fixed',    -- fixed | percent_of_basic
    percent_of_basic NUMERIC(5,2),                             -- only for calc_type='percent_of_basic'
    frequency        VARCHAR(20)  NOT NULL DEFAULT 'monthly',  -- monthly | annual | festival | onetime
    is_taxable       BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order       INTEGER      NOT NULL DEFAULT 0,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by       INTEGER,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payroll_employee_heads (
    id                 SERIAL PRIMARY KEY,
    global_user_id     INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    head_id            INTEGER NOT NULL REFERENCES payroll_heads(id) ON DELETE CASCADE,
    amount             NUMERIC(12,2),   -- NULL for percent_of_basic heads (computed live from this
                                          -- employee's BASIC head amount at generation time)
    frequency_override VARCHAR(20),     -- NULL = use payroll_heads.frequency for this employee;
                                          -- set only when a head's timing genuinely varies per
                                          -- employee (e.g. Rahat noted as "monthly or yearly" by
                                          -- company policy in the source reference sheet)
    pay_bs_month       INTEGER CHECK (pay_bs_month BETWEEN 1 AND 12),  -- for annual/festival/onetime
                                          -- heads: which BS month this is paid in. NULL for monthly.
    effective_bs       VARCHAR(10) NOT NULL DEFAULT '',
    is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (global_user_id, head_id)
);
CREATE INDEX IF NOT EXISTS idx_emp_heads_gu   ON payroll_employee_heads (global_user_id);
CREATE INDEX IF NOT EXISTS idx_emp_heads_head ON payroll_employee_heads (head_id);
"""


_PHASE21_DEDUCTIONS_SQL = """
-- Phase 21: statutory / pre-tax deductions (payroll_plan.md Section 6.2),
-- generalized the same way as earning heads so PF/CIT/Insurance aren't three
-- hardcoded columns forever — a 4th deduction type later is a catalog row,
-- not a migration. Schema only in this phase — wiring into tax computation
-- happens in Phase 6.

CREATE TABLE IF NOT EXISTS payroll_deduction_types (
    id                   SERIAL PRIMARY KEY,
    code                 VARCHAR(30)  NOT NULL UNIQUE,
    name                 VARCHAR(100) NOT NULL,
    calc_type            VARCHAR(20)  NOT NULL DEFAULT 'fixed',    -- fixed | percent_of_basic
    percent_of_basic     NUMERIC(5,2),                             -- only for calc_type='percent_of_basic'
    default_amount       NUMERIC(12,2),                            -- catalog default; per-employee override
                                                                     -- lives on payroll_employee_deductions
    is_pretax            BOOLEAN      NOT NULL DEFAULT TRUE,       -- reduces taxable income before the
                                                                     -- slab calculation (vs. a post-tax
                                                                     -- deduction from net pay)
    frequency            VARCHAR(20)  NOT NULL DEFAULT 'monthly',  -- monthly | annual
    cap_amount           NUMERIC(12,2),                            -- statutory cap in Rs, if any
    cap_percent_of_gross NUMERIC(5,2),                             -- statutory cap as % of gross, if any
                                                                     -- (both caps may be set together —
                                                                     -- e.g. "lesser of 1/3 of gross or
                                                                     -- Rs 500,000/yr" — the resolver
                                                                     -- applies whichever is more restrictive)
    sort_order           INTEGER      NOT NULL DEFAULT 0,
    is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by           INTEGER,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payroll_employee_deductions (
    id                 SERIAL PRIMARY KEY,
    global_user_id     INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    deduction_type_id  INTEGER NOT NULL REFERENCES payroll_deduction_types(id) ON DELETE CASCADE,
    is_enrolled        BOOLEAN     NOT NULL DEFAULT FALSE,
    amount             NUMERIC(12,2),   -- per-employee override; NULL = use catalog default_amount
                                          -- (ignored for percent_of_basic types, which always compute live)
    percent_override   NUMERIC(5,2),    -- per-employee override of percent_of_basic; NULL = use catalog %
    effective_bs       VARCHAR(10) NOT NULL DEFAULT '',
    is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (global_user_id, deduction_type_id)
);
CREATE INDEX IF NOT EXISTS idx_emp_deductions_gu ON payroll_employee_deductions (global_user_id);
"""


_PHASE22_ATTENDANCE_SNAPSHOT_SQL = """
-- Phase 22: persisted per-run attendance snapshot (payroll_plan.md Section
-- 8.1) — "per month days working and all, so we can find change log in
-- future." Written once per employee per payroll run at generation time,
-- not recomputed live and discarded. Since this table is in _AUDITED_TABLES,
-- if an admin later corrects a punch and regenerates the run, the old
-- snapshot row's UPDATE is captured with old/new values in audit_log — a
-- genuine change log.
CREATE TABLE IF NOT EXISTS payroll_attendance_snapshot (
    id                     SERIAL PRIMARY KEY,
    run_id                 INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    global_user_id         INTEGER NOT NULL REFERENCES global_users(id) ON DELETE CASCADE,
    employee_id_snapshot   VARCHAR(50),    -- master employee ID, denormalized (Section 3.2)
    working_days           INTEGER NOT NULL DEFAULT 0,
    present_days           INTEGER NOT NULL DEFAULT 0,
    paid_leave_days        INTEGER NOT NULL DEFAULT 0,
    unpaid_leave_days      INTEGER NOT NULL DEFAULT 0,
    absent_days            INTEGER NOT NULL DEFAULT 0,
    weekend_days           INTEGER NOT NULL DEFAULT 0,
    holiday_days           INTEGER NOT NULL DEFAULT 0,
    festival_days          INTEGER NOT NULL DEFAULT 0,
    total_days             INTEGER NOT NULL DEFAULT 0,
    paid_days              INTEGER NOT NULL DEFAULT 0,   -- present + paid_leave, capped at working_days
    total_work_minutes     INTEGER NOT NULL DEFAULT 0,
    regular_ot_minutes     INTEGER NOT NULL DEFAULT 0,
    holiday_ot_minutes     INTEGER NOT NULL DEFAULT 0,
    late_in_minutes        INTEGER NOT NULL DEFAULT 0,
    early_out_minutes      INTEGER NOT NULL DEFAULT 0,
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, global_user_id)
);
CREATE INDEX IF NOT EXISTS idx_att_snapshot_run  ON payroll_attendance_snapshot (run_id);
CREATE INDEX IF NOT EXISTS idx_att_snapshot_user ON payroll_attendance_snapshot (global_user_id);
"""


_PHASE23_ITEM_BREAKDOWN_SQL = """
-- Phase 23: immutable per-run head/deduction breakdown (payroll_plan.md
-- Section 2.5/Phase 8) — a payslip's line items are stamped at generation
-- time from the catalog (code/name/category/amount), NOT a live FK to
-- payroll_heads/payroll_deduction_types, so a later catalog edit (rename,
-- rate change) never silently rewrites what a historical payslip showed.

CREATE TABLE IF NOT EXISTS payroll_item_heads (
    id           SERIAL PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES payroll_items(id) ON DELETE CASCADE,
    head_code    VARCHAR(30)  NOT NULL,
    head_name    VARCHAR(100) NOT NULL,
    category     VARCHAR(20)  NOT NULL DEFAULT 'earning',
    frequency    VARCHAR(20)  NOT NULL DEFAULT 'monthly',
    amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_taxable   BOOLEAN      NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_item_heads_item ON payroll_item_heads (item_id);

CREATE TABLE IF NOT EXISTS payroll_item_deductions (
    id                SERIAL PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES payroll_items(id) ON DELETE CASCADE,
    deduction_code    VARCHAR(30)  NOT NULL,
    deduction_name    VARCHAR(100) NOT NULL,
    amount            NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_pretax         BOOLEAN      NOT NULL DEFAULT TRUE,
    capped            BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_item_deductions_item ON payroll_item_deductions (item_id);
"""


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
    # Phase 8: per-device connection timeout
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE8_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 8 migration skipped: %s", e)
    # Phase 9: web_users login table and audit log
    try:
        with conn.cursor() as cur:
            cur.execute(_WEB_USERS_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 9 (web_users) migration skipped: %s", e)
    # Phase 10: kaaj records, day remarks, manual attendance source column
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE10_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 10 migration skipped: %s", e)
    # Phase 11: enhanced global_users fields and company_settings
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE11_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 11 migration skipped: %s", e)
    # Phase 12: auto_attend_rules table
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE12_AUTO_ATTEND_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 12 (auto_attend_rules) migration skipped: %s", e)
    # Phase 13: payroll, overtime & tax
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE13_PAYROLL_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 13 (payroll) migration skipped: %s", e)
    # Phase 14: holiday-overtime premium rules
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE14_HOLIDAY_OT_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 14 (holiday OT) migration skipped: %s", e)
    # Phase 15: DB-backed pull schedule
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE15_PULL_SCHEDULE_SQL)
        conn.commit()
        _seed_pull_schedule_from_config(conn)
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 15 (pull schedule) migration skipped: %s", e)
    # Phase 16: extended employee profile fields (name_nep, citizenship, bank, etc.)
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE16_EMPLOYEE_PROFILE_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 16 (employee profile fields) migration skipped: %s", e)
    # Phase 17: generic audit log — table + trigger function + attach to
    # every administrative/HR/config table (see _AUDITED_TABLES).
    #
    # NOTE: _AUDITED_TABLES may include tables created by a *later* phase
    # (e.g. Phase 18's fiscal_years) — on a fresh install those tables don't
    # exist yet when this phase runs. Skip (don't fail) missing tables here;
    # the later phase that creates them is responsible for attaching its own
    # trigger to itself once its CREATE TABLE has run (see Phase 18 below).
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE17_AUDIT_LOG_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 17 (audit log table/function) migration skipped: %s", e)
    for tbl in _AUDITED_TABLES:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (f"public.{tbl}",))
                if cur.fetchone()[0] is None:
                    continue  # table doesn't exist yet — a later phase will attach its own trigger
                cur.execute(f"DROP TRIGGER IF EXISTS trg_audit_log ON {tbl}")
                cur.execute(f"""
                    CREATE TRIGGER trg_audit_log
                    AFTER INSERT OR UPDATE OR DELETE ON {tbl}
                    FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
                """)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.warning("Audit trigger attach skipped for %s: %s", tbl, e)
    # Phase 18: fiscal years table + fiscal-year-scoped tax slabs (replaces
    # the hardcoded _SLABS dict and the hardcoded Shrawan-start assumption).
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE18_FISCAL_YEARS_SQL)
            cur.execute("DROP TRIGGER IF EXISTS trg_audit_log ON fiscal_years")
            cur.execute("""
                CREATE TRIGGER trg_audit_log
                AFTER INSERT OR UPDATE OR DELETE ON fiscal_years
                FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
            """)
            cur.execute("DROP TRIGGER IF EXISTS trg_audit_log ON payroll_tax_slab_sets")
            cur.execute("""
                CREATE TRIGGER trg_audit_log
                AFTER INSERT OR UPDATE OR DELETE ON payroll_tax_slab_sets
                FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
            """)
            cur.execute("DROP TRIGGER IF EXISTS trg_audit_log ON payroll_tax_slab_bands")
            cur.execute("""
                CREATE TRIGGER trg_audit_log
                AFTER INSERT OR UPDATE OR DELETE ON payroll_tax_slab_bands
                FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
            """)
        conn.commit()
        _seed_current_fiscal_year_and_tax_slabs(conn)
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 18 (fiscal years / tax slabs) migration skipped: %s", e)
    # Phase 19: identity & audit unification — payroll_items snapshot columns.
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE19_IDENTITY_SNAPSHOT_SQL)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 19 (identity snapshot) migration skipped: %s", e)
    # Phase 20: salary head catalog (payroll_heads / payroll_employee_heads).
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE20_SALARY_HEADS_SQL)
            for tbl in ("payroll_heads", "payroll_employee_heads"):
                cur.execute(f"DROP TRIGGER IF EXISTS trg_audit_log ON {tbl}")
                cur.execute(f"""
                    CREATE TRIGGER trg_audit_log
                    AFTER INSERT OR UPDATE OR DELETE ON {tbl}
                    FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
                """)
        conn.commit()
        _seed_payroll_heads_and_migrate_structures(conn)
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 20 (salary heads) migration skipped: %s", e)
    # Phase 21: statutory deduction types (PF / CIT / Insurance).
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE21_DEDUCTIONS_SQL)
            for tbl in ("payroll_deduction_types", "payroll_employee_deductions"):
                cur.execute(f"DROP TRIGGER IF EXISTS trg_audit_log ON {tbl}")
                cur.execute(f"""
                    CREATE TRIGGER trg_audit_log
                    AFTER INSERT OR UPDATE OR DELETE ON {tbl}
                    FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
                """)
        conn.commit()
        _seed_deduction_types(conn)
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 21 (deduction types) migration skipped: %s", e)
    # Phase 22: persisted per-run attendance snapshot.
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE22_ATTENDANCE_SNAPSHOT_SQL)
            cur.execute("DROP TRIGGER IF EXISTS trg_audit_log ON payroll_attendance_snapshot")
            cur.execute("""
                CREATE TRIGGER trg_audit_log
                AFTER INSERT OR UPDATE OR DELETE ON payroll_attendance_snapshot
                FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
            """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 22 (attendance snapshot) migration skipped: %s", e)
    # Phase 23: immutable per-run head/deduction breakdown.
    try:
        with conn.cursor() as cur:
            cur.execute(_PHASE23_ITEM_BREAKDOWN_SQL)
            for tbl in ("payroll_item_heads", "payroll_item_deductions"):
                cur.execute(f"DROP TRIGGER IF EXISTS trg_audit_log ON {tbl}")
                cur.execute(f"""
                    CREATE TRIGGER trg_audit_log
                    AFTER INSERT OR UPDATE OR DELETE ON {tbl}
                    FOR EACH ROW EXECUTE FUNCTION fn_audit_log()
                """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Phase 23 (item breakdown) migration skipped: %s", e)
    logger.info("Database schema initialized.")


def _seed_pull_schedule_from_config(conn) -> None:
    """One-time seed: if pull_schedule is empty, copy the existing
    SCHEDULE_TIMES from config.py so upgrading servers keep their current
    pull times instead of losing them when the DB table is first created."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pull_schedule")
        if cur.fetchone()[0] > 0:
            return
    try:
        from config import SCHEDULE_TIMES as _existing_times
    except Exception:
        _existing_times = []
    if not _existing_times:
        return
    with conn.cursor() as cur:
        for hour, minute in _existing_times:
            cur.execute(
                "INSERT INTO pull_schedule (hour, minute) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (hour, minute),
            )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
#  Fiscal years & dynamic tax slabs  (Phase 18)
# ═══════════════════════════════════════════════════════════════════════════

# Seed-only reference rates: what payroll.py's old hardcoded _SLABS dict
# actually implemented, corrected to include the top band it was missing
# (see payroll_plan.md Section 0, gap #3). Used ONLY to seed the very first
# fiscal_years row on a fresh install so existing behavior below the top
# band is unchanged; every fiscal year after that is admin-entered data via
# create_tax_slab_set(), never another hardcoded table like this one.
_SEED_TAX_BANDS = {
    "single": [
        (500000, 1), (200000, 10), (300000, 20),
        (1000000, 30), (3000000, 36), (None, 39),
    ],
    "married": [
        (600000, 1), (200000, 10), (300000, 20),
        (900000, 30), (3000000, 36), (None, 39),
    ],
}


def _fiscal_year_bs_for(bs_year: int, bs_month: int) -> int:
    """The BS year a fiscal year *starts* in, given any BS year/month inside it.

    Nepal's fiscal year runs Shrawan 1 (BS month 4) through the following
    year's Ashadh-end (BS month 3). A date in BS months 1-3 belongs to the
    fiscal year that started the *previous* BS year.
    """
    return bs_year if bs_month >= 4 else bs_year - 1


def _seed_current_fiscal_year_and_tax_slabs(conn) -> None:
    """One-time seed: create the currently-running fiscal year (computed from
    today's BS date via nepali_utils, not hardcoded) plus its single/married
    tax slab sets, if none exist yet. Safe to re-run — no-ops once seeded."""
    import nepali_utils

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fiscal_years")
        if cur.fetchone()[0] > 0:
            return

    today_bs = _today_bs()
    if not today_bs:
        return
    bs_year, bs_month = int(today_bs[:4]), int(today_bs[5:7])
    fy_start_year = _fiscal_year_bs_for(bs_year, bs_month)
    fy_end_year = fy_start_year + 1
    fiscal_year_bs = f"{fy_start_year}/{str(fy_end_year)[-2:]}"

    end_month_info = nepali_utils.bs_month_info(fy_end_year, 3)
    if end_month_info is None:
        logger.warning("Could not resolve fiscal year %s (nepali_datetime unavailable?)",
                        fiscal_year_bs)
        return
    start_bs = f"{fy_start_year}-04-01"
    end_bs = f"{fy_end_year}-03-{end_month_info['days']:02d}"
    start_ad = nepali_utils.bs_to_ad(start_bs)
    end_ad = end_month_info["last_ad"]
    if not start_ad or not end_ad:
        logger.warning("Could not convert fiscal year %s BS dates to AD", fiscal_year_bs)
        return

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO fiscal_years (fiscal_year_bs, start_bs, end_bs, start_ad, end_ad, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            RETURNING id
        """, (fiscal_year_bs, start_bs, end_bs, start_ad, end_ad))
        fy_id = cur.fetchone()[0]

        for marital, bands in _SEED_TAX_BANDS.items():
            cur.execute("""
                INSERT INTO payroll_tax_slab_sets
                    (fiscal_year_id, marital_status, is_confirmed, source_note)
                VALUES (%s, %s, TRUE, %s)
                RETURNING id
            """, (fy_id, marital,
                  "Seeded from the previously-hardcoded payroll.py rates on first install, "
                  "corrected to include the top band the old code was missing. "
                  "See payroll_plan.md Section 0."))
            slab_set_id = cur.fetchone()[0]
            for order, (width, rate) in enumerate(bands, start=1):
                cur.execute("""
                    INSERT INTO payroll_tax_slab_bands (slab_set_id, band_order, band_width, rate_percent)
                    VALUES (%s, %s, %s, %s)
                """, (slab_set_id, order, width, rate))
    conn.commit()
    logger.info("Seeded fiscal year %s (id=%s) with default tax slabs.", fiscal_year_bs, fy_id)


def list_fiscal_years(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM fiscal_years ORDER BY start_ad DESC")
        return [dict(r) for r in cur.fetchall()]


def get_fiscal_year(conn, fiscal_year_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM fiscal_years WHERE id=%s", (fiscal_year_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_active_fiscal_year(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM fiscal_years WHERE status='active' ORDER BY start_ad DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None


def get_fiscal_year_for_ad(conn, ad_date: str):
    """Resolve which fiscal year a given AD date ('YYYY-MM-DD') falls inside."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT * FROM fiscal_years
            WHERE start_ad <= %s AND end_ad >= %s
            ORDER BY start_ad DESC LIMIT 1
        """, (ad_date, ad_date))
        row = cur.fetchone()
        return dict(row) if row else None


def create_fiscal_year(conn, fiscal_year_bs: str, created_by: int | None = None) -> int:
    """Create a new fiscal year row (status='upcoming') from its BS label
    'YYYY/YY' (e.g. '2083/84'). Start/end AD+BS dates are computed via
    nepali_utils, never hardcoded, since BS month lengths vary year to year."""
    import nepali_utils

    fy_start_year = int(str(fiscal_year_bs).split('/')[0])
    fy_end_year = fy_start_year + 1
    end_month_info = nepali_utils.bs_month_info(fy_end_year, 3)
    if end_month_info is None:
        raise ValueError(f"Could not resolve BS calendar for fiscal year {fiscal_year_bs}")
    start_bs = f"{fy_start_year}-04-01"
    end_bs = f"{fy_end_year}-03-{end_month_info['days']:02d}"
    start_ad = nepali_utils.bs_to_ad(start_bs)
    end_ad = end_month_info["last_ad"]
    if not start_ad or not end_ad:
        raise ValueError(f"Could not convert fiscal year {fiscal_year_bs} BS dates to AD")

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO fiscal_years
                (fiscal_year_bs, start_bs, end_bs, start_ad, end_ad, status, created_by)
            VALUES (%s, %s, %s, %s, %s, 'upcoming', %s)
            RETURNING id
        """, (fiscal_year_bs, start_bs, end_bs, start_ad, end_ad, created_by))
        fy_id = cur.fetchone()[0]
    conn.commit()
    return fy_id


def set_fiscal_year_status(conn, fiscal_year_id: int, status: str, updated_by: int | None = None) -> None:
    if status not in ('upcoming', 'active', 'closed', 'locked'):
        raise ValueError(f"Invalid fiscal year status: {status}")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE fiscal_years SET status=%s, updated_by=%s, updated_at=NOW() WHERE id=%s
        """, (status, updated_by, fiscal_year_id))
    conn.commit()


def create_tax_slab_set(conn, fiscal_year_id: int, marital_status: str,
                        bands: list, is_confirmed: bool = False,
                        source_note: str | None = None, created_by: int | None = None) -> int:
    """bands: list of (width, rate_percent) tuples, width=None for the top/remainder band."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_tax_slab_sets
                (fiscal_year_id, marital_status, is_confirmed, source_note, created_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (fiscal_year_id, marital_status) DO UPDATE SET
                is_confirmed = EXCLUDED.is_confirmed,
                source_note  = EXCLUDED.source_note
            RETURNING id
        """, (fiscal_year_id, marital_status, is_confirmed, source_note, created_by))
        slab_set_id = cur.fetchone()[0]
        cur.execute("DELETE FROM payroll_tax_slab_bands WHERE slab_set_id=%s", (slab_set_id,))
        for order, (width, rate) in enumerate(bands, start=1):
            cur.execute("""
                INSERT INTO payroll_tax_slab_bands (slab_set_id, band_order, band_width, rate_percent)
                VALUES (%s, %s, %s, %s)
            """, (slab_set_id, order, width, rate))
    conn.commit()
    return slab_set_id


def get_tax_slab_bands(conn, fiscal_year_id: int, marital_status: str):
    """Resolve the tax bands for a fiscal year + marital status.

    Looks for an exact marital_status match first (single/married), falling
    back to a marital_status='ALL' row if the fiscal year uses a unified
    structure with no single/married split.

    Returns {'slab_set_id', 'is_confirmed', 'bands': [{'width', 'rate'}, ...]}
    or None if no slab set exists for this fiscal year at all.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT * FROM payroll_tax_slab_sets
            WHERE fiscal_year_id=%s AND marital_status=%s
        """, (fiscal_year_id, marital_status))
        slab_set = cur.fetchone()
        if not slab_set:
            cur.execute("""
                SELECT * FROM payroll_tax_slab_sets
                WHERE fiscal_year_id=%s AND marital_status='ALL'
            """, (fiscal_year_id,))
            slab_set = cur.fetchone()
        if not slab_set:
            return None
        cur.execute("""
            SELECT band_width, rate_percent FROM payroll_tax_slab_bands
            WHERE slab_set_id=%s ORDER BY band_order
        """, (slab_set["id"],))
        bands = [{"width": r["band_width"], "rate": r["rate_percent"]} for r in cur.fetchall()]
    return {
        "slab_set_id": slab_set["id"],
        "is_confirmed": slab_set["is_confirmed"],
        "bands": bands,
    }


def get_default_shift_window(conn) -> tuple:
    """Company-wide default shift start/end, in minutes since midnight.
    Replaces the SI_MIN/SO_MIN = 600, 1020 literals hardcoded in web/app.py."""
    with conn.cursor() as cur:
        cur.execute("SELECT default_shift_start_min, default_shift_end_min FROM company_settings LIMIT 1")
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            return (row[0], row[1])
    return (600, 1020)


# ═══════════════════════════════════════════════════════════════════════════
#  Salary head catalog  (Phase 20)
# ═══════════════════════════════════════════════════════════════════════════

# Seed-only catalog: the 11 earning heads from the reference sheet (OT is
# excluded — it's computed dynamically from attendance, not a static head).
# code, name, calc_type, percent_of_basic, frequency, sort_order
_SEED_PAYROLL_HEADS = [
    ("BASIC",    "Basic Salary",     "fixed",            None, "monthly", 10),
    ("DA",       "Ad. 10%",          "percent_of_basic", 10,   "monthly", 20),
    ("UPADAN",   "Upadan",           "percent_of_basic", 6,    "monthly", 30),
    ("ALLOWANCE","Allowance",        "fixed",            None, "monthly", 40),
    ("TIFFIN",   "Tiffin Allowance", "fixed",            None, "monthly", 50),
    ("MEDICAL",  "Medical",          "fixed",            None, "monthly", 60),
    ("DRESS",    "Dress",            "fixed",            None, "annual",  70),
    ("DASHAIN",  "Dashain Expense",  "fixed",            None, "festival",80),
    ("COPY",     "Copy",             "fixed",            None, "annual",  90),
    ("BARSHIK",  "Barshik",          "fixed",            None, "annual",  100),
    ("RAHAT",    "Rahat",            "fixed",            None, "annual",  110),
]


def _seed_payroll_heads_and_migrate_structures(conn) -> None:
    """One-time seed of the head catalog (idempotent, ON CONFLICT DO NOTHING
    so it never clobbers admin edits made after the first run), plus a
    one-off migration: any existing payroll_salary_structures row with a
    basic_salary/allowances value gets matching BASIC/ALLOWANCE rows in
    payroll_employee_heads, so nothing already configured is lost when the
    head-based model takes over (Phase 8)."""
    with conn.cursor() as cur:
        for code, name, calc_type, pct, freq, sort_order in _SEED_PAYROLL_HEADS:
            cur.execute("""
                INSERT INTO payroll_heads (code, name, category, calc_type, percent_of_basic, frequency, sort_order)
                VALUES (%s, %s, 'earning', %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
            """, (code, name, calc_type, pct, freq, sort_order))
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT id, code FROM payroll_heads WHERE code IN ('BASIC','ALLOWANCE')")
        head_ids = {r["code"]: r["id"] for r in cur.fetchall()}
        if not head_ids:
            return
        cur.execute("""
            SELECT global_user_id, basic_salary, allowances, effective_bs
            FROM payroll_salary_structures
            WHERE basic_salary IS NOT NULL
        """)
        structures = cur.fetchall()

    with conn.cursor() as cur:
        for s in structures:
            if "BASIC" in head_ids and s["basic_salary"] is not None:
                cur.execute("""
                    INSERT INTO payroll_employee_heads (global_user_id, head_id, amount, effective_bs)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (global_user_id, head_id) DO NOTHING
                """, (s["global_user_id"], head_ids["BASIC"], s["basic_salary"], s["effective_bs"] or ""))
            if "ALLOWANCE" in head_ids and s["allowances"] is not None:
                cur.execute("""
                    INSERT INTO payroll_employee_heads (global_user_id, head_id, amount, effective_bs)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (global_user_id, head_id) DO NOTHING
                """, (s["global_user_id"], head_ids["ALLOWANCE"], s["allowances"], s["effective_bs"] or ""))
    conn.commit()


def get_all_payroll_heads(conn, active_only: bool = True) -> list:
    where = "WHERE is_active" if active_only else ""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(f"SELECT * FROM payroll_heads {where} ORDER BY sort_order, id")
        return [dict(r) for r in cur.fetchall()]


def get_payroll_head(conn, head_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_heads WHERE id=%s", (head_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_payroll_head(conn, head_id: int | None, code: str, name: str, calc_type: str,
                        percent_of_basic, frequency: str, is_taxable: bool, sort_order: int,
                        created_by: int | None = None) -> int:
    """Create (head_id=None) or update (head_id set) a catalog head. Editing here
    never touches historical payslips — payroll_item_heads is a stamped copy."""
    with conn.cursor() as cur:
        if head_id:
            cur.execute("""
                UPDATE payroll_heads SET code=%s, name=%s, calc_type=%s, percent_of_basic=%s,
                       frequency=%s, is_taxable=%s, sort_order=%s
                WHERE id=%s RETURNING id
            """, (code, name, calc_type, percent_of_basic, frequency, is_taxable, sort_order, head_id))
        else:
            cur.execute("""
                INSERT INTO payroll_heads (code, name, category, calc_type, percent_of_basic,
                                           frequency, is_taxable, sort_order, created_by)
                VALUES (%s, %s, 'earning', %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (code, name, calc_type, percent_of_basic, frequency, is_taxable, sort_order, created_by))
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def toggle_payroll_head(conn, head_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE payroll_heads SET is_active = NOT is_active WHERE id=%s", (head_id,))
    conn.commit()


def get_employee_heads(conn, global_user_id: int) -> list:
    """All active head rows configured for an employee, joined with the catalog."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT eh.*, h.code, h.name, h.category, h.calc_type,
                   h.percent_of_basic, h.frequency AS catalog_frequency,
                   h.is_taxable, h.sort_order
            FROM payroll_employee_heads eh
            JOIN payroll_heads h ON h.id = eh.head_id
            WHERE eh.global_user_id = %s AND eh.is_active AND h.is_active
            ORDER BY h.sort_order, h.id
        """, (global_user_id,))
        return [dict(r) for r in cur.fetchall()]


def upsert_employee_head(conn, global_user_id: int, head_id: int, amount=None,
                         frequency_override: str | None = None, pay_bs_month: int | None = None,
                         effective_bs: str = "") -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_employee_heads
                (global_user_id, head_id, amount, frequency_override, pay_bs_month, effective_bs, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (global_user_id, head_id) DO UPDATE SET
                amount             = EXCLUDED.amount,
                frequency_override = EXCLUDED.frequency_override,
                pay_bs_month       = EXCLUDED.pay_bs_month,
                effective_bs       = EXCLUDED.effective_bs,
                updated_at         = NOW()
            RETURNING id
        """, (global_user_id, head_id, amount, frequency_override, pay_bs_month, effective_bs))
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def resolve_employee_heads_for_month(conn, global_user_id: int, bs_month: int) -> list:
    """Resolve an employee's earning heads that are actually due for a given
    BS month — monthly heads always; annual/festival/onetime heads only when
    their pay_bs_month matches. percent_of_basic heads are computed live from
    the employee's own BASIC amount (never stored, so a Basic-salary raise
    automatically flows into DA/Upadan without a separate edit).

    Returns a list of {code, name, category, frequency, amount, is_taxable}.
    """
    heads = get_employee_heads(conn, global_user_id)
    by_code = {h["code"]: h for h in heads}
    basic_amount = by_code.get("BASIC", {}).get("amount") or 0

    resolved = []
    for h in heads:
        freq = h["frequency_override"] or h["catalog_frequency"]
        if freq != "monthly" and h["pay_bs_month"] != bs_month:
            continue
        if h["calc_type"] == "percent_of_basic":
            pct = h["percent_of_basic"] or 0
            amount = round(float(basic_amount) * float(pct) / 100.0, 2)
        else:
            amount = float(h["amount"] or 0)
        resolved.append({
            "code": h["code"], "name": h["name"], "category": h["category"],
            "frequency": freq, "amount": amount, "is_taxable": h["is_taxable"],
        })
    return resolved


# ═══════════════════════════════════════════════════════════════════════════
#  Statutory / pre-tax deductions  (Phase 21)
# ═══════════════════════════════════════════════════════════════════════════

# Seed values taken directly from the user's reference sheet and the Nepal
# tax-deduction rules they supplied — NOT independently verified against the
# Finance Act/IRD. Mirrors the same "seed from known-good source, flag for
# confirmation" approach used for the FY tax slabs in Phase 1/18.
#
#   PF (Provident Fund): 20% of Basic — matches the reference sheet's
#     113,007.20 = 565,036 x 20%. Capped at the lesser of 1/3 of gross income
#     or Rs 500,000/year, per the general retirement-fund deduction rule
#     supplied — both caps are set; the resolver applies whichever binds.
#   CIT (Citizen Investment Trust): flat Rs 36,000/year, matching the sheet.
#     No separate cap modeled yet — Nepali practice may pool this under the
#     same combined retirement-fund cap as PF; not implemented here pending
#     confirmation (flag this if it matters for your company).
#   INSURANCE: no sensible universal default (premiums vary per employee/
#     policy) — left for per-employee entry via payroll_employee_deductions.
#     Capped at Rs 40,000/year (life insurance), matching both the sheet's
#     number and the statutory cap you supplied. The Rs 20,000/year health-
#     insurance cap is NOT modeled as a separate type yet — add a
#     HEALTH_INSURANCE row via the catalog if/when needed (Phase 10 UI, or
#     directly via create_deduction_type()) — no schema change required.
_SEED_DEDUCTION_TYPES = [
    # code,        name,                        calc_type,          pct,  default_amount, frequency, cap_amount, cap_pct_gross
    ("PF",         "Provident Fund",            "percent_of_basic", 20,   None,           "monthly", 500000,     33.33),
    ("CIT",        "Citizen Investment Trust",  "fixed",            None, 36000,          "annual",  None,       None),
    ("INSURANCE",  "Life Insurance Premium",    "fixed",            None, None,           "annual",  40000,      None),
]


def _seed_deduction_types(conn) -> None:
    """One-time seed (idempotent, ON CONFLICT DO NOTHING — never clobbers
    admin edits made after the first run)."""
    with conn.cursor() as cur:
        for i, (code, name, calc_type, pct, default_amt, freq, cap_amt, cap_pct) in enumerate(_SEED_DEDUCTION_TYPES):
            cur.execute("""
                INSERT INTO payroll_deduction_types
                    (code, name, calc_type, percent_of_basic, default_amount, frequency,
                     cap_amount, cap_percent_of_gross, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
            """, (code, name, calc_type, pct, default_amt, freq, cap_amt, cap_pct, (i + 1) * 10))
    conn.commit()


def get_all_deduction_types(conn, active_only: bool = True) -> list:
    where = "WHERE is_active" if active_only else ""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(f"SELECT * FROM payroll_deduction_types {where} ORDER BY sort_order, id")
        return [dict(r) for r in cur.fetchall()]


def get_deduction_type(conn, deduction_type_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_deduction_types WHERE id=%s", (deduction_type_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_deduction_type(conn, deduction_type_id: int | None, code: str, name: str, calc_type: str,
                          percent_of_basic, default_amount, is_pretax: bool, frequency: str,
                          cap_amount, cap_percent_of_gross, sort_order: int,
                          created_by: int | None = None) -> int:
    """Create or update a catalog deduction type. Editing here never touches
    historical payslips — payroll_item_deductions is a stamped copy."""
    with conn.cursor() as cur:
        if deduction_type_id:
            cur.execute("""
                UPDATE payroll_deduction_types SET code=%s, name=%s, calc_type=%s, percent_of_basic=%s,
                       default_amount=%s, is_pretax=%s, frequency=%s, cap_amount=%s,
                       cap_percent_of_gross=%s, sort_order=%s
                WHERE id=%s RETURNING id
            """, (code, name, calc_type, percent_of_basic, default_amount, is_pretax, frequency,
                  cap_amount, cap_percent_of_gross, sort_order, deduction_type_id))
        else:
            cur.execute("""
                INSERT INTO payroll_deduction_types
                    (code, name, calc_type, percent_of_basic, default_amount, is_pretax,
                     frequency, cap_amount, cap_percent_of_gross, sort_order, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (code, name, calc_type, percent_of_basic, default_amount, is_pretax, frequency,
                  cap_amount, cap_percent_of_gross, sort_order, created_by))
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def toggle_deduction_type(conn, deduction_type_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE payroll_deduction_types SET is_active = NOT is_active WHERE id=%s", (deduction_type_id,))
    conn.commit()


def get_employee_deductions(conn, global_user_id: int) -> list:
    """All active, enrolled deduction rows for an employee, joined with the catalog."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT ed.*, dt.code, dt.name, dt.calc_type, dt.percent_of_basic AS catalog_percent,
                   dt.default_amount, dt.is_pretax, dt.frequency AS catalog_frequency,
                   dt.cap_amount, dt.cap_percent_of_gross, dt.sort_order
            FROM payroll_employee_deductions ed
            JOIN payroll_deduction_types dt ON dt.id = ed.deduction_type_id
            WHERE ed.global_user_id = %s AND ed.is_enrolled AND ed.is_active AND dt.is_active
            ORDER BY dt.sort_order, dt.id
        """, (global_user_id,))
        return [dict(r) for r in cur.fetchall()]


def upsert_employee_deduction(conn, global_user_id: int, deduction_type_id: int,
                              is_enrolled: bool = True, amount=None, percent_override=None,
                              effective_bs: str = "") -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_employee_deductions
                (global_user_id, deduction_type_id, is_enrolled, amount, percent_override, effective_bs, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (global_user_id, deduction_type_id) DO UPDATE SET
                is_enrolled      = EXCLUDED.is_enrolled,
                amount           = EXCLUDED.amount,
                percent_override = EXCLUDED.percent_override,
                effective_bs     = EXCLUDED.effective_bs,
                updated_at       = NOW()
            RETURNING id
        """, (global_user_id, deduction_type_id, is_enrolled, amount, percent_override, effective_bs))
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def resolve_employee_deductions_for_month(conn, global_user_id: int,
                                          basic_amount, gross_amount) -> list:
    """Resolve an employee's enrolled pre-tax/statutory deductions for one
    month, with caps applied.

    basic_amount / gross_amount: this month's resolved Basic and gross pay
    (from resolve_employee_heads_for_month() + OT) — required because
    percent_of_basic deductions and cap_percent_of_gross both depend on them.

    The final resolved amount is always a MONTHLY figure: annual-frequency
    deduction amounts are divided by 12; percent_of_basic amounts are already
    monthly since basic_amount is this month's Basic. Statutory caps
    (cap_amount) are, independently, always expressed as annual Rs figures
    (e.g. "Rs 500,000/year") regardless of how often the deduction itself is
    paid — so cap_amount is always divided by 12 for the monthly comparison,
    NOT conditionally on the deduction's own frequency. (Conflating those two
    was a bug caught before this ever ran against real data.)

    Returns a list of {code, name, amount, is_pretax, capped} — `capped` is
    True if a statutory cap actually reduced the resolved amount, so the
    caller (payslip formula display, Section 9) can show that explicitly.
    """
    basic_amount = float(basic_amount or 0)
    gross_amount = float(gross_amount or 0)
    deductions = get_employee_deductions(conn, global_user_id)

    resolved = []
    for d in deductions:
        freq = d["catalog_frequency"]

        if d["calc_type"] == "percent_of_basic":
            pct = d["percent_override"] if d["percent_override"] is not None else d["catalog_percent"]
            monthly_amount = basic_amount * float(pct or 0) / 100.0
        else:
            amt = d["amount"] if d["amount"] is not None else d["default_amount"]
            raw_amount = float(amt or 0)
            monthly_amount = raw_amount / 12.0 if freq == "annual" else raw_amount

        capped = False
        final_amount = monthly_amount
        if d["cap_amount"] is not None:
            monthly_cap = float(d["cap_amount"]) / 12.0  # cap_amount is always an annual Rs figure
            if final_amount > monthly_cap:
                final_amount = monthly_cap
                capped = True
        if d["cap_percent_of_gross"] is not None:
            pct_cap = gross_amount * float(d["cap_percent_of_gross"]) / 100.0
            if final_amount > pct_cap:
                final_amount = pct_cap
                capped = True

        resolved.append({
            "code": d["code"], "name": d["name"], "amount": round(final_amount, 2),
            "is_pretax": d["is_pretax"], "capped": capped,
        })
    return resolved


def get_pull_schedule(conn, active_only: bool = False) -> list:
    where = "WHERE is_active" if active_only else ""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(f"SELECT * FROM pull_schedule {where} ORDER BY hour, minute")
        return [dict(row) for row in cur.fetchall()]


def add_pull_schedule(conn, hour: int, minute: int, label: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pull_schedule (hour, minute, label) VALUES (%s, %s, %s) RETURNING id",
            (hour, minute, label or None),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def update_pull_schedule(conn, sched_id: int, hour: int, minute: int,
                          label: str | None = None, is_active: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pull_schedule SET hour=%s, minute=%s, label=%s, is_active=%s WHERE id=%s",
            (hour, minute, label or None, is_active, sched_id),
        )
    conn.commit()


def delete_pull_schedule(conn, sched_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pull_schedule WHERE id=%s", (sched_id,))
    conn.commit()


def toggle_pull_schedule(conn, sched_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE pull_schedule SET is_active = NOT is_active WHERE id=%s", (sched_id,))
    conn.commit()


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
    where = ["DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') BETWEEN %s AND %s"]
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
    sql = "SELECT id, name, ip_address, port, password, model, is_active, force_udp, connection_timeout, created_at FROM devices ORDER BY name"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def get_device(conn, device_id: int):
    sql = "SELECT id, name, ip_address, port, password, model, is_active, force_udp, connection_timeout FROM devices WHERE id = %s"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (device_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_device(conn, device: dict, app_user_id: int = 0) -> int:
    sql = """
        INSERT INTO devices (name, ip_address, port, password, model, is_active, force_udp, connection_timeout, created_bs, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            bool(device.get("force_udp", False)),
            int(device.get("connection_timeout", 10)),
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
            connection_timeout = %s,
            updated_by = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            bool(device.get("force_udp", False)),
            int(device.get("connection_timeout", 10)),
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
                      unit_id: int | None = None,
                      shift_id: int | None = None,
                      emp_status: str | None = None,
                      include_deleted: bool = False):
    where = []
    params = []
    if not include_deleted:
        where.append("gu.emp_status IS DISTINCT FROM 'DELETED'")
    if emp_status:
        where.append("gu.emp_status = %s")
        params.append(emp_status)
    if search:
        where.append("""(
            gu.name ILIKE %s OR gu.name_nep ILIKE %s OR gu.global_user_id ILIKE %s OR gu.employee_id ILIKE %s
            OR gu.email ILIKE %s OR gu.phone ILIKE %s OR gu.bank_number ILIKE %s
            OR d.name ILIKE %s OR dr.name ILIKE %s OR s.name ILIKE %s OR u.name ILIKE %s
        )""")
        p = f'%{search}%'
        params += [p, p, p, p, p, p, p, p, p, p, p]
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
    if shift_id:
        where.append("gu.shift_id = %s")
        params.append(shift_id)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT
            gu.id, gu.global_user_id, gu.employee_id, gu.name, gu.name_nep,
            gu.privilege, gu.card, gu.bank_number, gu.email, gu.phone,
            gu.emp_status, gu.emp_type, gu.join_date, gu.designation, gu.level_grade,
            gu.citizenship_no, gu.national_id_card_no, gu.full_address,
            gu.local_body, gu.state, gu.ward_no, gu.pan_no,
            gu.bank_name, gu.bank_branch,
            gu.initial_appointment_date, gu.retirement_date, gu.appointment_date,
            gu.dob, gu.gender, gu.is_technical,
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


def get_global_user_count(conn, include_deleted: bool = False) -> int:
    """Canonical total employee count — same population shown on the
    Global Users page. Used by the dashboard and monthly report so all
    three views agree on one number."""
    where = "" if include_deleted else "WHERE emp_status IS DISTINCT FROM 'DELETED'"
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM global_users {where}")
        return cur.fetchone()[0]


def get_global_user(conn, db_id: int) -> dict | None:
    sql = """
        SELECT
            gu.id, gu.global_user_id, gu.employee_id, gu.name, gu.name_nep,
            gu.privilege, gu.card, gu.bank_number, gu.email, gu.phone,
            gu.emp_type, gu.emp_status, gu.join_date, gu.designation, gu.level_grade, gu.usertype,
            gu.citizenship_no, gu.national_id_card_no, gu.full_address,
            gu.local_body, gu.state, gu.ward_no, gu.pan_no,
            gu.bank_name, gu.bank_branch,
            gu.initial_appointment_date, gu.retirement_date, gu.appointment_date,
            gu.dob, gu.gender, gu.is_technical, gu.profilepic_url,
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
    # Merge partial updates (e.g. soft-delete/restore only pass emp_status)
    # on top of the current row so unspecified fields are preserved.
    current = get_global_user(conn, db_id) or {}
    merged = {**current, **data}
    sql = """
        UPDATE global_users SET
            global_user_id   = %s,
            employee_id      = %s,
            name             = %s,
            name_nep         = %s,
            privilege        = %s,
            card             = %s,
            bank_number      = %s,
            email            = %s,
            phone            = %s,
            department_id    = %s,
            section_id       = %s,
            unit_id          = %s,
            shift_id         = %s,
            emp_type         = %s,
            emp_status       = %s,
            join_date        = %s,
            level_grade      = %s,
            designation      = %s,
            usertype         = %s,
            appointment_date = %s,
            dob              = %s,
            gender           = %s,
            profilepic_url   = %s,
            citizenship_no   = %s,
            national_id_card_no = %s,
            full_address     = %s,
            local_body       = %s,
            state            = %s,
            ward_no          = %s,
            pan_no           = %s,
            bank_name        = %s,
            bank_branch      = %s,
            initial_appointment_date = %s,
            retirement_date  = %s,
            is_technical     = %s,
            updated_at       = NOW(),
            updated_bs       = %s,
            updated_by       = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            merged.get('global_user_id'),
            merged.get('employee_id') or None,
            merged.get('name'),
            merged.get('name_nep') or None,
            int(merged.get('privilege') or 0),
            merged.get('card') or None,
            merged.get('bank_number') or None,
            merged.get('email') or None,
            merged.get('phone') or None,
            merged.get('department_id') or None,
            merged.get('section_id') or None,
            merged.get('unit_id') or None,
            merged.get('shift_id') or None,
            merged.get('emp_type') or 'PERMANENT',
            merged.get('emp_status') or 'ACTIVE',
            merged.get('join_date') or None,
            merged.get('level_grade') or None,
            merged.get('designation') or None,
            merged.get('usertype') or 'PERMANENT',
            merged.get('appointment_date') or None,
            merged.get('dob') or None,
            merged.get('gender') or None,
            merged.get('profilepic_url') or None,
            merged.get('citizenship_no') or None,
            merged.get('national_id_card_no') or None,
            merged.get('full_address') or None,
            merged.get('local_body') or None,
            merged.get('state') or None,
            merged.get('ward_no') or None,
            merged.get('pan_no') or None,
            merged.get('bank_name') or None,
            merged.get('bank_branch') or None,
            merged.get('initial_appointment_date') or None,
            merged.get('retirement_date') or None,
            bool(merged.get('is_technical') or False),
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


def get_employees_for_report(conn, emp_status: str | None = None) -> list:
    """
    Return employees grouped by global identity for the monthly report picker.
    Employees linked to global_users are merged into one entry (all devices combined).
    Unlinked employees are grouped by normalised name.
    Each item: {key, display_name, company_id, global_id, devices:[{device_id,device_name,user_id}]}
    """
    where = []
    params = []
    if emp_status:
        where.append("gu.emp_status = %s")
        params.append(emp_status)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT
            COALESCE(gu.name, e.name, e.user_id) AS display_name,
            gu.id                                  AS global_id,
            gu.global_user_id                      AS company_id,
            gu.emp_status,
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
        {w}
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
                                        from_date: str, to_date: str,
                                        global_id: int | None = None) -> list:
    """
    Multi-device attendance for one logical employee.
    Deduplicates punches within 60 seconds (same person, multiple readers).
    Returns: [{work_date, first_punch, last_punch,
               all_punch_times, all_punches_with_device}]

    device_user_pairs comes from the person's enrolled `employees` rows —
    but a punch can arrive from a device they were never explicitly enrolled
    on (e.g. enrolled on device 11 as user 546, but occasionally punches on
    device 10 which also happens to use raw id 546). When global_id is
    given, also pull in punches anywhere whose raw user_id matches this
    person's global_users.global_user_id (their company id) — unless that
    exact (device_id, user_id) is already claimed by a *different* linked
    employee, which would mean the id is a genuine cross-device collision
    between two different real people rather than the same person.
    """
    if not device_user_pairs and not global_id:
        return []

    clauses = []
    params  = []
    if device_user_pairs:
        clauses.append(" OR ".join(
            "(al.device_id = %s AND al.user_id = %s)" for _ in device_user_pairs
        ))
        params += [x for d, u in device_user_pairs for x in (int(d), str(u))]

    if global_id:
        clauses.append("""
            (al.user_id = (SELECT global_user_id FROM global_users WHERE id = %s)
             AND NOT EXISTS (
                 SELECT 1 FROM employees ee
                 WHERE ee.device_id = al.device_id AND ee.user_id = al.user_id
                   AND ee.global_user_id IS NOT NULL
                   AND ee.global_user_id <> %s
             ))
        """)
        params += [global_id, global_id]

    where_clause = " OR ".join(f"({c})" for c in clauses)

    sql = f"""
        SELECT
            al.timestamp AT TIME ZONE 'Asia/Kathmandu' AS ts_npt,
            DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS work_date,
            d.name AS device_name,
            COALESCE(al.source, 'device') AS source
        FROM attendance_logs al
        JOIN devices d ON al.device_id = d.id
        WHERE ({where_clause})
          AND DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') BETWEEN %s AND %s
        ORDER BY ts_npt
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params + [from_date, to_date])
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

        has_manual = any(p.get('source') == 'manual' for p in deduped)
        result.append({
            'work_date':  work_date,
            'first_punch': deduped[0]['ts_npt'],
            'last_punch':  deduped[-1]['ts_npt'],
            'has_manual':  has_manual,
            'all_punch_times': [p['ts_npt'].time() for p in deduped],
            'all_punches_with_device': [
                {'time': p['ts_npt'].time(), 'device_name': p['device_name'],
                 'source': p.get('source', 'device')}
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

def get_all_global_users_with_dept(conn, include_deleted: bool = False) -> list:
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
        {where}
        ORDER BY LOWER(COALESCE(gu.name, gu.global_user_id, ''))
    """.format(where="" if include_deleted else "WHERE gu.emp_status IS DISTINCT FROM 'DELETED'")
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def get_shift_calendar(conn, global_user_id: int, from_ad: str, to_ad: str) -> dict:
    """
    Returns {date_obj: {name, start_min, end_min}} for the date range.
    Priority: employee (5) > unit (4) > section (3) > department (2) > directorate (1).
    Fallback: employee's default shift from global_users.shift_id.
    """
    if not global_user_id:
        return {}

    sql = """
        WITH emp_org AS (
            SELECT gu.department_id, gu.section_id, gu.unit_id,
                   d.directorate_id, gu.shift_id AS emp_shift_id
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
        ),
        emp_default_shift AS (
            SELECT sh.start_time, sh.end_time, sh.name,
                   %(from_ad)s::date AS from_date, NULL::date AS to_date, 0 AS prio
            FROM emp_org eo
            JOIN shifts sh ON sh.id = eo.emp_shift_id
            WHERE eo.emp_shift_id IS NOT NULL
        )
        SELECT * FROM emp_rules
        UNION ALL SELECT * FROM unit_rules
        UNION ALL SELECT * FROM sec_rules
        UNION ALL SELECT * FROM dept_rules
        UNION ALL SELECT * FROM dir_rules
        UNION ALL SELECT * FROM emp_default_shift
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
               lt.name AS leave_type_name, lt.code,
               lt.color_code, lt.display_code
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
                              app_user_id: int = 0,
                              is_half_day: bool = False,
                              half_day_part: str = '') -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO leave_applications
                (global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad,
                 days, reason, applied_bs, applied_ad, status, created_by, updated_by,
                 is_half_day, half_day_part)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, %s, %s, %s)
            RETURNING id
        """, (global_user_id, leave_type_id, from_bs, to_bs, from_ad, to_ad,
              float(days), reason or '', applied_bs or '', status,
              app_user_id or None, app_user_id or None,
              bool(is_half_day), half_day_part or None))
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


# ─── Daily Present List (used by /reports/daily) ─────────────────────────────

def get_daily_present_list(conn, date_ad: str) -> list:
    """
    Returns one row per unique person who punched on date_ad.

    Grouped by resolved identity — employees.global_user_id when the punch's
    (device_id, user_id) maps to a globally-linked employee, otherwise
    device_id:user_id. Raw device user_id alone is NOT a safe grouping key:
    it is device-local and gets reused for different physical people across
    devices (e.g. two devices both enrolling a "user 546" who are not the
    same person), which previously merged unrelated employees' punches into
    a single row and could show '?' for a resolved name (MIN() over
    COALESCE(...,'?') can pick the '?' placeholder over a real name because
    '?' sorts before uppercase letters in ASCII).

    Dept/section are pulled via a LATERAL subquery that picks the single
    best-matching employee record (preferring a globally-linked one).
    """
    sql = """
        WITH punches AS (
            SELECT
                al.device_id, al.user_id, al.name AS raw_name,
                al.timestamp, al.punch_label,
                COALESCE(emp_data.emp_name, gu_fallback.emp_name)               AS emp_name,
                COALESCE(emp_data.att_id, gu_fallback.att_id)                   AS att_id,
                COALESCE(emp_data.department_name, gu_fallback.department_name) AS department_name,
                COALESCE(emp_data.section_name, gu_fallback.section_name)       AS section_name,
                COALESCE(emp_data.global_user_id, gu_fallback.global_user_id)   AS global_user_id
            FROM attendance_logs al
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(gu.name, e.name)                         AS emp_name,
                    COALESCE(gu.global_user_id, e.user_id, '')        AS att_id,
                    dept.name                                         AS department_name,
                    sect.name                                         AS section_name,
                    e.global_user_id
                FROM   employees   e
                LEFT JOIN global_users gu   ON gu.id   = e.global_user_id
                LEFT JOIN departments  dept ON dept.id  = gu.department_id
                LEFT JOIN sections     sect ON sect.id  = gu.section_id
                WHERE  e.device_id = al.device_id AND e.user_id = al.user_id
                ORDER BY e.global_user_id NULLS LAST
                LIMIT 1
            ) emp_data ON TRUE
            -- Fallback when no `employees` row exists for this exact
            -- (device_id, user_id): the raw device user_id often equals
            -- global_users.global_user_id (the company id) directly, even
            -- when the device-level employee sync record is missing.
            -- NOT falling back to "same user_id on any other device" here —
            -- that would reintroduce the cross-device collision bug.
            LEFT JOIN LATERAL (
                SELECT gu2.name AS emp_name, gu2.global_user_id AS att_id,
                       dept2.name AS department_name, sect2.name AS section_name,
                       gu2.id AS global_user_id
                FROM   global_users gu2
                LEFT JOIN departments dept2 ON dept2.id = gu2.department_id
                LEFT JOIN sections    sect2 ON sect2.id = gu2.section_id
                WHERE  gu2.global_user_id = al.user_id
                LIMIT 1
            ) gu_fallback ON emp_data.emp_name IS NULL
            WHERE DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') = %s
        )
        SELECT
            COALESCE('gu:' || global_user_id::text,
                     'dev:' || device_id::text || ':' || user_id)  AS identity_key,
            COALESCE(MIN(raw_name), MIN(emp_name), '?')            AS emp_name,
            MIN(COALESCE(att_id, user_id, ''))                     AS att_id,
            MIN(department_name)                                   AS department_name,
            MIN(section_name)                                      AS section_name,
            MIN(timestamp AT TIME ZONE 'Asia/Kathmandu')           AS first_punch,
            MAX(timestamp AT TIME ZONE 'Asia/Kathmandu')           AS last_punch,
            COUNT(*)                                                AS punch_count,
            ARRAY_AGG((timestamp AT TIME ZONE 'Asia/Kathmandu')
                      ORDER BY timestamp)                          AS punch_times,
            ARRAY_AGG(punch_label ORDER BY timestamp)              AS punch_labels
        FROM punches
        GROUP BY identity_key
        ORDER BY (CASE WHEN MIN(user_id) ~ E'^\\d+$' THEN MIN(user_id)::bigint END) NULLS LAST,
                 MIN(user_id)
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (date_ad,))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        times  = r.get('punch_times')  or []
        labels = r.get('punch_labels') or []
        r['punches'] = [{'ts': ts, 'label': lbl or '—'}
                        for ts, lbl in zip(times, labels + [None] * len(times))]
    return rows


def get_daily_present_gu_ids(conn, date_ad: str) -> set:
    """
    Returns the set of global_user IDs that have at least one punch on date_ad.
    Used to build the absent list (global_users not in this set).

    Same fallback as get_daily_present_list: if a punch's (device_id, user_id)
    has no matching `employees` row (e.g. the person is enrolled on a
    different device than the one they actually punched from), fall back to
    matching the raw user_id directly against global_users.global_user_id.
    Without this, such people were counted as present in the report's
    present list (via the get_daily_present_list fallback) but ALSO as
    absent here, since their global_user_id never made it into this set.
    """
    sql = """
        SELECT DISTINCT COALESCE(e.global_user_id, gu_fb.id) AS gu_id
        FROM   attendance_logs al
        LEFT JOIN employees e ON e.device_id = al.device_id AND e.user_id = al.user_id
        LEFT JOIN global_users gu_fb
               ON gu_fb.global_user_id = al.user_id AND e.id IS NULL
        WHERE  DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') = %s
          AND  COALESCE(e.global_user_id, gu_fb.id) IS NOT NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql, (date_ad,))
        return {row[0] for row in cur.fetchall()}


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
            COALESCE(gu.id::text, 'nogu_' || e.user_id)
                                  AS global_user_id,
            MIN(COALESCE(gu.name, e.name, '?'))          AS emp_name,
            MIN(COALESCE(gu.global_user_id, e.user_id, '')) AS company_id,
            dept.name             AS department_name,
            sect.name             AS section_name,
            MIN(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS first_punch,
            MAX(al.timestamp AT TIME ZONE 'Asia/Kathmandu') AS last_punch,
            COUNT(*)              AS punch_count,
            ARRAY_AGG((al.timestamp AT TIME ZONE 'Asia/Kathmandu')
                      ORDER BY al.timestamp) AS punch_times,
            ARRAY_AGG(al.punch_label ORDER BY al.timestamp) AS punch_labels
        FROM attendance_logs al
        JOIN employees e   ON e.device_id = al.device_id AND e.user_id = al.user_id
        LEFT JOIN global_users gu ON gu.id = e.global_user_id
        LEFT JOIN departments dept ON dept.id = gu.department_id
        LEFT JOIN sections    sect ON sect.id = gu.section_id
        WHERE DATE(al.timestamp AT TIME ZONE 'Asia/Kathmandu') = %s
        GROUP BY
            COALESCE(gu.id::text, 'nogu_' || e.user_id),
            dept.name, sect.name
        ORDER BY dept.name NULLS LAST, MIN(COALESCE(gu.name, e.name, '?'))
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql_pres, (date_ad,))
        pres_rows = [dict(r) for r in cur.fetchall()]

    present_map: dict = {}
    for row in pres_rows:
        uid = row['global_user_id']
        times  = row.get('punch_times')  or []
        labels = row.get('punch_labels') or []
        punches = [
            {'ts': t, 'label': l or ''}
            for t, l in zip(times, labels + [None] * len(times))
        ]
        if uid not in present_map:
            e = dict(row)
            e['punches'] = punches
            present_map[uid] = e
        else:
            e = present_map[uid]
            if row['first_punch'] and (not e['first_punch'] or row['first_punch'] < e['first_punch']):
                e['first_punch'] = row['first_punch']
            if row['last_punch'] and (not e['last_punch'] or row['last_punch'] > e['last_punch']):
                e['last_punch'] = row['last_punch']
            e['punch_count'] = e.get('punch_count', 0) + row.get('punch_count', 0)
            e['punches'] = sorted(e.get('punches', []) + punches, key=lambda x: x['ts'])

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

    def _cid_num(e):
        v = e.get('company_id') or ''
        try:    return (0, int(v))
        except: return (1, v)
    emp_summaries.sort(key=_cid_num)

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
        if punch and work_min > 0 and (is_weekend or is_holiday):
            # All hours worked on a weekly-off or holiday are overtime.
            ot_min = work_min
        elif punch and not is_weekend and not is_holiday:
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
        VALUES %s
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
    # execute_values needs a single %s in the SQL; per-row literals go in template.
    _template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'device', NOW())"
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, upsert_sql, rows,
                                       template=_template, page_size=100)
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


# ─── Web User Management ──────────────────────────────────────────────────────

def get_web_user_by_username(conn, username: str) -> 'dict | None':
    sql = """
        SELECT wu.id, wu.global_user_id, wu.username, wu.password_hash,
               wu.display_name, wu.role, wu.is_active, wu.last_login_at,
               wu.last_login_ip, wu.must_change_pwd, wu.created_at,
               gu.name AS gu_name, gu.global_user_id AS att_id,
               d.name AS department_name, s.name AS section_name,
               gu.email, gu.phone, gu.designation
        FROM   web_users wu
        LEFT JOIN global_users gu ON gu.id = wu.global_user_id
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        LEFT JOIN sections     s  ON s.id  = gu.section_id
        WHERE  LOWER(wu.username) = LOWER(%s) AND wu.is_active = TRUE
        LIMIT  1
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (username,))
        r = cur.fetchone()
    return dict(r) if r else None


def get_web_user_by_id(conn, user_id: int) -> 'dict | None':
    sql = """
        SELECT wu.id, wu.global_user_id, wu.username, wu.password_hash,
               wu.display_name, wu.role, wu.is_active, wu.last_login_at,
               wu.last_login_ip, wu.must_change_pwd, wu.created_at,
               gu.id AS gu_db_id, gu.name, gu.global_user_id AS att_id,
               gu.employee_id, gu.email, gu.phone, gu.bank_number,
               gu.designation, gu.emp_type, gu.emp_status, gu.join_date,
               gu.department_id, gu.section_id, gu.unit_id, gu.shift_id,
               sh.name AS shift_name,
               gu.level_grade, gu.appointment_date, gu.dob, gu.gender,
               d.name AS department_name, s.name AS section_name,
               u.name AS unit_name
        FROM   web_users wu
        LEFT JOIN global_users gu ON gu.id = wu.global_user_id
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        LEFT JOIN sections     s  ON s.id  = gu.section_id
        LEFT JOIN units        u  ON u.id  = gu.unit_id
        LEFT JOIN shifts       sh ON sh.id = gu.shift_id
        WHERE  wu.id = %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (user_id,))
        r = cur.fetchone()
    return dict(r) if r else None


def get_all_web_users(conn) -> list:
    sql = """
        SELECT wu.id, wu.username, wu.display_name, wu.role, wu.is_active,
               wu.last_login_at, wu.last_login_ip, wu.must_change_pwd,
               wu.created_at, wu.global_user_id,
               gu.name AS gu_name, gu.global_user_id AS att_id,
               gu.designation, gu.emp_type, gu.emp_status,
               d.name AS department_name
        FROM   web_users wu
        LEFT JOIN global_users gu ON gu.id = wu.global_user_id
        LEFT JOIN departments  d  ON d.id  = gu.department_id
        ORDER  BY LOWER(wu.username)
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def create_web_user(conn, username: str, password_hash: str, display_name: str,
                    role: str, global_user_id, created_by: int,
                    must_change_pwd: bool = False) -> int:
    sql = """
        INSERT INTO web_users (username, password_hash, display_name, role,
                               global_user_id, must_change_pwd, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (username, password_hash, display_name, role,
                          global_user_id or None, must_change_pwd, created_by, created_by))
        uid = cur.fetchone()[0]
    conn.commit()
    return uid


def get_global_user_ids_without_web_login(conn) -> list:
    """global_users.id for every active employee who has no linked web_users
    row yet — used to backfill employee logins in bulk."""
    sql = """
        SELECT gu.id
        FROM global_users gu
        LEFT JOIN web_users wu ON wu.global_user_id = gu.id
        WHERE gu.emp_status IS DISTINCT FROM 'DELETED'
          AND wu.id IS NULL
        ORDER BY gu.id
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def create_employee_login(conn, gu_id: int, created_by: int = 0) -> dict:
    """Create a 'employee' role web login for a global user, username =
    their attendance device ID (global_user_id), default password = the
    same ID (forced to change on first login). Returns a status dict;
    skips (does not raise) if a web_user with that username already exists,
    since web_users.username has no DB-level UNIQUE constraint."""
    gu = get_global_user(conn, gu_id)
    if not gu:
        return {'created': False, 'reason': 'global user not found'}
    username = (gu.get('global_user_id') or '').strip()
    if not username:
        return {'created': False, 'reason': 'employee has no attendance device ID'}
    if get_web_user_by_username(conn, username):
        return {'created': False, 'reason': 'username already in use', 'username': username}
    from web.auth import hash_password
    pw_hash = hash_password(username)
    new_id = create_web_user(
        conn, username, pw_hash, gu.get('name') or username,
        'employee', gu_id, created_by, must_change_pwd=True,
    )
    return {'created': True, 'id': new_id, 'username': username}


def get_audited_tables() -> list:
    """Table names covered by the generic audit trigger (see init_schema
    Phase 17) — used to populate the audit log viewer's table filter."""
    return list(_AUDITED_TABLES)


def _audit_log_where(table_name, action, changed_by, record_id, from_date, to_date):
    where = []
    params = []
    if table_name:
        where.append("al.table_name = %s")
        params.append(table_name)
    if action:
        where.append("al.action = %s")
        params.append(action.upper())
    if changed_by:
        where.append("al.changed_by = %s")
        params.append(changed_by)
    if record_id:
        where.append("al.record_id = %s")
        params.append(record_id)
    if from_date:
        where.append("al.changed_at >= %s")
        params.append(from_date)
    if to_date:
        where.append("al.changed_at < (%s::date + INTERVAL '1 day')")
        params.append(to_date)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    return w, params


def get_audit_log(conn, table_name: str | None = None, action: str | None = None,
                   changed_by: int | None = None, record_id: int | None = None,
                   from_date: str | None = None, to_date: str | None = None,
                   limit: int = 50, offset: int = 0) -> list:
    w, params = _audit_log_where(table_name, action, changed_by, record_id, from_date, to_date)
    sql = f"""
        SELECT al.*, wu.username AS changed_by_username, wu.display_name AS changed_by_name
        FROM audit_log al
        LEFT JOIN web_users wu ON wu.id = al.changed_by
        {w}
        ORDER BY al.changed_at DESC
        LIMIT %s OFFSET %s
    """
    params = params + [limit, offset]
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_audit_log_count(conn, table_name: str | None = None, action: str | None = None,
                         changed_by: int | None = None, record_id: int | None = None,
                         from_date: str | None = None, to_date: str | None = None) -> int:
    """Count matching the same filters as get_audit_log — used for server-side pagination,
    so the total reflects the filtered result set, not the whole table."""
    w, params = _audit_log_where(table_name, action, changed_by, record_id, from_date, to_date)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM audit_log al {w}", params)
        return cur.fetchone()[0]


def update_web_user(conn, user_id: int, updated_by: int, **fields) -> None:
    allowed = {'display_name', 'role', 'is_active', 'global_user_id',
               'password_hash', 'must_change_pwd'}
    parts, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            parts.append(f"{k} = %s")
            vals.append(v)
    if not parts:
        return
    parts += ["updated_at = NOW()", "updated_by = %s"]
    vals  += [updated_by, user_id]
    sql = f"UPDATE web_users SET {', '.join(parts)} WHERE id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, vals)
    conn.commit()


def update_web_user_login(conn, user_id: int, ip_address: str) -> None:
    sql = """
        UPDATE web_users
        SET last_login_at = NOW(), last_login_ip = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (ip_address or '', user_id))
    conn.commit()


def add_web_audit_log(conn, web_user_id, username: str, action: str,
                      ip_address: str = '', user_agent: str = '',
                      details: 'dict | None' = None) -> None:
    import json as _json
    sql = """
        INSERT INTO web_user_audit_log
               (web_user_id, username, action, ip_address, user_agent, details)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            web_user_id, username, action,
            ip_address or '', user_agent or '',
            _json.dumps(details) if details else None,
        ))
    conn.commit()


def get_web_audit_logs(conn, web_user_id=None, limit: int = 200) -> list:
    sql = """
        SELECT wl.id, wl.web_user_id, wl.username, wl.action,
               wl.ip_address, wl.user_agent, wl.details, wl.created_at,
               wu.display_name
        FROM   web_user_audit_log wl
        LEFT JOIN web_users wu ON wu.id = wl.web_user_id
        WHERE  (%s::integer IS NULL OR wl.web_user_id = %s)
        ORDER  BY wl.created_at DESC
        LIMIT  %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (web_user_id, web_user_id, limit))
        return [dict(r) for r in cur.fetchall()]


# ─── Company Settings ───────────────────────────────────────────────────────────

def get_company_settings(conn) -> dict:
    """Get company settings - returns a dict, empty dict if none found."""
    sql = "SELECT * FROM company_settings ORDER BY id DESC LIMIT 1"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        r = cur.fetchone()
        return dict(r) if r else {}


def update_company_settings(conn, updated_by: int, **fields) -> int:
    """Update company settings. Returns the ID of the updated/created record."""
    allowed = {'company_name', 'logo_url', 'address', 'phone', 'email',
                'website', 'pan_number', 'fiscal_year_bs',
                'default_shift_start_min', 'default_shift_end_min'}

    # Check if any record exists
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM company_settings ORDER BY id DESC LIMIT 1")
        existing = cur.fetchone()

    if existing:
        # Update existing
        parts, vals = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                parts.append(f"{k} = %s")
                vals.append(v)
        if not parts:
            return existing[0]
        parts.extend(["updated_at = NOW()", "updated_by = %s"])
        vals.extend([updated_by, existing[0]])
        sql = f"UPDATE company_settings SET {', '.join(parts)} WHERE id = %s"
        with conn.cursor() as cur:
            cur.execute(sql, vals)
        conn.commit()
        return existing[0]
    else:
        # Insert new
        cols, vals, placeholders = [], [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                cols.append(k)
                vals.append(v)
                placeholders.append("%s")
        cols.extend(['created_by', 'updated_by'])
        vals.extend([updated_by, updated_by])
        placeholders.extend(['%s', '%s'])
        sql = f"INSERT INTO company_settings ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING id"
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


# ─── Leave with type info ─────────────────────────────────────────────────────

def get_leaves_with_type_for_month(conn, global_user_id: int,
                                   from_ad: str, to_ad: str) -> dict:
    """Returns {date_str: {leave_type_name, code, color_code, is_paid, display_code, is_half_day}}
    for all approved leaves overlapping the given date range for one employee."""
    sql = """
        SELECT la.from_ad, la.to_ad,
               COALESCE(la.is_half_day, FALSE) AS is_half_day,
               COALESCE(la.half_day_part, '') AS half_day_part,
               lt.name AS leave_type_name, lt.code,
               COALESCE(lt.color_code, '#6366f1') AS color_code,
               lt.is_paid, COALESCE(lt.display_code, '') AS display_code
        FROM leave_applications la
        JOIN leave_types lt ON lt.id = la.leave_type_id
        WHERE la.global_user_id = %s
          AND la.status = 'approved'
          AND la.from_ad <= %s
          AND la.to_ad   >= %s
    """
    from datetime import date as _d, timedelta as _td
    result: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_id, to_ad, from_ad))
        rows = cur.fetchall()
    for r in rows:
        fd = r['from_ad'] if isinstance(r['from_ad'], _d) else _d.fromisoformat(str(r['from_ad']))
        td = r['to_ad']   if isinstance(r['to_ad'],   _d) else _d.fromisoformat(str(r['to_ad']))
        info = {
            'leave_type_name': r['leave_type_name'],
            'code':            r['code'],
            'color_code':      r['color_code'],
            'is_paid':         r['is_paid'],
            'display_code':    r['display_code'],
            'is_half_day':     bool(r['is_half_day']),
            'half_day_part':   r['half_day_part'],
        }
        while fd <= td:
            result[fd.isoformat()] = info
            fd += _td(days=1)
    return result


def get_leaves_with_type_batch(conn, from_ad: str, to_ad: str) -> dict:
    """Batch version. Returns {global_user_id: {date_str: leave_info}}."""
    sql = """
        SELECT la.global_user_id, la.from_ad, la.to_ad,
               COALESCE(la.is_half_day, FALSE) AS is_half_day,
               COALESCE(la.half_day_part, '') AS half_day_part,
               lt.name AS leave_type_name, lt.code,
               COALESCE(lt.color_code, '#6366f1') AS color_code,
               lt.is_paid, COALESCE(lt.display_code, '') AS display_code
        FROM leave_applications la
        JOIN leave_types lt ON lt.id = la.leave_type_id
        WHERE la.status = 'approved'
          AND la.from_ad <= %s
          AND la.to_ad   >= %s
    """
    from datetime import date as _d, timedelta as _td
    result: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (to_ad, from_ad))
        rows = cur.fetchall()
    for r in rows:
        gid = r['global_user_id']
        fd = r['from_ad'] if isinstance(r['from_ad'], _d) else _d.fromisoformat(str(r['from_ad']))
        td = r['to_ad']   if isinstance(r['to_ad'],   _d) else _d.fromisoformat(str(r['to_ad']))
        info = {
            'leave_type_name': r['leave_type_name'],
            'code':            r['code'],
            'color_code':      r['color_code'],
            'is_paid':         r['is_paid'],
            'display_code':    r['display_code'],
            'is_half_day':     bool(r['is_half_day']),
            'half_day_part':   r['half_day_part'],
        }
        if gid not in result:
            result[gid] = {}
        while fd <= td:
            result[gid][fd.isoformat()] = info
            fd += _td(days=1)
    return result


# ─── Kaaj records ─────────────────────────────────────────────────────────────

def get_kaaj_records(conn, global_user_id=None, from_ad=None, to_ad=None,
                     limit: int = 500) -> list:
    conds = []
    params: list = []
    if global_user_id:
        conds.append("kr.global_user_id = %s"); params.append(global_user_id)
    if from_ad:
        conds.append("kr.ad_date >= %s"); params.append(from_ad)
    if to_ad:
        conds.append("kr.ad_date <= %s"); params.append(to_ad)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT kr.*, gu.name AS emp_name,
               COALESCE(gu.global_user_id, '') AS company_id,
               d.name AS department_name
        FROM kaaj_records kr
        JOIN global_users gu ON gu.id = kr.global_user_id
        LEFT JOIN departments d ON d.id = gu.department_id
        {where}
        ORDER BY kr.ad_date DESC, gu.name
        LIMIT %s
    """
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_kaaj_for_dates(conn, global_user_ids: list,
                       from_ad: str, to_ad: str) -> dict:
    """Returns {global_user_id: {date_str: {is_paid, reason}}}."""
    if not global_user_ids:
        return {}
    sql = """
        SELECT global_user_id, ad_date, is_paid, reason
        FROM kaaj_records
        WHERE global_user_id = ANY(%s)
          AND ad_date BETWEEN %s AND %s
    """
    result: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_ids, from_ad, to_ad))
        for r in cur.fetchall():
            gid = r['global_user_id']
            ds  = r['ad_date'].isoformat() if hasattr(r['ad_date'], 'isoformat') else str(r['ad_date'])
            result.setdefault(gid, {})[ds] = {'is_paid': r['is_paid'], 'reason': r['reason']}
    return result


def create_kaaj_record(conn, global_user_id: int, ad_date: str, bs_date: str,
                       is_paid: bool, reason: str, approved_by: str,
                       created_by: int) -> int:
    sql = """
        INSERT INTO kaaj_records
            (global_user_id, ad_date, bs_date, is_paid, reason, approved_by, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (global_user_id, ad_date) DO UPDATE SET
            is_paid     = EXCLUDED.is_paid,
            reason      = EXCLUDED.reason,
            approved_by = EXCLUDED.approved_by,
            updated_at  = NOW()
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (global_user_id, ad_date, bs_date, is_paid,
                          reason or None, approved_by or None, created_by or None))
        return cur.fetchone()[0]


def update_kaaj_record(conn, record_id: int, is_paid: bool,
                       reason: str, approved_by: str) -> None:
    sql = """
        UPDATE kaaj_records
           SET is_paid = %s, reason = %s, approved_by = %s, updated_at = NOW()
         WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (is_paid, reason or None, approved_by or None, record_id))


def delete_kaaj_record(conn, record_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kaaj_records WHERE id = %s", (record_id,))


# ─── Day remarks ──────────────────────────────────────────────────────────────

def get_day_remarks(conn, global_user_id: int, from_ad: str, to_ad: str) -> dict:
    """Returns {date_str: remark_text}."""
    sql = """
        SELECT ad_date, remark_text
        FROM attendance_day_remarks
        WHERE global_user_id = %s AND ad_date BETWEEN %s AND %s
    """
    result: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_id, from_ad, to_ad))
        for r in cur.fetchall():
            ds = r['ad_date'].isoformat() if hasattr(r['ad_date'], 'isoformat') else str(r['ad_date'])
            result[ds] = r['remark_text']
    return result


def get_day_remarks_batch(conn, global_user_ids: list,
                          from_ad: str, to_ad: str) -> dict:
    """Returns {global_user_id: {date_str: remark_text}}."""
    if not global_user_ids:
        return {}
    sql = """
        SELECT global_user_id, ad_date, remark_text
        FROM attendance_day_remarks
        WHERE global_user_id = ANY(%s) AND ad_date BETWEEN %s AND %s
    """
    result: dict = {}
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (global_user_ids, from_ad, to_ad))
        for r in cur.fetchall():
            gid = r['global_user_id']
            ds  = r['ad_date'].isoformat() if hasattr(r['ad_date'], 'isoformat') else str(r['ad_date'])
            result.setdefault(gid, {})[ds] = r['remark_text']
    return result


def upsert_day_remark(conn, global_user_id: int, ad_date: str, bs_date: str,
                      remark_text: str, created_by: int) -> int:
    sql = """
        INSERT INTO attendance_day_remarks
            (global_user_id, ad_date, bs_date, remark_text, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (global_user_id, ad_date) DO UPDATE SET
            remark_text = EXCLUDED.remark_text,
            bs_date     = COALESCE(EXCLUDED.bs_date, attendance_day_remarks.bs_date)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (global_user_id, ad_date, bs_date or None,
                          remark_text, created_by or None))
        return cur.fetchone()[0]


def delete_day_remark(conn, remark_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM attendance_day_remarks WHERE id = %s", (remark_id,))


# ─── Manual attendance ────────────────────────────────────────────────────────

def add_manual_attendance_entry(conn, global_user_id: int,
                                 ad_date: str, bs_date: str,
                                 in_time_str: str, out_time_str: str,
                                 manual_note: str, created_by: int) -> list:
    """Insert manual punch(es) into attendance_logs. Returns list of inserted IDs."""
    import zoneinfo as _zi
    from datetime import datetime as _dt, date as _ddate

    NPT = _zi.ZoneInfo('Asia/Kathmandu')
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT e.device_id, e.uid, e.user_id, e.name
            FROM employees e
            WHERE e.global_user_id = %s
            ORDER BY e.id
            LIMIT 1
        """, (global_user_id,))
        emp = cur.fetchone()
    if not emp:
        raise ValueError(f"No device link for global_user_id={global_user_id}")

    device_id = emp['device_id']
    uid       = emp['uid']
    user_id   = emp['user_id']
    name      = emp['name']
    ad        = _ddate.fromisoformat(ad_date)

    def _build_ts(time_str: str) -> _dt:
        h, m = (int(x) for x in time_str.split(':')[:2])
        return _dt(ad.year, ad.month, ad.day, h, m, 0, tzinfo=NPT)

    inserted = []
    note_txt = (manual_note or '').strip() or None
    if in_time_str:
        ts = _build_ts(in_time_str)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO attendance_logs
                    (device_id, uid, user_id, name, timestamp, bs_date, source, manual_note, punch)
                VALUES (%s, %s, %s, %s, %s, %s, 'manual', %s, 0)
                ON CONFLICT (device_id, uid, timestamp) DO NOTHING
                RETURNING id
            """, (device_id, uid, user_id, name, ts, bs_date or '', note_txt))
            row = cur.fetchone()
            if row:
                inserted.append(row[0])
    if out_time_str:
        ts = _build_ts(out_time_str)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO attendance_logs
                    (device_id, uid, user_id, name, timestamp, bs_date, source, manual_note, punch)
                VALUES (%s, %s, %s, %s, %s, %s, 'manual', %s, 1)
                ON CONFLICT (device_id, uid, timestamp) DO NOTHING
                RETURNING id
            """, (device_id, uid, user_id, name, ts, bs_date or '', note_txt))
            row = cur.fetchone()
            if row:
                inserted.append(row[0])
    return inserted


def get_manual_attendance(conn, global_user_id=None,
                          from_ad=None, to_ad=None, limit: int = 500) -> list:
    conds = ["al.source = 'manual'"]
    params: list = []
    if global_user_id:
        conds.append("e.global_user_id = %s"); params.append(global_user_id)
    if from_ad:
        conds.append("(al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date >= %s"); params.append(from_ad)
    if to_ad:
        conds.append("(al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date <= %s"); params.append(to_ad)
    where = "WHERE " + " AND ".join(conds)
    sql = f"""
        SELECT al.id, al.timestamp AT TIME ZONE 'Asia/Kathmandu' AS ts_npt,
               al.user_id, al.name, al.bs_date, al.manual_note, al.punch,
               d.name AS device_name,
               gu.name AS emp_name, gu.global_user_id AS company_id,
               e.global_user_id AS global_id
        FROM attendance_logs al
        JOIN devices d ON d.id = al.device_id
        JOIN employees e ON e.device_id = al.device_id AND e.uid = al.uid
        JOIN global_users gu ON gu.id = e.global_user_id
        {where}
        ORDER BY al.timestamp DESC
        LIMIT %s
    """
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def delete_manual_attendance(conn, entry_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM attendance_logs WHERE id = %s AND source = 'manual'", (entry_id,))


# ─── Opening balance management ───────────────────────────────────────────────

def get_leave_opening_balances(conn, bs_year: int) -> list:
    """Get all employees x leave_types with opening balances for a year."""
    sql = """
        SELECT gu.id AS global_user_id, gu.name, gu.global_user_id AS company_id,
               d.name AS department_name,
               lt.id AS leave_type_id, lt.name AS leave_type_name, lt.code,
               COALESCE(lt.sort_order, 99) AS sort_order,
               COALESCE(lb.opening_balance, 0) AS opening_balance,
               COALESCE(lb.days_earned, 0)     AS days_earned,
               COALESCE(lb.days_taken, 0)      AS days_taken,
               COALESCE(lb.carried_forward, 0) AS carried_forward,
               lb.id AS balance_id
        FROM global_users gu
        CROSS JOIN leave_types lt
        LEFT JOIN leave_balances lb
               ON lb.global_user_id = gu.id
              AND lb.leave_type_id  = lt.id
              AND lb.bs_year        = %s
        LEFT JOIN departments d ON d.id = gu.department_id
        WHERE (gu.emp_status IS NULL OR gu.emp_status = 'ACTIVE')
          AND lt.code NOT IN ('KAAJ_PAID', 'KAAJ_UNPAID')
        ORDER BY gu.name, lt.sort_order, lt.name
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (bs_year,))
        return [dict(r) for r in cur.fetchall()]


def upsert_leave_opening_balance(conn, global_user_id: int, leave_type_id: int,
                                  bs_year: int, opening_balance: float,
                                  days_earned: float) -> None:
    sql = """
        INSERT INTO leave_balances
            (global_user_id, leave_type_id, bs_year, opening_balance, days_earned, days_taken)
        VALUES (%s, %s, %s, %s, %s, 0)
        ON CONFLICT (global_user_id, leave_type_id, bs_year) DO UPDATE SET
            opening_balance = EXCLUDED.opening_balance,
            days_earned     = EXCLUDED.days_earned
    """
    with conn.cursor() as cur:
        cur.execute(sql, (global_user_id, leave_type_id, bs_year,
                          opening_balance, days_earned))



# ═══════════════════════════════════════════════════════════════════════════
#  Payroll: salary structures, runs, items  (Phase 13)
# ═══════════════════════════════════════════════════════════════════════════

def get_salary_structure(conn, global_user_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_salary_structures WHERE global_user_id=%s",
                    (global_user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_all_salary_structures(conn) -> list:
    """Every global_user with their salary structure (LEFT JOIN -- nulls if unset)."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT gu.id AS global_user_id, gu.global_user_id AS emp_code, gu.name,
                   d.name AS department,
                   s.basic_salary, s.allowances, s.daily_hours, s.ot_multiplier,
                   s.marital, s.other_deductions, s.is_active, s.effective_bs
            FROM global_users gu
            LEFT JOIN payroll_salary_structures s ON s.global_user_id = gu.id
            LEFT JOIN departments d ON d.id = gu.department_id
            ORDER BY gu.name NULLS LAST, gu.global_user_id
        """)
        return [dict(r) for r in cur.fetchall()]


def upsert_salary_structure(conn, global_user_id: int, basic_salary, allowances,
                            daily_hours, ot_multiplier, marital,
                            other_deductions=0, effective_bs="") -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_salary_structures
                (global_user_id, basic_salary, allowances, daily_hours,
                 ot_multiplier, marital, other_deductions, effective_bs, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW())
            ON CONFLICT (global_user_id) DO UPDATE SET
                basic_salary     = EXCLUDED.basic_salary,
                allowances       = EXCLUDED.allowances,
                daily_hours      = EXCLUDED.daily_hours,
                ot_multiplier    = EXCLUDED.ot_multiplier,
                marital          = EXCLUDED.marital,
                other_deductions = EXCLUDED.other_deductions,
                effective_bs     = EXCLUDED.effective_bs,
                updated_at       = NOW()
        """, (global_user_id, basic_salary, allowances, daily_hours,
              ot_multiplier, marital, other_deductions, effective_bs))
    conn.commit()


def get_payroll_run(conn, run_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_runs WHERE id=%s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_payroll_run_by_period(conn, bs_year: int, bs_month: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_runs WHERE bs_year=%s AND bs_month=%s",
                    (bs_year, bs_month))
        row = cur.fetchone()
        return dict(row) if row else None


def list_payroll_runs(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT r.*,
                   COUNT(i.id)          AS item_count,
                   COALESCE(SUM(i.gross),0)   AS total_gross,
                   COALESCE(SUM(i.tax),0)     AS total_tax,
                   COALESCE(SUM(i.net_pay),0) AS total_net
            FROM payroll_runs r
            LEFT JOIN payroll_items i ON i.run_id = r.id
            GROUP BY r.id
            ORDER BY r.bs_year DESC, r.bs_month DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def create_payroll_run(conn, bs_year, bs_month, period_index, working_days,
                       created_by="", fiscal_year_id: int | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_runs (bs_year, bs_month, period_index, working_days, created_by, fiscal_year_id)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (bs_year, bs_month) DO UPDATE SET
                period_index   = EXCLUDED.period_index,
                working_days   = EXCLUDED.working_days,
                fiscal_year_id = EXCLUDED.fiscal_year_id
            RETURNING id
        """, (bs_year, bs_month, period_index, working_days, created_by, fiscal_year_id))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def clear_payroll_items(conn, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM payroll_items WHERE run_id=%s", (run_id,))
    conn.commit()


def insert_payroll_item(conn, run_id, global_user_id, data: dict) -> int:
    """Insert/update a payslip line item. Returns the item's id.

    Also stamps a point-in-time identity snapshot (employee_id_snapshot,
    employee_name_snapshot) from global_users as of THIS moment — per
    payroll_plan.md Section 3.2, a generated payslip must keep showing what
    was true when it was generated even if the employee's master ID or name
    is corrected later. Snapshot is refreshed on every re-generation/edit of
    this item (ON CONFLICT), which is correct: it should reflect the most
    recent computation, not the very first one.
    """
    import json
    with conn.cursor() as cur:
        cur.execute("SELECT employee_id, name FROM global_users WHERE id=%s", (global_user_id,))
        row = cur.fetchone()
        emp_id_snap, name_snap = (row[0], row[1]) if row else (None, None)
        cur.execute("""
            INSERT INTO payroll_items
                (run_id, global_user_id, present_days, ot_hours, ot_manual,
                 earned_basic, earned_allowance, ot_pay, other_earnings, gross,
                 taxable_this, taxable_ytd, tax, other_deductions, net_pay, detail,
                 employee_id_snapshot, employee_name_snapshot)
            VALUES (%(run_id)s,%(gu)s,%(present_days)s,%(ot_hours)s,%(ot_manual)s,
                    %(earned_basic)s,%(earned_allowance)s,%(ot_pay)s,%(other_earnings)s,%(gross)s,
                    %(taxable_this)s,%(taxable_ytd)s,%(tax)s,%(other_deductions)s,%(net_pay)s,%(detail)s,
                    %(emp_id_snap)s,%(name_snap)s)
            ON CONFLICT (run_id, global_user_id) DO UPDATE SET
                present_days=EXCLUDED.present_days, ot_hours=EXCLUDED.ot_hours,
                ot_manual=EXCLUDED.ot_manual, earned_basic=EXCLUDED.earned_basic,
                earned_allowance=EXCLUDED.earned_allowance, ot_pay=EXCLUDED.ot_pay,
                other_earnings=EXCLUDED.other_earnings, gross=EXCLUDED.gross,
                taxable_this=EXCLUDED.taxable_this, taxable_ytd=EXCLUDED.taxable_ytd,
                tax=EXCLUDED.tax, other_deductions=EXCLUDED.other_deductions,
                net_pay=EXCLUDED.net_pay, detail=EXCLUDED.detail,
                employee_id_snapshot=EXCLUDED.employee_id_snapshot,
                employee_name_snapshot=EXCLUDED.employee_name_snapshot
            RETURNING id
        """, {"run_id": run_id, "gu": global_user_id, **data,
              "detail": json.dumps(data.get("detail")) if data.get("detail") is not None else None,
              "emp_id_snap": emp_id_snap, "name_snap": name_snap})
        item_id = cur.fetchone()[0]
    conn.commit()
    return item_id


def save_payroll_item_breakdown(conn, item_id: int, resolved_heads: list, resolved_deductions: list) -> None:
    """Persist the immutable per-head/per-deduction breakdown for one payslip
    line item (Phase 8) — clears and re-inserts, so regenerating a run always
    reflects the current computation without ever touching another item's rows."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM payroll_item_heads WHERE item_id=%s", (item_id,))
        for h in resolved_heads:
            cur.execute("""
                INSERT INTO payroll_item_heads (item_id, head_code, head_name, category, frequency, amount, is_taxable)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (item_id, h["code"], h["name"], h["category"], h["frequency"], h["amount"], h["is_taxable"]))
        cur.execute("DELETE FROM payroll_item_deductions WHERE item_id=%s", (item_id,))
        for d in resolved_deductions:
            cur.execute("""
                INSERT INTO payroll_item_deductions (item_id, deduction_code, deduction_name, amount, is_pretax, capped)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (item_id, d["code"], d["name"], d["amount"], d["is_pretax"], d["capped"]))
    conn.commit()


def get_payroll_item_breakdown(conn, item_id: int) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM payroll_item_heads WHERE item_id=%s ORDER BY id", (item_id,))
        heads = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM payroll_item_deductions WHERE item_id=%s ORDER BY id", (item_id,))
        deductions = [dict(r) for r in cur.fetchall()]
    return {"heads": heads, "deductions": deductions}


# ═══════════════════════════════════════════════════════════════════════════
#  Annual Payroll Summary  (Phase 9, payroll_plan.md Section 2.8/9)
# ═══════════════════════════════════════════════════════════════════════════


def list_employees_with_payroll_in_fy(conn, fiscal_year_id: int) -> list:
    """Employees who have at least one generated payroll run in this fiscal year."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT gu.id AS global_id, gu.global_user_id AS company_id,
                   gu.employee_id, gu.name, d.name AS department
            FROM payroll_items i
            JOIN payroll_runs r ON r.id = i.run_id
            JOIN global_users gu ON gu.id = i.global_user_id
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE r.fiscal_year_id = %s
            ORDER BY gu.name NULLS LAST
        """, (fiscal_year_id,))
        return [dict(r) for r in cur.fetchall()]


def get_annual_payroll_summary(conn, fiscal_year_id: int, global_user_id: int) -> dict | None:
    """Aggregate every payroll run in a fiscal year for one employee into an
    annual summary matching the reference sheet's layout: per-head annual
    totals, per-deduction annual totals, gross/taxable/tax reconciliation,
    and the tax slab breakdown on the annual taxable figure.

    Returns None if the employee has no payroll items in this fiscal year.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT i.*, r.bs_year, r.bs_month, r.period_index
            FROM payroll_items i
            JOIN payroll_runs r ON r.id = i.run_id
            WHERE r.fiscal_year_id = %s AND i.global_user_id = %s
            ORDER BY r.period_index
        """, (fiscal_year_id, global_user_id))
        items = [dict(r) for r in cur.fetchall()]
        if not items:
            return None

        item_ids = [i["id"] for i in items]
        cur.execute("SELECT * FROM payroll_item_heads WHERE item_id = ANY(%s)", (item_ids,))
        all_heads = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM payroll_item_deductions WHERE item_id = ANY(%s)", (item_ids,))
        all_deductions = [dict(r) for r in cur.fetchall()]

    # Aggregate heads/deductions by code across every month they appeared in.
    heads_by_code: dict = {}
    for h in all_heads:
        c = h["head_code"]
        if c not in heads_by_code:
            heads_by_code[c] = {"code": c, "name": h["head_name"], "frequency": h["frequency"], "annual_amount": 0}
        heads_by_code[c]["annual_amount"] += float(h["amount"])

    deductions_by_code: dict = {}
    for d in all_deductions:
        c = d["deduction_code"]
        if c not in deductions_by_code:
            deductions_by_code[c] = {"code": c, "name": d["deduction_name"], "is_pretax": d["is_pretax"], "annual_amount": 0}
        deductions_by_code[c]["annual_amount"] += float(d["amount"])

    gross_annual = sum(float(i["gross"]) for i in items)
    pretax_annual = sum(v["annual_amount"] for v in deductions_by_code.values() if v["is_pretax"])
    taxable_annual = sum(float(i["taxable_this"]) for i in items)
    tax_annual = sum(float(i["tax"]) for i in items)
    net_annual = sum(float(i["net_pay"]) for i in items)
    other_deductions_annual = sum(float(i["other_deductions"]) for i in items)

    return {
        "months_included": len(items),
        "heads": sorted(heads_by_code.values(), key=lambda h: h["code"]),
        "deductions": sorted(deductions_by_code.values(), key=lambda d: d["code"]),
        "gross_annual": round(gross_annual, 2),
        "pretax_deductions_annual": round(pretax_annual, 2),
        "taxable_annual": round(taxable_annual, 2),
        "tax_annual": round(tax_annual, 2),
        "other_deductions_annual": round(other_deductions_annual, 2),
        "net_annual": round(net_annual, 2),
        "items": items,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Tax Projection — yearly tax prediction & monthly deduction forecast
# ═══════════════════════════════════════════════════════════════════════════
#
# Unlike get_annual_payroll_summary() above (which aggregates ACTUAL generated
# payroll_runs), this projects a FULL fiscal year from an employee's CURRENT
# salary head / deduction configuration — so it works before any payroll run
# has ever been generated, for HR planning and tax-liability forecasting.
# Reuses the exact same resolve_employee_heads_for_month / resolve_employee_
# deductions_for_month / compute_payslip / cumulative-TDS pipeline proven in
# Phase 6's 12-month simulation test (test_payroll.py), so the math is the
# same code path payroll generation itself uses — not a separate estimate.
#
# "Integrated with attendance": for a month that has already happened (or is
# in progress), the projection uses REAL attendance via
# get_month_attendance_summary(); for a future month, it assumes full
# attendance (can't know future punches) and labels that row as projected.


def _fiscal_year_month_sequence(fiscal_year_row: dict) -> list:
    """[(bs_year, bs_month), ...] for all 12 months of a fiscal year, in order,
    starting from the year's actual start month (e.g. Shrawan=4)."""
    start_year = int(fiscal_year_row["start_bs"][:4])
    start_month = int(fiscal_year_row["start_bs"][5:7])
    seq = []
    y, m = start_year, start_month
    for _ in range(12):
        seq.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return seq


def get_tax_projection_for_employee(conn, global_user_id: int, fiscal_year_id: int) -> dict | None:
    """Project a full fiscal year for one employee from their current salary
    head/deduction configuration. Returns None if the employee has no BASIC
    head configured (nothing to project). Each monthly row also carries
    'is_projected' — False when built from real attendance, True when
    assumed (future month)."""
    import payroll as pay
    from datetime import date as _ddate
    import attendance_engine  # noqa: F401 (imported for clarity; used via get_month_attendance_summary)

    fy = get_fiscal_year(conn, fiscal_year_id)
    if not fy:
        return None
    s = get_salary_structure(conn, global_user_id)
    marital = (s or {}).get("marital", "single")
    slab_info = get_tax_slab_bands(conn, fiscal_year_id, marital)
    if not slab_info or not slab_info["is_confirmed"]:
        return {"error": f"No confirmed tax slabs for fiscal year {fy['fiscal_year_bs']} ({marital})."}

    fiscal_start_month = int(fy["start_bs"][5:7])
    today = _ddate.today()

    months = []
    taxable_ytd = 0.0
    tax_paid = 0.0
    cumulative_gross = 0.0
    has_any_head = False

    for bs_year, bs_month in _fiscal_year_month_sequence(fy):
        mi = None
        try:
            import nepali_utils
            mi = nepali_utils.bs_month_info(bs_year, bs_month)
        except Exception:
            mi = None
        if not mi:
            continue
        from_ad, to_ad = mi["first_ad"], mi["last_ad"]

        resolved_heads = resolve_employee_heads_for_month(conn, global_user_id, bs_month)
        basic_head = next((h for h in resolved_heads if h["code"] == "BASIC"), None)
        if not basic_head:
            continue
        has_any_head = True
        basic_amount = basic_head["amount"]
        other_monthly_sum = sum(h["amount"] for h in resolved_heads
                                if h["frequency"] == "monthly" and h["code"] != "BASIC")
        onetime_sum = sum(h["amount"] for h in resolved_heads if h["frequency"] != "monthly")
        gross_unprorated = float(basic_amount) + float(other_monthly_sum)
        resolved_deductions = resolve_employee_deductions_for_month(conn, global_user_id, basic_amount, gross_unprorated)

        is_future = _ddate.fromisoformat(from_ad) > today
        if is_future:
            wd = mi["days"]
            present_days = wd
        else:
            summary = get_month_attendance_summary(conn, global_user_id, from_ad, to_ad)
            wd = summary["working_days"] or mi["days"]
            present_days = summary["paid_days"]

        period_index = pay.fiscal_period_index(bs_month, fiscal_start_month)
        slip = pay.compute_payslip(
            basic_salary=basic_amount, allowances=other_monthly_sum,
            working_days=wd, present_days=present_days, daily_hours=s.get("daily_hours", 8) if s else 8,
            other_earnings=onetime_sum, other_deductions=(s or {}).get("other_deductions", 0) or 0,
            pretax_deductions=resolved_deductions, tax_bands=slab_info["bands"],
            period_index=period_index, taxable_ytd_before=taxable_ytd, tax_paid_before=tax_paid,
        )
        taxable_ytd = float(slip["taxable_ytd"])
        tax_paid += float(slip["tax"])
        cumulative_gross += float(slip["gross"])

        months.append({
            "bs_year": bs_year, "bs_month": bs_month, "month_name": mi["month_name"],
            "is_projected": is_future,
            "gross": float(slip["gross"]),
            "heads": resolved_heads,
            "deductions": resolved_deductions,
            "pretax_deductions_total": float(slip["pretax_deductions_total"]),
            "taxable_this_month": float(slip["taxable_this_month"]),
            "tax_this_month": float(slip["tax"]),
            "net_pay": float(slip["net_pay"]),
        })

    if not has_any_head:
        return None

    return {
        "fiscal_year": fy, "marital": marital, "months": months,
        "annual_gross": round(cumulative_gross, 2),
        "annual_taxable": round(taxable_ytd, 2),
        "annual_tax": round(tax_paid, 2),
        "monthly_tax_avg": round(tax_paid / 12, 2),
    }


def get_tax_projection_all_employees(conn, fiscal_year_id: int) -> list:
    """Yearly tax prediction for every employee with a BASIC head configured —
    the register view: one row per employee, annual totals only (no monthly
    detail — drill into get_tax_projection_for_employee for that)."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT eh.global_user_id, gu.name, gu.global_user_id AS company_id,
                   d.name AS department
            FROM payroll_employee_heads eh
            JOIN payroll_heads h ON h.id = eh.head_id AND h.code = 'BASIC'
            JOIN global_users gu ON gu.id = eh.global_user_id
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE eh.is_active
            ORDER BY gu.name NULLS LAST
        """)
        employees = [dict(r) for r in cur.fetchall()]

    rows = []
    for emp in employees:
        proj = get_tax_projection_for_employee(conn, emp["global_user_id"], fiscal_year_id)
        if not proj or "error" in proj:
            rows.append({**emp, "error": (proj or {}).get("error", "no BASIC head configured")})
            continue
        rows.append({
            **emp,
            "annual_gross": proj["annual_gross"], "annual_taxable": proj["annual_taxable"],
            "annual_tax": proj["annual_tax"], "monthly_tax_avg": proj["monthly_tax_avg"],
        })
    return rows


def get_payroll_items(conn, run_id: int) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT i.*, gu.name, gu.global_user_id AS emp_code, d.name AS department
            FROM payroll_items i
            JOIN global_users gu ON gu.id = i.global_user_id
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE i.run_id = %s
            ORDER BY gu.name NULLS LAST
        """, (run_id,))
        return [dict(r) for r in cur.fetchall()]


def get_payroll_item(conn, run_id: int, global_user_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT i.*, gu.name, gu.global_user_id AS emp_code, d.name AS department
            FROM payroll_items i
            JOIN global_users gu ON gu.id = i.global_user_id
            LEFT JOIN departments d ON d.id = gu.department_id
            WHERE i.run_id=%s AND i.global_user_id=%s
        """, (run_id, global_user_id))
        row = cur.fetchone()
        return dict(row) if row else None


def get_ytd_tax_totals(conn, global_user_id: int, bs_year: int, before_period_index: int) -> dict:
    """Sum taxable income & tax already withheld earlier in the same fiscal year."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(i.taxable_this),0), COALESCE(SUM(i.tax),0)
            FROM payroll_items i
            JOIN payroll_runs r ON r.id = i.run_id
            WHERE i.global_user_id = %s
              AND r.period_index < %s
              AND ( (r.bs_year = %s) OR (r.bs_year = %s AND r.bs_month >= 4)
                    OR (r.bs_year = %s AND r.bs_month <= 3) )
        """, (global_user_id, before_period_index, bs_year, bs_year, bs_year + 1))
        row = cur.fetchone()
        return {"taxable_before": row[0] or 0, "tax_paid_before": row[1] or 0}


# ═══════════════════════════════════════════════════════════════════════════
#  Attendance → payroll linkage — live pipeline  (Phase 7)
# ═══════════════════════════════════════════════════════════════════════════
#
# Retires the old attendance_daily-based get_month_ot_hours/
# get_month_present_days/get_month_ot_split — payroll now reads from the
# exact same live computation (attendance_engine.compute_monthly_report +
# monthly_totals) that /reports/monthly/view shows a user, instead of a
# separate pre-aggregated table that could silently drift from it
# (payroll_plan.md Section 8). Also fixes the paid-leave gap: the old
# present-days count only counted actually-worked days, so an employee on
# approved paid leave lost salary for those days under proration — this
# unified summary distinguishes paid vs. unpaid leave via leave_types.is_paid.


def get_month_attendance_summary(conn, global_user_id: int, from_ad: str, to_ad: str) -> dict:
    """Full month attendance summary for payroll, computed via the live
    attendance report pipeline (attendance_engine) — the single source of
    truth shared with /reports/monthly/view, /reports/monthly/print-all,
    /reports/hajiri, and (from Phase 8 onward) payroll generation.
    """
    import attendance_engine as ae
    from datetime import date as _ddate, timedelta as _dtd

    with conn.cursor() as cur:
        cur.execute("SELECT device_id, user_id FROM employees WHERE global_user_id=%s", (global_user_id,))
        pairs = [(r[0], r[1]) for r in cur.fetchall()]

    daily = get_employee_daily_attendance_multi(conn, pairs, from_ad, to_ad) if pairs else []
    shift_cal = get_shift_calendar(conn, global_user_id, from_ad, to_ad)
    holiday_map = {
        (h['holiday_ad'].isoformat() if hasattr(h['holiday_ad'], 'isoformat') else str(h['holiday_ad'])): h
        for h in get_holidays(conn, from_ad, to_ad)
    }

    paid_leave_dates: set = set()
    unpaid_leave_dates: set = set()
    for la in get_leave_applications(conn, global_user_id=global_user_id, status='approved',
                                     from_ad=from_ad, to_ad=to_ad):
        ld = la['from_ad'] if isinstance(la['from_ad'], _ddate) else _ddate.fromisoformat(str(la['from_ad']))
        le = la['to_ad'] if isinstance(la['to_ad'], _ddate) else _ddate.fromisoformat(str(la['to_ad']))
        target = paid_leave_dates if la['is_paid'] else unpaid_leave_dates
        while ld <= le:
            target.add(ld.isoformat())
            ld += _dtd(days=1)

    si_min, so_min = get_default_shift_window(conn)
    days = ae.compute_monthly_report(daily, from_ad, to_ad, si_min, so_min,
                                     shift_cal, holiday_map, paid_leave_dates | unpaid_leave_dates)
    totals = ae.monthly_totals(days)

    def _parse_min(s):
        if not s:
            return 0
        try:
            p = str(s).split(':')
            return int(p[0]) * 60 + int(p[1])
        except Exception:
            return 0

    paid_leave_days = unpaid_leave_days = 0
    regular_ot_min = holiday_ot_min = 0
    late_in_min = early_out_min = 0
    for d in days:
        if d['remark'] == 'Leave':
            if d['ad_date'] in paid_leave_dates:
                paid_leave_days += 1
            else:
                unpaid_leave_days += 1
        if d['remark'] in ('Weekend', 'Holiday', 'Festival'):
            # compute_monthly_report never flags 'ot' on off-days (planned
            # work is 0 there) — any worked time on a day off is entirely
            # holiday OT, not captured by the 'ot' field at all.
            holiday_ot_min += d.get('work_min', 0) or 0
        else:
            regular_ot_min += _parse_min(d['ot'])
        late_in_min += _parse_min(d['late_in'])
        early_out_min += _parse_min(d['early_out'])

    present_days = totals['counts']['Present']
    working_days = totals['working_days']
    return {
        'working_days': working_days,
        'present_days': present_days,
        'paid_leave_days': paid_leave_days,
        'unpaid_leave_days': unpaid_leave_days,
        'absent_days': totals['counts']['Absent'],
        'weekend_days': totals['counts']['Weekend'],
        'holiday_days': totals['counts']['Holiday'],
        'festival_days': totals['counts']['Festival'],
        'total_days': totals['total_days'],
        'paid_days': min(present_days + paid_leave_days, working_days),
        'total_work_minutes': sum(d.get('work_min', 0) or 0 for d in days),
        'regular_ot_minutes': regular_ot_min,
        'holiday_ot_minutes': holiday_ot_min,
        'late_in_minutes': late_in_min,
        'early_out_minutes': early_out_min,
    }


def save_payroll_attendance_snapshot(conn, run_id: int, global_user_id: int, summary: dict) -> None:
    """Persist a get_month_attendance_summary() result for a payroll run
    (Section 8.1) — an immutable point-in-time record, not a live
    recomputation. Re-generating a run overwrites its own snapshot row (the
    audit trigger captures the old/new diff), but never affects other runs."""
    with conn.cursor() as cur:
        cur.execute("SELECT employee_id FROM global_users WHERE id=%s", (global_user_id,))
        row = cur.fetchone()
        emp_id_snap = row[0] if row else None
        cur.execute("""
            INSERT INTO payroll_attendance_snapshot
                (run_id, global_user_id, employee_id_snapshot, working_days, present_days,
                 paid_leave_days, unpaid_leave_days, absent_days, weekend_days, holiday_days,
                 festival_days, total_days, paid_days, total_work_minutes, regular_ot_minutes,
                 holiday_ot_minutes, late_in_minutes, early_out_minutes, computed_at)
            VALUES (%(run_id)s, %(gu)s, %(emp_id_snap)s, %(working_days)s, %(present_days)s,
                    %(paid_leave_days)s, %(unpaid_leave_days)s, %(absent_days)s, %(weekend_days)s,
                    %(holiday_days)s, %(festival_days)s, %(total_days)s, %(paid_days)s,
                    %(total_work_minutes)s, %(regular_ot_minutes)s, %(holiday_ot_minutes)s,
                    %(late_in_minutes)s, %(early_out_minutes)s, NOW())
            ON CONFLICT (run_id, global_user_id) DO UPDATE SET
                employee_id_snapshot = EXCLUDED.employee_id_snapshot,
                working_days         = EXCLUDED.working_days,
                present_days         = EXCLUDED.present_days,
                paid_leave_days      = EXCLUDED.paid_leave_days,
                unpaid_leave_days    = EXCLUDED.unpaid_leave_days,
                absent_days          = EXCLUDED.absent_days,
                weekend_days         = EXCLUDED.weekend_days,
                holiday_days         = EXCLUDED.holiday_days,
                festival_days        = EXCLUDED.festival_days,
                total_days           = EXCLUDED.total_days,
                paid_days            = EXCLUDED.paid_days,
                total_work_minutes   = EXCLUDED.total_work_minutes,
                regular_ot_minutes   = EXCLUDED.regular_ot_minutes,
                holiday_ot_minutes   = EXCLUDED.holiday_ot_minutes,
                late_in_minutes      = EXCLUDED.late_in_minutes,
                early_out_minutes    = EXCLUDED.early_out_minutes,
                computed_at          = NOW()
        """, {"run_id": run_id, "gu": global_user_id, "emp_id_snap": emp_id_snap, **summary})
    conn.commit()


def get_payroll_attendance_snapshot(conn, run_id: int, global_user_id: int):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT * FROM payroll_attendance_snapshot WHERE run_id=%s AND global_user_id=%s
        """, (run_id, global_user_id))
        row = cur.fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════
#  Holiday overtime: premium rules  (Phase 14)
# ═══════════════════════════════════════════════════════════════════════════


def get_holiday_ot_multiplier(conn, global_user_id: int):
    """Return the holiday-OT premium multiplier for an employee, or None if not eligible.

    An employee is eligible if an active rule matches them directly, or matches
    their department or section. The highest matching multiplier wins.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(r.multiplier)
            FROM payroll_holiday_ot_rules r
            JOIN global_users gu ON gu.id = %s
            WHERE r.is_active
              AND ( r.global_user_id = gu.id
                    OR (r.department_id IS NOT NULL AND r.department_id = gu.department_id)
                    OR (r.section_id   IS NOT NULL AND r.section_id   = gu.section_id) )
        """, (global_user_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def get_holiday_ot_rules(conn) -> list:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT r.*, gu.name AS emp_name, gu.global_user_id AS emp_code,
                   d.name AS dept_name, s.name AS section_name
            FROM payroll_holiday_ot_rules r
            LEFT JOIN global_users gu ON gu.id = r.global_user_id
            LEFT JOIN departments d   ON d.id  = r.department_id
            LEFT JOIN sections s      ON s.id  = r.section_id
            ORDER BY r.is_active DESC, r.id DESC
        """)
        return [dict(x) for x in cur.fetchall()]


def add_holiday_ot_rule(conn, global_user_id=None, department_id=None,
                        section_id=None, multiplier=1.5, note="") -> int:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payroll_holiday_ot_rules
                (global_user_id, department_id, section_id, multiplier, note)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (global_user_id or None, department_id or None, section_id or None,
              multiplier, note))
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def delete_holiday_ot_rule(conn, rule_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM payroll_holiday_ot_rules WHERE id=%s", (rule_id,))
    conn.commit()
