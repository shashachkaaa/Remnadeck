#!/usr/bin/env bash
# Bedolaga (бот и кабинет) из панели RemnaDeck: найти на сервере, обновить, откатить кабинет.
#
# Панель живёт в контейнере и докером не управляет, поэтому работа идёт здесь, на хосте:
# панель кладёт data/bedolaga.request, systemd (remnadeck-bedolaga.path, ставит install.sh)
# запускает этот скрипт с --request. Из запроса берётся только выбор из фиксированного списка
# действий, коммит (40 hex) и галочка «сохранить кастом» — само содержимое не исполняется.
# Что нашли — data/bedolaga.json, ход — data/bedolaga.log, итог — data/bedolaga.state.
#
# Из консоли:
#   bash deploy/bedolaga.sh detect                 найти бота и кабинет
#   bash deploy/bedolaga.sh bot                    бот → последний релиз
#   bash deploy/bedolaga.sh cabinet [--no-custom]  кабинет → последний релиз (--no-custom — чистый, без своих правок)
#   bash deploy/bedolaga.sh custom                 наложить свой слой на кабинет заново (после правки custom.css)
#   bash deploy/bedolaga.sh rollback               вернуть кабинет из последней резервной копии
set -uo pipefail
export LC_ALL=C   # sort и comm должны сравнивать одинаково

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA="$DIR/data"
BACKUPS="$DATA/backups"
INFO="$DATA/bedolaga.json" STATE="$DATA/bedolaga.state" LOG="$DATA/bedolaga.log" REQ="$DATA/bedolaga.request"
CAB_MANIFEST="$DATA/cabinet.manifest"   # sha256 файлов поставленного релиза: по нему видно, что вы правили сами
CAB_RELEASE="$DATA/cabinet.release"     # какой релиз стоит (коммит, версия, когда)
CAB_IMAGE="ghcr.io/bedolaga-dev/bedolaga-cabinet"
BOT_REPO="BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot"
CAB_REPO="BEDOLAGA-DEV/bedolaga-cabinet"
MARK="rd-custom"                         # метка наших строк в index.html кабинета
ROOT_HOME="${HOME:-/root}"               # под systemd HOME может быть не задан
KEEP_BACKUPS=3

mkdir -p "$BACKUPS" && chmod 700 "$BACKUPS"

ok() { printf '\033[32m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
# тяжёлое (сборка, распаковка) — с низким приоритетом: на сервере живые подписки
low() { nice -n 10 ionice -c3 "$@"; }
js() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\t'/ }; s=${s//$'\r'/}; printf '"%s"' "$s"; }
js_list() { local out="" x; while IFS= read -r x; do [ -n "$x" ] && out+="${out:+,}$(js "$x")"; done; printf '[%s]' "$out"; }
kv() { grep -m1 "^$1=" "$2" 2>/dev/null | cut -d= -f2-; }
prune() { ls -1t "$BACKUPS"/$1 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r f; do rm -f "$f" "${f%.tar.gz}".{manifest,release}; done; }

# docker pull с повторами: ghcr.io и Docker Hub с серверов в РФ отвечают через раз
pull() { local i; for i in 1 2 3 4 5; do docker pull -q "$1" >/dev/null 2>&1 && return 0; [ "$i" = 5 ] || { echo "  $1 не скачался, попытка $((i + 1))…"; sleep 8; }; done; return 1; }
# образ есть локально — или скачать; для Docker Hub — ещё и с зеркал
ensure_image() {
  local img=$1 m src
  docker image inspect "$img" >/dev/null 2>&1 && return 0
  pull "$img" && return 0
  case "${img%%/*}" in *.*|*:*) return 1 ;; esac   # не Docker Hub — зеркал нет
  [[ "$img" == */* ]] && src="$img" || src="library/$img"
  for m in mirror.gcr.io dockerhub.timeweb.cloud; do
    if pull "$m/$src"; then docker tag "$m/$src" "$img" && ok "  $img взят с $m"; return 0; fi
  done
  return 1
}
gh_latest() {   # "tag sha" последнего релиза — для запуска из консоли без панели
  local j tag sha
  j=$(curl -fsSL -m 15 "https://api.github.com/repos/$1/releases/latest" 2>/dev/null) || return 1
  tag=$(printf '%s' "$j" | grep -m1 '"tag_name"' | cut -d'"' -f4)
  sha=$(printf '%s' "$j" | grep -m1 '"target_commitish"' | cut -d'"' -f4)
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || sha=$(curl -fsSL -m 15 "https://api.github.com/repos/$1/commits/$tag" 2>/dev/null | grep -m1 '"sha"' | cut -d'"' -f4)
  [[ -n "$tag" && "$sha" =~ ^[0-9a-f]{40}$ ]] && echo "$tag $sha"
}

# ======================================================================== поиск
compose_file() { local f; for f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do [ -f "$1/$f" ] && { echo "$1/$f"; return 0; }; done; return 1; }

find_bot() {
  BOT_DIR=""
  local d
  [ -n "${BEDOLAGA_BOT_DIR:-}" ] && [ -d "$BEDOLAGA_BOT_DIR" ] && { BOT_DIR=$BEDOLAGA_BOT_DIR; return 0; }   # указан явно
  while IFS= read -r d; do
    [ -n "$d" ] && [ -d "$d" ] && compose_file "$d" >/dev/null || continue
    if grep -qs "remnawave-bedolaga-telegram-bot" "$d/pyproject.toml" \
       || git -C "$d" remote get-url origin 2>/dev/null | grep -qi "bedolaga-telegram-bot" \
       || grep -qsiE "image:.*bedolaga.*bot" "$(compose_file "$d")"; then
      BOT_DIR=$d; return 0
    fi
  done < <({ docker ps -a --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null
             printf '%s\n' "$ROOT_HOME/remnawave-bedolaga-telegram-bot" /root/remnawave-bedolaga-telegram-bot \
               /opt/remnawave-bedolaga-telegram-bot /opt/bedolaga-bot; } | awk 'NF && !seen[$0]++')
  return 1
}
bot_ct() { # контейнер бота: сервис bot этого compose-проекта
  local c
  c=$(docker ps -a --filter "label=com.docker.compose.project.working_dir=$BOT_DIR" --filter label=com.docker.compose.service=bot --format '{{.Names}}' | head -1)
  echo "${c:-remnawave_bot}"
}
bot_mode() { [ -d "$BOT_DIR/.git" ] && grep -qsE '^\s+build:' "$(compose_file "$BOT_DIR")" && echo git || echo image; }
bot_version() {
  local v
  v=$(grep -sE '"\."\s*:' "$BOT_DIR/.release-please-manifest.json" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^"]*' | head -1)
  [ -n "$v" ] || v=$(grep -m1 -E '^version\s*=' "$BOT_DIR/pyproject.toml" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^"'"'"']*')
  echo "$v"
}

is_cabinet_dir() { [ -f "$1/index.html" ] && [ -d "$1/assets" ] && grep -qs "/api/cabinet/" "$1/index.html"; }
# версия сборки: кабинет сравнивает свою версию с tag_name релизов — берём строку из того же файла
cab_js_version() {
  grep -lsF tag_name "$1"/assets/*.js | xargs -r grep -ohE '[`"][0-9]+\.[0-9]+\.[0-9]+[`"]' | tr -d '`"' \
    | sort | uniq -c | sort -rn | awk 'NR == 1 {print $2}'
}
find_cabinet() {
  CAB_MODE="" CAB_DIST="" CAB_CT=""
  local d
  # 0) папка указана явно (нестандартное место)
  [ -n "${CABINET_DIST:-}" ] && is_cabinet_dir "$CABINET_DIST" && { CAB_MODE=static; CAB_DIST=$CABINET_DIST; return 0; }
  # 1) кабинет в своём контейнере (так в инструкции Bedolaga)
  CAB_CT=$(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /bedolaga-cabinet/ && $1 != "tmp_cabinet" {print $1; exit}')
  [ -n "$CAB_CT" ] && { CAB_MODE=container; return 0; }
  # 2) собранные файлы, которые отдаёт Caddy/nginx: ищем среди примонтированных в контейнеры папок
  while IFS= read -r d; do
    is_cabinet_dir "$d" && { CAB_MODE=static; CAB_DIST=$d; return 0; }
  done < <({ docker ps -q | xargs -r docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}{{"\n"}}{{end}}{{end}}' 2>/dev/null
             [ -n "$BOT_DIR" ] && echo "$BOT_DIR/cabinet-dist"
             printf '%s\n' /var/www/cabinet /var/www/bedolaga-cabinet /opt/bedolaga-cabinet/dist; } | awk 'NF && !seen[$0]++')
  return 1
}
custom_src() { # папка вашего слоя правок кабинета (исходник; в кабинет она копируется как custom/)
  local d
  for d in "${CABINET_CUSTOM:-}" "$ROOT_HOME/cabinet-custom" /root/cabinet-custom "$(dirname "$CAB_DIST")/cabinet-custom"; do
    [ -n "$d" ] && [ -d "$d" ] && { echo "$d"; return 0; }
  done
  return 1
}
# файлы кабинета, которые вы поменяли (хеш не совпадает с поставленным релизом) и добавили сами
cab_modified() { [ -f "$CAB_MANIFEST" ] && (cd "$CAB_DIST" && sha256sum -c --quiet "$CAB_MANIFEST" 2>/dev/null | sed -n 's/^\.\/\(.*\): FAILED.*/\1/p'); }
cab_extra() {
  [ -f "$CAB_MANIFEST" ] || return 0
  comm -23 <(cd "$CAB_DIST" && find . -type f ! -name index.html ! -path './custom/*' ! -name '.index.html.*' | sort) \
           <(awk '{print $2}' "$CAB_MANIFEST" | sort) | sed 's#^\./##'
}

backups_json() {  # $1 — маска файлов
  local out="" f base
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    base=${f%.tar.gz}; base=${base%.sql.gz}
    out+="${out:+,}{\"file\":$(js "$(basename "$f")"),\"size\":$(stat -c %s "$f"),\"ts\":$(stat -c %Y "$f"),\"version\":$(js "$(kv version "$base.release")")}"
  done < <(ls -1t "$BACKUPS"/$1 2>/dev/null)
  printf '[%s]' "$out"
}

detect() {
  local bot="null" cab="null" ct st
  if find_bot; then
    ct=$(bot_ct)
    st=$(docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.StartedAt}}' "$ct" 2>/dev/null || echo "missing||")
    bot="{\"dir\":$(js "$BOT_DIR"),\"mode\":$(js "$(bot_mode)"),\"version\":$(js "$(bot_version)"),
      \"commit\":$(js "$(git -C "$BOT_DIR" rev-parse HEAD 2>/dev/null)"),\"branch\":$(js "$(git -C "$BOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"),
      \"dirty\":$(git -C "$BOT_DIR" status --porcelain --untracked-files=no 2>/dev/null | cut -c4- | head -30 | js_list),
      \"container\":$(js "$ct"),\"status\":$(js "${st%%|*}"),\"health\":$(js "$(cut -d'|' -f2 <<<"$st")"),\"started_at\":$(js "${st##*|}"),
      \"backups\":$(backups_json 'bot-db-*.sql.gz')}"
  fi
  if find_cabinet; then
    if [ "$CAB_MODE" = container ]; then
      local img
      img=$(docker inspect -f '{{.Image}}' "$CAB_CT")
      cab="{\"mode\":\"container\",\"container\":$(js "$CAB_CT"),\"image\":$(js "$(docker inspect -f '{{.Config.Image}}' "$CAB_CT")"),
        \"dir\":$(js "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CAB_CT")"),
        \"revision\":$(js "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$img")"),
        \"built_at\":$(js "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.created"}}' "$img")"),
        \"status\":$(js "$(docker inspect -f '{{.State.Status}}' "$CAB_CT")"),\"backups\":[]}"
    else
      local src="" ver rev
      src=$(custom_src || true)
      rev=$(kv revision "$CAB_RELEASE"); ver=$(kv version "$CAB_RELEASE")
      # поставлено не нами — версию угадываем по сборке (строка версии чаще всего встречается в js)
      [ -z "$ver" ] && ver=$(cab_js_version "$CAB_DIST")
      cab="{\"mode\":\"static\",\"dist\":$(js "$CAB_DIST"),\"revision\":$(js "$rev"),\"version\":$(js "$ver"),
        \"installed_at\":$(js "$(kv installed_at "$CAB_RELEASE")"),\"manifest\":$([ -f "$CAB_MANIFEST" ] && echo true || echo false),
        \"custom\":{\"source\":$(js "$src"),\"served\":$([ -d "$CAB_DIST/custom" ] && echo true || echo false),
          \"injected\":$(grep -qs "data-$MARK" "$CAB_DIST/index.html" && echo true || echo false),
          \"files\":$( { [ -n "$src" ] && ls -1 "$src" || ls -1 "$CAB_DIST/custom" 2>/dev/null; } | head -30 | js_list)},
        \"modified\":$(cab_modified | head -30 | js_list),\"extra\":$(cab_extra | head -30 | js_list),
        \"backups\":$(backups_json 'cabinet-*.tar.gz')}"
    fi
  fi
  printf '{"detected_at":%s,"bot":%s,"cabinet":%s}\n' "$(date +%s)" "$bot" "$cab" | tr '\n' ' ' > "$INFO.new" && mv -f "$INFO.new" "$INFO"
}

# ======================================================================== бот
bot_db_backup() {
  local ct user db f
  ct=$(docker ps --filter "label=com.docker.compose.project.working_dir=$BOT_DIR" --filter label=com.docker.compose.service=postgres --format '{{.Names}}' | head -1)
  [ -n "$ct" ] || { warn "PostgreSQL бота не запущен — копия базы не сделана"; return 0; }
  user=$(docker exec "$ct" printenv POSTGRES_USER); db=$(docker exec "$ct" printenv POSTGRES_DB)
  f="$BACKUPS/bot-db-$(date +%Y%m%d-%H%M%S).sql.gz"
  echo "Копия базы бота → $f"
  (umask 077; docker exec "$ct" pg_dump -U "${user:-postgres}" -d "${db:-${user:-postgres}}" | gzip > "$f") \
    || { rm -f "$f"; die "Не удалось сделать копию базы — обновление остановлено, бот не тронут"; }
  ok "  готово, $(du -h "$f" | cut -f1)"
  prune 'bot-db-*.sql.gz'
}

bot_wait() {
  local ct i s
  ct=$(bot_ct)
  echo "Жду, пока бот поднимется ($ct)…"
  for i in $(seq 1 48); do
    s=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$ct" 2>/dev/null)
    case "$s" in
      running/healthy|running/none) ok "Бот работает ($s)"; return 0 ;;
      exited/*|dead/*) break ;;
    esac
    sleep 5
  done
  warn "Бот не стал healthy за 4 минуты (состояние: ${s:-нет контейнера}). Последние строки лога:"
  docker logs --tail 30 "$ct" 2>&1
  return 1
}

do_bot() {
  local sha=${1:-} old changed mine clash from to cf
  find_bot || die "Бот Bedolaga на этом сервере не найден"
  cd "$BOT_DIR" || die "Нет папки $BOT_DIR"
  cf=$(compose_file "$BOT_DIR")
  echo "Бот: $BOT_DIR (версия $(bot_version), $(bot_mode))"

  if [ "$(bot_mode)" = image ]; then   # бот из готового образа — просто скачать свежий
    bot_db_backup
    local i; for i in 1 2 3 4 5; do docker compose pull -q && break; [ "$i" = 5 ] && die "Не удалось скачать образы"; sleep 8; done
    docker compose up -d || die "docker compose up не прошёл"
    bot_wait; return
  fi

  if [ -z "$sha" ]; then
    read -r _ sha < <(gh_latest "$BOT_REPO") || die "GitHub недоступен — не знаю, какой релиз последний"
  fi
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "Неверный коммит: $sha"
  old=$(git rev-parse HEAD)
  echo "Сейчас: ${old:0:8}, нужно: ${sha:0:8}"
  local i; for i in 1 2 3; do git fetch --quiet --tags origin && break; [ "$i" = 3 ] && die "git fetch не прошёл — GitHub недоступен"; sleep 5; done
  git cat-file -e "$sha^{commit}" 2>/dev/null || die "Коммита ${sha:0:8} нет в репозитории — ветка другая?"
  if [ "$old" = "$sha" ]; then
    ok "Код уже этой версии"
  else
    git merge-base --is-ancestor HEAD "$sha" \
      || die "Нельзя перемотать ${old:0:8} → ${sha:0:8}: в $BOT_DIR свои коммиты или другая ветка. Обновите вручную (git pull)"
    # ваши правки в файлах репозитория сохраняются, если новая версия их не трогает; если трогает — стоп до изменений
    changed=$(git diff --name-only HEAD "$sha"); mine=$(git diff --name-only HEAD)
    clash=$(comm -12 <(sort <<<"$changed") <(sort <<<"$mine") | grep . || true)
    [ -z "$clash" ] || die "Вы правили файлы, которые меняет обновление — ничего не тронуто:
$clash
Уберите правки (например, в docker-compose.override.yml) или обновите вручную."
    # новая мажорная версия PostgreSQL требует переноса базы — это только руками, по инструкции релиза
    from=$(git show "HEAD:${cf#$BOT_DIR/}" 2>/dev/null | grep -oE 'image:\s*postgres:[0-9]+' | grep -oE '[0-9]+$' | head -1)
    to=$(git show "$sha:${cf#$BOT_DIR/}" 2>/dev/null | grep -oE 'image:\s*postgres:[0-9]+' | grep -oE '[0-9]+$' | head -1)
    [ "$from" = "$to" ] || die "В новой версии PostgreSQL $from → $to: базу нужно переносить по инструкции релиза. Ничего не тронуто — обновите вручную."
    bot_db_backup
    git merge --ff-only --quiet "$sha" || die "git merge не прошёл"
    ok "Код обновлён до ${sha:0:8} ($(bot_version))"
  fi

  # базовые образы — заранее и с зеркал: Docker Hub часто недоступен
  local stages img
  stages=$(awk 'toupper($1)=="FROM" && toupper($(NF-1))=="AS" {print $NF}' Dockerfile 2>/dev/null)
  for img in $(awk 'toupper($1)=="FROM" {for (i=2;i<=NF;i++) if ($i !~ /^--/) {print $i; break}}' Dockerfile 2>/dev/null | sort -u); do
    grep -qxF "$img" <<<"$stages" && continue
    ensure_image "$img" || warn "Не удалось скачать $img — попробую собрать как есть"
  done
  echo "Собираю образ бота (низкий приоритет — подписки не страдают)…"
  if ! low docker compose build 2>&1; then
    warn "Сборка не прошла — возвращаю код на ${old:0:8}, работающий бот не тронут"
    [ "$old" = "$sha" ] || git reset --quiet --keep "$old"
    die "Обновление бота не удалось"
  fi
  for img in $(docker compose config --images 2>/dev/null); do ensure_image "$img" >/dev/null 2>&1 || true; done
  docker compose up -d 2>&1 || die "docker compose up не прошёл"
  bot_wait
}

# ======================================================================== кабинет
# index.html → то же, но с нашими строками перед </head> (custom.css, custom.js — если есть в слое)
inject() {
  local src=$1 dst=$2 css="" jsl="" v
  if [ -f "$CAB_DIST/custom/custom.css" ]; then v=$(sha256sum "$CAB_DIST/custom/custom.css" | cut -c1-10); css="  <link rel=\"stylesheet\" href=\"/custom/custom.css?v=$v\" data-$MARK>"; fi
  if [ -f "$CAB_DIST/custom/custom.js" ]; then v=$(sha256sum "$CAB_DIST/custom/custom.js" | cut -c1-10); jsl="  <script src=\"/custom/custom.js?v=$v\" defer data-$MARK></script>"; fi
  grep -v "data-$MARK" "$src" | awk -v a="$css" -v b="$jsl" '/<\/head>/ && !d { if (a != "") print a; if (b != "") print b; d = 1 } { print }' > "$dst"
}
sync_custom() {
  local src
  if src=$(custom_src); then
    mkdir -p "$CAB_DIST/custom"
    rsync -a --delete --exclude '.*' "$src/" "$CAB_DIST/custom/"
    echo "Слой правок: $src → custom/ ($(ls "$src" | xargs))"
  elif [ -d "$CAB_DIST/custom" ]; then
    echo "Слой правок: custom/ в кабинете (оставлен как есть)"
  fi
}
# сменить файлы кабинета без «пустого окна»: новые файлы → index.html → убрать лишнее
swap_in() {  # $1 — папка с новой сборкой, $2 — keep (сохранить свои правки)
  local new=$1 keep=$2 prot="" f
  if [ "$keep" = true ]; then
    prot=$(mktemp)
    if [ -f "$CAB_MANIFEST" ]; then
      cab_modified > "$prot"
      [ -s "$prot" ] && { warn "Ваши правки в файлах кабинета — не перезаписываю:"; sed 's/^/  /' "$prot"; }
    fi
    sed 's#^#/#' "$prot" > "$prot.x"
    rsync -a --exclude /index.html --exclude /custom --exclude-from="$prot.x" "$new/" "$CAB_DIST/"
    sync_custom
    inject "$new/index.html" "$CAB_DIST/.index.html.new" && mv -f "$CAB_DIST/.index.html.new" "$CAB_DIST/index.html"
    # лишнее убираем только из того, что ставили мы (старые файлы релиза); ваши файлы остаются.
    # Первый раз списка нет — тогда чистим только assets/ (там одни файлы сборки с хешами в именах)
    if [ -f "$CAB_MANIFEST" ]; then
      comm -23 <(awk '{print $2}' "$CAB_MANIFEST" | sed 's#^\./##' | sort) <(cd "$new" && find . -type f | sed 's#^\./##' | sort) \
        | grep -vxFf "$prot" | while IFS= read -r f; do rm -f "$CAB_DIST/$f"; done
    else
      rsync -a --delete "$new/assets/" "$CAB_DIST/assets/"
    fi
    local extra; extra=$(cd "$new" && cab_extra_vs "$CAB_DIST")
    [ -n "$extra" ] && { echo "Ваши файлы оставлены:"; sed 's/^/  /' <<<"$extra"; }
    rm -f "$prot" "$prot.x"
  else
    rsync -a --exclude /index.html "$new/" "$CAB_DIST/"
    cp "$new/index.html" "$CAB_DIST/.index.html.new" && mv -f "$CAB_DIST/.index.html.new" "$CAB_DIST/index.html"
    rsync -a --delete "$new/" "$CAB_DIST/"
    warn "Поставлен чистый кабинет: custom/ и свои файлы убраны (копия — в резервной)"
  fi
}
cab_extra_vs() { comm -23 <(cd "$1" && find . -type f ! -name index.html ! -path './custom/*' ! -name '.index.html.*' | sort) <(find . -type f | sort) | sed 's#^\./##' | head -30; }

cab_backup() {
  local b="$BACKUPS/cabinet-$(date +%Y%m%d-%H%M%S)"
  low tar -czf "$b.tar.gz" -C "$CAB_DIST" . || die "Не удалось сделать резервную копию кабинета — ничего не тронуто"
  [ -f "$CAB_MANIFEST" ] && cp "$CAB_MANIFEST" "$b.manifest"
  [ -f "$CAB_RELEASE" ] && cp "$CAB_RELEASE" "$b.release"
  ok "Резервная копия: $(basename "$b").tar.gz ($(du -h "$b.tar.gz" | cut -f1))"
  prune 'cabinet-*.tar.gz'
}

do_cabinet() {
  local sha=${1:-} keep=${2:-true} tag="" img rev tmp
  find_bot || true
  find_cabinet || die "Кабинет Bedolaga на этом сервере не найден"

  if [ "$CAB_MODE" = container ]; then   # кабинет в своём контейнере — обновляем образ, который указан в его compose
    local dir svc
    dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CAB_CT")
    svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CAB_CT")
    [ -n "$dir" ] && [ -n "$svc" ] || die "Контейнер $CAB_CT запущен не через docker compose — обновите вручную"
    echo "Кабинет в контейнере $CAB_CT ($dir, сервис $svc) — ставлю образ из его compose"
    [ "$keep" = true ] && echo "Свои правки внутри контейнера не сохраняются — держите их в своём образе или томе"
    cd "$dir" || die "Нет папки $dir"
    local i; for i in 1 2 3 4 5; do docker compose pull -q "$svc" && break; [ "$i" = 5 ] && die "Не удалось скачать образ кабинета"; sleep 8; done
    docker compose up -d "$svc" || die "docker compose up не прошёл"
    ok "Кабинет обновлён: $(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(docker inspect -f '{{.Image}}' "$CAB_CT")" | cut -c1-8)"
    return
  fi

  echo "Кабинет: файлы в $CAB_DIST"
  if [ -z "$sha" ]; then
    read -r tag sha < <(gh_latest "$CAB_REPO") || { warn "GitHub недоступен — беру :latest как есть"; sha=""; }
  fi
  [ -z "$sha" ] || [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "Неверный коммит: $sha"
  # образы кабинета помечены коммитом (sha-xxxxxxx) — ставим ровно выбранный релиз; :latest — запасной путь
  img=""
  if [ -n "$sha" ]; then
    echo "Скачиваю $CAB_IMAGE:sha-${sha:0:7}…"
    if pull "$CAB_IMAGE:sha-${sha:0:7}"; then img="$CAB_IMAGE:sha-${sha:0:7}"
    else
      warn "Образа по коммиту нет — пробую :latest"
      pull "$CAB_IMAGE:latest" || die "ghcr.io не отвечает — кабинет не тронут, попробуйте позже"
      rev=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$CAB_IMAGE:latest")
      [ "$rev" = "$sha" ] || die ":latest собран из ${rev:0:8}, а не из релиза ${sha:0:8} — кабинет не тронут, попробуйте позже"
      img="$CAB_IMAGE:latest"
    fi
  else
    pull "$CAB_IMAGE:latest" || die "ghcr.io не отвечает — кабинет не тронут, попробуйте позже"
    img="$CAB_IMAGE:latest"
  fi
  rev=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$img")
  ok "Образ: $img (коммит ${rev:0:8})"

  tmp=$(mktemp -d); CLEAN+=("$tmp")
  docker rm -f tmp_cabinet >/dev/null 2>&1 || true
  docker create --name tmp_cabinet "$img" >/dev/null || die "docker create не прошёл"
  low docker cp -q tmp_cabinet:/usr/share/nginx/html/. "$tmp/" || die "Не удалось достать файлы из образа"
  is_cabinet_dir "$tmp" || die "В образе нет собранного кабинета — ничего не тронуто"
  local ver
  ver=${tag#v}
  [ -n "$ver" ] || ver=$(cab_js_version "$tmp")

  cab_backup
  swap_in "$tmp" "$keep"
  (cd "$tmp" && find . -type f ! -name index.html -print0 | sort -z | xargs -0 sha256sum) > "$CAB_MANIFEST"
  printf 'revision=%s\nversion=%s\nimage=%s\ninstalled_at=%s\n' "$rev" "$ver" "$img" "$(date +%s)" > "$CAB_RELEASE"
  ok "Кабинет обновлён${ver:+ до $ver}"
}

do_custom() {
  find_bot || true
  find_cabinet && [ "$CAB_MODE" = static ] || die "Кабинет из файлов на этом сервере не найден"
  sync_custom
  inject "$CAB_DIST/index.html" "$CAB_DIST/.index.html.new" && mv -f "$CAB_DIST/.index.html.new" "$CAB_DIST/index.html"
  grep -qs "data-$MARK" "$CAB_DIST/index.html" && ok "Слой наложен" || warn "В слое нет custom.css/custom.js — подключать нечего"
}

do_rollback() {
  local b tmp
  find_bot || true
  find_cabinet && [ "$CAB_MODE" = static ] || die "Кабинет из файлов на этом сервере не найден"
  b=$(ls -1t "$BACKUPS"/cabinet-*.tar.gz 2>/dev/null | head -1)
  [ -n "$b" ] || die "Резервных копий кабинета нет"
  b=${b%.tar.gz}
  echo "Возвращаю кабинет из $(basename "$b").tar.gz $(kv version "$b.release")"
  tmp=$(mktemp -d); CLEAN+=("$tmp")
  low tar -xzf "$b.tar.gz" -C "$tmp" || die "Копия повреждена — ничего не тронуто"
  is_cabinet_dir "$tmp" || die "В копии нет кабинета — ничего не тронуто"
  # копия — ровно то, что было, вместе со слоем и вашими файлами
  rsync -a --exclude /index.html "$tmp/" "$CAB_DIST/"
  cp "$tmp/index.html" "$CAB_DIST/.index.html.new" && mv -f "$CAB_DIST/.index.html.new" "$CAB_DIST/index.html"
  rsync -a --delete "$tmp/" "$CAB_DIST/"
  if [ -f "$b.manifest" ]; then cp "$b.manifest" "$CAB_MANIFEST"; else rm -f "$CAB_MANIFEST"; fi
  if [ -f "$b.release" ]; then cp "$b.release" "$CAB_RELEASE"; else rm -f "$CAB_RELEASE"; fi
  rm -f "$b.tar.gz" "$b.manifest" "$b.release"   # следующий откат — на копию раньше
  ok "Кабинет возвращён"
}

CLEAN=()
cleanup() { rm -rf "${CLEAN[@]}"; docker rm -f tmp_cabinet >/dev/null 2>&1 || true; }
run() {  # $1 действие, $2 коммит, $3 keep
  trap cleanup EXIT   # и при die: временные папки и контейнер-распаковщик не остаются
  case "$1" in cabinet|custom|rollback) command -v rsync >/dev/null || die "Нужен rsync: apt install rsync" ;; esac
  case "$1" in
    detect) detect; ok "Найдено: $(cat "$INFO")" ;;
    bot) do_bot "${2:-}" ;;
    cabinet) do_cabinet "${2:-}" "${3:-true}" ;;
    custom) do_custom ;;
    rollback) do_rollback ;;
    *) die "Неизвестное действие: $1" ;;
  esac
}

exec 9>"$DATA/bedolaga.lock"
flock -w 900 9 || die "Другое действие с Bedolaga ещё идёт"

if [ "${1:-}" = "--request" ]; then
  req=$(head -c 2000 "$REQ" 2>/dev/null || true); rm -f "$REQ"
  field() { grep -oE "\"$1\"[[:space:]]*:[[:space:]]*(\"[^\"]*\"|true|false)" <<<"$req" | head -1 | sed -E 's/^[^:]*:[[:space:]]*"?//; s/"$//'; }
  action=$(field action) sha=$(field sha) keep=$(field keep_custom)
  case "$action" in detect|bot|cabinet|custom|rollback) ;; *) exit 0 ;; esac
  [ -z "$sha" ] || [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 0
  [ "$keep" = false ] || keep=true
  if [ "$action" = detect ]; then detect; exit 0; fi
  echo "running $action $(date +%s)" > "$STATE"
  if (cd /tmp && run "$action" "$sha" "$keep") > "$LOG" 2>&1; then r=done; else r=error; fi
  echo "$r $action $(date +%s)" > "$STATE"
  detect
  exit 0
fi

action=${1:-detect}; keep=true
[ "${2:-}" = "--no-custom" ] && keep=false
run "$action" "" "$keep"; rc=$?
[ "$action" = detect ] || detect
exit $rc
