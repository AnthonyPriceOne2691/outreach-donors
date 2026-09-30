#!/usr/bin/env bash
# TS-половина мутационного гейта: Stryker вместо mutmut, вопрос тот же.
#
# Часть `check_mutation_gate.sh` (`cqg@1.86`) — гейт разрезан по планке 300 строк
# (Delivery §9.1a п.5). Половина вынесена целиком, потому что это ПОДСТАНОВКА
# инструмента в ту же строку каталога §3: своего снимка и порога у неё нет, и
# бюджет §9.1a она не трогает.
#
# Подключается через `source`; `FE_DIR`, `TS_SRC`, `MIN_KILLED`, `TIMEOUT_BIN` и
# палитру задаёт вход. Самостоятельно не запускается — гейт это `check_*`.

# --- TS-половина: тот же вопрос, другой инструмент ---------------------------
# Роль «тесты хоть что-то утверждают» на TS закрывает Stryker. Это ПОДСТАНОВКА
# инструмента в существующую строку каталога §3, а не новый гейт: снимка у роли
# нет вовсе, порог один (`MIN_KILLED`) и сообщение одно — значит строка таблицы
# остаётся одна, и бюджет §9.1a не трогается (единица счёта — строка, §3).
#
# Замер, ради которого ветка и написана (Stryker 9.6.1 + vitest 4.1, 24 мутанта):
#   утверждения на месте → убито 20 из 24 (83%)
#   утверждения ВЫРЕЗАНЫ → убито 0 из 24, и `vitest run` при этом ЗЕЛЁНЫЙ (7 passed)
# Обе строки — с кодом возврата Stryker РАВНЫМ НУЛЮ.
#
# ⚠ Отсюда несущее правило ветки: код возврата Stryker вердиктом НЕ является.
# Он ноль и при 0% убитых, потому что `thresholds.break` по умолчанию не задан.
# Судить можно только по отчёту — ровно как python-половина судит по счётчикам
# mutmut, а не по его коду возврата.
FE_DIR=${LINT_FE_DIR:-frontend}
TS_SRC=${LINT_TS_SRC:-$FE_DIR/src}

# Есть ли чем и что мутировать. Наружу уходят три имени — `STRYKER`, `changed`,
# `n`; они локальны у `ts_mutation`, и помощник видит их динамической областью
# bash — тем же приёмом, которым `undecided` в `mutation_verdict.sh` читает `out`
# и `rc`.
#
# ⚠ Код возврата тут — РЕШЕНИЕ, а не вердикт гейта: 1 значит «пропуск уже назван
# вслух, судить нечем», и выход остаётся у вызывающего (`|| return 0`). Возврат
# нулём означал бы «продолжай», то есть прогон Stryker'а без Stryker'а.
ts_stryker_setup() {
  STRYKER="$FE_DIR/node_modules/.bin/stryker"
  [[ -x "$STRYKER" ]] || STRYKER="node_modules/.bin/stryker"
  [[ -x "$STRYKER" ]] || STRYKER=$(command -v stryker 2>/dev/null || true)
  if [[ -z "$STRYKER" ]]; then
    printf '%s⚠ mutation: Stryker не найден — TS-половина пропущена.\n' "$yellow"
    printf 'Установка: npm i -D @stryker-mutator/core @stryker-mutator/<runner>-runner%s\n' "$reset"
    return 1
  fi
  if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
    printf '%s⚠ mutation: ref %s недоступен — TS-половина пропущена%s\n' "$yellow" "$BASE" "$reset"
    return 1
  fi
  # Тесты из населения исключаются: мутировать надо продуктовый код. Маска шире
  # дефолта Stryker намеренно — проекты зовут каталог и `test`, и `tests`, и `__tests__`.
  changed=$(git diff --name-only "$BASE"...HEAD -- "$TS_SRC" 2>/dev/null \
    | grep -E '\.(ts|tsx|js|jsx|mts|cts)$' \
    | grep -vE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.' || true)
  if [[ -z "$changed" ]]; then
    printf '%smutation: изменённых prod-файлов нет (BASE=%s, смотрел в %s)%s\n' \
      "$green" "$BASE" "$TS_SRC" "$reset"
    return 1
  fi
  n=$(printf '%s\n' "$changed" | wc -l | tr -d ' ')
  printf 'mutation/ts: %s файл(ов), бюджет %ss, цель ≥%s%% убитых\n' "$n" "$BUDGET_SEC" "$MIN_KILLED"
  return 0
}

# Чем запускать Stryker: конфиг проекта либо единственный установленный раннер.
# Наружу уходят два имени — `cfg` и `runner`; `runners` за границу не выходит и
# потому объявлен здесь. Возврат 1 — «пропуск назван, дальше не судим».
#
# Конфиг проекта, если он есть, Stryker находит сам — тогда гейт добавляет только
# область и репортёр. Если конфига нет, нужен раннер, и угадывать его нельзя:
# берём тот, что реально установлен, и только когда он ОДИН. Два раннера — это
# выбор проекта, а не гейта; ноль — честный пропуск с командой установки.
ts_stryker_config() {
  local runners
  cfg=""
  for c in stryker.config.json stryker.config.mjs stryker.config.cjs stryker.config.js \
           .stryker.conf.json .stryker.conf.js "$FE_DIR/stryker.config.json"; do
    [[ -f "$c" ]] && { cfg="$c"; break; }
  done
  runner=""
  if [[ -n "$cfg" ]]; then
    return 0
  fi
  runners=$(ls -d "$FE_DIR"/node_modules/@stryker-mutator/*-runner node_modules/@stryker-mutator/*-runner \
              2>/dev/null | sed -E 's#.*/([a-z0-9]+)-runner$#\1#' | sort -u)
  if [[ -z "$runners" ]]; then
    printf '%s⚠ mutation: ни конфига Stryker, ни раннера — TS-половина не судит.\n' "$yellow"
    printf 'Поставь раннер под свой сьют: npm i -D @stryker-mutator/vitest-runner%s\n' "$reset"
    return 1
  fi
  if [[ $(printf '%s\n' "$runners" | wc -l | tr -d ' ') -gt 1 ]]; then
    printf '%s⚠ mutation: раннеров несколько (%s) — какой брать, решает ПРОЕКТ.\n' \
      "$yellow" "$(printf '%s' "$runners" | tr '\n' ',')"
    printf 'Заведи stryker.config.json с полем testRunner — гейт его подхватит.%s\n' "$reset"
    return 1
  fi
  runner="$runners"
  return 0
}

ts_mutation() {
  local STRYKER changed n cfg runner out rc report score
  ts_stryker_setup || return 0
  ts_stryker_config || return 0

  out=$(mktemp)
  # Бюджет держим тем же способом, что python-половина: 124 от timeout — это
  # НЕПОЛНЫЕ данные, а не «зелено».
  if [[ -n "$TIMEOUT_BIN" ]]; then
    "$TIMEOUT_BIN" "$BUDGET_SEC" "$STRYKER" run ${cfg:+"$cfg"} ${runner:+--testRunner "$runner"} \
      --mutate "$(printf '%s' "$changed" | tr '\n' ',')" --reporters json >"$out" 2>&1
  else
    "$STRYKER" run ${cfg:+"$cfg"} ${runner:+--testRunner "$runner"} \
      --mutate "$(printf '%s' "$changed" | tr '\n' ',')" --reporters json >"$out" 2>&1
  fi
  rc=$?
  if (( rc == 124 )); then
    printf '%s⚠ mutation: бюджет %ss исчерпан — данные НЕПОЛНЫЕ, гейт не судит.\n' "$yellow" "$BUDGET_SEC"
    printf 'Это не «зелено»: часть мутантов не проверена. Сузь дифф или подними BUDGET_SEC.%s\n' "$reset"
    rm -f "$out"; return 0
  fi

  # Путь к отчёту берём из СЛОВ САМОГО Stryker'а, а не из своего представления о
  # дефолте: замер дал `reports/mutation/mutation.json`, а конфиг проекта волен
  # положить его куда угодно. Зашитый путь молча читал бы вчерашний отчёт.
  report=$(grep -oE 'file://[^ ]*\.json' "$out" | tail -1 | sed 's#^file://##')
  [[ -n "$report" && -f "$report" ]] || report="${MUTATION_TS_REPORT:-reports/mutation/mutation.json}"
  if [[ ! -f "$report" ]]; then
    printf '%s⚠ mutation: Stryker отработал, но отчёт не найден — гейт НЕ судит.%s\n' "$yellow" "$reset"
    tail -5 "$out" >&2; rm -f "$out"; return 0
  fi

  score=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.stderr.write("отчёт Stryker не разобран\n"); raise SystemExit(3)
det = und = 0
for info in d.get("files", {}).values():
    for m in info.get("mutants", []):
        s = m.get("status")
        if s in ("Killed", "Timeout"):
            det += 1
        elif s in ("Survived", "NoCoverage"):
            und += 1
total = det + und
if total == 0:
    sys.stderr.write("мутанты не сгенерированы\n"); raise SystemExit(4)
sys.stdout.write("%d %d %d" % (det, total, 100 * det // total))
' "$report" 2>&1)
  if [[ ! "$score" =~ ^[0-9]+\ [0-9]+\ [0-9]+$ ]]; then
    printf '%s⚠ mutation: отчёт есть, а вердикта из него нет — гейт НЕ судит: %s%s\n' \
      "$yellow" "$score" "$reset"
    rm -f "$out"; return 0
  fi
  rm -f "$out"
  set -- $score
  printf 'mutation/ts: убито %s из %s (%s%%), порог %s%%\n' "$1" "$2" "$3" "$MIN_KILLED"
  if (( $3 < MIN_KILLED )); then
    printf '\n%sERROR%s: тесты не убивают мутантов изменённого кода — они его не проверяют.\n' \
      "$red" "$reset" >&2
    printf 'Выживший мутант = поведение, которое сьют пропустил бы в проде.\n' >&2
    printf 'Кто выжил: %s\n' "$report" >&2
    [[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; return 0; }
    return 1
  fi
  printf '%smutation/ts: OK%s\n' "$green" "$reset"
  return 0
}
