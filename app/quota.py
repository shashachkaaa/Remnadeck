"""Квоты трафика на отдельные ноды.

Правило: «на выбранных нодах — не больше 300 ГБ на пользователя в месяц».
Раз в N минут считаем трафик каждого пользователя на этих нодах за текущий
период. Превысил — меняем ему «сквад с лимитом» на «запасной» и рвём
активные соединения на нодах с лимитом. В новом периоде возвращаем обратно.

Клиенту цифры показываются через {{DESCRIPTION}} в названии хоста: Remnawave
подставляет туда описание конкретного пользователя при сборке подписки, а
RemnaDeck пишет в описание «120 из 300 ГБ».
"""
import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from .config import settings
from .db import get_db
from .events import add_event
from .remnawave import rw

log = logging.getLogger("quota")
GB = 1024 ** 3
_lock = asyncio.Lock()

DESC_OK = "{used} из {limit} ГБ"
DESC_OVER = "лимит исчерпан · вернётся {reset}"


# ---------------- период
def period_of(rule: dict, today: date | None = None) -> tuple[date, date, str]:
    """(начало, дата сброса, ключ периода). Сброс — первый день следующего периода."""
    today = today or datetime.now(timezone.utc).date()
    if rule.get("period") == "days":
        n = max(1, int(rule.get("period_days") or 30))
        anchor = date.fromisoformat(rule.get("anchor") or today.isoformat())
        k = max(0, (today - anchor).days // n)
        start = anchor + timedelta(days=k * n)
        return start, start + timedelta(days=n), start.isoformat()
    start = today.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt, start.strftime("%Y-%m")


# ---------------- текст для {{DESCRIPTION}}
def gb(n: int) -> str:
    v = n / GB
    if v < 0.05:
        return "0"
    return f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}"


def render(tpl: str, used: int, limit: int, reset: date) -> str:
    left = max(0, limit - used)
    return (tpl.replace("{used}", gb(used)).replace("{limit}", gb(limit))
            .replace("{left}", gb(left))
            .replace("{percent}", str(min(100, round(used * 100 / limit))) if limit else "0")
            .replace("{reset}", reset.strftime("%d.%m")))[:180]


def squad_ids(u: dict) -> list[str]:
    return [s if isinstance(s, str) else s.get("uuid")
            for s in (u.get("activeInternalSquads") or []) if s]


# ---------------- один прогон правила
async def run_rule(rule: dict, force_dry: bool = False) -> dict:
    """Возвращает сводку. dry — только считаем и показываем, кого бы перевели."""
    # dry — только считаем; desc — пишем счётчик в описание, но никого не переводим;
    # active — и счётчик, и перевод по превышению
    dry = force_dry or rule["mode"] != "active"
    write_desc_now = not force_dry and rule["mode"] in ("active", "desc")
    nodes = json.loads(rule["node_uuids"] or "[]")
    limit = int(rule["limit_bytes"] or 0)
    full, fallback = rule["full_squad"], rule["fallback_squad"]
    start, reset, key = period_of(rule)
    today = datetime.now(timezone.utc).date()

    # трафик на нодах правила: username -> байты за период
    used_by: dict[str, int] = {}
    for uuid in nodes:
        for name, total in (await rw.node_users_usage(uuid, start.isoformat(), today.isoformat())).items():
            used_by[name] = used_by.get(name, 0) + total

    db = await get_db()
    async with db.execute("SELECT * FROM quota_users WHERE rule_id=?", (rule["id"],)) as cur:
        state = {r["user_id"]: dict(r) for r in await cur.fetchall()}

    summary = {"period": key, "reset": reset.isoformat(), "dry": dry, "in_scope": 0,
               "over": 0, "near": 0, "moved": [], "restored": [], "restored_limit": [], "would_move": [],
               "would_restore": [],
               "desc_updated": 0, "errors": []}
    now = int(time.time())

    exempt = {int(x) for x in json.loads(rule.get("exempt") or "[]")}
    from .automations import held_users  # здесь, а не наверху: automations импортирует quota
    held = await held_users(rule["id"])
    for u in await rw.users_all():
        uid = u.get("id")
        if not uid:
            continue
        squads = squad_ids(u)
        st = state.get(uid)
        has_full, has_fb = full in squads, fallback in squads
        moved_earlier = bool(st and st["moved"])
        if not has_full and not moved_earlier and uid not in held:
            continue                                   # правило его не касается
        if uid in exempt and not moved_earlier:
            continue                                   # исключён — без лимита
        summary["in_scope"] += 1
        used = used_by.get(u.get("username"), 0)
        over = limit > 0 and used >= limit
        summary["over"] += over
        summary["near"] += (not over and limit > 0 and used >= limit * 0.8)
        new_squads = None
        action = None
        added_fb = (st or {}).get("added_fb")

        # 1) возвращаем переведённых: новый период, человека исключили или он больше не превышает
        #    лимит — его подняли посреди периода (или пересчёт трафика дал меньше)
        new_period = moved_earlier and st["moved_period"] != key
        if moved_earlier and (new_period or uid in exempt or not over):
            if has_fb and not has_full:                    # сквады в том виде, в каком мы их оставили
                drop_fb = st.get("added_fb") == 1          # запасной сквад снимаем, только если добавляли сами
                new_squads = [s for s in squads if not (drop_fb and s == fallback)] + [full]
                action = "restore"
            else:
                action = "forget"                      # сквады меняли руками — не трогаем
        # 2) превысил в текущем периоде
        elif over and has_full and not moved_earlier:
            new_squads = [s for s in squads if s != full] + ([] if has_fb else [fallback])
            added_fb = 0 if has_fb else 1
            action = "move"

        moved_now = moved_earlier
        if action == "move":
            if dry:
                summary["would_move"].append(u["username"])
            else:
                try:
                    await rw.user_update({"id": uid, "activeInternalSquads": new_squads})
                    try:
                        await rw.drop_connections([uid], nodes)
                    except Exception as e:             # noqa: BLE001 — сквад уже сменён, это главное
                        log.warning("drop connections %s: %s", uid, e)
                    moved_now = True
                    summary["moved"].append(u["username"])
                    await add_event("quota", "warn", f"Квота «{rule['name']}»: {u['username']} "
                                                     f"израсходовал {gb(used)} из {gb(limit)} ГБ — переведён на запасной сквад")
                except Exception as e:                 # noqa: BLE001
                    summary["errors"].append(f"{u['username']}: {e}")
        elif action in ("restore", "forget") and dry:
            summary["would_restore"].append(u["username"])
        elif action in ("restore", "forget") and not dry:
            try:
                if action == "restore":
                    await rw.user_update({"id": uid, "activeInternalSquads": new_squads})
                    summary["restored"].append(u["username"])
                    if not new_period and uid not in exempt:
                        summary["restored_limit"].append(u["username"])
                moved_now = False
            except Exception as e:                     # noqa: BLE001
                summary["errors"].append(f"{u['username']}: {e}")

        # текст для {{DESCRIPTION}} — пишем только когда он изменился
        desc = None
        if rule["write_desc"]:
            # used — всегда трафик текущего периода: после возврата человек видит «310 из 500»
            tpl = (rule["desc_over"] or DESC_OVER) if moved_now else (rule["desc_ok"] or DESC_OK)
            desc = render(tpl, used, limit, reset)
            if write_desc_now and desc != (u.get("description") or ""):
                try:
                    await rw.user_update({"id": uid, "description": desc})
                    summary["desc_updated"] += 1
                except Exception as e:                 # noqa: BLE001
                    summary["errors"].append(f"{u['username']} описание: {e}")

        await db.execute(
            """INSERT INTO quota_users(rule_id,user_id,username,used,moved,moved_period,moved_at,descr,updated,added_fb)
               VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(rule_id,user_id) DO UPDATE SET
               username=excluded.username, used=excluded.used, moved=excluded.moved,
               moved_period=excluded.moved_period, moved_at=excluded.moved_at,
               descr=COALESCE(excluded.descr, quota_users.descr), updated=excluded.updated,
               added_fb=excluded.added_fb""",
            (rule["id"], uid, u.get("username"), used, int(moved_now),
             key if moved_now else None,
             now if (action == "move" and moved_now) else (st or {}).get("moved_at") if moved_now else None,
             desc if write_desc_now else (st or {}).get("descr"), now,
             added_fb if moved_now else None))

    if summary["restored"]:
        by_limit = len(summary["restored_limit"])
        why = ("лимит больше не превышен" if by_limit == len(summary["restored"])
               else "новый период" if not by_limit else f"новый период и лимит больше не превышен ({by_limit})")
        await add_event("quota", "ok", f"Квота «{rule['name']}»: {why} — вернул доступ "
                                       f"{len(summary['restored'])} пользователям")
    await db.execute("UPDATE quota_rules SET last_run=?, last_result=? WHERE id=?",
                     (now, json.dumps(summary, ensure_ascii=False), rule["id"]))
    await db.commit()
    return summary


async def explain(rule: dict, username: str) -> dict:
    """Почему у пользователя такое (или пустое) описание — для кнопки в карточке правила."""
    users = await rw.users_all()
    u = next((x for x in users if (x.get("username") or "").lower() == username.strip().lower()), None)
    if not u:
        return {"found": False, "reasons": [f"Пользователь «{username}» не найден в Remnawave"]}
    nodes = json.loads(rule["node_uuids"] or "[]")
    start, reset, key = period_of(rule)
    today = datetime.now(timezone.utc).date()
    used = 0
    for uuid in nodes:
        used += (await rw.node_users_usage(uuid, start.isoformat(), today.isoformat())).get(u["username"], 0)
    squads = squad_ids(u)
    db = await get_db()
    async with db.execute("SELECT * FROM quota_users WHERE rule_id=? AND user_id=?", (rule["id"], u.get("id"))) as cur:
        st = await cur.fetchone()
    moved = bool(st and st["moved"])
    in_scope = rule["full_squad"] in squads or moved
    limit = int(rule["limit_bytes"] or 0)
    tpl = (rule["desc_over"] or DESC_OVER) if moved else (rule["desc_ok"] or DESC_OK)
    expected = render(tpl, used, limit, reset)
    actual = u.get("description") or ""
    reasons = []
    if not rule["enabled"]:
        reasons.append("Правило выключено")
    if rule["mode"] == "dry":
        reasons.append("Правило в тестовом режиме — описания не пишутся. Переключи на «только счётчик» или «боевой»")
    if not rule["write_desc"]:
        reasons.append("В правиле выключено «Показывать в подписке»")
    if not in_scope:
        reasons.append("У пользователя нет «сквада с лимитом» из правила — правило его не касается")
    if not rule["last_run"]:
        reasons.append("Правило ещё ни разу не прогонялось — нажми «Прогнать сейчас»")
    if not reasons and actual != expected:
        reasons.append("Описание ещё не обновилось: оно пишется при прогоне — нажми «Прогнать сейчас»")
    return {"found": True, "username": u["username"], "in_scope": in_scope, "moved": moved,
            "used": used, "limit": limit, "expected": expected, "actual": actual,
            "ok": not reasons and actual == expected, "reasons": reasons}


async def run_all():
    """Все включённые правила. Вызывается из фонового цикла."""
    async with _lock:
        db = await get_db()
        async with db.execute("SELECT * FROM quota_rules WHERE enabled=1") as cur:
            rules = [dict(r) for r in await cur.fetchall()]
        for rule in rules:
            try:
                await run_rule(rule)
            except Exception as e:                     # noqa: BLE001
                log.exception("quota rule %s", rule["id"])
                await add_event("quota", "bad", f"Квота «{rule['name']}»: ошибка прогона — {e}")
    from .automations import wake                  # свежие счётчики — сразу проверить пороги
    wake()


async def restore_all(rule: dict) -> list[str]:
    """Вернуть всех переведённых прямо сейчас, не дожидаясь нового периода."""
    db = await get_db()
    async with db.execute("SELECT * FROM quota_users WHERE rule_id=? AND moved=1", (rule["id"],)) as cur:
        moved = {r["user_id"]: dict(r) for r in await cur.fetchall()}
    back = []
    for u in await rw.users_all():
        if u.get("id") not in moved:
            continue
        squads = squad_ids(u)
        if rule["full_squad"] not in squads:
            drop_fb = moved[u["id"]].get("added_fb") == 1
            await rw.user_update({"id": u["id"], "activeInternalSquads":
                                  [s for s in squads if not (drop_fb and s == rule["fallback_squad"])]
                                  + [rule["full_squad"]]})
            back.append(u["username"])
        await db.execute("UPDATE quota_users SET moved=0, moved_period=NULL WHERE rule_id=? AND user_id=?",
                         (rule["id"], u["id"]))
    await db.commit()
    return back


_wake = asyncio.Event()
next_run_at = 0.0


def wake():
    """Разбудить цикл сейчас — после смены интервала он не ждёт конца старой паузы."""
    _wake.set()


async def loop_forever(_interval: int | None = None):
    """Интервал перечитывается на каждом круге — его можно менять из панели без перезапуска."""
    global next_run_at
    await asyncio.sleep(60)                            # даём панели подняться
    while True:
        try:
            await run_all()
        except Exception:                              # noqa: BLE001
            log.exception("quota loop")
        interval = settings.quota_interval
        next_run_at = time.time() + interval
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
