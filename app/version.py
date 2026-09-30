"""Версия панели и обновление по кнопке.

Что собрано: build.json кладёт install.sh перед сборкой образа (коммит, дата, репозиторий).
Что нового: GitHub compare «наш коммит…ветка» — сколько коммитов впереди и их сообщения
(кеш 30 минут: без токена GitHub даёт 60 запросов в час).

Обновление: контейнер не может пересобрать сам себя, а давать ему docker.sock — это root на
хосте. Поэтому панель только кладёт data/update.request, а systemd-юнит на хосте
(remnadeck-update.path, ставит install.sh) запускает deploy/updater.sh → install.sh --update.
Ход — в data/update.log, состояние — в data/update.state.
"""
import json
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .auth import require_auth
from .config import settings
from .events import add_event

router = APIRouter(prefix="/api/version", tags=["version"])

VERSION = "0.7.0"
BUILD = Path(__file__).parent / "build.json"
_cache: dict = {"ts": 0.0, "v": None}


def data_dir() -> Path:
    return Path(os.path.dirname(settings.db_path) or "/data")


def build_info() -> dict:
    try:
        b = json.loads(BUILD.read_text())
    except (OSError, ValueError):
        b = {}
    return {"version": VERSION, "commit": b.get("commit") or "", "built_at": b.get("built_at"),
            "repo": b.get("repo") or "shashachkaaa/Remnadeck", "branch": b.get("branch") or "main"}


async def check(force: bool = False) -> dict:
    if not force and _cache["v"] and time.monotonic() - _cache["ts"] < 1800:
        return _cache["v"]
    b = build_info()
    api = f"https://api.github.com/repos/{b['repo']}"
    out = {"checked_at": int(time.time()), "status": "unknown", "behind": 0, "commits": [], "latest": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=10, headers={"Accept": "application/vnd.github+json"}) as c:
            if b["commit"]:
                r = await c.get(f"{api}/compare/{b['commit']}...{b['branch']}")
                if r.status_code == 404:  # нашего коммита на GitHub нет — собрано из неотправленных правок
                    out["status"] = "local"
                else:
                    r.raise_for_status()
                    d = r.json()
                    # compare base...head: ahead_by — сколько на GitHub новее нас, behind_by — сколько у нас не отправлено
                    out["behind"], out["ahead"] = d.get("ahead_by", 0), d.get("behind_by", 0)
                    out["status"] = {"identical": "latest", "ahead": "update", "behind": "local",
                                     "diverged": "diverged"}.get(d.get("status"), "unknown")
                    out["commits"] = [{"sha": x["sha"][:7], "message": x["commit"]["message"].split("\n")[0][:140],
                                       "date": x["commit"]["author"]["date"]} for x in reversed(d.get("commits", []))][:30]
            r = await c.get(f"{api}/commits/{b['branch']}")
            r.raise_for_status()
            x = r.json()
            out["latest"] = {"sha": x["sha"][:7], "message": x["commit"]["message"].split("\n")[0][:140],
                             "date": x["commit"]["author"]["date"]}
            if not b["commit"]:
                out["status"] = "unknown"
    except (httpx.HTTPError, ValueError, KeyError) as e:
        out["error"] = f"GitHub недоступен: {e.__class__.__name__}"
    _cache["ts"], _cache["v"] = time.monotonic(), out
    return out


def updater_state() -> dict:
    d = data_dir()
    installed = (d / "updater.installed").exists()
    state, ts = "idle", None
    try:
        parts = (d / "update.state").read_text().split()
        state, ts = parts[0], int(parts[1]) if len(parts) > 1 else None
    except (OSError, ValueError, IndexError):
        pass
    if (d / "update.request").exists():
        state = "requested"
    try:
        log = (d / "update.log").read_text(errors="replace")[-6000:]
    except OSError:
        log = ""
    return {"installed": installed, "state": state, "ts": ts, "log": log}


@router.get("")
async def version_get(force: bool = False, _: str = Depends(require_auth)):
    return {**build_info(), "github": await check(force), "updater": updater_state()}


@router.get("/update")
async def version_update_state(_: str = Depends(require_auth)):
    return {**updater_state(), "commit": build_info()["commit"]}


@router.post("/update")
async def version_update(user: str = Depends(require_auth)):
    st = updater_state()
    if not st["installed"]:
        raise HTTPException(409, "Обновлятор на сервере не установлен — выполни install.sh один раз вручную")
    if st["state"] in ("requested", "running"):
        raise HTTPException(409, "Обновление уже идёт")
    d = data_dir()
    (d / "update.log").write_text("")
    (d / "update.request").write_text(json.dumps({"by": user, "ts": int(time.time())}))
    await add_event("action", "info", f"{user}: запущено обновление панели")
    return {"ok": True}
