"""Серверы нод: SSH-доступ, обновление, настройка под Hysteria2 и CDN.

Через API Remnawave можно только завести запись ноды. Обновить контейнер,
выпустить сертификат или поднять nginx под CDN — только на самом сервере,
поэтому здесь реестр серверов и задачи по SSH.
"""
import asyncio
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import scripts, ssh
from .auth import require_auth
from .config import encrypt
from .db import get_db
from .events import add_event
from .remnawave import rw

router = APIRouter(prefix="/api", tags=["infra"])


def clean_secret(auth: str, secret: str, key_pass: str) -> str:
    """Ключ проверяем до записи, чтобы ошибка всплыла в форме, а не при первом подключении."""
    secret = (secret or "").strip()
    if auth == "key" and secret:
        try:
            return ssh.check_key(secret, key_pass)
        except ssh.SSHError as e:
            raise HTTPException(400, str(e)) from e
    return secret
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")

SITE_TEMPLATE = """upstream {{upstream}} {
    server 127.0.0.1:{{port}};
    keepalive 256;
    keepalive_requests 100000;
    keepalive_timeout 120s;
}

server {
    listen 80;
    listen [::]:80;
    server_name {{server_names}};
    location /.well-known/acme-challenge/ { root /var/www/rd-acme; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl{{default}};
    listen [::]:443 ssl{{default}};
    http2 on;
    server_name {{server_names}};

    ssl_certificate     {{cert}};
    ssl_certificate_key {{key}};

    # access_log /var/log/nginx/rd-cdn.log rd_cdn;

    # Свой proxy_set_header здесь отключил бы заголовки из rd-cdn-common.conf
    location {{path}} { proxy_pass http://{{upstream}}; }
    location / { return 404; }
}"""

BUILTIN_TEMPLATES = [
    ('Yandex CDN', 'yandex', SITE_TEMPLATE, 'Uplink едет в заголовке X-Client-Data — буферы заголовков заданы в общем конфиге.', '/api/v4/session/getFiles.ts', 10085, 0),
    ('Beeline CDN', 'beeline', SITE_TEMPLATE, 'Uplink идёт телом запроса, до 1 МБ — client_max_body_size в общем конфиге.', '/media/feed/preview.php', 10086, 0),
    ('Timeweb CDN', 'timeweb', SITE_TEMPLATE, 'Пропускает только точный путь к файлу. Обычно ставится default_server: ловит запросы без SNI. Техдомен Timeweb добавляй вторым — он попадёт в server_name, сертификат выпишется только на твой домен.', '/static/video/getFile.ts', 10087, 1),
    ('VK CDN', 'vk', SITE_TEMPLATE, 'Домен заводится как CNAME на техдомен VK Cloud.', '/static/getFile/video/segment.ts', 10088, 0),
    ('Универсальный CDN → xhttp', 'generic', SITE_TEMPLATE, 'Свой путь и порт. Подходит для любого CDN поверх xhttp.', '/', 10085, 0),
]


async def seed_templates():
    """Встроенные шаблоны держим в актуальном виде, пользовательские не трогаем."""
    db = await get_db()
    for name, slug, body, note, path, port, dflt in BUILTIN_TEMPLATES:
        await db.execute(
            """INSERT INTO cdn_templates(name,slug,body,note,builtin,default_path,default_port,
               is_default_server) VALUES(?,?,?,?,1,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET name=excluded.name, note=excluded.note,
               default_path=excluded.default_path, default_port=excluded.default_port,
               is_default_server=excluded.is_default_server,
               body=CASE WHEN cdn_templates.builtin=1 THEN excluded.body ELSE cdn_templates.body END""",
            (name, slug, body, note, path, port, dflt))
    await db.commit()


# ---------------- серверы
class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    host: str = Field(min_length=1)
    port: int = Field(22, ge=1, le=65535)
    username: str = "root"
    auth: str = "password"                 # password | key
    secret: str = ""                       # пароль либо текст приватного ключа
    key_pass: str = ""
    node_path: str = "/opt/remnanode"
    node_uuid: str = ""
    note: str = ""


def server_row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "host": r["host"], "port": r["port"],
            "username": r["username"], "auth": r["auth"], "has_secret": bool(r["secret"]),
            "node_path": r["node_path"], "node_uuid": r["node_uuid"], "note": r["note"],
            "last_ok": r["last_ok"], "last_info": r["last_info"],
            "last_fail": r["last_fail"] if "last_fail" in r.keys() else None,
            "last_error": r["last_error"] if "last_error" in r.keys() else None}


async def get_server(sid: int) -> dict:
    db = await get_db()
    async with db.execute("SELECT * FROM servers WHERE id=?", (sid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Сервер не найден")
    return dict(row)


@router.get("/servers")
async def servers_list(_: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM servers ORDER BY name") as cur:
        return [server_row(r) for r in await cur.fetchall()]


@router.post("/servers")
async def server_add(body: ServerIn, user: str = Depends(require_auth)):
    if not body.secret.strip():
        raise HTTPException(400, "Нужен пароль или приватный ключ")
    secret = clean_secret(body.auth, body.secret, body.key_pass)
    db = await get_db()
    await db.execute(
        """INSERT INTO servers(name,host,port,username,auth,secret,key_pass,node_path,
           node_uuid,note,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (body.name.strip(), body.host.strip(), body.port, body.username.strip() or "root",
         body.auth, encrypt(secret), encrypt(body.key_pass),
         body.node_path.strip() or "/opt/remnanode", body.node_uuid.strip(), body.note.strip(),
         int(time.time())))
    await db.commit()
    await add_event("action", "info", f"{user}: добавлен сервер {body.name}")
    return {"ok": True}


@router.put("/servers/{sid}")
async def server_edit(sid: int, body: ServerIn, user: str = Depends(require_auth)):
    await get_server(sid)
    db = await get_db()
    sets = ["name=?", "host=?", "port=?", "username=?", "auth=?", "node_path=?", "node_uuid=?", "note=?"]
    args = [body.name.strip(), body.host.strip(), body.port, body.username.strip() or "root",
            body.auth, body.node_path.strip() or "/opt/remnanode", body.node_uuid.strip(),
            body.note.strip()]
    if body.secret.strip():                       # пустое поле = не менять
        sets += ["secret=?", "key_pass=?"]
        args += [encrypt(clean_secret(body.auth, body.secret, body.key_pass)), encrypt(body.key_pass)]
    args.append(sid)
    await db.execute(f"UPDATE servers SET {', '.join(sets)} WHERE id=?", args)
    await db.commit()
    await add_event("action", "info", f"{user}: изменён сервер {body.name}")
    return {"ok": True}


@router.delete("/servers/{sid}")
async def server_delete(sid: int, user: str = Depends(require_auth)):
    srv = await get_server(sid)
    db = await get_db()
    await db.execute("DELETE FROM servers WHERE id=?", (sid,))
    await db.execute("DELETE FROM cdn_sites WHERE server_id=?", (sid,))
    await db.commit()
    await add_event("action", "warn", f"{user}: удалён сервер {srv['name']}")
    return {"ok": True}


def describe(info: dict, node_path: str) -> str:
    return " · ".join(filter(None, [
        info.get("os"), info.get("docker") or "docker нет",
        "нода найдена" if info.get("compose") == "yes" else f"нет {node_path}/docker-compose.yml",
        info.get("image", ""), "nginx есть" if info.get("nginx") == "yes" else "",
        "" if info.get("root") == "0" else "вход не под root: нужен sudo без пароля",
    ]))


async def check_one(srv: dict) -> dict:
    """Проверка SSH одного сервера; результат — и успех, и ошибка — пишется в базу."""
    db = await get_db()
    now = int(time.time())
    try:
        info = await ssh.probe(srv)
    except Exception as e:                                # noqa: BLE001
        msg = str(e) if isinstance(e, ssh.SSHError) else f"{e.__class__.__name__}: {e}"
        await db.execute("UPDATE servers SET last_fail=?, last_error=? WHERE id=?", (now, msg, srv["id"]))
        await db.commit()
        return {"id": srv["id"], "name": srv["name"], "ok": False, "info": msg}
    text = describe(info, srv["node_path"])
    await db.execute("UPDATE servers SET last_ok=?, last_info=?, last_error=NULL WHERE id=?",
                     (now, text, srv["id"]))
    await db.commit()
    return {"id": srv["id"], "name": srv["name"], "ok": True, "info": text,
            "node": info.get("compose") == "yes"}


@router.post("/servers/{sid}/check")
async def server_check(sid: int, _: str = Depends(require_auth)):
    r = await check_one(await get_server(sid))
    if not r["ok"]:
        raise HTTPException(502, r["info"])
    return r


@router.post("/servers-check-all")
async def servers_check_all(user: str = Depends(require_auth)):
    """Все серверы параллельно, но не больше 8 SSH-сессий разом."""
    db = await get_db()
    async with db.execute("SELECT * FROM servers ORDER BY name") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        raise HTTPException(400, "Серверы ещё не добавлены")
    gate = asyncio.Semaphore(8)

    async def one(srv):
        async with gate:
            return await check_one(srv)

    results = await asyncio.gather(*(one(r) for r in rows))
    bad = [r for r in results if not r["ok"]]
    await add_event("action", "warn" if bad else "info",
                    f"{user}: проверка SSH — доступно {len(results) - len(bad)} из {len(results)}")
    return {"total": len(results), "ok": len(results) - len(bad), "results": results}


# ---------------- задачи
def spawn(title: str, srv: dict, script: str, on_ok=None) -> dict:
    job = ssh.new_job(title, srv["name"])
    asyncio.create_task(ssh.run_job(srv, script, job, on_ok))
    return {"job": job.id}


@router.get("/jobs")
async def jobs_list(_: str = Depends(require_auth)):
    return [j.dump(len(j.lines)) for j in list(ssh.JOBS.values())[::-1]]


@router.get("/jobs/{jid}")
async def job_get(jid: str, since: int = 0, _: str = Depends(require_auth)):
    job = ssh.JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Задача не найдена или уже вытеснена из истории")
    return job.dump(since)


@router.post("/servers/{sid}/update")
async def server_update(sid: int, user: str = Depends(require_auth)):
    srv = await get_server(sid)
    await add_event("action", "info", f"{user}: обновление ноды на {srv['name']}")
    return spawn(f"Обновление ноды · {srv['name']}", srv, scripts.update_node(srv["node_path"]))


@router.post("/servers/update-all")
async def servers_update_all(user: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM servers ORDER BY name") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        raise HTTPException(400, "Серверы ещё не добавлены")
    job = ssh.new_job("Обновление всех нод", f"{len(rows)} серверов")

    async def run():
        bad = 0
        for srv in rows:
            job.write(f"\n########## {srv['name']} ({srv['host']})")
            try:
                code = await ssh.run_script(srv, scripts.update_node(srv["node_path"]), job)
                if code:
                    bad += 1
                    job.write(f"✗ {srv['name']}: код {code}")
            except Exception as e:                        # noqa: BLE001
                bad += 1
                job.write(f"✗ {srv['name']}: {e}")
        job.done(bad == 0, f"\nГотово. Успешно: {len(rows) - bad} из {len(rows)}")

    asyncio.create_task(run())
    await add_event("action", "info", f"{user}: обновление всех нод ({len(rows)})")
    return {"job": job.id}


class Hy2In(BaseModel):
    domain: str = Field(min_length=3)
    email: str = ""
    cf_token: str = ""                     # задан — выпуск через DNS-01, домен может быть за CDN


@router.post("/servers/{sid}/hysteria2")
async def server_hysteria2(sid: int, body: Hy2In, user: str = Depends(require_auth)):
    srv = await get_server(sid)
    await add_event("action", "info", f"{user}: настройка Hysteria2 на {srv['name']} ({body.domain})")
    return spawn(f"Hysteria2 · {srv['name']}", srv,
                 scripts.setup_hysteria2(srv["node_path"], body.domain.strip(), body.email.strip(),
                                         body.cf_token.strip()))


@router.post("/servers/{sid}/cdn-status")
async def server_cdn_status(sid: int, _: str = Depends(require_auth)):
    srv = await get_server(sid)
    return spawn(f"Состояние CDN · {srv['name']}", srv, scripts.cdn_status())


# ---------------- шаблоны CDN
class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    slug: str = Field(min_length=2, max_length=32)
    body: str = Field(min_length=10)
    note: str = ""
    default_path: str = "/"
    default_port: int = Field(10085, ge=1, le=65535)
    is_default_server: bool = False


@router.get("/cdn/templates")
async def templates_list(_: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM cdn_templates ORDER BY builtin DESC, name") as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/cdn/templates")
async def template_add(body: TemplateIn, user: str = Depends(require_auth)):
    slug = body.slug.strip().lower()
    if not SLUG.match(slug):
        raise HTTPException(400, "Идентификатор: латиница, цифры и дефис, 2–31 символ")
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO cdn_templates(name,slug,body,note,default_path,default_port,
               is_default_server) VALUES(?,?,?,?,?,?,?)""",
            (body.name.strip(), slug, body.body, body.note.strip(), body.default_path.strip() or "/",
             body.default_port, int(body.is_default_server)))
        await db.commit()
    except Exception as e:                                # noqa: BLE001
        raise HTTPException(400, "Шаблон с таким именем или идентификатором уже есть") from e
    await add_event("action", "info", f"{user}: добавлен CDN-шаблон {body.name}")
    return {"ok": True}


@router.put("/cdn/templates/{tid}")
async def template_edit(tid: int, body: TemplateIn, _: str = Depends(require_auth)):
    db = await get_db()
    await db.execute(
        """UPDATE cdn_templates SET name=?, body=?, note=?, default_path=?, default_port=?,
           is_default_server=? WHERE id=?""",
        (body.name.strip(), body.body, body.note.strip(), body.default_path.strip() or "/",
         body.default_port, int(body.is_default_server), tid))
    await db.commit()
    return {"ok": True}


@router.delete("/cdn/templates/{tid}")
async def template_delete(tid: int, _: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT builtin FROM cdn_templates WHERE id=?", (tid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Шаблон не найден")
    await db.execute("DELETE FROM cdn_templates WHERE id=?", (tid,))
    await db.commit()
    return {"ok": True}


# ---------------- конфиги CDN на серверах
class SiteIn(BaseModel):
    server_id: int = 0
    node_uuid: str = ""                    # альтернатива server_id: берём сервер ноды
    template_id: int
    domain: str = Field(min_length=3)      # домены через запятую, первый — основной
    path: str = "/"
    upstream: int = Field(10085, ge=1, le=65535)
    email: str = ""
    cf_token: str = ""                     # задан — выпуск через DNS-01 Cloudflare
    is_default_server: bool = False
    cert_primary_only: bool = False        # остальные домены — только server_name (техдомен Timeweb)


@router.get("/cdn/sites")
async def sites_list(_: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute(
            """SELECT s.*, sv.name AS server_name, t.name AS template_name
               FROM cdn_sites s LEFT JOIN servers sv ON sv.id=s.server_id
               LEFT JOIN cdn_templates t ON t.id=s.template_id ORDER BY s.domain""") as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/cdn/sites")
async def site_add(body: SiteIn, user: str = Depends(require_auth)):
    srv = await server_by_node(body.node_uuid) if body.node_uuid else await get_server(body.server_id)
    body.server_id = srv["id"]
    db = await get_db()
    async with db.execute("SELECT * FROM cdn_templates WHERE id=?", (body.template_id,)) as cur:
        tpl = await cur.fetchone()
    if not tpl:
        raise HTTPException(404, "Шаблон не найден")
    domains = [d.strip().lower() for d in re.split(r"[,\s]+", body.domain) if d.strip()]
    if not domains:
        raise HTTPException(400, "Укажи хотя бы один домен")
    bad = [d for d in domains if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", d)]
    if bad:
        raise HTTPException(400, f"Не похоже на домен: {', '.join(bad)}")
    domain = domains[0]
    path = body.path.strip() or "/"
    if not path.startswith("/"):
        raise HTTPException(400, "Путь должен начинаться со слэша")
    slug = f"{tpl['slug']}-{re.sub(r'[^a-z0-9]+', '-', domain).strip('-')}"

    async def remember():
        await db.execute(
            """INSERT INTO cdn_sites(server_id,template_id,slug,domain,domains,path,upstream,
               is_default_server,ts) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(server_id,domain) DO UPDATE SET
               template_id=excluded.template_id, slug=excluded.slug, domains=excluded.domains,
               path=excluded.path, upstream=excluded.upstream,
               is_default_server=excluded.is_default_server, ts=excluded.ts""",
            (body.server_id, body.template_id, slug, domain, " ".join(domains), path, body.upstream,
             int(body.is_default_server), int(time.time())))
        await db.commit()

    await add_event("action", "info", f"{user}: CDN {domain} на {srv['name']}")
    return spawn(f"CDN {domain} · {srv['name']}", srv,
                 scripts.cdn_install(slug, domains, path, body.upstream, tpl["body"],
                                     body.email.strip(), body.is_default_server,
                                     body.cf_token.strip(),
                                     domains[:1] if body.cert_primary_only else domains), remember)


@router.delete("/cdn/sites/{site_id}")
async def site_delete(site_id: int, drop_cert: bool = False, user: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM cdn_sites WHERE id=?", (site_id,)) as cur:
        site = await cur.fetchone()
    if not site:
        raise HTTPException(404, "Конфиг не найден")
    srv = await get_server(site["server_id"])

    async def forget():
        await db.execute("DELETE FROM cdn_sites WHERE id=?", (site_id,))
        await db.commit()

    await add_event("action", "warn", f"{user}: снят CDN {site['domain']} с {srv['name']}")
    return spawn(f"Снятие CDN {site['domain']} · {srv['name']}", srv,
                 scripts.cdn_remove(site["slug"], site["domain"], drop_cert), forget)


# ---------------- SSH-доступ, привязанный к ноде
class NodeSSHIn(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(22, ge=1, le=65535)
    username: str = "root"
    auth: str = "password"
    secret: str = ""                       # при правке пустое = не менять
    key_pass: str = ""
    node_path: str = "/opt/remnanode"
    name: str = ""
    note: str = ""


async def server_by_node(uuid: str) -> dict:
    db = await get_db()
    async with db.execute("SELECT * FROM servers WHERE node_uuid=?", (uuid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "У этой ноды не указан SSH-доступ")
    return dict(row)


@router.get("/nodes/{uuid}/ssh")
async def node_ssh_get(uuid: str, _: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT * FROM servers WHERE node_uuid=?", (uuid,)) as cur:
        row = await cur.fetchone()
    return server_row(row) if row else None


@router.put("/nodes/{uuid}/ssh")
async def node_ssh_set(uuid: str, body: NodeSSHIn, user: str = Depends(require_auth)):
    db = await get_db()
    async with db.execute("SELECT id FROM servers WHERE node_uuid=?", (uuid,)) as cur:
        row = await cur.fetchone()
    name = body.name.strip() or body.host.strip()
    path = body.node_path.strip() or "/opt/remnanode"
    if row:
        sets = ["name=?", "host=?", "port=?", "username=?", "auth=?", "node_path=?", "note=?"]
        args = [name, body.host.strip(), body.port, body.username.strip() or "root",
                body.auth, path, body.note.strip()]
        if body.secret.strip():
            sets += ["secret=?", "key_pass=?"]
            args += [encrypt(clean_secret(body.auth, body.secret, body.key_pass)),
                     encrypt(body.key_pass)]
        args.append(row["id"])
        await db.execute(f"UPDATE servers SET {', '.join(sets)} WHERE id=?", args)
    else:
        if not body.secret.strip():
            raise HTTPException(400, "Нужен пароль или приватный ключ")
        await db.execute(
            """INSERT INTO servers(name,host,port,username,auth,secret,key_pass,node_path,
               node_uuid,note,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (name, body.host.strip(), body.port, body.username.strip() or "root", body.auth,
             encrypt(clean_secret(body.auth, body.secret, body.key_pass)),
             encrypt(body.key_pass), path, uuid, body.note.strip(),
             int(time.time())))
    await db.commit()
    await add_event("action", "info", f"{user}: SSH-доступ для ноды {name}")
    return {"ok": True}


@router.delete("/nodes/{uuid}/ssh")
async def node_ssh_delete(uuid: str, user: str = Depends(require_auth)):
    srv = await server_by_node(uuid)
    db = await get_db()
    await db.execute("DELETE FROM servers WHERE id=?", (srv["id"],))
    await db.execute("DELETE FROM cdn_sites WHERE server_id=?", (srv["id"],))
    await db.commit()
    await add_event("action", "warn", f"{user}: снят SSH-доступ с ноды {srv['name']}")
    return {"ok": True}


@router.post("/nodes/{uuid}/ssh/check")
async def node_ssh_check(uuid: str, _: str = Depends(require_auth)):
    r = await check_one(await server_by_node(uuid))
    if not r["ok"]:
        raise HTTPException(502, r["info"])
    return r


@router.post("/nodes/{uuid}/ssh/update")
async def node_ssh_update(uuid: str, user: str = Depends(require_auth)):
    return await server_update((await server_by_node(uuid))["id"], user)


@router.post("/nodes/{uuid}/ssh/hysteria2")
async def node_ssh_hysteria2(uuid: str, body: Hy2In, user: str = Depends(require_auth)):
    return await server_hysteria2((await server_by_node(uuid))["id"], body, user)


class InstallIn(BaseModel):
    tune: bool = False
    force: bool = False


@router.post("/nodes/{uuid}/ssh/install")
async def node_ssh_install(uuid: str, body: InstallIn, user: str = Depends(require_auth)):
    """Чистая установка ноды на сервер: docker, каталог, ключ панели, запуск."""
    srv = await server_by_node(uuid)
    node = await rw.node(uuid)
    secret = await rw.keygen()
    port = int(node.get("port") or 2222)
    name = node.get("name") or uuid[:8]
    await add_event("action", "info", f"{user}: установка ноды {name} на {srv['host']}")
    return spawn(f"Установка ноды · {name}", srv,
                 scripts.install_node(srv["node_path"], port, secret, body.tune, body.force))


@router.post("/nodes/{uuid}/ssh/cdn-status")
async def node_ssh_cdn_status(uuid: str, user: str = Depends(require_auth)):
    return await server_cdn_status((await server_by_node(uuid))["id"], user)
