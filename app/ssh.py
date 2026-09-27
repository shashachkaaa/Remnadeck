"""SSH к серверам нод: выполнение скриптов с живым логом.

Remnawave API умеет только управлять записью ноды в панели. Поставить
сертификат, поправить docker-compose или подтянуть новый образ можно
только на самом сервере, поэтому всё это идёт по SSH.
"""
import asyncio
import logging
import shlex
import time
import uuid

import asyncssh

from .config import decrypt, settings

log = logging.getLogger("ssh")
MAX_JOBS = 40


class SSHError(Exception):
    pass


PUBLIC_PREFIXES = ("ssh-ed25519", "ssh-rsa", "ssh-dss", "ecdsa-sha2-", "sk-ssh-", "sk-ecdsa-")


def check_key(text: str, passphrase: str = "") -> str:
    """Проверяет приватный ключ до сохранения и возвращает его нормализованным.

    Самая частая ошибка — вставляют содержимое id_ed25519.pub: это публичный
    ключ, он живёт в authorized_keys на сервере и для входа не годится.
    """
    text = (text or "").strip()
    if not text:
        raise SSHError("Ключ пустой")
    if text.startswith(PUBLIC_PREFIXES):
        raise SSHError(
            "Это публичный ключ — он лежит на сервере в ~/.ssh/authorized_keys. "
            "Нужен приватный: файл id_ed25519 без расширения .pub, он начинается "
            "со строки -----BEGIN OPENSSH PRIVATE KEY-----")
    text += "\n"                                   # OpenSSH требует перевод строки в конце
    enc_error = getattr(asyncssh, "KeyEncryptionError", asyncssh.KeyImportError)
    try:
        asyncssh.import_private_key(text, passphrase.strip() or None)
    except enc_error as e:
        raise SSHError("Ключ зашифрован — укажи пароль ключа. Если он введён, он не подошёл" 
                       if not passphrase.strip() else f"Пароль ключа не подошёл: {e}") from e
    except asyncssh.KeyImportError as e:
        raise SSHError(f"Ключ не читается: {e}. Вставляй файл целиком, вместе со строками "
                       "BEGIN и END") from e
    return text


def _conn_args(srv: dict) -> dict:
    kw = {
        "host": srv["host"],
        "port": int(srv["port"] or 22),
        "username": srv["username"] or "root",
        "known_hosts": None,          # серверы добавляет сам админ, доверяем записи
        "connect_timeout": settings.ssh_timeout,
    }
    secret = decrypt(srv["secret"] or "")
    if srv["auth"] == "password":
        kw["password"] = secret
    else:
        passphrase = decrypt(srv["key_pass"] or "") or None
        try:
            kw["client_keys"] = [asyncssh.import_private_key(secret.strip() + "\n", passphrase)]
        except asyncssh.KeyImportError as e:
            raise SSHError(f"Приватный ключ не читается: {e}") from e
    return kw


async def connect(srv: dict):
    try:
        return await asyncio.wait_for(asyncssh.connect(**_conn_args(srv)), settings.ssh_timeout + 10)
    except asyncio.TimeoutError as e:
        raise SSHError(f"{srv['host']}: таймаут подключения") from e
    except asyncssh.PermissionDenied as e:
        raise SSHError(f"{srv['host']}: сервер отверг логин или ключ") from e
    except (OSError, asyncssh.Error) as e:
        raise SSHError(f"{srv['host']}: {e}") from e


async def probe(srv: dict) -> dict:
    """Быстрая проверка: доступ, docker, наличие каталога ноды."""
    async with await connect(srv) as conn:
        path = shlex.quote(srv["node_path"] or "/opt/remnanode")
        cmd = (
            "echo \"os=$(. /etc/os-release 2>/dev/null; echo ${PRETTY_NAME:-unknown})\"; "
            "echo \"docker=$(docker --version 2>/dev/null | head -1)\"; "
            f"echo \"compose=$(test -f {path}/docker-compose.yml && echo yes || echo no)\"; "
            f"echo \"image=$(grep -oE 'remnawave/node:[A-Za-z0-9_.-]+' {path}/docker-compose.yml 2>/dev/null | head -1)\"; "
            "echo \"nginx=$(command -v nginx >/dev/null && echo yes || echo no)\"; "
            "echo \"root=$(id -u)\""
        )
        r = await conn.run(cmd, check=False)
        out = {}
        for line in (r.stdout or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        return out


def sudo_prefix(srv: dict) -> str:
    return "" if (srv["username"] or "root") == "root" else "sudo -n "


async def run_cmd(srv: dict, cmd: str, timeout: float = 40) -> str:
    """Короткая команда без живого лога — для сбора данных (сертификаты и т.п.)."""
    async with await connect(srv) as conn:
        r = await asyncio.wait_for(conn.run(sudo_prefix(srv) + "bash -lc " + shlex.quote(cmd), check=False),
                                   timeout)
        return (r.stdout or "") + (r.stderr or "")


# ---------------- фоновые задачи с логом


class Job:
    def __init__(self, title: str, target: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.target = target
        self.lines: list[str] = []
        self.status = "running"          # running | ok | error
        self.started = int(time.time())
        self.finished: int | None = None

    def write(self, text: str):
        for line in str(text).rstrip().splitlines():
            self.lines.append(line)
        del self.lines[:-800]

    def done(self, ok: bool, message: str = ""):
        if message:
            self.write(message)
        self.status = "ok" if ok else "error"
        self.finished = int(time.time())

    def dump(self, since: int = 0) -> dict:
        return {"id": self.id, "title": self.title, "target": self.target, "status": self.status,
                "started": self.started, "finished": self.finished,
                "from": since, "lines": self.lines[since:], "total": len(self.lines)}


JOBS: dict[str, Job] = {}


def new_job(title: str, target: str = "") -> Job:
    job = Job(title, target)
    JOBS[job.id] = job
    for old in list(JOBS)[:-MAX_JOBS]:
        JOBS.pop(old, None)
    return job


async def run_script(srv: dict, script: str, job: Job, sudo: bool = True) -> int:
    """Заливает скрипт во временный файл и выполняет, отдавая вывод в job."""
    async with await connect(srv) as conn:
        remote = f"/tmp/remnadeck-{job.id}.sh"
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(remote, "w") as f:
                await f.write(script)
        prefix = ""
        if sudo and (srv["username"] or "root") != "root":
            prefix = "sudo -n "
        proc = await conn.create_process(f"{prefix}bash {remote}; rc=$?; rm -f {remote}; exit $rc")
        async for line in proc.stdout:
            job.write(line)
        err = await proc.stderr.read()
        if err:
            job.write(err)
        result = await proc.wait()
        return result.exit_status or 0


async def run_job(srv: dict, script: str, job: Job, on_ok=None):
    """Обёртка: ловит ошибки, закрывает job, при успехе зовёт on_ok()."""
    try:
        code = await run_script(srv, script, job)
    except SSHError as e:
        job.done(False, f"✗ {e}")
        return
    except Exception as e:                                  # noqa: BLE001
        log.exception("job %s failed", job.id)
        job.done(False, f"✗ Неожиданная ошибка: {e.__class__.__name__}: {e}")
        return
    if code == 0:
        if on_ok:
            try:
                await on_ok()
            except Exception:                               # noqa: BLE001
                log.exception("job %s on_ok failed", job.id)
        job.done(True, "✓ Готово")
    else:
        job.done(False, f"✗ Скрипт завершился с кодом {code}")
