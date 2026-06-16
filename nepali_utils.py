"""Nepali (Bikram Sambat) calendar utilities.

Converts AD (Gregorian) dates/datetimes to BS (Bikram Sambat) for display.
Requires: pip install nepali-datetime

All functions degrade gracefully — if the library is missing or conversion
fails they return empty strings so the UI continues to work.
"""
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

NEPALI_MONTHS = [
    '', 'Baisakh', 'Jestha', 'Ashar', 'Shrawan', 'Bhadra', 'Ashwin',
    'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra',
]

_NPT_TZ = None
try:
    import zoneinfo
    _NPT_TZ = zoneinfo.ZoneInfo('Asia/Kathmandu')
except Exception:
    pass


def _to_npt_date(dt) -> date | None:
    """Normalise any date/datetime/str to a date in NPT (UTC+5:45)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            return date.fromisoformat(dt[:10])
        except Exception:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo and _NPT_TZ:
            dt = dt.astimezone(_NPT_TZ)
        return dt.date()
    if isinstance(dt, date):
        return dt
    return None


def _to_npt_datetime(dt) -> datetime | None:
    """Convert an aware datetime to NPT; return None on failure."""
    if not isinstance(dt, datetime):
        return None
    try:
        if dt.tzinfo and _NPT_TZ:
            return dt.astimezone(_NPT_TZ)
    except Exception:
        pass
    return dt


def ad_to_bs_tuple(dt) -> tuple[int, int, int] | None:
    """Return (year, month, day) in BS, or None on failure."""
    d = _to_npt_date(dt)
    if d is None:
        return None
    try:
        import nepali_datetime  # type: ignore
        nd = nepali_datetime.date.from_datetime_date(d)
        return (nd.year, nd.month, nd.day)
    except Exception:
        return None


def bs_date_str(dt, fmt: str = 'long') -> str:
    """Format AD date/datetime as a BS date string.

    fmt='short'  →  '2083-03-02'
    fmt='long'   →  'Ashar 02, 2083'
    fmt='full'   →  '2083 Ashar 02'
    """
    bs = ad_to_bs_tuple(dt)
    if bs is None:
        return ''
    y, m, d = bs
    mn = NEPALI_MONTHS[m] if 1 <= m <= 12 else str(m)
    if fmt == 'short':
        return f'{y}-{m:02d}-{d:02d}'
    if fmt == 'long':
        return f'{mn} {d:02d}, {y}'
    return f'{y} {mn} {d:02d}'


def bs_datetime_str(dt) -> str:
    """Return 'Ashar 02, 2083 20:15' (NPT time, BS date)."""
    if dt is None:
        return ''
    date_str = bs_date_str(dt, fmt='long')
    if not date_str:
        return ''
    npt_dt = _to_npt_datetime(dt) if isinstance(dt, datetime) else None
    if npt_dt:
        return f"{date_str} {npt_dt.strftime('%H:%M')}"
    return date_str


# ── Jinja2 filters ──────────────────────────────────────────────────────────

def jinja_bs_date(dt, fmt: str = 'long') -> str:
    """{{ timestamp | bs_date }}  →  'Ashar 02, 2083 BS'"""
    result = bs_date_str(dt, fmt)
    return f'{result} BS' if result else ''


def jinja_bs_datetime(dt) -> str:
    """{{ timestamp | bs_datetime }}  →  'Ashar 02, 2083 20:15 BS'"""
    result = bs_datetime_str(dt)
    return f'{result} BS' if result else ''


def jinja_fmt_dt(dt) -> str:
    """{{ timestamp | fmt_dt }}  →  '2026-06-16 20:15' (NPT)"""
    if dt is None:
        return '—'
    try:
        npt = _to_npt_datetime(dt) if isinstance(dt, datetime) else None
        if npt:
            return npt.strftime('%Y-%m-%d %H:%M')
        if isinstance(dt, (date, datetime)):
            return dt.strftime('%Y-%m-%d %H:%M') if isinstance(dt, datetime) else str(dt)
        return str(dt)
    except Exception:
        return str(dt)


def register_filters(templates) -> None:
    """Register all BS Jinja2 filters on a FastAPI Jinja2Templates instance."""
    templates.env.filters['bs_date'] = jinja_bs_date
    templates.env.filters['bs_datetime'] = jinja_bs_datetime
    templates.env.filters['fmt_dt'] = jinja_fmt_dt
