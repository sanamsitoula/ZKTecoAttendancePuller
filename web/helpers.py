"""Shared helpers for the web UI."""
from datetime import datetime, timezone

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import DEVICE_TIMEZONE, PUNCH_LABELS, DeviceConfig
from web.flash import attach_flash_clear, read_flashes

try:
    import zoneinfo
    _device_tz = zoneinfo.ZoneInfo(DEVICE_TIMEZONE)
except Exception:
    _device_tz = timezone.utc


def render(templates: Jinja2Templates, request: Request, name: str, context: dict | None = None):
    flashes = read_flashes(request)
    ctx = {"request": request, "flashes": flashes, **(context or {})}
    response = templates.TemplateResponse(request, name, ctx)
    attach_flash_clear(response, bool(flashes))
    return response


def device_config_from_row(row: dict) -> DeviceConfig:
    return DeviceConfig(
        name=row["name"],
        ip=row["ip_address"],
        port=int(row.get("port", 4370)),
        password=row.get("password", "") or "",
        model=row.get("model", "") or "",
        is_active=bool(row.get("is_active", True)),
        connection_timeout=int(row.get("connection_timeout", 10)),
        force_udp=bool(row.get("force_udp", False)),
    )


def _to_aware_utc(dt) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_device_tz)
    return dt.astimezone(timezone.utc)


def attendance_to_dict(record) -> dict:
    punch = int(record.punch) if record.punch is not None else None
    return {
        "uid": record.uid,
        "user_id": str(record.user_id),
        "timestamp": _to_aware_utc(record.timestamp),
        "status": int(record.status) if record.status is not None else None,
        "punch": punch,
        "punch_label": PUNCH_LABELS.get(punch, f"Code-{punch}"),
    }


def action_label(action: str) -> str:
    return {
        "push_missing": "Push to device",
        "import_unknown": "Import from device",
        "pull": "Attendance pull",
        "delete": "Device deleted",
    }.get(action, action.replace("_", " ").title())
