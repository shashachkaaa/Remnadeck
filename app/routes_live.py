"""Живые инструменты: терминал и лог ноды по WebSocket, проверка доступности
из России через check-host.net и сроки сертификатов Let's Encrypt на серверах.
"""
import asyncio
import json
import logging
import re
import shlex
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import ssh
from .auth import current_user, require_auth
from .db import get_db
from .events import add_event
from .routes_infra import get_server, spawn

log = logging.getLogger("live")
router = APIRouter(tags=["live"])


# ---------------- WebSocket: общий вход
def ws_origin_ok(ws: WebSocket) -> bool:
    """Защита от чужих страниц: Origin должен совпадать с хостом панели."""
    origin = ws.headers.get("origin")
    if not origin:
        return True                                   # не браузер — решает кука
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host") or ""
    return urlparse(origin).netloc.split(":")[0] == host.split(":")[0]


async def ws_open(ws: WebSocket, sid: int) -> tuple[dict | None, str | None]:
    user = current_user(ws)                           # у WebSocket те же cookies, что у Request
    if not user or not ws_origin_ok(ws):
        await ws.close(code=4401)
        return None, None
    await ws.accept()
    try:
        return await get_server(sid), user
    except HTTPException as e:
        await ws.send_text(f"\r\n✗ {e.detail}\r\n")
        await ws.close()
        return None, None


# ---------------- терминал
@router.websocket("/ws/terminal/{sid}")
async def terminal(ws: WebSocket, sid: int):
    srv, user = await ws_open(ws, sid)
    if not srv:
        return
    cols = max(20, min(400, int(ws.query_params.get("c", 100))))
    rows = max(5, min(200, int(ws.query_params.get("r", 30))))
    await ws.send_text(f"\x1b[2mПодключаюсь к {srv['username']}@{srv['host']}…\x1b[0m\r\n")
    try:
        conn = await ssh.connect(srv)
    except ssh.SSHError as e:
        await ws.send_text(f"\r\n\x1b[31m✗ {e}\x1b[0m\r\n")
        await ws.close()
        return
    await add_event("action", "warn", f"{user}: открыт терминал {srv['name']}")
    async with conn:
        proc = await conn.create_process(term_type="xterm-256color", term_size=(cols, rows))

        async def pump():
            try:
                while True:
                    data = await proc.stdout.read(8192)
                    if not data:
                        break
                    await ws.send_text(data)
            except Exception:                         # noqa: BLE001 — клиент ушёл
                pass
            try:
                await ws.send_text("\r\n\x1b[2m[сессия закрыта]\x1b[0m\r\n")
                await ws.close()
            except Exception:                         # noqa: BLE001
                pass

        task = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("t") == "i":
                    proc.stdin.write(msg.get("d", ""))
                elif msg.get("t") == "r":
                    proc.change_terminal_size(int(msg["c"]), int(msg["r"]))
        except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
            pass
        finally:
            task.cancel()
            proc.close()


# ---------------- живой лог ноды
@router.websocket("/ws/logs/{sid}")
async def node_logs(ws: WebSocket, sid: int):
    srv, _ = await ws_open(ws, sid)
    if not srv:
        return
    tail = max(10, min(2000, int(ws.query_params.get("tail", 200))))
    path = shlex.quote(srv["node_path"] or "/opt/remnanode")
    cmd = (f"{ssh.sudo_prefix(srv)}bash -lc "
           + shlex.quote(f"cd {path} && docker compose logs -f --no-color --tail {tail} 2>&1"))
    try:
        conn = await ssh.connect(srv)
    except ssh.SSHError as e:
        await ws.send_text(f"✗ {e}\n")
        await ws.close()
        return
    async with conn:
        proc = await conn.create_process(cmd)

        async def pump():
            try:
                async for line in proc.stdout:
                    await ws.send_text(line)
            except Exception:                         # noqa: BLE001
                pass
            try:
                await ws.send_text("\n[поток закрыт]\n")
                await ws.close()
            except Exception:                         # noqa: BLE001
                pass

        task = asyncio.create_task(pump())
        try:
            while True:
                await ws.receive_text()               # клиент ничего не шлёт — ждём разрыва
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            task.cancel()
            proc.close()


# ---------------- доступность из России (check-host.net)
CH = "https://check-host.net"
FALLBACK_RU = [f"ru{i}.node.check-host.net" for i in range(1, 5)]
_ru_cache: dict = {"ts": 0, "nodes": {}}


async def ru_nodes(client: httpx.AsyncClient) -> dict[str, str]:
    """Точки проверки в России: имя узла -> город. Кешируем на час."""
    if time.time() - _ru_cache["ts"] < 3600 and _ru_cache["nodes"]:
        return _ru_cache["nodes"]
    nodes: dict[str, str] = {}
    try:
        r = await client.get(f"{CH}/nodes/hosts")
        for name, info in (r.json().get("nodes") or {}).items():
            loc = info.get("location") or []
            if loc and str(loc[0]).lower() == "ru":
                nodes[name] = loc[2] if len(loc) > 2 else loc[-1]
    except Exception as e:                            # noqa: BLE001
        log.warning("check-host nodes: %s", e)
    if not nodes:
        nodes = {n: n.split(".")[0] for n in FALLBACK_RU}
    _ru_cache.update(ts=time.time(), nodes=nodes)
    return nodes


class ReachIn(BaseModel):
    targets: list[str] = Field(min_length=1, max_length=20)


TARGET = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


@router.post("/api/reach")
async def reach(body: ReachIn, _: str = Depends(require_auth)):
    targets = []
    for t in body.targets:
        t = t.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        if t and TARGET.match(t):
            targets.append(t if ":" in t else f"{t}:443")
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise HTTPException(400, "Нет корректных адресов вида host:port")

    async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/json",
                                                      "User-Agent": "RemnaDeck"}) as client:
        nodes = await ru_nodes(client)
        params = [("node", n) for n in nodes]
        started: dict[str, str] = {}
        for t in targets:
            try:
                r = await client.get(f"{CH}/check-tcp", params=[("host", t), *params])
                rid = r.json().get("request_id")
                if rid:
                    started[t] = rid
            except Exception as e:                    # noqa: BLE001
                log.warning("check-host start %s: %s", t, e)
            await asyncio.sleep(0.3)                  # не долбим сервис пачкой
        if not started:
            raise HTTPException(502, "check-host.net не ответил. Попробуй через минуту")

        results: dict[str, dict] = {t: {} for t in started}
        deadline = time.time() + 25
        while time.time() < deadline:
            await asyncio.sleep(2)
            pending = False
            for t, rid in started.items():
                try:
                    data = (await client.get(f"{CH}/check-result/{rid}")).json()
                except Exception:                     # noqa: BLE001
                    pending = True
                    continue
                for node, val in (data or {}).items():
                    if val is None:
                        pending = True
                    else:
                        results[t][node] = val
            if not pending:
                break

    out = []
    for t in targets:
        rows = []
        for node, city in nodes.items():
            val = results.get(t, {}).get(node)
            item = val[0] if isinstance(val, list) and val else None
            if item is None:
                rows.append({"city": city, "state": "wait"})
            elif item.get("error"):
                rows.append({"city": city, "state": "fail", "error": item["error"]})
            else:
                rows.append({"city": city, "state": "ok", "ms": round(float(item.get("time", 0)) * 1000)})
        ok = sum(1 for r in rows if r["state"] == "ok")
        out.append({"target": t, "ok": ok, "total": len(rows), "points": rows,
                    "started": t in started})
    return {"results": out, "checked_at": int(time.time())}


# ---------------- сертификаты Let's Encrypt
CERT_RE = re.compile(
    r"Certificate Name:\s*(?P<name>\S+).*?Domains:\s*(?P<domains>[^\n]+).*?"
    r"Expiry Date:\s*(?P<exp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.S)


def parse_certs(text: str) -> list[dict]:
    out = []
    now = datetime.now(timezone.utc)
    for m in CERT_RE.finditer(text):
        exp = datetime.strptime(m["exp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.append({"name": m["name"], "domains": m["domains"].split(),
                     "expires": int(exp.timestamp()), "days": (exp - now).days})
    return out


@router.get("/api/certs")
async def certs(_: str = Depends(require_auth)):
    """certbot certificates со всех серверов параллельно (до 6 сессий разом)."""
    db = await get_db()
    async with db.execute("SELECT * FROM servers ORDER BY name") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    gate = asyncio.Semaphore(6)

    async def one(srv):
        async with gate:
            try:
                text = await ssh.run_cmd(srv, "command -v certbot >/dev/null && certbot certificates 2>/dev/null || echo NO_CERTBOT")
            except Exception as e:                    # noqa: BLE001
                return {"server_id": srv["id"], "server": srv["name"], "error": str(e)[:160], "certs": []}
            if "NO_CERTBOT" in text:
                return {"server_id": srv["id"], "server": srv["name"], "certs": [], "note": "certbot не установлен"}
            return {"server_id": srv["id"], "server": srv["name"], "certs": parse_certs(text)}

    return await asyncio.gather(*(one(r) for r in rows))


class RenewIn(BaseModel):
    name: str = Field(min_length=1, max_length=253)
    force: bool = False


@router.post("/api/servers/{sid}/cert-renew")
async def cert_renew(sid: int, body: RenewIn, user: str = Depends(require_auth)):
    srv = await get_server(sid)
    name = shlex.quote(body.name)
    flag = " --force-renewal" if body.force else ""
    script = f"""#!/usr/bin/env bash
set -e
echo "== certbot renew --cert-name {body.name}{flag}"
certbot renew --cert-name {name}{flag} --non-interactive
echo
certbot certificates --cert-name {name} 2>/dev/null | grep -E 'Domains|Expiry' || true
"""
    await add_event("action", "info", f"{user}: продление сертификата {body.name} на {srv['name']}")
    return spawn(f"Сертификат {body.name} · {srv['name']}", srv, script)
