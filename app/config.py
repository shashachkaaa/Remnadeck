"""Настройки живут в .env (ENV_FILE). Панель читает его при старте и сама
дописывает изменения из веб-интерфейса."""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading

log = logging.getLogger("config")
ENV_PATH = os.getenv("ENV_FILE", "/config/.env")

DEFAULTS = {
    "PANEL_DOMAIN": "", "PANEL_PORT": "8090", "STATUS_DOMAIN": "", "SUB_DOMAIN": "",
    "SUB_UPSTREAM": "http://remnawave-subscription-page:3010",
    "REMNAWAVE_URL": "http://remnawave:3000", "REMNAWAVE_TOKEN": "",
    "REMNAWAVE_INTERNAL": "auto", "REMNAWAVE_VERIFY_TLS": "true",
    "ADMIN_USERNAME": "", "ADMIN_PASSWORD_HASH": "", "SECRET_KEY": "", "SETUP_TOKEN": "",
    "POLL_INTERVAL": "60", "CHECK_INTERVAL": "300",
    "TG_BOT_TOKEN": "", "TG_CHAT_ID": "",
    "COOKIE_SECURE": "true", "DB_PATH": "/data/panel.db",
    "CRYPT_KEY": "", "SSH_TIMEOUT": "20", "QUOTA_INTERVAL": "600",
}
_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$")
_lock = threading.Lock()


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _quote(v) -> str:
    v = str(v).replace("\n", " ").strip()
    if "$" in v and "'" not in v:
        return f"'{v}'"  # в одинарных кавычках compose не подставляет переменные
    return f'"{v}"' if re.search(r"[\s#\"'\\]", v) else v


def read_env(path: str = ENV_PATH) -> dict:
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.lstrip().startswith("#"):
                    continue
                m = _LINE.match(line)
                if m:
                    out[m.group(1)] = _unquote(m.group(2))
    except FileNotFoundError:
        pass
    return out


def write_env(changes: dict, path: str = ENV_PATH):
    """Меняет строки на месте, новые ключи дописывает в конец, None — удаляет ключ.
    Пишем в тот же файл (без rename), чтобы работал bind-mount одного файла."""
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = []
        left = dict(changes)
        out = []
        for line in lines:
            m = _LINE.match(line)
            if m and not line.lstrip().startswith("#") and m.group(1) in left:
                value = left.pop(m.group(1))
                if value is not None:
                    out.append(f"{m.group(1)}={_quote(value)}")
                continue
            out.append(line)
        out += [f"{k}={_quote(v)}" for k, v in left.items() if v is not None]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt:{salt.hex()}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split(":")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), h)


class Settings:
    def __init__(self):
        self.v: dict = {}
        self.load()

    def load(self):
        self.v = {**DEFAULTS, **read_env()}

    def update(self, changes: dict):
        write_env(changes)
        self.load()

    # --- Remnawave
    @property
    def rw_url(self): return self.v["REMNAWAVE_URL"].rstrip("/")
    @property
    def rw_token(self): return self.v["REMNAWAVE_TOKEN"]
    @property
    def rw_internal_mode(self): return self.v["REMNAWAVE_INTERNAL"].lower()
    @property
    def rw_internal(self):
        mode = self.rw_internal_mode
        return self.rw_url.startswith("http://") if mode == "auto" else _bool(mode)
    @property
    def rw_verify_tls(self): return _bool(self.v["REMNAWAVE_VERIFY_TLS"])

    # --- доступ
    @property
    def admin_user(self): return self.v["ADMIN_USERNAME"]
    @property
    def admin_hash(self): return self.v["ADMIN_PASSWORD_HASH"]
    @property
    def admin_ready(self): return bool(self.admin_user and self.admin_hash)
    @property
    def secret_key(self): return self.v["SECRET_KEY"]
    @property
    def setup_token(self): return self.v["SETUP_TOKEN"]
    @property
    def cookie_secure(self): return _bool(self.v["COOKIE_SECURE"])

    # --- прочее
    @property
    def panel_domain(self): return self.v["PANEL_DOMAIN"]
    @property
    def status_domain(self): return self.v["STATUS_DOMAIN"].strip().lower()
    @property
    def sub_domain(self): return self.v["SUB_DOMAIN"].strip().lower()
    @property
    def sub_upstream(self): return self.v["SUB_UPSTREAM"].strip().rstrip("/")
    @property
    def poll_interval(self): return max(15, int(self.v["POLL_INTERVAL"] or 60))
    @property
    def check_interval(self): return max(60, int(self.v["CHECK_INTERVAL"] or 300))
    @property
    def tg_token(self): return self.v["TG_BOT_TOKEN"]
    @property
    def tg_chat(self): return self.v["TG_CHAT_ID"]
    @property
    def db_path(self): return self.v["DB_PATH"]
    @property
    def crypt_key(self): return self.v["CRYPT_KEY"]
    @property
    def quota_interval(self): return min(86400, max(60, int(self.v["QUOTA_INTERVAL"] or 600)))
    @property
    def ssh_timeout(self): return max(5, int(self.v["SSH_TIMEOUT"] or 20))


settings = Settings()


def _fernet():
    """Ключ шифрования паролей SSH. Отдельный от SECRET_KEY: тот меняется при
    смене пароля администратора, и данные серверов стали бы нечитаемыми."""
    from cryptography.fernet import Fernet
    raw = settings.crypt_key
    if not raw:
        raise RuntimeError("CRYPT_KEY не задан")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode() if value else ""


def bootstrap():
    if os.path.isdir(ENV_PATH):
        raise RuntimeError(f"{ENV_PATH} — папка, а не файл. Удали её на хосте, создай пустой .env и перезапусти")
    changes = {}
    if not settings.secret_key:
        changes["SECRET_KEY"] = secrets.token_hex(32)
    if not settings.crypt_key:
        changes["CRYPT_KEY"] = secrets.token_hex(32)
    legacy = settings.v.get("ADMIN_PASSWORD")
    if legacy and not settings.admin_hash:  # миграция с версии 0.1
        changes["ADMIN_PASSWORD_HASH"] = hash_password(legacy)
        changes["ADMIN_USERNAME"] = settings.admin_user or "admin"
    if "ADMIN_PASSWORD" in settings.v:
        changes["ADMIN_PASSWORD"] = None
    if changes:
        settings.update(changes)
    if not settings.admin_ready:
        if not settings.setup_token:
            settings.update({"SETUP_TOKEN": secrets.token_hex(5)})
        log.warning("Панель не настроена. Код первого входа: %s", settings.setup_token)
