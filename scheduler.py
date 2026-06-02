import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import SCHEDULE_TIMES, SCHEDULER_TIMEZONE

logger = logging.getLogger(__name__)

_pull_lock = threading.Lock()


def _make_safe_job(pull_func):
    """Wrap pull_func so overlapping invocations are skipped, not queued."""
    def wrapper():
        if not _pull_lock.acquire(blocking=False):
            logger.warning("Pull cycle already running — skipping this slot.")
            return
        try:
            pull_func()
        finally:
            _pull_lock.release()
    return wrapper


def create_scheduler(pull_job_func) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
    safe_job = _make_safe_job(pull_job_func)
    for hour, minute in SCHEDULE_TIMES:
        scheduler.add_job(
            safe_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=SCHEDULER_TIMEZONE),
            id=f"zkteco_pull_{hour:02d}{minute:02d}",
            name=f"ZKTeco Pull {hour:02d}:{minute:02d}",
            max_instances=1,
            misfire_grace_time=300,
            coalesce=True,
        )
    return scheduler


def start_scheduler(scheduler: BackgroundScheduler) -> None:
    """Start the scheduler and block until Ctrl+C (standalone mode).
    The Windows service calls scheduler.start() directly and blocks via win32event."""
    try:
        scheduler.start()
        for job in scheduler.get_jobs():
            logger.info("Scheduled: %-30s  next run: %s", job.name, job.next_run_time)
        logger.info("Scheduler running — press Ctrl+C to stop.")
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        scheduler.shutdown(wait=False)
