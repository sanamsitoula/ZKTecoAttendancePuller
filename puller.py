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


def _bulk_timeout(device: DeviceConfig) -> int:
    """Return a longer socket timeout for bulk-data operations (get_users, get_templates)."""
    return max(device.connection_timeout * 4, 60)


def list_device_users(device: DeviceConfig):
    """Return list of users from a device or raise on failure."""
    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(device.ip, port=device.port, timeout=_bulk_timeout(device), password=password,
            ommit_ping=True, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        return conn.get_users()
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def push_global_user_to_device(device: DeviceConfig, global_user: dict) -> dict:
    """Robustly enroll or update a single global_user on the device.

    Returns a dict: {ok: bool, action: 'created'|'updated'|'noop', uid: int, message: str}
    Idempotent: if the user exists with identical attributes, returns action 'noop'.
    Any exception is captured and returned as ok=False with message.
    """
    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(device.ip, port=device.port, timeout=device.connection_timeout, password=password,
            ommit_ping=True, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        users = conn.get_users()

        # normalize incoming fields
        g_user_id = str(global_user.get("global_user_id") or "")
        g_name = str(global_user.get("name") or "")
        g_priv = int(global_user.get("privilege") or 0)
        try:
            g_card = int(global_user.get("card") or 0)
        except (ValueError, TypeError):
            g_card = 0

        # search for existing by user_id
        existing = None
        max_uid = -1
        for u in users:
            try:
                uid_val = int(u.uid)
                if uid_val > max_uid:
                    max_uid = uid_val
            except Exception:
                pass
            if str(getattr(u, 'user_id', '') ) == g_user_id:
                existing = u
                break

        # If exists, check if update is needed
        if existing:
            same_name = str(getattr(existing, 'name', '') or '') == g_name
            same_priv = int(getattr(existing, 'privilege', 0) or 0) == g_priv
            try:
                same_card = int(getattr(existing, 'card', 0) or 0) == g_card
            except (ValueError, TypeError):
                same_card = False
            if same_name and same_priv and same_card:
                return {"ok": True, "action": "noop", "uid": int(existing.uid), "message": "Already present"}
            uid_to_use = int(existing.uid)
            try:
                conn.set_user(uid=uid_to_use, name=g_name, privilege=g_priv,
                              user_id=str(g_user_id), card=int(g_card or 0))
            except Exception as exc:
                return {"ok": False, "message": f"Failed to update user: {exc}"}
            return {"ok": True, "action": "updated", "uid": uid_to_use, "message": "Updated user"}

        # Not existing: choose uid (max_uid+1)
        uid_to_use = max_uid + 1 if max_uid >= 0 else 1
        try:
            conn.set_user(uid=uid_to_use, name=g_name, privilege=g_priv,
                          user_id=str(g_user_id), card=int(g_card or 0))
        except Exception as exc:
            return {"ok": False, "message": f"Failed to create user: {exc}"}
        return {"ok": True, "action": "created", "uid": uid_to_use, "message": "User enrolled"}

    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def delete_user_from_device(device: DeviceConfig, global_user_id: str) -> dict:
    """Delete a user on the device by their `user_id` (global_user_id).

    Returns {ok: bool, message: str, uid: Optional[int]}.
    Attempts multiple delete methods and verifies removal. Exceptions are caught and reported.
    """
    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(device.ip, port=device.port, timeout=device.connection_timeout, password=password,
            ommit_ping=True, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        users = conn.get_users()
        target_uid = None
        for u in users:
            if str(getattr(u, 'user_id', '')) == str(global_user_id):
                target_uid = getattr(u, 'uid', None)
                break
        if target_uid is None:
            return {"ok": False, "message": "User not found on device"}

        # Attempt deletion using common APIs
        try:
            conn.delete_user(int(target_uid))
        except Exception:
            try:
                # some pyzk variants expose delete_user_by_uid
                conn.delete_user_by_uid(int(target_uid))
            except Exception:
                try:
                    # last resort: try set_user with empty fields (some devices interpret as deletion)
                    conn.set_user(int(target_uid), "", 0, "", "")
                except Exception as exc:
                    return {"ok": False, "message": f"Delete attempt failed: {exc}", "uid": int(target_uid)}

        # verify removal
        try:
            new_users = conn.get_users()
            for u in new_users:
                if str(getattr(u, 'user_id', '')) == str(global_user_id):
                    return {"ok": False, "message": "User still present after delete attempts", "uid": int(target_uid)}
        except Exception:
            # if verification fails, still report delete attempted
            return {"ok": True, "message": "Delete attempted (verification failed)", "uid": int(target_uid)}

        return {"ok": True, "message": "Deleted", "uid": int(target_uid)}

    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def delete_employee_by_uid(device: DeviceConfig, uid: int) -> dict:
    """Delete a user from device directly by hardware UID.

    Returns {ok: bool, message: str}.
    """
    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(device.ip, port=device.port, timeout=device.connection_timeout, password=password,
            ommit_ping=True, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        try:
            conn.delete_user(int(uid))
        except Exception:
            try:
                conn.delete_user_by_uid(int(uid))
            except Exception as exc:
                return {"ok": False, "message": f"Delete failed: {exc}"}
        # verify removal
        try:
            remaining = conn.get_users()
            for u in remaining:
                if int(getattr(u, 'uid', -1)) == int(uid):
                    return {"ok": False, "message": "User still present after delete"}
        except Exception:
            pass
        return {"ok": True, "message": "Deleted from device"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def get_device_backup(device: DeviceConfig) -> dict:
    """Export all users and fingerprint templates from a device.

    Returns {ok: bool, data: {...}} or {ok: False, message: str}.
    Template bytes are base64-encoded so the result is JSON-serialisable.
    """
    import base64
    password = int(device.password) if device.password and device.password.isdigit() else 0
    zk = ZK(device.ip, port=device.port, timeout=_bulk_timeout(device), password=password,
            ommit_ping=True, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        raw_users = conn.get_users()

        templates_by_uid: dict = {}
        try:
            raw_templates = conn.get_templates()
            for t in raw_templates:
                t_uid = int(getattr(t, 'uid', 0))
                if t_uid not in templates_by_uid:
                    templates_by_uid[t_uid] = []
                raw_bytes = getattr(t, 'template', b'') or b''
                templates_by_uid[t_uid].append({
                    "fid": int(getattr(t, 'fid', 0)),
                    "valid": int(getattr(t, 'valid', 1)),
                    "template": base64.b64encode(raw_bytes).decode('ascii'),
                })
        except Exception as tpl_err:
            logger.warning("[%s] Could not fetch fingerprint templates: %s", device.name, tpl_err)

        users = []
        for u in raw_users:
            uid = int(getattr(u, 'uid', 0))
            users.append({
                "uid": uid,
                "user_id": str(getattr(u, 'user_id', '') or ''),
                "name": str(getattr(u, 'name', '') or ''),
                "privilege": int(getattr(u, 'privilege', 0) or 0),
                "card": str(getattr(u, 'card', '') or ''),
                "fingerprints": templates_by_uid.get(uid, []),
            })

        return {
            "ok": True,
            "data": {
                "version": "1",
                "device_name": device.name,
                "device_ip": device.ip,
                "device_port": device.port,
                "user_count": len(users),
                "users": users,
            },
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def migrate_users_to_device(
    source_device: DeviceConfig,
    target_device: DeviceConfig,
    source_uids: list | None = None,
) -> dict:
    """Copy users and fingerprint templates from source to target device.

    source_uids: list of int UIDs to migrate; None = migrate all users.
    Returns {ok, total, succeeded, failed, results}.
    """
    import base64

    backup = get_device_backup(source_device)
    if not backup["ok"]:
        return {"ok": False, "message": f"Could not read source device: {backup.get('message')}"}

    source_users = backup["data"]["users"]
    if source_uids is not None:
        uid_set = {int(u) for u in source_uids}
        source_users = [u for u in source_users if u["uid"] in uid_set]

    if not source_users:
        return {"ok": True, "total": 0, "succeeded": 0, "failed": 0, "results": [],
                "message": "No users to migrate"}

    password = int(target_device.password) if target_device.password and target_device.password.isdigit() else 0
    zk = ZK(target_device.ip, port=target_device.port, timeout=_bulk_timeout(target_device),
            password=password, ommit_ping=True, force_udp=False)
    conn = None
    results = []
    succeeded = 0
    failed = 0

    try:
        conn = zk.connect()
        existing = conn.get_users()
        existing_by_userid = {str(getattr(u, 'user_id', '')): u for u in existing}
        max_uid = max((int(getattr(u, 'uid', 0)) for u in existing), default=0)

        for src in source_users:
            user_id = src["user_id"]
            name = src["name"]
            privilege = src["privilege"]
            card = src["card"]
            fingerprints = src.get("fingerprints", [])

            if user_id in existing_by_userid:
                target_uid = int(getattr(existing_by_userid[user_id], 'uid', 0))
                action = "updated"
            else:
                max_uid += 1
                target_uid = max_uid
                action = "created"

            try:
                conn.set_user(uid=target_uid, name=name, privilege=privilege,
                              user_id=str(user_id), card=int(card or 0))

                fp_ok = 0
                fp_fail = 0
                if fingerprints:
                    from zk.finger import Finger  # type: ignore
                    from zk.user import User  # type: ignore
                    finger_list = []
                    for fp in fingerprints:
                        try:
                            tpl_bytes = base64.b64decode(fp["template"])
                            fid = int(fp["fid"])
                            valid = int(fp.get("valid", 1))
                            finger_list.append(Finger(target_uid, fid, valid, tpl_bytes))
                        except Exception:
                            fp_fail += 1
                    if finger_list:
                        target_user = User(
                            uid=target_uid,
                            name=name,
                            privilege=privilege,
                            password='',
                            group_id='',
                            user_id=str(user_id),
                            card=card or 0,
                        )
                        try:
                            conn.save_user_template(target_user, finger_list)
                            fp_ok = len(finger_list)
                        except Exception:
                            fp_fail += len(finger_list)

                results.append({
                    "user_id": user_id,
                    "name": name,
                    "uid": target_uid,
                    "ok": True,
                    "action": action,
                    "fingerprints_ok": fp_ok,
                    "fingerprints_fail": fp_fail,
                })
                succeeded += 1
            except Exception as exc:
                results.append({"user_id": user_id, "name": name, "ok": False, "message": str(exc)})
                failed += 1

        return {
            "ok": failed == 0,
            "total": len(source_users),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


def push_bulk_users_to_device(device: DeviceConfig, global_users: list) -> dict:
    """Push multiple global users to a device. Continues on individual failures.

    Returns summary: {ok: bool, results: [{global_user_id, ok, action, uid, message}], summary: {created, updated, noop, failed}}
    """
    results = []
    summary = {"created": 0, "updated": 0, "noop": 0, "failed": 0}
    for gu in global_users:
        try:
            r = push_global_user_to_device(device, gu)
            results.append({"global_user_id": gu.get('global_user_id'), **r})
            if r.get('ok'):
                action = r.get('action')
                if action in summary:
                    summary[action] += 1
            else:
                summary['failed'] += 1
        except Exception as exc:
            summary['failed'] += 1
            results.append({"global_user_id": gu.get('global_user_id'), "ok": False, "message": str(exc)})
    overall_ok = summary['failed'] == 0
    return {"ok": overall_ok, "results": results, "summary": summary}

