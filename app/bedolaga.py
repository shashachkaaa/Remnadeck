"""Bedolaga (бот и кабинет): версии и обновление по кнопке.

Панель в контейнере и докером не управляет — всё делает deploy/bedolaga.sh на хосте. Панель кладёт
data/bedolaga.request (действие из фиксированного списка + коммит релиза), systemd-юнит
remnadeck-bedolaga.path (ставит install.sh) запускает скрипт. Что найдено на сервере —
data/bedolaga.json, ход — data/bedolaga.log, итог — data/bedolaga.state.

Релизы и заметки к ним — с GitHub (кеш 30 минут, как у версии самой панели).
"""
import json
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import require_auth
from .events import add_event
from .version import data_dir

router = APIRouter(prefix="/api/bedolaga", tags=["bedolaga"])

REPOS = {"bot": "BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot", "cabinet": "BEDOLAGA-DEV/bedolaga-cabinet"}
ACTIONS = ("detect", "bot", "cabinet", "custom", "rollback")
DETECT_EVERY = 300   # раз в 5 минут, пока открыт раздел: бот могли поставить или удалить
_gh: dict = {}


def _p(name: str) -> Path:
    return data_dir() / name


def info() -> dict | None:
    try:
        return json.loads(_p("bedolaga.json").read_text())
    except (OSError, ValueError):
        return None


def present() -> bool:
    """Показывать ли раздел: на сервере найден бот или кабинет."""
    i = info()
    return bool(i and (i.get("bot") or i.get("cabinet")))


def job() -> dict:
    st = {"state": "idle", "action": None, "ts": None}
    try:
        parts = _p("bedolaga.state").read_text().split()
        st = {"state": parts[0], "action": parts[1], "ts": int(parts[2])}
    except (OSError, ValueError, IndexError):
        pass
    if _p("bedolaga.request").exists():
        try:
            req = json.loads(_p("bedolaga.request").read_text())
        except (OSError, ValueError):
            req = {}
        if req.get("action") != "detect":
            st = {"state": "requested", "action": req.get("action"), "ts": req.get("ts")}
    try:
        log = _p("bedolaga.log").read_text(errors="replace")[-12000:]
    except OSError:
        log = ""
    return {**st, "log": log, "installed": _p("bedolaga.installed").exists()}


def _request(action: str, **extra) -> None:
    body = {"action": action, "ts": int(time.time()), **extra}
    tmp = _p("bedolaga.request.tmp")
    tmp.write_text(json.dumps(body))
    tmp.replace(_p("bedolaga.request"))   # целиком: systemd срабатывает на появление файла


def _ver(v: str | None) -> tuple:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else ()


async def releases(key: str, force: bool = False) -> dict:
    c = _gh.get(key)
    if c and not force and time.monotonic() - c["ts"] < 1800:
        return c["v"]
    api = f"https://api.github.com/repos/{REPOS[key]}"
    out = {"releases": [], "error": None, "checked_at": int(time.time()), "repo": REPOS[key]}
    try:
        async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/vnd.github+json"}) as cl:
            r = await cl.get(f"{api}/releases", params={"per_page": 20})
            r.raise_for_status()
            rels = [{"tag": x["tag_name"], "version": x["tag_name"].lstrip("v"), "name": x.get("name") or x["tag_name"],
                     "date": x.get("published_at"), "url": x.get("html_url"), "body": (x.get("body") or "")[:20000],
                     "sha": x["target_commitish"] if re.fullmatch(r"[0-9a-f]{40}", x.get("target_commitish") or "") else None}
                    for x in r.json() if not x.get("draft") and not x.get("prerelease") and _ver(x.get("tag_name"))]
            rels.sort(key=lambda x: _ver(x["tag"]), reverse=True)
            if rels and not rels[0]["sha"]:   # релиз от ветки, а не коммита — узнаём коммит тега
                r = await cl.get(f"{api}/commits/{rels[0]['tag']}")
                r.raise_for_status()
                rels[0]["sha"] = r.json()["sha"]
            out["releases"] = rels
    except (httpx.HTTPError, ValueError, KeyError) as e:
        out["error"] = f"GitHub недоступен: {e.__class__.__name__}"
        if c:   # отдаём прошлый ответ — лучше, чем ничего
            out["releases"] = c["v"]["releases"]
    _gh[key] = {"ts": time.monotonic(), "v": out}
    return out


def compare(installed: dict, gh: dict) -> dict:
    """Статус установленного относительно последнего релиза + заметки ко всем более новым."""
    rels = gh["releases"]
    if not rels:
        return {"status": "unknown", "latest": None, "newer": []}
    latest = rels[0]
    ver, commit = installed.get("version") or "", installed.get("commit") or installed.get("revision") or ""
    if commit and latest["sha"] and commit == latest["sha"]:
        status = "latest"
    elif _ver(ver) and _ver(ver) < _ver(latest["tag"]):
        status = "update"
    elif _ver(ver) and _ver(ver) > _ver(latest["tag"]):
        status = "ahead"
    elif _ver(ver):
        # та же версия, но другой коммит: версию меняет только коммит релиза — значит, стоит main новее релиза
        status = "ahead" if commit else "latest"
    else:
        status = "unknown"
    newer = [r for r in rels if not _ver(ver) or _ver(r["tag"]) > _ver(ver)][:10] if status in ("update", "unknown") else []
    return {"status": status, "latest": latest, "newer": newer}


@router.get("/present")
async def bedolaga_present(_: str = Depends(require_auth)):
    return {"show": present()}


@router.get("")
async def bedolaga_get(force: bool = False, _: str = Depends(require_auth)):
    i, j = info(), job()
    detecting = False
    if j["installed"] and j["state"] not in ("running", "requested") and not _p("bedolaga.request").exists() \
            and (force or not i or time.time() - (i.get("detected_at") or 0) > DETECT_EVERY):
        _request("detect")
        detecting = True
    out = {"info": i, "job": {k: v for k, v in j.items() if k != "log"}, "detecting": detecting, "github": {}}
    for key in ("bot", "cabinet"):
        if i and i.get(key):
            gh = await releases(key, force)
            out["github"][key] = {**compare(i[key], gh), "error": gh["error"], "checked_at": gh["checked_at"],
                                  "repo": gh["repo"]}
    return out


@router.get("/job")
async def bedolaga_job(_: str = Depends(require_auth)):
    return job()


class RunIn(BaseModel):
    action: str
    keep_custom: bool = True


@router.post("/run")
async def bedolaga_run(body: RunIn, user: str = Depends(require_auth)):
    if body.action not in ACTIONS:
        raise HTTPException(400, "Неизвестное действие")
    j = job()
    if not j["installed"]:
        raise HTTPException(409, "Обновлятор на сервере не установлен — выполни один раз: cd /opt/remnadeck && sudo bash install.sh")
    if j["state"] in ("running", "requested"):
        raise HTTPException(409, "Уже идёт другое действие — дождись конца")
    if body.action == "detect":
        _request("detect")
        return {"ok": True}
    i = info() or {}
    target = "bot" if body.action == "bot" else "cabinet"
    if not i.get(target):
        raise HTTPException(409, "Бот на сервере не найден" if target == "bot" else "Кабинет на сервере не найден")
    extra, label = {}, ""
    if body.action in ("bot", "cabinet"):
        gh = await releases(target, force=True)
        rel = gh["releases"][0] if gh["releases"] else None
        if not rel or not rel["sha"]:
            raise HTTPException(502, gh["error"] or "На GitHub нет релизов")
        extra = {"sha": rel["sha"], "version": rel["version"]}
        label = f" до {rel['tag']}"
    if body.action == "cabinet":
        extra["keep_custom"] = body.keep_custom
    if body.action in ("custom", "rollback") and i["cabinet"].get("mode") != "static":
        raise HTTPException(409, "Кабинет в контейнере — это доступно только для кабинета из файлов")
    if body.action == "rollback" and not i["cabinet"].get("backups"):
        raise HTTPException(409, "Резервных копий кабинета нет")
    _p("bedolaga.log").write_text("")
    _request(body.action, **extra)
    what = {"bot": "обновление бота", "cabinet": "обновление кабинета", "custom": "наложение своего слоя на кабинет",
            "rollback": "откат кабинета"}[body.action]
    await add_event("action", "info", f"{user}: Bedolaga — {what}{label}"
                                      f"{'' if body.action != 'cabinet' else ' (свои правки сохраняются)' if body.keep_custom else ' (чистый, без своих правок)'}")
    return {"ok": True}
