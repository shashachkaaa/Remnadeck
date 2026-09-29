#!/usr/bin/env bash
# Запускается systemd (remnadeck-update.path), когда панель положила data/update.request.
# Скачивает свежий код и пересобирает панель; ход — в data/update.log, итог — в data/update.state.
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$DIR" || exit 1
rm -f data/update.request
echo "running $(date +%s)" > data/update.state
if bash install.sh --update > data/update.log 2>&1; then
  echo "done $(date +%s)" > data/update.state
else
  echo "error $(date +%s)" > data/update.state
fi
