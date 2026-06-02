import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DeviceConfig:
    name: str
    ip: str
    port: int
    password: str
    model: str
    is_active: bool = True


# ── Device Registry ──────────────────────────────────────────────────────────
# Add or remove DeviceConfig entries here to manage your ZKTeco devices.
# Passwords are read from .env (leave empty string if device has no password).
DEVICES: list[DeviceConfig] = [
    DeviceConfig(
        name="Attn1",
        ip="10.10.10.18",
        port=4370,
        password=os.getenv("DEVICE_PASSWORD_ATTN1", ""),
        model="MB2000",
    ),
    DeviceConfig(
        name="attn2",
        ip="10.10.10.11",
        port=4370,
        password=os.getenv("DEVICE_PASSWORD_ATTN2", ""),
        model="iFace302",
    ),
    DeviceConfig(
        name="atn3",
        ip="10.10.10.12",
        port=4370,
        password=os.getenv("DEVICE_PASSWORD_ATN3", ""),
        model="unknown",
    ),
]

# ── Database ─────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "dbname":   os.getenv("DB_NAME", "zkteco"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# ── Scheduler ────────────────────────────────────────────────────────────────
# Pull runs at these UTC hours every day (4 times/day)
SCHEDULE_HOURS = [0, 6, 12, 18]

# ── Connection ───────────────────────────────────────────────────────────────
CONNECTION_TIMEOUT = 10  # seconds to wait for device TCP handshake

# Timezone the physical devices are set to (for naive-datetime localisation).
# Examples: "Asia/Kathmandu", "Asia/Dhaka", "Asia/Karachi", "UTC"
DEVICE_TIMEZONE = os.getenv("DEVICE_TIMEZONE", "UTC")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = "logs"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per log file
LOG_BACKUP_COUNT = 7

# ── Reports ──────────────────────────────────────────────────────────────────
REPORTS_DIR = "reports"  # daily attendance image reports saved here

# ZKTeco punch-type codes → human-readable label
PUNCH_LABELS = {
    0:   "Check-In",
    1:   "Check-Out",
    2:   "Break-Out",
    3:   "Break-In",
    4:   "OT-In",
    5:   "OT-Out",
    255: "Check-In",   # some firmware uses 255 for check-in
}
