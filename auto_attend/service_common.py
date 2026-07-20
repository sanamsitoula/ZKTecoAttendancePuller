"""
Core service logic for auto_attend.

Shared functions: holiday check, attendance existence check,
punch insertion, user push to device.
"""

import logging
import random
import sys
import os
from datetime import datetime, date, time, timedelta, timezone

import psycopg2
import psycopg2.extras

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config import load_db_config, DeviceConfig

logger = logging.getLogger("auto_attend")


def get_connection():
    return psycopg2.connect(**load_db_config())


def is_holiday(conn, target_date: date) -> bool:
    """Check if target_date is in the holidays table."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM holidays WHERE holiday_ad = %s", (target_date,))
        return cur.fetchone() is not None


def has_attendance(conn, user_id: str, device_ids: list, target_date: date,
                   punch_type: int, tz_name: str = "Asia/Kathmandu") -> bool:
    """Check if attendance record already exists for this user/date/punch type."""
    sql = """
        SELECT 1 FROM attendance_logs
        WHERE user_id = %s
          AND device_id = ANY(%s)
          AND punch = %s
          AND (%s AT TIME ZONE %s)::date = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (user_id, device_ids, punch_type, "NOW()", tz_name, target_date))
        return cur.fetchone() is not None


def has_checkin(conn, user_id: str, device_ids: list, target_date: date,
                tz_name: str = "Asia/Kathmandu") -> bool:
    """Check if check-in (punch=0) exists for today."""
    return has_attendance(conn, user_id, device_ids, target_date, 0, tz_name)


def generate_random_time(start_str: str, end_str: str) -> time:
    """Generate a random time between start and end (HH:MM:SS format)."""
    def _parse(t_str):
        parts = t_str.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    start_sec = _parse(start_str)
    end_sec = _parse(end_str)
    random_sec = random.randint(start_sec, end_sec)
    h, rem = divmod(random_sec, 3600)
    m, s = divmod(rem, 60)
    return time(h, m, s)


def get_employee_for_device(conn, user_id: str, device_ids: list) -> dict | None:
    """Look up employee record for this user_id on any of the target devices."""
    sql = """
        SELECT e.device_id, e.uid, e.user_id, e.name, e.global_user_id,
               d.ip_address, d.port, d.password, d.model, d.force_udp
        FROM employees e
        JOIN devices d ON d.id = e.device_id
        WHERE e.user_id = %s AND e.device_id = ANY(%s)
        ORDER BY e.id
        LIMIT 1
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (user_id, device_ids))
        row = cur.fetchone()
        return dict(row) if row else None


def insert_punch(conn, device_id: int, uid: int, user_id: str, name: str,
                 target_date: date, punch_time: time, punch_type: int,
                 punch_label: str, source: str, tz_name: str = "Asia/Kathmandu") -> bool:
    """Insert a single attendance record. Returns True if inserted, False if duplicate."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_name)
    ts = datetime.combine(target_date, punch_time, tzinfo=tz)

    # Compute BS date
    bs_date = ""
    try:
        from nepali_utils import ad_to_bs
        bs_date = ad_to_bs(target_date.strftime("%Y-%m-%d")) or ""
    except Exception:
        pass

    sql = """
        INSERT INTO attendance_logs
            (device_id, uid, user_id, name, timestamp, bs_date, punch, punch_label, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (device_id, uid, timestamp) DO NOTHING
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, uid, user_id, name, ts, bs_date,
                          punch_type, punch_label, source))
        row = cur.fetchone()
        return row is not None


def push_user_to_device(conn, employee: dict) -> dict:
    """Push user to ZKTeco device via puller module. Returns result dict."""
    try:
        import puller as puller_mod
        device_cfg = DeviceConfig(
            name=employee.get("user_id", ""),
            ip=employee["ip_address"],
            port=int(employee.get("port", 4370)),
            password=str(employee.get("password", "")),
            model=employee.get("model", ""),
            is_active=True,
            connection_timeout=10,
            force_udp=bool(employee.get("force_udp", False)),
        )
        global_user = {
            "global_user_id": employee["user_id"],
            "name": employee.get("name", ""),
            "privilege": 0,
            "card": None,
        }
        return puller_mod.push_global_user_to_device(device_cfg, global_user)
    except Exception as exc:
        logger.warning("Failed to push user %s to device: %s", employee.get("user_id"), exc)
        return {"ok": False, "message": str(exc)}


def execute_punch(user_id: str, device_ids: list, punch_type: int,
                  punch_label: str, start_str: str, end_str: str,
                  source: str, tz_name: str, dry_run: bool = False,
                  use_current_time: bool = True) -> dict:
    """
    Full punch execution flow:
    1. Check holiday
    2. Check existing attendance
    3. Determine punch time (current time or random within window)
    4. Look up employee
    5. Insert punch
    6. Push to device

    Args:
        use_current_time: If True (default), use datetime.now() as punch time.
                          If False, generate random time within window (for --run-now).
    Returns result dict with status info.
    """
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.now(tz)
    today = now.date()
    result = {"date": str(today), "punch_type": punch_label, "status": "pending"}

    conn = get_connection()
    try:
        # 1. Holiday check
        if is_holiday(conn, today):
            result["status"] = "skipped"
            result["reason"] = "holiday"
            logger.info("[%s] %s skipped — holiday", punch_label, today)
            return result

        # 2. Day-of-week check (already done in scheduler, but double-check)
        dow = today.weekday()  # 0=Mon..6=Sun
        allowed_days = [0, 1, 2, 3, 4]  # Mon-Fri
        if dow not in allowed_days:
            result["status"] = "skipped"
            result["reason"] = "weekend"
            logger.info("[%s] %s skipped — weekend (day %d)", punch_label, today, dow)
            return result

        # 3. Existing attendance check
        if has_attendance(conn, user_id, device_ids, today, punch_type, tz_name):
            result["status"] = "skipped"
            result["reason"] = "already_exists"
            logger.info("[%s] %s already exists for user %s — skipping",
                        punch_label, today, user_id)
            return result

        # For checkout, also verify check-in exists
        if punch_type == 1 and not has_checkin(conn, user_id, device_ids, today, tz_name):
            result["status"] = "skipped"
            result["reason"] = "no_checkin"
            logger.info("[Check-Out] %s skipped — no check-in found for user %s", today, user_id)
            return result

        # 4. Determine punch time
        if use_current_time:
            # Use the actual current time (scheduler already slept to the right moment)
            punch_time = now.time()
        else:
            # Generate random time within window (for --run-now CLI)
            punch_time = generate_random_time(start_str, end_str)
        result["time"] = str(punch_time)

        if dry_run:
            result["status"] = "dry_run"
            result["reason"] = "test_mode"
            logger.info("[%s] DRY RUN: would insert at %s for user %s",
                        punch_label, punch_time, user_id)
            return result

        # 5. Look up employee
        employee = get_employee_for_device(conn, user_id, device_ids)
        if not employee:
            result["status"] = "error"
            result["reason"] = "employee_not_found"
            logger.error("[%s] Employee not found: user_id=%s, devices=%s",
                         punch_label, user_id, device_ids)
            return result

        # 6. Insert punch for each device the employee is on
        inserted_any = False
        for dev_id in device_ids:
            # Find employee on this specific device
            sql = """
                SELECT e.device_id, e.uid, e.user_id, e.name
                FROM employees e
                WHERE e.user_id = %s AND e.device_id = %s
                LIMIT 1
            """
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (user_id, dev_id))
                emp = cur.fetchone()
            if not emp:
                continue

            ok = insert_punch(
                conn=conn,
                device_id=dev_id,
                uid=emp["uid"],
                user_id=emp["user_id"],
                name=emp["name"],
                target_date=today,
                punch_time=punch_time,
                punch_type=punch_type,
                punch_label=punch_label,
                source=source,
                tz_name=tz_name,
            )
            if ok:
                inserted_any = True
                logger.info("[%s] Inserted for device %d at %s", punch_label, dev_id, punch_time)

        conn.commit()

        if inserted_any:
            result["status"] = "success"
        else:
            result["status"] = "skipped"
            result["reason"] = "all_duplicates"

        # 7. Push user to device (best effort, after commit)
        push_result = push_user_to_device(conn, employee)
        result["push"] = push_result

        return result

    except Exception as exc:
        conn.rollback()
        result["status"] = "error"
        result["reason"] = str(exc)
        logger.error("[%s] Error: %s", punch_label, exc)
        return result
    finally:
        conn.close()
