import logging
import time

import httpx

from .config import settings
from .db import get_db

log = logging.getLogger("events")
ICON = {"bad": "🔴", "warn": "🟡", "ok": "🟢", "info": "🔵"}


async def add_event(kind: str, level: str, text: str):
    db = await get_db()
    await db.execute("INSERT INTO events(ts,kind,level,text) VALUES(?,?,?,?)",
                     (int(time.time()), kind, level, text))
    await db.commit()
    if settings.tg_token and settings.tg_chat and level in ("bad", "warn", "ok"):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"https://api.telegram.org/bot{settings.tg_token}/sendMessage",
                             json={"chat_id": settings.tg_chat, "text": f"{ICON[level]} {text}"})
        except httpx.HTTPError as e:
            log.warning("telegram notify failed: %s", e)
