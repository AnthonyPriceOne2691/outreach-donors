#!/usr/bin/env bash
# Сторож здоровья — контейнеры и свежесть бэкапа: крон раз в пять минут,
# тревога по переходу.
#
#   scripts/healthwatch.sh        # из deploy/healthwatch.cron
#
# У каждого долгоживущего контейнера есть проверка здоровья, но докер
# `unhealthy` только помечает: не перезапускает и никому не говорит.
# Вставший воркер выглядит так же, как тихий день: экраны рисуются,
# ошибок нет, прогоны просто не идут. Сторож читает `docker compose ps`
# и шлёт тревогу (`scripts/alert.sh`), когда сервис сломался и когда
# восстановился.
#
# **По переходу, а не каждые пять минут.** Что сломано сейчас, лежит
# в файле состояния; тревога — только о разнице с прошлым проходом.
# Сообщение каждые пять минут приучило бы не читать ни одного. Не ушла
# тревога (сеть, Telegram) — состояние не записывается, и следующий
# проход скажет о той же разнице ещё раз.
#
# Что считается сломанным:
#   • долгоживущий сервис не запущен (exited, dead, restarting, paused,
#     created), без контейнера вовсе или помечен unhealthy;
#   • одноразовый (миграции) завершился не с нулём;
#   • сам docker compose не отвечает — тогда про сервисы не известно
#     ничего, и их прошлое состояние остаётся как было;
#   • свежей копии бэкапа нет дольше HEALTHWATCH_BACKUP_DAYS дней (8 при
#     еженедельном кроне) — бэкап, который не запустился вовсе, о себе
#     сказать не может. Проверяется, если задан BACKUP_DIR.
# `starting` — ни поломка, ни выздоровление: контейнер только поднят,
# и прошлое состояние держится до первого ответа проверки. Иначе каждый
# перезапуск в цикле падений давал бы пару «сломался — восстановился».
#
# Когда сторож смотрел последний раз — время файла состояния: он
# переписывается каждым проходом. В журнал пишется только то, что
# сломано или изменилось.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/ops_env.sh
. "$ROOT/scripts/ops_env.sh"
ops_env_load "$ROOT"
COMPOSE="${COMPOSE:-docker compose}"
STATE="${HEALTHWATCH_STATE:-$ROOT/.healthwatch.state}"
# Сервисы, которым положено завершиться: миграции идут до остальных.
ONESHOT=" ${HEALTHWATCH_ONESHOT:-migrate} "
BACKUP_DAYS="${HEALTHWATCH_BACKUP_DAYS:-8}"
TAB="$(printf '\t')"

cd "$ROOT" || exit 1

# verdict СЕРВИС СОСТОЯНИЕ ЗДОРОВЬЕ КОД — «ok», «wait» или «bad:что с ним».
verdict() {
  local service="$1" state="$2" health="$3" code="$4"
  case "$ONESHOT" in
    *" $service "*)
      if [ -z "$state" ] || [ "$state" = running ] || { [ "$state" = exited ] && [ "$code" = 0 ]; }; then
        echo ok
      else
        echo "bad:$state, код $code — миграции не прошли, остальные сервисы их ждут"
      fi
      return
      ;;
  esac
  case "$state:$health" in
    :*) echo "bad:нет контейнера" ;;
    running:unhealthy) echo "bad:unhealthy — проверка здоровья не проходит" ;;
    running:starting) echo wait ;;
    running:*) echo ok ;;
    exited:* | dead:*) echo "bad:$state, код $code" ;;
    *) echo "bad:$state" ;;
  esac
}

# Прошлое состояние: «сервис<TAB>что с ним» по строке на сломанное.
PREVIOUS=""
[ -f "$STATE" ] && PREVIOUS="$(cat "$STATE")"

# was СЕРВИС — строка прошлого состояния про сервис или пусто.
was() {
  printf '%s\n' "$PREVIOUS" | awk -F "$TAB" -v s="$1" '$1 == s' | head -n 1
}

NOW=""
add() {
  NOW="$NOW$1$TAB$2
"
}

# keep СЕРВИС — про сервис ничего нового не известно: прошлое остаётся.
keep() {
  local line
  line="$(was "$1")"
  [ -z "$line" ] || NOW="$NOW$line
"
}

# stderr компоуза — отдельно от ответа: предупреждение в списке сервисов
# стало бы «сервисом без контейнера» и тревогой на пустом месте.
ERRS="$(mktemp "${TMPDIR:-/tmp}/outreach-healthwatch.XXXXXX")"
trap 'rm -f "$ERRS" "$ERRS.ps"' EXIT
SERVICES="$($COMPOSE config --no-interpolate --services 2>"$ERRS" | grep -E '^[A-Za-z0-9._-]+$')"
# pipefail: код — компоуза, а не grep; пустой список — тоже отказ.
SERVICES_CODE=$?
PS="$($COMPOSE ps --all --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' 2>"$ERRS.ps")"
PS_CODE=$?

if [ "$SERVICES_CODE" -ne 0 ] || [ "$PS_CODE" -ne 0 ]; then
  if [ "$SERVICES_CODE" -ne 0 ]; then WHY="$(cat "$ERRS")"; else WHY="$(cat "$ERRS.ps")"; fi
  add docker "docker compose не отвечает: $(printf '%s' "$WHY" | head -n 1)"
  for service in $(printf '%s\n' "$PREVIOUS" | cut -f1 | grep -vx docker | grep -vx backup); do
    keep "$service"
  done
else
  for service in $SERVICES; do
    # У сервиса бывает несколько копий (обходчик — `CRAWL_WORKERS`): смотрим
    # каждую, сломана одна — сломан сервис. Раньше смотрелась первая строка,
    # и вставшая третья копия выглядела бы здоровой.
    lines="$(printf '%s\n' "$PS" | awk -F '|' -v s="$service" '$1 == s')"
    [ -n "$lines" ] || lines="$service|||"
    worst="" waiting=""
    while IFS='|' read -r _ state health code; do
      result="$(verdict "$service" "${state:-}" "${health:-}" "${code:-}")"
      case "$result" in
        bad:*) [ -n "$worst" ] || worst="${result#bad:}" ;;
        wait) waiting=1 ;;
      esac
    done <<<"$lines"
    if [ -n "$worst" ]; then
      add "$service" "$worst"
    elif [ -n "$waiting" ]; then
      keep "$service"
    fi
  done
fi

if [ -n "${BACKUP_DIR:-}" ]; then
  if [ ! -d "$BACKUP_DIR" ]; then
    add backup "каталога копий нет: $BACKUP_DIR — бэкап не запускался ни разу"
  elif [ -z "$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d ! -name '*.partial' -mtime "-$BACKUP_DAYS" 2>/dev/null | head -n 1)" ]; then
    add backup "свежей копии нет дольше $BACKUP_DAYS дней в $BACKUP_DIR — крон бэкапа не запускается или падает до начала"
  fi
fi

names() {
  printf '%s' "$1" | cut -f1 | grep -v '^$' | sort -u
}
# joined СПИСОК — строки через запятую: «docker, worker».
joined() {
  printf '%s\n' "$1" | grep -v '^$' | paste -sd ',' - | sed 's/,/, /g'
}
BROKE="$(comm -13 <(names "$PREVIOUS") <(names "$NOW"))"
HEALED="$(comm -23 <(names "$PREVIOUS") <(names "$NOW"))"

save() {
  printf '%s' "$NOW" >"$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}

if [ -z "$BROKE" ] && [ -z "$HEALED" ]; then
  save
  if [ -n "$NOW" ]; then
    echo "$(date '+%F %T') по-прежнему сломано: $(joined "$(names "$NOW")")"
  fi
  exit 0
fi

TEXT="сторож здоровья"
if [ -n "$BROKE" ]; then
  TEXT="$TEXT
Сломалось:"
  for service in $BROKE; do
    TEXT="$TEXT
• $service — $(printf '%s' "$NOW" | awk -F "$TAB" -v s="$service" '$1 == s {print $2; exit}')"
  done
fi
if [ -n "$HEALED" ]; then
  TEXT="$TEXT
Восстановилось: $(joined "$HEALED")"
fi
# Подсказка — к тому, что сломалось: команда, которую наберут первой.
if [ -n "$BROKE" ] && printf '%s\n' "$BROKE" | grep -qvx backup; then
  TEXT="$TEXT
Смотреть: docker compose ps; почему unhealthy — docker inspect --format '{{json .State.Health}}' <контейнер>; журнал — docker compose logs --tail 200 <сервис>."
fi
if [ -n "$BROKE" ] && printf '%s\n' "$BROKE" | grep -qx backup; then
  TEXT="$TEXT
Бэкап: журнал крона /var/log/outreach-backup.log и файл /etc/cron.d/outreach-donors-backup."
fi

echo "$(date '+%F %T') $TEXT"
"$ROOT/scripts/alert.sh" "$TEXT"
SENT=$?
# Не настроено (3) — строка уже в журнале, и повторять её каждые пять
# минут незачем. Не ушло (1) — состояние не пишем: следующий проход
# скажет о той же разнице ещё раз.
if [ "$SENT" -eq 0 ] || [ "$SENT" -eq 3 ]; then
  save
  exit 0
fi
echo "$(date '+%F %T') тревога не ушла — состояние не сохранено, следующий проход повторит" >&2
exit 1
