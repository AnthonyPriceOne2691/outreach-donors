#!/usr/bin/env bash
# Измеряющие половины гейта сложности: python (ruff) и ts (eslint).
#
# Часть `check_complexity_gate.sh` (`cqg@1.85`) — гейт разрезан по планке 300
# строк (Delivery §9.1a п.5). Здесь только СЧЁТ: сколько функций нарушают пороги
# и сколько файлов при этом просмотрено. Пороги, снимок, вердикт и печать
# остались во входном скрипте — резать надо по вопросу, а не по размеру.
#
# Подключается через `source`; переменные (`PY_SRC`, `TS_SRC`, `RUFF`, `ESLINT`,
# `py_live`, `ts_live`, `RULES`, `TS_RULES`) задаёт вход до вызова функций.
# Самостоятельно файл не запускается: гейт — это `check_*`, а это его часть.

# Просмотренное считается по ОБЕИМ живым половинам. «Просмотрено N» — это
# доказательство §6, и на fullstack-репо оно обязано складываться: иначе число
# врёт в меньшую сторону ровно там, где вторая половина как раз работает.
count_seen() {
  local n=0 half
  if (( py_live )); then
    half=$(git ls-files -- "$PY_SRC" 2>/dev/null | grep -cE "${LINT_SRC_EXT_RE:-\.py$}") || true
    n=$(( n + ${half:-0} ))
  fi
  if (( ts_live )); then
    half=$(git ls-files -- "$TS_SRC" 2>/dev/null \
             | grep -cE "${LINT_TS_EXT_RE:-\.(ts|tsx|js|jsx|mts|cts)$}") || true
    n=$(( n + ${half:-0} ))
  fi
  printf '%d' "$n"
}

py_counts() {
  local out rc
  out=$("$RUFF" check --no-cache --quiet --output-format=concise \
          --select "$RULES" --config 'lint.ignore=[]' -- "$PY_SRC" 2>"$errf")
  rc=$?
  if (( rc > 1 )); then
    printf '%sERROR%s: ruff вышел с кодом %d — снимок НЕ снят и старый не тронут.\n' \
      "$red" "$reset" "$rc" >&2
    sed 's/^/  ruff: /' "$errf" >&2
    printf 'Пустой снимок при живом ruff неотличим от «нарушений нет» (находка 8:\n' >&2
    printf '1 прогон из 5 дал 0 находок при факте 1). Починить вызов, потом снимать.\n' >&2
    return 2
  fi
  printf '%s\n' "$out" \
    | awk -F: 'NF>=4 { c[$1]++ } END { for (f in c) printf "%d:%s\n", c[f], f }' \
    | sort -t: -k2
}

# TS-половина. Три её свойства ЗАМЕРЕНЫ, и каждое иначе стоило бы тихого зелёного.
#
# 1. Правила НАВЯЗЫВАЮТСЯ флагом `--rule`, а не берутся из конфига проекта. Замер:
#    проект, поставивший себе `complexity: off`, гейтом всё равно ловится. Это тот
#    же смысл, что `lint.ignore=[]` у ruff-половины: порог принадлежит гейту.
#    Конфиг проекта при этом НУЖЕН — он даёт парсер; гейт даёт пороги.
# 2. Ошибка РАЗБОРА неотличима от находки по коду возврата: eslint без парсера
#    TypeScript печатает «Parsing error» и выходит ЕДИНИЦЕЙ — ровно как при
#    настоящем нарушении. Дискриминатор только в JSON: `fatal: true` /
#    `ruleId: null`. Такой файл НЕ проанализирован, и засчитать его за «ноль
#    нарушений» — это выдать непроверенное за чистое (класс F17).
# 3. JSON читается НЕСТРОГО (`strict=False`): сообщение об ошибке разбора несёт
#    сырой управляющий символ, и строгий `json.loads` падает трейсбеком ровно на
#    том случае, который гейт обязан диагностировать.
# 4. Считаются ТОЛЬКО правила гейта. Замер: полевой прогон дал 5 нарушений при 4
#    настоящих — пятым пришёл `@typescript-eslint/no-non-null-assertion`, правило
#    ПРОЕКТА из его же конфига (канонный `eslint.config.js` ставит его `warn`).
#    Флаг `--rule` навязывает пороги гейта, но не глушит остальные, поэтому снимок
#    «сложности» смешивался с линтерным долгом проекта и рос от правок, к сложности
#    отношения не имеющих: ратчет краснел бы на чужой находке и чинился бы
#    пересъёмом вверх (`cqg@2.04`).

# Конфиг eslint ищется ПО КАНДИДАТАМ — тем же приёмом, каким четыре гейта ищут venv
# (находка 5 первого развёртывания). Причина замерена: flat-config разрешается от
# CWD, а §5.0 кладёт фронтовый конфиг в ПОДКАТАЛОГ (`<frontend>/eslint.config.js`),
# и гейт, звавший eslint из корня репозитория, получал exit 2 «ESLint couldn't find
# an eslint.config.(js|mjs|cjs) file». Переопределяется `LINT_ESLINT_CONFIG` (§6).
eslint_config() {
  local c
  for c in "${LINT_ESLINT_CONFIG:-}" "$FE_DIR/eslint.config.js" \
           "$FE_DIR/eslint.config.mjs" "$FE_DIR/eslint.config.cjs" \
           eslint.config.js eslint.config.mjs eslint.config.cjs; do
    [[ -n "$c" && -f "$c" ]] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# eslint запускается ИЗ КАТАЛОГА КОНФИГА, а не с `--config` из корня, и это тоже
# замер: канонный конфиг объявляет `files: ['src/**/*.{ts,tsx}']`, а flat-config
# сверяет такие маски с CWD. `--config frontend/eslint.config.js` из корня дал бы
# НОЛЬ подходящих файлов — то есть зелёное на непросмотренном (класс F17), что хуже
# честного exit 2. Цель и инструмент передаются абсолютными путями: cwd меняется.
ts_eslint() {
  local cfg dir
  cfg=$(eslint_config) || cfg=""
  dir="."
  [[ -n "$cfg" ]] && dir=$(dirname "$cfg")
  ( cd "$dir" || exit 2; "$ESLINT_ABS" "$TS_ABS" "$@" )
}

ts_counts() {
  local out rc body prc
  out=$(ts_eslint --format json --no-color --rule "$TS_RULES" 2>"$errf")
  rc=$?
  if (( rc > 1 )); then
    printf '%sERROR%s: eslint вышел с кодом %d — снимок НЕ снят и старый не тронут.\n' \
      "$red" "$reset" "$rc" >&2
    sed 's/^/  eslint: /' "$errf" >&2
    printf 'Код 2 у eslint — это ошибка конфига или CLI, а не находки.\n' >&2
    # Причина НАЗЫВАЕТСЯ вместе с переменной, которой правится: чужие слова
    # («couldn't find eslint.config») отправляют чинить не туда — искать конфиг,
    # который лежит на месте, просто не там, откуда звали.
    if ! eslint_config >/dev/null; then
      printf 'Конфига eslint не найдено. Искал: %s/eslint.config.{js,mjs,cjs} и то же\n' \
        "$FE_DIR" >&2
      printf 'в корне репо. Правится LINT_ESLINT_CONFIG или LINT_FE_DIR (§6).\n' >&2
    fi
    return 2
  fi
  body=$(printf '%s' "$out" | TS_RULES="$TS_RULES" python3 -c '
import json, os, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw, strict=False)
except Exception:
    sys.stderr.write("вывод eslint не разобран как JSON\n")
    raise SystemExit(3)
# Правила ГЕЙТА, и считаются только они (свойство 4 в шапке). Пустой набор — не
# фильтровать: порог задан проектом через LINT_TS_COMPLEXITY_RULES и разобрать его
# нечем, а тихо обнулить счёт значило бы выдать непроверенное за чистое.
try:
    own = set(json.loads(os.environ.get("TS_RULES") or "{}"))
except Exception:
    own = set()
root = os.getcwd().rstrip(os.sep) + os.sep
counts, fatal = {}, []
for entry in data:
    path = entry.get("filePath", "")
    if path.startswith(root):
        path = path[len(root):]
    for m in entry.get("messages", []):
        if m.get("fatal") or m.get("ruleId") is None:
            head = (str(m.get("message", "")).splitlines() or [""])[0]
            fatal.append(path + ": " + head)
            continue
        if own and m.get("ruleId") not in own:
            continue          # правило проекта, не порог гейта
        counts[path] = counts.get(path, 0) + 1
if fatal:
    sys.stderr.write("файлы НЕ разобраны — это не «ноль нарушений»:\n")
    for line in fatal[:5]:
        sys.stderr.write("  " + line + "\n")
    raise SystemExit(4)
for p in sorted(counts):
    sys.stdout.write("%d:%s\n" % (counts[p], p))
' 2>>"$errf")
  prc=$?
  if (( prc != 0 )); then
    printf '%sERROR%s: TS-половина НЕ проверена — снимок не снят и старый не тронут.\n' \
      "$red" "$reset" >&2
    sed 's/^/  eslint: /' "$errf" >&2
    printf 'Нет парсера TS в конфиге проекта? Гейт берёт оттуда разбор, а пороги\n' >&2
    printf 'навязывает сам. Починить конфиг, потом снимать (§3.2b).\n' >&2
    return 2
  fi
  [[ -n "$body" ]] && printf '%s\n' "$body"
  return 0
}

# Обе половины — в ОДИН снимок и один формат. Порядок фиксируем сортировкой,
# иначе сверка двух прогонов в `--generate` ловила бы перестановку как расхождение.
current_counts() {
  local acc="" half
  if (( py_live )); then
    acc=$(py_counts) || return 2
  fi
  if (( ts_live )); then
    half=$(ts_counts) || return 2
    acc="$acc${acc:+$'\n'}$half"
  fi
  [[ -n "$acc" ]] && printf '%s\n' "$acc" | sort -t: -k2
  return 0
}
