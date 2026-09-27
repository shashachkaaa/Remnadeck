"""Автоматизации: условие → действия, с откатом, когда условие перестало выполняться.

Условия:
  quota   — по правилу квоты израсходовано ≥ N% (счёт ведёт движок квот, здесь только порог)
  expiry  — подписка истекает через ≤ N дней / истекла (не больше N дней назад, 0 — сколько угодно)
Действия:
  squad_move / squad_add / squad_remove — сквады пользователя в Remnawave (+ разрыв соединений)
  sub_message / sub_hide                 — только подписка через прослойку, в Remnawave не пишется

События (trigger sub_request) — другой род: «обновил подписку, в User-Agent есть слово» →
действия выполняются ОДИН раз на пользователя и не откатываются (extend_days, squad_add,
squad_remove). Отметка «уже получил» ставится до выполнения — два одновременных запроса
подписки не дадут бонус дважды; упало действие — отметка снимается, повторится в следующий раз.

Откат. Что именно поменяли в сквадах, записывается в automation_state. Когда условие
перестало выполняться (новый период квоты, продлил подписку), автоматизация выключена или
удалена — изменения отменяются. Если админ за это время сам поменял сквады, откат ничего не
трогает («ручные правки уважаются»). Откаты идут от поздних применений к ранним, чтобы
цепочки вроде 80% A→B, 100% B→C разматывались правильно.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from .config import settings
from .db import get_db
from .events import add_event
from .quota import squad_ids
from .remnawave import rw

log = logging.getLogger("automations")
_lock = asyncio.Lock()
_wake = asyncio.Event()
INTERVAL = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS automations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
    enabled INTEGER DEFAULT 1, mode TEXT DEFAULT 'dry', trigger TEXT, actions TEXT,
    created INTEGER, last_run INTEGER, last_result TEXT);
CREATE TABLE IF NOT EXISTS automation_state(auto_id INTEGER, user_id INTEGER, username TEXT,
    since INTEGER, applied TEXT, PRIMARY KEY(auto_id, user_id));
CREATE TABLE IF NOT EXISTS automation_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
    auto_id INTEGER, user_id INTEGER, username TEXT, event TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_alog ON automation_log(auto_id, id);
CREATE TABLE IF NOT EXISTS automation_once(auto_id INTEGER, user_id INTEGER, username TEXT, ts INTEGER,
    PRIMARY KEY(auto_id, user_id));
"""
SQUAD_ACTIONS = ("squad_move", "squad_add", "squad_remove")
SUB_ACTIONS = ("sub_message", "sub_hide")
EVENT_TRIGGERS = ("sub_request",)
EVENT_ACTIONS = ("extend_days", "squad_add", "squad_remove")


def is_event(a: dict) -> bool:
    return a["trigger"].get("type") in EVENT_TRIGGERS


async def db_ready():
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    return db


def parse(row) -> dict:
    d = dict(row)
    d["trigger"] = json.loads(d.get("trigger") or "{}")
    d["actions"] = json.loads(d.get("actions") or "[]")
    d["last_result"] = json.loads(d["last_result"]) if d.get("last_result") else None
    return d


async def all_automations(only_live: bool = False) -> list[dict]:
    db = await db_ready()
    sql = "SELECT * FROM automations" + (" WHERE enabled=1 AND mode='active'" if only_live else "") + " ORDER BY id"
    async with db.execute(sql) as cur:
        return [parse(r) for r in await cur.fetchall()]


# ---------------- условия
def _ts(v) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def quota_ctx() -> dict:
    """Счётчики квот: {rule_id: {"rule": rule, "rows": {user_id: row}}}. Берём только строки
    последнего прогона правила — иначе ушедший из-под квоты навсегда застрял бы с old used."""
    db = await get_db()
    async with db.execute("SELECT * FROM quota_rules WHERE enabled=1") as cur:
        rules = {r["id"]: dict(r) for r in await cur.fetchall()}
    out = {}
    for rid, rule in rules.items():
        async with db.execute("SELECT * FROM quota_users WHERE rule_id=? AND updated>=?",
                              (rid, (rule["last_run"] or 0) - 120)) as cur:
            out[rid] = {"rule": rule, "rows": {r["user_id"]: dict(r) for r in await cur.fetchall()}}
    return out


def matches(trigger: dict, u: dict, qctx: dict, now: float) -> bool:
    kind = trigger.get("type")
    if kind == "quota":
        q = qctx.get(int(trigger.get("rule_id") or 0))
        if not q:
            return False
        row = q["rows"].get(u.get("id"))
        limit = int(q["rule"]["limit_bytes"] or 0)
        return bool(row and limit) and int(row["used"] or 0) * 100 >= limit * float(trigger.get("percent") or 100)
    if kind == "expiry":
        exp, days = _ts(u.get("expireAt")), float(trigger.get("days") or 0)
        if exp is None:
            return False
        if trigger.get("when") == "expired":
            return exp <= now and (not days or now - exp <= days * 86400)
        return u.get("status") == "ACTIVE" and now < exp <= now + days * 86400
    return False


# ---------------- сквады: применение и откат
def plan_squads(actions: list[dict], squads: list[str]) -> tuple[list[str], dict, list[str]]:
    """(новые сквады, что сделали {added, removed, expect}, ноды для разрыва соединений).
    expect — куда перемещали: пропал к моменту отката — значит, сквады правили руками."""
    cur = list(squads)
    drop: list[str] = []
    expect: list[str] = []
    for a in actions:
        t = a.get("type")
        if t == "squad_move" and a.get("from") in cur:
            cur = [s for s in cur if s != a["from"]] + ([] if a.get("to") in cur or not a.get("to") else [a["to"]])
            expect.append(a["to"])
            drop += a.get("drop_nodes") or []
        elif t == "squad_add" and a.get("squad") and a["squad"] not in cur:
            cur.append(a["squad"])
        elif t == "squad_remove" and a.get("squad") in cur:
            cur = [s for s in cur if s != a["squad"]]
            drop += a.get("drop_nodes") or []
    added = [s for s in cur if s not in squads]
    removed = [s for s in squads if s not in cur]
    return cur, {"added": added, "removed": removed, "expect": [s for s in expect if s in cur]}, drop


def revert_squads(applied: dict, squads: list[str]) -> list[str] | None:
    """Новый список сквадов для отката; None — сквады меняли руками, не трогаем."""
    added, removed = applied.get("added") or [], applied.get("removed") or []
    if not added and not removed:
        return list(squads)
    expect = set(added) | set(applied.get("expect") or [])
    if any(s not in squads for s in expect) or any(s in squads for s in removed):
        return None
    return [s for s in squads if s not in added] + removed


async def _log(db, auto: dict, u: dict, event: str, detail: str = ""):
    await db.execute("INSERT INTO automation_log(ts,auto_id,user_id,username,event,detail) VALUES(?,?,?,?,?,?)",
                     (int(time.time()), auto["id"], u.get("id"), u.get("username"), event, detail))


async def _set_squads(uid: int, squads: list[str]):
    await rw.user_update({"id": uid, "activeInternalSquads": squads})


# ---------------- прогон
async def run(only: int | None = None, dry: bool = False) -> dict:
    """Один прогон всех (или одной) автоматизаций. dry — только посчитать, кого затронет."""
    async with _lock:
        db = await db_ready()
        autos = [a for a in await all_automations() if (only is None or a["id"] == only) and not is_event(a)]
        async with db.execute("SELECT COUNT(*) FROM automation_state") as cur:
            if not autos and not (await cur.fetchone())[0]:
                return {}  # нечего проверять и нечего откатывать — Remnawave не дёргаем
        users = await rw.users_all()
        by_id = {u.get("id"): u for u in users if u.get("id")}
        squads_now = {uid: squad_ids(u) for uid, u in by_id.items()}
        qctx = await quota_ctx()
        now = time.time()
        async with db.execute("SELECT * FROM automation_state ORDER BY since DESC, rowid DESC") as cur:
            states = [dict(r) for r in await cur.fetchall()]
        summary = {a["id"]: {"matched": 0, "applied": [], "reverted": [], "forgotten": [], "would_apply": [],
                             "would_revert": [], "errors": []} for a in autos}

        want: dict[int, set[int]] = {}
        for a in autos:
            live = a["enabled"] and a["mode"] == "active" and not dry
            filt = a["trigger"].get("squad") or ""
            held = {s["user_id"] for s in states if s["auto_id"] == a["id"]}
            ok = set()
            for uid, u in by_id.items():
                if matches(a["trigger"], u, qctx, now) and (uid in held or not filt or filt in squads_now[uid]):
                    ok.add(uid)
            want[a["id"]] = ok if a["enabled"] else set()
            summary[a["id"]]["matched"] = len(ok)
            if not live:
                summary[a["id"]]["would_apply"] = sorted(by_id[x]["username"] for x in ok - held)[:200]
                summary[a["id"]]["would_revert"] = sorted((s["username"] for s in states
                                                           if s["auto_id"] == a["id"] and s["user_id"] not in ok))[:200]

        autos_by_id = {a["id"]: a for a in autos}
        # 1) откаты — от поздних применений к ранним
        for s in states:
            a = autos_by_id.get(s["auto_id"])
            if a is None or dry or s["user_id"] in want.get(a["id"], set()) and a["mode"] == "active":
                continue
            u = by_id.get(s["user_id"]) or {"id": s["user_id"], "username": s["username"]}
            applied = json.loads(s["applied"] or "{}")
            try:
                if s["user_id"] in squads_now:
                    new = revert_squads(applied, squads_now[s["user_id"]])
                    if new is None:
                        summary[a["id"]]["forgotten"].append(u["username"])
                        await _log(db, a, u, "forget", "сквады поменяли вручную — откат пропущен")
                    else:
                        if new != squads_now[s["user_id"]]:
                            await _set_squads(s["user_id"], new)
                            squads_now[s["user_id"]] = new
                        summary[a["id"]]["reverted"].append(u["username"])
                        await _log(db, a, u, "revert", json.dumps(applied, ensure_ascii=False))
                await db.execute("DELETE FROM automation_state WHERE auto_id=? AND user_id=?", (a["id"], s["user_id"]))
            except Exception as e:  # noqa: BLE001
                summary[a["id"]]["errors"].append(f"{u['username']}: {e}")
                await _log(db, a, u, "error", f"откат: {e}")

        # 2) применения
        async with db.execute("SELECT auto_id, user_id FROM automation_state") as cur:
            held_now = {(r["auto_id"], r["user_id"]) for r in await cur.fetchall()}
        for a in autos:
            if not (a["enabled"] and a["mode"] == "active") or dry:
                continue
            sq_actions = [x for x in a["actions"] if x.get("type") in SQUAD_ACTIONS]
            for uid in sorted(want[a["id"]]):
                if (a["id"], uid) in held_now:
                    continue
                u = by_id[uid]
                try:
                    new, applied, drop = plan_squads(sq_actions, squads_now[uid])
                    added, removed = applied["added"], applied["removed"]
                    if added or removed:
                        await _set_squads(uid, new)
                        squads_now[uid] = new
                        if drop and removed:
                            try:
                                await rw.drop_connections([uid], sorted(set(drop)))
                            except Exception as e:  # noqa: BLE001 — сквад уже сменён, это главное
                                log.warning("drop connections %s: %s", uid, e)
                    await db.execute("INSERT INTO automation_state VALUES(?,?,?,?,?)",
                                     (a["id"], uid, u.get("username"), int(now),
                                      json.dumps(applied, ensure_ascii=False)))
                    summary[a["id"]]["applied"].append(u.get("username"))
                    await _log(db, a, u, "apply", json.dumps(applied, ensure_ascii=False))
                except Exception as e:  # noqa: BLE001
                    summary[a["id"]]["errors"].append(f"{u.get('username')}: {e}")
                    await _log(db, a, u, "error", f"применение: {e}")

        for a in autos:
            s = summary[a["id"]]
            if not dry:
                await db.execute("UPDATE automations SET last_run=?, last_result=? WHERE id=?",
                                 (int(now), json.dumps(s, ensure_ascii=False), a["id"]))
                if s["applied"] or s["reverted"]:
                    await add_event("action", "info", f"Автоматизация «{a['name']}»: применена к "
                                                      f"{len(s['applied'])}, откат у {len(s['reverted'])}")
                if s["errors"]:
                    await add_event("action", "warn", f"Автоматизация «{a['name']}»: ошибок {len(s['errors'])} — "
                                                      f"{s['errors'][0]}")
        await db.execute("DELETE FROM automation_log WHERE ts<?", (int(now) - 60 * 86400,))
        await db.commit()
        return summary


async def revert_all(auto_id: int) -> dict:
    """Откатить всё, что сделала автоматизация (перед выключением, удалением, сменой действий)."""
    db = await db_ready()
    await db.execute("UPDATE automations SET enabled=0 WHERE id=?", (auto_id,))
    await db.commit()
    res = await run(only=auto_id)
    return res.get(auto_id, {})


# ---------------- события: обновление подписки
_events: dict = {"ts": 0.0, "v": []}


async def event_automations() -> list[dict]:
    if time.monotonic() - _events["ts"] > 10:
        _events["v"] = [a for a in await all_automations() if a["enabled"] and is_event(a)]
        _events["ts"] = time.monotonic()
    return _events["v"]


def drop_event_cache():
    _events["ts"] = 0


def ua_matches(trigger: dict, ua: str) -> bool:
    words = [w.strip().casefold() for w in (trigger.get("ua") or "").split(",") if w.strip()]
    return any(w in (ua or "").casefold() for w in words)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def apply_once(a: dict, u: dict) -> str:
    """Разовые действия. Данные пользователя — свежие из Remnawave, а не из кеша прослойки."""
    fresh = await rw.user_find("username", u["username"])
    squads = squad_ids(fresh)
    body, done = {"id": fresh.get("id") or u["id"]}, []
    for x in a["actions"]:
        t = x.get("type")
        if t == "extend_days":
            now = time.time()
            base = max(_ts(fresh.get("expireAt")) or now, now)  # истекла — считаем от сегодня
            # status не передаём: Remnawave сама переводит EXPIRED → ACTIVE, когда новая дата
            # в будущем, и возвращает пользователя на ноды
            body["expireAt"] = _iso(base + int(x["days"]) * 86400)
            done.append(f"+{x['days']} дн. → до {body['expireAt'][:10]}")
        elif t == "squad_add" and x.get("squad") and x["squad"] not in squads:
            squads = squads + [x["squad"]]
            body["activeInternalSquads"] = squads
            done.append("+ сквад")
        elif t == "squad_remove" and x.get("squad") in squads:
            squads = [s for s in squads if s != x["squad"]]
            body["activeInternalSquads"] = squads
            done.append("− сквад")
    if len(body) > 1:
        await rw.user_update(body)
    return ", ".join(done) or "нечего менять"


async def on_sub_request(u: dict, ua: str):
    """Вызывается прослойкой на каждое успешное обновление подписки (в фоне)."""
    autos = [a for a in await event_automations() if ua_matches(a["trigger"], ua)]
    if not autos:
        return
    db = await db_ready()
    for a in autos:
        filt = a["trigger"].get("squad") or ""
        if filt and filt not in squad_ids(u):
            continue
        if a["mode"] != "active":
            async with db.execute("SELECT 1 FROM automation_log WHERE auto_id=? AND user_id=? AND event='would'",
                                  (a["id"], u["id"])) as cur:
                if await cur.fetchone():
                    continue
            await _log(db, a, u, "would", f"сработала бы · UA: {ua[:100]}")
            await db.commit()
            continue
        cur = await db.execute("INSERT OR IGNORE INTO automation_once VALUES(?,?,?,?)",
                               (a["id"], u["id"], u.get("username"), int(time.time())))
        await db.commit()
        if cur.rowcount == 0:
            continue  # уже получал — единоразово
        try:
            detail = await apply_once(a, u)
            await _log(db, a, u, "apply", f"{detail} · UA: {ua[:80]}")
            await add_event("action", "info", f"Автоматизация «{a['name']}»: {u.get('username')} — {detail}")
        except Exception as e:  # noqa: BLE001 — снимаем отметку, повторится при следующем обновлении
            await db.execute("DELETE FROM automation_once WHERE auto_id=? AND user_id=?", (a["id"], u["id"]))
            await _log(db, a, u, "error", f"{e}"[:300])
            log.warning("event automation %s for %s: %s", a["id"], u.get("username"), e)
        await db.commit()


# ---------------- для прослойки и квот
async def held_users(rule_id: int) -> set[int]:
    """Кого держат автоматизации этого правила квоты: они остаются «под квотой», даже если
    автоматизация уже сняла с них сквад с лимитом — иначе счётчик бы замер."""
    db = await db_ready()
    out = set()
    async with db.execute("SELECT a.trigger, s.user_id FROM automation_state s "
                          "JOIN automations a ON a.id=s.auto_id") as cur:
        for r in await cur.fetchall():
            t = json.loads(r["trigger"] or "{}")
            if t.get("type") == "quota" and int(t.get("rule_id") or 0) == rule_id:
                out.add(r["user_id"])
    return out


async def sub_effects(user_id: int) -> tuple[list[str], set[str]]:
    """(сообщения для подписки, названия скрываемых хостов) — от активных автоматизаций пользователя."""
    db = await db_ready()
    msgs, hide = [], set()
    async with db.execute("SELECT a.actions FROM automation_state s JOIN automations a ON a.id=s.auto_id "
                          "WHERE s.user_id=? AND a.enabled=1 AND a.mode='active' ORDER BY a.id", (user_id,)) as cur:
        for r in await cur.fetchall():
            for x in json.loads(r["actions"] or "[]"):
                if x.get("type") == "sub_message" and (x.get("text") or "").strip():
                    msgs.append(x["text"].strip())
                elif x.get("type") == "sub_hide":
                    hide |= set(x.get("hosts") or [])
    return msgs, hide


def wake():
    _wake.set()


async def loop_forever():
    await asyncio.sleep(90)  # после первого прогона квот
    while True:
        try:
            if rw.configured:
                await run()
        except Exception:  # noqa: BLE001
            log.exception("automations loop")
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=max(INTERVAL, settings.poll_interval))
        except asyncio.TimeoutError:
            pass
