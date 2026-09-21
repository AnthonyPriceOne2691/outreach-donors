#!/usr/bin/env bash
# Восстановление из бэкапа. Операция необратимая: затирает базу.
#
#   scripts/restore.sh backups/2026-09-21-2030          # покажет, что сделает
#   scripts/restore.sh backups/2026-09-21-2030 --yes    # выполнит
#
# Подтверждение обязательно и вторым аргументом, а не вопросом
# в терминале: восстановление запускают в плохой день и часто по ssh
# из скрипта, где интерактивного ответа никто не даст, — а «Enter
# на всякий случай» нажимают.
#
# **Сначала останавливаются те, кто пишет.** Восстановление под живым
# воркером и процессом добивок даёт базу, в которой половина строк
# из бэкапа, половина — из работы, которая шла всё это время. Хуже
# того: добивки в этот момент могут уйти донорам по сроку, который
# в восстановленной базе уже погашен.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE:-docker compose}"
# Кто пишет в базу. Порядок остановки не важен, важно, что никого
# не забыли: забытый процесс тихо допишет своё в середину восстановления.
WRITERS="${WRITERS:-api worker reaper followups}"
SOURCE="${1:-}"
CONFIRM="${2:-}"

if [ -z "$SOURCE" ]; then
  echo "укажи каталог бэкапа: scripts/restore.sh backups/<дата> [--yes]" >&2
  exit 2
fi
if [ ! -f "$SOURCE/outreach.dump" ]; then
  echo "в $SOURCE нет outreach.dump — это не каталог бэкапа" >&2
  exit 2
fi

cd "$ROOT"

WAS="$(cat "$SOURCE/alembic_version.txt" 2>/dev/null || echo "неизвестна")"

if [ "$CONFIRM" != "--yes" ]; then
  echo "Будет выполнено (сейчас — ничего):"
  echo "  1. остановлены: $WRITERS"
  echo "  2. база outreach ОЧИЩЕНА и восстановлена из $SOURCE/outreach.dump"
  echo "     (схема копии: $WAS)"
  echo "  3. применены миграции, которых в копии ещё не было"
  echo "  4. остановленные запущены обратно"
  echo "Повтори с --yes, если это то, что нужно."
  exit 0
fi

# shellcheck disable=SC2086 — список сервисов должен разбиться на слова.
$COMPOSE stop $WRITERS

# `--clean --if-exists` вместо пересоздания базы: пересоздание требует
# прав, которых у пользователя приложения может не быть, и рвёт чужие
# подключения.
$COMPOSE exec -T postgres pg_restore -U outreach -d outreach --clean --if-exists \
  < "$SOURCE/outreach.dump"

# Копия могла быть снята на старой схеме, а код уехал вперёд. Миграции
# после восстановления — не перестраховка: без них сервис поднимется
# и упадёт на первом же запросе к новой колонке.
$COMPOSE run --rm migrate

# shellcheck disable=SC2086
$COMPOSE start $WRITERS

NOW="$($COMPOSE exec -T postgres psql -U outreach -d outreach -tAc \
  'select version_num from alembic_version' | tr -d '[:space:]')"
DONORS="$($COMPOSE exec -T postgres psql -U outreach -d outreach -tAc \
  'select count(*) from domains' | tr -d '[:space:]')"

echo "Восстановлено из $SOURCE: схема была $WAS, стала $NOW, доменов в базе $DONORS"
