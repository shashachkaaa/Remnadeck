"""Тонкий клиент Remnawave API (3.x). Ответы панели лежат в поле `response`."""
import time

import httpx

from .config import settings


class RWError(Exception):
    pass


class Remnawave:
    def __init__(self, url=None, token=None, internal=None, verify=None):
        self._fixed = (url, token, internal, verify)
        self.client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, object]] = {}
        self.reconfigure()

    def reconfigure(self):
        """Пересобрать клиент после изменения настроек."""
        url, token, internal, verify = self._fixed
        self.url = (url if url is not None else settings.rw_url).rstrip("/")
        self.token = token if token is not None else settings.rw_token
        internal = settings.rw_internal if internal is None else internal
        verify = settings.rw_verify_tls if verify is None else verify
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if internal:
            # без этих заголовков ProxyCheckMiddleware рвёт сокет
            headers["X-Forwarded-For"] = "127.0.0.1"
            headers["X-Forwarded-Proto"] = "https"
        old = self.client
        self.client = httpx.AsyncClient(base_url=self.url, headers=headers, timeout=20, verify=verify)
        self._cache.clear()
        return old

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    async def _req(self, method: str, path: str, **kw):
        if not self.configured:
            raise RWError("Remnawave не подключена — укажи адрес и API-токен в разделе «Подключение»")
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise RWError(f"Remnawave недоступна ({e.__class__.__name__}). Проверь адрес в разделе «Подключение»") from e
        if r.status_code in (401, 403):
            raise RWError(f"Remnawave отклонила токен ({r.status_code}). Проверь API-токен")
        if r.status_code >= 400:
            raise RWError(f"{method} {path} → {r.status_code}: {r.text[:200]}")
        data = r.json() if r.content else {}
        return data.get("response", data) if isinstance(data, dict) else data

    async def _get(self, path: str, ttl: float = 10, **kw):
        key = path + repr(sorted(kw.get("params", {}).items()))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        data = await self._req("GET", path, **kw)
        self._cache[key] = (time.monotonic(), data)
        return data

    def drop_cache(self, prefix: str = ""):
        for k in [k for k in self._cache if k.startswith(prefix)]:
            del self._cache[k]

    # --- система
    async def health(self):
        return await self._get("/api/system/health", ttl=5)

    async def stats(self):
        return await self._get("/api/system/stats", ttl=15)

    # --- ноды и хосты
    async def nodes(self, ttl: float = 10) -> list[dict]:
        data = await self._get("/api/nodes", ttl=ttl)
        return data if isinstance(data, list) else data.get("nodes", [])

    async def hosts(self) -> list[dict]:
        data = await self._get("/api/hosts", ttl=30)
        return data if isinstance(data, list) else data.get("hosts", [])

    async def node(self, uuid: str) -> dict:
        return await self._get(f"/api/nodes/{uuid}", ttl=0)

    async def node_action(self, uuid: str, action: str):
        self.drop_cache("/api/nodes")
        return await self._req("POST", f"/api/nodes/{uuid}/actions/{action}")

    async def node_create(self, body: dict):
        self.drop_cache("/api/nodes")
        return await self._req("POST", "/api/nodes", json=body)

    async def node_update(self, body: dict):
        """PATCH /api/nodes — uuid передаётся внутри тела, так устроен API 3.x."""
        self.drop_cache("/api/nodes")
        return await self._req("PATCH", "/api/nodes", json=body)

    async def node_delete(self, uuid: str):
        self.drop_cache("/api/nodes")
        return await self._req("DELETE", f"/api/nodes/{uuid}")

    async def reorder(self, kind: str, uuids: list[str]):
        """kind: nodes | hosts. Тело DTO в разных версиях панели отличается,
        поэтому пробуем известные варианты по очереди."""
        items = [{"uuid": u, "viewPosition": i} for i, u in enumerate(uuids)]
        self.drop_cache(f"/api/{kind}")
        last: Exception | None = None
        for body in ({kind: items}, {"uuids": uuids}, items):
            try:
                return await self._req("POST", f"/api/{kind}/actions/reorder", json=body)
            except RWError as e:
                last = e
        raise last or RWError("Панель не приняла новый порядок")

    async def nodes_restart_all(self, force: bool = False):
        self.drop_cache("/api/nodes")
        return await self._req("POST", "/api/nodes/actions/restart-all", json={"forceRestart": force})

    # --- хосты
    async def host(self, uuid: str) -> dict:
        return await self._get(f"/api/hosts/{uuid}", ttl=0)

    async def host_create(self, body: dict):
        self.drop_cache("/api/hosts")
        return await self._req("POST", "/api/hosts", json=body)

    async def host_update(self, body: dict):
        self.drop_cache("/api/hosts")
        return await self._req("PATCH", "/api/hosts", json=body)

    async def host_delete(self, uuid: str):
        self.drop_cache("/api/hosts")
        return await self._req("DELETE", f"/api/hosts/{uuid}")

    async def keygen(self) -> str:
        """Ключ, которым нода представляется панели. До 2.9 поле pubKey и
        переменная SSL_CERT, с 2.9 — secretKey и SECRET_KEY."""
        data = await self._get("/api/keygen", ttl=0)
        if isinstance(data, dict):
            for key in ("secretKey", "pubKey", "secret_key", "pub_key"):
                if data.get(key):
                    return data[key]
        raise RWError("Панель не отдала ключ ноды (/api/keygen)")

    async def hosts_bulk(self, action: str, uuids: list[str]):
        """action: enable | disable | delete"""
        self.drop_cache("/api/hosts")
        return await self._req("POST", f"/api/hosts/bulk/{action}", json={"uuids": uuids})

    async def tags(self, kind: str) -> list[str]:
        """kind: nodes | hosts | users"""
        data = await self._get(f"/api/{kind}/tags", ttl=120)
        return data.get("tags", []) if isinstance(data, dict) else (data or [])

    # --- профили конфигов и инбаунды
    async def profiles(self, ttl: float = 60) -> list[dict]:
        data = await self._get("/api/config-profiles", ttl=ttl)
        if isinstance(data, dict):
            return data.get("configProfiles", data.get("profiles", []))
        return data or []

    async def profile_inbounds(self, uuid: str) -> list[dict]:
        data = await self._get(f"/api/config-profiles/{uuid}/inbounds", ttl=60)
        if isinstance(data, dict):
            return data.get("inbounds", [])
        return data or []

    # --- пользователи
    async def users_all(self, page: int = 500) -> list[dict]:
        cached = self._cache.get("users_all")
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        out, start = [], 0
        while True:
            res = await self._req("GET", "/api/users", params={"size": page, "start": start})
            batch = res.get("users", []) if isinstance(res, dict) else res
            out.extend(batch)
            total = res.get("total", len(out)) if isinstance(res, dict) else len(out)
            start += page
            if not batch or start >= total:
                break
        self._cache["users_all"] = (time.monotonic(), out)
        return out

    async def user_action(self, uuid: str, action: str):
        self._cache.pop("users_all", None)
        return await self._req("POST", f"/api/users/{uuid}/actions/{action}")

    @staticmethod
    def user_ref(ref) -> dict:
        """С 3.4 пользователь адресуется числовым id, до этого — uuid."""
        ref = str(ref)
        return {"id": int(ref)} if ref.isdigit() else {"uuid": ref}

    @staticmethod
    def users_ref(refs: list) -> dict:
        refs = [str(r) for r in refs]
        if refs and all(r.isdigit() for r in refs):
            return {"userIds": [int(r) for r in refs]}
        return {"uuids": refs}

    def _forget_users(self):
        self._cache.pop("users_all", None)
        self.drop_cache("/api/users")

    async def user(self, uuid: str) -> dict:
        return await self._get(f"/api/users/{uuid}", ttl=0)

    async def user_find(self, kind: str, value: str):
        """В 3.4 остались только by-username и by-short-uuid; остальное — через resolve."""
        if kind in ("username", "short-uuid"):
            return await self._get(f"/api/users/by-{kind}/{value}", ttl=0)
        if kind == "id":
            return await self.user(value)
        raise RWError(f"Поиск по «{kind}» панель 3.4 не поддерживает")

    async def user_create(self, body: dict):
        self._forget_users()
        return await self._req("POST", "/api/users", json=body)

    async def user_update(self, body: dict):
        self._forget_users()
        return await self._req("PATCH", "/api/users", json=body)

    async def user_delete(self, uuid: str):
        self._forget_users()
        return await self._req("DELETE", f"/api/users/{uuid}")

    async def user_revoke(self, uuid: str, short_uuid: str = "", only_passwords: bool = False):
        self._forget_users()
        body: dict = {"revokeOnlyPasswords": only_passwords}
        if short_uuid:
            body["shortUuid"] = short_uuid
        return await self._req("POST", f"/api/users/{uuid}/actions/revoke", json=body)

    async def user_extend(self, ref: str, days: int):
        """Продлить срок на N дней от текущей даты окончания (3.4+)."""
        self._forget_users()
        return await self._req("POST", f"/api/users/{ref}/actions/extend", json={"days": days})

    async def user_history(self, ref: str):
        return await self._get(f"/api/users/{ref}/subscription-request-history", ttl=30)

    async def user_nodes(self, uuid: str):
        return await self._get(f"/api/users/{uuid}/accessible-nodes", ttl=30)

    async def user_usage(self, ref: str, start: str, end: str):
        """3.4: bandwidth-stats, даты в формате YYYY-MM-DD."""
        return await self._get(f"/api/bandwidth-stats/users/{ref}", ttl=60,
                               params={"start": start[:10], "end": end[:10]})

    async def user_tags(self) -> list[str]:
        data = await self._get("/api/users/tags", ttl=120)
        return data.get("tags", []) if isinstance(data, dict) else (data or [])

    async def users_bulk(self, action: str, body: dict):
        """action: delete | delete-by-status | revoke-subscription | reset-traffic |
        update | update-squads | extend-expiration-date. Список пользователей
        в теле — userIds (3.4) или uuids (раньше), см. users_ref."""
        self._forget_users()
        return await self._req("POST", f"/api/users/bulk/{action}", json=body)

    # --- устройства (HWID)
    async def hwid_list(self, user_uuid: str):
        data = await self._get(f"/api/hwid/devices/{user_uuid}", ttl=0)
        return data.get("devices", []) if isinstance(data, dict) else (data or [])

    @staticmethod
    def _hwid_owner(ref) -> dict:
        ref = str(ref)
        return {"userId": int(ref)} if ref.isdigit() else {"userUuid": ref}

    async def hwid_delete(self, user_ref: str, hwid: str):
        return await self._req("POST", "/api/hwid/devices/delete",
                               json={**self._hwid_owner(user_ref), "hwid": hwid})

    async def hwid_delete_all(self, user_ref: str):
        return await self._req("POST", "/api/hwid/devices/delete-all",
                               json=self._hwid_owner(user_ref))

    async def hwid_stats(self):
        return await self._get("/api/hwid/devices/stats", ttl=60)

    # --- сквады
    async def squads(self) -> list[dict]:
        data = await self._get("/api/internal-squads", ttl=30)
        if isinstance(data, dict):
            return data.get("internalSquads", data.get("squads", []))
        return data or []

    async def squad(self, uuid: str) -> dict:
        return await self._get(f"/api/internal-squads/{uuid}", ttl=0)

    async def squad_create(self, body: dict):
        self.drop_cache("/api/internal-squads")
        return await self._req("POST", "/api/internal-squads", json=body)

    async def squad_update(self, body: dict):
        self.drop_cache("/api/internal-squads")
        return await self._req("PATCH", "/api/internal-squads", json=body)

    async def squad_delete(self, uuid: str):
        self.drop_cache("/api/internal-squads")
        return await self._req("DELETE", f"/api/internal-squads/{uuid}")

    async def squad_users(self, uuid: str, add: bool):
        """Добавить всех пользователей в сквад или убрать всех из него."""
        self._forget_users()
        path = f"/api/internal-squads/{uuid}/bulk-actions/{'add' if add else 'remove'}-users"
        return await self._req("POST" if add else "DELETE", path)

    # --- статистика системы
    async def bandwidth(self):
        return await self._get("/api/system/stats/bandwidth", ttl=30)

    async def nodes_statistics(self):
        return await self._get("/api/system/stats/nodes", ttl=30)

    async def nodes_metrics(self):
        return await self._get("/api/system/nodes/metrics", ttl=15)

    # 3.4: статистика переехала в bandwidth-stats, даты — YYYY-MM-DD
    async def nodes_usage(self, start: str, end: str):
        return await self._get("/api/bandwidth-stats/nodes/", ttl=60,
                               params={"start": start[:10], "end": end[:10]})

    async def node_usage(self, uuid: str, start: str, end: str):
        return await self._get(f"/api/bandwidth-stats/nodes/{uuid}/users", ttl=60,
                               params={"start": start[:10], "end": end[:10]})

    async def node_users_usage(self, uuid: str, start: str, end: str) -> dict[str, int]:
        """Трафик всех пользователей ноды за период: username -> байты."""
        data = await self._get(f"/api/bandwidth-stats/nodes/{uuid}/users", ttl=0,
                               params={"start": start[:10], "end": end[:10], "topUsersLimit": 100000})
        top = data.get("topUsers", []) if isinstance(data, dict) else []
        return {u["username"]: int(u.get("total") or 0) for u in top if u.get("username")}

    async def drop_connections(self, user_ids: list[int], node_uuids: list[str] | None = None):
        """Разорвать активные соединения пользователей — сразу, не дожидаясь переподключения."""
        target = ({"target": "specificNodes", "nodeUuids": node_uuids} if node_uuids
                  else {"target": "allNodes"})
        return await self._req("POST", "/api/connections/drop",
                               json={"dropBy": {"by": "userIds", "userIds": user_ids},
                                     "targetNodes": target})

    async def subscription_settings(self) -> dict:
        return await self._get("/api/subscription-settings/", ttl=0)

    async def subscription_settings_update(self, body: dict):
        return await self._req("PATCH", "/api/subscription-settings/", json=body)

    async def stats_recap(self):
        return await self._get("/api/system/stats/recap", ttl=60)

    # --- инструменты
    async def x25519(self):
        return await self._get("/api/system/tools/x25519/generate", ttl=0)

    async def srr_match(self, body: dict):
        return await self._req("POST", "/api/system/testers/srr-matcher", json=body)


rw = Remnawave()
