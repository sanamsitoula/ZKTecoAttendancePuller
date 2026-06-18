"""
Pull attendance records from all active ZKTeco devices for a specific BS month.
Filters device records to the month's AD date range and inserts into attendance_logs.
Devices are loaded from the database (same source as the web UI).

Usage:
  python pull_month.py 2083 2          <- Jestha 2083  (all devices)
  python pull_month.py 2083 2 --device "GateMiddle"    <- one device only
"""
import sys
import os
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import puller
from config import DeviceConfig, DEVICE_TIMEZONE, PUNCH_LABELS
from nepali_utils import bs_month_info, NEPALI_MONTHS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    _device_tz = ZoneInfo(DEVICE_TIMEZONE)
    _npt_tz    = ZoneInfo('Asia/Kathmandu')
except Exception:
    _device_tz = timezone.utc
    _npt_tz    = timezone.utc


def _to_aware_utc(dt) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_device_tz)
    return dt.astimezone(timezone.utc)


def _attendance_to_dict(record) -> dict:
    punch = int(record.punch) if record.punch is not None else None
    return {
        'uid':         record.uid,
        'user_id':     str(record.user_id),
        'timestamp':   _to_aware_utc(record.timestamp),
        'status':      int(record.status) if record.status is not None else None,
        'punch':       punch,
        'punch_label': PUNCH_LABELS.get(punch, f'Code-{punch}'),
    }


def _db_row_to_device_config(row: dict) -> DeviceConfig:
    """Convert a devices-table row to a DeviceConfig the puller can use."""
    return DeviceConfig(
        name               = row['name'],
        ip                 = row['ip_address'],
        port               = int(row['port'] or 4370),
        password           = row.get('password') or '',
        model              = row.get('model') or '',
        is_active          = bool(row.get('is_active', True)),
        connection_timeout = 10,
        force_udp          = bool(row.get('force_udp', False)),
    )


def main():
    if len(sys.argv) < 3:
        print("Usage: python pull_month.py <bs_year> <bs_month> [--device <name>]")
        print("  e.g. python pull_month.py 2083 2")
        print("  e.g. python pull_month.py 2083 2 --device GateMiddle")
        sys.exit(1)

    bs_year  = int(sys.argv[1])
    bs_month = int(sys.argv[2])

    # Optional --device filter
    only_device = None
    if '--device' in sys.argv:
        idx = sys.argv.index('--device')
        if idx + 1 < len(sys.argv):
            only_device = sys.argv[idx + 1].strip()

    mi = bs_month_info(bs_year, bs_month)
    if mi is None:
        print(f"ERROR: invalid BS year/month {bs_year}/{bs_month}")
        sys.exit(1)

    month_name = NEPALI_MONTHS[bs_month] if 1 <= bs_month <= 12 else str(bs_month)
    from_ad    = mi['first_ad']
    to_ad      = mi['last_ad']

    print(f"\n{'='*60}")
    print(f"  Pulling: {month_name} {bs_year}  ({mi['days']} days)")
    print(f"  AD range : {from_ad}  to  {to_ad}")
    if only_device:
        print(f"  Device   : {only_device}")
    print(f"{'='*60}\n")

    # ── Load devices from database ───────────────────────────────────────────
    conn = db.get_connection()
    db.init_schema(conn)
    conn.commit()

    db_devices = db.get_devices(conn)
    active_rows = [d for d in db_devices if d.get('is_active')]
    if only_device:
        active_rows = [d for d in active_rows
                       if d['name'].lower() == only_device.lower()]

    if not active_rows:
        print("No matching active devices found in the database.")
        print("Register devices via Dashboard first, or check the device name.")
        conn.close()
        sys.exit(1)

    print(f"  {len(active_rows)} device(s) to pull from:\n")
    for d in active_rows:
        print(f"    • {d['name']}  ({d['ip_address']}:{d['port']})")
    print()

    # ── Pull from each device ────────────────────────────────────────────────
    total_filtered = 0
    total_new      = 0

    for row in active_rows:
        device     = _db_row_to_device_config(row)
        device_id  = row['id']

        print(f"[{device.name}] Connecting to {device.ip}:{device.port} ...")
        result = puller.pull_device(device)

        if not result.success:
            print(f"[{device.name}] FAILED: {result.error}\n")
            continue

        raw_count = len(result.attendance)
        print(f"[{device.name}] Connected — {len(result.users)} users, "
              f"{raw_count} attendance records on device")

        # Upsert employees (needed for employee_map lookup)
        for user in result.users:
            try:
                db.upsert_employee(conn, device_id, user)
            except Exception:
                conn.rollback()
        conn.commit()

        employee_map = db.build_employee_map(conn, device_id)

        # Convert all records then filter to the month range in Nepal time
        all_records = [_attendance_to_dict(a) for a in result.attendance]
        filtered = []
        skipped_no_ts = 0
        for r in all_records:
            ts = r.get('timestamp')
            if ts is None:
                skipped_no_ts += 1
                continue
            npt_date = ts.astimezone(_npt_tz).date().isoformat()
            if from_ad <= npt_date <= to_ad:
                filtered.append(r)

        print(f"[{device.name}] {len(filtered)} records fall in "
              f"{month_name} {bs_year} "
              f"(skipped {raw_count - len(filtered) - skipped_no_ts} outside range"
              + (f", {skipped_no_ts} with null timestamp" if skipped_no_ts else '')
              + ")")

        if filtered:
            new_inserts = db.insert_attendance_batch(
                conn, device_id, filtered, employee_map
            )
            conn.commit()
            print(f"[{device.name}] {new_inserts} new rows inserted "
                  f"({len(filtered) - new_inserts} already existed)\n")
            total_new      += new_inserts
            total_filtered += len(filtered)
        else:
            print(f"[{device.name}] Nothing to insert for this month.\n")

    conn.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"{'='*60}")
    print(f"  Done!")
    print(f"  Records in {month_name} range : {total_filtered}")
    print(f"  New rows inserted            : {total_new}")
    print(f"\n  Verify data:")
    print(f"    python report_month.py {bs_year} {bs_month}")
    print(f"\n  Open in browser:")
    print(f"    /reports/hajiri?bs_year={bs_year}&bs_month={bs_month}")
    print(f"    /reports/monthly?bs_year={bs_year}&bs_month={bs_month}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
