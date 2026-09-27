"""Фоновый сборщик: снимки нод, проверки хостов, события."""
import asyncio
import logging
import time
from datetime import date

from .checks import check_target
from .config import settings
from .db import get_db, kv_get, kv_set
from .events import add_event
from .remnawave import RWError, rw

log = logging.getLogger("poller")


def node_state(n: dict) -> int:
    if n.get("isDisabled"):
        return -1
    return 1 if n.get("isConnected") else 0


async def poll_nodes():
    nodes = await rw.nodes(ttl=0)
    db = await get_db()
    ts = int(time.time())
    for n in nodes:
        uuid, name = n.get("uuid"), n.get("name", "?")
        state = node_state(n)
        await db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)",
                         (ts, uuid, name, int(n.get("trafficUsedBytes") or 0),
                          int(n.get("usersOnline") or 0), state))
        prev = await kv_get(f"node:{uuid}")
        if prev is not None and prev != str(state):
            if state == 0:
                await add_event("node", "bad", f"{name}: нода потеряла связь с панелью")
            elif state == 1 and prev == "0":
                await add_event("node", "ok", f"{name}: нода снова на связи")
        await kv_set(f"node:{uuid}", str(state))
    await db.execute("DELETE FROM snapshots WHERE ts<?", (ts - 8 * 86400,))
    await db.execute("DELETE FROM events WHERE ts<?", (ts - 30 * 86400,))
    await db.commit()


def collect_targets(hosts: list[dict]) -> dict[str, dict]:
    targets = {}
    for h in hosts:
        if h.get("isDisabled") or not h.get("address"):
            continue
        port = int(h.get("port") or 443)
        sni = h.get("sni") or ""
        key = f"{h['address']}:{port}/{sni}"
        targets.setdefault(key, {"target": key, "label": h.get("remark") or h["address"],
                                 "host": h["address"], "port": port, "sni": sni})
    return targets


async def run_checks():
    targets = collect_targets(await rw.hosts())
    db = await get_db()
    started = int(time.time())
    sem = asyncio.Semaphore(20)

    async def one(t):
        async with sem:
            return t, await check_target(t["host"], t["port"], t["sni"])

    for t, r in await asyncio.gather(*(one(t) for t in targets.values())):
        async with db.execute("SELECT tcp_ok, tls_ok FROM health WHERE target=?", (t["target"],)) as cur:
            prev = await cur.fetchone()
        ok = bool(r["tcp_ok"] and r["tls_ok"])
        if prev is not None:
            was_ok = bool(prev["tcp_ok"] and prev["tls_ok"])
            if was_ok and not ok:
                await add_event("path", "bad", f"{t['label']}: {r['error']}")
            elif not was_ok and ok:
                await add_event("path", "ok", f"{t['label']}: путь восстановлен")
        days = r["tls_days"]
        if r["tls_trusted"] and days is not None and days <= 7:
            flag = f"certwarn:{t['target']}"
            if await kv_get(flag) != date.today().isoformat():
                await add_event("tls", "warn", f"{t['label']}: сертификат истекает через {days} дн.")
                await kv_set(flag, date.today().isoformat())
        await db.execute(
            """INSERT INTO health VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(target) DO UPDATE SET label=excluded.label, ts=excluded.ts,
               dns_ok=excluded.dns_ok, tcp_ok=excluded.tcp_ok, tls_ok=excluded.tls_ok,
               tls_trusted=excluded.tls_trusted, tls_days=excluded.tls_days,
               latency_ms=excluded.latency_ms, ip=excluded.ip, error=excluded.error""",
            (t["target"], t["label"], t["host"], t["port"], t["sni"], int(time.time()),
             r["dns_ok"], r["tcp_ok"], r["tls_ok"], r["tls_trusted"], days,
             r["latency_ms"], r["ip"], r["error"]))
    await db.execute("DELETE FROM health WHERE ts<?", (started,))
    await db.commit()


async def loop_forever():
    last_check = 0.0
    while True:
        if not rw.configured:
            await asyncio.sleep(10)
            continue
        try:
            await poll_nodes()
            if time.monotonic() - last_check >= settings.check_interval:
                last_check = time.monotonic()
                await run_checks()
        except RWError as e:
            log.warning("%s", e)
        except Exception:
            log.exception("poller error")
        await asyncio.sleep(settings.poll_interval)
