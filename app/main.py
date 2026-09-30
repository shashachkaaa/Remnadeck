import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from . import poller, ssh, quota, routes_infra, routes_live, routes_quota, routes_rw, routes_users, status_page, sub_proxy, automations, routes_auto, bans, version, bedolaga
from .version import VERSION
from .auth import (COOKIE, SESSION_TTL, check_code, current_user, fail, forget, require_auth,
                   set_session, setup_or_auth, throttle)
from .config import ENV_PATH, bootstrap, hash_password, settings, verify_password
from .db import get_db
from .events import add_event
from .remnawave import Remnawave, RWError, rw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    rw.reconfigure()
    await get_db()
    await routes_infra.seed_templates()
    task = asyncio.create_task(poller.loop_forever())
    qtask = asyncio.create_task(quota.loop_forever())
    atask = asyncio.create_task(automations.loop_forever())
    yield
    task.cancel()
    qtask.cancel()
    atask.cancel()


app = FastAPI(title="RemnaDeck", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.add_middleware(status_page.StatusDomainGuard)
app.add_middleware(sub_proxy.SubDomainProxy)   # домен подписок целиком уходит в прокси


app.include_router(routes_rw.router)
app.include_router(routes_users.router)
app.include_router(routes_infra.router)
app.include_router(routes_live.router)
app.include_router(routes_quota.router)
app.include_router(status_page.router)
app.include_router(sub_proxy.router)
app.include_router(routes_auto.router)
app.include_router(bans.router)
app.include_router(version.router)
app.include_router(bedolaga.router)


@app.exception_handler(RWError)
async def rw_error(_: Request, exc: RWError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------- первый запуск
@app.get("/api/state")
async def state(req: Request):
    out = {"setup": not settings.admin_ready, "user": current_user(req), "version": VERSION}
    if out["setup"]:
        out["remnawave_url"] = settings.rw_url  # подсказка для мастера
    return out


class Code(BaseModel):
    code: str


@app.post("/api/setup/check")
async def setup_check(body: Code, req: Request):
    check_code(req, body.code)
    return {"ok": True}


class SetupIn(BaseModel):
    code: str
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    remnawave_url: str = ""
    remnawave_token: str = ""
    tg_bot_token: str = ""
    tg_chat_id: str = ""


@app.post("/api/setup")
async def setup(body: SetupIn, req: Request, resp: Response):
    check_code(req, body.code)
    changes = {
        "ADMIN_USERNAME": body.username.strip(),
        "ADMIN_PASSWORD_HASH": hash_password(body.password),
        "SETUP_TOKEN": None,
        "TG_BOT_TOKEN": body.tg_bot_token.strip(),
        "TG_CHAT_ID": body.tg_chat_id.strip(),
    }
    if body.remnawave_url.strip():
        changes["REMNAWAVE_URL"] = body.remnawave_url.strip()
    if body.remnawave_token.strip():
        changes["REMNAWAVE_TOKEN"] = body.remnawave_token.strip()
    settings.update(changes)
    await _apply_rw()
    set_session(resp)
    await add_event("action", "info", f"{settings.admin_user}: панель настроена")
    return {"ok": True}


# ---------------- вход
class Login(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: Login, req: Request, resp: Response):
    if not settings.admin_ready:
        raise HTTPException(409, "Сначала пройди первичную настройку")
    throttle(req)
    user_ok = secrets.compare_digest(body.username, settings.admin_user)
    pass_ok = verify_password(body.password, settings.admin_hash)
    if not (user_ok and pass_ok):
        fail(req, "Неверный логин или пароль")
    forget(req)
    set_session(resp)
    return {"ok": True}


@app.post("/api/logout")
async def logout(resp: Response):
    resp.delete_cookie(COOKIE)
    return {"ok": True}


# ---------------- helpers
def pick(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


async def traffic_hourly(hours: int = 24) -> list[dict]:
    """Дельты счётчиков трафика нод по часам (счётчик сбросился → дельта 0)."""
    db = await get_db()
    start = int(time.time()) - hours * 3600
    async with db.execute(
            "SELECT ts, node_uuid, traffic FROM snapshots WHERE ts>=? ORDER BY node_uuid, ts",
            (start - 3600,)) as cur:
        rows = await cur.fetchall()
    buckets = [0] * hours
    prev: dict[str, int] = {}
    for r in rows:
        p = prev.get(r["node_uuid"])
        prev[r["node_uuid"]] = r["traffic"]
        if p is None or r["ts"] < start:
            continue
        delta = r["traffic"] - p
        if delta > 0:
            buckets[min(hours - 1, (r["ts"] - start) // 3600)] += delta
    return [{"hour_ts": start + i * 3600, "bytes": b} for i, b in enumerate(buckets)]


# ---------------- обзор
@app.get("/api/overview")
async def overview(_: str = Depends(require_auth)):
    db = await get_db()
    errors, nodes, stats = [], [], {}
    try:
        nodes = await rw.nodes()
    except RWError as e:
        errors.append(str(e))
    try:
        stats = await rw.stats()
    except RWError as e:
        if str(e) not in errors:
            errors.append(str(e))

    active = [n for n in nodes if not n.get("isDisabled")]
    online = [n for n in active if n.get("isConnected")]
    online_sum = sum(int(n.get("usersOnline") or 0) for n in online)
    top = sorted(online, key=lambda n: int(n.get("usersOnline") or 0), reverse=True)[:6]

    day_ago = int(time.time()) - 86400
    async with db.execute("SELECT COUNT(*) c FROM events WHERE level='bad' AND ts>=?", (day_ago,)) as cur:
        incidents = (await cur.fetchone())["c"]
    async with db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 7") as cur:
        events = [dict(r) for r in await cur.fetchall()]
    async with db.execute("SELECT COUNT(*) t, SUM(tcp_ok AND tls_ok) ok FROM health") as cur:
        h = await cur.fetchone()

    return {
        "errors": errors,
        "nodes": {"online": len(online), "total": len(active)},
        "users": {
            "active": pick(stats, "users", "statusCounts", "ACTIVE"),
            "total": pick(stats, "users", "totalUsers"),
            "online_now": pick(stats, "onlineStats", "onlineNow", default=online_sum),
            "online_day": pick(stats, "onlineStats", "lastDay"),
        },
        "incidents_24h": incidents,
        "events": events,
        "traffic": await traffic_hourly(),
        "top_nodes": [{"name": n.get("name"), "online": int(n.get("usersOnline") or 0),
                       "share": round(int(n.get("usersOnline") or 0) * 100 / online_sum) if online_sum else 0}
                      for n in top],
        "health": {"ok": h["ok"] or 0, "total": h["t"] or 0},
    }


@app.get("/api/status")
async def status(_: str = Depends(require_auth)):
    """Лёгкий статус для боковой панели."""
    db = await get_db()
    async with db.execute("SELECT COUNT(*) c FROM events WHERE level='bad' AND ts>=?",
                          (int(time.time()) - 86400,)) as cur:
        incidents = (await cur.fetchone())["c"]
    t0 = time.monotonic()
    try:
        await rw.health()
        rw_state = {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except RWError as e:
        rw_state = {"ok": False, "error": str(e)}
    nodes = None
    try:
        nodes = len(await rw.nodes())
    except RWError:
        pass
    running = [j for j in ssh.JOBS.values() if j.status == "running"]
    return {"remnawave": rw_state, "incidents_24h": incidents, "nodes": nodes, "user": settings.admin_user,
            "jobs_running": len(running), "job_last": running[-1].id if running else None,
            "job_title": running[-1].title if running else None,
            "bedolaga": bedolaga.present()}   # пункт «Bedolaga» в меню — только если бот или кабинет есть на сервере


# ---------------- ноды
def node_live(n: dict) -> dict:
    """Живые метрики ноды из system.stats (3.4+): память, скорость интерфейса, аптайм."""
    system = n.get("system") or {}
    info, stats = system.get("info") or {}, system.get("stats") or {}
    iface = stats.get("interface") or {}
    total = info.get("memoryTotal") or 0
    used = stats.get("memoryUsed") or 0
    return {
        "mem_pct": round(used * 100 / total) if total else None,
        "mem_used": used, "mem_total": total,
        "rx_bps": iface.get("rxBytesPerSec"), "tx_bps": iface.get("txBytesPerSec"),
        "uptime": n.get("xrayUptime") or stats.get("uptime"),
        "cpus": info.get("cpus"),
    }


@app.get("/api/nodes")
async def nodes_list(_: str = Depends(require_auth)):
    out = []
    for n in await rw.nodes():
        out.append({
            "uuid": n.get("uuid"), "name": n.get("name"), "address": n.get("address"),
            "country": n.get("countryCode"), "state": poller.node_state(n),
            "online": int(n.get("usersOnline") or 0),
            "traffic": int(n.get("trafficUsedBytes") or 0),
            "traffic_limit": n.get("trafficLimitBytes"),
            "xray": (n.get("versions") or {}).get("xray") or n.get("xrayVersion"),
            "node_version": (n.get("versions") or {}).get("node") or n.get("nodeVersion"),
            "message": n.get("lastStatusMessage"),
            **node_live(n),
            "pos": n.get("viewPosition"),
        })
    # порядок как в Remnawave: по viewPosition, без своей сортировки
    if any(x["pos"] is not None for x in out):
        return sorted(out, key=lambda x: (x["pos"] is None, x["pos"] or 0))
    return sorted(out, key=lambda x: (x["state"] != 1, x["name"] or ""))


@app.post("/api/nodes/{uuid}/{action}")
async def node_action(uuid: str, action: str, user: str = Depends(require_auth)):
    if action not in ("restart", "enable", "disable", "reset-traffic"):
        raise HTTPException(400, "Неизвестное действие")
    await rw.node_action(uuid, action)
    names = {"restart": "перезапуск", "enable": "включение", "disable": "выключение",
             "reset-traffic": "сброс трафика"}
    await add_event("action", "info", f"{user}: {names[action]} ноды {uuid[:8]}")
    return {"ok": True}


# ---------------- пользователи
def user_row(u: dict) -> dict:
    traffic = u.get("userTraffic") or {}
    return {
        # в разных версиях панели идентификатор называется по-разному
        "uuid": u.get("uuid") or u.get("userUuid") or u.get("id") or "",
        "username": u.get("username"), "status": u.get("status"),
        "used": traffic.get("usedTrafficBytes", u.get("usedTrafficBytes")) or 0,
        "limit": u.get("trafficLimitBytes") or 0, "expire_at": u.get("expireAt"),
        "online_at": traffic.get("onlineAt", u.get("onlineAt")),
        "telegram_id": u.get("telegramId"), "short_uuid": u.get("shortUuid"),
    }


@app.get("/api/users")
async def users_list(q: str = "", status: str = "", page: int = 1, _: str = Depends(require_auth)):
    rows = [user_row(u) for u in await rw.users_all()]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if status:
        rows = [r for r in rows if r["status"] == status]
    if q:
        ql = q.lower()
        rows = [r for r in rows
                if ql in f"{r['username']} {r['telegram_id']} {r['short_uuid']} {r['uuid']}".lower()]
    per, page = 50, max(1, page)
    return {"total": len(rows), "all": sum(counts.values()), "counts": counts, "page": page,
            "pages": max(1, -(-len(rows) // per)), "items": rows[(page - 1) * per: page * per]}


@app.post("/api/users/{uuid}/{action}")
async def user_action(uuid: str, action: str, user: str = Depends(require_auth)):
    if action not in ("enable", "disable", "reset-traffic"):
        raise HTTPException(400, "Неизвестное действие")
    await rw.user_action(uuid, action)
    names = {"enable": "включение", "disable": "отключение", "reset-traffic": "сброс трафика"}
    await add_event("action", "info", f"{user}: {names[action]} пользователя {uuid[:8]}")
    return {"ok": True}


# ---------------- здоровье пути
@app.get("/api/health")
async def health_list(_: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM health ORDER BY (tcp_ok AND tls_ok), label") as cur:
        return [dict(r) for r in await cur.fetchall()]


@app.post("/api/health/run")
async def health_run(_: str = Depends(require_auth)):
    await poller.run_checks()
    return {"ok": True}


# ---------------- события
@app.get("/api/events")
async def events_list(level: str = "", limit: int = 300, _: str = Depends(require_auth)):
    db = await get_db()
    sql, args = "SELECT * FROM events", []
    if level:
        sql += " WHERE level=?"
        args.append(level)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 1000))
    async with db.execute(sql, args) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ---------------- настройки (всё пишется в .env)
async def _apply_rw():
    old = rw.reconfigure()
    if old:
        await old.aclose()


def mask(v: str) -> str:
    return f"••••{v[-4:]}" if len(v) > 8 else ("задан" if v else "")


@app.get("/api/settings")
async def settings_get(_: str = Depends(require_auth)):
    return {
        "env_path": ENV_PATH, "version": VERSION,
        "panel_domain": settings.panel_domain,
        "remnawave_url": settings.rw_url, "remnawave_token": mask(settings.rw_token),
        "remnawave_internal": settings.rw_internal_mode, "remnawave_verify_tls": settings.rw_verify_tls,
        "tg_bot_token": mask(settings.tg_token), "tg_chat_id": settings.tg_chat,
        "poll_interval": settings.poll_interval, "check_interval": settings.check_interval,
        "username": settings.admin_user,
    }


class SettingsIn(BaseModel):
    # None — не менять; "" у токенов — очистить
    panel_domain: str | None = None
    remnawave_url: str | None = None
    remnawave_token: str | None = None
    remnawave_internal: str | None = None
    remnawave_verify_tls: bool | None = None
    tg_bot_token: str | None = None
    tg_chat_id: str | None = None
    poll_interval: int | None = Field(None, ge=15, le=3600)
    check_interval: int | None = Field(None, ge=60, le=86400)


KEYS = {"panel_domain": "PANEL_DOMAIN", "remnawave_url": "REMNAWAVE_URL",
        "remnawave_token": "REMNAWAVE_TOKEN", "remnawave_internal": "REMNAWAVE_INTERNAL",
        "remnawave_verify_tls": "REMNAWAVE_VERIFY_TLS", "tg_bot_token": "TG_BOT_TOKEN",
        "tg_chat_id": "TG_CHAT_ID", "poll_interval": "POLL_INTERVAL", "check_interval": "CHECK_INTERVAL"}


@app.put("/api/settings")
async def settings_put(body: SettingsIn, user: str = Depends(require_auth)):
    data = body.model_dump(exclude_none=True)
    if "remnawave_internal" in data and data["remnawave_internal"] not in ("auto", "true", "false"):
        raise HTTPException(400, "remnawave_internal: auto, true или false")
    if "remnawave_url" in data and not data["remnawave_url"].startswith(("http://", "https://")):
        raise HTTPException(400, "Адрес Remnawave должен начинаться с http:// или https://")
    changes = {KEYS[k]: (str(v).lower() if isinstance(v, bool) else str(v).strip()) for k, v in data.items()}
    if not changes:
        return {"ok": True}
    settings.update(changes)
    if any(k.startswith("remnawave") for k in data):
        await _apply_rw()
    await add_event("action", "info", f"{user}: изменены настройки ({', '.join(changes)})")
    return {"ok": True}


class RWTest(BaseModel):
    remnawave_url: str
    remnawave_token: str = ""
    remnawave_internal: str = "auto"
    remnawave_verify_tls: bool = True


@app.post("/api/settings/test-remnawave")
async def test_remnawave(body: RWTest, who: str = Depends(setup_or_auth)):
    url = body.remnawave_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Адрес должен начинаться с http:// или https://")
    token = body.remnawave_token.strip() or (settings.rw_token if who != "setup" else "")
    if not token:
        raise HTTPException(400, "Укажи API-токен")
    internal = url.startswith("http://") if body.remnawave_internal == "auto" else body.remnawave_internal == "true"
    client = Remnawave(url, token, internal, body.remnawave_verify_tls)
    t0 = time.monotonic()
    try:
        nodes = await client.nodes(ttl=0)
    finally:
        await client.client.aclose()
    return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000), "nodes": len(nodes)}


class TGTest(BaseModel):
    tg_bot_token: str = ""
    tg_chat_id: str = ""


@app.post("/api/settings/test-telegram")
async def test_telegram(body: TGTest, who: str = Depends(setup_or_auth)):
    token = body.tg_bot_token.strip() or (settings.tg_token if who != "setup" else "")
    chat = body.tg_chat_id.strip() or settings.tg_chat
    if not token or not chat:
        raise HTTPException(400, "Нужны токен бота и ID чата")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat, "text": "✅ RemnaDeck: уведомления работают"})
    except httpx.HTTPError:
        raise HTTPException(502, "Telegram не отвечает с этого сервера")
    if r.status_code != 200:
        raise HTTPException(400, f"Telegram отказал: {r.json().get('description', r.status_code)}")
    return {"ok": True}


class AccountIn(BaseModel):
    current_password: str
    username: str = Field(min_length=3, max_length=32)
    new_password: str = ""


@app.post("/api/settings/account")
async def account(body: AccountIn, req: Request, resp: Response, _: str = Depends(require_auth)):
    throttle(req)
    if not verify_password(body.current_password, settings.admin_hash):
        fail(req, "Текущий пароль неверный")
    changes = {"ADMIN_USERNAME": body.username.strip(), "SECRET_KEY": secrets.token_hex(32)}
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(400, "Новый пароль — минимум 8 символов")
        changes["ADMIN_PASSWORD_HASH"] = hash_password(body.new_password)
    settings.update(changes)  # новый SECRET_KEY выкидывает все остальные сессии
    set_session(resp)
    await add_event("action", "info", f"{settings.admin_user}: изменена учётная запись")
    return {"ok": True}


# PWA: манифест и service worker должны лежать в корне, иначе scope будет /static/
def _asset_version() -> str:
    """Хеш всей статики. Меняется сам при любой правке файлов — руками ?v= больше не поднимаем.
    От него зависят ссылки в index.html и имя кеша в sw.js: новый sw.js — браузер ставит
    новый service worker, выкидывает старый кеш, и установленное приложение обновляется."""
    h = hashlib.sha256()
    for p in sorted(STATIC.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(STATIC).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


ASSET_VER = _asset_version()
NO_CACHE = {"Cache-Control": "no-cache"}


def _versioned(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8").replace("__VER__", ASSET_VER)


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json",
                        headers=NO_CACHE)


@app.get("/sw.js")
async def service_worker():
    return Response(_versioned("sw.js"), media_type="application/javascript", headers=NO_CACHE)


@app.get("/status")
async def status_html(req: Request):
    """Статус-страница для клиентов. На STATUS_DOMAIN она же отдаётся с корня (см. spa)."""
    if not (await status_page.load_conf())["enabled"] and not current_user(req):
        raise HTTPException(404, "Не найдено")
    return Response(_versioned("status.html"), media_type="text/html", headers=NO_CACHE)


@app.get("/{path:path}")
async def spa(path: str, req: Request):
    if path.startswith("api/"):
        raise HTTPException(404, "Не найдено")
    if status_page.is_status_host(req):
        return await status_html(req)
    # страницу браузер обязан перепроверять каждый раз: в ней ссылки ?v=… на свежие
    # стили и скрипты. Без этого Chrome эвристически держал старую копию днями
    return Response(_versioned("index.html"), media_type="text/html", headers=NO_CACHE)
