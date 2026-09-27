import os

import aiosqlite

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(ts INTEGER, node_uuid TEXT, node_name TEXT,
    traffic INTEGER, online INTEGER, connected INTEGER);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
    kind TEXT, level TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts);
CREATE TABLE IF NOT EXISTS health(target TEXT PRIMARY KEY, label TEXT, host TEXT, port INTEGER,
    sni TEXT, ts INTEGER, dns_ok INTEGER, tcp_ok INTEGER, tls_ok INTEGER, tls_trusted INTEGER,
    tls_days INTEGER, latency_ms INTEGER, ip TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, operator TEXT,
    token_hash TEXT UNIQUE, created INTEGER, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS agent_results(agent_id INTEGER, target TEXT, label TEXT, ok INTEGER,
    latency_ms INTEGER, error TEXT, ts INTEGER, PRIMARY KEY(agent_id, target));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS quota_rules(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
    node_uuids TEXT, limit_bytes INTEGER, period TEXT DEFAULT 'month', period_days INTEGER DEFAULT 30,
    anchor TEXT, full_squad TEXT, fallback_squad TEXT, write_desc INTEGER DEFAULT 1,
    desc_ok TEXT, desc_over TEXT, mode TEXT DEFAULT 'dry', enabled INTEGER DEFAULT 1,
    created INTEGER, last_run INTEGER, last_result TEXT);
CREATE TABLE IF NOT EXISTS quota_users(rule_id INTEGER, user_id INTEGER, username TEXT,
    used INTEGER DEFAULT 0, moved INTEGER DEFAULT 0, moved_period TEXT, moved_at INTEGER,
    descr TEXT, updated INTEGER, PRIMARY KEY(rule_id, user_id));
CREATE TABLE IF NOT EXISTS servers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
    host TEXT, port INTEGER DEFAULT 22, username TEXT DEFAULT 'root', auth TEXT,
    secret TEXT, key_pass TEXT, node_path TEXT DEFAULT '/opt/remnanode',
    node_uuid TEXT, note TEXT, created INTEGER, last_ok INTEGER, last_info TEXT);
CREATE TABLE IF NOT EXISTS cdn_templates(id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE, slug TEXT UNIQUE, body TEXT, note TEXT, builtin INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cdn_sites(id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER,
    template_id INTEGER, slug TEXT, domain TEXT, path TEXT, upstream INTEGER, ts INTEGER,
    UNIQUE(server_id, domain));
"""

# столбцы, добавленные после первого релиза: ALTER TABLE, если их ещё нет
MIGRATIONS = {
    "cdn_templates": {"default_path": "TEXT", "default_port": "INTEGER",
                      "is_default_server": "INTEGER DEFAULT 0"},
    "cdn_sites": {"domains": "TEXT", "is_default_server": "INTEGER DEFAULT 0"},
    "servers": {"last_fail": "INTEGER", "last_error": "TEXT"},
    # added_fb: сквад без обхода добавил движок (1) или он был у пользователя сам (0);
    # NULL — перевод был до этой правки, снимать его при возврате нельзя
    "quota_users": {"added_fb": "INTEGER"},
    "quota_rules": {"exempt": "TEXT"},
}

_db: aiosqlite.Connection | None = None


async def migrate(db: aiosqlite.Connection):
    for table, columns in MIGRATIONS.items():
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            have = {r["name"] for r in await cur.fetchall()}
        for name, kind in columns.items():
            if name not in have:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    await db.commit()


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
        _db = await aiosqlite.connect(settings.db_path)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(SCHEMA)
        await _db.commit()
        await migrate(_db)
    return _db


async def kv_get(key: str):
    db = await get_db()
    async with db.execute("SELECT value FROM kv WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else None


async def kv_set(key: str, value: str):
    db = await get_db()
    await db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))
    await db.commit()
