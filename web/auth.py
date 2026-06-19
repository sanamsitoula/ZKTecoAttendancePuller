"""
Authentication helpers.

Primary source: web_users DB table (linked to global_users).
Fallback source: users.json (gitignored) — keeps legacy admin accounts working.
"""
import json
import os
import bcrypt

_USERS_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'users.json')
_USERS_EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'users.json.example')

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        path = _USERS_PATH if os.path.exists(_USERS_PATH) else _USERS_EXAMPLE
        with open(path, encoding='utf-8') as f:
            _cache = json.load(f)
    return _cache


def get_secret_key() -> str:
    return _load().get('secret_key', 'change-me')


def get_all_users() -> list:
    return _load().get('users', [])


def find_user_by_username(username: str) -> dict | None:
    for u in get_all_users():
        if u['username'].lower() == username.lower():
            return u
    return None


def find_user_by_id(user_id: int) -> dict | None:
    for u in get_all_users():
        if u['id'] == user_id:
            return u
    return None


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def get_session_user(request) -> dict | None:
    """
    Return a user dict for the current session, or None if not logged in.

    For DB-sourced users the dict is built directly from session data
    (no DB query on every request).  For JSON-sourced users we fall back
    to the users.json lookup.
    """
    uid = request.session.get('user_id')
    if not uid:
        return None
    source = request.session.get('user_source', 'json')
    if source == 'db':
        return {
            'id':             uid,
            'username':       request.session.get('username', ''),
            'display_name':   request.session.get('display_name', ''),
            'role':           request.session.get('role', 'viewer'),
            'source':         'db',
            'global_user_id': request.session.get('global_user_id'),
        }
    # legacy json fallback
    u = find_user_by_id(uid)
    if u:
        u.setdefault('source', 'json')
    return u
