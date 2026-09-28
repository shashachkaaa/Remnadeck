"""Бан по HWID и IP — в прослойке подписок.

Запрос подписки с забаненного устройства (заголовок x-hwid) или адреса (IP или подсеть)
в Remnawave не уходит: клиент получает заглушку с сообщением, а Remnawave даже не узнаёт
об этом устройстве. HWID-бан действует на все аккаунты — против набора пробных подписок
с одного телефона.

Бан закрывает только ПОЛУЧЕНИЕ подписки. Уже скачанные конфиги работают, пока у
пользователя не сменить ключи («Новая подписка» в карточке). IP мобильных операторов
делят тысячи людей (CGNAT) — надёжнее банить HWID.

Каждое обновление подписки через прослойку пишется в sub_log (7 дней): по нему удобно
искать, кого банить.
"""
import base64
import html
import ipaddress
import json
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from .auth import require_auth
from .db import get_db, kv_get, kv_set
from .events import add_event

router = APIRouter(prefix="/api/bans", tags=["bans"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS bans(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, value TEXT, note TEXT,
    created INTEGER, hits INTEGER DEFAULT 0, last_hit INTEGER, last_user TEXT, UNIQUE(kind, value));
CREATE TABLE IF NOT EXISTS sub_log(ts INTEGER, user_id INTEGER, username TEXT, ip TEXT, hwid TEXT,
    ua TEXT, banned INTEGER);
CREATE INDEX IF NOT EXISTS idx_sublog_ts ON sub_log(ts);
"""
MSG_KEY = "ban_message"
DEFAULT_MSG = "🚫 Доступ с этого устройства заблокирован\nОбратитесь в поддержку"
KEEP = 7 * 86400
MIHOMO = ("clash", "mihomo", "stash", "verge", "flclash", "koala")

_cache: dict = {"ts": 0.0, "hwid": {}, "nets": [], "msg": DEFAULT_MSG}
_writes = {"n": 0}


async def db_ready():
    db = await get_db()
    await db.executescript(SCHEMA)
    return db


async def _load(ttl: float = 30):
    if time.monotonic() - _cache["ts"] < ttl:
        return
    db = await db_ready()
    async with db.execute("SELECT * FROM bans") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    _cache["hwid"] = {r["value"].lower(): r for r in rows if r["kind"] == "hwid"}
    nets = []
    for r in rows:
        if r["kind"] == "ip":
            try:
                nets.append((ipaddress.ip_network(r["value"], strict=False), r))
            except ValueError:
                pass
    _cache["nets"] = nets
    _cache["msg"] = (await kv_get(MSG_KEY)) or DEFAULT_MSG
    _cache["ts"] = time.monotonic()


def drop_cache():
    _cache["ts"] = 0


async def match(ip: str, hwid: str) -> dict | None:
    await _load()
    if hwid and hwid.lower() in _cache["hwid"]:
        return _cache["hwid"][hwid.lower()]
    if ip and _cache["nets"]:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for net, row in _cache["nets"]:
            if addr.version == net.version and addr in net:
                return row
    return None


async def log_request(user: dict | None, ip: str, hwid: str, ua: str, ban: dict | None):
    """Запись в журнал обновлений (фоном из прослойки) и счётчик срабатываний бана."""
    db = await db_ready()
    now = int(time.time())
    await db.execute("INSERT INTO sub_log VALUES(?,?,?,?,?,?,?)",
                     (now, (user or {}).get("id"), (user or {}).get("username"), ip, hwid or None,
                      (ua or "")[:200], ban["id"] if ban else None))
    if ban:
        await db.execute("UPDATE bans SET hits=hits+1, last_hit=?, last_user=? WHERE id=?",
                         (now, (user or {}).get("username"), ban["id"]))
    _writes["n"] += 1
    if _writes["n"] % 500 == 1:
        await db.execute("DELETE FROM sub_log WHERE ts<?", (now - KEEP,))
    await db.commit()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def stub(accept: str, ua: str) -> Response:
    """Заглушка в формате клиента: у него на экране появится текст бана вместо серверов."""
    text = _cache["msg"]
    lines = list(dict.fromkeys(ln.strip() for ln in text.split("\n") if ln.strip())) or ["🚫"]
    headers = {"announce": "base64:" + _b64(text), "profile-title": "base64:" + _b64(lines[0]),
               "profile-update-interval": "1", "cache-control": "no-store"}
    if "text/html" in (accept or ""):
        body = "<br>".join(html.escape(ln) for ln in lines)
        return Response(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        f"<body style='font:18px system-ui;padding:40px 20px;text-align:center'>{body}</body>",
                        status_code=403, media_type="text/html; charset=utf-8", headers={"cache-control": "no-store"})
    if any(k in (ua or "").lower() for k in MIHOMO):
        names = [json.dumps(ln, ensure_ascii=False) for ln in lines]
        yaml = ("proxies:\n" + "".join(f"  - {{name: {n}, type: socks5, server: 127.0.0.1, port: 1}}\n" for n in names)
                + f"proxy-groups:\n  - name: BAN\n    type: select\n    proxies: [{', '.join(names)}]\n"
                + "rules:\n  - MATCH,DIRECT\n")
        return Response(yaml, media_type="text/yaml; charset=utf-8", headers=headers)
    links = "\n".join("vless://00000000-0000-0000-0000-000000000000@0.0.0.0:1?encryption=none&type=tcp&security=none#"
                      + quote(ln, safe="-_.!~*'()") for ln in lines)
    return Response(_b64(links), media_type="text/plain; charset=utf-8", headers=headers)


# ---------------- API
def normalize(kind: str, value: str) -> str:
    value = value.strip()
    if kind == "hwid":
        if not 4 <= len(value) <= 200:
            raise HTTPException(400, "HWID: от 4 до 200 символов")
        return value
    if kind == "ip":
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise HTTPException(400, "Не похоже на IP или подсеть (1.2.3.4, 1.2.3.0/24, 2001:db8::/32)")
        too_big = net.prefixlen < 16 if net.version == 4 else net.prefixlen < 32
        if too_big:
            raise HTTPException(400, "Слишком большая подсеть — так можно забанить целого оператора")
        return str(net.network_address) if net.num_addresses == 1 else str(net)
    raise HTTPException(400, "Тип: hwid или ip")


@router.get("")
async def bans_list(_: str = Depends(require_auth)):
    db = await db_ready()
    async with db.execute("SELECT * FROM bans ORDER BY id DESC") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    async with db.execute("SELECT COUNT(*) c, MIN(ts) since FROM sub_log") as cur:
        lg = dict(await cur.fetchone())
    return {"bans": rows, "message": (await kv_get(MSG_KEY)) or DEFAULT_MSG, "log": lg}


class BanIn(BaseModel):
    kind: str
    value: str = Field(min_length=1, max_length=200)
    note: str = Field("", max_length=200)


@router.post("")
async def bans_add(body: BanIn, user: str = Depends(require_auth)):
    value = normalize(body.kind, body.value)
    db = await db_ready()
    async with db.execute("SELECT 1 FROM bans WHERE kind=? AND lower(value)=lower(?)", (body.kind, value)) as c:
        if await c.fetchone():
            raise HTTPException(409, "Уже в бане")  # HWID сравниваем без регистра — как и при проверке
    cur = await db.execute("INSERT OR IGNORE INTO bans(kind,value,note,created) VALUES(?,?,?,?)",
                           (body.kind, value, body.note.strip(), int(time.time())))
    await db.commit()
    if not cur.rowcount:
        raise HTTPException(409, "Уже в бане")
    drop_cache()
    await add_event("action", "info", f"{user}: бан {body.kind.upper()} {value}"
                                      + (f" ({body.note.strip()})" if body.note.strip() else ""))
    return {"ok": True, "value": value}


@router.delete("/{bid}")
async def bans_delete(bid: int, user: str = Depends(require_auth)):
    db = await db_ready()
    async with db.execute("SELECT * FROM bans WHERE id=?", (bid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Бан не найден")
    await db.execute("DELETE FROM bans WHERE id=?", (bid,))
    await db.commit()
    drop_cache()
    await add_event("action", "info", f"{user}: снят бан {row['kind'].upper()} {row['value']}")
    return {"ok": True}


class MsgIn(BaseModel):
    message: str = Field(min_length=1, max_length=300)


@router.put("/message")
async def bans_message(body: MsgIn, _: str = Depends(require_auth)):
    await kv_set(MSG_KEY, body.message.strip())
    drop_cache()
    return {"ok": True}


@router.get("/log")
async def bans_log(q: str = "", _: str = Depends(require_auth)):
    """Последние обновления подписки: поиск по нику, IP, HWID или приложению."""
    db = await db_ready()
    sql, args = "SELECT * FROM sub_log", []
    if q.strip():
        like = f"%{q.strip()}%"
        sql += " WHERE username LIKE ? OR ip LIKE ? OR hwid LIKE ? OR ua LIKE ?"
        args = [like] * 4
    async with db.execute(sql + " ORDER BY ts DESC LIMIT 200", args) as cur:
        return [dict(r) for r in await cur.fetchall()]
