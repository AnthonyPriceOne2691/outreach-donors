#!/usr/bin/env bash
# Ратчет по сложности ФУНКЦИЙ: цикломатика, ветвления, операторы, аргументы, return'ы.
#
# Зачем отдельным гейтом, если правила уже есть в ruff. Обычный ruff-хук даёт
# бинарное «да/нет» на весь пакет. На легаси это означает сотни срабатываний в
# первый же день — и правила отправляются в `ignore` целиком, после чего сложность
# не контролируется НИГДЕ. Ратчет снимает это противоречие: старое зафиксировано
# снимком, новый код держим на нуле, снимок только тает.
#
# Почему это важнее длины файла: file-length смотрит на файл, а плохой код живёт
# в функции. Функция на 400 строк внутри файла на 480 строк проходит file-length
# свободно.
#
# Пороги берутся из pyproject проекта ([tool.ruff.lint.mccabe] / [.pylint]) —
# один источник правды. `lint.ignore=[]` сбрасывает ignore, чтобы правила
# отработали даже если в конфиге они заглушены для основного хука.
#
# ⚠ ДВЕ ПОЛОВИНЫ, как у `check_deps_audit.sh`: python через ruff и TS/JS через
# eslint. Выбор — по наличию каталога, а не по «языку проекта»: fullstack-репо
# получает обе, и обе пишут в ОДИН снимок в одном формате `<count>:<path>`.
# Это не украшение, а условие бюджета §9.1a: единица счёта — строка таблицы §3,
# и роль остаётся одной строкой ровно потому, что снимок, порог и сообщение у
# неё одни. Завёл бы второй baseline-файл — получил бы второй гейт и был бы
# обязан заплатить слотом (а свободных нет).
#
# ⚠ Цена для УЖЕ РАЗВЁРНУТОГО fullstack-проекта названа честно: у него в снимке
# не было TS-строк, и первый прогон после обновления канона краснеет на легаси
# фронта. Лечение — один раз `--generate`, и это РОСТ снимка, который
# `check_baseline_ratchet.sh` штатно блокирует: нужен `ALLOW_BASELINE_GROWTH=1`
# с причиной в PR (§8.2). Молча дорастить снимок нельзя — и не надо.
#
# Чего TS-половина НЕ покрывает, и это замер, а не догадка: у PLR0911 (число
# return'ов) аналога в ESLint нет вовсе — правила `max-returns` не существует,
# `--rule` с ним даёт exit 2. Остальные четыре легли один в один:
# C901→complexity, PLR0912→max-depth (ветвление через глубину), PLR0913→max-params,
# PLR0915→max-statements.
#
# Настройка (env): LINT_PY_SRC, LINT_TS_SRC, LINT_FE_DIR, LINT_VENV, STRICT=0 — soft.
#
# Режимы:
#   check_complexity_gate.sh             # проверка
#   check_complexity_gate.sh --generate  # пере-снять baseline
#   check_complexity_gate.sh --report    # показать текущие нарушения по файлам

set -uo pipefail

STRICT=${STRICT:-1}
PY_SRC=${LINT_PY_SRC:-backend/features}
FE_DIR=${LINT_FE_DIR:-frontend}
TS_SRC=${LINT_TS_SRC:-$FE_DIR/src}
VENV=${LINT_VENV:-backend/.venv}
# Значение, данное «относительно backend» (так его описывала §6 до cqg@1.33),
# тоже принимается: одно написание не могло удовлетворить обе трактовки, и
# документированное `.venv` глушило этот гейт мягким пропуском (lab-9 №7).
[[ -d "$VENV" || ! -d "backend/$VENV" ]] || VENV="backend/$VENV"
RULES=${LINT_COMPLEXITY_RULES:-C901,PLR0911,PLR0912,PLR0913,PLR0915}
# Пороги TS повторяют python-овские числом, чтобы обе половины держали одну
# планку: complexity=C901, max-params=PLR0913, max-statements=PLR0915.
TS_RULES=${LINT_TS_COMPLEXITY_RULES:-'{"complexity":["error",10],"max-depth":["error",4],"max-params":["error",8],"max-statements":["error",50]}'}
MODE=${1:-check}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASELINE="$SCRIPT_DIR/complexity_baseline.txt"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

RUFF="$VENV/bin/ruff"
[[ -x "$RUFF" ]] || RUFF=$(command -v ruff 2>/dev/null || true)
ESLINT="$FE_DIR/node_modules/.bin/eslint"
[[ -x "$ESLINT" ]] || ESLINT="node_modules/.bin/eslint"
[[ -x "$ESLINT" ]] || ESLINT=$(command -v eslint 2>/dev/null || true)
# АБСОЛЮТНЫЕ пути для TS-половины: она меняет cwd (flat-config eslint ищется от
# него, а канон кладёт конфиг в `<frontend>/`, §5.0), и относительные значения
# после `cd` указывали бы не туда. Замер до этого: exit 2 «ESLint couldn't find an
# eslint.config.(js|mjs|cjs) file» — то есть канон не попадал в раскладку, которую
# сам же предписывает (`cqg@2.04`, найдено полевым прогоном).
ESLINT_ABS=$ESLINT; [[ "$ESLINT_ABS" == /* ]] || ESLINT_ABS="$REPO_ROOT/$ESLINT"
TS_ABS=$TS_SRC;     [[ "$TS_ABS" == /* ]]    || TS_ABS="$REPO_ROOT/$TS_SRC"

# Половина ЖИВА, только если есть И каталог, И инструмент. Причина мёртвой
# половины называется отдельно от «нашли ноль» — тот же класс, что
# `py_unchecked`/`js_unchecked` у `check_deps_audit.sh` (lab-9 №6): ноль без
# проверки читается как доказательство, которого не было.
py_live=0; ts_live=0; skipped=()
if [[ ! -d "$PY_SRC" ]]; then
  skipped+=("python: нет каталога $PY_SRC (настрой LINT_PY_SRC, §6)")
elif [[ -z "$RUFF" ]]; then
  skipped+=("python: ruff не найден (pip install ruff)")
else
  py_live=1
fi
if [[ ! -d "$TS_SRC" ]]; then
  skipped+=("ts: нет каталога $TS_SRC (настрой LINT_TS_SRC, §6)")
elif [[ -z "$ESLINT" ]]; then
  skipped+=("ts: eslint не найден (npm i -D eslint)")
else
  ts_live=1
fi
if (( py_live == 0 && ts_live == 0 )); then
  printf '%s⚠ гейт сложности пропущен — обе половины мертвы:%s\n' "$yellow" "$reset"
  printf '  · %s\n' "${skipped[@]}"
  exit 0
fi
# Какая половина судила — считаем ЗДЕСЬ, потому что это нужно и шапке снимка, и
# итоговой строке. Первая редакция считала перед печатью итога, и `--generate`
# писал шапку «Правила: C901,PLR09xx / пороги — в pyproject» над снимком, целиком
# набранным eslint'ом. Поймано обратным прогоном; класс F8 — поле, существующее
# ради ответа «чем это снято», врало молча.
halves=""
(( py_live )) && halves="python"
(( ts_live )) && halves="$halves${halves:+ + }ts"

# "<count>:<path>" — тот же формат, что у остальных снимков, поэтому
# check_baseline_ratchet.sh автоматически следит и за этим файлом.
#
# ⚠ Код возврата ruff проверяется, а stderr больше не уходит в /dev/null —
# находка 8 первого развёртывания: один прогон из пяти дал ПУСТОЙ снимок при
# фактическом нарушении, и объяснить это было нечем. У ruff `0` — чисто, `1` —
# есть находки, `2` и выше — ОШИБКА (битый конфиг, нечитаемый файл, падение).
# Пока stderr глотался, ошибка выглядела как «нарушений нет»: пустой снимок
# легализует любую сложность, и он ещё и КОММИТИТСЯ. Теперь причина названа
# словами ruff, а снимок не пишется вовсе.

# Половины живут в соседнем файле — подключаем ПОСЛЕ настройки переменных, чтобы
# читалось сверху вниз: сначала «чем меряем», потом «чем считаем».
# shellcheck source=/dev/null
. "$SCRIPT_DIR/complexity_halves.sh"

tmp=$(mktemp); errf=$(mktemp); tmp2=$(mktemp)
trap 'rm -f "$tmp" "$errf" "$tmp2"' EXIT
current_counts >"$tmp" || exit 1

if [[ "$MODE" == "--generate" ]]; then
  # ⚠ ДВА прогона и сверка — вторая половина находки 8. Снимок это КОММИТИМЫЙ
  # артефакт: неверный легализует нарушения молча и надолго, поэтому цена второго
  # прогона (секунда на ручном шаге) меньше цены одной молчаливой ошибки. Если
  # прогоны разошлись — писать нечего: выбирать между двумя ответами значит
  # угадывать, а гейт не угадывает.
  current_counts >"$tmp2" || exit 1
  if ! diff -q "$tmp" "$tmp2" >/dev/null; then
    printf '%sERROR%s: два прогона ruff на одном дереве дали РАЗНОЕ — снимок не снят.\n' \
      "$red" "$reset" >&2
    printf 'Это ровно находка 8 (непостоянный результат), и теперь она видна, а не\n' >&2
    printf 'записана в снимок. Разница:\n' >&2
    diff "$tmp" "$tmp2" >&2 || true
    exit 1
  fi
  {
    echo "# complexity_baseline.txt — снимок нарушений сложности ФУНКЦИЙ по файлам."
    echo "# Формат: <число нарушений>:<путь>. Половины, снявшие снимок: $halves"
    (( py_live )) && echo "# python: ruff $RULES; пороги — в pyproject ([tool.ruff.lint.mccabe] / [.pylint])."
    (( ts_live )) && echo "# ts: eslint $TS_RULES (порог у гейта, env LINT_TS_COMPLEXITY_RULES)."
    echo "# Снимок только ТАЕТ: тронул файл — раздели функцию и пере-сними вниз."
    cat "$tmp"
  } >"$BASELINE"
  # Просмотренное и «с находками» — РАЗНЫЕ числа, и печатать надо оба (F6).
  # `grep -cv '^#'` считает файлы С НАРУШЕНИЯМИ; на чистом проекте это «0», что
  # неотличимо от «ruff смотрел не туда». Тот же класс L2, что уже правился в
  # check-режиме этого же гейта, — в ветке `--generate` остался.
  n_seen=$(count_seen)
  printf '%sbaseline пересобран:%s %s — просмотрено %s файл(ов), с находками %d\n' \
    "$green" "$reset" "$BASELINE" "$n_seen" "$(grep -cv '^#' "$BASELINE")"
  if [[ "$n_seen" == "0" ]]; then
    printf '%s⚠ просмотрено 0 файлов — проверь LINT_PY_SRC/LINT_TS_SRC (§6): пустой\n' "$yellow"
    printf 'снимок на непустом проекте значит «гейт смотрел не туда».%s\n' "$reset"
  fi
  exit 0
fi

if [[ "$MODE" == "--report" ]]; then
  if (( py_live )); then
    printf 'Нарушения сложности по файлам, python (правила %s):\n' "$RULES"
    "$RUFF" check --no-cache --quiet --output-format=concise \
      --select "$RULES" --config 'lint.ignore=[]' -- "$PY_SRC" 2>/dev/null
  fi
  if (( ts_live )); then
    printf 'Нарушения сложности по файлам, ts (правила %s):\n' "$TS_RULES"
    # Через ту же обёртку, что и счёт: иначе `--report` (то самое «что именно
    # длинное», куда посылает красный итог) падал бы с exit 2 на раскладке,
    # которую канон предписывает сам.
    ts_eslint --no-color --rule "$TS_RULES" 2>/dev/null
  fi
  exit 0
fi

violations=0
# `scanned` — число ПРОСМОТРЕННЫХ файлов, а не файлов с находками. Первая версия
# счётчика (cqg@1.20) считала итерации цикла ниже, а цикл идёт по НАХОДКАМ ruff:
# на чистом проекте выходило «просмотрено 0 файл(ов)», что по смыслу §6 читается
# как «гейт ничего не видел». Хуже — одна и та же фраза в двух гейтах канона
# значила разное. Найдено независимым развёртыванием (lab-4).
scanned=$(count_seen)
scanned=${scanned:-0}
flagged=0
while IFS=: read -r count path; do
  [[ -n "$path" ]] || continue
  allowed=0
  if [[ -f "$BASELINE" ]]; then
    allowed=$(awk -F: -v p="$path" '!/^#/ && $2 == p { print $1; exit }' "$BASELINE")
    allowed=${allowed:-0}
  fi
  flagged=$((flagged + 1))
  if (( count > allowed )); then
    printf '%s  ✗  %s: %d нарушений сложности (разрешено %d)%s\n' \
      "$red" "$path" "$count" "$allowed" "$reset"
    violations=$((violations + 1))
  fi
done <"$tmp"

# «OK» на fullstack-репо с мёртвой половиной выглядит ровно как честное «OK» по
# обеим — поэтому пропуск называется вслух и здесь, а не только когда мертвы обе.
if (( ${#skipped[@]} )); then
  printf '%s⚠ половина(ы) НЕ проверены:%s\n' "$yellow" "$reset"
  printf '  · %s\n' "${skipped[@]}"
fi

if (( violations == 0 )); then
  # Число просмотренного — обязательная часть вывода (приёмка §6), иначе «OK»
  # неотличимо от «нечего было смотреть».
  if (( scanned == 0 )); then
    printf '%scomplexity: 0 файлов просмотрено%s — проверь LINT_PY_SRC/LINT_TS_SRC (§6)\n' \
      "$yellow" "$reset"
    exit 0
  fi
  printf '%scomplexity: OK%s (%s) — просмотрено %d файл(ов), с находками %d, по маске: %s\n' \
    "$green" "$reset" "$halves" "$scanned" "$flagged" "$PY_SRC/** $TS_SRC/**"
  exit 0
fi

printf '\n%sERROR%s: %d файл(ов) превышают снимок сложности.\n' "$red" "$reset" "$violations" >&2
printf 'Дели функцию (§2.1: thin orchestrator + phase-helpers), не поднимай порог.\n' >&2
printf 'Что именно длинное/ветвистое: %s --report\n' "$0" >&2
printf 'Легаси-файл впервые попал под гейт? --generate (снимок только вниз).\n' >&2
[[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
exit 1
