#!/usr/bin/env bash
# Копия бэкапа вне машины — в S3-совместимое хранилище: Cloudflare R2
# или Backblaze B2 (для проверки — MinIO).
#
#   scripts/offsite.sh upload /srv/backups/outreach-donors/2026-09-28-030000
#   scripts/offsite.sh list
#   scripts/offsite.sh fetch 2026-09-28-030000 /srv/backups/outreach-donors-restore
#
# `upload` зовёт backup.sh через свой крючок BACKUP_UPLOAD — руками он нужен
# редко. `list` и `fetch` — для восстановления (deploy/README.md,
# «Восстановление из хранилища»).
#
# Хранилище — переменные BACKUP_S3_* из окружения или из `.env.ops` рядом
# с компоузом (scripts/ops_env.sh):
#   BACKUP_S3_ENDPOINT        https://<id>.r2.cloudflarestorage.com
#                             или https://s3.<регион>.backblazeb2.com
#   BACKUP_S3_BUCKET          бакет, созданный руками
#   BACKUP_S3_ACCESS_KEY_ID   ключ доступа
#   BACKUP_S3_SECRET          его секрет
#   BACKUP_S3_PREFIX          папка в бакете, по умолчанию outreach-donors
#   BACKUP_S3_REGION          по умолчанию — из адреса B2, для R2 — auto
#   BACKUP_S3_PROVIDER        по умолчанию — Cloudflare для R2, Other для B2
#
# **Инструмент — rclone в контейнере с закреплённой версией и хешем.**
# Докер на машине уже есть, ставить ничего не надо; rclone знает и R2,
# и B2 и умеет проверить, что легло (`check`). Хранилище описывается
# переменными RCLONE_CONFIG_OFFSITE_* — файла настроек нет, ключ второй
# раз на диск не ложится. В контейнер ключи едут именами (`-e ИМЯ`),
# а не значениями: аргументы `docker run` видны в `ps` любому на машине.
# Образ закреплён хешем, а не только меткой: метку перевешивают, а этот
# контейнер получает ключ от копий базы.
#
# **«Выгружено» — это проверено, а не «копирование вернуло ноль».** После
# копирования rclone заново читает хранилище и сверяет каждый файл с диском
# по размеру и MD5; расхождение или пропажа — ошибка, и backup.sh
# превращает её в тревогу.
#
# **Старые копии удаляет правило жизненного цикла бакета — 90 дней, как
# требует срок хранения, — а не этот скрипт.** Ключу на сервере тогда
# не нужно право удалять, и тот, кто получил машину, не сотрёт вместе
# с ней и копии.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/ops_env.sh
. "$ROOT/scripts/ops_env.sh"
ops_env_load "$ROOT"

# 1.75.1 — стабильная на 28.09.2026. Обновлять меткой и хешем вместе:
#   docker pull rclone/rclone:<версия> && docker image inspect \
#     --format '{{index .RepoDigests 0}}' rclone/rclone:<версия>
IMAGE="${BACKUP_RCLONE_IMAGE:-rclone/rclone:1.75.1@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5}"
PREFIX="${BACKUP_S3_PREFIX:-outreach-donors}"
PREFIX="${PREFIX%/}"

die() {
  echo "offsite: $1" >&2
  exit 1
}

# configure — описать хранилище для rclone. Зовут подкоманды, а не начало
# скрипта: без аргументов человеку нужна подсказка, а не «не настроено».
configure() {
  local missing="" name
  for name in BACKUP_S3_ENDPOINT BACKUP_S3_BUCKET BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_SECRET; do
    [ -n "${!name:-}" ] || missing="$missing $name"
  done
  if [ -n "$missing" ]; then
    die "хранилище не настроено — не заданы:$missing (в .env.ops рядом с компоузом, шаги — deploy/README.md)"
  fi

  # Поставщик и регион — из адреса: их легко перепутать, а адрес человек
  # копирует из кабинета целиком. Явная переменная сильнее догадки.
  local provider=Other region=auto
  case "$BACKUP_S3_ENDPOINT" in
    *.r2.cloudflarestorage.com*) provider=Cloudflare ;;
    *://s3.*.backblazeb2.com*)
      region="${BACKUP_S3_ENDPOINT#*://s3.}"
      region="${region%%.backblazeb2.com*}"
      ;;
  esac

  export RCLONE_CONFIG_OFFSITE_TYPE=s3
  export RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_S3_PROVIDER:-$provider}"
  export RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_S3_REGION:-$region}"
  export RCLONE_CONFIG_OFFSITE_ENDPOINT="$BACKUP_S3_ENDPOINT"
  export RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID"
  export RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET"
  # Бакет создаёт человек. Проверка «есть ли бакет, не создать ли» требует
  # права на бакеты, которого у ключа на один бакет нет, — и роняла бы
  # каждую выгрузку.
  export RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true
  REMOTE="offsite:$BACKUP_S3_BUCKET/$PREFIX"
}

# rclone_in_docker МОНТИРОВАНИЕ АРГУМЕНТЫ… — rclone в контейнере. Монтирование
# «каталог:путь[:ro]» или пусто; BACKUP_RCLONE_NETWORK — сеть докера для
# проверки с MinIO в соседнем контейнере.
rclone_in_docker() {
  local mount="$1"
  shift
  # RCLONE_CONFIG=/dev/null: файла настроек нет и не будет — всё
  # в переменных; без этого rclone на каждом вызове пишет, что файла нет.
  docker run --rm \
    ${mount:+-v "$mount"} \
    ${BACKUP_RCLONE_NETWORK:+--network "$BACKUP_RCLONE_NETWORK"} \
    -e RCLONE_CONFIG=/dev/null \
    -e RCLONE_CONFIG_OFFSITE_TYPE \
    -e RCLONE_CONFIG_OFFSITE_PROVIDER \
    -e RCLONE_CONFIG_OFFSITE_REGION \
    -e RCLONE_CONFIG_OFFSITE_ENDPOINT \
    -e RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID \
    -e RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY \
    -e RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET \
    "$IMAGE" "$@"
}

# reachable — хранилище отвечает и ключ подходит. Отдельной пробой перед
# работой: при недоступном адресе копирование у rclone 1.75.1 кончается
# словами «is a file not a directory» (живая проверка 28.09.2026), а
# настоящая причина — «no such host», неверная подпись — видна только
# в отладочном выводе. Тревога с чужой причиной хуже тревоги без причины.
# Коды: 0 — ответил, 3 — «каталога нет» (первая выгрузка), прочее — нет.
reachable() {
  local said code=0
  said="$(rclone_in_docker "" lsf "$REMOTE" --max-depth 1 --retries 1 --low-level-retries 2 2>&1 >/dev/null)" || code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne 3 ]; then
    die "хранилище не отвечает или не пускает ($BACKUP_S3_ENDPOINT, бакет $BACKUP_S3_BUCKET): $(printf '%s' "$said" | tail -n 1)"
  fi
}

upload() {
  local source="${1:-}" name
  [ -n "$source" ] || die "укажи каталог копии: scripts/offsite.sh upload <каталог>"
  [ -f "$source/outreach.dump" ] || die "в $source нет outreach.dump — это не каталог бэкапа"
  source="$(cd "$source" && pwd)"
  name="$(basename "$source")"

  echo "Выгружаю $name → $BACKUP_S3_BUCKET/$PREFIX/$name ($BACKUP_S3_ENDPOINT)"
  reachable
  rclone_in_docker "$source:/backup:ro" copy /backup "$REMOTE/$name" --stats-one-line -v ||
    die "копирование в хранилище не прошло — причина строками rclone выше"
  # Отдельный проход по хранилищу: код выхода копирования говорит, что
  # rclone считает сделанным, а не что лежит в бакете.
  rclone_in_docker "$source:/backup:ro" check /backup "$REMOTE/$name" --one-way ||
    die "проверка после выгрузки не сошлась — копия $name в хранилище неполная или испорчена"
  echo "Проверено в хранилище: $name — каждый файл того же размера и хеша, что на диске."
}

list() {
  echo "Копии в $BACKUP_S3_BUCKET/$PREFIX (размер дампа, время выгрузки, имя):"
  rclone_in_docker "" lsl "$REMOTE" --include "*/outreach.dump"
}

fetch() {
  local name="${1:-}" into="${2:-}"
  if [ -z "$name" ] || [ -z "$into" ]; then
    die "укажи копию и каталог: scripts/offsite.sh fetch <имя из list> <куда>"
  fi
  case "$name" in
    */* | .*) die "имя копии — как в list, без пути: $name" ;;
  esac
  mkdir -p "$into"
  into="$(cd "$into" && pwd)"
  # Существующую копию не затираем: рядом может лежать та, с которой
  # как раз сравнивают.
  [ ! -e "$into/$name" ] || die "$into/$name уже есть — выбери другой каталог или убери её"

  reachable
  rm -rf "$into/$name.partial"
  rclone_in_docker "$into:/restore" copy "$REMOTE/$name" "/restore/$name.partial" --stats-one-line -v ||
    die "скачать $name не вышло — причина строками rclone выше; какие копии есть — scripts/offsite.sh list"
  rclone_in_docker "$into:/restore" check "$REMOTE/$name" "/restore/$name.partial" --one-way ||
    die "скачанное не сошлось с хранилищем — $into/$name.partial не восстанавливать"
  [ -f "$into/$name.partial/outreach.dump" ] || die "в копии $name нет outreach.dump"
  mv "$into/$name.partial" "$into/$name"

  echo "Скачано и сверено: $into/$name ($(du -sh "$into/$name" | cut -f1)), схема $(cat "$into/$name/alembic_version.txt" 2>/dev/null || echo "неизвестна")"
  echo "Дальше: scripts/restore.sh $into/$name   — покажет, что сделает; с --yes — выполнит"
}

case "${1:-}" in
  upload) configure && upload "${2:-}" ;;
  list) configure && list ;;
  fetch) configure && fetch "${2:-}" "${3:-}" ;;
  *)
    echo "scripts/offsite.sh upload <каталог копии> | list | fetch <имя> <куда>" >&2
    exit 2
    ;;
esac
