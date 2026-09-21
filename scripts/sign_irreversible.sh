#!/usr/bin/env bash
# §3.4a: запрос подписи приходит сам, подписывает палец.
#
# Парный к `check_irreversible_signature.sh`: тот сверяет, этот спрашивает.
# Сверку он не повторяет, а зовёт — две реализации одного правила
# разъезжаются на первой правке, и разъедутся молча.
#
# **Что меняется и что нет.** Меняется вызов: человек больше не копирует
# команду из красного CI, окно приходит само. Не меняется, кто подписывает:
# ключ в анклаве требует касания на каждое использование, и агент, который
# окно вызвал, коснуться сенсора не может.
#
# **Почему это не отменяет замер `delivery@1.91`.** Там агент четыре раза
# позвал подпись, окно всплыло четыре раза, человек подтвердил все четыре,
# не зная, что подписывает. Вывод был сделан про запрос, а дефект был
# в окне: **системное окно называет процесс и молчит о содержимом**.
# Здесь окно несёт объявление целиком и отдельной строкой — что в нём
# добавилось с прошлой подписи. Касание привязано к прочитанному тексту.
#
# Что держит рефлекс, кроме содержимого:
#   • кнопка по умолчанию — «Не сейчас»: Enter подписи не ставит;
#   • тот же текст не спрашивается повторно раньше срока молчания;
#   • у окна два шага, и первый — чтение, а не палец.
#
# ⚠ **Чего это не даёт.** Окно по-прежнему может вызвать агент — правкой
# объявления. Защита не в том, что запрос редкий, а в том, что отказаться
# ничего не стоит, а согласиться можно только прочитав. Если запросов
# станет много, смотреть надо не на окно, а на того, кто их порождает.
#
#   --watch    для наблюдателя: молчит, если сверка зелёная или про этот
#              же текст уже спрашивали недавно
#   --install  поставить наблюдателя (launchd). Ставит ЧЕЛОВЕК: постоянный
#              агент, который будет просить его палец, заводится с его ведома
#   --dry      напечатать, что было бы в окне, и никого не спрашивать
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Пути не подменяются переменными нарочно: сверка читает их у себя, и
# подменённый здесь путь означал бы, что окно показывает одно, а CI
# проверяет другое. Наблюдатель проверяется копией дерева.
STATUS="$REPO/delivery/active/STATUS.md"
SIG="$REPO/delivery/active/irreversible.sig"
SIGNED="$REPO/delivery/active/irreversible.signed.txt"
SIGNERS="$REPO/.github/allowed_signers"
CHECK="$REPO/scripts/check_irreversible_signature.sh"
NS="irreversible"
LABEL="contour.irreversible-watch"
# Путь к сокету агента берётся у приложения ключа и подставляется
# переменной: искать его `find`'ом по `~/Library/Containers` нельзя —
# macOS отвечает `Operation not permitted` и находит ноль (§3.4a②).
AGENT_SOCKET="${IRREVERSIBLE_AGENT_SOCKET:-$HOME/Library/Containers/com.maxgoedjen.Secretive.SecretAgent/Data/socket.ssh}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/Library/Application Support}/contour"
# Сколько молчать про тот же текст после отказа: правка соседней строки
# статуса не должна превращаться в стук в дверь.
QUIET_FOR_SEC="${IRREVERSIBLE_QUIET_SEC:-1800}"

MODE="${1:-}"

declaration() {
  grep -m1 -E '^\s*-\s+\*\*irreversible_surfaces:\*\*' "$STATUS" 2>/dev/null \
    | sed -E 's/^.*\*\*irreversible_surfaces:\*\*[[:space:]]*//'
}

needs_signature() {
  ( cd "$REPO" && bash "$CHECK" >/dev/null 2>&1 ) && return 1
  return 0
}

verifies() {
  local text="$1" who
  [ -f "$SIG" ] && [ -f "$SIGNERS" ] || return 1
  who=$(awk 'NF {print $1; exit}' "$SIGNERS")
  printf '%s' "$text" \
    | ssh-keygen -Y verify -f "$SIGNERS" -I "$who" -n "$NS" -s "$SIG" >/dev/null 2>&1
}

previous() {
  # Что было подписано в прошлый раз. Копия сверяется той же подписью:
  # подменённая не подтвердится, и окно честно скажет, что прошлый текст
  # неизвестен, вместо того чтобы показать выдуманный диф.
  [ -f "$SIGNED" ] || return 1
  local was
  was=$(cat "$SIGNED")
  verifies "$was" || return 1
  printf '%s' "$was"
}

changes_against() {
  # Опасна именно дописка: длинную строку перечитывают по диагонали,
  # а добавленный кусок виден сразу.
  python3 - "$1" "$2" <<'PYEOF'
import re
import sys

def parts(line: str) -> list[str]:
    return [p.strip(" —-") for p in re.split(r"[;,]", line) if p.strip(" —-")]

now, was = parts(sys.argv[1]), parts(sys.argv[2])
fresh = [p for p in now if p not in was]
gone = [p for p in was if p not in now]
if fresh:
    print("ДОБАВЛЕНО: " + "; ".join(fresh))
if gone:
    print("УБРАНО: " + "; ".join(gone))
if not fresh and not gone:
    print("Слова те же — изменилась расстановка.")
PYEOF
}

asked_recently() {
  local stamp
  stamp="$STATE_DIR/asked-$(printf '%s' "$1" | shasum | cut -c1-16)"
  if [ -f "$stamp" ]; then
    local age=$(( $(date +%s) - $(stat -f %m "$stamp") ))
    [ "$age" -lt "$QUIET_FOR_SEC" ] && return 0
  fi
  mkdir -p "$STATE_DIR" && touch "$stamp"
  return 1
}

ask() {
  local prompt="Объявление необратимого (§3.4a).

Подписываешь ровно это:

$1

$2

Дальше касание. Окно может вызвать агент, коснуться сенсора — нет."

  if [ "$MODE" = "--dry" ]; then
    printf '%s\n' "── окно показало бы ──" "$prompt" "── конец окна ──"
    return 1
  fi

  # Окна нет — значит и запроса нет. Молча свалиться в «подписать без
  # чтения» нельзя: правило §3.4a держится на содержимом окна, и система
  # без него обязана сказать это вслух, а не притвориться работающей.
  if ! command -v osascript >/dev/null 2>&1; then
    printf '%s\n' "$prompt" >&2
    echo "Окно показать нечем (нет osascript): вариант написан под macOS." >&2
    echo "На другой системе подпись ставит человек, прочитав строку выше:" >&2
    echo "  printf '%s' '<строка объявления>' | ssh-keygen -Y sign -f <ключ> -n irreversible - > $SIG" >&2
    return 1
  fi

  # Текст уходит доводом, а не внутрь строки скрипта: в объявлении бывают
  # кавычки, и склейка однажды съест их вместе со смыслом.
  osascript - "$prompt" <<'APPLESCRIPT' 2>/dev/null | grep -q "Подписать"
on run argv
  display dialog (item 1 of argv) with title "Подпись необратимого" ¬
    buttons {"Не сейчас", "Подписать"} default button "Не сейчас" with icon caution
  return button returned of result
end run
APPLESCRIPT
}

install_watcher() {
  # Наблюдатель срабатывает по правке объявления, а не по расписанию:
  # спрашивать «на всякий случай» — прямой путь к рефлексу.
  if ! command -v launchctl >/dev/null 2>&1; then
    echo "Наблюдатель написан под launchd (macOS). На другой системе тот же" >&2
    echo "эффект даёт вызов '--watch' из хука или таймера системы." >&2
    exit 1
  fi
  local plist="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$REPO/scripts/sign_irreversible.sh</string>
		<string>--watch</string>
	</array>
	<key>WatchPaths</key>
	<array>
		<string>$STATUS</string>
		<string>$SIG</string>
	</array>
	<key>StandardOutPath</key><string>/tmp/$LABEL.log</string>
	<key>StandardErrorPath</key><string>/tmp/$LABEL.log</string>
	<key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST
  # launchd держит копию и сам файл не перечитывает: без выгрузки правка
  # plist не действует — молча.
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" "$plist" || {
    echo "launchd отказался загрузить $plist"
    exit 1
  }
  echo "Наблюдатель поставлен: $plist"
  echo "Снять: launchctl bootout gui/$(id -u)/$LABEL"
  exit 0
}

notify() {
  osascript -e "display notification \"$1\" with title \"Подпись необратимого\"" >/dev/null 2>&1
}

[ "$MODE" = "--install" ] && install_watcher

line=$(declaration)
if ! needs_signature; then
  [ "$MODE" = "--watch" ] || echo "Подписывать нечего: сверка зелёная."
  exit 0
fi

if [ -z "$line" ]; then
  [ "$MODE" = "--watch" ] && exit 0
  echo "В $STATUS нет строки irreversible_surfaces — сначала объявление, потом подпись."
  exit 1
fi

if [ "$MODE" = "--watch" ] && asked_recently "$line"; then
  exit 0
fi

if was=$(previous); then
  changes=$(changes_against "$line" "$was")
else
  changes="Прошлое подписанное объявление неизвестно — сверять не с чем."
fi

if ! ask "$line" "$changes"; then
  [ "$MODE" = "--watch" ] || echo "Отложено."
  exit 1
fi

if [ ! -S "$AGENT_SOCKET" ]; then
  notify "Хранилище ключа не запущено — подписать нечем"
  echo "Нет сокета ключа: $AGENT_SOCKET"
  exit 1
fi

# Публичный ключ — из списка разрешённых подписантов, а не откуда придётся:
# подписать можно только тем ключом, которым сверка потом проверяет.
pub=$(mktemp)
awk 'NF {print $2, $3; exit}' "$SIGNERS" > "$pub"
# Только `2>/dev/null`: с `2>&1` служебная строка ssh-keygen попадает в файл
# подписи, и сверка отвечает «missing header» — это читается как неверная
# подпись, а не как испорченный файл (§3.4a③).
if printf '%s' "$line" | SSH_AUTH_SOCK="$AGENT_SOCKET" \
    ssh-keygen -Y sign -f "$pub" -n "$NS" - > "$SIG" 2>/dev/null; then
  rm -f "$pub"
  printf '%s' "$line" > "$SIGNED"
  if verifies "$line"; then
    notify "Подписано"
    echo "Подписано: $line"
    exit 0
  fi
  notify "Подпись не сходится — смотри вывод"
  echo "Подпись поставлена, но сверка не сошлась: ключ в allowed_signers тот же?"
  exit 1
fi

rm -f "$pub"
notify "Подпись не поставлена"
echo "Подписать не удалось: касания не было или ключ недоступен."
exit 1
