import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from config import DB_CONFIG, DeviceConfig

logger = logging.getLogger(__name__)

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
"""


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    logger.info("Database schema initialized.")


def upsert_device(conn, device: DeviceConfig) -> int:
    sql = """
        INSERT INTO devices (name, ip_address, port, password, model, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            ip_address = EXCLUDED.ip_address,
            port       = EXCLUDED.port,
            model      = EXCLUDED.model,
            is_active  = EXCLUDED.is_active
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device.name, device.ip, device.port,
            device.password, device.model, device.is_active,
        ))
        return cur.fetchone()[0]


def upsert_employee(conn, device_id: int, user) -> int:
    sql = """
        INSERT INTO employees (device_id, uid, user_id, name, privilege, card)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (device_id, uid) DO UPDATE SET
            user_id    = EXCLUDED.user_id,
            name       = EXCLUDED.name,
            privilege  = EXCLUDED.privilege,
            card       = EXCLUDED.card,
            updated_at = NOW()
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            device_id,
            user.uid,
            str(user.user_id),
            user.name or "",
            int(user.privilege) if user.privilege is not None else 0,
            str(user.card) if user.card else None,
        ))
        return cur.fetchone()[0]


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
        ))

    sql = """
        WITH ins AS (
            INSERT INTO attendance_logs
                (device_id, employee_id, uid, user_id, name,
                 timestamp, status, punch, punch_label)
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
        INSERT INTO pull_sessions (device_id, started_at, status)
        VALUES (%s, %s, 'running')
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, started_at))
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
            records_pulled = %s,
            new_inserts    = %s,
            status         = %s,
            error_message  = %s
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (records_pulled, new_inserts, status, error_message, session_id))


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
        LEFT JOIN employees e ON al.employee_id = e.id
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
        LEFT JOIN employees e ON al.employee_id = e.id
        JOIN devices d ON al.device_id = d.id
        WHERE DATE(al.timestamp) = %s
        GROUP BY al.user_id, COALESCE(al.name, e.name, 'Unknown')
        ORDER BY first_in ASC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (date_str,))
        return [dict(row) for row in cur.fetchall()]
