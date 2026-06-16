"""
Central configuration loader.

Device list   → devices.json        (edit to add/remove devices, no restart needed)
DB connection → db_config.json      (edit to change credentials, no restart needed)
              → .env fallback if db_config.json is absent

Both JSON files are reloaded on every pull cycle so changes take effect immediately.
"""

import json
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


@dataclass
class DeviceConfig:
    name: str
    ip: str
    port: int
    password: str
    model: str
    is_active: bool = True
    connection_timeout: int = 10


def load_devices() -> list[DeviceConfig]:
    """
    Read devices.json and return a list of DeviceConfig objects.
    Called before every pull cycle — add/remove/edit devices.json without restarting.
    """
    path = os.path.join(BASE_DIR, "devices.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "devices.json not found. "
            "Copy devices.json.example to devices.json and configure your devices."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("devices.json must be a JSON array of device objects.")
    return [
        DeviceConfig(
            name=d["name"],
            ip=d["ip"],
            port=int(d.get("port", 4370)),
            password=str(d.get("password", "")),
            model=d.get("model", "unknown"),
            is_active=bool(d.get("is_active", True)),
            connection_timeout=int(d.get("connection_timeout", 10)),
        )
        for d in data
    ]


def load_db_config() -> dict:
    """
    Read db_config.json (preferred) or fall back to .env environment variables.
    Called on every db.get_connection() — change db_config.json without restarting.
    """
    path = os.path.join(BASE_DIR, "db_config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {
            "host":     d.get("host", "localhost"),
            "port":     int(d.get("port", 5432)),
            "dbname":   d.get("dbname", "zkteco"),
            "user":     d.get("user", "postgres"),
            "password": str(d.get("password", "")),
        }
    # Fall back to .env
    return {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", "5432")),
        "dbname":   os.getenv("DB_NAME", "zkteco"),
        "user":     os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


# ── Scheduler ────────────────────────────────────────────────────────────────
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Asia/Kathmandu")

SCHEDULE_TIMES = [
    (6,  20),  # 06:20 NPT
    (7,  20),  # 07:20 NPT
    (9,  20),  # 09:20 NPT
    (13, 20),  # 13:20 NPT
    (17, 10),  # 17:10 NPT
]

# ── Connection ───────────────────────────────────────────────────────────────
CONNECTION_TIMEOUT = 10  # default; overridden per-device via devices.json

DEVICE_TIMEZONE = os.getenv("DEVICE_TIMEZONE", "Asia/Kathmandu")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per log file
LOG_BACKUP_COUNT = 7

# ── Reports ──────────────────────────────────────────────────────────────────
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

# ── Punch type codes ─────────────────────────────────────────────────────────
PUNCH_LABELS = {
    0:   "Check-In",
    1:   "Check-Out",
    2:   "Break-Out",
    3:   "Break-In",
    4:   "OT-In",
    5:   "OT-Out",
    255: "Check-In",
}

# ── Company / Report header ───────────────────────────────────────────────────
COMPANY_NAME    = "JANAK EDUCATION MATERIALS CENTER"
COMPANY_ADDRESS = "SANOTHIMI, BHAKTAPUR"
COMPANY_EMAIL   = "info@janakedu.org.np"
COMPANY_WEBSITE = "www.janakedu.org.np"
