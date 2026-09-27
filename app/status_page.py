"""Публичная статус-страница для клиентов: состояние нод, аптайм, нагрузка, инциденты.

Всё считается из снимков поллера (таблица snapshots), в Remnawave публичный запрос
не ходит чаще раза в CACHE_TTL секунд. Наружу не уходит ничего лишнего: ни адресов,
ни IP, ни трафика, ни точного онлайна — только публичное имя, флаг и состояние.

Настройки страницы лежат в kv (status_page) JSON-ом, домен — в .env (STATUS_DOMAIN):
на этом домене панель отдаёт только статус-страницу, см. StatusDomainGuard.
"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user, require_auth
from .config import settings
from .db import get_db, kv_get, kv_set
from .events import add_event
from .poller import node_state
from .remnawave import RWError, rw

router = APIRouter(tags=["status"])

KV_KEY = "status_page"
CACHE_TTL = 30
DAYS = 7
BUCKET = 3 * 3600                     # полоска аптайма: 7 дней по 3 часа = 56 делений
MIN_INCIDENT = 120                    # короче — одиночный промах опроса, не инцидент
DEFAULTS = {"enabled": False, "title": "Состояние серверов", "announce": "", "announce_level": "info",
            "show_load": True, "show_incidents": True, "nodes": {}}

_cache: dict = {"ts": 0.0, "data": None}
_lock = asyncio.Lock()
_last_nodes: list[dict] = []          # последний удачный ответ Remnawave — на случай, если она недоступна


async def load_conf() -> dict:
    raw = await kv_get(KV_KEY)
    try:
        conf = json.loads(raw) if raw else {}
    except ValueError:
        conf = {}
    return {**DEFAULTS, **conf}


def node_visible(n: dict, conf: dict) -> tuple[bool, str]:
    """Показывать ли ноду и под каким именем. Нода без настройки видна, если включена."""
    c = conf["nodes"].get(n.get("uuid")) or {}
    show = c.get("show", not n.get("isDisabled"))
    return bool(show), (c.get("name") or "").strip() or n.get("name") or "?"


def split_flag(name: str) -> tuple[str | None, str]:
    """«🇷🇺 Russia Mobile» → ("RU", "Russia Mobile"). Флаг в начале имени главнее страны ноды:
    нода может стоять в Швеции, а для клиента это «мобильный обход» из России."""
    s = name.lstrip()
    if len(s) >= 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in s[:2]):
        return "".join(chr(ord(ch) - 0x1F1A5) for ch in s[:2]), s[2:].strip()
    return None, name


async def _nodes() -> tuple[list[dict], bool]:
    global _last_nodes
    try:
        _last_nodes = await rw.nodes()
        return _last_nodes, True
    except RWError:
        return _last_nodes, False


async def _history(uuids: set[str], now: int) -> dict[str, dict]:
    """Один проход по снимкам за DAYS дней: полоски, аптайм, пик онлайна, инциденты."""
    start = now - DAYS * 86400
    start -= start % BUCKET  # деления по круглым 3 часам, чтобы подписи были ровными
    n_buckets = -(-(now - start) // BUCKET)
    db = await get_db()
    async with db.execute("SELECT ts, node_uuid, online, connected FROM snapshots WHERE ts>=? "
                          "ORDER BY node_uuid, ts", (start,)) as cur:
        rows = await cur.fetchall()

    out: dict[str, dict] = {}
    for r in rows:
        uuid = r["node_uuid"]
        if uuid not in uuids:
            continue
        h = out.get(uuid)
        if h is None:
            h = out[uuid] = {"up": [0] * n_buckets, "down": [0] * n_buckets, "up24": 0, "all24": 0,
                             "online": [], "incidents": [], "_down_since": None}
        ts, state = r["ts"], r["connected"]
        if state == 1:
            h["online"].append(r["online"])
        # выключенная нода (-1) — плановая остановка, в аптайм не идёт, инцидент закрывает
        if state in (0, 1):
            b = (ts - start) // BUCKET
            h["up" if state == 1 else "down"][b] += 1
            if ts >= now - 86400:
                h["all24"] += 1
                h["up24"] += state == 1
        if state == 0 and h["_down_since"] is None:
            h["_down_since"] = ts
        elif state != 0 and h["_down_since"] is not None:
            if ts - h["_down_since"] >= MIN_INCIDENT:
                h["incidents"].append({"start": h["_down_since"], "end": ts})
            h["_down_since"] = None

    for h in out.values():
        if h["_down_since"] is not None:  # сбой идёт прямо сейчас
            h["incidents"].append({"start": h["_down_since"], "end": None})
        up, down = sum(h["up"]), sum(h["down"])
        h["uptime_7d"] = round(up * 100 / (up + down), 2) if up + down else None
        h["uptime_24h"] = round(h["up24"] * 100 / h["all24"], 2) if h["all24"] else None
        h["bars"] = [None if u + d == 0 else round(u * 100 / (u + d), 1) for u, d in zip(h["up"], h["down"])]
        # «обычная» нагрузка — 95-й перцентиль онлайна за неделю, чтобы один всплеск не задавал шкалу
        on = sorted(h["online"])
        h["peak"] = on[int(len(on) * .95)] if on else 0
    return {"start": start, "bucket": BUCKET, "nodes": out}


LOAD_MEDIUM_MIN = 10                  # меньше людей — всегда «свободно», сколько бы ни было обычно
LOAD_HIGH_MIN = 20


def load_level(online: int, peak: int) -> str:
    """Обычный вечерний пик — это ещё не нагрузка: «средняя» — дошли до него,
    «высокая» — вышли за него. Плюс абсолютный минимум, чтобы пара человек не щёлкала значок."""
    if online > peak and online >= LOAD_HIGH_MIN:
        return "high"
    return "medium" if online >= peak and online >= LOAD_MEDIUM_MIN else "low"


async def build() -> dict:
    conf = await load_conf()
    now = int(time.time())
    nodes, fresh = await _nodes()
    shown = []
    for n in nodes:
        show, name = node_visible(n, conf)
        if show:
            shown.append((n, name))
    hist = await _history({n.get("uuid") for n, _ in shown}, now)

    items, incidents = [], []
    for n, name in shown:
        h = hist["nodes"].get(n.get("uuid")) or {}
        cc, name = split_flag(name)
        name = name or n.get("name") or "?"  # в имени был только флаг
        state = node_state(n)
        item = {
            "name": name,
            "country": cc or (n.get("countryCode") or "").upper()[:2],
            "state": {1: "up", 0: "down", -1: "maintenance"}[state],
            "uptime_24h": h.get("uptime_24h"), "uptime_7d": h.get("uptime_7d"),
            "bars": h.get("bars") or [],
        }
        if conf["show_load"] and state == 1:
            item["load"] = load_level(int(n.get("usersOnline") or 0), h.get("peak") or 0)
        items.append(item)
        if conf["show_incidents"]:
            incidents += [{"node": name, **i} for i in h.get("incidents", [])]

    active = [i for i in items if i["state"] != "maintenance"]
    down = sum(i["state"] == "down" for i in active)
    overall = "up" if not down else ("major" if down * 2 >= len(active) else "partial")
    incidents.sort(key=lambda i: (i["end"] is None, i["start"]), reverse=True)  # идущие — первыми
    return {
        "title": conf["title"], "overall": overall, "updated": now, "fresh": fresh,
        "announce": {"text": conf["announce"], "level": conf["announce_level"]} if conf["announce"].strip() else None,
        "start": hist["start"], "bucket": hist["bucket"],
        "nodes": items, "incidents": incidents[:15] if conf["show_incidents"] else None,
    }


@router.get("/api/public/status")
async def public_status(req: Request):
    conf = await load_conf()
    if not conf["enabled"] and not current_user(req):  # выключенную видит только админ — как превью
        raise HTTPException(404, "Не найдено")
    async with _lock:  # толпа клиентов после сбоя получает один и тот же расчёт
        if _cache["data"] is None or time.monotonic() - _cache["ts"] > CACHE_TTL:
            _cache["data"], _cache["ts"] = await build(), time.monotonic()
    return _cache["data"]


def drop_cache():
    _cache["data"] = None


# ---------------- настройки (админка)
@router.get("/api/status-page")
async def status_page_get(_: str = Depends(require_auth)):
    conf = await load_conf()
    nodes = []
    try:
        for n in await rw.nodes():
            show, _name = node_visible(n, conf)
            nodes.append({"uuid": n.get("uuid"), "name": n.get("name"), "country": n.get("countryCode"),
                          "state": node_state(n), "show": show,
                          "public_name": (conf["nodes"].get(n.get("uuid")) or {}).get("name", "")})
    except RWError as e:
        return {**conf, "status_domain": settings.status_domain, "nodes": [], "error": str(e)}
    return {**conf, "status_domain": settings.status_domain, "nodes": nodes}


class NodeConf(BaseModel):
    show: bool
    name: str = Field("", max_length=40)


class StatusPageIn(BaseModel):
    enabled: bool
    title: str = Field(min_length=1, max_length=60)
    announce: str = Field("", max_length=500)
    announce_level: str = "info"
    show_load: bool = True
    show_incidents: bool = True
    status_domain: str = Field("", max_length=253)
    nodes: dict[str, NodeConf] = {}


@router.put("/api/status-page")
async def status_page_put(body: StatusPageIn, user: str = Depends(require_auth)):
    if body.announce_level not in ("info", "warn", "bad"):
        raise HTTPException(400, "announce_level: info, warn или bad")
    domain = body.status_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if domain and domain == settings.panel_domain.lower():
        raise HTTPException(400, "Домен статус-страницы должен отличаться от домена панели — иначе админка закроется")
    if domain and domain == settings.sub_domain:
        raise HTTPException(400, "Этот домен уже занят прослойкой подписок")
    conf = body.model_dump(exclude={"status_domain"})
    conf["title"], conf["announce"] = conf["title"].strip(), conf["announce"].strip()
    await kv_set(KV_KEY, json.dumps(conf, ensure_ascii=False))
    if domain != settings.status_domain:
        settings.update({"STATUS_DOMAIN": domain})
    drop_cache()
    await add_event("action", "info", f"{user}: изменена статус-страница")
    return {"ok": True}


# ---------------- отдельный домен
STATUS_PATHS = {"/", "/status", "/api/public/status", "/static/status.css", "/static/status.js",
                "/static/logo.svg", "/static/favicon-32.png", "/static/apple-touch-icon.png"}


class StatusDomainGuard:
    """ASGI-middleware: на STATUS_DOMAIN доступна только статус-страница. Чистый ASGI,
    а не @app.middleware, — чтобы закрыть и WebSocket (терминал, логи)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        domain = settings.status_domain
        if domain and scope["type"] in ("http", "websocket"):
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1").split(":")[0].lower()
            if host == domain and scope["path"] not in STATUS_PATHS:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": "Не найдено".encode()})
                return
        await self.app(scope, receive, send)


def is_status_host(req: Request) -> bool:
    domain = settings.status_domain
    return bool(domain) and req.headers.get("host", "").split(":")[0].lower() == domain
