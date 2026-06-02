"""
ZKTeco Attendance Puller — main entry point.

Usage:
  python main.py              # start scheduler (runs 4x/day at 00:00/06:00/12:00/18:00 UTC)
  python main.py --run-now    # run one pull cycle immediately, then exit
  python main.py --report YYYY-MM-DD   # generate report images for a past date
"""
import argparse
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone

import db
import puller
import report as report_mod
import scheduler as sched_module
from config import (
    DEVICES, LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    DEVICE_TIMEZONE, PUNCH_LABELS,
)

try:
    import zoneinfo
    _device_tz = zoneinfo.ZoneInfo(DEVICE_TIMEZONE)
except Exception:
    _device_tz = timezone.utc


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "zkteco_puller.log"),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


logger = logging.getLogger(__name__)


def _to_aware_utc(dt) -> datetime:
    """Convert a datetime to UTC. Treats naive datetimes as DEVICE_TIMEZONE."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_device_tz)
    return dt.astimezone(timezone.utc)


def _attendance_to_dict(record) -> dict:
    punch = int(record.punch) if record.punch is not None else None
    return {
        "uid":         record.uid,
        "user_id":     str(record.user_id),
        "timestamp":   _to_aware_utc(record.timestamp),
        "status":      int(record.status) if record.status is not None else None,
        "punch":       punch,
        "punch_label": PUNCH_LABELS.get(punch, f"Code-{punch}"),
    }


def run_pull_cycle() -> None:
    now_utc = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Pull cycle started at %s UTC", now_utc.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0
    today = now_utc.strftime("%Y-%m-%d")

    try:
        conn = db.get_connection()
    except Exception as exc:
        logger.error("Cannot connect to database: %s", exc)
        return

    try:
        for device in DEVICES:
            if not device.is_active:
                logger.info("[%s] Skipped (inactive).", device.name)
                continue

            started_at = datetime.now(timezone.utc)
            session_id = None

            try:
                device_id = db.upsert_device(conn, device)
                conn.commit()

                session_id = db.start_pull_session(conn, device_id, started_at)
                conn.commit()

                result = puller.pull_device(device)

                if not result.success:
                    db.complete_pull_session(conn, session_id, 0, 0, "failed", result.error)
                    conn.commit()
                    logger.warning("[%s] Pull failed — recorded in pull_sessions.", device.name)
                    fail_count += 1
                    continue

                # Upsert employees
                for user in result.users:
                    try:
                        db.upsert_employee(conn, device_id, user)
                    except Exception as exc:
                        logger.warning("[%s] upsert_employee uid=%s failed: %s",
                                       device.name, user.uid, exc)
                        conn.rollback()

                employee_map = db.build_employee_map(conn, device_id)

                records = [_attendance_to_dict(a) for a in result.attendance]
                new_inserts = db.insert_attendance_batch(conn, device_id, records, employee_map)

                db.complete_pull_session(
                    conn, session_id,
                    records_pulled=len(records),
                    new_inserts=new_inserts,
                    status="success",
                )
                conn.commit()

                logger.info(
                    "[%s] ✓ %d users | %d records pulled | %d new inserts",
                    device.name, len(result.users), len(records), new_inserts,
                )
                success_count += 1

            except Exception as exc:
                conn.rollback()
                logger.error("[%s] Unexpected error: %s", device.name, exc, exc_info=True)
                if session_id is not None:
                    try:
                        db.complete_pull_session(conn, session_id, 0, 0, "failed", str(exc))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                fail_count += 1

        # ── Generate today's report images ──────────────────────────────────
        try:
            summary = db.get_daily_summary(conn, today)
            records_today = db.get_attendance_for_date(conn, today)
            if summary:
                report_mod.generate_daily_report(today, summary)
                report_mod.generate_device_timeline(today, records_today)
                logger.info("Reports generated for %s (%d employees).", today, len(summary))
            else:
                logger.info("No attendance data for %s — reports skipped.", today)
        except Exception as exc:
            logger.warning("Report generation failed: %s", exc, exc_info=True)

    finally:
        conn.close()

    total = success_count + fail_count
    logger.info(
        "=== Cycle complete. Success: %d/%d  |  Failed: %d/%d ===",
        success_count, total, fail_count, total,
    )


def generate_report_for_date(date_str: str) -> None:
    """CLI helper: pull report images for a specific past date."""
    conn = db.get_connection()
    try:
        summary = db.get_daily_summary(conn, date_str)
        records = db.get_attendance_for_date(conn, date_str)
        if not summary:
            print(f"No attendance data found for {date_str}.")
            return
        p1 = report_mod.generate_daily_report(date_str, summary)
        p2 = report_mod.generate_device_timeline(date_str, records)
        print(f"Summary report : {p1}")
        if p2:
            print(f"Timeline chart : {p2}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ZKTeco Attendance Puller")
    parser.add_argument("--run-now", action="store_true",
                        help="Run one pull cycle immediately and exit.")
    parser.add_argument("--report", metavar="YYYY-MM-DD",
                        help="Generate report images for a past date and exit.")
    args = parser.parse_args()

    setup_logging()
    logger.info("ZKTeco Attendance Puller starting up.")

    # Initialise DB schema
    try:
        conn = db.get_connection()
        db.init_schema(conn)
        conn.commit()
        conn.close()
        logger.info("Database schema ready.")
    except Exception as exc:
        logger.error("Database initialisation failed: %s", exc)
        sys.exit(1)

    if args.report:
        generate_report_for_date(args.report)
        return

    if args.run_now:
        run_pull_cycle()
        return

    # Normal mode: start scheduler
    scheduler = sched_module.create_scheduler(run_pull_cycle)
    sched_module.start_scheduler(scheduler)


if __name__ == "__main__":
    main()
