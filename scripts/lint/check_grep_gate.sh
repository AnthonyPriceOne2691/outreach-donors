#!/usr/bin/env bash
# Grep-гейты для конвенций, у которых нет готового линтера.
#
# Механика — per-file baseline-ratchet: текущие (легаси) нарушения живут в снимке
# `<count>:<path>` и тают по мере правки файлов; файл ВНЕ снимка (в т.ч. любой новый)
# обязан иметь 0 нарушений. Ратчет только вниз: файл из baseline проходит при
# count <= снимок; --generate пере-снимает снимок вниз.
#
# Правила:
#   config-access      — getattr(config,...) / os.getenv / os.environ вне config/
#                        (опечатка в настройке должна падать, а не тихо дефолтиться)
#   di-indirection     — (pkg|session|ctx): Any + importlib.import_module("...") reach-back
#                        (DI вместо importlib-магии; Any в новых сигнатурах запрещён)
#   service-no-web     — импорт web-фреймворка (fastapi) в сервис-слое
#   no-grab-bag-module — файлы-помойки utils/misc/common/helpers.py без темы
#
# Настройка (env):
#   LINT_PY_SRC  — корневой каталог прод-Python для гейтов, от repo-root (дефолт: backend/features)
#
# Режимы:
#   check_grep_gate.sh --rule config-access             # проверка; exit 1 при регрессии
#   check_grep_gate.sh --rule config-access --generate  # пересобрать baseline правила
#   STRICT=0 check_grep_gate.sh --rule ...              # soft (warning, exit 0)

set -uo pipefail

STRICT=${STRICT:-1}
PY_SRC=${LINT_PY_SRC:-backend/features}
RULE=""
GENERATE=0
# --list-rules: скрипт сам объявляет свои правила. Нужен мета-гейту
# check_gate_coverage.sh: его гранулярность была «файл», и правило, не
# подключённое в конфиге, оставалось невидимым, пока хоть одно правило этого же
# скрипта упомянуто (полевая находка F7). Список — здесь, а не в мета-гейте:
# источник истины о правилах скрипта — сам скрипт.
LIST_RULES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rule) RULE="${2:-}"; shift 2 ;;
    --generate) GENERATE=1; shift ;;
    --list-rules) LIST_RULES=1; shift ;;
    *) shift ;;
  esac
done

if [[ "$LIST_RULES" == "1" ]]; then
  printf '%s\n' config-access di-indirection service-no-web no-grab-bag-module \
    blind-error unstructured-log
  exit 0
fi

# SCRIPT_DIR резолвим ДО cd в REPO_ROOT (BASH_SOURCE относителен cwd вызова).
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Токен-граница делается через (^|[^A-Za-z_.]) вместо \b: BSD grep (macOS) \b не держит.
# FILTER (опц.) — grep -E по путям: сузить правило до подмножества файлов по имени.
# EXCLUDE (опц.) — grep -vE по путям: вычесть область, в которой правило НЕ действует.
#
# EXCLUDE появился потому, что LABEL правила обещал «вне `config/`», а выразить это
# было нечем: FILTER умеет только сужать. Гейт краснел на самом `config.py` — то
# есть на файле, который правило разрешает по построению, и совпадение находилось
# в тексте его же docstring'а. Три независимых развёртывания нашли это по
# отдельности. Правило, чей LABEL называет исключение, обязано иметь EXCLUDE:
# расхождение обещания и выборки — ложное срабатывание по §4.3b.
FILTER=""
EXCLUDE=""
case "$RULE" in
  config-access)
    PATTERN='getattr\([[:space:]]*config|(^|[^A-Za-z_.])os\.getenv|(^|[^A-Za-z_.])os\.environ'
    BASELINE="$SCRIPT_DIR/config_access_baseline.txt"
    EXCLUDE='(^|/)config(\.py|/)'
    LABEL='config-access: getattr(config,...) / os.getenv вне config/'
    HINT='Читай настройку через типизированный config.X (опечатку ловит type-checker). Дефолт — один раз в config/.'
    ;;
  di-indirection)
    PATTERN='(^|[^A-Za-z_])(pkg|session|ctx)[[:space:]]*:[[:space:]]*Any|import_module\([[:space:]]*["'"'"']'
    BASELINE="$SCRIPT_DIR/di_indirection_baseline.txt"
    LABEL='di-indirection: (pkg|session|ctx): Any / importlib reach-back'
    HINT='Новый код: явные зависимости через параметры/Protocol, конкретные типы вместо Any.'
    ;;
  service-no-web)
    PATTERN='^[[:space:]]*(from fastapi|import fastapi)'
    FILTER='/services/|service\.py$'
    BASELINE="$SCRIPT_DIR/service_no_web_baseline.txt"
    LABEL='service-no-web: сервис-слой не импортирует web-фреймворк'
    HINT='HTTP-примитивы (Request/Depends/HTTPException) — в роутере. Сервис принимает id-параметры и бросает доменные исключения, не HTTP.'
    ;;
  no-grab-bag-module)
    # Любая строка = нарушение: правило имени файла, не содержимого. Новый
    # utils/misc/common/helpers.py (без темы) имеет count>0 -> hard fail; легаси в
    # baseline и не растёт. `_helpers.py` (с подчёркиванием) FILTER не матчит — разрешён.
    PATTERN='^'
    FILTER='/(utils|misc|common|helpers)\.py$'
    BASELINE="$SCRIPT_DIR/no_grab_bag_baseline.txt"
    LABEL='no-grab-bag-module: файлы-помойки utils/misc/common/helpers.py без темы запрещены'
    HINT='Имя модуля описывает ответственность: <topic>.py / <topic>_helpers.py / _helpers.py рядом с фичей.'
    ;;
  blind-error)
    # Сообщение об ошибке, которое не локализует проблему. Ловим два вида:
    #   raise Exception(...)            — тип не говорит ничего
    #   raise XError("короткий текст")  — константа без контекста (нет f-строки,
    #                                     нет .format, нет конкатенации)
    # Смысл: `silent-except` требует, чтобы ошибка не молчала; это правило требует,
    # чтобы она была ПОЛЕЗНОЙ. `raise ValueError("failed")` в проде = час чтения
    # логов вместо секунды. Годное сообщение содержит, ЧТО и НА ЧЁМ упало:
    #   raise ValueError(f"cannot parse feed url={url!r} status={code}")
    PATTERN='raise[[:space:]]+(Exception|BaseException)\(|raise[[:space:]]+[A-Za-z_]*(Error|Exception)\([\"'"'"'][^\"'"'"'{}]{0,24}[\"'"'"']\)'
    FILTER='\.py$'
    BASELINE="$SCRIPT_DIR/blind_error_baseline.txt"
    LABEL='blind-error: сообщение об ошибке без контекста не локализует проблему'
    HINT='Добавь ЧТО и НА ЧЁМ упало: raise ValueError(f"cannot parse feed url={url!r} status={code}"). Голый Exception замени доменным типом.'
    ;;
  unstructured-log)
    # §2.4a правило 2: лог обязан искаться ПО ПОЛЮ. `logger.info(f"feed {url} skipped")`
    # не ищется: значение вплавлено в текст, и запрос «все случаи по этому url»
    # приходится делать грепом по подстроке. Нужен контекст ключ-значением:
    #   logger.warning("feed skipped", extra={"url": url, "reason": "robots"})
    # Ловим два вида вплавления в вызове логгера: f-строку и .format(.
    # Это же — нижняя ступень лестницы наблюдаемости (Delivery §13.3): без
    # структурного лога post-merge локализация опирается на чтение простыни.
    #
    # print() сознательно НЕ ловим: в CLI и скриптах он законен, и правило стало
    # бы стабильно красным на легальном коде (§4.3b Delivery). Фронт закрыт
    # ESLint-правилом no-console через гейт eslint-warnings, а не здесь.
    # Имя логгера — тем же ПРИНЦИПОМ, что у брата `silent-except` (`ast_rules.py`,
    # `_LOGGER_WORDS`): имя оканчивается словом log/logger/logging, отделённым
    # началом или `_`, регистр не важен, цепочка `self.logger` узнаётся. Списком
    # имён правило не видело ни `LOG.info(f"…")`, ни `self.logger.info(f"…")` —
    # то есть молчало на самых частых формах. Общего кода у шелла с питоном нет,
    # поэтому согласие двух ответов держит ПРОГОН на общем списке форм
    # (`tests/test_silent_except_logger.py`), а не чтение: разъедутся — красное.
    # Регистр берётся классами, а не `-i`: флаг пришлось бы тянуть в общий
    # `count_hits`, то есть на все шесть правил разом.
    PATTERN='(^|[^A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*\.)*_*([A-Za-z0-9]+_)*[Ll][Oo][Gg]([Gg][Ee][Rr]|[Gg][Ii][Nn][Gg])?\.(debug|info|warning|warn|error|critical|exception)\([[:space:]]*(f["'"'"']|["'"'"'][^"'"'"']*["'"'"'][[:space:]]*\.format\()'
    FILTER='\.py$'
    BASELINE="$SCRIPT_DIR/unstructured_log_baseline.txt"
    LABEL='unstructured-log: значение вплавлено в текст лога вместо extra={...}'
    HINT='Контекст — ключ-значением: logger.warning("feed skipped", extra={"url": url, "reason": "robots", "req_id": req_id}). Иначе лог не ищется по полю (§2.4a).'
    ;;
  *)
    echo "unknown --rule: '${RULE}' (config-access|di-indirection|service-no-web|no-grab-bag-module|blind-error|unstructured-log)" >&2
    exit 2
    ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# Прод-.py под $PY_SRC, без тестов. git ls-files рекурсивен; пути — от repo-root.
list_targets() {
  local out
  # Расширение — настройка, не константа (см. LENGTH_GLOBS в check_file_length.sh
  # и находку lab-3): без этого ни один файл не-Python-стека в выборку не попадал,
  # какой бы паттерн правила ни задать. Дефолт прежний.
  out=$(git ls-files "$PY_SRC/" 2>/dev/null \
    | grep -E "${LINT_SRC_EXT_RE:-\.py\$}" \
    | grep -vE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.')
  [[ -n "${FILTER:-}" ]] && out=$(printf '%s\n' "$out" | grep -E "$FILTER")
  # `|| true`: grep -v без остатка выходит 1, и под `set -e` это уронило бы гейт на
  # проекте, где ВСЯ выборка попала в исключение — законный случай, не ошибка.
  [[ -n "${EXCLUDE:-}" ]] && out=$(printf '%s\n' "$out" | grep -vE "$EXCLUDE" || true)
  printf '%s\n' "$out"
}

# Число строк-нарушений в файле (grep -c всегда печатает число; 0 при отсутствии).
count_hits() { grep -cE "$PATTERN" "$1" 2>/dev/null; }

# Снимок для точного пути (awk-сравнение точной строки — корректно с пробелами в пути).
baseline_lookup() {
  [[ -f "$BASELINE" ]] || return 0
  awk -v p="$1" '
    /^[[:space:]]*#/ { next }
    /^[0-9]+:/ {
      n=$0; sub(/:.*/, "", n)
      path=$0; sub(/^[0-9]+:/, "", path)
      if (path == p) { print n; exit }
    }
  ' "$BASELINE"
}

# --- --generate: пересобрать baseline --------------------------------------
if [[ "$GENERATE" == "1" ]]; then
  tmp=$(mktemp)
  # ⚠ Число ПРОСМОТРЕННЫХ считается и в generate, не только в check. Без него
  # «(0 файлов)» читается двояко — «нарушений нет» и «гейт не видит код», — а это
  # ровно та развилка, против которой написан §6. На развёртывании 2026-07-31
  # пришлось запускать вторую команду в check-режиме, чтобы узнать, какое из двух;
  # у соседа (`check_ast_gate.py`) число в generate печаталось всегда, то есть
  # расхождение было между братьями, а не забывчивостью (класс F15).
  gen_scanned=0
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    gen_scanned=$((gen_scanned + 1))
    hits=$(count_hits "$f")
    [[ "$hits" -gt 0 ]] && printf '%s:%s\n' "$hits" "$f" >>"$tmp"
  done < <(list_targets)
  {
    echo "# ${BASELINE##*/} — снимок grep-гейта. Генерируется --generate, НЕ руками."
    echo "# Правило: ${LABEL}"
    echo "# Формат: <count>:<path> (path от repo-root)."
    echo "# Ратчет вниз: файл проходит при count <= снимок; файл ВНЕ снимка (новый) — hard 0."
    LC_ALL=C sort -t: -k2 "$tmp"
  } >"$BASELINE"
  n=$(grep -c '^[0-9]' "$BASELINE" 2>/dev/null) || true
  n=${n:-0}
  echo "${green}baseline пересобран${reset}: $BASELINE — просмотрено ${gen_scanned} файл(ов), в снимке ${n}, правило ${RULE}"
  if [[ "$gen_scanned" -eq 0 ]]; then
    printf '%s⚠ просмотрено 0 файлов — снимок пуст не потому, что нарушений нет,\n' "$yellow"
    printf 'а потому, что гейт не видит код. Проверь LINT_PY_SRC/FILTER (§6).%s\n' "$reset" >&2
  fi
  rm -f "$tmp"
  exit 0
fi

# --- проверка ---------------------------------------------------------------
# scanned считается ОБЯЗАТЕЛЬНО и печатается на успешном пути: без этого числа
# нельзя отличить «код чист» от «просканировано ноль файлов», а приёмка §6
# требует ровно этого («каждый гейт показал непустое число просканированных
# файлов»). Найдено развёртыванием: шесть правил давали exit 0 и пустой вывод,
# и чтобы понять, работают ли они, приходилось читать исходник и строить пробник.
# Образец правильного поведения был рядом — check_baseline_ratchet.sh печатает
# «0 снимков сверено».
violations=0
scanned=0
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  scanned=$((scanned + 1))
  hits=$(count_hits "$f")
  [[ "$hits" -gt 0 ]] || continue
  snap=$(baseline_lookup "$f")
  allowed=${snap:-0}
  if [[ "$hits" -gt "$allowed" ]]; then
    violations=$((violations + 1))
    if [[ "$STRICT" == "1" ]]; then
      printf '%s  ✗  %s: %d нарушений (разрешено %d)%s\n' "$red" "$f" "$hits" "$allowed" "$reset"
    else
      printf '%s  ⚠  %s: %d нарушений (разрешено %d)%s\n' "$yellow" "$f" "$hits" "$allowed" "$reset"
    fi
  fi
done < <(list_targets)

if [[ "$violations" -gt 0 ]]; then
  hdr=$([[ "$STRICT" == "1" ]] && printf '%sERROR%s' "$red" "$reset" || printf '%sWARNING%s' "$yellow" "$reset")
  printf '\n%s: %d файл(ов) нарушают правило %s (вне baseline или сверх снимка).\n' "$hdr" "$violations" "$RULE"
  printf '%s\n' "$HINT"
  echo "Легаси-файл из baseline — ок до его чистки; новый код держим на нуле. Пересъём вниз: --generate."
  [[ "$STRICT" == "1" ]] && exit 1
fi

# grep -c при нуле совпадений печатает 0 И выходит 1, поэтому `|| echo 0`
# дописывал второй ноль и printf падал с «invalid number». Та же ошибка была в
# ветке --generate ниже — исправлена там же.
snap_n=0
if [[ -f "$BASELINE" ]]; then
  snap_n=$(grep -c '^[0-9]' "$BASELINE" 2>/dev/null) || true
  snap_n=${snap_n:-0}
fi
# Гейт судит по `git ls-files`, то есть по ЗАКОММИЧЕННОМУ состоянию: правки в
# рабочем дереве он не видит. Само это верно (ратчет считает историю, а не черновик),
# но молчать нельзя — приёмочное развёртывание показало случай, где гейт печатал
# «OK», пока в области правила лежали незакоммиченные изменения, и это читалось как
# «код проверен». Тот же приём уже был у diff-coverage, здесь его не было.
dirty=$(git status --porcelain -- "$PY_SRC" 2>/dev/null | grep -cE '\.py|\.ts' || true)
dirty=${dirty:-0}
if (( dirty > 0 )); then
  printf '%s⚠ %s: в области правила %d незакоммиченных файл(ов) — гейт их НЕ смотрел%s\n' \
    "$yellow" "$RULE" "$dirty" "$reset"
fi
if [[ "$scanned" -eq 0 ]]; then
  # Ноль просмотренных файлов — не успех, а вопрос к раскладке путей (§6).
  # Это WARNING, а не ошибка: у правила с FILTER пустая выборка бывает законной
  # (нет сервис-слоя, нет фронта). Но молчать нельзя — молчание тут неотличимо
  # от работы.
  printf '%s%s: 0 файлов просмотрено%s — проверь LINT_PY_SRC/FILTER (§6): гейт, который ничего не видит, хуже красного\n' \
    "$yellow" "$RULE" "$reset"
  exit 0
fi
# Маска — корень и расширение выборки (§6): см. `check_file_length.sh` о том,
# зачем число без маски не даёт отличить узкую маску от узкой области.
printf '%s%s: OK%s — просмотрено %d файл(ов), в снимке %d, по маске: %s\n' \
  "$green" "$RULE" "$reset" "$scanned" "$snap_n" "$PY_SRC/**"
exit 0
