#!/usr/bin/env bash
# Длина файлов кода: prod <= 500 / tests <= 1000 строк, с baseline-ratchet.
#
# Одномоментный сплит всех god-файлов невозможен, поэтому вводится снимок текущих
# превышений (file_length_baseline.txt), который ТАЕТ по мере сплитов. Файл из
# baseline проходит при size <= снимок+HEADROOM; новый / вне baseline — hard 500/1000.
# Exemption-секция (файлы без шва для сплита) не тает.
#
#   Тип файла                 | Hard limit (вне baseline)
#   Production .py/.ts/.tsx    |    500
#   Tests (*/tests/*, *.test)  |   1000
#   (миграции / node_modules — без лимита; настрой is_excluded под себя)
#
# Настройка (env): MAX_LINES_PROD, MAX_LINES_TESTS, BASELINE_HEADROOM, EXEMPTION_HEADROOM.
#
# Режимы:
#   check_file_length.sh            # проверка; exit 1 при нарушении
#   check_file_length.sh --generate # пересобрать baseline из текущего дерева
#   check_file_length.sh --tighten  # опустить снимки ужавшихся baseline-файлов
#   STRICT=0 check_file_length.sh   # soft (warning, exit 0) — аварийно, виден в PR

MAX_LINES_PROD=${MAX_LINES_PROD:-500}
MAX_LINES_TESTS=${MAX_LINES_TESTS:-1000}
# Headroom к снимку baseline: точечные правки god-файла не блокируются до его сплита.
# Exemption получает больше (швов нет — рост правдоподобнее).
BASELINE_HEADROOM=${BASELINE_HEADROOM:-30}
EXEMPTION_HEADROOM=${EXEMPTION_HEADROOM:-50}
STRICT=${STRICT:-1}

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# SCRIPT_DIR резолвим ДО cd в REPO_ROOT (BASH_SOURCE относителен cwd вызова).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASELINE="$SCRIPT_DIR/file_length_baseline.txt"

cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m')
yellow=$(printf '\033[33m')
green=$(printf '\033[32m')
reset=$(printf '\033[0m')

# --- helpers ---------------------------------------------------------------

# Путь вне населения: «не наш код» (§3.1f) ИЛИ «наш, но не судим длиной».
# Первый список — общий для контура и правится каноном, второй — настройка проекта.
# ⚠ Прежняя форма `*/node_modules/*` требовала ведущего слэша, поэтому
# `node_modules/` В КОРНЕ чужим не считался вовсе, а `.venv`, `vendor`, `.tox`,
# `build`, `dist` не считались нигде: 9 расхождений из 15 с двумя соседями.
is_excluded() {
  printf '%s\n' "$1" | grep -qE '(^|/)(\.venv|venv|site-packages|node_modules|vendor|\.tox|\.nox|\.eggs|__pycache__|\.mypy_cache|\.pytest_cache|\.ruff_cache|build|dist|\.git)(/|$)' && return 0
  case "$1" in
    */migrations/*) return 0 ;;   # наш код, длиной не судим — настройка проекта
  esac
  return 1
}

# Тестовый файл? Принцип и полное выражение — §3.1e. Выражение здесь БАЙТ-В-БАЙТ
# такое же, как в `check_grep_gate.sh`, `mutation_ts.sh` и `check_diff_coverage.sh`;
# согласие всех пяти реализаций держит `tests/test_what_is_a_test_file.py`.
# Прежняя форма знала три случая из одиннадцати: `conftest.py`, `util_test.py`,
# `__tests__/`, `FooTest.java` и `FooTests.cs` получали ПРОДОВЫЙ лимит длины.
is_test() {
  printf '%s\n' "$1" | grep -qE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.'
}

# Файлы под exemption (нет шва для сплита — держим сознательно, лимит = снимок+EXEMPTION_HEADROOM).
# По умолчанию пусто; впиши сюда свои случаи через case-паттерны.
is_exempt_path() {
  case "$1" in
    __never_matches__) return 0 ;;
  esac
  return 1
}

base_limit_for() {
  if is_test "$1"; then echo "$MAX_LINES_TESTS"; else echo "$MAX_LINES_PROD"; fi
}

# Снимок baseline для точного пути (или пусто). Печатает "<snapshot>:<section>".
# awk-сравнение точной строки пути -> корректно с пробелами в пути.
baseline_lookup() {
  [[ -f "$BASELINE" ]] || return 0
  awk -v p="$1" '
    /^[[:space:]]*#/ { next }
    /^\[baseline\]/  { sect="baseline"; next }
    /^\[exemption\]/ { sect="exemption"; next }
    /^[0-9]+:/ {
      n=$0; sub(/:.*/, "", n)
      path=$0; sub(/^[0-9]+:/, "", path)
      if (path == p) { print n ":" sect; exit }
    }
  ' "$BASELINE"
}

# Механика контура — не продуктовый код. Гейт длины сканирует ВСЁ дерево (в
# отличие от grep-гейтов, ограниченных $PY_SRC), и без этого фильтра
# scripts/delivery_check.py — 912 строк из payload'а самого канона — попадал в
# снимок продуктовых нарушений при развёртывании: канон поставлял скрипт,
# нарушающий собственное правило, а процедура молча узаконивала это снимком.
# Найдено полем (lab-2); зеркало решения для breaker'ов (Delivery §3.4).
# Размер скриптов контура мерится отдельно — ратчет ВЕСА (Delivery §9.1a): снимок
# `tests/contour_size_baseline.txt` в репозитории канона, потолок на скрипт только
# вниз, планка нового — 300. До cqg@1.81 эта строка обещала замер, которого не было.
#
# ⚠ КОНФИГИ контура исключаются здесь же (cqg@2.04). Дефолтная маска их не берёт,
# но LINT_LENGTH_GLOBS — настройка: стоит проекту дописать '*.js *.cjs', и гейт
# начнёт мерить `eslint.config.js` и `.dependency-cruiser.cjs` — файлы, которые
# поставляет сам канон, — как продуктовый код. Ровно F14, только по другому пути.
# И то же правило читает доктор (CONTOUR_RE): зеркало обязано совпадать, иначе
# полная канонная раскладка обвиняет сама себя (замер: DEAD 1 на свежем стенде).
exclude_contour() {
  grep -vE '^(scripts/lint/|scripts/delivery_(check|metrics)\.py$|scripts/okf_[a-z_]+\.py$|scripts/merge_guard\.sh$|delivery/|knowledge/|docs/canon/|([^/]+/)*(\.dependency-cruiser|eslint\.config)\.[cm]?js$)'
}

scanned=0
baseline_n=0

# Маска расширений — НАСТРОЙКА, а не константа. Была вшита ('*.py' '*.ts' '*.tsx'),
# из-за чего на Swift-проекте гейт печатал «0 файлов просмотрено», хотя таблица
# «Применимость» обещает, что file-length переносится как есть. Расширение файла —
# это язык, а язык менять было нечем: env §6 настраивает пути. Найдено полем (lab-3).
# Дефолт прежний, поведение Python+TS-проектов не меняется.
LENGTH_GLOBS=${LINT_LENGTH_GLOBS:-"*.py *.ts *.tsx"}

collect_files() {
  if [[ $# -gt 0 ]]; then
    printf '%s\n' "$@"
    return 0
  fi
  # Паттерны отдаём git'у ПООДИНОЧНО И В КАВЫЧКАХ. Неквотированное $LENGTH_GLOBS
  # раскрывает ШЕЛЛ по текущему каталогу вместо git по всему дереву: на
  # Swift-проекте `*.swift` превращался в единственный Package.swift из корня, и
  # гейт рапортовал «просмотрено 1» при четырёх файлах (поймано проверкой правки).
  # `read -ra` даёт разделение по словам БЕЗ pathname expansion; `for g in $VAR`
  # не годится — там раскрытие остаётся.
  local -a globs=()
  IFS=' ' read -ra globs <<<"$LENGTH_GLOBS"
  # Пустой массив под `set -u` в bash 3.2 (штатный на macOS) роняет расширение.
  (( ${#globs[@]} )) || return 0
  git ls-files "${globs[@]}" 2>/dev/null | exclude_contour
}

# --- --generate: пересобрать baseline из текущего дерева ---------------------

if [[ "${1:-}" == "--generate" ]]; then
  tmp_base=$(mktemp)
  tmp_exempt=$(mktemp)
  n_seen=0
  while IFS= read -r f; do
    is_excluded "$f" && continue
    [[ -f "$f" ]] || continue
    n_seen=$((n_seen + 1))
    limit=$(base_limit_for "$f")
    lines=$(wc -l <"$f" | tr -d ' ')
    [[ "$lines" -gt "$limit" ]] || continue
    if is_exempt_path "$f"; then
      printf '%s:%s\n' "$lines" "$f" >>"$tmp_exempt"
    else
      printf '%s:%s\n' "$lines" "$f" >>"$tmp_base"
    fi
  done < <(collect_files)

  {
    echo "# file_length_baseline.txt — снимок file-length гейта. Генерируется --generate, НЕ руками."
    echo "# Формат: <lines>:<path> (path в нотации git ls-files от repo-root)."
    echo "# [baseline] тает по мере сплитов (--tighten опускает снимки); файл проходит при size <= снимок+${BASELINE_HEADROOM}."
    echo "# [exemption] не тает (швов нет); лимит = снимок+${EXEMPTION_HEADROOM}, рост сверх — пере-ревью в PR."
    echo "# Вне обоих списков (в т.ч. любой новый файл) — hard ${MAX_LINES_PROD}/${MAX_LINES_TESTS}."
    echo "[baseline]"
    LC_ALL=C sort -t: -k2 "$tmp_base"
    echo "[exemption]"
    LC_ALL=C sort -t: -k2 "$tmp_exempt"
  } >"$BASELINE"

  rm -f "$tmp_base" "$tmp_exempt"
  n_base=$(grep -c '^[0-9]' "$BASELINE")
  # Число ПРОСМОТРЕННЫХ, а не только записей снимка (F6). «0 записей» читается и
  # как «нарушений нет», и как «гейт не видит код» — ровно та развилка, против
  # которой написан §6, и узнать ответ можно было только вторым запуском в
  # check-режиме. Брат `check_grep_gate.sh` печатает просмотренное с cqg@1.37,
  # здесь и в гейте сложности остался нетронутым: 2 счётчика из 4.
  echo "${green}baseline пересобран${reset}: $BASELINE — просмотрено ${n_seen} файл(ов), записей ${n_base}"
  if (( n_seen == 0 )); then
    # Диагноз называет то, что читает ИМЕННО этот гейт: маски `LINT_LENGTH_GLOBS`
    # и отслеживаемость файлов (`git ls-files` не видит неотслеженное). Первая
    # редакция этой подсказки посылала проверять `LINT_PY_SRC`, которого гейт не
    # читает вовсе, — поймано классовым оракулом «потребители LINT_PY_SRC», и это
    # ровно то, против чего написано правило «пропуск с чужим диагнозом хуже
    # красного гейта».
    printf '%s⚠ просмотрено 0 файлов — пустой снимок на непустом проекте значит\n' "$yellow"
    printf '«гейт смотрел не туда». Проверь маски LINT_LENGTH_GLOBS (сейчас: %s)\n' "$LENGTH_GLOBS"
    printf 'и что файлы отслеживаются git: `git ls-files` неотслеженное не видит.%s\n' "$reset"
  fi
  exit 0
fi

# --- проверка (и --tighten как её вариант) ----------------------------------

TIGHTEN=0
if [[ "${1:-}" == "--tighten" ]]; then
  TIGHTEN=1
  shift
  tmp_tight=$(mktemp)
fi

violations=0
tightened=0

check_file() {
  local file=$1
  local limit lines snap sect info allowed newsnap

  is_excluded "$file" && return
  [[ -f "$file" ]] || return

  limit=$(base_limit_for "$file")
  lines=$(wc -l <"$file" | tr -d ' ')

  info=$(baseline_lookup "$file")
  if [[ -n "$info" ]]; then
    snap=${info%%:*}
    sect=${info##*:}
    if [[ "$sect" == "exemption" ]]; then
      allowed=$((snap + EXEMPTION_HEADROOM))
    else
      allowed=$((snap + BASELINE_HEADROOM))
    fi
    # --tighten перезаписывает всю [baseline]-секцию из $tmp_tight, поэтому КАЖДАЯ
    # baseline-запись обязана сюда попасть — иначе неизменённые файлы молча выпадут.
    # Ратчет только ВНИЗ: снимок = min(строки, снимок); ужался <= лимита -> выпадает.
    if [[ "$TIGHTEN" == "1" && "$sect" == "baseline" ]]; then
      newsnap=$snap
      [[ "$lines" -lt "$snap" ]] && newsnap=$lines
      if [[ "$newsnap" -le "$limit" ]]; then
        tightened=$((tightened + 1))  # выпал из baseline (не пишем в tmp)
      else
        printf '%s:%s\n' "$newsnap" "$file" >>"$tmp_tight"
        [[ "$newsnap" -lt "$snap" ]] && tightened=$((tightened + 1))
      fi
      return
    fi
  else
    allowed=$limit
  fi

  if [[ "$lines" -gt "$allowed" ]]; then
    violations=$((violations + 1))
    if [[ "$STRICT" == "1" ]]; then
      printf '%s  ✗  %s: %d lines (> %d)%s\n' "$red" "$file" "$lines" "$allowed" "$reset"
    else
      printf '%s  ⚠  %s: %d lines (> %d)%s\n' "$yellow" "$file" "$lines" "$allowed" "$reset"
    fi
  fi
}

# --tighten перезаписывает всю [baseline]-секцию -> ОБЯЗАН видеть всё дерево, иначе
# усечёт baseline до переданных файлов. Обычная проверка уважает список файлов из хука.
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  scanned=$((scanned + 1))
  check_file "$f"
done < <(if [[ "$TIGHTEN" == "1" ]]; then collect_files; else collect_files "$@"; fi)

if [[ "$TIGHTEN" == "1" ]]; then
  # Перезаписать [baseline]-секцию ужатыми снимками, [exemption] оставить как есть.
  # Шапку и exemption-секцию ЧИТАЕМ ДО редиректа: '>"$BASELINE"' усекает файл до
  # выполнения блока, поэтому чтение из него внутри {} вернуло бы пусто.
  header=$(grep '^#' "$BASELINE")
  exempt_section=$(awk '/^\[exemption\]/{p=1} p' "$BASELINE")
  {
    printf '%s\n' "$header"
    echo "[baseline]"
    LC_ALL=C sort -t: -k2 "$tmp_tight" 2>/dev/null
    [[ -n "$exempt_section" ]] && echo "$exempt_section"
  } >"$BASELINE"
  rm -f "$tmp_tight"
  echo "${green}--tighten: обновлено снимков/выпало из baseline: $tightened${reset}"
  exit 0
fi

if [[ "$violations" -gt 0 ]]; then
  hdr=$([[ "$STRICT" == "1" ]] && printf '%sERROR%s' "$red" "$reset" || printf '%sWARNING%s' "$yellow" "$reset")
  printf '\n%s: %d файл(ов) превышают лимит длины (вне baseline/exemption).\n' "$hdr" "$violations"
  echo "Лимиты: prod ${MAX_LINES_PROD} / tests ${MAX_LINES_TESTS}. Новый код держим под лимитом;"
  echo "баг-фикс god-файла из baseline — ок до его сплита."
  [[ "$STRICT" == "1" ]] && exit 1
fi

# Счётчик просмотренного печатается ВСЕГДА (приёмка §6: «каждый гейт показал
# непустое число просканированных файлов»). Молчание на успехе неотличимо от
# «просканировано ноль» — найдено полем на lab-1 у grep-гейтов и на lab-2 здесь.
if (( scanned == 0 )); then
  printf '%sfile-length: 0 файлов просмотрено%s — проверь раскладку путей (§6)\n' \
    "$yellow" "$reset"
  exit 0
fi
# ⚠ МАСКА печатается вместе с числом (`cqg@1.88`). Число отвечает «сколько
# посмотрел», но не «на что смотрел вообще», и без второго доктор мог только
# ПОДОЗРЕВАТЬ частичную слепоту: «просмотрено 1 из 4» одинаково выглядит и при
# узкой маске, и при законном сужении области. С маской это перестаёт быть
# догадкой — расхождение СЧИТАЕТСЯ, а не предполагается.
printf '%sfile-length: OK%s — просмотрено %d файл(ов), в снимке %d, по маске: %s\n' \
  "$green" "$reset" "$scanned" "$baseline_n" "$LENGTH_GLOBS"
exit 0
