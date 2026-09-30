#!/usr/bin/env bash
# DRY / copy-paste гейт (jscpd). Глобальный clone-count ратчет.
#
# jscpd считает clone-пары в прод-Python; снимок хранится в jscpd_baseline.txt; гейт
# падает, если число дублей ВЫРОСЛО (добавили копипаст). После консолидации дублей
# число падает — пере-снять baseline вниз через --generate. Порог jscpd: min-tokens 50.
#
# Бинарь jscpd ищется в node_modules фронта, затем в PATH. Нет бинаря — гейт не
# блокирует, только предупреждает (свежий clone без npm ci).
#
# Настройка (env):
#   LINT_PY_SRC  — каталог прод-Python для анализа, от repo-root (дефолт: backend/features)
#   LINT_FE_DIR  — фронт-каталог с node_modules (дефолт: frontend)
#
# Режимы:
#   check_jscpd_gate.sh             # проверка; exit 1 при росте дублей
#   check_jscpd_gate.sh --generate  # пере-снять baseline (текущее число дублей)
#   STRICT=0 check_jscpd_gate.sh    # soft (warning, exit 0)

set -uo pipefail

STRICT=${STRICT:-1}
PY_SRC=${LINT_PY_SRC:-backend/features}
FE_DIR=${LINT_FE_DIR:-frontend}
TS_SRC=${LINT_TS_SRC:-$FE_DIR/src}
GENERATE=0
[[ "${1:-}" == "--generate" ]] && GENERATE=1

# Любое значение перед арифметикой приводится к числу — страховка КЛАССА, тот же
# приём и по той же причине, что `num()` в check_deps_audit.sh (lab-9 №6): пустой
# вывод инструмента в `(( ))` роняет сырую ошибку шелла.
num() { local v=${1//[!0-9]/}; printf '%s' "${v:-0}"; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASELINE="$SCRIPT_DIR/jscpd_baseline.txt"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

if [[ -x "$FE_DIR/node_modules/.bin/jscpd" ]]; then
  JSCPD="$FE_DIR/node_modules/.bin/jscpd"
elif [[ -x "node_modules/.bin/jscpd" ]]; then
  # Односоставный npm-проект: `node_modules` в корне, каталога фронта нет вовсе.
  # Без этой ветки гейт печатал «jscpd не найден» ПРИ установленном jscpd.
  JSCPD="node_modules/.bin/jscpd"
elif command -v jscpd >/dev/null 2>&1; then
  JSCPD="jscpd"
else
  printf '%s⚠ jscpd не найден — DRY-гейт пропущен. Установка: npm i -D jscpd%s\n' "$yellow" "$reset"
  exit 0
fi

# ⚠ ДВЕ ПОЛОВИНЫ, как у complexity и layers (`cqg@1.66`) — и появились они позже
# на девять ревизий, что само по себе находка. Карта стеков §Применимость всё это
# время обещала `jscpd-DRY` переносимым («jscpd умеет ~150 языков»), а вызов был
# зашит на `-f python` и `$LINT_PY_SRC`. На TS-проекте это давало худшее из
# возможного: инструмент установлен, гейт вписан, снимок снят — и всё это по
# несуществующему python-каталогу. Полевой аудит нашёл ровно это.
#
# ⚠ И отдельно: НЕЛЬЗЯ было чинить одной переменной. Задать `LINT_FE_DIR` в
# `entry:`, не тронув `-f python`, значит превратить честный пропуск («jscpd не
# найден») в молчаливое зелёное («0 clone-пар» по пустому каталогу) — ровно тот
# обмен, против которого весь контур.
PY_FMT=${LINT_JSCPD_PY_FORMAT:-python}
TS_FMT=${LINT_JSCPD_TS_FORMAT:-typescript}
# Расширения → формат. Замер (jscpd 4.x, Astro-проект): без этой строки `.astro`
# не считается ВООБЩЕ — «Files analyzed 1» вместо 2, то есть весь код проекта
# мимо гейта. Проект дописывает свои: `typescript:ts,tsx,astro,vue,svelte`.
TS_EXTS=${LINT_JSCPD_TS_EXTS:-typescript:ts,tsx,mts,cts}

# «Files analyzed» — то самое число просмотренного, которого §6 требует от
# каждого сканирующего гейта. До cqg@1.76 гейт его не печатал, и доктор честно
# отвечал «сканирующий ли он — отсюда не видно»: проверка на слепоту
# структурно не покрывала гейт, который как раз и был слеп.
_run_jscpd() {   # $1 — каталог, $2 — формат, $3 — доп.флаги (может быть пусто)
  # shellcheck disable=SC2086
  "$JSCPD" "$1" -f "$2" $3 -k 50 -i "**/tests/**,**/test_*.py,**/*.test.*,**/*.spec.*" \
    -r console 2>/dev/null
}

# ⚠ РАСКРАСКА СНИМАЕТСЯ ПЕРВОЙ, и это не косметика: в ANSI-последовательностях
# есть ЦИФРЫ (`\033[39m`, `\033[90m`), а `num()` вытаскивает все подряд. Первая
# редакция читала «Files analyzed 2» как **39290** — то есть гейт отчитывался,
# что просмотрел сорок тысяч файлов в проекте из двух. Замер поймал сразу
# (число было абсурдным), но абсурдным оно оказалось случайно: при других кодах
# получилось бы правдоподобное враньё, и проверка «видит ли гейт код» приняла бы
# его за доказательство.
_plain() { printf '%s\n' "$1" | sed $'s/\033\\[[0-9;]*m//g'; }
_clones_of() { _plain "$1" | grep -oE 'Found [0-9]+ clones' | grep -oE '[0-9]+' | head -1; }
# Разделитель таблицы — рамочный `│` (U+2502), а НЕ ASCII-пайп: подмена дала
# ноль просмотренных, то есть ложную слепоту вместо верного числа.
_files_of()  { _plain "$1" | awk -F'│' '/Total:/ { gsub(/[^0-9]/,"",$3); print $3; exit }'; }

count=0; scanned=0; halves=""
if [[ -d "$PY_SRC" ]]; then
  out=$(_run_jscpd "$PY_SRC" "$PY_FMT" "")
  count=$(( count + $(num "$(_clones_of "$out")") ))
  scanned=$(( scanned + $(num "$(_files_of "$out")") ))
  halves="python"
fi
if [[ -d "$TS_SRC" ]]; then
  out=$(_run_jscpd "$TS_SRC" "$TS_FMT" "--formats-exts $TS_EXTS")
  count=$(( count + $(num "$(_clones_of "$out")") ))
  scanned=$(( scanned + $(num "$(_files_of "$out")") ))
  halves="$halves${halves:+ + }ts"
fi
if [[ -z "$halves" ]]; then
  printf '%s⚠ jscpd: нет ни %s, ни %s — DRY-гейт пропущен (настрой LINT_PY_SRC/LINT_TS_SRC, §6)%s\n' \
    "$yellow" "$PY_SRC" "$TS_SRC" "$reset"
  exit 0
fi

if [[ "$GENERATE" == "1" ]]; then
  {
    echo "# jscpd_baseline.txt — снимок DRY-гейта. Генерируется --generate, НЕ руками."
    echo "# Одно число = кол-во clone-пар в прод-Python (python, min-tokens 50, без tests)."
    echo "# Гейт падает при РОСТЕ; после консолидации дублей пере-снять вниз (--generate)."
    echo "$count"
  } >"$BASELINE"
  echo "${green}jscpd baseline пересобран${reset}: $count clone-пар"
  exit 0
fi

baseline=$(grep -m1 -oE '^[0-9]+' "$BASELINE" 2>/dev/null)
baseline=${baseline:-0}

if [[ "$count" -gt "$baseline" ]]; then
  if [[ "$STRICT" == "1" ]]; then
    printf '%sERROR%s: дубли выросли: %d clone-пар (baseline %d).\n' "$red" "$reset" "$count" "$baseline"
    echo "Не копируй код: вынеси общий helper/константу (rule-of-three для логики)."
    echo "Если рост оправдан (напр. split-facade) — пере-снять снимок: --generate."
    exit 1
  fi
  printf '%sWARNING%s: дубли выросли: %d (baseline %d).\n' "$yellow" "$reset" "$count" "$baseline"
else
  # Успешный путь обязан назвать число (§6). Четвёртое появление класса «гейт не
  # сказал, что смотрел»: закрывали у grep-гейта, complexity, ast-гейта — и каждый
  # раз у одного скрипта. Здесь молчание неотличимо от «jscpd не запускался».
  if (( scanned == 0 )); then
    printf '%sjscpd: 0 файлов просмотрено%s — проверь LINT_PY_SRC/LINT_TS_SRC и\n' "$yellow" "$reset"
    printf 'LINT_JSCPD_TS_EXTS (§6): ноль дублей на непросмотренном — не «чисто».\n'
    exit 0
  fi
  printf '%sjscpd: OK%s (%s) — просмотрено %d файл(ов), clone-пар %d, снимок %d, по маске: %s\n' \
    "$green" "$reset" "$halves" "$scanned" "$count" "$baseline" "$PY_SRC/** $TS_SRC/**"
fi
exit 0
