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
# **Сначала — копия нынешней базы** (аудит 19.11.2026). Восстановление
# затирает базу целиком, и без копии «вернуть как было» нечем. Дамп снимается
# до остановки пишущих — снимок pg_dump согласован и под работой — в свой
# каталог `BEFORE_RESTORE_DIR`, а не в каталог бэкапов: тот ротируется по
# `KEEP`, и ротация снесла бы самые старые копии — бывает, ту самую, из которой
# восстанавливают. Копия не снялась — восстановления нет, всё как было. База
# битая настолько, что дамп не снимается, — `--without-current-copy`, осознанно.
#
# **Потом останавливаются те, кто пишет.** Восстановление под живым
# воркером и процессом добивок даёт базу, в которой половина строк
# из бэкапа, половина — из работы, которая шла всё это время. Хуже
# того: добивки в этот момент могут уйти донорам по сроку, который
# в восстановленной базе уже погашен.
#
# **Дамп разворачивается одной транзакцией** (`--single-transaction`):
# pg_restore, упавший на середине, откатывает всё, и база остаётся прежней,
# а не полуочищенной. Остановленные при любом отказе так и стоят — скрипт
# говорит, на каком шаге оборвалось и как поднять их обратно.
set -euo pipefail
# Копия нынешней базы — та же база контактов и переписки: читает её только
# владелец (как и копии scripts/backup.sh).
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE:-docker compose}"
# Кто пишет в базу. Порядок остановки не важен, важно, что никого
# не забыли: забытый процесс тихо допишет своё в середину восстановления.
WRITERS="${WRITERS:-api worker worker-quick worker-contacts worker-sales worker-sales-bulk crawler reaper followups}"
# Вне каталога бэкапов и вне его ротации; на сервере —
# BEFORE_RESTORE_DIR=/srv/backups/outreach-donors-before-restore (deploy/README.md).
BEFORE_RESTORE_DIR="${BEFORE_RESTORE_DIR:-$ROOT/backups-before-restore}"
SOURCE="${1:-}"
CONFIRM=""
WITHOUT_COPY=""
for arg in "${@:2}"; do
  case "$arg" in
    --yes) CONFIRM="--yes" ;;
    --without-current-copy) WITHOUT_COPY=1 ;;
    *)
      echo "непонятный аргумент «${arg}»: scripts/restore.sh <каталог> [--yes] [--without-current-copy]" >&2
      exit 2
      ;;
  esac
done

if [ -z "$SOURCE" ]; then
  echo "укажи каталог бэкапа: scripts/restore.sh backups/<дата> [--yes]" >&2
  exit 2
fi
if [ ! -f "$SOURCE/outreach.dump" ]; then
  echo "в $SOURCE нет outreach.dump — это не каталог бэкапа" >&2
  exit 2
fi
# Путь — от места вызова: ниже скрипт уходит в корень, и относительный путь
# указал бы уже на другой каталог.
SOURCE="$(cd "$SOURCE" && pwd)"

cd "$ROOT"

WAS="$(cat "$SOURCE/alembic_version.txt" 2>/dev/null || echo "неизвестна")"
BEFORE="$BEFORE_RESTORE_DIR/$(date +%Y-%m-%d-%H%M%S)"

if [ "$CONFIRM" != "--yes" ]; then
  echo "Будет выполнено (сейчас — ничего):"
  if [ -n "$WITHOUT_COPY" ]; then
    echo "  0. копия нынешней базы НЕ снимается (--without-current-copy)"
  else
    echo "  0. снята копия нынешней базы: $BEFORE"
  fi
  echo "  1. остановлены: $WRITERS"
  echo "  2. база outreach ОЧИЩЕНА и восстановлена из $SOURCE/outreach.dump"
  echo "     одной транзакцией (схема копии: $WAS)"
  echo "  3. применены миграции, которых в копии ещё не было"
  echo "  4. остановленные запущены обратно"
  echo "Повтори с --yes, если это то, что нужно."
  exit 0
fi

# Шаг, на котором оборвалось, — для слов при отказе. Пусто — сервисы не тронуты.
STEP=""
finish() {
  local code=$?
  rm -rf "$BEFORE.partial"
  if [ "$code" -eq 0 ] || [ -z "$STEP" ]; then
    return 0
  fi
  echo "⚠ Восстановление оборвалось на шаге «${STEP}» (код $code); остановлены: $WRITERS" >&2
  case "$STEP" in
    восстановление) echo "  pg_restore шёл одной транзакцией — база осталась прежней." >&2 ;;
    миграции)
      echo "  База уже из копии, схема — копии ($WAS): сначала догнать миграции" >&2
      echo "  ($COMPOSE run --rm migrate), потом поднимать." >&2
      ;;
  esac
  if [ -d "$BEFORE" ]; then
    echo "  Копия базы до восстановления: $BEFORE" >&2
  fi
  echo "  Поднять остановленных: $COMPOSE start $WRITERS" >&2
}
trap finish EXIT

if [ -z "$WITHOUT_COPY" ]; then
  # Не затираем и не вкладываем: `mv` в существующий каталог кладёт копию внутрь
  # него (тот же урок у scripts/backup.sh) — второй запуск в ту же секунду.
  if [ -e "$BEFORE" ]; then
    echo "копия $BEFORE уже есть — восстановление не начато; повтори через секунду" >&2
    exit 1
  fi
  # `</dev/null` у `exec`: `exec -T` пересылает в контейнер stdin скрипта, и pg_dump
  # проглотил бы остаток вызывающего (`ssh … bash -s < выкатка.sh`, scripts/backup.sh).
  mkdir -p "$BEFORE.partial"
  if ! $COMPOSE exec -T postgres pg_dump -U outreach -d outreach -Fc </dev/null >"$BEFORE.partial/outreach.dump"; then
    echo "копия нынешней базы не снялась — восстановление не начато, база и сервисы как были." >&2
    echo "  База битая и дамп не снимается — повтори с --yes --without-current-copy" >&2
    exit 1
  fi
  if [ "$(head -c 5 "$BEFORE.partial/outreach.dump")" != "PGDMP" ]; then
    echo "копия нынешней базы — не архив pg_dump (нет подписи PGDMP): восстановление не начато" >&2
    exit 1
  fi
  $COMPOSE exec -T postgres psql -U outreach -d outreach -tAc \
    'select version_num from alembic_version' </dev/null >"$BEFORE.partial/alembic_version.txt" ||
    echo "версия схемы нынешней базы не прочитана — копия всё равно снята" >&2
  mv "$BEFORE.partial" "$BEFORE"
  echo "Копия нынешней базы: $BEFORE (вернуть как было: scripts/restore.sh $BEFORE --yes)"
fi

STEP="остановка"
# shellcheck disable=SC2086 — список сервисов должен разбиться на слова.
$COMPOSE stop $WRITERS

STEP="восстановление"
# `--clean --if-exists` вместо пересоздания базы: пересоздание требует
# прав, которых у пользователя приложения может не быть, и рвёт чужие
# подключения.
$COMPOSE exec -T postgres pg_restore -U outreach -d outreach --clean --if-exists \
  --single-transaction < "$SOURCE/outreach.dump"

STEP="миграции"
# Копия могла быть снята на старой схеме, а код уехал вперёд. Миграции
# после восстановления — не перестраховка: без них сервис поднимется
# и упадёт на первом же запросе к новой колонке.
$COMPOSE run --rm migrate

STEP="запуск"
# shellcheck disable=SC2086
$COMPOSE start $WRITERS
STEP=""

NOW="$($COMPOSE exec -T postgres psql -U outreach -d outreach -tAc \
  'select version_num from alembic_version' | tr -d '[:space:]')"
DONORS="$($COMPOSE exec -T postgres psql -U outreach -d outreach -tAc \
  'select count(*) from domains' | tr -d '[:space:]')"

echo "Восстановлено из $SOURCE: схема была $WAS, стала $NOW, доменов в базе $DONORS"
