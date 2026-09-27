"""Сессии и защита от перебора. Вынесено из main.py, чтобы роутеры
(ноды, хосты, серверы) могли переиспользовать зависимости без циклов импорта."""
import secrets
import time

from fastapi import Header, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

COOKIE = "rd_session"
SESSION_TTL = 7 * 86400
_attempts: dict[str, list[float]] = {}


def client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")


def throttle(req: Request):
    now = time.time()
    recent = [t for t in _attempts.get(client_ip(req), []) if now - t < 600]
    _attempts[client_ip(req)] = recent
    if len(recent) >= 8:
        raise HTTPException(429, "Слишком много попыток. Подожди 10 минут")


def fail(req: Request, msg: str):
    _attempts.setdefault(client_ip(req), []).append(time.time())
    raise HTTPException(401, msg)


def forget(req: Request):
    _attempts.pop(client_ip(req), None)


def signer():
    return URLSafeTimedSerializer(settings.secret_key, salt="session")


def set_session(resp: Response):
    resp.set_cookie(COOKIE, signer().dumps({"u": settings.admin_user}), max_age=SESSION_TTL,
                    httponly=True, secure=settings.cookie_secure, samesite="lax")


def current_user(req: Request) -> str | None:
    token = req.cookies.get(COOKIE)
    if not token or not settings.admin_ready:
        return None
    try:
        user = signer().loads(token, max_age=SESSION_TTL)["u"]
    except (BadSignature, KeyError, TypeError):
        return None
    return user if user == settings.admin_user else None


def require_auth(req: Request) -> str:
    user = current_user(req)
    if not user:
        raise HTTPException(401, "Нужен вход")
    return user


def check_code(req: Request, code: str):
    if settings.admin_ready:
        raise HTTPException(409, "Панель уже настроена")
    throttle(req)
    if not secrets.compare_digest(code.strip().lower(), settings.setup_token.lower()):
        fail(req, "Неверный код. Он в выводе install.sh, в .env (SETUP_TOKEN) и в docker logs remnadeck")


def setup_or_auth(req: Request, x_setup_code: str = Header("")):
    if not settings.admin_ready:
        check_code(req, x_setup_code)
        return "setup"
    return require_auth(req)
