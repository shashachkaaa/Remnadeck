"""Ноды и хосты: создание, правка, удаление через API Remnawave.

Правка идёт «поверх» объекта из панели: панель отдаёт полный объект, мы
меняем только присланные поля и отправляем обратно. Так незнакомые поля
(их набор отличается от версии к версии) не теряются.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_auth
from .events import add_event
from .remnawave import rw

router = APIRouter(prefix="/api", tags=["remnawave"])


# ---------------- профили конфигов (нужны для форм)
@router.get("/profiles")
async def profiles(_: str = Depends(require_auth)):
    out = []
    for p in await rw.profiles():
        inbounds = p.get("inbounds")
        if inbounds is None:
            inbounds = await rw.profile_inbounds(p["uuid"])
        out.append({
            "uuid": p.get("uuid"), "name": p.get("name"),
            "inbounds": [{
                "uuid": i.get("uuid"), "tag": i.get("tag"), "type": i.get("type"),
                "port": i.get("port"), "network": i.get("network"), "security": i.get("security"),
            } for i in inbounds],
        })
    return out


# ---------------- ноды
class NodeIn(BaseModel):
    name: str = Field(min_length=3, max_length=30)               # ограничения панели 3.4
    address: str = Field(min_length=2)
    port: int = Field(2222, ge=1, le=65535)
    country_code: str = "XX"
    profile_uuid: str = ""
    inbounds: list[str] = []
    traffic_limit_bytes: int = Field(0, ge=0)
    traffic_reset_day: int = Field(1, ge=1, le=31)
    notify_percent: int = Field(0, ge=0, le=100)
    consumption_multiplier: float = Field(1.0, gt=0, le=100)
    traffic_tracking: bool = False


def node_payload(body: NodeIn) -> dict:
    out = {
        "name": body.name.strip(),
        "address": body.address.strip(),
        "port": body.port,
        "countryCode": (body.country_code or "XX").upper()[:2],
        "isTrafficTrackingActive": body.traffic_tracking,
        "trafficLimitBytes": body.traffic_limit_bytes,
        "trafficResetDay": body.traffic_reset_day,
        "notifyPercent": body.notify_percent,
        "consumptionMultiplier": body.consumption_multiplier,
    }
    if body.profile_uuid:
        # 3.x ждёт вложенный объект, плоские поля панель отвергает
        out["configProfile"] = {"activeConfigProfileUuid": body.profile_uuid,
                                "activeInbounds": body.inbounds}
    return out


@router.post("/nodes")
async def node_create(body: NodeIn, user: str = Depends(require_auth)):
    if not body.profile_uuid or not body.inbounds:
        raise HTTPException(400, "Выбери профиль конфига и хотя бы один инбаунд")
    node = await rw.node_create(node_payload(body))
    await add_event("action", "info", f"{user}: создана нода {body.name}")
    return {"ok": True, "uuid": (node or {}).get("uuid")}


@router.get("/nodes/{uuid}/full")
async def node_full(uuid: str, _: str = Depends(require_auth)):
    n = await rw.node(uuid)
    profile = n.get("configProfile") or {}
    return {
        "uuid": n.get("uuid"), "name": n.get("name"), "address": n.get("address"),
        "port": n.get("port"), "country_code": n.get("countryCode"),
        "profile_uuid": profile.get("activeConfigProfileUuid"),
        "inbounds": [i if isinstance(i, str) else i.get("uuid")
                     for i in (profile.get("activeInbounds") or [])],
        "traffic_limit_bytes": n.get("trafficLimitBytes") or 0,
        "traffic_reset_day": n.get("trafficResetDay") or 1,
        "notify_percent": n.get("notifyPercent") or 0,
        "consumption_multiplier": n.get("consumptionMultiplier") or 1,
        "traffic_tracking": bool(n.get("isTrafficTrackingActive")),
        "disabled": bool(n.get("isDisabled")),
    }


@router.put("/nodes/{uuid}")
async def node_update(uuid: str, body: NodeIn, user: str = Depends(require_auth)):
    payload = node_payload(body)
    payload["uuid"] = uuid
    if not body.profile_uuid:
        payload.pop("configProfile", None)
    await rw.node_update(payload)
    await add_event("action", "info", f"{user}: изменена нода {body.name}")
    return {"ok": True}


@router.delete("/nodes/{uuid}")
async def node_delete(uuid: str, user: str = Depends(require_auth)):
    name = uuid[:8]
    try:
        name = (await rw.node(uuid)).get("name") or name
    except Exception:                                    # noqa: BLE001
        pass
    await rw.node_delete(uuid)
    await add_event("action", "warn", f"{user}: удалена нода {name}")
    return {"ok": True}


@router.post("/nodes-restart-all")
async def nodes_restart_all(user: str = Depends(require_auth)):
    await rw.nodes_restart_all(False)
    await add_event("action", "info", f"{user}: перезапуск всех нод")
    return {"ok": True}


class HostsBulkIn(BaseModel):
    action: str
    uuids: list[str] = Field(min_length=1)


@router.post("/hosts-bulk")
async def hosts_bulk(body: HostsBulkIn, user: str = Depends(require_auth)):
    if body.action not in ("enable", "disable", "delete"):
        raise HTTPException(400, "Неизвестное действие")
    await rw.hosts_bulk(body.action, body.uuids)
    await add_event("action", "warn", f"{user}: хосты {body.action} ({len(body.uuids)})")
    return {"ok": True}


class ReorderIn(BaseModel):
    uuids: list[str] = Field(min_length=1)


@router.post("/nodes-reorder")
async def nodes_reorder(body: ReorderIn, _: str = Depends(require_auth)):
    await rw.reorder("nodes", body.uuids)
    return {"ok": True}


@router.post("/hosts-reorder")
async def hosts_reorder(body: ReorderIn, _: str = Depends(require_auth)):
    await rw.reorder("hosts", body.uuids)
    return {"ok": True}


# ---------------- хосты
class HostIn(BaseModel):
    remark: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1)
    port: int = Field(443, ge=1, le=65535)
    profile_uuid: str = ""
    inbound_uuid: str = ""
    path: str = ""
    sni: str = ""
    host: str = ""
    alpn: str = ""
    fingerprint: str = ""
    security_layer: str = ""
    server_description: str = ""
    allow_insecure: bool = False
    disabled: bool = False


HOST_FIELDS = {
    "remark": "remark", "address": "address", "port": "port", "path": "path",
    "sni": "sni", "host": "host", "alpn": "alpn", "fingerprint": "fingerprint",
    "security_layer": "securityLayer", "server_description": "serverDescription",
    "allow_insecure": "allowInsecure", "disabled": "isDisabled",
}
EMPTY_AS_NULL = {"alpn", "fingerprint", "sni", "host", "path", "server_description"}


def host_payload(body: HostIn, base: dict | None = None) -> dict:
    out = dict(base or {})
    for field, key in HOST_FIELDS.items():
        value = getattr(body, field)
        if isinstance(value, str):
            value = value.strip()
            if not value and field in EMPTY_AS_NULL:
                value = None
            elif not value and field == "security_layer":
                continue
        out[key] = value
    if body.inbound_uuid:
        out["inbound"] = {"configProfileUuid": body.profile_uuid,
                          "configProfileInboundUuid": body.inbound_uuid}
    return out


CLEAN = {"createdAt", "updatedAt", "inboundTag", "rawInbound", "configProfileName",
         "nodeName", "profileName"}


async def inbound_tags() -> dict[str, str]:
    """uuid инбаунда -> тег. В хосте панель отдаёт только uuid."""
    tags: dict[str, str] = {}
    try:
        for p in await rw.profiles():
            inbounds = p.get("inbounds")
            if inbounds is None:
                inbounds = await rw.profile_inbounds(p["uuid"])
            for i in inbounds or []:
                if i.get("uuid"):
                    tags[i["uuid"]] = i.get("tag") or ""
    except Exception:                                    # noqa: BLE001
        pass                                             # без тегов список всё равно покажем
    return tags


@router.get("/hosts")
async def hosts_list(_: str = Depends(require_auth)):
    out = []
    tags = await inbound_tags()
    for h in await rw.hosts():
        inbound = h.get("inbound") or {}
        out.append({
            "uuid": h.get("uuid"), "remark": h.get("remark"), "address": h.get("address"),
            "port": h.get("port"), "path": h.get("path"), "sni": h.get("sni"),
            "host": h.get("host"), "alpn": h.get("alpn"), "fingerprint": h.get("fingerprint"),
            "security_layer": h.get("securityLayer"), "allow_insecure": bool(h.get("allowInsecure")),
            "server_description": h.get("serverDescription"),
            "disabled": bool(h.get("isDisabled")),
            "profile_uuid": inbound.get("configProfileUuid"),
            "inbound_uuid": inbound.get("configProfileInboundUuid"),
            "inbound_tag": (h.get("inboundTag") or inbound.get("tag")
                            or tags.get(inbound.get("configProfileInboundUuid") or "")),
            "pos": h.get("viewPosition"),
        })
    if any(x["pos"] is not None for x in out):
        return sorted(out, key=lambda x: (x["pos"] is None, x["pos"] or 0))
    return sorted(out, key=lambda x: (x["disabled"], x["remark"] or ""))


@router.post("/hosts")
async def host_create(body: HostIn, user: str = Depends(require_auth)):
    if not body.inbound_uuid:
        raise HTTPException(400, "Выбери инбаунд, к которому привязан хост")
    await rw.host_create(host_payload(body))
    await add_event("action", "info", f"{user}: создан хост {body.remark}")
    return {"ok": True}


@router.put("/hosts/{uuid}")
async def host_update(uuid: str, body: HostIn, user: str = Depends(require_auth)):
    base = {k: v for k, v in (await rw.host(uuid)).items() if k not in CLEAN}
    payload = host_payload(body, base)
    payload["uuid"] = uuid
    await rw.host_update(payload)
    await add_event("action", "info", f"{user}: изменён хост {body.remark}")
    return {"ok": True}


@router.delete("/hosts/{uuid}")
async def host_delete(uuid: str, user: str = Depends(require_auth)):
    await rw.host_delete(uuid)
    await add_event("action", "warn", f"{user}: удалён хост {uuid[:8]}")
    return {"ok": True}


@router.post("/hosts/{uuid}/{action}")
async def host_toggle(uuid: str, action: str, user: str = Depends(require_auth)):
    if action not in ("enable", "disable"):
        raise HTTPException(400, "Неизвестное действие")
    base = {k: v for k, v in (await rw.host(uuid)).items() if k not in CLEAN}
    base["isDisabled"] = action == "disable"
    await rw.host_update(base)
    await add_event("action", "info",
                    f"{user}: хост {base.get('remark') or uuid[:8]} "
                    f"{'выключен' if action == 'disable' else 'включён'}")
    return {"ok": True}
