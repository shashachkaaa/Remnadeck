"""Пользователи, сквады и системные инструменты Remnawave.

Остальные контроллеры API панели: users (создание, правка, поиск, массовые
действия), hwid (устройства), internal-squads, system/tools и статистика.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_auth
from .events import add_event
from .remnawave import rw

router = APIRouter(prefix="/api", tags=["users"])

STRATEGIES = ("NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING")
STATUSES = ("ACTIVE", "DISABLED", "LIMITED", "EXPIRED")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- пользователи
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=36)          # ограничения панели 3.4
    expire_days: int | None = None            # None — не трогать дату
    expire_at: str = ""                       # либо точная дата ISO
    traffic_limit_gb: float = Field(0, ge=0)
    traffic_strategy: str = "NO_RESET"
    squads: list[str] = []
    telegram_id: str = ""
    email: str = ""
    tag: str = ""
    description: str = ""
    hwid_limit: int | None = None


def user_payload(body: UserIn, creating: bool) -> dict:
    if body.traffic_strategy not in STRATEGIES:
        raise HTTPException(400, f"Стратегия сброса: {', '.join(STRATEGIES)}")
    out: dict = {
        "username": body.username.strip(),
        "trafficLimitBytes": int(body.traffic_limit_gb * 1024 ** 3),
        "trafficLimitStrategy": body.traffic_strategy,
        "activeInternalSquads": body.squads,
        "description": body.description.strip() or None,
        "tag": (body.tag.strip().upper() or None),
        "email": body.email.strip() or None,
    }
    if body.telegram_id.strip():
        try:
            out["telegramId"] = int(body.telegram_id.strip())
        except ValueError as e:
            raise HTTPException(400, "Telegram ID — это число") from e
    else:
        out["telegramId"] = None
    if body.hwid_limit is not None:
        out["hwidDeviceLimit"] = body.hwid_limit
    if body.expire_at.strip():
        out["expireAt"] = body.expire_at.strip()
    elif body.expire_days is not None:
        out["expireAt"] = iso(datetime.now(timezone.utc) + timedelta(days=body.expire_days))
    elif creating:
        out["expireAt"] = iso(datetime.now(timezone.utc) + timedelta(days=30))
    return out


BAD_UUID = ("", "null", "undefined", "none")


@router.get("/users/{uuid}/full")
async def user_full(uuid: str, short: str = "", _: str = Depends(require_auth)):
    """short — запасной путь: если в списке не было uuid, ищем по short uuid."""
    if uuid.lower() in BAD_UUID:
        if not short:
            raise HTTPException(400, "Панель не вернула идентификатор этого пользователя")
        found = await rw.user_find("short-uuid", short)
        if isinstance(found, dict):
            uuid = str(found.get("id") or found.get("uuid") or "")
        if not uuid:
            raise HTTPException(404, "Пользователь не найден по short uuid")
    u = await rw.user(uuid)
    squads = [s if isinstance(s, str) else s.get("uuid")
              for s in (u.get("activeInternalSquads") or [])]
    return {
        "uuid": str(u.get("id") or u.get("uuid") or ""),         # в 3.4 — числовой id
        "username": u.get("username"), "status": u.get("status"),
        "short_uuid": u.get("shortUuid"), "subscription_url": u.get("subscriptionUrl"),
        "expire_at": u.get("expireAt"), "used": int(u.get("usedTrafficBytes") or 0),
        "lifetime": int(u.get("lifetimeUsedTrafficBytes") or 0),
        "traffic_limit_gb": round(int(u.get("trafficLimitBytes") or 0) / 1024 ** 3, 2),
        "traffic_strategy": u.get("trafficLimitStrategy") or "NO_RESET",
        "squads": squads, "telegram_id": str(u.get("telegramId") or ""),
        "email": u.get("email") or "", "tag": u.get("tag") or "",
        "description": u.get("description") or "",
        "hwid_limit": u.get("hwidDeviceLimit"),
        "online_at": u.get("onlineAt"), "created_at": u.get("createdAt"),
        "sub_last_opened": u.get("subLastOpenedAt"), "last_agent": u.get("subLastUserAgent"),
    }


@router.post("/users")
async def user_create(body: UserIn, user: str = Depends(require_auth)):
    res = await rw.user_create(user_payload(body, True))
    await add_event("action", "info", f"{user}: создан пользователь {body.username}")
    return {"ok": True, "uuid": (res or {}).get("uuid"),
            "subscription_url": (res or {}).get("subscriptionUrl")}


@router.put("/users/{uuid}")
async def user_update(uuid: str, body: UserIn, user: str = Depends(require_auth)):
    payload = user_payload(body, False)
    payload.update(rw.user_ref(uuid))
    await rw.user_update(payload)
    await add_event("action", "info", f"{user}: изменён пользователь {body.username}")
    return {"ok": True}


@router.delete("/users/{uuid}")
async def user_delete(uuid: str, user: str = Depends(require_auth)):
    await rw.user_delete(uuid)
    await add_event("action", "warn", f"{user}: удалён пользователь {uuid[:8]}")
    return {"ok": True}


@router.post("/users/{uuid}/revoke")
async def user_revoke(uuid: str, user: str = Depends(require_auth)):
    res = await rw.user_revoke(uuid)
    await add_event("action", "warn", f"{user}: перевыпущена подписка {uuid[:8]}")
    return {"ok": True, "subscription_url": (res or {}).get("subscriptionUrl")}


class ExtendIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.post("/users/{uuid}/extend")
async def user_extend(uuid: str, body: ExtendIn, user: str = Depends(require_auth)):
    await rw.user_extend(uuid, body.days)
    await add_event("action", "info", f"{user}: продлён пользователь {uuid} на {body.days} дн.")
    return {"ok": True}


@router.get("/users/{uuid}/history")
async def user_history(uuid: str, _: str = Depends(require_auth)):
    data = await rw.user_history(uuid)
    items = data.get("records", data.get("history", [])) if isinstance(data, dict) else (data or [])
    return [{"at": r.get("requestAt") or r.get("createdAt"), "ip": r.get("requestIp"),
             "agent": r.get("userAgent")} for r in items[:30]]


@router.get("/users/{uuid}/devices")
async def user_devices(uuid: str, _: str = Depends(require_auth)):
    return [{"hwid": d.get("hwid"), "platform": d.get("platform"),
             "model": d.get("deviceModel") or d.get("model"),
             "os": d.get("osVersion"), "app": d.get("userAgent") or d.get("appVersion"),
             "created_at": d.get("createdAt"), "updated_at": d.get("updatedAt")}
            for d in await rw.hwid_list(uuid)]


@router.delete("/users/{uuid}/devices")
async def user_devices_clear(uuid: str, hwid: str = "", user: str = Depends(require_auth)):
    if hwid:
        await rw.hwid_delete(uuid, hwid)
    else:
        await rw.hwid_delete_all(uuid)
    await add_event("action", "info", f"{user}: сброс устройств {uuid[:8]}")
    return {"ok": True}


@router.get("/users/{uuid}/nodes")
async def user_accessible_nodes(uuid: str, _: str = Depends(require_auth)):
    data = await rw.user_nodes(uuid)
    if isinstance(data, dict):
        data = data.get("activeNodes", data.get("nodes", []))
    return [{"name": n.get("nodeName") or n.get("name"),
             "country": n.get("countryCode"),
             "inbounds": n.get("activeInbounds") or n.get("inbounds") or []}
            for n in (data or [])]


@router.get("/user-search")
async def user_search(kind: str, value: str, _: str = Depends(require_auth)):
    if kind not in ("username", "short-uuid", "id"):
        raise HTTPException(400, "Панель 3.4 ищет по username, short-uuid или id")
    data = await rw.user_find(kind, value.strip())
    items = data if isinstance(data, list) else (data.get("users") if isinstance(data, dict) else None)
    if items is None:
        items = [data] if data else []
    return [{"uuid": str(u.get("id") or u.get("uuid") or ""), "username": u.get("username"),
             "status": u.get("status"),
             "telegram_id": str(u.get("telegramId") or ""), "tag": u.get("tag") or "",
             "expire_at": u.get("expireAt")} for u in items if isinstance(u, dict)]


class BulkIn(BaseModel):
    action: str
    uuids: list[str] = []
    status: str = ""
    traffic_strategy: str = ""
    expire_days: int | None = None
    squads: list[str] = []


@router.post("/users-bulk")
async def users_bulk(body: BulkIn, user: str = Depends(require_auth)):
    """Массовые действия над выбранными пользователями."""
    act = body.action
    if act in ("delete", "revoke-subscription", "reset-traffic"):
        if not body.uuids:
            raise HTTPException(400, "Никто не выбран")
        await rw.users_bulk(act, rw.users_ref(body.uuids))
    elif act == "extend":
        if not body.uuids or not body.expire_days:
            raise HTTPException(400, "Выбери пользователей и число дней")
        await rw.users_bulk("extend-expiration-date",
                            {**rw.users_ref(body.uuids), "extendDays": body.expire_days})
    elif act == "delete-by-status":
        if body.status not in STATUSES:
            raise HTTPException(400, f"Статус: {', '.join(STATUSES)}")
        await rw.users_bulk(act, {"status": body.status})
    elif act == "update":
        fields: dict = {}
        if body.traffic_strategy:
            fields["trafficLimitStrategy"] = body.traffic_strategy
        if body.expire_days is not None:
            fields["expireAt"] = iso(datetime.now(timezone.utc) + timedelta(days=body.expire_days))
        if not fields or not body.uuids:
            raise HTTPException(400, "Нечего менять или никто не выбран")
        await rw.users_bulk("update", {**rw.users_ref(body.uuids), "fields": fields})
    elif act == "update-squads":
        if not body.uuids or not body.squads:
            raise HTTPException(400, "Выбери пользователей и сквады")
        await rw.users_bulk("update-squads", {**rw.users_ref(body.uuids),
                                              "activeInternalSquads": body.squads})
    else:
        raise HTTPException(400, "Неизвестное массовое действие")
    await add_event("action", "warn", f"{user}: массовое действие {act} "
                                      f"({len(body.uuids) or body.status})")
    return {"ok": True}


@router.get("/users-raw")
async def users_raw(_: str = Depends(require_auth)):
    """Диагностика: какие поля панель реально отдаёт в списке пользователей."""
    items = await rw.users_all()
    if not items:
        return {"count": 0, "keys": [], "sample": {}}
    first = items[0]
    return {"count": len(items), "keys": sorted(first.keys()),
            "has_uuid": sum(1 for u in items if u.get("uuid")),
            "sample": {k: str(v)[:60] for k, v in first.items()}}


@router.get("/user-tags")
async def user_tags(_: str = Depends(require_auth)):
    return await rw.user_tags()


# ---------------- сквады
class SquadIn(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    inbounds: list[str] = []


@router.get("/squads")
async def squads_list(_: str = Depends(require_auth)):
    out = []
    for s in await rw.squads():
        info = s.get("info") or {}
        out.append({
            "uuid": s.get("uuid"), "name": s.get("name"),
            "users": info.get("membersCount", s.get("membersCount")),
            "inbounds_count": info.get("inboundsCount", len(s.get("inbounds") or [])),
            "inbounds": [i if isinstance(i, str) else i.get("uuid")
                         for i in (s.get("inbounds") or [])],
        })
    return out


@router.post("/squads")
async def squad_create(body: SquadIn, user: str = Depends(require_auth)):
    await rw.squad_create({"name": body.name.strip(), "inbounds": body.inbounds})
    await add_event("action", "info", f"{user}: создан сквад {body.name}")
    return {"ok": True}


@router.put("/squads/{uuid}")
async def squad_update(uuid: str, body: SquadIn, user: str = Depends(require_auth)):
    await rw.squad_update({"uuid": uuid, "name": body.name.strip(), "inbounds": body.inbounds})
    await add_event("action", "info", f"{user}: изменён сквад {body.name}")
    return {"ok": True}


@router.delete("/squads/{uuid}")
async def squad_delete(uuid: str, user: str = Depends(require_auth)):
    await rw.squad_delete(uuid)
    await add_event("action", "warn", f"{user}: удалён сквад {uuid[:8]}")
    return {"ok": True}


@router.post("/squads/{uuid}/{action}")
async def squad_users(uuid: str, action: str, user: str = Depends(require_auth)):
    if action not in ("add-all", "remove-all"):
        raise HTTPException(400, "Неизвестное действие")
    await rw.squad_users(uuid, action == "add-all")
    await add_event("action", "warn",
                    f"{user}: {'все пользователи добавлены в' if action == 'add-all' else 'все убраны из'} "
                    f"сквад {uuid[:8]}")
    return {"ok": True}


# ---------------- статистика и инструменты
@router.get("/metrics")
async def metrics(_: str = Depends(require_auth)):
    out: dict = {}
    for name, call in (("bandwidth", rw.bandwidth), ("nodes", rw.nodes_statistics),
                       ("recap", rw.stats_recap), ("devices", rw.hwid_stats)):
        try:
            out[name] = await call()
        except Exception as e:                            # noqa: BLE001
            out[name] = {"error": str(e)[:160]}           # версия панели может не знать метод
    return out


@router.get("/nodes/{uuid}/usage")
async def node_usage(uuid: str, days: int = 7, _: str = Depends(require_auth)):
    end = datetime.now(timezone.utc)
    return await rw.node_usage(uuid, iso(end - timedelta(days=max(1, days))), iso(end))


@router.get("/users/{uuid}/usage")
async def user_usage(uuid: str, days: int = 7, _: str = Depends(require_auth)):
    end = datetime.now(timezone.utc)
    return await rw.user_usage(uuid, iso(end - timedelta(days=max(1, days))), iso(end))


@router.get("/tools/x25519")
async def tool_x25519(_: str = Depends(require_auth)):
    data = await rw.x25519()
    # 3.4 отдаёт {keypairs: [...]}, раньше — одну пару
    if isinstance(data, dict) and data.get("keypairs"):
        return data["keypairs"][0]
    return data


class SrrIn(BaseModel):
    user_agent: str = ""
    srr: dict = {}


@router.post("/tools/srr")
async def tool_srr(body: SrrIn, _: str = Depends(require_auth)):
    payload = dict(body.srr)
    if body.user_agent.strip():
        payload.setdefault("userAgent", body.user_agent.strip())
    return await rw.srr_match(payload)
