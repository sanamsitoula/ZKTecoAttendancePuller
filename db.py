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
"""


def get_connection():
    return psycopg2.connect(**load_db_config())


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
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
) -> None:
    sql = """
        UPDATE pull_sessions
        SET completed_at   = NOW(),
            completed_bs   = %s,
            records_pulled = %s,
            new_inserts    = %s,
            status         = %s,
            error_message  = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (_today_bs(), records_pulled, new_inserts, status, error_message, session_id))


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
    sql = "SELECT id, name, ip_address, port, password, model, is_active, created_at FROM devices ORDER BY name"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def get_device(conn, device_id: int):
    sql = "SELECT id, name, ip_address, port, password, model, is_active FROM devices WHERE id = %s"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (device_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_device(conn, device: dict) -> int:
    sql = """
        INSERT INTO devices (name, ip_address, port, password, model, is_active, created_bs)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            _today_bs(),
        ))
        return cur.fetchone()[0]


def update_device(conn, device_id: int, device: dict) -> None:
    sql = """
        UPDATE devices SET
            name = %s,
            ip_address = %s,
            port = %s,
            password = %s,
            model = %s,
            is_active = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.get("name"), device.get("ip_address"), device.get("port", 4370),
            device.get("password", ""), device.get("model", ""), bool(device.get("is_active", True)),
            device_id,
        ))


def delete_device(conn, device_id: int) -> None:
    sql = "DELETE FROM devices WHERE id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (device_id,))


def list_global_users(conn):
    sql = "SELECT id, global_user_id, name, privilege, card, created_at, updated_at FROM global_users ORDER BY name"
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]


def create_global_user(conn, global_user_id: str, name: str, privilege: int = 0, card: str | None = None) -> int:
    sql = """
        INSERT INTO global_users (global_user_id, name, privilege, card, created_bs, updated_bs)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        today = _today_bs()
        cur.execute(sql, (global_user_id, name, int(privilege), card, today, today))
        return cur.fetchone()[0]


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
