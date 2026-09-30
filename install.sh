#!/usr/bin/env bash
# Установка и обновление RemnaDeck.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/shashachkaaa/Remnadeck/main/install.sh)
#
# Запущенный так, скрипт сам скачает проект в /opt/remnadeck (другая папка — REMNADECK_DIR=…).
# Повторный запуск той же командой — обновление: код заменяется, .env и data/ не трогаются.
# Из папки проекта: sudo bash install.sh — пересобрать как есть; --update — сначала скачать свежий код.
set -euo pipefail

REPO="${REMNADECK_REPO:-shashachkaaa/Remnadeck}"
BRANCH="${REMNADECK_BRANCH:-main}"

ok() { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
# читаем с терминала, а не со stdin — иначе не работает запуск через curl | bash
ask() { local p="$1" d="${2:-}" v=""; read -rp "$p${d:+ [$d]}: " v </dev/tty || true; echo "${v:-$d}"; }

[[ $EUID -eq 0 ]] || die "Запусти от root (sudo)"

# ---------------- скачать / обновить код: запущены не из папки проекта или с --update
SELF=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)
IN_PROJECT=0; [[ -f "$SELF/docker-compose.yml" && -d "$SELF/app" ]] && IN_PROJECT=1
if [[ $IN_PROJECT -eq 0 || "${1:-}" == "--update" ]]; then
  [[ $IN_PROJECT -eq 1 ]] && DIR="$SELF" || DIR="${REMNADECK_DIR:-/opt/remnadeck}"
  mkdir -p "$DIR"
  if [[ -d "$DIR/.git" ]] && command -v git >/dev/null; then
    ok "Обновляю $DIR из git"
    git -C "$DIR" pull --ff-only || die "git pull не прошёл — в $DIR есть свои правки. Разберись с ними или удали .git"
  else
    [[ -f "$DIR/docker-compose.yml" ]] && ok "Обновляю код в $DIR (.env и data/ остаются)" || ok "Скачиваю RemnaDeck в $DIR"
    command -v curl >/dev/null || die "Нужен curl"
    # качаем конкретный коммит — его же панель покажет как свою версию
    SHA=$(curl -fsSL "https://api.github.com/repos/$REPO/commits/$BRANCH" | grep -m1 '"sha"' | cut -d'"' -f4 || true)
    # в архиве нет .env и data/ — они в .gitignore, так что настройки не перезапишутся
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/${SHA:-refs/heads/$BRANCH}" \
      | tar -xz --strip-components=1 -C "$DIR" || die "Не удалось скачать https://github.com/$REPO"
    [[ -n "$SHA" ]] && echo "$SHA" > "$DIR/.commit"
  fi
  exec bash "$DIR/install.sh"
fi

cd "$SELF"
DIR=$(pwd)
envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E "s/^[\"'](.*)[\"']$/\1/"; }

# ---------------- Docker
if ! command -v docker >/dev/null; then
  [[ "$(ask "Docker не найден. Поставить (официальный скрипт get.docker.com)? y/n" "y")" == "y" ]] || die "Нужен Docker"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Нужен docker compose v2 (плагин docker-compose-plugin)"

# docker сам создаёт ПАПКУ .env, если файла не было при первом запуске — чиним
[[ -d .env ]] && rmdir .env 2>/dev/null || true

FIRST=0
if [[ ! -s .env ]]; then
  FIRST=1
  DOMAIN=$(ask "Домен панели (например deck.example.com)")
  [[ -n "$DOMAIN" ]] || die "Домен обязателен"
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
DOMAIN=$(envget PANEL_DOMAIN); PORT=$(envget PANEL_PORT || true); PORT=${PORT:-8090}

docker network inspect remnawave-network >/dev/null 2>&1 || {
  warn "Сети remnawave-network нет — создаю. Если Remnawave на другом сервере, в мастере укажи https://адрес_панели"
  docker network create remnawave-network >/dev/null
}

# ---------------- сборка. Docker Hub бывает недоступен — тогда базовый образ берём с зеркала
build() {
  docker compose build && return 0
  local base
  base=$(awk '/^FROM/ {print $2; exit}' Dockerfile)
  warn "Сборка не прошла — похоже, недоступен Docker Hub. Беру $base с зеркала"
  for m in mirror.gcr.io/library dockerhub.timeweb.cloud/library; do
    if docker pull -q "$m/$base" >/dev/null 2>&1; then
      docker tag "$m/$base" "$base"
      ok "Базовый образ взят с $m"
      # классический сборщик берёт локальный образ и не лезет в Docker Hub за метаданными
      DOCKER_BUILDKIT=0 docker build -t remnadeck:latest . && return 0
    fi
  done
  die "Не удалось собрать образ: Docker Hub и зеркала недоступны"
}
mkdir -p data

# версия сборки — её показывает панель и сверяет с GitHub
COMMIT=""
if [[ -d .git ]] && command -v git >/dev/null; then COMMIT=$(git rev-parse HEAD 2>/dev/null || true)
elif [[ -f .commit ]]; then COMMIT=$(cat .commit); fi
printf '{"commit": "%s", "built_at": %s, "repo": "%s", "branch": "%s"}\n' "$COMMIT" "$(date +%s)" "$REPO" "$BRANCH" > app/build.json

build
docker compose up -d

# ---------------- обновление по кнопке из панели: панель кладёт data/update.request,
# systemd на хосте запускает deploy/updater.sh. Контейнеру docker.sock не нужен
if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  cat > /etc/systemd/system/remnadeck-update.service <<UNIT
[Unit]
Description=RemnaDeck: обновление по кнопке из панели
[Service]
Type=oneshot
ExecStart=/usr/bin/env bash $DIR/deploy/updater.sh
UNIT
  cat > /etc/systemd/system/remnadeck-update.path <<UNIT
[Unit]
Description=RemnaDeck: ждать запрос на обновление
[Path]
PathExists=$DIR/data/update.request
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload && systemctl enable --now remnadeck-update.path >/dev/null 2>&1 \
    && date +%s > data/updater.installed && ok "Обновление по кнопке в панели включено"

  # Bedolaga (бот и кабинет) по кнопке: так же — панель кладёт data/bedolaga.request, делает deploy/bedolaga.sh
  cat > /etc/systemd/system/remnadeck-bedolaga.service <<UNIT
[Unit]
Description=RemnaDeck: обновление бота и кабинета Bedolaga по кнопке из панели
[Service]
Type=oneshot
TimeoutStartSec=3600
ExecStart=/usr/bin/env bash $DIR/deploy/bedolaga.sh --request
UNIT
  cat > /etc/systemd/system/remnadeck-bedolaga.path <<UNIT
[Unit]
Description=RemnaDeck: ждать запрос по Bedolaga
[Path]
PathExists=$DIR/data/bedolaga.request
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload && systemctl enable --now remnadeck-bedolaga.path >/dev/null 2>&1 \
    && date +%s > data/bedolaga.installed
  # найти бота и кабинет сразу — пункт «Bedolaga» в меню появится, только если они есть
  bash deploy/bedolaga.sh detect >/dev/null 2>&1 && grep -q '"bot":{\|"cabinet":{' data/bedolaga.json \
    && ok "Найден Bedolaga — обновлять бота и кабинет можно из панели (раздел «Bedolaga»)" || true
fi
ok "Контейнер remnadeck запущен (127.0.0.1:$PORT)"

# ---------------- реверс-прокси (Caddy от Remnawave) — только при первой установке
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
elif [[ -z "$CADDYFILE" && $FIRST -eq 1 ]]; then
  warn "Caddyfile не найден — примеры для Caddy и nginx в папке deploy/"
fi

sleep 2
CODE=$(envget SETUP_TOKEN || true)
echo
ok "Панель: https://$DOMAIN   (A-запись домена → IP этого сервера)"
[[ -n "$CODE" ]] && ok "Код первого входа: $CODE"
ok "Обновить потом: bash <(curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh)"
exit 0
