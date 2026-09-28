"""Прослойка подписок: клиент → Caddy → RemnaDeck → subscription-page → Remnawave.

На SUB_DOMAIN панель проксирует всё в subscription-page и на лету правит ответы
клиентам — ничего не записывая в Remnawave. Тексты живут в Remnawave (объявление,
названия и описания хостов), RemnaDeck только подставляет в них свои значения:

  {{RD_QUOTA}}          счётчик по шаблону правила («120 из 300 ГБ» / «лимит исчерпан · …»)
  {{RD_QUOTA_USED}}  {{RD_QUOTA_LIMIT}}  {{RD_QUOTA_LEFT}}  {{RD_QUOTA_PERCENT}}  {{RD_QUOTA_RESET}}
  {{RD_MESSAGE}}        сообщения автоматизаций (нет в тексте — встают в начало плашки)
  …:Имя правила         {{RD_QUOTA_LEFT:Обход}} — значение конкретного правила

Remnawave неизвестные ей подстановки оставляет как есть. Строка заголовка, где все
подстановки пустые (пользователь не под квотой), убирается целиком; хост — по настройке.
Плюс пометка хостов упавших нод и скрытие хостов по автоматизациям.

Ссылки (base64 / plain) и Xray JSON разбираются; в Mihomo и sing-box подстановки меняются
прямо в тексте, а скрыть хост там нельзя. Любая ошибка → клиент получает исходный ответ.
Скрыть хост в подписке ≠ закрыть доступ: доступ режут сквады.
"""
import asyncio
import base64
import http.cookiejar
import ipaddress
import json
import logging
import re
import time
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response

from . import automations, bans, quota
from .auth import require_auth
from .config import settings
from .db import get_db, kv_get, kv_set
from .events import add_event
from .poller import node_state
from .remnawave import RWError, rw
from .routes_rw import CLEAN

log = logging.getLogger("sub_proxy")
router = APIRouter(prefix="/api/sub-proxy", tags=["sub-proxy"])

KV_KEY = "sub_proxy"
DEFAULTS = {"enabled": False, "hide_info_without_quota": True, "mark_down": True, "down_prefix": "⚠️ ",
            "down_to_end": False, "hide_disabled": False}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "trailers", "transfer-encoding", "upgrade", "content-length", "content-encoding", "host"}
SHORT_UUID = re.compile(r"^/([A-Za-z0-9_-]{6,64})(?:/[A-Za-z0-9_-]{1,32})?/?$")
TOKEN = re.compile(r"\{\{RD_(QUOTA_USED|QUOTA_LIMIT|QUOTA_LEFT|QUOTA_PERCENT|QUOTA_RESET|QUOTA|MESSAGE)"
                   r"(?::([^{}]*))?\}\}")
URI_SAFE = "-_.!~*'()"  # как encodeURIComponent в Remnawave

_client: httpx.AsyncClient | None = None
_conf: dict = {"ts": 0.0, "v": dict(DEFAULTS)}
_users: dict = {"ts": 0.0, "by_short": {}, "task": None}
_rules: dict = {"ts": 0.0, "v": [], "held": {}}
stats = {"since": time.time(), "requests": 0, "subs": 0, "changed": 0, "errors": 0, "banned": 0,
         "upstream_errors": 0, "last_request": 0, "last_error": "", "ms": []}


# ---------------- настройки
async def load_conf(ttl: float = 10) -> dict:
    if time.monotonic() - _conf["ts"] > ttl:
        raw = await kv_get(KV_KEY)
        try:
            saved = json.loads(raw) if raw else {}
        except ValueError:
            saved = {}
        _conf["v"] = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
        _conf["ts"] = time.monotonic()
    return _conf["v"]


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # клиент общий на всех — cookie из ответов (session subscription-page) хранить нельзя,
        # иначе они уедут в запросы других пользователей. Cookie клиента идут заголовком как есть
        jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False, cookies=jar,
                                    limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))
    return _client


# ---------------- данные: пользователи, квоты
async def _refresh_users():
    try:
        users = await rw.users_all()
        _users["by_short"] = {u.get("shortUuid"): u for u in users if u.get("shortUuid")}
        _users["ts"] = time.monotonic()
    except RWError as e:
        log.warning("users refresh: %s", e)


async def user_by_short(short: str) -> dict | None:
    """Индекс shortUuid → пользователь. Устаревший индекс обновляется в фоне, чтобы запрос
    подписки не ждал выгрузку всех пользователей; новичка, которого нет в индексе, ждём."""
    stale = time.monotonic() - _users["ts"] > 90
    task = _users["task"]
    if (stale or short not in _users["by_short"]) and (task is None or task.done()):
        _users["task"] = task = asyncio.create_task(_refresh_users())
    if short not in _users["by_short"] and task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), 5)
        except asyncio.TimeoutError:
            pass
    return _users["by_short"].get(short)


async def active_rules() -> tuple[list[dict], dict[int, set[int]]]:
    """Правила квот не в тестовом режиме + кого держат автоматизации (кеш 30 с)."""
    if time.monotonic() - _rules["ts"] > 30:
        db = await get_db()
        async with db.execute("SELECT * FROM quota_rules WHERE enabled=1 AND mode!='dry'") as cur:
            _rules["v"] = [dict(r) for r in await cur.fetchall()]
        _rules["held"] = {r["id"]: await automations.held_users(r["id"]) for r in _rules["v"]}
        _rules["ts"] = time.monotonic()
    return _rules["v"], _rules["held"]


async def quota_info(u: dict) -> list[dict]:
    """Значения квот пользователя — по тем же данным, что считает движок квот."""
    rules, held = await active_rules()
    if not rules or not u.get("id"):
        return []
    db = await get_db()
    async with db.execute("SELECT * FROM quota_users WHERE user_id=?", (u["id"],)) as cur:
        rows = {r["rule_id"]: dict(r) for r in await cur.fetchall()}
    squads = set(quota.squad_ids(u))
    out = []
    for rule in rules:
        st = rows.get(rule["id"])
        if not st or (st["updated"] or 0) < (rule["last_run"] or 0) - 120:
            continue  # в последнем прогоне правило его не считало — он не под квотой
        moved = bool(st["moved"])
        exempt = {int(x) for x in json.loads(rule.get("exempt") or "[]")}
        in_scope = rule["full_squad"] in squads or moved or u["id"] in held.get(rule["id"], set())
        if not in_scope or (u["id"] in exempt and not moved):
            continue
        _, reset, _ = quota.period_of(rule)
        limit, used = int(rule["limit_bytes"] or 0), int(st["used"] or 0)
        tpl = (rule["desc_over"] or quota.DESC_OVER) if moved else (rule["desc_ok"] or quota.DESC_OK)
        out.append({"name": rule["name"], "text": quota.render(tpl, used, limit, reset),
                    "used": quota.gb(used), "limit": quota.gb(limit), "left": quota.gb(max(0, limit - used)),
                    "percent": str(min(100, round(used * 100 / limit))) if limit else "0",
                    "reset": reset.strftime("%d.%m")})
    return out


class Ctx:
    """Всё, что подставляется в подписку конкретного пользователя."""
    KEYS = {"QUOTA": "text", "QUOTA_USED": "used", "QUOTA_LIMIT": "limit", "QUOTA_LEFT": "left",
            "QUOTA_PERCENT": "percent", "QUOTA_RESET": "reset"}

    def __init__(self, quotas: list[dict], messages: list[str], hide: set[str] = frozenset()):
        self.quotas, self.hide = quotas, set(hide)
        self.messages = [self.render(m)[0] for m in messages]

    def value(self, key: str, arg: str | None) -> str:
        if key == "MESSAGE":
            return "\n".join(getattr(self, "messages", []))
        q = next((x for x in self.quotas if x["name"].casefold() == arg.strip().casefold()), None) if arg \
            else (self.quotas[0] if self.quotas else None)
        return q[self.KEYS[key]] if q else ""

    def render(self, text: str) -> tuple[str, bool]:
        """(текст с подстановками, была ли хоть одна непустая)."""
        filled = False

        def one(m):
            nonlocal filled
            v = self.value(m.group(1), m.group(2))
            filled |= bool(v)
            return v
        return TOKEN.sub(one, text), filled

    def render_lines(self, text: str) -> str:
        """Для заголовков: строка, где все подстановки пустые, убирается целиком."""
        out = []
        for line in text.split("\n"):
            if TOKEN.search(line):
                line, filled = self.render(line)
                if not filled:
                    continue
            out.append(line)
        return "\n".join(out)


_dns: dict[str, tuple[float, set[str]]] = {}
_states: dict = {"ts": 0.0, "v": {}}


async def resolve(name: str) -> set[str]:
    """IP адреса (кеш 10 минут). У хоста в Remnawave часто домен, а у ноды — IP, или наоборот."""
    name = (name or "").lower()
    hit = _dns.get(name)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        ipaddress.ip_address(name)
        ips = {name}
    except ValueError:
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(name, None), 3)
            ips = {i[4][0] for i in infos}
        except (OSError, asyncio.TimeoutError):
            ips = set()
    _dns[name] = (time.monotonic(), ips)
    return ips


async def host_states(ttl: float = 15) -> dict[str, str]:
    """Название хоста → up / down / disabled. Ноды хоста — из поля nodes, а если оно пустое —
    ноды с тем же адресом или IP. Хосты с подстановками в названии не сопоставить."""
    if time.monotonic() - _states["ts"] < ttl:
        return _states["v"]
    nodes, hosts = await rw.nodes(), await rw.hosts()
    by_uuid = {n.get("uuid"): n for n in nodes}
    names = {(x.get("address") or "").lower() for x in nodes + hosts} - {""}
    ips = dict(zip(names, await asyncio.gather(*(resolve(x) for x in names))))
    by_ip: dict[str, list[dict]] = {}
    for n in nodes:
        for ip in ips.get((n.get("address") or "").lower(), ()):
            by_ip.setdefault(ip, []).append(n)
    linked: dict[str, dict] = {}
    for h in hosts:
        remark = h.get("remark") or ""
        if h.get("isDisabled") or not remark or "{{" in remark:
            continue
        ns = [by_uuid[x] for x in (h.get("nodes") or []) if x in by_uuid] \
            or [n for ip in ips.get((h.get("address") or "").lower(), ()) for n in by_ip.get(ip, [])]
        for n in ns:
            linked.setdefault(remark, {})[n.get("uuid")] = n
    out = {}
    for remark, ns in linked.items():
        states = [node_state(n) for n in ns.values()]
        out[remark] = "disabled" if all(s == -1 for s in states) else \
            "up" if any(s == 1 for s in states) else "down"
    _states["ts"], _states["v"] = time.monotonic(), out
    return out


# ---------------- правка названий
class Plan:
    """Что сделать с каждым хостом подписки: новое имя, выкинуть, унести в конец."""

    def __init__(self, conf: dict, ctx: Ctx, states: dict[str, str]):
        self.conf, self.ctx, self.states = conf, ctx, states
        self.changed = False

    def apply(self, remark: str) -> tuple[str | None, bool]:
        """(новое имя или None — убрать, уносить ли в конец)."""
        if remark in self.ctx.hide:
            self.changed = True
            return None, False
        new, last = remark, False
        if TOKEN.search(remark):
            new, filled = self.ctx.render(remark)
            if not filled and self.conf["hide_info_without_quota"]:
                self.changed = True
                return None, False
        state = self.states.get(remark)
        if state == "disabled" and self.conf["hide_disabled"]:
            self.changed = True
            return None, False
        if state == "down" and self.conf["mark_down"]:
            prefix = self.conf["down_prefix"]
            if prefix and not new.startswith(prefix):
                new = prefix + new
            last = self.conf["down_to_end"]
        self.changed |= new != remark or last
        return new, last

    def run(self, items: list, get, put) -> list:
        """Общий проход для любого формата: get(item) → имя, put(item, имя) → item."""
        keep, tail = [], []
        for it in items:
            remark = get(it)
            if remark is None:
                keep.append(it)
                continue
            new, last = self.apply(remark)
            if new is None:
                continue
            it = put(it, new) if new != remark else it
            (tail if last else keep).append(it)
        return keep + tail


def _b64(s: str) -> bytes:
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _link_remark(line: str) -> str | None:
    if line.lower().startswith("vmess://"):
        try:
            return json.loads(_b64(line[8:])).get("ps")
        except ValueError:
            return None
    return unquote(line.rsplit("#", 1)[1]) if "://" in line and "#" in line else None


def _link_put(line: str, remark: str) -> str:
    if line.lower().startswith("vmess://"):
        cfg = json.loads(_b64(line[8:]))
        cfg["ps"] = remark
        return "vmess://" + base64.b64encode(json.dumps(cfg, ensure_ascii=False).encode()).decode()
    return line.rsplit("#", 1)[0] + "#" + quote(remark, safe=URI_SAFE)


def transform_links(body: bytes, plan: Plan) -> bytes | None:
    """Список ссылок, как есть или в base64. None — это не ссылки или менять нечего."""
    text = body.decode("utf-8", "replace").strip()
    encoded = "://" not in text
    if encoded:
        try:
            text = _b64(text).decode("utf-8")
        except ValueError:
            return None
        if "://" not in text:
            return None
    lines = plan.run([ln for ln in text.splitlines() if ln.strip()], _link_remark, _link_put)
    if not plan.changed:
        return None
    out = "\n".join(lines)
    return base64.b64encode(out.encode()).decode().encode() if encoded else out.encode()


def transform_xray_json(body: bytes, plan: Plan) -> bytes | None:
    """Xray JSON (Happ, v2rayN, Streisand): список конфигов с remarks или один конфиг."""
    try:
        data = json.loads(body)
    except ValueError:
        return None

    def put(cfg, remark):
        cfg["remarks"] = remark
        return cfg

    get = lambda cfg: cfg.get("remarks") if isinstance(cfg, dict) else None  # noqa: E731
    if isinstance(data, list) and any(isinstance(c, dict) and "remarks" in c for c in data):
        data = plan.run(data, get, put)
    elif isinstance(data, dict) and "remarks" in data and "outbounds" in data:
        data = plan.run([data], get, put)
        data = data[0] if data else []
    else:
        return None  # sing-box и прочее
    return json.dumps(data, ensure_ascii=False).encode() if plan.changed else None


def transform_text(body: bytes, ctx: Ctx, is_json: bool) -> bytes | None:
    """Mihomo YAML и sing-box: только подстановки прямо в тексте. Одинаковое имя меняется
    одинаково везде — ссылки на прокси из групп не ломаются."""
    text = body.decode("utf-8", "replace")
    if not TOKEN.search(text):
        return None

    def one(m):
        v = ctx.value(m.group(1), m.group(2)).replace("\n", " ")
        return json.dumps(v, ensure_ascii=False)[1:-1] if is_json else re.sub(r"[\"'\\]", "", v)
    return TOKEN.sub(one, text).encode()


def decode_header(v: str) -> tuple[str, bool]:
    if v.startswith("base64:"):
        try:
            return base64.b64decode(v[7:]).decode("utf-8"), True
        except ValueError:
            return v, False
    return v, False


def encode_header(text: str, was_b64: bool) -> str:
    if was_b64 or "\n" in text or not text.isascii():
        return "base64:" + base64.b64encode(text.encode()).decode()
    return text


def rewrite_headers(headers: list, ctx: Ctx) -> tuple[list, bool]:
    """Подстановки во всех заголовках. Сообщения автоматизаций без {{RD_MESSAGE}} в тексте —
    в начало плашки announce."""
    out, changed, placed = [], False, False
    for k, v in headers:
        text, was_b64 = decode_header(v)
        if TOKEN.search(text):
            placed |= "{{RD_MESSAGE" in text
            v = encode_header(ctx.render_lines(text), was_b64)
            changed = True
        out.append((k, v))
    if ctx.messages and not placed:
        cur = next((v for k, v in out if k.lower() == "announce"), None)
        old = decode_header(cur)[0] if cur else ""
        out = [(k, v) for k, v in out if k.lower() != "announce"]
        out.append(("announce", encode_header("\n".join(ctx.messages + ([old] if old else [])), True)))
        changed = True
    return out, changed


# ---------------- сам прокси
def is_sub_host(scope) -> bool:
    domain = settings.sub_domain
    if not domain:
        return False
    host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1").split(":")[0].lower()
    return host == domain


async def handle(scope, receive, send):
    req = Request(scope, receive)
    t0 = time.monotonic()
    stats["requests"] += 1
    stats["last_request"] = int(time.time())
    # accept-encoding свой: httpx распакует только gzip/deflate, а brotli/zstd ушли бы клиенту
    # сжатыми, но без content-encoding — мусор вместо страницы. Сжатие для клиента делает Caddy
    headers = [(k, v) for k, v in req.headers.items() if k.lower() not in HOP - {"host"} | {"accept-encoding"}]
    headers.append(("accept-encoding", "gzip"))
    # без них subscription-page рвёт соединение («Reverse proxy and HTTPS are required»)
    have = {k.lower() for k, _ in headers}
    if "x-forwarded-for" not in have and req.client:
        headers.append(("x-forwarded-for", req.client.host))
    if "x-forwarded-proto" not in have:
        headers.append(("x-forwarded-proto", "https"))
    target = settings.sub_upstream.rstrip("/") + scope.get("raw_path", scope["path"].encode()).decode("latin-1")
    if scope.get("query_string"):
        target += "?" + scope["query_string"].decode("latin-1")
    m = SHORT_UUID.match(scope["path"])
    ip, hwid = (req.client.host if req.client else ""), req.headers.get("x-hwid", "")
    ua = req.headers.get("user-agent", "")
    if m and req.method in ("GET", "HEAD"):
        ban = await bans.match(ip, hwid)
        if ban:  # в Remnawave не идём вовсе — она даже не узнает об этом устройстве
            stats["banned"] += 1
            _fire(m.group(1), ua, ip, hwid, ban)
            await bans.stub(req.headers.get("accept", ""), ua)(scope, receive, send)
            return
    try:
        r = await client().request(req.method, target, headers=headers, content=await req.body())
    except httpx.HTTPError as e:
        stats["upstream_errors"] += 1
        stats["last_error"] = f"subscription-page: {e.__class__.__name__}"
        await Response(status_code=502)(scope, receive, send)
        return
    body = r.content
    out_headers = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in HOP]
    if m and r.status_code == 200 and req.method == "GET":
        stats["subs"] += 1
        _fire(m.group(1), ua, ip, hwid)
        try:
            new = await rewrite(m.group(1), r, body, out_headers)
            if new is not None:
                body, out_headers = new
                stats["changed"] += 1
        except Exception as e:  # noqa: BLE001 — клиент в любом случае получает подписку
            stats["errors"] += 1
            stats["last_error"] = f"{e.__class__.__name__}: {e}"[:300]
            log.exception("rewrite %s", m.group(1))
    ms = stats["ms"]
    ms.append(int((time.monotonic() - t0) * 1000))
    del ms[:-200]
    resp = Response(content=body, status_code=r.status_code)
    resp.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out_headers] + \
        [(b"content-length", str(len(body)).encode())]
    await resp(scope, receive, send)


_bg: set = set()


def _fire(short: str, ua: str, ip: str, hwid: str, ban: dict | None = None):
    """Журнал обновлений, счётчик бана и событийные автоматизации — в фоне, выдачу не задерживаем."""
    async def run():
        try:
            u = await user_by_short(short)
            await bans.log_request(u, ip, hwid, ua, ban)
            if u and u.get("id") and not ban:
                await automations.on_sub_request(u, ua)
        except Exception:  # noqa: BLE001
            log.exception("sub request background for %s", short)
    t = asyncio.create_task(run())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


async def user_ctx(u: dict | None) -> Ctx:
    if not u:
        return Ctx([], [])
    msgs, hide = await automations.sub_effects(u["id"]) if u.get("id") else ([], set())
    return Ctx(await quota_info(u), msgs, hide)


async def rewrite(short: str, r: httpx.Response, body: bytes, headers: list):
    conf = await load_conf()
    if not conf["enabled"]:
        return None
    ctype = r.headers.get("content-type", "")
    ctx = await user_ctx(await user_by_short(short))
    try:
        states = await host_states() if conf["mark_down"] or conf["hide_disabled"] else {}
    except RWError:
        states = {}
    plan = Plan(conf, ctx, states)
    new_body = None
    if "text/plain" in ctype:
        new_body = transform_links(body, plan)
    elif "json" in ctype:
        new_body = transform_xray_json(body, plan)
        if new_body is None and not plan.changed:
            new_body = transform_text(body, ctx, True)
    elif "yaml" in ctype or "text/" in ctype:
        new_body = transform_text(body, ctx, False)
    headers, h_changed = rewrite_headers(headers, ctx)
    if new_body is None and not h_changed:
        return None
    return (new_body if new_body is not None else body), headers


class SubDomainProxy:
    """ASGI: всё, что пришло на SUB_DOMAIN, уходит в прокси. Админка там недоступна."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and is_sub_host(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await handle(scope, receive, send)
            return
        await self.app(scope, receive, send)


# ---------------- админка
@router.get("")
async def sub_proxy_get(_: str = Depends(require_auth)):
    conf = await load_conf(ttl=0)
    ms = sorted(stats["ms"])
    return {**conf, "sub_domain": settings.sub_domain, "sub_upstream": settings.sub_upstream,
            "stats": {**{k: v for k, v in stats.items() if k != "ms"},
                      "p50_ms": ms[len(ms) // 2] if ms else None,
                      "p95_ms": ms[int(len(ms) * .95)] if ms else None}}


class SubProxyIn(BaseModel):
    enabled: bool
    hide_info_without_quota: bool = True
    mark_down: bool = True
    down_prefix: str = Field("⚠️ ", max_length=16)
    down_to_end: bool = False
    hide_disabled: bool = False
    sub_domain: str = Field("", max_length=253)
    sub_upstream: str = Field("http://remnawave-subscription-page:3010", max_length=200)


@router.put("")
async def sub_proxy_put(body: SubProxyIn, user: str = Depends(require_auth)):
    domain = body.sub_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if domain and domain in (settings.panel_domain.lower(), settings.status_domain):
        raise HTTPException(400, "Домен подписок должен отличаться от доменов панели и статус-страницы")
    upstream = body.sub_upstream.strip().rstrip("/")
    if not upstream.startswith(("http://", "https://")):
        raise HTTPException(400, "Адрес subscription-page должен начинаться с http:// или https://")
    await kv_set(KV_KEY, json.dumps(body.model_dump(exclude={"sub_domain", "sub_upstream"}), ensure_ascii=False))
    changes = {}
    if domain != settings.sub_domain:
        changes["SUB_DOMAIN"] = domain
    if upstream != settings.sub_upstream:
        changes["SUB_UPSTREAM"] = upstream
    if changes:
        settings.update(changes)
    _conf["ts"] = 0
    await add_event("action", "info", f"{user}: изменена прослойка подписок")
    return {"ok": True}


class PreviewIn(BaseModel):
    username: str = Field(min_length=1)


@router.post("/preview")
async def sub_proxy_preview(body: PreviewIn, _: str = Depends(require_auth)):
    """Что прослойка сделает с подпиской пользователя — без запроса самой подписки
    (включён HWID: без устройства вернулась бы заглушка, а с выдуманным — завелось бы устройство)."""
    conf = await load_conf(ttl=0)
    users = await rw.users_all()
    u = next((x for x in users if (x.get("username") or "").lower() == body.username.strip().lower()), None)
    if not u:
        raise HTTPException(404, f"Пользователь «{body.username}» не найден")
    _rules["ts"] = 0
    ctx = await user_ctx(u)
    states = await host_states(ttl=0)
    plan = Plan(conf, ctx, states)
    hosts = []
    for h in await rw.hosts():
        if h.get("isDisabled") or not h.get("remark"):
            continue
        new, last = plan.apply(h["remark"])
        hosts.append({"remark": h["remark"], "state": states.get(h["remark"], "unknown"),
                      "result": new, "to_end": last})
    announce = None
    try:
        tpl = ((await rw.subscription_settings()).get("customResponseHeaders") or {}).get("announce") or ""
        tpl = tpl.removeprefix("rwEncodeBase64:")
        hdrs, _ = rewrite_headers([("announce", tpl)] if tpl else [], ctx)
        announce = next((decode_header(v)[0] for k, v in hdrs if k == "announce"), "")
    except RWError:
        pass
    return {"username": u["username"], "short_uuid": u.get("shortUuid"), "quotas": ctx.quotas,
            "messages": ctx.messages, "announce": announce, "hosts": hosts, "enabled": conf["enabled"]}


# ---------------- переход с {{DESCRIPTION}} на {{RD_QUOTA}}
def _hdr_text(v: str) -> str:
    return v.removeprefix("rwEncodeBase64:") if isinstance(v, str) else ""


@router.get("/texts")
async def sub_proxy_texts(_: str = Depends(require_auth)):
    """Где в Remnawave используются {{DESCRIPTION}} и подстановки RemnaDeck."""
    s = await rw.subscription_settings()
    headers = [{"name": k, "text": _hdr_text(v)} for k, v in (s.get("customResponseHeaders") or {}).items()
               if isinstance(v, str) and ("{{DESCRIPTION}}" in v or "{{RD_" in v)]
    hosts = [{"uuid": h["uuid"], "remark": h.get("remark") or "", "description": h.get("serverDescription") or ""}
             for h in await rw.hosts()
             if any(x in f"{h.get('remark')}{h.get('serverDescription')}" for x in ("{{DESCRIPTION}}", "{{RD_"))]
    counters = await counter_users()
    return {"headers": headers, "hosts": hosts, "counter_users": len(counters),
            "legacy": any("{{DESCRIPTION}}" in x["text"] for x in headers)
            or any("{{DESCRIPTION}}" in x["remark"] + x["description"] for x in hosts)}


def _live() -> bool:
    return _conf["v"]["enabled"] and time.time() - stats["last_request"] < 900


@router.post("/migrate")
async def sub_proxy_migrate(user: str = Depends(require_auth)):
    """{{DESCRIPTION}} → {{RD_QUOTA}} в заголовках подписки и хостах Remnawave."""
    await load_conf(ttl=0)
    if not _live():
        raise HTTPException(409, "Сначала пусти подписки через прослойку и включи правку — иначе клиенты "
                                 "увидят «{{RD_QUOTA}}» буквально")
    changed = []
    s = await rw.subscription_settings()
    hdrs = dict(s.get("customResponseHeaders") or {})
    new = {k: v.replace("{{DESCRIPTION}}", "{{RD_QUOTA}}") if isinstance(v, str) else v for k, v in hdrs.items()}
    if new != hdrs:
        await rw.subscription_settings_update({"uuid": s["uuid"], "customResponseHeaders": new})
        changed += [f"заголовок {k}" for k in hdrs if hdrs[k] != new[k]]
    for h in await rw.hosts():
        if "{{DESCRIPTION}}" not in f"{h.get('remark')}{h.get('serverDescription')}":
            continue
        base = {k: v for k, v in (await rw.host(h["uuid"])).items() if k not in CLEAN}
        for f in ("remark", "serverDescription"):
            if isinstance(base.get(f), str):
                base[f] = base[f].replace("{{DESCRIPTION}}", "{{RD_QUOTA}}")
        await rw.host_update(base)
        changed.append(f"хост {base.get('remark')}")
    await add_event("action", "info", f"{user}: {{{{DESCRIPTION}}}} → {{{{RD_QUOTA}}}} ({len(changed)})")
    return {"changed": changed}


async def counter_users() -> list[dict]:
    """Пользователи, у которых в описании лежит ровно счётчик квоты — его записал RemnaDeck."""
    db = await get_db()
    async with db.execute("SELECT desc_ok, desc_over FROM quota_rules") as cur:
        tpls = {t for r in await cur.fetchall() for t in (r["desc_ok"] or quota.DESC_OK, r["desc_over"] or quota.DESC_OVER)}
    tpls |= {quota.DESC_OK, quota.DESC_OVER}
    pats = []
    for t in tpls:
        p = re.escape(t)
        for k, rx in (("used", r"[\d.]+"), ("limit", r"[\d.]+"), ("left", r"[\d.]+"), ("percent", r"\d+"),
                      ("reset", r"\d\d\.\d\d")):
            p = p.replace(re.escape("{" + k + "}"), rx)
        pats.append(re.compile(p))
    return [u for u in await rw.users_all()
            if u.get("description") and any(p.fullmatch(u["description"].strip()) for p in pats)]


class CleanIn(BaseModel):
    dry: bool = True


@router.post("/clean-descriptions")
async def sub_proxy_clean(body: CleanIn, user: str = Depends(require_auth)):
    """Очистить описания, где лежит только счётчик, и выключить запись счётчика в описание."""
    users = await counter_users()
    if body.dry:
        return {"count": len(users), "sample": [u["description"] for u in users[:5]]}
    texts = await sub_proxy_texts(user)
    if texts["legacy"]:
        raise HTTPException(409, "В Remnawave ещё используется {{DESCRIPTION}} — сначала переведи тексты на {{RD_QUOTA}}")
    db = await get_db()
    await db.execute("UPDATE quota_rules SET write_desc=0")
    await db.commit()
    sem, done, errors = asyncio.Semaphore(6), [], []
    empty: list = [""]

    async def one(u):
        async with sem:
            try:
                await rw.user_update({"id": u["id"], "description": empty[0]})
                done.append(u["username"])
            except RWError as e:
                if empty[0] == "" and "description" in str(e):
                    empty[0] = None  # старые версии не принимают пустую строку
                    return await one(u)
                errors.append(f"{u['username']}: {e}")
    await asyncio.gather(*(one(u) for u in users))
    await add_event("action", "info", f"{user}: очищены описания-счётчики у {len(done)} пользователей")
    return {"cleaned": len(done), "errors": errors[:10]}
