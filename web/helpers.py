"""Shared helpers for the web UI."""
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import DEVICE_TIMEZONE, PUNCH_LABELS, DeviceConfig
from web.flash import attach_flash_clear, read_flashes

# Per-request actor context (set by the auth-gate middleware, read by
# db.get_connection() to thread IP/user-agent into the audit-log trigger via
# Postgres session GUCs — see db.py's fn_audit_log()). None outside a request
# (background jobs, CLI scripts), which is fine: the trigger treats a missing
# GUC as "no IP available" rather than failing.
current_request_ip: ContextVar[str | None] = ContextVar("current_request_ip", default=None)
current_user_agent: ContextVar[str | None] = ContextVar("current_user_agent", default=None)

try:
    import zoneinfo
    _device_tz = zoneinfo.ZoneInfo(DEVICE_TIMEZONE)
except Exception:
    _device_tz = timezone.utc


def _get_company_settings():
    """Get company settings from DB (cached for the request)."""
    defaults = {
        'company_name': 'ZKTeco Attendance',
        'logo_url': None,
        'address': '',
        'phone': '',
        'email': '',
        'website': '',
        'pan_number': '',
        'fiscal_year_bs': '',
    }
    try:
        from db import get_connection, get_company_settings
        conn = get_connection()
        try:
            data = get_company_settings(conn)
            if data:
                defaults.update(data)
            return defaults
        finally:
            conn.close()
    except Exception:
        return defaults


def _get_global_user_count():
    """Total employee count shown in the sidebar badge — same figure used
    on the dashboard and /reports/monthly, so the number matches everywhere."""
    try:
        from db import get_connection, get_global_user_count
        conn = get_connection()
        try:
            return get_global_user_count(conn)
        finally:
            conn.close()
    except Exception:
        return None


def render(templates: Jinja2Templates, request: Request, name: str, context: dict | None = None):
    flashes = read_flashes(request)
    session_data = {
        "display_name": request.session.get("display_name", "User"),
        "username": request.session.get("username", ""),
        "role": request.session.get("role", "viewer"),
        "user_id": request.session.get("user_id"),
    }
    company = _get_company_settings()
    nav_global_user_count = _get_global_user_count()
    ctx = {"request": request, "flashes": flashes, "session": session_data, "company": company,
           "nav_global_user_count": nav_global_user_count, **(context or {})}
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
