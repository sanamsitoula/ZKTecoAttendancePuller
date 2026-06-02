"""
ZKTeco Attendance Puller — Windows Service

Commands (run PowerShell or CMD as Administrator):
  Install :  python windows_service.py install
  Start   :  python windows_service.py start
  Stop    :  python windows_service.py stop
  Restart :  python windows_service.py restart
  Remove  :  python windows_service.py remove
  Debug   :  python windows_service.py debug   (runs in foreground, Ctrl+C to stop)

Or use the helper script:
  powershell -ExecutionPolicy Bypass -File install_service.ps1
"""

import logging
import os
import sys

# Fix working directory BEFORE any local imports so relative paths resolve correctly.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import win32event
import win32service
import win32serviceutil
import servicemanager

import db
import scheduler as sched_module
from main import run_pull_cycle, setup_logging

logger = logging.getLogger(__name__)


class ZKTecoAttendanceService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ZKTecoAttendancePuller"
    _svc_display_name_ = "ZKTeco Attendance Puller"
    _svc_description_ = (
        "Pulls attendance data from ZKTeco biometric devices into PostgreSQL "
        "on a configurable daily schedule (06:20, 07:20, 09:20, 13:20, 17:10)."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._scheduler = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        logger.info("ZKTeco service stop requested.")
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        setup_logging()
        logger.info("ZKTeco Attendance Service started. Working dir: %s", BASE_DIR)

        try:
            conn = db.get_connection()
            db.init_schema(conn)
            conn.commit()
            conn.close()
            logger.info("Database schema ready.")
        except Exception as exc:
            logger.error("Database initialisation failed: %s", exc)
            self.SvcStop()
            return

        self._scheduler = sched_module.create_scheduler(run_pull_cycle)
        self._scheduler.start()
        for job in self._scheduler.get_jobs():
            logger.info("Scheduled: %-30s  next run: %s", job.name, job.next_run_time)

        # Block service thread until SvcStop signals
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
        logger.info("ZKTeco Attendance Service stopped.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ZKTecoAttendanceService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ZKTecoAttendanceService)
