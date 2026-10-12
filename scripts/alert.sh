#!/usr/bin/env bash
# Тревога в Telegram из шелла:
#
#   scripts/alert.sh "бэкап не снят: …"
#   scripts/alert.sh --ok "сторож здоровья
#   Восстановилось: worker"
#
# **Вид.** Цвета у текста в Telegram нет — уровень значком в начале: 🔴
# поломка (по умолчанию), 🟡 `--warn` — не беда, но сказать, 🟢 `--ok` —
# восстановилось. Первая строка — тема, жирным: «🔴 outreach-donors · сторож
# здоровья». Текст в обратных кавычках — моноширинно: команду копируют
# нажатием. Разметка — HTML Bot API; не принял её Telegram — тот же текст
# уходит простым, и тревога не теряется из-за своей же разметки.
#
# Её шлют кроны хоста — бэкап (scripts/backup.sh) и сторож контейнеров
# (scripts/healthwatch.sh). Сервис шлёт свои тревоги сам
# (`backend/shared/alerts.py`); здесь — то же для машины, где нет питона
# нужной версии, и для минут, когда контейнеры лежат.
#
# Бот и чат — ALERT_TELEGRAM_BOT_TOKEN и ALERT_TELEGRAM_CHAT_ID из окружения
# или из `.env` рядом с компоузом (scripts/ops_env.sh).
#
# Коды выхода: 0 — Telegram принял; 3 — бот не настроен; 1 — не ушло.
# Что тревога не ушла, всегда сказано строкой в stderr: крон пишет её
# в свой журнал. Тот, кто зовёт, от тревоги не падает: `… || true`.
#
# **Токен не попадает ни в журнал, ни в список процессов.** Bot API
# принимает его только в адресе, а аргументы команды видны в `ps` любому
# пользователю машины, — поэтому адрес уходит в curl через stdin (`-K -`),
# а всё, что отсюда печатается, проходит замену токена.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/ops_env.sh
. "$ROOT/scripts/ops_env.sh"
ops_env_load "$ROOT"

LEVEL="🔴"
case "${1:-}" in
  --ok) LEVEL="🟢" && shift ;;
  --warn) LEVEL="🟡" && shift ;;
esac
if [ -z "${1:-}" ]; then
  echo "alert.sh: нужен текст тревоги — scripts/alert.sh [--ok|--warn] \"что случилось\"" >&2
  exit 2
fi
# Имя сервиса — в теме: бот и чат бывают общими у нескольких сервисов
# машины. Потолок Bot API — 4096 знаков: длиннее Telegram отказывает
# целиком, и тревога не ушла бы из-за своей же подробности. Режется
# байтами и до разметки (иначе срез пришёлся бы посреди `&lt;`), а
# `iconv -c` убирает разрезанную букву: у крона локаль обычно C, и
# `${…:0:N}` резал бы кириллицу посреди символа — такой текст Telegram
# не примет вовсе.
TEXT="$1"
if [ "$(printf '%s' "$TEXT" | wc -c)" -gt 3500 ]; then
  TEXT="$(printf '%s' "$TEXT" | head -c 3500 | iconv -c -f UTF-8 -t UTF-8)…"
fi
TOPIC="${TEXT%%$'\n'*}"
BODY="${TEXT#"$TOPIC"}"
MESSAGE="$LEVEL outreach-donors · $TEXT"

# html ТЕКСТ — экранирование для разметки Bot API; `…` — моноширинно.
html() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
    -e 's/`\([^`]*\)`/<code>\1<\/code>/g'
}
MARKED="$LEVEL <b>outreach-donors · $(html "$TOPIC")</b>$(html "$BODY")"
TOKEN="${ALERT_TELEGRAM_BOT_TOKEN:-}"
CHAT="${ALERT_TELEGRAM_CHAT_ID:-}"

if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "⚠ ТРЕВОГА НЕ ОТПРАВЛЕНА — не заданы ALERT_TELEGRAM_BOT_TOKEN и ALERT_TELEGRAM_CHAT_ID" \
    "(.env рядом с компоузом, шаги — deploy/README.md). Текст: $MESSAGE" >&2
  exit 3
fi

# Адрес Bot API меняется только для проверки подставным сервером.
API="${ALERT_TELEGRAM_API:-https://api.telegram.org}"
# send РАЗМЕТКА ТЕКСТ — один запрос; разметка — HTML или пусто (простой текст).
send() {
  if [ -n "$1" ]; then
    set -- --data-urlencode "parse_mode=$1" --data-urlencode "text=$2"
  else
    set -- --data-urlencode "text=$2"
  fi
  printf 'url = "%s/bot%s/sendMessage"\n' "${API%/}" "$TOKEN" |
    curl -sS --max-time 20 -K - --data-urlencode "chat_id=$CHAT" "$@" 2>&1
}
ANSWER="$(send HTML "$MARKED")"
CODE=$?
case "$ANSWER" in
  *"can't parse entities"*)
    ANSWER="$(send "" "$MESSAGE")"
    CODE=$?
    ;;
esac
ANSWER="${ANSWER//"$TOKEN"/<токен>}"

# Верится полю ok, а не коду выхода: прокси перед Telegram отвечает 200
# своей страницей, и такая тревога не дошла бы молча.
case "$ANSWER" in
  *'"ok":true'*) exit 0 ;;
esac

case "$ANSWER" in
  *'"error_code":400'*) HINT="проверить ALERT_TELEGRAM_CHAT_ID: чат не найден или бот в него не добавлен" ;;
  *'"error_code":401'* | *'"error_code":404'*) HINT="токен неверен или отозван — выпустить новый у @BotFather и заменить в .env" ;;
  *'"error_code":403'*) HINT="бот удалён из чата или заблокирован — вернуть его в чат" ;;
  *) HINT="Telegram недоступен или ответ не похож на Bot API — повторить: scripts/alert.sh \"проверка\"" ;;
esac
echo "⚠ ТРЕВОГА НЕ ОТПРАВЛЕНА — $HINT (curl: код $CODE, ответ: ${ANSWER:0:300}). Текст: $MESSAGE" >&2
exit 1
