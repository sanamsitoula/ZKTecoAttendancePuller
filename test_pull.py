"""
Manual test runner — runs one full pull cycle immediately.
Does NOT require a running scheduler.

Usage:
    python test_pull.py

What it tests:
  1. TCP port 4370 reachability on all devices
  2. ZKTeco SDK connection + user + attendance pull
  3. PostgreSQL connectivity and schema init
  4. Full DB write (devices, employees, attendance_logs, pull_sessions)
  5. Daily report image generation (reports/<today>.png)
"""
import logging
import socket
import sys
from datetime import datetime, timezone

# ── 1. Port reachability check ────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  ZKTeco Attendance Puller — Connection Test")
print("=" * 55)

from config import load_devices

DEVICES = load_devices()

print("\n[1] TCP Port Check (port 4370)")
for dev in DEVICES:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    result = s.connect_ex((dev.ip, dev.port))
    s.close()
    status = "✓ OPEN" if result == 0 else f"✗ CLOSED (err={result})"
    print(f"    {dev.name:8s}  {dev.ip}:{dev.port}  =>  {status}")

# ── 2. ZKTeco SDK test ───────────────────────────────────────────────────────
print("\n[2] ZKTeco SDK Connection + Data Pull")
from zk import ZK

for dev in DEVICES:
    if not dev.is_active:
        print(f"    {dev.name}: skipped (inactive)")
        continue
    password = int(dev.password) if dev.password and dev.password.isdigit() else 0
    zk = ZK(dev.ip, port=dev.port, timeout=10, password=password,
            ommit_ping=True, force_udp=False)
    conn_zk = None
    try:
        conn_zk = zk.connect()
        users = conn_zk.get_users()
        att   = conn_zk.get_attendance()
        conn_zk.enable_device()
        print(f"    {dev.name:8s}  ({dev.model})  =>  ✓  {len(users)} users, {len(att)} attendance records")
    except Exception as e:
        print(f"    {dev.name:8s}  =>  ✗  {e}")
    finally:
        if conn_zk:
            try: conn_zk.disconnect()
            except: pass

# ── 3. Full cycle via main.py ────────────────────────────────────────────────
print("\n[3] Full Pull Cycle (DB write + report generation)")
print("    (Check .env for DB credentials if this fails)")
print()

import db
from main import setup_logging, run_pull_cycle

setup_logging()

try:
    c = db.get_connection()
    db.init_schema(c)
    c.commit()
    c.close()
    print("    DB schema: ✓ ready")
except Exception as e:
    print(f"    DB connection FAILED: {e}")
    print("    → Check DB_HOST, DB_NAME, DB_USER, DB_PASSWORD in .env")
    sys.exit(1)

run_pull_cycle()

# ── 4. Summary query ─────────────────────────────────────────────────────────
print("\n[4] Verification Queries")
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
c = db.get_connection()
try:
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM devices")
        print(f"    devices            : {cur.fetchone()[0]} rows")
        cur.execute("SELECT COUNT(*) FROM employees")
        print(f"    employees          : {cur.fetchone()[0]} rows")
        cur.execute("SELECT COUNT(*) FROM attendance_logs")
        print(f"    attendance_logs    : {cur.fetchone()[0]} rows")
        cur.execute("SELECT COUNT(*) FROM pull_sessions WHERE status='success'")
        print(f"    pull_sessions (ok) : {cur.fetchone()[0]} rows")
        cur.execute("SELECT COUNT(*) FROM attendance_logs WHERE DATE(timestamp)=%s", (today,))
        print(f"    today's records    : {cur.fetchone()[0]} rows")
finally:
    c.close()

print("\n✓ All checks complete. See reports/ for generated PNG images.\n")
