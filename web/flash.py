"""One-shot flash messages via cookie (survives a single redirect)."""
import json
from urllib.parse import quote, unquote

from fastapi import Request
from fastapi.responses import RedirectResponse

FLASH_COOKIE = "_flashes"
_MAX_AGE = 60


def redirect_with_flash(url: str, category: str, message: str) -> RedirectResponse:
    payload = quote(json.dumps([{"category": category, "message": message}]))
    resp = RedirectResponse(url=url, status_code=303)
    resp.set_cookie(
        FLASH_COOKIE,
        payload,
        max_age=_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


def read_flashes(request: Request) -> list[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return []
    try:
        data = json.loads(unquote(raw))
        if isinstance(data, list):
            return [
                {"category": str(item.get("category", "info")), "message": str(item.get("message", ""))}
                for item in data
                if item.get("message")
            ]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def attach_flash_clear(response, had_flashes: bool) -> None:
    if had_flashes:
        response.delete_cookie(FLASH_COOKIE)
