#!/usr/bin/env bash
# Установка / обновление RemnaDeck. Запуск: sudo bash install.sh
set -euo pipefail
cd "$(dirname "$0")"
DIR=$(pwd)

ok() { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
ask() { local p="$1" d="${2:-}" v; read -rp "$p${d:+ [$d]}: " v; echo "${v:-$d}"; }
envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E "s/^[\"'](.*)[\"']$/\1/"; }

command -v docker >/dev/null || { echo "Нужен Docker"; exit 1; }

# docker сам создаёт ПАПКУ .env, если файла не было при первом запуске — чиним
[[ -d .env ]] && rmdir .env 2>/dev/null || true

if [[ ! -s .env ]]; then
  DOMAIN=$(ask "Домен панели (например deck.example.com)")
  [[ -n "$DOMAIN" ]] || { echo "Домен обязателен"; exit 1; }
  PORT=$(ask "Локальный порт" "8090")
  cat > .env <<ENV
# RemnaDeck — остальное панель допишет сама после первого входа
PANEL_DOMAIN=$DOMAIN
PANEL_PORT=$PORT
SECRET_KEY=$(openssl rand -hex 32)
CRYPT_KEY=$(openssl rand -hex 32)
SETUP_TOKEN=$(openssl rand -hex 5)
REMNAWAVE_URL=http://remnawave:3000
REMNAWAVE_INTERNAL=auto
ENV
  ok ".env создан: $DIR/.env"
else
  ok "Найден $DIR/.env — оставляю как есть"
fi
grep -q "^CRYPT_KEY=" .env || {
  echo "CRYPT_KEY=$(openssl rand -hex 32)" >> .env
  ok "Добавлен CRYPT_KEY — им шифруются SSH-доступы к серверам нод"
}
chmod 600 .env
DOMAIN=$(envget PANEL_DOMAIN); PORT=$(envget PANEL_PORT); PORT=${PORT:-8090}

docker network inspect remnawave-network >/dev/null 2>&1 || {
  warn "Сети remnawave-network нет — создаю. Если Remnawave на другом сервере, в мастере укажи https://адрес_панели"
  docker network create remnawave-network >/dev/null
}

mkdir -p data
docker compose up -d --build
ok "Контейнер remnadeck запущен (127.0.0.1:$PORT)"

# --- реверс-прокси (Caddy от Remnawave)
CADDY_CT=$(docker ps --format '{{.Names}}' | grep -i caddy | head -1 || true)
CADDYFILE=""
for f in /opt/remnawave/caddy/Caddyfile /opt/remnawave/Caddyfile /etc/caddy/Caddyfile; do
  [[ -f "$f" ]] && { CADDYFILE="$f"; break; }
done
if [[ -n "$CADDYFILE" ]] && ! grep -qF "$DOMAIN" "$CADDYFILE"; then
  UPSTREAM="127.0.0.1:$PORT"
  [[ -n "$CADDY_CT" ]] && docker inspect "$CADDY_CT" -f '{{json .NetworkSettings.Networks}}' | grep -q remnawave-network \
    && UPSTREAM="remnadeck:8090"
  if [[ "$(ask "Добавить $DOMAIN в $CADDYFILE (upstream $UPSTREAM)? y/n" "y")" == "y" ]]; then
    cp "$CADDYFILE" "$CADDYFILE.bak.$(date +%s)"
    printf '\n%s {\n    encode gzip\n    reverse_proxy %s\n}\n' "$DOMAIN" "$UPSTREAM" >> "$CADDYFILE"
    if [[ -n "$CADDY_CT" ]]; then
      docker exec "$CADDY_CT" caddy reload --config /etc/caddy/Caddyfile && ok "Caddy перечитал конфиг"
    else
      systemctl reload caddy && ok "Caddy перечитал конфиг"
    fi
  fi
elif [[ -z "$CADDYFILE" ]]; then
  warn "Caddyfile не найден — примеры для Caddy и nginx в папке deploy/"
fi

sleep 2
CODE=$(envget SETUP_TOKEN || true)
echo
ok "Панель: https://$DOMAIN   (A-запись домена → IP этого сервера)"
[[ -n "$CODE" ]] && ok "Код первого входа: $CODE"
exit 0
