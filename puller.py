import logging
from dataclasses import dataclass, field

from zk import ZK

from config import DeviceConfig

logger = logging.getLogger(__name__)


@dataclass
class PullResult:
    device_name: str
    users: list = field(default_factory=list)
    attendance: list = field(default_factory=list)
    success: bool = True
    error: str | None = None


def pull_device(device: DeviceConfig) -> PullResult:
    """
    Connect to a ZKTeco device, pull all users and attendance records.
    Disables the device during the pull to prevent torn reads.
    Always re-enables and disconnects in the finally block.
    Timeout is read from device.connection_timeout (set per device in devices.json).
    """
    logger.info("[%s] Connecting to %s:%d (timeout=%ds) ...",
                device.name, device.ip, device.port, device.connection_timeout)

    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(
        device.ip,
        port=device.port,
        timeout=device.connection_timeout,
        password=password,
        ommit_ping=True,   # skip ICMP — often blocked on device VLANs
        force_udp=False,
    )
    conn = None
    try:
        conn = zk.connect()
        conn.disable_device()  # pause swipe recording during pull
        logger.info("[%s] Connected. Pulling users and attendance.", device.name)

        users = conn.get_users()
        attendance = conn.get_attendance()

        conn.enable_device()
        logger.info(
            "[%s] Pull complete: %d users, %d attendance records.",
            device.name, len(users), len(attendance),
        )
        return PullResult(device_name=device.name, users=users, attendance=attendance)

    except Exception as exc:
        logger.error("[%s] Pull failed: %s", device.name, exc)
        return PullResult(device_name=device.name, success=False, error=str(exc))

    finally:
        if conn is not None:
            try:
                conn.enable_device()
            except Exception:
                pass
            try:
                conn.disconnect()
            except Exception:
                pass
