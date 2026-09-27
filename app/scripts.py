"""Скрипты, которые панель заливает на сервер ноды и выполняет по SSH.

Все подстановки идут через shlex.quote — значения приходят из веб-формы.
Логика Hysteria2 повторяет remnautility.sh: сертификат Let's Encrypt,
монтирование его в контейнер ноды и переезд на тег latest.
"""
import re
import shlex

HEAD = """#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
say() { printf '\\n== %s\\n' "$*"; }
need_root() { [ "$(id -u)" = "0" ] || { echo "Нужен root"; exit 1; }; }
need_root
"""

APT = """
apt_install() {
  local miss=()
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || miss+=("$p"); done
  [ ${#miss[@]} -eq 0 ] && return 0
  say "Ставлю пакеты: ${miss[*]}"
  apt-get update -y -qq
  apt-get install -y -qq "${miss[@]}"
}
"""

COMPOSE = """
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
check_compose() {
  [ -f "$COMPOSE_FILE" ] || { echo "Не нашёл $COMPOSE_FILE — проверь путь к ноде"; exit 1; }
  command -v docker >/dev/null || { echo "На сервере нет docker"; exit 1; }
}
"""


CERT_HELPERS = """
# Проверяем домен так, как его увидит Let's Encrypt: публичным резолвером и по всем записям.
check_dns() {
  local d="$1" ips cname
  IP_SRV=$(curl -4 -s --max-time 6 https://api.ipify.org 2>/dev/null || true)
  [ -n "$IP_SRV" ] || IP_SRV=$(curl -4 -s --max-time 6 https://ifconfig.me 2>/dev/null || true)
  cname=$(dig @1.1.1.1 +short CNAME "$d" 2>/dev/null | head -1)
  ips=$(dig @1.1.1.1 +short A "$d" 2>/dev/null | grep -E '^[0-9.]+$' | tr '\n' ' ')
  [ -n "$ips" ] || ips=$(dig @8.8.8.8 +short A "$d" 2>/dev/null | grep -E '^[0-9.]+$' | tr '\n' ' ')
  echo "  $d -> ${ips:-нет A-записи}${cname:+  (CNAME $cname)}"
  echo "  этот сервер: ${IP_SRV:-IP не определён}"
  [ -n "$IP_SRV" ] || return 1
  case " $ips " in *" $IP_SRV "*) return 0 ;; esac
  return 1
}

dns_hint() {
  echo
  echo "Домен ведёт не на этот сервер, поэтому проверка по HTTP-01 не пройдёт:"
  echo "Let's Encrypt постучится по адресу выше, а не сюда."
  echo "Варианты:"
  echo "  1. Поправить A-запись на IP этого сервера и запустить заново."
  echo "  2. Если домен специально за CDN или Cloudflare — указать в форме токен"
  echo "     Cloudflare API: сертификат выпустится через DNS-01 и адрес будет не важен."
  echo "  3. Проверить, нет ли лишних A-записей: LE может выбрать любую из них."
}

cf_prepare() {
  apt_install python3-certbot-dns-cloudflare
  install -m 600 /dev/null /etc/letsencrypt/rd-cloudflare.ini
  printf 'dns_cloudflare_api_token = %s\n' "$1" > /etc/letsencrypt/rd-cloudflare.ini
}
"""


def q(v) -> str:
    return shlex.quote(str(v))


def update_node(node_path: str) -> str:
    """docker compose pull + up -d, с версиями до и после."""
    return f"""{HEAD}{COMPOSE}
COMPOSE_FILE={q(node_path)}/docker-compose.yml
check_compose
say "Текущий образ"
grep -oE 'remnawave/node:[A-Za-z0-9_.-]+' "$COMPOSE_FILE" | head -1 || true
docker inspect --format '{{{{.Config.Image}}}} ({{{{.Created}}}})' \\
  "$(compose ps -q remnanode 2>/dev/null | head -1)" 2>/dev/null || true
say "Скачиваю обновления"
compose pull
say "Перезапускаю"
compose up -d
sleep 3
say "Состояние"
compose ps
say "Последние строки лога"
compose logs --tail 15 remnanode 2>/dev/null || true
say "Чищу старые образы"
docker image prune -f >/dev/null 2>&1 || true
"""


def setup_hysteria2(node_path: str, domain: str, email: str = "", cf_token: str = "") -> str:
    """Сертификат Let's Encrypt + монтирование в контейнер ноды + тег latest."""
    reg = f"--email {q(email)}" if email else "--register-unsafely-without-email"
    return f"""{HEAD}{APT}{COMPOSE}{CERT_HELPERS}
COMPOSE_FILE={q(node_path)}/docker-compose.yml
DOMAIN={q(domain)}
CF_TOKEN={q(cf_token)}
CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
KEY=/etc/letsencrypt/live/$DOMAIN/privkey.pem
check_compose

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  say "Сертификат $DOMAIN уже есть, выпуск пропускаю"
else
  apt_install dnsutils curl certbot
  if [ -n "$CF_TOKEN" ]; then
    say "Выпускаю сертификат через DNS-01 Cloudflare"
    cf_prepare "$CF_TOKEN"
    certbot certonly --dns-cloudflare \\
      --dns-cloudflare-credentials /etc/letsencrypt/rd-cloudflare.ini \\
      --dns-cloudflare-propagation-seconds 30 -d "$DOMAIN" \\
      --non-interactive --agree-tos {reg} \\
      --deploy-hook "docker compose -f $COMPOSE_FILE restart remnanode" || {{
        echo "DNS-01 не прошёл. Токен должен давать Zone:DNS:Edit на эту зону"; exit 1; }}
  else
    say "Проверяю домен $DOMAIN"
    if ! check_dns "$DOMAIN"; then
      dns_hint
      echo
      echo "Ничего не менял: файлы ноды не тронуты, сертификат не запрашивался."
      exit 1
    fi
    echo "  совпадает, выпускаю по HTTP-01"
    STOPPED=""
    if ss -ltn 2>/dev/null | grep -q ':80 '; then
      if systemctl is-active --quiet nginx; then
        systemctl stop nginx; STOPPED=nginx
        echo "Временно остановил nginx — порт 80 нужен certbot"
      else
        echo "! Порт 80 занят, certbot может не подняться:"; ss -ltnp | grep ':80 ' || true
      fi
    fi
    set +e
    certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos {reg} \\
      --deploy-hook "docker compose -f $COMPOSE_FILE restart remnanode"
    RC=$?
    set -e
    [ -n "$STOPPED" ] && systemctl start nginx || true
    if [ $RC -ne 0 ]; then
      echo
      echo "Сертификат выпустить не удалось. Частые причины:"
      echo "  · 80/tcp закрыт облачным фаерволом (GCP, Hetzner, Oracle) — открой у хостера"
      echo "  · домен за CDN, запрос до сервера не доходит — используй токен Cloudflare"
      echo "  · у домена несколько A-записей и LE выбрал чужую"
      echo "Файлы ноды не тронуты."
      exit 1
    fi
  fi
fi

say "Правлю $COMPOSE_FILE"
python3 - "$COMPOSE_FILE" "$CERT" "$KEY" <<'PYEOF'
import re, shutil, sys, time
path, cert, key = sys.argv[1:4]
src = open(path, encoding="utf-8").read()
shutil.copy(path, f"{{path}}.bak.{{int(time.time())}}")
src = re.sub(r"remnawave/node:[A-Za-z0-9_.-]+", "remnawave/node:latest", src)
lines = [l for l in src.splitlines() if "/var/lib/remnawave/configs/xray/ssl" not in l]
mounts = lambda ind: [f"{{ind}}- {{cert}}:/var/lib/remnawave/configs/xray/ssl/cert.pem:ro",
                      f"{{ind}}- {{key}}:/var/lib/remnawave/configs/xray/ssl/cert.key:ro"]
out, done = [], False
for line in lines:
    out.append(line)
    m = re.match(r"^(\\s+)volumes:\\s*$", line)
    if m and not done:
        out += mounts(m.group(1) + "  ")
        done = True
if not done:
    out = []
    for line in lines:
        out.append(line)
        m = re.match(r"^(\\s+)image:\\s*remnawave/node", line)
        if m and not done:
            out.append(f"{{m.group(1)}}volumes:")
            out += mounts(m.group(1) + "  ")
            done = True
if not done:
    sys.exit("Не нашёл сервис remnanode в docker-compose.yml — правь вручную")
open(path, "w", encoding="utf-8").write("\\n".join(out) + "\\n")
print("готово: тег latest, сертификаты примонтированы")
PYEOF

say "Перезапускаю ноду"
compose pull
compose up -d --force-recreate
sleep 3
compose ps
echo
echo "Дальше в Remnawave: в профиле ноды включить инбаунд Hysteria2,"
echo "в сертификатах Xray указать /var/lib/remnawave/configs/xray/ssl/cert.pem и cert.key"
"""


CDN_COMMON = r"""# RemnaDeck: общие настройки для CDN-хостов. Файл создаёт и перезаписывает панель.
# Директивы, которые уже заданы в /etc/nginx/nginx.conf, панель отключает
# автоматически и помечает их строкой #rd-disabled.

# Yandex гонит uplink в заголовке X-Client-Data (~18 КБ), Beeline — в теле запроса.
large_client_header_buffers 8 64k;
client_max_body_size 2m;

# CDN держит одно соединение под тысячи мелких запросов.
keepalive_timeout 120s;
keepalive_requests 100000;
http2_max_concurrent_streams 256;
reset_timedout_connection on;
server_tokens off;

ssl_session_cache   shared:SSL:20m;
ssl_session_timeout 1d;
ssl_session_tickets on;

# Реальный IP клиента: CDN дописывает его в КОНЕЦ X-Forwarded-For.
map $http_x_forwarded_for $rd_client_ip {
    default                     $remote_addr;
    "~(?<xff_last>[^,\s]+)\s*$" $xff_last;
}

log_format rd_cdn '$time_local $host $remote_addr '
                  'xff="$http_x_forwarded_for" client=$rd_client_ip '
                  '$request_method $uri $status';

# XHTTP, не WebSocket. Если в location появится свой proxy_set_header,
# все эти заголовки в ней перестанут действовать — в шаблонах их не задавай.
proxy_http_version 1.1;
proxy_set_header Connection      "";
proxy_set_header Host            $host;
proxy_set_header X-Real-IP       $rd_client_ip;
proxy_set_header X-Forwarded-For $rd_client_ip;
proxy_buffering         off;
proxy_request_buffering off;
proxy_connect_timeout 5s;
proxy_read_timeout    1h;
proxy_send_timeout    1h;
"""

# nginx ругается на директиву, уже заданную в nginx.conf. Гасим нашу копию и пробуем снова.
FIX_NGINX = """
fix_nginx() {
  local file="$1" site="$2" out line n=0
  while :; do
    out=$(nginx -t 2>&1) && return 0
    n=$((n + 1)); [ $n -gt 25 ] && { echo "$out"; return 1; }
    line=$(echo "$out" | grep -oE "$file:[0-9]+" | head -1 | cut -d: -f2)
    if echo "$out" | grep -q "is duplicate" && [ -n "$line" ]; then
      echo "  · отключил строку $line — уже задано в nginx.conf: $(sed -n "${line}p" "$file" | sed 's/^ *//')"
      sed -i "${line}s|^|#rd-disabled |" "$file"
      continue
    fi
    if echo "$out" | grep -q "duplicate default server" && [ -n "$site" ]; then
      echo "  · снял default_server: его уже занял другой сайт"
      sed -i 's/ default_server//g' "$site"
      continue
    fi
    echo "$out"; return 1
  done
}
"""


def cdn_install(slug: str, domains: list, path: str, port: int, body: str,
                email: str = "", default_server: bool = False, cf_token: str = "",
                cert_domains: list | None = None) -> str:
    """Общий http-конфиг + сертификат + сайт из шаблона. В server_name идут все домены,
    в сертификат — cert_domains (по умолчанию тоже все). Техдомен CDN (Timeweb) смотрит
    не на сервер и не в нашей зоне DNS: ни HTTP-01, ни DNS-01 на него не пройдут."""
    reg = f"--email {q(email)}" if email else "--register-unsafely-without-email"
    conf_name = f"rd-cdn-{slug}"
    names = " ".join(domains)
    primary = domains[0]
    rendered = (body
                .replace("{{server_names}}", names)
                .replace("{{domain}}", primary)
                .replace("{{path}}", path)
                .replace("{{port}}", str(port))
                .replace("{{upstream}}", "rd_" + re.sub(r"[^a-z0-9]+", "_", slug.lower()))
                .replace("{{default}}", " default_server" if default_server else "")
                .replace("{{cert}}", f"/etc/letsencrypt/live/{primary}/fullchain.pem")
                .replace("{{key}}", f"/etc/letsencrypt/live/{primary}/privkey.pem"))
    boot = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {names};
    location /.well-known/acme-challenge/ {{ root /var/www/rd-acme; }}
    location / {{ return 404; }}
}}"""
    cert_domains = cert_domains or domains
    cert_names = " ".join(cert_domains)
    extra = " ".join(d for d in domains if d not in cert_domains)
    dflags = " ".join(f"-d {q(d)}" for d in cert_domains)
    return f"""{HEAD}{APT}{FIX_NGINX}{CERT_HELPERS}
CONF=/etc/nginx/sites-available/{q(conf_name)}
LINK=/etc/nginx/sites-enabled/{q(conf_name)}
COMMON=/etc/nginx/conf.d/rd-cdn-common.conf
CERT=/etc/letsencrypt/live/{q(primary)}/fullchain.pem
CF_TOKEN={q(cf_token)}

apt_install nginx certbot dnsutils curl
mkdir -p /var/www/rd-acme
[ -d /etc/nginx/sites-enabled ] || {{ echo "Нестандартная сборка nginx: нет sites-enabled"; exit 1; }}

say "Общие настройки CDN ($COMMON)"
cat > "$COMMON" <<'NGINX'
{CDN_COMMON}
NGINX
fix_nginx "$COMMON" "" || {{ echo "nginx не принял общий конфиг"; exit 1; }}
systemctl reload nginx 2>/dev/null || systemctl start nginx

{f'echo "Без сертификата, только в server_name: {extra}"' if extra else ""}
if [ -f "$CERT" ]; then
  say "Сертификат {primary} уже есть"
  certbot certificates --cert-name {q(primary)} 2>/dev/null | grep -E 'Domains|Expiry' || true
elif [ -n "$CF_TOKEN" ]; then
  say "Выпускаю сертификат через DNS-01 Cloudflare: {cert_names}"
  cf_prepare "$CF_TOKEN"
  certbot certonly --dns-cloudflare \\
    --dns-cloudflare-credentials /etc/letsencrypt/rd-cloudflare.ini \\
    --dns-cloudflare-propagation-seconds 30 {dflags} --cert-name {q(primary)} \\
    --non-interactive --agree-tos {reg} --deploy-hook "systemctl reload nginx" || {{
      echo "DNS-01 не прошёл. Токен должен давать Zone:DNS:Edit на все зоны из списка"; exit 1; }}
else
  say "Проверяю домены"
  BAD=0
  for d in {cert_names}; do check_dns "$d" || BAD=1; done
  if [ $BAD -eq 1 ]; then
    dns_hint
    echo
    echo "Для домена за CDN это нормально: сертификат надо брать через DNS-01."
    echo "Укажи токен Cloudflare в форме — тогда адрес домена значения не имеет."
    echo "Конфиг nginx не менял."
    exit 1
  fi
  say "Временный конфиг под проверку Let's Encrypt"
  cat > "$CONF" <<'NGINX'
{boot}
NGINX
  ln -sf "$CONF" "$LINK"
  fix_nginx "$COMMON" "$CONF" || exit 1
  systemctl reload nginx
  say "Выпускаю сертификат: {cert_names}"
  certbot certonly --webroot -w /var/www/rd-acme {dflags} --cert-name {q(primary)} \\
    --non-interactive --agree-tos {reg} --deploy-hook "systemctl reload nginx" || {{
      echo "Сертификат не выпущен: проверь, что 80/tcp доходит до сервера снаружи"
      echo "и что домен ещё не проксируется CDN. Иначе используй токен Cloudflare."
      exit 1; }}
fi

say "Ставлю конфиг {conf_name}"
cat > "$CONF" <<'NGINX'
{rendered}
NGINX
ln -sf "$CONF" "$LINK"
fix_nginx "$COMMON" "$CONF" || exit 1
systemctl reload nginx

say "Проверка"
curl -sk -o /dev/null -w 'корень: HTTP %{{http_code}}\\n' "https://{primary}/" || true
curl -sk -o /dev/null -w 'рабочий путь: HTTP %{{http_code}} (400 — нормально, Xray отвечает)\\n' \\
  "https://{primary}{path}" || true
echo
echo "Инбаунд должен слушать 127.0.0.1:{port}, путь {path}"
echo "В инбаунде включи доверие X-Forwarded-For — реальный IP приходит в X-Real-IP"
"""


def cdn_remove(slug: str, domain: str, drop_cert: bool = False) -> str:
    """Снять конфиг CDN и, если просят, удалить сертификат."""
    conf_name = f"rd-cdn-{slug}"
    cert = f"""
say "Удаляю сертификат"
certbot delete --cert-name {q(domain)} --non-interactive || true
""" if drop_cert else ""
    return f"""{HEAD}
CONF=/etc/nginx/sites-available/{q(conf_name)}
LINK=/etc/nginx/sites-enabled/{q(conf_name)}
say "Снимаю конфиг {conf_name}"
rm -f "$LINK" "$CONF"
nginx -t && systemctl reload nginx || true
{cert}
say "Осталось включённых конфигов RemnaDeck"
ls /etc/nginx/sites-enabled/ | grep '^rd-cdn-' || echo "нет"
"""


def cdn_status() -> str:
    return f"""{HEAD}
say "nginx"
nginx -v 2>&1 || echo "не установлен"
systemctl is-active nginx 2>/dev/null || true
say "Общий конфиг"
if [ -f /etc/nginx/conf.d/rd-cdn-common.conf ]; then
  grep -c . /etc/nginx/conf.d/rd-cdn-common.conf | xargs echo "строк:"
  grep '#rd-disabled' /etc/nginx/conf.d/rd-cdn-common.conf | sed 's/^/  отключено: /' || echo "  всё применено"
else
  echo "не установлен"
fi
say "Конфиги RemnaDeck"
for f in /etc/nginx/sites-enabled/rd-cdn-*; do
  [ -e "$f" ] || continue
  echo "--- $(basename "$f")"
  grep -E 'server_name|proxy_pass|location ' "$f" | sed 's/^ */  /'
done
say "Сертификаты"
certbot certificates 2>/dev/null | grep -E 'Certificate Name|Domains|Expiry' || echo "certbot не установлен"
say "Слушают локально"
ss -ltn | awk 'NR==1 || /127.0.0.1:/'
"""


NODE_COMPOSE = """services:
  remnanode:
    container_name: remnanode
    hostname: remnanode
    image: remnawave/node:latest
    restart: always
    network_mode: host
    env_file:
      - .env
    logging:
      driver: json-file
      options:
        max-size: "30m"
        max-file: "5"
"""

SYSCTL = """# RemnaDeck: тюнинг под ноду Xray
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1
net.ipv4.ip_local_port_range = 10240 65535
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
fs.file-max = 1000000
"""


def install_node(node_path: str, port: int, secret: str, tune: bool = False,
                 force: bool = False) -> str:
    """Чистая установка ноды: docker, каталог, .env с ключом панели, запуск."""
    tune_block = f"""
say "Тюнинг сети (BBR, буферы, лимиты)"
cat > /etc/sysctl.d/99-remnadeck.conf <<'SYSCTL'
{SYSCTL}
SYSCTL
sysctl --system >/dev/null 2>&1 || true
sysctl -n net.ipv4.tcp_congestion_control | sed 's/^/  congestion control: /'
grep -q 'remnadeck' /etc/security/limits.conf 2>/dev/null || cat >> /etc/security/limits.conf <<'LIM'
# remnadeck
* soft nofile 1000000
* hard nofile 1000000
LIM
""" if tune else ""
    return f"""{HEAD}{APT}{COMPOSE}
NODE_PATH={q(node_path)}
PORT={q(port)}
COMPOSE_FILE="$NODE_PATH/docker-compose.yml"

if [ -f "$COMPOSE_FILE" ] && [ {"1" if force else "0"} != "1" ]; then
  echo "В $NODE_PATH уже есть docker-compose.yml — сервер не пустой."
  echo "Если это старая нода и её нужно перезаписать, выбери «Переустановить»."
  exit 1
fi

say "Система"
. /etc/os-release 2>/dev/null; echo "${{PRETTY_NAME:-unknown}} · $(uname -m)"
apt_install curl ca-certificates

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  say "Docker уже стоит"; docker --version
else
  say "Ставлю Docker"
  curl -fsSL https://get.docker.com -o /tmp/rd-docker.sh
  sh /tmp/rd-docker.sh >/dev/null
  rm -f /tmp/rd-docker.sh
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker --version
  docker compose version >/dev/null 2>&1 || {{ echo "Нет docker compose plugin"; exit 1; }}
fi

say "Кладу ноду в $NODE_PATH"
mkdir -p "$NODE_PATH"
[ -f "$COMPOSE_FILE" ] && cp "$COMPOSE_FILE" "$COMPOSE_FILE.bak.$(date +%s)" || true
cat > "$COMPOSE_FILE" <<'YML'
{NODE_COMPOSE}
YML
cat > "$NODE_PATH/.env" <<'ENVEOF'
### RemnaDeck: ключ панели. Имена переменных разных версий ноды — значение одно.
APP_PORT={port}
NODE_PORT={port}
SSL_CERT={secret}
SECRET_KEY={secret}
ENVEOF
chmod 600 "$NODE_PATH/.env"

say "Фаервол"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  PANEL_IP=$(echo "${{SSH_CLIENT:-}}" | awk '{{print $1}}')
  if [ -n "$PANEL_IP" ]; then
    ufw allow from "$PANEL_IP" to any port "$PORT" proto tcp >/dev/null && \
      echo "  открыл $PORT/tcp для $PANEL_IP (адрес панели)"
  else
    ufw allow "$PORT"/tcp >/dev/null && echo "  открыл $PORT/tcp для всех"
  fi
else
  echo "  ufw не активен — правила не трогаю. Если сервер за облачным фаерволом"
  echo "  (GCP, Hetzner, Oracle), порт $PORT нужно открыть в панели хостера"
fi
{tune_block}
say "Запускаю ноду"
compose pull
compose up -d
sleep 5
compose ps

say "Проверка порта"
ss -ltn 2>/dev/null | grep ":$PORT " && echo "  нода слушает $PORT" || \
  echo "  порт $PORT не слушается — смотри лог ниже"
say "Лог ноды"
compose logs --tail 25 remnanode 2>/dev/null || true
echo
echo "Если панель не увидела ноду за минуту — проверь, что $PORT доступен снаружи"
echo "и что адрес ноды в панели совпадает с IP этого сервера."
"""
