"""
ZKTeco Auto Attend Service — Main Entry Point

Standalone service that auto-punches check-in/check-out for a configured
employee on selected devices at random times within configured windows.

Supports:
  - Windows Service (via pywin32)
  - Linux systemd daemon
  - Manual CLI execution

Usage:
  python auto_attend/auto_attend.py                  # Start scheduler
  python auto_attend/auto_attend.py --run-now        # Execute both punches now
  python auto_attend/auto_attend.py --run-now --type checkin   # Only checkin
  python auto_attend/auto_attend.py --run-now --type checkout  # Only checkout
  python auto_attend/auto_attend.py --test           # Dry run, no DB writes
  python auto_attend/auto_attend.py --status         # Show config and next runs
"""

import argparse
import logging
import os
import sys
import time as _time
import threading
import random
from datetime import datetime, date, timedelta

# Fix working directory BEFORE any local imports
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_ATTEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import zoneinfo
from config import LOG_DIR
from auto_attend.config_loader import load_auto_attend_config
from auto_attend.service_common import execute_punch, get_connection

# ── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE = os.path.join(LOG_DIR, "auto_attend.log")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


logger = logging.getLogger("auto_attend")


# ── Core Punch Job ───────────────────────────────────────────────────────────

_punch_lock = threading.Lock()


def _run_punch_job(punch_type: str, dry_run: bool = False):
    """
    Execute punch job with lock to prevent overlap.

    Flow:
    1. Check holiday / day-of-week (fast, no sleep)
    2. Calculate random delay to land inside the punch window
    3. Sleep until that random time
    4. Execute the punch at the actual random time
    """
    if not _punch_lock.acquire(blocking=False):
        logger.warning("Punch job already running — skipping.")
        return

    try:
        cfg = load_auto_attend_config()
        tz_name = cfg.get("timezone", "Asia/Kathmandu")
        user_id = cfg.get("user_id", "258")
        device_ids = cfg.get("device_ids", [1, 2])
        source = cfg.get("source_tag", "auto_attend")

        tz = zoneinfo.ZoneInfo(tz_name)
        now = datetime.now(tz)
        today = now.date()
        dow = today.weekday()  # 0=Mon..6=Sun

        # ── Pre-checks (before any sleep) ──────────────────────────────────
        import auto_attend.service_common as sc

        conn = sc.get_connection()
        try:
            # Holiday check
            if sc.is_holiday(conn, today):
                logger.info("[%s] %s skipped — holiday", punch_type.upper(), today)
                return

            # Day-of-week check
            if punch_type in ("checkin", "both"):
                ci_days = cfg.get("checkin_days", [1, 2, 3, 4, 5])
                if dow not in ci_days:
                    logger.info("Check-In skipped — day %d not in allowed days %s", dow, ci_days)
                    return

            if punch_type in ("checkout", "both"):
                co_days = cfg.get("checkout_days", [1, 2, 3, 4, 5])
                if dow not in co_days:
                    logger.info("Check-Out skipped — day %d not in allowed days %s", dow, co_days)
                    return

            # Existing attendance check (before sleeping)
            if punch_type in ("checkin", "both"):
                ci_start = cfg.get("checkin_start", "08:57:00")
                ci_end = cfg.get("checkin_end", "08:59:49")
                if sc.has_attendance(conn, user_id, device_ids, today, 0, tz_name):
                    logger.info("Check-In already exists for %s — skipping", today)
                else:
                    delay = _calc_delay(now, ci_start, ci_end, tz_name)
                    logger.info("Check-In: sleeping %.1f seconds (until ~%s)", delay, ci_end)
                    if not dry_run and delay > 0:
                        _time.sleep(delay)
                    result = execute_punch(
                        user_id=user_id, device_ids=device_ids,
                        punch_type=0, punch_label="Check-In",
                        start_str=ci_start, end_str=ci_end,
                        source=source, tz_name=tz_name, dry_run=dry_run,
                    )
                    logger.info("Check-In result: %s", result)

            if punch_type in ("checkout", "both"):
                co_start = cfg.get("checkout_start", "17:19:00")
                co_end = cfg.get("checkout_end", "17:27:00")

                # Re-check at checkout time: holiday may have been added, or checkin may be missing
                if sc.is_holiday(conn, today):
                    logger.info("Check-Out skipped — holiday (re-check)")
                    return
                if not sc.has_checkin(conn, user_id, device_ids, today, tz_name):
                    logger.info("Check-Out skipped — no check-in found for %s", today)
                    return
                if sc.has_attendance(conn, user_id, device_ids, today, 1, tz_name):
                    logger.info("Check-Out already exists for %s — skipping", today)
                else:
                    delay = _calc_delay(now, co_start, co_end, tz_name)
                    logger.info("Check-Out: sleeping %.1f seconds (until ~%s)", delay, co_end)
                    if not dry_run and delay > 0:
                        _time.sleep(delay)
                    result = execute_punch(
                        user_id=user_id, device_ids=device_ids,
                        punch_type=1, punch_label="Check-Out",
                        start_str=co_start, end_str=co_end,
                        source=source, tz_name=tz_name, dry_run=dry_run,
                    )
                    logger.info("Check-Out result: %s", result)
        finally:
            conn.close()

    finally:
        _punch_lock.release()


def _calc_delay(now: datetime, window_start: str, window_end: str, tz_name: str) -> float:
    """
    Calculate sleep delay so the punch lands at a random time between
    window_start and window_end today.

    Returns seconds to sleep (0 if window already passed or in the past).
    """
    def _parse_sec(t_str):
        parts = t_str.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    start_sec = _parse_sec(window_start)
    end_sec = _parse_sec(window_end)

    now_sec = now.hour * 3600 + now.minute * 60 + now.second

    # If we're already past the window, execute immediately
    if now_sec >= end_sec:
        return 0

    # If we're before the window, sleep until a random point inside it
    if now_sec < start_sec:
        target_sec = random.randint(start_sec, end_sec)
    else:
        # We're inside the window — pick a random point from now to end
        target_sec = random.randint(max(now_sec + 1, start_sec), end_sec)

    delay = target_sec - now_sec
    return max(0, delay)


# ── Scheduler ────────────────────────────────────────────────────────────────

def start_scheduler(dry_run: bool = False):
    """Start APScheduler with checkin and checkout jobs."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    cfg = load_auto_attend_config()
    tz_name = cfg.get("timezone", "Asia/Kathmandu")
    tz = zoneinfo.ZoneInfo(tz_name)

    scheduler = BlockingScheduler(timezone=tz)

    ci_hour = cfg.get("schedule_hour", 8)
    ci_minute = cfg.get("schedule_minute", 56)
    co_hour = cfg.get("checkout_schedule_hour", 17)
    co_minute = cfg.get("checkout_schedule_minute", 18)

    def _safe_checkin():
        if not _punch_lock.acquire(blocking=False):
            return
        try:
            _run_punch_job("checkin", dry_run)
        finally:
            _punch_lock.release()

    def _safe_checkout():
        if not _punch_lock.acquire(blocking=False):
            return
        try:
            _run_punch_job("checkout", dry_run)
        finally:
            _punch_lock.release()

    scheduler.add_job(
        _safe_checkin,
        CronTrigger(hour=ci_hour, minute=ci_minute, day_of_week="mon-fri",
                    timezone=tz),
        id="auto_checkin",
        name="Auto Check-In",
        max_instances=1,
        misfire_grace_time=600,
        coalesce=True,
    )

    scheduler.add_job(
        _safe_checkout,
        CronTrigger(hour=co_hour, minute=co_minute, day_of_week="mon-fri",
                    timezone=tz),
        id="auto_checkout",
        name="Auto Check-Out",
        max_instances=1,
        misfire_grace_time=600,
        coalesce=True,
    )

    logger.info("Scheduler started.")
    for job in scheduler.get_jobs():
        logger.info("  Job: %-25s  next: %s", job.name, job.next_run_time)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down.")
        scheduler.shutdown(wait=False)


# ── CLI ──────────────────────────────────────────────────────────────────────

def show_status():
    cfg = load_auto_attend_config()
    print("=" * 55)
    print("  ZKTeco Auto Attend — Configuration")
    print("=" * 55)
    print(f"  User ID        : {cfg.get('user_id')}")
    print(f"  Device IDs     : {cfg.get('device_ids')}")
    print(f"  Timezone       : {cfg.get('timezone')}")
    print(f"  Source Tag     : {cfg.get('source_tag')}")
    print()
    print(f"  Check-In Window: {cfg.get('checkin_start')} — {cfg.get('checkin_end')}")
    print(f"  Check-In Days  : {cfg.get('checkin_days')}")
    print(f"  Schedule       : {cfg.get('schedule_hour'):02d}:{cfg.get('schedule_minute'):02d}")
    print()
    print(f"  Check-Out Win  : {cfg.get('checkout_start')} — {cfg.get('checkout_end')}")
    print(f"  Check-Out Days : {cfg.get('checkout_days')}")
    print(f"  Schedule       : {cfg.get('checkout_schedule_hour'):02d}:{cfg.get('checkout_schedule_minute'):02d}")
    print("=" * 55)

    # Check DB state
    try:
        conn = get_connection()
        today = datetime.now(zoneinfo.ZoneInfo(cfg.get("timezone", "Asia/Kathmandu"))).date()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT punch, COUNT(*) FROM attendance_logs
                WHERE user_id = %s
                  AND device_id = ANY(%s)
                  AND (%s AT TIME ZONE 'Asia/Kathmandu')::date = %s
                GROUP BY punch
            """, (cfg["user_id"], cfg["device_ids"], "NOW()", today))
            rows = cur.fetchall()
        conn.close()
        print(f"\n  Today ({today}) attendance for user {cfg['user_id']}:")
        if rows:
            for punch, cnt in rows:
                label = "Check-In" if punch == 0 else "Check-Out" if punch == 1 else f"Punch-{punch}"
                print(f"    {label}: {cnt} record(s)")
        else:
            print("    No records yet.")
    except Exception as exc:
        print(f"\n  DB check failed: {exc}")


def main():
    parser = argparse.ArgumentParser(
        description="ZKTeco Auto Attend Service"
    )
    parser.add_argument("--run-now", action="store_true",
                        help="Execute punch immediately (manual mode)")
    parser.add_argument("--type", choices=["checkin", "checkout", "both"],
                        default="both", help="Which punch to run (default: both)")
    parser.add_argument("--test", action="store_true",
                        help="Dry run — check logic without DB writes")
    parser.add_argument("--status", action="store_true",
                        help="Show configuration and current status")
    parser.add_argument("--service", action="store_true",
                        help="Run as Windows Service (internal use)")

    args = parser.parse_args()

    setup_logging()

    if args.status:
        show_status()
        return

    if args.run_now:
        logger.info("Manual run: type=%s, test=%s", args.type, args.test)
        _run_punch_job(args.type, dry_run=args.test)
        logger.info("Manual run complete.")
        return

    if args.service:
        # Windows Service mode — will be handled by service_windows.py
        logger.info("Starting in service mode...")
        start_scheduler(dry_run=False)
        return

    # Default: start scheduler
    logger.info("Starting auto_attend scheduler...")
    show_status()
    start_scheduler(dry_run=args.test)


if __name__ == "__main__":
    main()
