import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def create_scheduler(pull_job_func) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        pull_job_func,
        trigger=CronTrigger(hour="0,6,12,18", minute=0),
        id="zkteco_pull",
        name="ZKTeco Attendance Pull",
        max_instances=1,
        misfire_grace_time=300,
        coalesce=True,
    )
    return scheduler


def start_scheduler(scheduler: BlockingScheduler) -> None:
    try:
        jobs = scheduler.get_jobs()
        if jobs:
            logger.info("Scheduler started. Next run: %s", jobs[0].next_run_time)
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        scheduler.shutdown(wait=False)
