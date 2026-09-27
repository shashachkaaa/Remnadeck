"""API автоматизаций (см. automations.py)."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import automations as auto
from .auth import require_auth
from .db import get_db
from .events import add_event
from .remnawave import rw

router = APIRouter(prefix="/api/automations", tags=["automations"])


class AutoIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    enabled: bool = True
    mode: str = "dry"
    trigger: dict
    actions: list[dict] = Field(min_length=1, max_length=10)


def validate(body: AutoIn) -> tuple[dict, list[dict]]:
    if body.mode not in ("dry", "active"):
        raise HTTPException(400, "mode: dry или active")
    t = body.trigger
    if t.get("type") == "quota":
        trig = {"type": "quota", "rule_id": int(t.get("rule_id") or 0), "percent": float(t.get("percent") or 0)}
        if not trig["rule_id"] or not 1 <= trig["percent"] <= 1000:
            raise HTTPException(400, "Выбери правило квоты и порог от 1 до 1000%")
    elif t.get("type") == "expiry":
        trig = {"type": "expiry", "when": t.get("when"), "days": int(t.get("days") or 0)}
        if trig["when"] not in ("before", "expired") or not 0 <= trig["days"] <= 365:
            raise HTTPException(400, "Срок: «истекает через» или «истекла», дней от 0 до 365")
        if trig["when"] == "before" and not trig["days"]:
            raise HTTPException(400, "«Истекает через» — укажи число дней")
    elif t.get("type") == "sub_request":
        words = ", ".join(w.strip() for w in (t.get("ua") or "").split(",") if w.strip())
        if not words or len(words) > 200:
            raise HTTPException(400, "Укажи слово из User-Agent (можно несколько через запятую)")
        trig = {"type": "sub_request", "ua": words}
    else:
        raise HTTPException(400, "Неизвестное условие")
    trig["squad"] = (t.get("squad") or "").strip()
    event = trig["type"] in auto.EVENT_TRIGGERS

    actions = []
    for a in body.actions:
        kind = a.get("type")
        drop = [x for x in (a.get("drop_nodes") or []) if isinstance(x, str)]
        if event and kind not in auto.EVENT_ACTIONS:
            raise HTTPException(400, "Для обновления подписки подходят: продлить подписку, добавить или убрать сквад")
        if not event and kind == "extend_days":
            raise HTTPException(400, "Продление — только для условия «обновил подписку»: иначе оно повторялось бы")
        if kind == "squad_move":
            if not a.get("from") or not a.get("to") or a["from"] == a["to"]:
                raise HTTPException(400, "Переместить: укажи два разных сквада")
            actions.append({"type": kind, "from": a["from"], "to": a["to"], "drop_nodes": drop})
        elif kind in ("squad_add", "squad_remove"):
            if not a.get("squad"):
                raise HTTPException(400, "Укажи сквад")
            actions.append({"type": kind, "squad": a["squad"], **({"drop_nodes": drop} if kind == "squad_remove" else {})})
        elif kind == "extend_days":
            days = int(a.get("days") or 0)
            if not 1 <= days <= 3650:
                raise HTTPException(400, "Продлить: от 1 до 3650 дней")
            actions.append({"type": kind, "days": days})
        elif kind == "sub_message":
            text = (a.get("text") or "").strip()
            if not text or len(text) > 300:
                raise HTTPException(400, "Сообщение: от 1 до 300 символов")
            actions.append({"type": kind, "text": text})
        elif kind == "sub_hide":
            hosts = [h for h in (a.get("hosts") or []) if isinstance(h, str) and h]
            if not hosts:
                raise HTTPException(400, "Скрыть хосты: выбери хотя бы один")
            actions.append({"type": kind, "hosts": hosts})
        else:
            raise HTTPException(400, f"Неизвестное действие: {kind}")
    return trig, actions


async def get_auto(aid: int) -> dict:
    db = await auto.db_ready()
    async with db.execute("SELECT * FROM automations WHERE id=?", (aid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Автоматизация не найдена")
    return auto.parse(row)


@router.get("")
async def autos_list(_: str = Depends(require_auth)):
    db = await auto.db_ready()
    async with db.execute("SELECT auto_id, COUNT(*) c FROM automation_state GROUP BY auto_id") as cur:
        held = {r["auto_id"]: r["c"] for r in await cur.fetchall()}
    async with db.execute("SELECT auto_id, COUNT(*) c FROM automation_once GROUP BY auto_id") as cur:
        fired = {r["auto_id"]: r["c"] for r in await cur.fetchall()}
    async with db.execute("SELECT auto_id, COUNT(*) c FROM automation_log WHERE event='would' GROUP BY auto_id") as cur:
        would = {r["auto_id"]: r["c"] for r in await cur.fetchall()}
    return [{**a, "held": held.get(a["id"], 0), "event": auto.is_event(a), "fired": fired.get(a["id"], 0),
             "would": would.get(a["id"], 0)} for a in await auto.all_automations()]


@router.get("/meta")
async def autos_meta(_: str = Depends(require_auth)):
    """Всё для формы: правила квот, сквады, хосты, ноды."""
    db = await get_db()
    async with db.execute("SELECT id, name, limit_bytes, mode, write_desc FROM quota_rules ORDER BY id") as cur:
        rules = [dict(r) for r in await cur.fetchall()]
    squads = [{"uuid": s.get("uuid"), "name": s.get("name")} for s in await rw.squads()]
    hosts = sorted({h["remark"] for h in await rw.hosts() if h.get("remark") and not h.get("isDisabled")})
    nodes = [{"uuid": n.get("uuid"), "name": n.get("name"), "country": n.get("countryCode")} for n in await rw.nodes()]
    return {"rules": rules, "squads": squads, "hosts": hosts, "nodes": nodes}


@router.post("")
async def autos_create(body: AutoIn, user: str = Depends(require_auth)):
    trig, actions = validate(body)
    db = await auto.db_ready()
    cur = await db.execute("INSERT INTO automations(name,enabled,mode,trigger,actions,created) VALUES(?,?,?,?,?,?)",
                           (body.name.strip(), int(body.enabled), body.mode, json.dumps(trig, ensure_ascii=False),
                            json.dumps(actions, ensure_ascii=False), int(time.time())))
    await db.commit()
    auto.drop_event_cache()
    await add_event("action", "info", f"{user}: создана автоматизация «{body.name.strip()}»")
    res = await auto.run(only=cur.lastrowid)
    return {"ok": True, "id": cur.lastrowid, "result": res.get(cur.lastrowid)}


@router.put("/{aid}")
async def autos_update(aid: int, body: AutoIn, user: str = Depends(require_auth)):
    old = await get_auto(aid)
    trig, actions = validate(body)
    squads = lambda xs: [x for x in xs if x["type"] in auto.SQUAD_ACTIONS]  # noqa: E731
    if not auto.is_event(old) and squads(old["actions"]) != squads(actions):
        # held-пользователи получили старые действия — откатываем их, новые применятся заново
        await auto.revert_all(aid)
    db = await auto.db_ready()
    await db.execute("UPDATE automations SET name=?, enabled=?, mode=?, trigger=?, actions=? WHERE id=?",
                     (body.name.strip(), int(body.enabled), body.mode, json.dumps(trig, ensure_ascii=False),
                      json.dumps(actions, ensure_ascii=False), aid))
    await db.commit()
    auto.drop_event_cache()
    await add_event("action", "info", f"{user}: изменена автоматизация «{body.name.strip()}»")
    res = await auto.run(only=aid)  # выключили / тест — тут же откатит; боевой — тут же применит
    return {"ok": True, "result": res.get(aid)}


@router.delete("/{aid}")
async def autos_delete(aid: int, user: str = Depends(require_auth)):
    a = await get_auto(aid)
    res = await auto.revert_all(aid)
    if res.get("errors"):
        raise HTTPException(409, f"Не удалось откатить: {res['errors'][0]}. Автоматизация выключена, но не удалена")
    db = await auto.db_ready()
    await db.execute("DELETE FROM automations WHERE id=?", (aid,))
    await db.execute("DELETE FROM automation_state WHERE auto_id=?", (aid,))
    await db.execute("DELETE FROM automation_once WHERE auto_id=?", (aid,))
    await db.commit()
    auto.drop_event_cache()
    await add_event("action", "info", f"{user}: удалена автоматизация «{a['name']}» "
                                      f"(откат у {len(res.get('reverted', []))})")
    return {"ok": True, "reverted": res.get("reverted", []), "forgotten": res.get("forgotten", [])}


@router.post("/{aid}/run")
async def autos_run(aid: int, _: str = Depends(require_auth)):
    await get_auto(aid)
    return (await auto.run(only=aid)).get(aid)


@router.post("/{aid}/preview")
async def autos_preview(aid: int, _: str = Depends(require_auth)):
    """Кого затронет прямо сейчас — ничего не меняя."""
    await get_auto(aid)
    return (await auto.run(only=aid, dry=True)).get(aid)


@router.get("/{aid}/log")
async def autos_log(aid: int, _: str = Depends(require_auth)):
    db = await auto.db_ready()
    if auto.is_event(await get_auto(aid)):  # «сейчас действует» у событий — кто уже получил
        async with db.execute("SELECT user_id, username, ts AS since FROM automation_once WHERE auto_id=? "
                              "ORDER BY ts DESC", (aid,)) as cur:
            held = [dict(r) for r in await cur.fetchall()]
    else:
        async with db.execute("SELECT user_id, username, since, applied FROM automation_state WHERE auto_id=? "
                              "ORDER BY since DESC", (aid,)) as cur:
            held = [{**dict(r), "applied": json.loads(r["applied"] or "{}")} for r in await cur.fetchall()]
    async with db.execute("SELECT ts, username, event, detail FROM automation_log WHERE auto_id=? "
                          "ORDER BY id DESC LIMIT 200", (aid,)) as cur:
        log = [dict(r) for r in await cur.fetchall()]
    return {"held": held, "log": log}
