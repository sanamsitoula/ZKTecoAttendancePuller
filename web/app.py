from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import csv
import io
import os
import json
import socket
import threading as _threading
import importlib
from datetime import date, datetime, timezone

from db import get_connection, create_device, update_device, delete_device, get_devices, get_device
from db import list_global_users, create_global_user, delete_global_user, find_global_user_by_global_id
from db import upsert_employee
from db import (get_employee_with_device, get_employees_with_device,
                delete_employee_record, bulk_delete_employee_records)
import db as db_mod
import puller as puller_mod
import re
import time as _time
import concurrent.futures
import psycopg2.extras
import config as _cfg_mod
from config import SCHEDULE_TIMES, SCHEDULER_TIMEZONE, load_db_config
from config import COMPANY_NAME, COMPANY_ADDRESS, COMPANY_EMAIL, COMPANY_WEBSITE
from urllib.parse import urlencode
from web.flash import redirect_with_flash
from web.helpers import render, device_config_from_row, attendance_to_dict, action_label
import nepali_utils

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# ---- In-process scheduler -----------------------------------------------
_web_scheduler: BackgroundScheduler | None = None
_pull_lock = _threading.Lock()


def _scheduler_jobs_info() -> list:
    if _web_scheduler is None:
        return []
    out = []
    for j in _web_scheduler.get_jobs():
        nrt = j.next_run_time
        out.append({
            "name": j.name,
            "next_run": nrt.strftime("%Y-%m-%d %H:%M %Z") if nrt else "—",
        })
    return out


def _restart_web_scheduler():
    global _web_scheduler
    if _web_scheduler and _web_scheduler.running:
        _web_scheduler.shutdown(wait=False)
    importlib.reload(_cfg_mod)
    times = _cfg_mod.SCHEDULE_TIMES
    tz    = _cfg_mod.SCHEDULER_TIMEZONE
    from main import run_pull_cycle

    def _safe_pull():
        if not _pull_lock.acquire(blocking=False):
            return
        try:
            run_pull_cycle()
        finally:
            _pull_lock.release()

    sched = BackgroundScheduler(timezone=tz)
    for hour, minute in times:
        sched.add_job(
            _safe_pull,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"zkteco_pull_{hour:02d}{minute:02d}",
            name=f"ZKTeco Pull {hour:02d}:{minute:02d}",
            max_instances=1,
            misfire_grace_time=300,
            coalesce=True,
        )
    sched.start()
    _web_scheduler = sched


@asynccontextmanager
async def _app_lifespan(fastapi_app: FastAPI):
    # Ensure schema is up to date (idempotent) and backfill missing BS dates
    try:
        _conn = get_connection()
        db_mod.init_schema(_conn)
        _conn.commit()
        filled = db_mod.backfill_bs_dates(_conn)
        if filled:
            import logging as _lg
            _lg.getLogger(__name__).info("Backfilled BS dates for %d attendance rows.", filled)
        _conn.close()
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).warning("Startup DB init/backfill failed: %s", _e)
    _restart_web_scheduler()
    yield
    if _web_scheduler and _web_scheduler.running:
        _web_scheduler.shutdown(wait=False)


app = FastAPI(title="ZKTeco Puller — Web UI", lifespan=_app_lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
nepali_utils.register_filters(templates)


def _fmt_schedule() -> list[str]:
    try:
        importlib.reload(_cfg_mod)
        return [f"{hour:02d}:{minute:02d}" for hour, minute in _cfg_mod.SCHEDULE_TIMES]
    except Exception:
        return [f"{hour:02d}:{minute:02d}" for hour, minute in SCHEDULE_TIMES]


def _db_status() -> dict:
    cfg = load_db_config()
    try:
        conn = get_connection()
        conn.close()
        return {
            "ok": True,
            "label": "Connected",
            "detail": f"{cfg.get('host')}:{cfg.get('port')} / {cfg.get('dbname')}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "label": "Disconnected",
            "detail": str(exc),
        }


def _ping_device(ip: str, port: int, timeout: int = 3) -> dict:
    """TCP connect check. Returns {ok, ms, checked_at (UTC datetime)}."""
    start = _time.monotonic()
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        sock.close()
        ms = int((_time.monotonic() - start) * 1000)
        return {"ok": True, "ms": ms, "checked_at": datetime.now(timezone.utc)}
    except Exception:
        return {"ok": False, "ms": None, "checked_at": datetime.now(timezone.utc)}


def _ping_devices_parallel(devices: list) -> None:
    """Ping all devices concurrently; adds 'ping' key to each device dict in-place."""
    def _ping_one(d):
        d["ping"] = _ping_device(d["ip_address"], int(d.get("port", 4370)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(_ping_one, devices))


def _dashboard_data(conn) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT COUNT(*) FROM devices")
        device_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM devices WHERE is_active")
        active_device_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM employees")
        employee_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM attendance_logs WHERE DATE(timestamp) = CURRENT_DATE")
        punches_today = cur.fetchone()[0]
        cur.execute("""
            SELECT ps.*, d.name AS device_name
            FROM pull_sessions ps
            JOIN devices d ON ps.device_id = d.id
            ORDER BY ps.started_at DESC
            LIMIT 1
        """)
        latest = cur.fetchone()
        cur.execute("""
            SELECT d.id, d.name, d.ip_address, d.port, d.model, d.is_active,
                   ps.status AS last_status, ps.started_at AS last_started_at,
                   ps.completed_at AS last_completed_at, ps.records_pulled,
                   ps.new_inserts, ps.error_message
            FROM devices d
            LEFT JOIN LATERAL (
                SELECT *
                FROM pull_sessions ps
                WHERE ps.device_id = d.id
                ORDER BY ps.started_at DESC
                LIMIT 1
            ) ps ON TRUE
            ORDER BY d.name
        """)
        devices = [dict(row) for row in cur.fetchall()]
    _ping_devices_parallel(devices)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT al.timestamp, COALESCE(al.name, e.name, 'Unknown') AS name,
                   al.user_id, al.punch_label, d.name AS device_name
            FROM attendance_logs al
            LEFT JOIN LATERAL (
                SELECT name FROM employees
                WHERE device_id = al.device_id AND user_id = al.user_id
                ORDER BY id LIMIT 1
            ) e ON TRUE
            JOIN devices d ON al.device_id = d.id
            ORDER BY al.timestamp DESC
            LIMIT 12
        """)
        recent_punches = [dict(row) for row in cur.fetchall()]
    return {
        "device_count": device_count,
        "active_device_count": active_device_count,
        "employee_count": employee_count,
        "punches_today": punches_today,
        "latest_session": dict(latest) if latest else None,
        "devices": devices,
        "recent_punches": recent_punches,
    }


def write_devices_json_from_db(conn):
    devices = get_devices(conn)
    out = []
    for d in devices:
        out.append({
            "name": d["name"],
            "ip": d["ip_address"],
            "port": int(d.get("port", 4370)),
            "password": d.get("password", ""),
            "model": d.get("model", "unknown"),
            "is_active": bool(d.get("is_active", True)),
            "connection_timeout": 10,
        })
    path = os.path.join(BASE_DIR, "devices.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


@app.get("/")
def index(request: Request):
    db_status = _db_status()
    if not db_status["ok"]:
        return render(templates, request, "devices.html", {
            "db_status": db_status,
            "schedule_times": _fmt_schedule(),
            "scheduler_timezone": SCHEDULER_TIMEZONE,
            "stats": None,
            "devices": [],
        })
    conn = get_connection()
    try:
        stats = _dashboard_data(conn)
        return render(templates, request, "devices.html", {
            "db_status": db_status,
            "schedule_times": _fmt_schedule(),
            "scheduler_timezone": SCHEDULER_TIMEZONE,
            "stats": stats,
            "devices": stats["devices"],
        })
    finally:
        conn.close()


@app.get("/devices/add")
def device_add_form(request: Request):
    return render(templates, request, "device_form.html", {"device": None})


@app.post("/devices/add")
def device_add(request: Request,
               name: str = Form(...),
               ip: str = Form(...),
               port: int = Form(4370),
               password: str = Form(""),
               model: str = Form(""),
               is_active: str = Form("on")):
    conn = get_connection()
    try:
        device = {
            "name": name,
            "ip_address": ip,
            "port": int(port),
            "password": password,
            "model": model,
            "is_active": True if is_active == "on" else False,
        }
        create_device(conn, device)
        conn.commit()
        write_devices_json_from_db(conn)
        return redirect_with_flash("/", "success", f'Device "{name}" was added.')
    finally:
        conn.close()


@app.get("/devices/{device_id}/edit")
def device_edit_form(request: Request, device_id: int):
    conn = get_connection()
    try:
        device = get_device(conn, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        return render(templates, request, "device_form.html", {"device": device})
    finally:
        conn.close()


@app.post("/devices/{device_id}/edit")
def device_edit(request: Request, device_id: int,
                name: str = Form(...), ip: str = Form(...), port: int = Form(4370),
                password: str = Form(""), model: str = Form(""), is_active: str = Form("on")):
    conn = get_connection()
    try:
        device = {
            "name": name,
            "ip_address": ip,
            "port": int(port),
            "password": password,
            "model": model,
            "is_active": True if is_active == "on" else False,
        }
        update_device(conn, device_id, device)
        conn.commit()
        write_devices_json_from_db(conn)
        return redirect_with_flash("/", "success", f'Device "{name}" was updated.')
    finally:
        conn.close()


@app.post("/devices/{device_id}/delete")
def device_delete(request: Request, device_id: int):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
        snapshot = {
            "device_name": d["name"],
            "ip_address": d["ip_address"],
            "port": d.get("port", 4370),
        }
        delete_device(conn, device_id)
        conn.commit()
        write_devices_json_from_db(conn)
        return render(templates, request, "delete_result.html", snapshot)
    finally:
        conn.close()


@app.post("/devices/{device_id}/test")
def device_test(device_id: int):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
        ip = d["ip_address"]
        port = int(d.get("port", 4370))
        try:
            sock = socket.create_connection((ip, port), timeout=5)
            sock.close()
            return {"ok": True, "message": f"Connected to {ip}:{port}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


# ---- Users ---------------------------------------------------------------


_EMP_SORT_COLS = {
    'name':       'e.name',
    'user_id':    'e.user_id',
    'uid':        'e.uid',
    'created_at': 'e.created_at',
    'device':     'd.name',
}


def _int_param(v) -> int | None:
    """Convert a query-string value to int, returning None for empty / None inputs."""
    if v is None:
        return None
    s = str(v).strip()
    return int(s) if s else None


def _build_emp_query(device_id, search, date_str, sort_by, sort_dir, limit=1000):
    col   = _EMP_SORT_COLS.get(sort_by, 'e.name')
    direc = 'DESC' if sort_dir == 'desc' else 'ASC'
    where = []
    params = []
    if device_id:
        where.append("d.id = %s")
        params.append(device_id)
    if search:
        where.append("(e.name ILIKE %s OR e.user_id ILIKE %s)")
        params += [f'%{search}%', f'%{search}%']
    if date_str:
        where.append("DATE(e.created_at) = %s")
        params.append(date_str)
    w = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT e.id, e.uid, e.user_id, e.name, e.privilege, e.card,
               e.global_user_id, d.name AS device_name, d.id AS device_id,
               e.created_at
        FROM employees e
        JOIN devices d ON e.device_id = d.id
        {w}
        ORDER BY {col} {direc}
        LIMIT {limit}
    """
    return sql, params


@app.get("/users")
def users_index(
    request: Request,
    device_id: str | None = None,
    search: str | None = None,
    date_str: str | None = None,
    sort_by: str = 'name',
    sort_dir: str = 'asc',
):
    conn = get_connection()
    try:
        users = list_global_users(conn)
        sql, params = _build_emp_query(_int_param(device_id), search, date_str, sort_by, sort_dir)
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            employees = [dict(row) for row in cur.fetchall()]
        devices = get_devices(conn)
        return render(templates, request, "users.html", {
            "users": users,
            "employees": employees,
            "devices": devices,
            "selected_device_id": device_id,
            "search": search or '',
            "date_str": date_str or '',
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        })
    finally:
        conn.close()


@app.get("/users/export")
def users_export(
    device_id: str | None = None,
    search: str | None = None,
    date_str: str | None = None,
    sort_by: str = 'name',
    sort_dir: str = 'asc',
):
    conn = get_connection()
    try:
        sql, params = _build_emp_query(_int_param(device_id), search, date_str, sort_by, sort_dir, limit=10000)
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Device', 'UID', 'User ID', 'Name', 'Privilege', 'Card',
                     'Linked to Global', 'Created (AD)', 'Created (BS)'])
    for r in rows:
        writer.writerow([
            r.get('device_name', ''),
            r.get('uid', ''),
            r.get('user_id', ''),
            r.get('name', ''),
            r.get('privilege', 0),
            r.get('card', '') or '',
            'Yes' if r.get('global_user_id') else 'No',
            nepali_utils.jinja_fmt_dt(r.get('created_at')),
            nepali_utils.jinja_bs_datetime(r.get('created_at')),
        ])

    fname = f"employees_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )


@app.get("/users/add")
def user_add_form(request: Request):
    return render(templates, request, "user_form.html", {"user": None})


@app.post("/users/add")
def user_add(request: Request,
             global_user_id: str = Form(...),
             name: str = Form(...),
             privilege: int = Form(0),
             card: str = Form(None),
             enroll_devices: str = Form(None)):
    # enroll_devices is optional CSV of device ids
    conn = get_connection()
    try:
        gid = create_global_user(conn, global_user_id, name, int(privilege), card)
        conn.commit()
        # push to selected devices
        if enroll_devices:
            ids = [int(x) for x in enroll_devices.split(",") if x.strip()]
            for did in ids:
                d = get_device(conn, did)
                if not d:
                    continue
                device_cfg = device_config_from_row(d)
                try:
                    puller_mod.push_global_user_to_device(device_cfg, {"global_user_id": global_user_id, "name": name, "privilege": privilege, "card": card})
                except Exception:
                    pass
        return redirect_with_flash("/users", "success", f'User "{name}" was created.')
    finally:
        conn.close()


@app.post("/users/{global_id}/delete")
def user_delete(request: Request, global_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT global_user_id, name FROM global_users WHERE id = %s", (global_id,))
            row = cur.fetchone()
            if not row:
                return redirect_with_flash("/users", "warning", "User was not found.")
            global_user_id, user_name = row[0], row[1]
        devices = get_devices(conn)
        for d in devices:
            device_cfg = device_config_from_row(d)
            try:
                puller_mod.delete_user_from_device(device_cfg, global_user_id)
            except Exception:
                pass
        delete_global_user(conn, global_id)
        conn.commit()
        return redirect_with_flash("/users", "success", f'User "{user_name}" was deleted from the database and devices.')
    finally:
        conn.close()


@app.get("/users/{global_id}/push")
def user_push_form(request: Request, global_id: int):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id, global_user_id, name, privilege, card FROM global_users WHERE id = %s", (global_id,))
            user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        devices = get_devices(conn)
        return render(templates, request, 'user_push.html', {'user': dict(user), 'devices': devices})
    finally:
        conn.close()


@app.post("/users/{global_id}/push")
def user_push(request: Request, global_id: int, device_id: int = Form(...)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT global_user_id, name, privilege, card FROM global_users WHERE id = %s", (global_id,))
            row = cur.fetchone()
            if not row:
                return redirect_with_flash('/users', 'warning', 'User was not found.')
            global_user_id, name, privilege, card = row
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail='Device not found')
        device_cfg = device_config_from_row(d)
        res = puller_mod.push_global_user_to_device(device_cfg, {'global_user_id': global_user_id, 'name': name, 'privilege': privilege, 'card': card})
        return render(templates, request, 'user_push_result.html', {'result': res, 'user': {'global_user_id': global_user_id, 'name': name}})
    finally:
        conn.close()


# ---- Employee delete (from device + DB) ---------------------------------


@app.post("/employees/{emp_id}/delete")
def employee_delete(request: Request, emp_id: int):
    conn = get_connection()
    try:
        emp = get_employee_with_device(conn, emp_id)
        if not emp:
            return redirect_with_flash("/users", "warning", "Employee not found.")
        device_cfg = device_config_from_row(emp)
        result = puller_mod.delete_employee_by_uid(device_cfg, emp["uid"])
        delete_employee_record(conn, emp_id)
        conn.commit()
        if result["ok"]:
            msg = f'Removed {emp["name"] or emp["user_id"]} (UID {emp["uid"]}) from {emp["device_name"]}.'
        else:
            msg = f'Removed from DB; device said: {result["message"]}'
        return redirect_with_flash("/users", "success" if result["ok"] else "warning", msg)
    finally:
        conn.close()


@app.post("/employees/bulk-delete")
async def employees_bulk_delete(request: Request):
    form = await request.form()
    ids_raw = form.getlist("ids")
    emp_ids = [int(x) for x in ids_raw if x.strip()]
    if not emp_ids:
        return redirect_with_flash("/users", "warning", "No employees selected.")
    conn = get_connection()
    try:
        rows = get_employees_with_device(conn, emp_ids)
        ok_count = 0
        fail_count = 0
        for emp in rows:
            device_cfg = device_config_from_row(emp)
            result = puller_mod.delete_employee_by_uid(device_cfg, emp["uid"])
            if result["ok"]:
                ok_count += 1
            else:
                fail_count += 1
        bulk_delete_employee_records(conn, emp_ids)
        conn.commit()
        msg = f"Deleted {ok_count} employee(s) from device(s)."
        if fail_count:
            msg += f" {fail_count} had device errors but were removed from DB."
        return redirect_with_flash("/users", "success", msg)
    finally:
        conn.close()


# ---- Device backup download ---------------------------------------------


@app.get("/devices/{device_id}/backup")
def device_backup(device_id: int):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
    finally:
        conn.close()

    device_cfg = device_config_from_row(d)
    result = puller_mod.get_device_backup(device_cfg)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("message", "Backup failed"))

    filename = f"backup_{d['name']}_{date.today().isoformat()}.json"
    payload = json.dumps(result["data"], indent=2, ensure_ascii=False)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- Migrate users between devices --------------------------------------


@app.get("/migrate")
def migrate_form(request: Request, source_id: int | None = None):
    conn = get_connection()
    try:
        devices = get_devices(conn)
        source_users = None
        source_device = None
        error = None
        if source_id:
            source_device = get_device(conn, source_id)
            if source_device:
                device_cfg = device_config_from_row(source_device)
                try:
                    raw = puller_mod.list_device_users(device_cfg)
                    source_users = [
                        {
                            "uid": getattr(u, "uid", ""),
                            "user_id": getattr(u, "user_id", ""),
                            "name": getattr(u, "name", ""),
                            "card": getattr(u, "card", ""),
                        }
                        for u in raw
                    ]
                except Exception as exc:
                    msg = str(exc)
                    if "timed out" in msg.lower() or "timeout" in msg.lower():
                        msg = (f"Could not load device users: connection timed out. "
                               f"Check that the device is powered on and reachable on the network. "
                               f"(raw: {msg})")
                    else:
                        msg = f"Could not load device users: {msg}"
                    error = msg
        return render(templates, request, "migrate.html", {
            "devices": devices,
            "source_users": source_users,
            "source_device": source_device,
            "source_id": source_id,
            "error": error,
        })
    finally:
        conn.close()


@app.post("/migrate")
async def migrate_execute(request: Request):
    form = await request.form()
    source_device_id = int(form.get("source_device_id") or 0)
    target_device_id = int(form.get("target_device_id") or 0)
    uids_raw = form.getlist("uids")
    uids = [int(u) for u in uids_raw if u.strip()] if uids_raw else None

    if not source_device_id or not target_device_id:
        return redirect_with_flash("/migrate", "warning", "Select both source and target devices.")
    if source_device_id == target_device_id:
        return redirect_with_flash("/migrate", "warning", "Source and target must be different devices.")

    conn = get_connection()
    try:
        source_d = get_device(conn, source_device_id)
        target_d = get_device(conn, target_device_id)
        if not source_d or not target_d:
            raise HTTPException(status_code=404, detail="Device not found")
    finally:
        conn.close()

    source_cfg = device_config_from_row(source_d)
    target_cfg = device_config_from_row(target_d)
    result = puller_mod.migrate_users_to_device(source_cfg, target_cfg, uids)
    return render(templates, request, "migrate_result.html", {
        "result": result,
        "source_device": source_d,
        "target_device": target_d,
    })


# ---- Pull / Sync endpoints ----------------------------------------------


@app.post("/devices/{device_id}/pull")
def device_pull(request: Request, device_id: int):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
        device_cfg = device_config_from_row(d)
        started_at = datetime.now(timezone.utc)
        session_id = None
        error_message = None
        success = False
        user_count = 0
        records_pulled = 0
        new_inserts = 0
        completed_at = None

        try:
            device_db_id = db_mod.upsert_device(conn, device_cfg)
            conn.commit()
            session_id = db_mod.start_pull_session(conn, device_db_id, started_at)
            conn.commit()
            result = puller_mod.pull_device(device_cfg)

            if not result.success:
                error_message = result.error
                db_mod.complete_pull_session(conn, session_id, 0, 0, 'failed', result.error)
                conn.commit()
            else:
                for user in result.users:
                    gu = db_mod.find_global_user_by_global_id(conn, str(user.user_id))
                    if gu:
                        try:
                            setattr(user, 'global_user_id', gu['id'])
                        except Exception:
                            pass
                    try:
                        db_mod.upsert_employee(conn, device_db_id, user)
                    except Exception:
                        conn.rollback()

                user_count = len(result.users)
                employee_map = db_mod.build_employee_map(conn, device_db_id)
                records = [attendance_to_dict(a) for a in result.attendance]
                records_pulled = len(records)
                new_inserts = db_mod.insert_attendance_batch(conn, device_db_id, records, employee_map)
                db_mod.complete_pull_session(conn, session_id, records_pulled, new_inserts, 'success')
                conn.commit()
                success = True

            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT completed_at FROM pull_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                completed_at = row["completed_at"] if row else None

        except Exception as exc:
            conn.rollback()
            error_message = str(exc)
            if session_id:
                try:
                    db_mod.complete_pull_session(conn, session_id, 0, 0, 'failed', str(exc))
                    conn.commit()
                except Exception:
                    conn.rollback()

        return render(templates, request, "pull_result.html", {
            "device": d,
            "success": success,
            "session_id": session_id,
            "user_count": user_count,
            "records_pulled": records_pulled,
            "new_inserts": new_inserts,
            "error_message": error_message,
            "started_at": started_at,
            "completed_at": completed_at,
        })
    finally:
        conn.close()


@app.get("/devices/{device_id}/users")
def device_users_view(request: Request, device_id: int):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
        device_cfg = device_config_from_row(d)
        try:
            device_users = puller_mod.list_device_users(device_cfg)
            users = [
                {
                    "uid": getattr(u, "uid", ""),
                    "user_id": getattr(u, "user_id", ""),
                    "name": getattr(u, "name", ""),
                    "privilege": getattr(u, "privilege", ""),
                    "card": getattr(u, "card", ""),
                }
                for u in device_users
            ]
            error = None
        except Exception as exc:
            users = []
            msg = str(exc)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                msg = (f"Could not load device users: connection timed out. "
                       f"Check that the device is powered on and reachable on the network. "
                       f"(raw: {msg})")
            else:
                msg = f"Could not load device users: {msg}"
            error = msg
        return render(templates, request, "device_users.html", {
            "device": d,
            "users": users,
            "error": error,
        })
    finally:
        conn.close()


@app.post("/devices/{device_id}/import-users")
def import_device_users(
    request: Request,
    device_id: int,
    selected: list[str] | None = Form(None),
    all: str | None = Form(None),
):
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Device not found")
        device_cfg = device_config_from_row(d)
        selected_uids = {int(uid) for uid in selected} if selected else set()
        import_all = all == "1"
        results = []
        for user in puller_mod.list_device_users(device_cfg):
            uid = int(getattr(user, "uid", 0))
            if not import_all and uid not in selected_uids:
                continue
            user_id = str(getattr(user, "user_id", "") or "")
            name = str(getattr(user, "name", "") or "")
            privilege = int(getattr(user, "privilege", 0) or 0)
            card = str(getattr(user, "card", "") or "") or None
            if not user_id:
                results.append({"uid": uid, "name": name, "ok": False, "message": "Skipped blank user ID"})
                continue
            global_user = find_global_user_by_global_id(conn, user_id)
            if global_user:
                global_user_id = global_user["id"]
            else:
                global_user_id = create_global_user(conn, user_id, name, privilege, card)
                conn.commit()
            setattr(user, "global_user_id", global_user_id)
            upsert_employee(conn, device_id, user)
            conn.commit()
            results.append({"uid": uid, "name": name, "ok": True, "message": "Imported"})
        return render(templates, request, "import_result.html", {
            "device": d,
            "results": results,
        })
    finally:
        conn.close()


@app.get("/attendance")
def attendance_view(
    request: Request,
    from_date: str | None = None,
    to_date:   str | None = None,
    from_bs:   str | None = None,
    to_bs:     str | None = None,
    date_str:  str | None = None,   # legacy compat
    device_id: str | None = None,
    name:      str | None = None,
    page:      str | None = None,
    log_page:  str | None = None,
):
    import nepali_utils as _nu
    # BS date overrides
    if from_bs:
        _ad = _nu.bs_to_ad(from_bs)
        if _ad:
            from_date = _ad
    if to_bs:
        _ad = _nu.bs_to_ad(to_bs)
        if _ad:
            to_date = _ad
    # legacy single-date compat
    if date_str and not from_date:
        from_date = date_str
    if date_str and not to_date:
        to_date = date_str
    today = date.today().isoformat()
    from_date = from_date or today
    to_date   = to_date   or today
    if to_date < from_date:
        to_date = from_date

    per_page      = 100
    page_num      = max(1, _int_param(page)     or 1)
    log_page_num  = max(1, _int_param(log_page) or 1)
    device_id_int = _int_param(device_id)
    name_clean    = name.strip() if name else None

    conn = get_connection()
    try:
        devices = get_devices(conn)

        # ── Summary (all rows, then slice for pagination) ──
        summary_all  = db_mod.get_attendance_summary_filtered(
            conn, from_date, to_date, device_id_int, name_clean)
        total_summary = len(summary_all)
        total_pages   = max(1, (total_summary + per_page - 1) // per_page)
        page_num      = min(page_num, total_pages)
        summary       = summary_all[(page_num - 1) * per_page : page_num * per_page]

        # ── Raw punch log (server-side paginated) ──
        where:  list = ["DATE(al.timestamp) BETWEEN %s AND %s"]
        params: list = [from_date, to_date]
        if device_id_int:
            where.append("al.device_id = %s")
            params.append(device_id_int)
        if name_clean:
            where.append(
                "(al.name ILIKE %s OR al.user_id ILIKE %s "
                "OR EXISTS (SELECT 1 FROM employees _e "
                "           WHERE _e.device_id = al.device_id AND _e.user_id = al.user_id "
                "           AND _e.name ILIKE %s))"
            )
            params += [f'%{name_clean}%', f'%{name_clean}%', f'%{name_clean}%']

        _lat = ("LEFT JOIN LATERAL (SELECT name FROM employees "
                "WHERE device_id = al.device_id AND user_id = al.user_id "
                "ORDER BY id LIMIT 1) e ON TRUE ")

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM attendance_logs al "
                + _lat +
                f"JOIN devices d ON al.device_id = d.id "
                f"WHERE {' AND '.join(where)}", tuple(params))
            total_records = cur.fetchone()[0]

        total_log_pages = max(1, (total_records + per_page - 1) // per_page)
        log_page_num    = min(log_page_num, total_log_pages)
        log_offset      = (log_page_num - 1) * per_page

        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"SELECT al.timestamp, al.uid, al.user_id, "
                f"       COALESCE(al.name, e.name, 'Unknown') AS name, "
                f"       al.status, al.punch, al.punch_label, d.name AS device_name "
                f"FROM attendance_logs al "
                + _lat +
                f"JOIN devices d ON al.device_id = d.id "
                f"WHERE {' AND '.join(where)} "
                f"ORDER BY al.timestamp DESC LIMIT %s OFFSET %s",
                tuple(params) + (per_page, log_offset))
            records = [dict(r) for r in cur.fetchall()]

        filter_qs = urlencode({
            'from_date': from_date, 'to_date': to_date,
            'from_bs': from_bs or '', 'to_bs': to_bs or '',
            'device_id': device_id or '', 'name': name or '',
        })
        return render(templates, request, "attendance.html", {
            "records": records, "summary": summary,
            "devices": devices,
            "from_date": from_date, "to_date": to_date,
            "from_bs": from_bs or '', "to_bs": to_bs or '',
            "selected_device_id": device_id_int,
            "name_search": name or '',
            # pagination — summary
            "page": page_num, "total_pages": total_pages, "total_summary": total_summary,
            # pagination — log
            "log_page": log_page_num, "total_log_pages": total_log_pages, "total_records": total_records,
            "per_page": per_page,
            "filter_qs": filter_qs,
            # company
            "COMPANY_NAME": COMPANY_NAME, "COMPANY_ADDRESS": COMPANY_ADDRESS,
            "COMPANY_EMAIL": COMPANY_EMAIL, "COMPANY_WEBSITE": COMPANY_WEBSITE,
        })
    finally:
        conn.close()


@app.get("/attendance/export/excel")
def attendance_export_excel(
    from_date: str | None = None, to_date:   str | None = None,
    from_bs:   str | None = None, to_bs:     str | None = None,
    date_str:  str | None = None,
    device_id: str | None = None, name: str | None = None,
):
    import nepali_utils as _nu
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    if from_bs:
        _ad = _nu.bs_to_ad(from_bs)
        if _ad: from_date = _ad
    if to_bs:
        _ad = _nu.bs_to_ad(to_bs)
        if _ad: to_date = _ad
    if date_str and not from_date: from_date = date_str
    if date_str and not to_date:   to_date   = date_str
    today = date.today().isoformat()
    from_date = from_date or today
    to_date   = to_date   or today
    device_id_int = _int_param(device_id)
    name_clean = name.strip() if name else None

    conn = get_connection()
    try:
        summary = db_mod.get_attendance_summary_filtered(conn, from_date, to_date, device_id_int, name_clean)
    finally:
        conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance Summary"

    thin = Side(style='thin')
    bdr  = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Company header rows
    header_font = Font(bold=True, size=14)
    sub_font    = Font(size=10)
    ws.merge_cells('A1:J1'); ws['A1'] = COMPANY_NAME
    ws['A1'].font = header_font; ws['A1'].alignment = Alignment(horizontal='center')
    ws.merge_cells('A2:J2'); ws['A2'] = COMPANY_ADDRESS
    ws['A2'].font = sub_font; ws['A2'].alignment = Alignment(horizontal='center')
    ws.merge_cells('A3:J3'); ws['A3'] = f"Email: {COMPANY_EMAIL}  |  Website: {COMPANY_WEBSITE}"
    ws['A3'].font = sub_font; ws['A3'].alignment = Alignment(horizontal='center')
    ws.merge_cells('A4:J4'); ws['A4'] = (
        f"Attendance Report  |  {from_date} to {to_date}"
        f"  |  {_nu.bs_date_str(from_date)} BS  to  {_nu.bs_date_str(to_date)} BS")
    ws['A4'].font = Font(italic=True, size=10)
    ws['A4'].alignment = Alignment(horizontal='center')
    ws.append([])  # blank row 5

    col_headers = ['SN', 'Name', 'User ID',
                   'First Check-In (NPT)', 'First Check-In (BS)',
                   'Last Check-Out (NPT)', 'Last Check-Out (BS)',
                   'All Punch Times', 'Total Punches', 'Devices']
    ws.append(col_headers)
    hdr_fill = PatternFill("solid", fgColor="1769E0")
    hdr_font = Font(bold=True, color="FFFFFF")
    for cell in ws[6]:
        cell.fill = hdr_fill; cell.font = hdr_font
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
        cell.border = bdr

    for i, s in enumerate(summary, 1):
        punch_str = "\n".join(
            f"{_nu.jinja_fmt_dt(p['ts'])} {p['label']}" for p in s.get('punches', []))
        row_data = [
            i,
            s['name'], str(s['user_id']),
            _nu.jinja_fmt_dt(s['first_in']),
            _nu.jinja_bs_datetime(s['first_in']),
            _nu.jinja_fmt_dt(s['last_out']),
            _nu.jinja_bs_datetime(s['last_out']),
            punch_str,
            s['total_punches'],
            s['devices'] or '',
        ]
        ws.append(row_data)
        row_idx = ws.max_row
        for cell in ws[row_idx]:
            cell.border = bdr
            cell.alignment = Alignment(wrap_text=True, vertical='top')
        if i % 2 == 0:
            fill = PatternFill("solid", fgColor="EEF6FF")
            for cell in ws[row_idx]: cell.fill = fill

    col_widths = [6, 30, 12, 22, 28, 22, 28, 40, 10, 24]
    for idx, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    for r in range(1, 5):
        ws.row_dimensions[r].height = 18
    ws.row_dimensions[6].height = 20
    ws.freeze_panes = 'A7'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"attendance_{from_date}_to_{to_date}.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/attendance/export/pdf")
def attendance_export_pdf(
    from_date: str | None = None, to_date:   str | None = None,
    from_bs:   str | None = None, to_bs:     str | None = None,
    date_str:  str | None = None,
    device_id: str | None = None, name: str | None = None,
):
    import nepali_utils as _nu
    from fpdf import FPDF
    if from_bs:
        _ad = _nu.bs_to_ad(from_bs)
        if _ad: from_date = _ad
    if to_bs:
        _ad = _nu.bs_to_ad(to_bs)
        if _ad: to_date = _ad
    if date_str and not from_date: from_date = date_str
    if date_str and not to_date:   to_date   = date_str
    today = date.today().isoformat()
    from_date = from_date or today
    to_date   = to_date   or today
    device_id_int = _int_param(device_id)
    name_clean = name.strip() if name else None

    conn = get_connection()
    try:
        summary = db_mod.get_attendance_summary_filtered(conn, from_date, to_date, device_id_int, name_clean)
    finally:
        conn.close()

    # Column widths (mm) for landscape A4 (277mm usable)
    COL_W = [10, 52, 18, 32, 40, 32, 40, 11, 42]  # total = 277
    HEADERS = ['SN', 'Name', 'User ID', 'First In (NPT)', 'First In (BS)',
               'Last Out (NPT)', 'Last Out (BS)', 'Punches', 'Devices']
    ROW_H = 7

    class AttPDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 13)
            self.cell(0, 7, COMPANY_NAME, align='C', new_x='LMARGIN', new_y='NEXT')
            self.set_font('Helvetica', '', 9)
            self.cell(0, 5, COMPANY_ADDRESS, align='C', new_x='LMARGIN', new_y='NEXT')
            self.cell(0, 5, f"Email: {COMPANY_EMAIL}  |  Website: {COMPANY_WEBSITE}",
                      align='C', new_x='LMARGIN', new_y='NEXT')
            self.set_draw_color(23, 105, 224)
            self.set_line_width(0.5)
            self.line(self.l_margin, self.get_y() + 1,
                      self.w - self.r_margin, self.get_y() + 1)
            self.ln(4)
            self.set_font('Helvetica', 'I', 8)
            bs_from = _nu.bs_date_str(from_date)
            bs_to   = _nu.bs_date_str(to_date)
            self.cell(0, 5,
                f"Attendance Report  |  {from_date}  to  {to_date}"
                f"   ({bs_from} BS  to  {bs_to} BS)",
                align='C', new_x='LMARGIN', new_y='NEXT')
            self.ln(2)
            # Table header
            self.set_fill_color(23, 105, 224)
            self.set_text_color(255, 255, 255)
            self.set_font('Helvetica', 'B', 7)
            for h, w in zip(HEADERS, COL_W):
                self.cell(w, ROW_H, h, border=1, align='C', fill=True)
            self.set_text_color(0, 0, 0)
            self.ln()

        def footer(self):
            self.set_y(-12)
            self.set_font('Helvetica', 'I', 7)
            self.cell(0, 5, f"Page {self.page_no()} — Generated by ZKTeco Attendance Console", align='C')

    pdf = AttPDF(orientation='L', unit='mm', format='A4')
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font('Helvetica', '', 7)

    for i, s in enumerate(summary, 1):
        if pdf.get_y() + ROW_H > pdf.page_break_trigger:
            pdf.add_page()
        fill = (i % 2 == 0)
        if fill:
            pdf.set_fill_color(238, 246, 255)
        def _t(v):
            txt = str(v or '')
            return txt[:40] + '…' if len(txt) > 40 else txt
        row_vals = [
            str(i),
            _t(s['name']),
            str(s['user_id']),
            _nu.jinja_fmt_dt(s['first_in']),
            _nu.jinja_bs_datetime(s['first_in']),
            _nu.jinja_fmt_dt(s['last_out']),
            _nu.jinja_bs_datetime(s['last_out']),
            str(s['total_punches']),
            _t(s['devices'] or ''),
        ]
        for val, w in zip(row_vals, COL_W):
            pdf.cell(w, ROW_H, val, border=1, fill=fill)
        pdf.ln()

    buf = io.BytesIO(pdf.output())
    fname = f"attendance_{from_date}_to_{to_date}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/pull-sessions")
def pull_sessions_view(request: Request, device_id: str | None = None, status: str | None = None, days: str | None = None):
    """Show pull sessions with optional filters: device_id, status, days (lookback)."""
    device_id_int = _int_param(device_id)
    days_int = _int_param(days) or 7
    conn = get_connection()
    try:
        sql = "SELECT ps.*, d.name as device_name FROM pull_sessions ps JOIN devices d ON ps.device_id = d.id"
        where = []
        params = []
        if device_id_int:
            where.append("ps.device_id = %s")
            params.append(device_id_int)
        if status:
            where.append("ps.status = %s")
            params.append(status)
        where.append("ps.started_at >= NOW() - (%s * INTERVAL '1 day')")
        params.append(days_int)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ps.started_at DESC LIMIT 1000"
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, tuple(params))
            sessions = [dict(r) for r in cur.fetchall()]
        # load devices for filter dropdown
        devices = db_mod.get_devices(conn) if hasattr(db_mod, 'get_devices') else get_devices(conn)
        return render(templates, request, "pull_sessions.html", {
            "sessions": sessions,
            "devices": devices,
            "selected_device_id": device_id_int,
            "selected_status": status,
            "selected_days": days_int,
        })
    finally:
        conn.close()


@app.get('/bulk-enroll')
def bulk_enroll_form(request: Request):
    conn = get_connection()
    try:
        users = list_global_users(conn)
        devices = get_devices(conn)
        return render(templates, request, 'bulk_enroll.html', {'users': users, 'devices': devices})
    finally:
        conn.close()


@app.get('/devices/{device_id}/sync')
def device_sync_view(request: Request, device_id: int):
    """Show diff between DB and device users for a device."""
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail='Device not found')
        device_cfg = device_config_from_row(d)
        # fetch device users
        try:
            device_users = puller_mod.list_device_users(device_cfg)
        except Exception as exc:
            return render(templates, request, 'sync.html', {'device': d, 'error': str(exc)})

        # Map device users by user_id (global_user_id) and uid
        dev_by_userid = {str(getattr(u, 'user_id')): u for u in device_users}
        dev_by_uid = {int(getattr(u, 'uid')): u for u in device_users}

        # fetch DB employees for this device
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute('SELECT id, uid, user_id, name, privilege, card, global_user_id FROM employees WHERE device_id = %s', (device_id,))
            db_employees = [dict(r) for r in cur.fetchall()]

        db_by_userid = {str(r['user_id']): r for r in db_employees if r.get('user_id')}
        db_by_uid = {int(r['uid']): r for r in db_employees}

        users_only_on_device = [v for k, v in dev_by_userid.items() if k not in db_by_userid]
        users_only_in_db = [v for k, v in db_by_userid.items() if k not in dev_by_userid]

        return render(templates, request, 'sync.html', {
            'device': d,
            'device_users': device_users,
            'db_employees': db_employees,
            'only_on_device': users_only_on_device,
            'only_in_db': users_only_in_db,
        })
    finally:
        conn.close()


@app.post('/devices/{device_id}/sync')
def device_sync_action(request: Request, device_id: int, action: str = Form(...), selected: list[str] | None = Form(None)):
    """Resolve diffs: action='push_missing' pushes DB users missing on device; action='import_unknown' imports device-only users to global_users and employees."""
    conn = get_connection()
    try:
        d = get_device(conn, device_id)
        if not d:
            raise HTTPException(status_code=404, detail='Device not found')
        device_cfg = device_config_from_row(d)

        ids = [s.strip() for s in selected] if selected else []
        results = []

        if action == 'push_missing':
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                if ids:
                    cur.execute(
                        'SELECT global_user_id, name, privilege, card FROM global_users WHERE id = ANY(%s)',
                        ([int(x) for x in ids],),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                else:
                    rows = []
            for gu in rows:
                try:
                    r = puller_mod.push_global_user_to_device(device_cfg, gu)
                    results.append({'global_user_id': gu.get('global_user_id'), 'result': r})
                except Exception as exc:
                    results.append({'global_user_id': gu.get('global_user_id'), 'result': {'ok': False, 'message': str(exc)}})

        elif action == 'import_unknown':
            # import device-only users to global_users and employees
            try:
                device_users = puller_mod.list_device_users(device_cfg)
            except Exception as exc:
                return render(templates, request, 'sync_result.html', {
                    'device': d,
                    'results': [{'ok': False, 'message': str(exc)}],
                    'action': action,
                    'action_label': action_label(action),
                    'ok_count': 0,
                    'fail_count': 1,
                })
            device_users_filter = device_users
            if ids:
                selected_uids = set(int(x) for x in ids)
                device_users_filter = [u for u in device_users if int(getattr(u, 'uid')) in selected_uids]
            for u in device_users_filter:
                uid = int(getattr(u, 'uid'))
                user_id = str(getattr(u, 'user_id') or '')
                if user_id == '':
                    # skip blank ids
                    continue
                # if already in global_users skip
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute('SELECT id FROM global_users WHERE global_user_id = %s', (user_id,))
                    row = cur.fetchone()
                    if row:
                        gu_id = row['id']
                    else:
                        gu_id = create_global_user(conn, user_id, getattr(u, 'name') or '', int(getattr(u, 'privilege') or 0), getattr(u, 'card') or None)
                        conn.commit()
                # upsert employee for this device
                try:
                    # build a simple user-like dict
                    user_obj = {'uid': uid, 'user_id': user_id, 'name': getattr(u, 'name') or '', 'privilege': int(getattr(u, 'privilege') or 0), 'card': getattr(u, 'card') or None, 'global_user_id': gu_id}
                    db_mod.upsert_employee(conn, device_id, user_obj)
                    conn.commit()
                    results.append({'uid': uid, 'global_user_id': user_id, 'ok': True})
                except Exception as exc:
                    conn.rollback()
                    results.append({'uid': uid, 'global_user_id': user_id, 'ok': False, 'error': str(exc)})

        else:
            return render(templates, request, 'sync_result.html', {
                'device': d,
                'results': [{'ok': False, 'message': 'Unknown action'}],
                'action': action,
                'action_label': action_label(action),
                'ok_count': 0,
                'fail_count': 1,
            })

        ok_count = sum(1 for r in results if (r.get('result') or {}).get('ok') or r.get('ok'))
        fail_count = len(results) - ok_count
        return render(templates, request, 'sync_result.html', {
            'device': d,
            'results': results,
            'action': action,
            'action_label': action_label(action),
            'ok_count': ok_count,
            'fail_count': fail_count,
        })
    finally:
        conn.close()


@app.post('/bulk-enroll')
def bulk_enroll(request: Request, device_id: int = Form(...), user_ids: str = Form(None)):
    """Enroll multiple global users (CSV of ids) to a single device."""
    conn = get_connection()
    try:
        devices = get_devices(conn)
        target = None
        for d in devices:
            if d['id'] == device_id:
                target = d
                break
        if not target:
            raise HTTPException(status_code=404, detail='Device not found')
        # resolve user list
        ids = [int(x) for x in user_ids.split(',') if x.strip()] if user_ids else []
        global_users = []
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            if ids:
                cur.execute('SELECT global_user_id, name, privilege, card FROM global_users WHERE id = ANY(%s)', (ids,))
            else:
                cur.execute('SELECT global_user_id, name, privilege, card FROM global_users')
            for row in cur.fetchall():
                global_users.append(dict(row))

        device_cfg = device_config_from_row(target)
        summary = puller_mod.push_bulk_users_to_device(device_cfg, global_users)
        return render(templates, request, 'bulk_result.html', {'summary': summary, 'device': target})
    finally:
        conn.close()


# ---- Nepali date conversion API -----------------------------------------


@app.get("/api/bs-to-ad")
def api_bs_to_ad(date: str = ""):
    """Convert BS date 'YYYY-MM-DD' → AD ISO date."""
    from nepali_utils import bs_to_ad
    ad = bs_to_ad(date) if date else None
    return {"bs": date, "ad": ad}


@app.get("/api/ad-to-bs")
def api_ad_to_bs(date: str = ""):
    """Convert AD date 'YYYY-MM-DD' → BS ISO date."""
    from nepali_utils import ad_to_bs
    bs = ad_to_bs(date) if date else None
    return {"ad": date, "bs": bs}


@app.get("/api/bs-month-info")
def api_bs_month_info(year: int = 2082, month: int = 1):
    """Return days count + first weekday (0=Sun) for a BS month."""
    from nepali_utils import bs_month_info
    info = bs_month_info(year, month)
    if info is None:
        raise HTTPException(status_code=400, detail="Invalid BS year/month")
    return info


# ---- Monthly attendance report ------------------------------------------


def _fmt_min(minutes: int | None) -> str:
    if minutes is None or minutes <= 0:
        return ''
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def _time_to_min(t) -> int | None:
    """Convert a datetime.time or HH:MM string to minutes since midnight."""
    if t is None:
        return None
    try:
        if hasattr(t, 'hour'):
            return t.hour * 60 + t.minute
        parts = str(t).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def _compute_monthly_report(daily_rows: list, from_ad: str, to_ad: str,
                              default_si_min: int = 600, default_so_min: int = 1020,
                              shift_calendar: dict = None) -> list:
    """Build per-day dicts matching the 16-column ZKBioTime periodic attendance format.

    Columns: Work Date | Planned In | Planned Out | Work Time |
             Time In | Time Out | Break In | Break Out | Time |
             Actual | OT | LateIn | EarlyOut | EarlyIn | LateOut | Remark
    """
    from datetime import date, timedelta
    from nepali_utils import ad_to_bs_tuple

    NEPAL_DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

    punch_map = {r['work_date']: r for r in daily_rows}
    start = date.fromisoformat(from_ad)
    end   = date.fromisoformat(to_ad)

    def _pstr(pd) -> str:
        if not pd:
            return ''
        t = pd.get('time')
        ts = t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t)[:5]
        dev = pd.get('device_name', '')
        return f"{ts} ({dev})" if dev else ts

    days = []
    d = start
    while d <= end:
        bs_t = ad_to_bs_tuple(d)
        bs_str = f"{bs_t[2]:02d}/{bs_t[1]:02d}/{bs_t[0]}" if bs_t else ''
        nepal_dow = d.isoweekday() % 7
        day_name  = NEPAL_DAYS[nepal_dow]
        is_weekend = (nepal_dow == 6)

        # Per-day shift lookup (employee-specific or department-level via shift_calendar)
        shift_info   = (shift_calendar or {}).get(d)
        si_min       = shift_info['start_min'] if shift_info else default_si_min
        so_min       = shift_info['end_min']   if shift_info else default_so_min
        shift_name   = shift_info['name']      if shift_info else ''
        planned_work = so_min - si_min

        row = punch_map.get(d)

        pts = []
        if row:
            if row.get('all_punches_with_device'):
                pts = [p for p in row['all_punches_with_device'] if p]
            elif row.get('all_punch_times'):
                pts = [{'time': p, 'device_name': ''} for p in row['all_punch_times'] if p]

        first_punch = row['first_punch'] if row else None
        last_punch  = row['last_punch']  if row else None

        # 1 punch: Time In only
        # 2 punches: Time In + Break Out
        # 3 punches: Time In + Time Out + Break Out
        # 4+:        Time In + Time Out + Break In + Break Out
        time_in   = _pstr(pts[0])  if len(pts) >= 1 else ''
        time_out  = _pstr(pts[1])  if len(pts) >= 3 else ''
        break_in  = _pstr(pts[2])  if len(pts) >= 4 else ''
        break_out = _pstr(pts[-1]) if len(pts) >= 2 else ''

        ci_min = _time_to_min(first_punch)
        co_min = _time_to_min(last_punch) if (last_punch and first_punch and last_punch != first_punch) else None

        work_min = (co_min - ci_min) if (ci_min is not None and co_min is not None and co_min > ci_min) else None
        if work_min is not None:
            time_col = _fmt_min(work_min)
        elif len(pts) == 1:
            time_col = _pstr(pts[0])
        else:
            time_col = ''

        late_in = early_out = early_in = late_out = ot = ''
        if not is_weekend and ci_min is not None:
            if ci_min > si_min:
                late_in  = _fmt_min(ci_min - si_min)
            elif ci_min < si_min:
                early_in = _fmt_min(si_min - ci_min)
        if not is_weekend and co_min is not None:
            if co_min < so_min:
                early_out = _fmt_min(so_min - co_min)
            elif co_min > so_min:
                late_out  = _fmt_min(co_min - so_min)
        if not is_weekend and work_min and work_min > planned_work:
            ot = _fmt_min(work_min - planned_work)

        if is_weekend:
            remark = 'Weekend'
        elif row:
            remark = 'Present'
        else:
            remark = 'Absent'

        days.append({
            'bs_date':      bs_str,
            'ad_date':      d.isoformat(),
            'day_name':     day_name,
            'shift_name':   shift_name,
            'planned_in':   _fmt_min(si_min)      if not is_weekend else '00:00',
            'planned_out':  _fmt_min(so_min)      if not is_weekend else '00:00',
            'planned_work': _fmt_min(planned_work) if not is_weekend else '',
            'planned_min':  planned_work           if not is_weekend else 0,
            'time_in':      time_in,
            'time_out':     time_out,
            'break_in':     break_in,
            'break_out':    break_out,
            'time_col':     time_col,
            'actual':       time_col,
            'ot':           ot,
            'late_in':      late_in,
            'early_out':    early_out,
            'early_in':     early_in,
            'late_out':     late_out,
            'remark':       remark,
            'work_min':     work_min or 0,
        })
        d += timedelta(days=1)

    return days


def _monthly_totals(days: list, planned_work: int = 0) -> dict:
    tot_actual = tot_ot = tot_late_in = tot_early_out = tot_early_in = tot_late_out = 0
    tot_planned = 0

    counts = {'Present': 0, 'Absent': 0, 'Weekend': 0, 'Holiday': 0, 'Leave': 0, 'Misc': 0}
    for d in days:
        tot_actual    += d.get('work_min', 0)
        # Sum per-day planned for all workdays (present + absent, not weekend/holiday)
        if d['remark'] not in ('Weekend', 'Holiday'):
            tot_planned += d.get('planned_min', 0)
        def _parse(s):
            if not s: return 0
            try:
                p = str(s).split(':')
                return int(p[0]) * 60 + int(p[1])
            except Exception:
                return 0
        tot_ot        += _parse(d['ot'])
        tot_late_in   += _parse(d['late_in'])
        tot_early_out += _parse(d['early_out'])
        tot_early_in  += _parse(d['early_in'])
        tot_late_out  += _parse(d['late_out'])
        r = d['remark']
        if r in counts:
            counts[r] += 1

    return {
        'planned':   _fmt_min(tot_planned),
        'actual':    _fmt_min(tot_actual),
        'ot':        _fmt_min(tot_ot),
        'late_in':   _fmt_min(tot_late_in),
        'early_out': _fmt_min(tot_early_out),
        'early_in':  _fmt_min(tot_early_in),
        'late_out':  _fmt_min(tot_late_out),
        'counts':    counts,
    }


def _bs_defaults():
    try:
        import nepali_datetime
        t = nepali_datetime.date.today()
        return t.year, t.month
    except Exception:
        return 2082, 1


def _npt_now_str():
    import zoneinfo as _zi
    return datetime.now(_zi.ZoneInfo('Asia/Kathmandu')).strftime('%Y-%m-%d %H:%M') + ' NPT'


def _month_name(m: int) -> str:
    names = ['','Baisakh','Jestha','Ashadh','Shrawan','Bhadra','Ashwin',
             'Kartik','Mangsir','Poush','Magh','Falgun','Chaitra']
    return names[m] if 1 <= m <= 12 else str(m)


@app.get("/reports/monthly")
def reports_monthly_list(
    request: Request,
    bs_year:  str | None = None,
    bs_month: str | None = None,
    page:     str | None = None,
):
    def_year, def_month = _bs_defaults()
    sel_year  = _int_param(bs_year)  or def_year
    sel_month = _int_param(bs_month) or def_month
    page_num  = max(1, _int_param(page) or 1)
    per_page  = 25

    conn = get_connection()
    try:
        from db import get_employees_for_report as _gef
        all_emps = _gef(conn)
    finally:
        conn.close()

    total       = len(all_emps)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page_num    = min(page_num, total_pages)
    page_emps   = all_emps[(page_num - 1) * per_page: page_num * per_page]

    return render(templates, request, 'reports_monthly.html', {
        'view':            'list',
        'page_emps':       page_emps,
        'total_employees': total,
        'page':            page_num,
        'total_pages':     total_pages,
        'sel_bs_year':     sel_year,
        'sel_bs_month':    sel_month,
        'def_year':        def_year,
        'def_month':       def_month,
        'report':          None,
        'error':           None,
        'now_str':         _npt_now_str(),
        'COMPANY_NAME':    COMPANY_NAME,
    })


@app.get("/reports/monthly/view")
def reports_monthly_view(
    request: Request,
    emp_key:   str | None = None,
    global_id: str | None = None,   # backward-compat
    bs_year:   str | None = None,
    bs_month:  str | None = None,
):
    def_year, def_month = _bs_defaults()
    bs_y = _int_param(bs_year)  or def_year
    bs_m = _int_param(bs_month) or def_month
    SI_MIN, SO_MIN = 600, 1020   # default 10:00–17:00 if no shift rule found

    conn = get_connection()
    try:
        from db import get_employees_for_report as _gef
        all_emps = _gef(conn)

        # Resolve emp_key: prefer explicit emp_key, fall back to global_id compat
        resolved_key = emp_key
        if not resolved_key and global_id:
            gid_int = _int_param(global_id)
            resolved_key = f"g{gid_int}" if gid_int else None

        emp_entry = None
        if resolved_key:
            for e in all_emps:
                if e['key'] == resolved_key:
                    emp_entry = e
                    break
        if emp_entry is None and all_emps:
            emp_entry    = all_emps[0]
            resolved_key = emp_entry['key']

        g_id = emp_entry['global_id'] if emp_entry else None

        report = None
        error  = None
        if emp_entry:
            from nepali_utils import bs_month_info as _bsmi
            mi = _bsmi(bs_y, bs_m)
            if mi is None:
                error = "Invalid BS year/month"
            else:
                from_ad = mi['first_ad']
                to_ad   = mi['last_ad']
                pairs   = [(dv['device_id'], dv['user_id']) for dv in emp_entry['devices']]

                from db import get_employee_daily_attendance_multi as _multi
                from db import get_shift_calendar as _gsc
                daily     = _multi(conn, pairs, from_ad, to_ad)
                shift_cal = _gsc(conn, g_id, from_ad, to_ad)
                days      = _compute_monthly_report(daily, from_ad, to_ad, SI_MIN, SO_MIN, shift_cal)
                totals    = _monthly_totals(days)

                report = {
                    'days':         days,
                    'totals':       totals,
                    'emp_name':     emp_entry['display_name'],
                    'emp_user_id':  emp_entry['company_id'] or (
                                    emp_entry['devices'][0]['user_id'] if emp_entry['devices'] else ''),
                    'device_name':  ', '.join(dv['device_name'] for dv in emp_entry['devices']),
                    'department':   emp_entry.get('department_name', ''),
                    'bs_year':      bs_y,
                    'bs_month':     bs_m,
                    'month_name':   mi['month_name'],
                    'from_ad':      from_ad,
                    'to_ad':        to_ad,
                    'from_bs_disp': nepali_utils.bs_date_str(from_ad, fmt='long'),
                    'to_bs_disp':   nepali_utils.bs_date_str(to_ad,   fmt='long'),
                    'global_id':    g_id,
                }
        else:
            error = "No employees found. Pull data from devices first."

    except Exception as exc:
        error    = str(exc)
        report   = None
        all_emps = []
        resolved_key = None
    finally:
        conn.close()

    return render(templates, request, 'reports_monthly.html', {
        'view':             'report',
        'all_emps':         all_emps,
        'sel_bs_year':      bs_y,
        'sel_bs_month':     bs_m,
        'def_year':         def_year,
        'def_month':        def_month,
        'sel_emp_key':      resolved_key,
        'sel_emp_display':  emp_entry['display_name'] if emp_entry else None,
        'report':           report,
        'error':            error,
        'now_str':          _npt_now_str(),
        'COMPANY_NAME':     COMPANY_NAME,
    })


@app.get("/reports/monthly/print-all")
def reports_monthly_print_all(
    request: Request,
    bs_year:  str | None = None,
    bs_month: str | None = None,
):
    def_year, def_month = _bs_defaults()
    bs_y = _int_param(bs_year)  or def_year
    bs_m = _int_param(bs_month) or def_month
    SI_MIN, SO_MIN = 600, 1020

    conn = get_connection()
    try:
        from db import get_employees_for_report as _gef
        from nepali_utils import bs_month_info as _bsmi
        from db import get_employee_daily_attendance_multi as _multi
        all_emps = _gef(conn)
        mi = _bsmi(bs_y, bs_m)
        reports = []
        if mi:
            from_ad, to_ad = mi['first_ad'], mi['last_ad']
            from db import get_shift_calendar as _gsc
            for emp in all_emps:
                pairs     = [(dv['device_id'], dv['user_id']) for dv in emp['devices']]
                daily     = _multi(conn, pairs, from_ad, to_ad)
                shift_cal = _gsc(conn, emp.get('global_id'), from_ad, to_ad)
                days      = _compute_monthly_report(daily, from_ad, to_ad, SI_MIN, SO_MIN, shift_cal)
                totals    = _monthly_totals(days)
                reports.append({
                    'emp_name':    emp['display_name'],
                    'emp_user_id': emp['company_id'] or (emp['devices'][0]['user_id'] if emp['devices'] else ''),
                    'device_name': ', '.join(dv['device_name'] for dv in emp['devices']),
                    'days':        days,
                    'totals':      totals,
                    'month_name':  mi['month_name'],
                    'bs_year':     bs_y,
                    'from_bs_disp': nepali_utils.bs_date_str(from_ad, fmt='long'),
                    'to_bs_disp':   nepali_utils.bs_date_str(to_ad,   fmt='long'),
                })
    finally:
        conn.close()

    return render(templates, request, 'reports_monthly_print_all.html', {
        'reports':      reports,
        'bs_year':      bs_y,
        'bs_month':     bs_m,
        'month_name':   mi['month_name'] if mi else '',
        'now_str':      _npt_now_str(),
        'COMPANY_NAME': COMPANY_NAME,
    })


# ---- Settings (Org Hierarchy / Shifts / Shift Rules) ---------------------


@app.get("/settings")
def settings_page(request: Request):
    conn = get_connection()
    try:
        from db import (get_all_departments, get_all_shifts, get_all_shift_rules,
                        get_all_global_users_with_dept, get_employees_for_report as _gef,
                        get_all_directorates, get_all_sections, get_all_units)
        departments  = get_all_departments(conn)
        shifts       = get_all_shifts(conn)
        shift_rules  = get_all_shift_rules(conn)
        employees    = get_all_global_users_with_dept(conn)
        directorates = get_all_directorates(conn)
        sections     = get_all_sections(conn)
        units        = get_all_units(conn)
        all_emps     = _gef(conn)
    finally:
        conn.close()
    return render(templates, request, 'settings.html', {
        'departments':  departments,
        'shifts':       shifts,
        'shift_rules':  shift_rules,
        'employees':    employees,
        'directorates': directorates,
        'sections':     sections,
        'units':        units,
        'all_emps':     all_emps,
    })


# ── Directorates ──────────────────────────────────────────────────────────────

@app.post("/settings/directorates/add")
async def add_directorate(request: Request):
    form = await request.form()
    name = (form.get('name') or '').strip()
    if not name:
        return redirect_with_flash('/settings', 'error', 'Directorate name is required.')
    conn = get_connection()
    try:
        from db import create_directorate
        create_directorate(conn, name)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', f"Directorate '{name}' added.")

@app.post("/settings/directorates/{did}/delete")
async def delete_directorate_route(request: Request, did: int):
    conn = get_connection()
    try:
        from db import delete_directorate
        delete_directorate(conn, did)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Directorate deleted.')


# ── Departments ───────────────────────────────────────────────────────────────

@app.post("/settings/departments/add")
async def add_department(request: Request):
    form = await request.form()
    name    = (form.get('name') or '').strip()
    dir_id  = _int_param(form.get('directorate_id'))
    if not name:
        return redirect_with_flash('/settings', 'error', 'Department name is required.')
    conn = get_connection()
    try:
        from db import create_department
        create_department(conn, name)
        if dir_id:
            with conn.cursor() as cur:
                cur.execute("UPDATE departments SET directorate_id=%s WHERE name=%s", (dir_id, name))
            conn.commit()
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', f"Department '{name}' added.")

@app.post("/settings/departments/{dept_id}/delete")
async def delete_department_route(request: Request, dept_id: int):
    conn = get_connection()
    try:
        from db import delete_department
        delete_department(conn, dept_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Department deleted.')


# ── Sections ──────────────────────────────────────────────────────────────────

@app.post("/settings/sections/add")
async def add_section(request: Request):
    form    = await request.form()
    name    = (form.get('name') or '').strip()
    dept_id = _int_param(form.get('department_id'))
    if not name or not dept_id:
        return redirect_with_flash('/settings', 'error', 'Section name and department are required.')
    conn = get_connection()
    try:
        from db import create_section
        create_section(conn, name, dept_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', f"Section '{name}' added.")

@app.post("/settings/sections/{sid}/delete")
async def delete_section_route(request: Request, sid: int):
    conn = get_connection()
    try:
        from db import delete_section
        delete_section(conn, sid)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Section deleted.')


# ── Units ─────────────────────────────────────────────────────────────────────

@app.post("/settings/units/add")
async def add_unit(request: Request):
    form    = await request.form()
    name    = (form.get('name') or '').strip()
    sec_id  = _int_param(form.get('section_id'))
    if not name or not sec_id:
        return redirect_with_flash('/settings', 'error', 'Unit name and section are required.')
    conn = get_connection()
    try:
        from db import create_unit
        create_unit(conn, name, sec_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', f"Unit '{name}' added.")

@app.post("/settings/units/{uid}/delete")
async def delete_unit_route(request: Request, uid: int):
    conn = get_connection()
    try:
        from db import delete_unit
        delete_unit(conn, uid)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Unit deleted.')


# ── Shifts ────────────────────────────────────────────────────────────────────

@app.post("/settings/shifts/add")
async def add_shift(request: Request):
    form  = await request.form()
    name  = (form.get('name') or '').strip()
    start = (form.get('start_time') or '').strip()
    end   = (form.get('end_time') or '').strip()
    if not (name and start and end):
        return redirect_with_flash('/settings', 'error', 'Name, start time and end time are required.')
    conn = get_connection()
    try:
        from db import create_shift
        create_shift(conn, name, start, end)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', f"Shift '{name}' added.")

@app.post("/settings/shifts/{shift_id}/delete")
async def delete_shift_route(request: Request, shift_id: int):
    conn = get_connection()
    try:
        from db import delete_shift
        delete_shift(conn, shift_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Shift deleted.')


# ── Shift Rules ───────────────────────────────────────────────────────────────

@app.post("/settings/shift-rules/add")
async def add_shift_rule(request: Request):
    form        = await request.form()
    shift_id    = _int_param(form.get('shift_id'))
    from_date   = (form.get('from_date') or '').strip()
    to_date     = (form.get('to_date') or '').strip() or None
    target_type = form.get('target_type', 'employee')

    if not shift_id or not from_date:
        return redirect_with_flash('/settings', 'error', 'Shift and from-date are required.')

    # Collect all target IDs based on type
    g_user_ids = []
    dept_id    = dir_id = sec_id = unit_id = None

    if target_type == 'employee':
        raw = form.getlist('global_user_id[]') or form.getlist('global_user_id')
        g_user_ids = [_int_param(v) for v in raw if _int_param(v)]
        if not g_user_ids:
            return redirect_with_flash('/settings', 'error', 'Select at least one employee.')
    elif target_type == 'department':
        dept_id = _int_param(form.get('department_id'))
        if not dept_id:
            return redirect_with_flash('/settings', 'error', 'Select a department.')
    elif target_type == 'section':
        sec_id = _int_param(form.get('section_id'))
        if not sec_id:
            return redirect_with_flash('/settings', 'error', 'Select a section.')
    elif target_type == 'unit':
        unit_id = _int_param(form.get('unit_id'))
        if not unit_id:
            return redirect_with_flash('/settings', 'error', 'Select a unit.')
    elif target_type == 'directorate':
        dir_id = _int_param(form.get('directorate_id'))
        if not dir_id:
            return redirect_with_flash('/settings', 'error', 'Select a directorate.')

    conn = get_connection()
    try:
        from db import create_shift_rule
        if g_user_ids:
            for gid in g_user_ids:
                create_shift_rule(conn, shift_id, from_date, to_date, gid,
                                  None, None, None, None)
        else:
            create_shift_rule(conn, shift_id, from_date, to_date, None,
                              dept_id, dir_id, sec_id, unit_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    count = len(g_user_ids) if g_user_ids else 1
    return redirect_with_flash('/settings', 'success',
                                f"{count} shift rule(s) added.")

@app.post("/settings/shift-rules/{rule_id}/delete")
async def delete_shift_rule_route(request: Request, rule_id: int):
    conn = get_connection()
    try:
        from db import delete_shift_rule
        delete_shift_rule(conn, rule_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Shift rule deleted.')


# ── Employee Org Assignment ───────────────────────────────────────────────────

@app.post("/settings/employees/{global_id}/org")
async def set_emp_org(request: Request, global_id: int):
    form    = await request.form()
    dept_id = _int_param(form.get('department_id'))
    sec_id  = _int_param(form.get('section_id'))
    unit_id = _int_param(form.get('unit_id'))
    conn = get_connection()
    try:
        from db import set_employee_org
        set_employee_org(conn, global_id, dept_id, sec_id, unit_id)
    except Exception as exc:
        return redirect_with_flash('/settings', 'error', str(exc))
    finally:
        conn.close()
    return redirect_with_flash('/settings', 'success', 'Employee org assignment updated.')


# ---- Schedule editor ----------------------------------------------------


@app.get("/schedule")
def schedule_view(request: Request):
    cfg_path = os.path.join(BASE_DIR, 'config.py')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        txt = f.read()
    m = re.search(r'SCHEDULE_TIMES\s*=\s*(\[[\s\S]*?\])', txt)
    schedule_text = m.group(1) if m else '[]'
    return render(templates, request, 'schedule.html', {
        'schedule_text': schedule_text,
        'jobs': _scheduler_jobs_info(),
    })


@app.post("/schedule")
def schedule_update(request: Request, schedule_text: str = Form(...)):
    cfg_path = os.path.join(BASE_DIR, 'config.py')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        txt = f.read()
    new_txt = re.sub(r"SCHEDULE_TIMES\s*=\s*\[[\s\S]*?\]",
                     f"SCHEDULE_TIMES = {schedule_text}", txt)
    with open(cfg_path, 'w', encoding='utf-8') as f:
        f.write(new_txt)
    try:
        _restart_web_scheduler()
        jobs = _scheduler_jobs_info()
        msg = 'Schedule saved and applied immediately.'
    except Exception as exc:
        jobs = []
        msg = f'Schedule saved to config.py but scheduler reload failed: {exc}'
    return render(templates, request, 'schedule_result.html', {'message': msg, 'jobs': jobs})

