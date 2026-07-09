"""
ZKTeco Auto Attend — Windows Service

Commands (run PowerShell or CMD as Administrator):
  Install :  python auto_attend/service_windows.py install
  Start   :  python auto_attend/service_windows.py start
  Stop    :  python auto_attend/service_windows.py stop
  Restart :  python auto_attend/service_windows.py restart
  Remove  :  python auto_attend/service_windows.py remove
  Debug   :  python auto_attend/service_windows.py debug   (foreground)

Or use the helper script:
  powershell -ExecutionPolicy Bypass -File auto_attend/install_service.ps1
"""

import logging
import os
import sys

# Fix working directory BEFORE any local imports
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import win32event
import win32service
import win32serviceutil
import servicemanager

from config import LOG_DIR

logger = logging.getLogger("auto_attend")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(LOG_DIR, "auto_attend.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


class ZKTecoAutoAttendService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ZKTecoAutoAttend"
    _svc_display_name_ = "ZKTeco Auto Attend"
    _svc_description_ = (
        "Auto check-in/check-out for configured employees on ZKTeco devices. "
        "Runs daily at scheduled times with random delay within configured windows."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._scheduler = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        logger.info("Auto Attend service stop requested.")
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        _setup_logging()
        logger.info("Auto Attend Service started. Working dir: %s", BASE_DIR)

        # Init DB schema
        try:
            import db
            conn = db.get_connection()
            db.init_schema(conn)
            conn.commit()
            conn.close()
            logger.info("Database schema ready.")
        except Exception as exc:
            logger.error("Database initialisation failed: %s", exc)
            self.SvcStop()
            return

        # Start scheduler
        from auto_attend.auto_attend import start_scheduler as _start_sched
        self._scheduler_thread = __import__("threading").Thread(
            target=self._run_scheduler, daemon=True
        )
        self._scheduler_thread.start()

        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
        logger.info("Auto Attend Service stopped.")

    def _run_scheduler(self):
        from auto_attend.auto_attend import start_scheduler
        try:
            start_scheduler(dry_run=False)
        except Exception as exc:
            logger.error("Scheduler error: %s", exc)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ZKTecoAutoAttendService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ZKTecoAutoAttendService)
