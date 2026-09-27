"""API квот трафика на ноды."""
import json
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import quota
from .auth import require_auth
from .config import settings
from .db import get_db
from .events import add_event
from .remnawave import rw

router = APIRouter(prefix="/api", tags=["quota"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    nodes: list[str] = Field(min_length=1)
    limit_gb: float = Field(gt=0, le=1_000_000)
    period: str = "month"                          # month | days
    period_days: int = Field(30, ge=1, le=366)
    anchor: str = ""                               # для period=days: дата начала отсчёта
    full_squad: str = Field(min_length=1)
    fallback_squad: str = Field(min_length=1)
    write_desc: bool = True
    desc_ok: str = quota.DESC_OK
    desc_over: str = quota.DESC_OVER
    mode: str = "dry"                              # dry | desc | active
    enabled: bool = True


def check(body: RuleIn):
    if body.full_squad == body.fallback_squad:
        raise HTTPException(400, "«Сквад с лимитом» и «запасной сквад» должны быть разными")
    if body.period not in ("month", "days") or body.mode not in ("dry", "desc", "active"):
        raise HTTPException(400, "Неверный период или режим")
    if body.period == "days":
        try:
            date.fromisoformat(body.anchor or date.today().isoformat())
        except ValueError as e:
            raise HTTPException(400, "Дата начала — в формате ГГГГ-ММ-ДД") from e


def values(body: RuleIn) -> tuple:
    return (body.name.strip(), json.dumps(body.nodes), int(body.limit_gb * quota.GB), body.period,
            body.period_days, body.anchor or date.today().isoformat(), body.full_squad,
            body.fallback_squad, int(body.write_desc), body.desc_ok.strip() or quota.DESC_OK,
            body.desc_over.strip() or quota.DESC_OVER, body.mode, int(body.enabled))


def row(r) -> dict:
    d = dict(r)
    d["nodes"] = json.loads(d.pop("node_uuids") or "[]")
    d["limit_gb"] = round((d.pop("limit_bytes") or 0) / quota.GB, 2)
    d["last_result"] = json.loads(d["last_result"]) if d.get("last_result") else None
    d["exempt"] = json.loads(d.get("exempt") or "[]")
    start, reset, key = quota.period_of(d)
    d.update(period_start=start.isoformat(), reset=reset.isoformat(), period_key=key)
    return d


async def get_rule(rid: int) -> dict:
    db = await get_db()
    async with db.execute("SELECT * FROM quota_rules WHERE id=?", (rid,)) as cur:
        r = await cur.fetchone()
    if not r:
        raise HTTPException(404, "Правило не найдено")
    return dict(r)


class IntervalIn(BaseModel):
    minutes: int = Field(ge=1, le=1440)


@router.get("/quotas/interval")
async def quota_interval_get(_: str = Depends(require_auth)):
    return {"minutes": settings.quota_interval // 60, "next_run": int(quota.next_run_at) or None}


@router.put("/quotas/interval")
async def quota_interval_set(body: IntervalIn, user: str = Depends(require_auth)):
    settings.update({"QUOTA_INTERVAL": str(body.minutes * 60)})
    quota.wake()                                   # применить сразу, не дожидаясь старой паузы
    await add_event("action", "info", f"{user}: квоты проверяются каждые {body.minutes} мин.")
    return {"minutes": settings.quota_interval // 60}


@router.get("/quotas")
async def rules_list(_: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM quota_rules ORDER BY id") as cur:
        return [row(r) for r in await cur.fetchall()]


@router.post("/quotas")
async def rule_add(body: RuleIn, user: str = Depends(require_auth)):
    check(body)
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO quota_rules(name,node_uuids,limit_bytes,period,period_days,anchor,full_squad,
           fallback_squad,write_desc,desc_ok,desc_over,mode,enabled,created)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*values(body), int(time.time())))
    await db.commit()
    await add_event("action", "info", f"{user}: создана квота «{body.name}» ({body.limit_gb:g} ГБ)")
    return {"ok": True, "id": cur.lastrowid}


@router.put("/quotas/{rid}")
async def rule_edit(rid: int, body: RuleIn, user: str = Depends(require_auth)):
    check(body)
    await get_rule(rid)
    db = await get_db()
    await db.execute(
        """UPDATE quota_rules SET name=?,node_uuids=?,limit_bytes=?,period=?,period_days=?,anchor=?,
           full_squad=?,fallback_squad=?,write_desc=?,desc_ok=?,desc_over=?,mode=?,enabled=? WHERE id=?""",
        (*values(body), rid))
    await db.commit()
    await add_event("action", "info", f"{user}: изменена квота «{body.name}»")
    quota.wake()  # новый лимит — сразу пересчитать: поднятый вернёт доступ, сниженный переведёт
    return {"ok": True}


@router.delete("/quotas/{rid}")
async def rule_delete(rid: int, restore: bool = True, user: str = Depends(require_auth)):
    rule = await get_rule(rid)
    back = await quota.restore_all(rule) if restore else []
    db = await get_db()
    await db.execute("DELETE FROM quota_rules WHERE id=?", (rid,))
    await db.execute("DELETE FROM quota_users WHERE rule_id=?", (rid,))
    await db.commit()
    await add_event("action", "warn", f"{user}: удалена квота «{rule['name']}»"
                                      + (f", вернул доступ {len(back)}" if back else ""))
    return {"ok": True, "restored": back}


@router.post("/quotas/{rid}/run")
async def rule_run(rid: int, dry: bool = False, _: str = Depends(require_auth)):
    return await quota.run_rule(await get_rule(rid), force_dry=dry)


@router.post("/quotas/{rid}/restore")
async def rule_restore(rid: int, user: str = Depends(require_auth)):
    rule = await get_rule(rid)
    back = await quota.restore_all(rule)
    await add_event("action", "warn", f"{user}: квота «{rule['name']}» — вернул доступ {len(back)} вручную")
    return {"ok": True, "restored": back}


@router.get("/quotas/{rid}/users")
async def rule_users(rid: int, _: str = Depends(require_auth)):
    rule = await get_rule(rid)
    db = await get_db()
    async with db.execute(
            "SELECT * FROM quota_users WHERE rule_id=? ORDER BY moved DESC, used DESC", (rid,)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    limit = rule["limit_bytes"] or 1
    for r in rows:
        r["percent"] = min(100, round(r["used"] * 100 / limit))
    return rows


@router.get("/users/{uid}/quotas")
async def user_quotas(uid: str, _: str = Depends(require_auth)):
    """Все правила и статус пользователя в каждом — для блока «Квоты» в его карточке."""
    if not uid.isdigit():
        return []
    user = await rw.user(uid)
    squads = quota.squad_ids(user)
    db = await get_db()
    async with db.execute("SELECT * FROM quota_rules ORDER BY id") as cur:
        rules = [dict(r) for r in await cur.fetchall()]
    async with db.execute("SELECT * FROM quota_users WHERE user_id=?", (int(uid),)) as cur:
        state = {r["rule_id"]: dict(r) for r in await cur.fetchall()}
    out = []
    for r in rules:
        st = state.get(r["id"]) or {}
        exempt = int(uid) in {int(x) for x in json.loads(r.get("exempt") or "[]")}
        moved = bool(st.get("moved"))
        has_full = r["full_squad"] in squads
        status = ("moved" if moved else "exempt" if exempt and has_full
                  else "in" if has_full else "out")
        _, reset, _ = quota.period_of(r)
        out.append({"rule_id": r["id"], "rule": r["name"], "status": status, "mode": r["mode"],
                    "enabled": bool(r["enabled"]), "used": st.get("used") or 0,
                    "limit": r["limit_bytes"], "reset": reset.isoformat(), "descr": st.get("descr")})
    return out


class UserQuotaIn(BaseModel):
    action: str                                    # grant | exempt | include | restore


@router.post("/users/{uid}/quotas/{rid}")
async def user_quota_action(uid: str, rid: int, body: UserQuotaIn, user: str = Depends(require_auth)):
    """grant — выдать сквад с лимитом (попасть под квоту); exempt — исключить из квоты,
    доступ остаётся без лимита; include — снова под квоту; restore — вернуть доступ досрочно."""
    if not uid.isdigit():
        raise HTTPException(400, "Нужен числовой id пользователя (Remnawave 3.4+)")
    rule = await get_rule(rid)
    target = await rw.user(uid)
    name = target.get("username") or uid
    squads = quota.squad_ids(target)
    db = await get_db()
    exempt = [int(x) for x in json.loads(rule.get("exempt") or "[]")]

    async def save_exempt(ids):
        await db.execute("UPDATE quota_rules SET exempt=? WHERE id=?", (json.dumps(sorted(set(ids))), rid))
        await db.commit()

    async def restore_now():
        async with db.execute("SELECT * FROM quota_users WHERE rule_id=? AND user_id=?", (rid, int(uid))) as cur:
            st = await cur.fetchone()
        if not st or not st["moved"]:
            return False
        drop_fb = st["added_fb"] == 1
        new = [s for s in squads if not (drop_fb and s == rule["fallback_squad"])]
        if rule["full_squad"] not in new:
            new.append(rule["full_squad"])
        await rw.user_update({"id": int(uid), "activeInternalSquads": new})
        await db.execute("UPDATE quota_users SET moved=0, moved_period=NULL WHERE rule_id=? AND user_id=?", (rid, int(uid)))
        await db.commit()
        return True

    if body.action == "grant":
        if rule["full_squad"] not in squads:
            await rw.user_update({"id": int(uid), "activeInternalSquads": squads + [rule["full_squad"]]})
        msg = f"{name}: выдан сквад с лимитом — теперь под квотой «{rule['name']}»"
    elif body.action == "exempt":
        await save_exempt(exempt + [int(uid)])
        back = await restore_now()
        msg = f"{name}: исключён из квоты «{rule['name']}»" + (" — доступ возвращён" if back else "")
    elif body.action == "include":
        await save_exempt([x for x in exempt if x != int(uid)])
        msg = f"{name}: снова под квотой «{rule['name']}»"
    elif body.action == "restore":
        if not await restore_now():
            raise HTTPException(400, "Пользователь сейчас не ограничен этим правилом")
        msg = f"{name}: доступ по квоте «{rule['name']}» возвращён досрочно"
    else:
        raise HTTPException(400, "Неизвестное действие")
    await add_event("quota", "info", f"{user}: {msg}")
    return {"ok": True, "message": msg}


@router.get("/quotas/{rid}/explain")
async def quota_explain(rid: int, username: str, _: str = Depends(require_auth)):
    return await quota.explain(await get_rule(rid), username)


class AnnounceIn(BaseModel):
    text: str = Field("📊 {{DESCRIPTION}}", max_length=200)
    remove: bool = False


@router.post("/quotas/announce")
async def quota_announce(body: AnnounceIn, user: str = Depends(require_auth)):
    """Баннер вверху подписки в Happ: заголовок announce с шаблоном. Остальные
    заголовки подписки не трогаем — только дописываем или убираем свой."""
    cur = await rw.subscription_settings()
    headers = dict(cur.get("customResponseHeaders") or {})
    if body.remove:
        headers.pop("announce", None)
    else:
        headers["announce"] = "rwEncodeBase64:" + body.text
    await rw.subscription_settings_update({"uuid": cur["uuid"], "customResponseHeaders": headers})
    await add_event("action", "info", f"{user}: анонс подписки {'убран' if body.remove else 'включён'}")
    return {"ok": True, "headers": headers}
