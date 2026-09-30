#!/usr/bin/env bash
# Ратчет по уязвимым зависимостям: pip-audit (backend) + npm audit (frontend).
#
# Зачем. Secrets в git мы ловим (§2.7), а уязвимую библиотеку — нет: целый класс
# был невидим. При этом это самый дешёвый баг из всех: он уже найден, описан и
# исправлен кем-то другим, нужно лишь обновиться.
#
# Механика — как у остальных: снимок текущего числа по severity, снимок только
# ТАЕТ. Плюс жёсткое правило поверх ратчета: НОВЫЕ critical/high не легализуются
# снимком вообще (см. HARD_FAIL_SEVERITY) — иначе «зафиксировали и забыли».
#
# Настройка (env):
#   LINT_BE_DIR, LINT_FE_DIR, LINT_VENV
#   HARD_FAIL_SEVERITY=1  — новые critical/high роняют даже при непустом снимке (дефолт)
#   STRICT=0              — soft
#
# Режимы:
#   check_deps_audit.sh             # проверка
#   check_deps_audit.sh --generate  # пере-снять baseline
#
# Нет pip-audit / npm — мягкий пропуск с указанием, как поставить: гейт не должен
# ронять коммит из-за отсутствия инструмента, но и молчать про пропуск не должен.

set -uo pipefail

STRICT=${STRICT:-1}
HARD_FAIL_SEVERITY=${HARD_FAIL_SEVERITY:-1}
BE_DIR=${LINT_BE_DIR:-backend}
FE_DIR=${LINT_FE_DIR:-frontend}
VENV=${LINT_VENV:-$BE_DIR/.venv}
# Значение, данное «относительно backend» (так его описывала §6 до cqg@1.33),
# тоже принимается: одно написание не могло удовлетворить обе трактовки, и
# документированное `.venv` глушило этот гейт мягким пропуском (lab-9 №7).
[[ -d "$VENV" || ! -d "$BE_DIR/$VENV" ]] || VENV="$BE_DIR/$VENV"
GENERATE=0
[[ "${1:-}" == "--generate" ]] && GENERATE=1

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASELINE="$SCRIPT_DIR/deps_audit_baseline.txt"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

skipped=()
py_high=0; py_total=0; js_crit=0; js_high=0; js_total=0
# «Половина гейта не проверена» — отдельное состояние от «проверена, нашла ноль».
# Без него ноль читается как доказательство, которого не было.
py_unchecked=0; js_unchecked=0

# Страховка КЛАССА, а не случая: любое значение, попадающее в арифметику, сначала
# приводится к числу. Сырая ошибка `((: 0\n0: syntax error` появилась ровно потому,
# что в `(( ))` уехало то, что числом не было; следующая смена формата вывода любого
# инструмента сделала бы то же самое в другом месте.
num() { local v=${1//[!0-9]/}; printf '%s' "${v:-0}"; }

# --- backend: pip-audit -------------------------------------------------------
PIP_AUDIT="$VENV/bin/pip-audit"
[[ -x "$PIP_AUDIT" ]] || PIP_AUDIT=$(command -v pip-audit 2>/dev/null || true)
# ⚠ Применимость судится по МАНИФЕСТУ проекта, а не по наличию каталога, и
# разница не косметическая: джоба §8.3 сама ставит оснастку гейтов в
# `$BE_DIR/.venv` и тем СОЗДАЁТ `$BE_DIR`. Охрана `-d "$BE_DIR"` после этого
# истинна на проекте, где питона нет вовсе, и `pip-audit` аудитит не продукт, а
# собственную оснастку контура. Замер portfolio-site 07.08: `py_total: 6 (было
# 0)`, джоба упала — гейт измерил сам себя и предъявил счёт проекту.
# Каталог создаёт соседний шаг того же workflow; манифест — не создаёт никто.
# js-половина ниже так устроена с самого начала (`-f package-lock.json`):
# расходились не принципы, а одна из двух половин.
py_manifest=""
for cand in "$BE_DIR/pyproject.toml" "$BE_DIR/requirements.txt" "$BE_DIR/setup.py" \
            "$BE_DIR/setup.cfg" "$BE_DIR/Pipfile" \
            pyproject.toml requirements.txt setup.py setup.cfg Pipfile; do
  [[ -f "$cand" ]] && { py_manifest="$cand"; break; }
done
if [[ -n "$PIP_AUDIT" && -n "$py_manifest" ]]; then
  # pip-audit не различает severity в базовом выводе, поэтому считаем уязвимости:
  # число записей = число (пакет, CVE). Точнее и без парсинга HTML.
  #
  # ⚠ Запуск и разбор РАЗДЕЛЕНЫ, и это не стиль. Прежняя форма — пайплайн с
  # `|| echo 0` под `set -o pipefail` — давала ДВА числа: pip-audit, не достучавшийся
  # до базы уязвимостей, выходит 1, python на пустом stdin печатает 0, а затем
  # срабатывает `|| echo 0`, потому что ненулевой статус у ПАЙПЛАЙНА. `py_total`
  # становился "0\n0": `(( now > was ))` роняло сырую ошибку арифметики, а
  # `--generate` писал снимок в две строки (полевая находка lab-9 №6).
  #
  # Различать «упал» и «нашёл ноль» по коду возврата НЕЛЬЗЯ: pip-audit выходит
  # ненулём и когда честно нашёл уязвимости. Признак — разобрался ли JSON.
  pa_json=$("$PIP_AUDIT" --format=json --progress-spinner=off 2>/dev/null || true)
  py_total=$(printf '%s' "$pa_json" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit(1)
deps=d.get("dependencies", d if isinstance(d,list) else [])
print(sum(len(x.get("vulns",[])) for x in deps))' 2>/dev/null) || py_total=""
  if [[ -z "$py_total" ]]; then
    # Инструмент ЕСТЬ и не смог проверить. Ноль здесь был бы зелёным на
    # непроверенном — тот же класс, что F17 («OK на стеке без манифеста»),
    # закрытый тогда только для случая «манифеста нет», а не для класса.
    skipped+=("pip-audit запустился, но результат не разобран (сеть? база уязвимостей?) — python-половина НЕ проверена")
    py_unchecked=1
    py_total=0
  fi
else
  # Две разные причины — два разных совета. Слитые в одну строку, они советовали
  # ставить pip-audit проекту, у которого питона нет: совет невыполним, а значит
  # его не выполнят, и пропуск станет привычным (тот же класс, что рутинный waiver).
  if [[ -z "$PIP_AUDIT" ]]; then
    skipped+=("pip-audit (установка: pip install pip-audit)")
  else
    skipped+=("python-манифеста нет ни в $BE_DIR/, ни в корне — python-половина не применима")
  fi
  py_unchecked=1
fi

# --- frontend: npm audit ------------------------------------------------------
if command -v npm >/dev/null 2>&1 && [[ -f "$FE_DIR/package-lock.json" ]]; then
  read -r js_crit js_high js_total < <(
    npm audit --json --prefix "$FE_DIR" 2>/dev/null \
      | python3 -c 'import json,sys
try: v=json.load(sys.stdin).get("metadata",{}).get("vulnerabilities",{})
except Exception: v={}
print(v.get("critical",0), v.get("high",0), sum(int(x) for x in v.values() if str(x).isdigit()))' 2>/dev/null \
      || echo "0 0 0"
  )
else
  skipped+=("npm audit (нет npm или $FE_DIR/package-lock.json)")
  js_unchecked=1
fi

# Приводим к числу ДО арифметики и до записи в снимок — см. `num()` выше.
py_total=$(num "$py_total"); js_crit=$(num "$js_crit")
js_high=$(num "$js_high");  js_total=$(num "$js_total")

current="py_total=$py_total js_critical=$js_crit js_high=$js_high js_total=$js_total"
# Сколько манифестов реально проверено — нужно ОБЕИМ веткам вывода.
checked=$(( (1 - py_unchecked) + (1 - js_unchecked) ))

if (( GENERATE )); then
  {
    echo "# deps_audit_baseline.txt — снимок известных уязвимостей в зависимостях."
    echo "# Снимок только ТАЕТ: обновил либу — пере-сними вниз."
    echo "# НОВЫЕ critical/high не легализуются снимком (HARD_FAIL_SEVERITY=1)."
    echo "$current"
  } >"$BASELINE"
  printf '%sbaseline пересобран:%s %s\n' "$green" "$reset" "$current"
  exit 0
fi

get_base() { # $1 = ключ
  [[ -f "$BASELINE" ]] || { echo 0; return; }
  sed -n "s/.*$1=\([0-9]*\).*/\1/p" "$BASELINE" | head -1 | grep -E '^[0-9]+$' || echo 0
}

fails=0
for pair in "py_total:$py_total" "js_critical:$js_crit" "js_high:$js_high" "js_total:$js_total"; do
  key=${pair%%:*}; now=${pair##*:}
  was=$(get_base "$key")
  if (( now > was )); then
    printf '%s  ✗  %s: %d (было %d)%s\n' "$red" "$key" "$now" "$was" "$reset"
    fails=$((fails + 1))
  fi
done

# Жёсткое правило поверх ратчета: critical/high не живут в снимке долго.
hard=0
if [[ "$HARD_FAIL_SEVERITY" == "1" ]] && (( js_crit > 0 || js_high > 0 )); then
  printf '%s  ✗  критические/высокие уязвимости: critical=%d high=%d — снимком не легализуются%s\n' \
    "$red" "$js_crit" "$js_high" "$reset"
  hard=1
fi

if (( ${#skipped[@]} )); then
  for s in "${skipped[@]}"; do printf '  ○ пропущено: %s\n' "$s"; done
fi

if (( fails == 0 && hard == 0 )); then
  # Ни одного поддерживаемого манифеста — это НЕ «уязвимостей нет», а «нечего было
  # аудировать». На Swift/SPM-проекте с двумя настоящими зависимостями гейт
  # печатал «OK — py_total=0 js_critical=0», то есть врал зелёным (полевая находка
  # lab-3). Образец правильного поведения лежит рядом: jscpd в такой ситуации
  # честно говорит «инструмента нет, гейт пропущен».
  if [[ ! -f pyproject.toml && ! -f requirements.txt && ! -f package.json ]]; then
    printf '%sdeps-audit: WARNING%s — не найдено ни одного поддерживаемого манифеста '\
'(pyproject/requirements/package.json). Зависимости ЭТОГО стека не проверены: для\n'\
'SPM/Cargo/Go роль «уязвимые зависимости» занимает другой инструмент — впиши его\n'\
'в карту ролей (§Применимость).\n' "$yellow" "$reset"
    exit 0
  fi
  # Половина гейта не проверена — «OK» тут было бы тем же зелёным на непроверенном,
  # против которого написан §6. Числа печатаем, но словом «OK» их не называем.
  if (( py_unchecked || js_unchecked )); then
    # Число проверенных манифестов печатается и ЗДЕСЬ. Ветка «проверено не всё»
    # — самая частая на чужом стеке (одна половина всегда чужая), и без числа
    # доктор честно отвечал «сканирующий ли гейт — отсюда не видно». Счётчик
    # нужен там, где половина работает, не меньше чем там, где обе.
    printf '%sdeps-audit: WARNING%s — просмотрено %d манифест(ов), проверено НЕ ВСЁ: %s\n' \
      "$yellow" "$reset" "$checked" "$current"
    (( py_unchecked )) && printf 'python-зависимости не проверены (см. «пропущено» выше).\n'
    (( js_unchecked )) && printf 'js-зависимости не проверены (см. «пропущено» выше).\n'
    printf 'Отметь непокрытость в verify-report.md — ноль здесь не доказательство.\n'
    exit 0
  fi
  # ⚠ «Просмотрено N» здесь считает МАНИФЕСТЫ, а не файлы кода, и это не
  # натяжка: гейт судит зависимости, значит его «что я смотрел» — это
  # `requirements/pyproject` и `package-lock`. Ноль проверенных манифестов при
  # нулях уязвимостей выглядит точно так же, как чистый проект, — тот же класс,
  # ради которого счётчик есть у остальных гейтов формы (§6). Последний `SKIP`
  # доктора закрывается именно здесь: без числа он честно отвечал «сканирующий
  # ли он — отсюда не видно».
  if (( checked == 0 )); then
    printf '%sdeps-audit: 0 манифестов просмотрено%s — ни одна половина не\n' \
      "$yellow" "$reset"
    printf 'проверена (см. причины выше): нули тут ничего не доказывают.\n'
    exit 0
  fi
  printf '%sdeps-audit: OK%s — просмотрено %d манифест(ов), %s\n' \
    "$green" "$reset" "$checked" "$current"
  exit 0
fi

printf '\n%sERROR%s: уязвимости в зависимостях выросли или есть critical/high.\n' "$red" "$reset" >&2
printf 'Починка: обновить пакет (npm audit fix / pip install -U), затем --generate.\n' >&2
printf 'Если апстрим не выпустил фикс — строка `deps_audit_waiver: reason=… by=human:…`\n' >&2
printf 'в delivery/active/STATUS.md (§4.3a Delivery: waiver виден в PR, живёт одну поставку).\n' >&2
# Требуются ИМЕННО `reason=` и `by=human:` (§4.3a Delivery). Раньше проверялось
# «значение не плейсхолдер», поэтому `deps_audit_waiver: no` СНИМАЛО security-гейт:
# строка выглядела как отказ, а работала как разрешение — при `critical=1 high=8`
# гейт выходил 0. Две сестры того же механизма (`human_ok_*`) значение `no`
# отбрасывают, здесь смысл был противоположный. Нашло приёмочное развёртывание.
if [[ -f delivery/active/STATUS.md ]] \
   && grep -qE '^[[:space:]]*[-*]?[[:space:]]*\**deps_audit_waiver\**[[:space:]]*:.*reason=.*by=human:' \
        delivery/active/STATUS.md; then
  printf '%sdeps-audit: разрешено waiver'"'"'ом из STATUS%s\n' "$yellow" "$reset" >&2
  exit 0
fi
[[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
exit 1
