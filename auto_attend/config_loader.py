"""
Config loader for auto_attend service.

Priority: JSON file > DB table > hardcoded defaults.
JSON overrides DB when present (for quick testing).
"""

import json
import os
import sys

# Add parent dir so we can import db, config
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_ATTEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import load_db_config

import psycopg2.extras

DEFAULTS = {
    "user_id": "258",
    "device_ids": [1, 2],
    "timezone": "Asia/Kathmandu",
    "checkin_start": "08:57:00",
    "checkin_end": "08:59:49",
    "checkin_days": [1, 2, 3, 4, 5],
    "checkout_start": "17:19:00",
    "checkout_end": "17:27:00",
    "checkout_days": [1, 2, 3, 4, 5],
    "schedule_hour": 8,
    "schedule_minute": 56,
    "checkout_schedule_hour": 17,
    "checkout_schedule_minute": 18,
    "source_tag": "auto_attend",
    "service_name": "ZKTecoAutoAttend",
}


def _load_json_config() -> dict | None:
    path = os.path.join(BASE_DIR, "auto_attend_config.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_db_config() -> dict | None:
    try:
        import psycopg2
        conn = psycopg2.connect(**load_db_config())
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT user_id, device_ids, is_active,
                       checkin_start, checkin_end, checkin_days,
                       checkout_start, checkout_end, checkout_days,
                       source_tag, timezone,
                       schedule_hour, schedule_minute,
                       checkout_schedule_hour, checkout_schedule_minute
                FROM auto_attend_rules
                WHERE is_active = TRUE
                ORDER BY id
                LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        r = dict(row)
        # Convert TIME objects to strings
        for key in ("checkin_start", "checkin_end", "checkout_start", "checkout_end"):
            v = r.get(key)
            if v and hasattr(v, "strftime"):
                r[key] = v.strftime("%H:%M:%S")
        return r
    except Exception:
        return None


def load_auto_attend_config() -> dict:
    """Load config: JSON file > DB table > defaults."""
    cfg = dict(DEFAULTS)

    db_cfg = _load_db_config()
    if db_cfg:
        cfg.update({k: v for k, v in db_cfg.items() if v is not None})

    json_cfg = _load_json_config()
    if json_cfg:
        cfg.update({k: v for k, v in json_cfg.items() if v is not None})

    return cfg
