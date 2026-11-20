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
# АДАПТАЦИЯ ПРОЕКТА (аудит 20.11.2026, п. 35; объявлена в adapted.json). До неё одна
# строка `deps_audit_waiver: reason=… by=human:…` в delivery/active/STATUS.md
# выключала гейт целиком — любой рост, любые critical/high, python-половина тоже, —
# а npm audit, не достучавшийся до реестра, давал «0 0 0» и «OK». Теперь:
#
#   * waiver называет НАХОДКИ и ПОТОЛОК, одной строкой:
#       deps_audit_waiver: ghsa=GHSA-xxxx-xxxx-xxxx[,GHSA-…] max_critical=0 max_high=7 reason=… by=human:…
#     ghsa= — id находок через запятую без пробелов: GHSA-… (npm и PyPI), для
#     python-находки без GHSA — её PYSEC-…/CVE-… (сверяется с id и псевдонимами
#     pip-audit). max_critical=/max_high= — потолок числа critical/high в итоге
#     npm audit (пакеты, как их считает npm: уязвимый пакет и те, что его тянут).
#     ghsa= и max_* — до reason=, by=human: — в конце строки. Waiver покрывает
#     только critical/high npm и находки python; moderate/low судит снимок.
#   * неперечисленное идёт в ратчет и в HARD_FAIL_SEVERITY, как раньше; python-
#     находка вне waiver'а снимком не легализуется (severity pip-audit не даёт).
#   * инструмент не ответил (сеть, реестр, упал, ответ не разобран) или не стоит
#     при манифесте — половина «не проверена»: STRICT=1 — красное, STRICT=0 —
#     предупреждение.
#
#   Исход (код выхода при STRICT=1 / STRICT=0):
#     всё проверено, роста нет, critical/high нет                          0 / 0
#     critical/high вне waiver'а; python-находка вне waiver'а              1 / 0
#     все critical/high в waiver'е, итог npm в пределах max_*              0 / 0
#     итог npm выше max_critical/max_high                                  1 / 0
#     рост счётчика над снимком (moderate/low и неперечисленное)           1 / 0
#     waiver старого формата или с ошибкой — и снимать есть что            1 / 0
#     waiver старого формата, а снимать нечего                     0 (заметка) / 0
#     половина не проверена (инструмент не ответил или не стоит)           1 / 0
#     половина не применима (нет манифеста) — WARNING                      0 / 0
#   Разбор ответов и приговор — `deps_audit_verdict.py` рядом (часть гейта);
#   здесь — поиск инструментов и их запуск.
#
# Настройка (env):
#   LINT_BE_DIR, LINT_FE_DIR, LINT_VENV
#   HARD_FAIL_SEVERITY=1  — новые critical/high роняют даже при непустом снимке (дефолт)
#   STRICT=0              — soft
#
# Режимы:
#   check_deps_audit.sh             # проверка
#   check_deps_audit.sh --generate  # пере-снять baseline (отказ, если половина не проверена)
#
# Нет манифеста половины — мягкий пропуск с названной причиной: проверять там нечего.

set -uo pipefail

STRICT=${STRICT:-1}
HARD_FAIL_SEVERITY=${HARD_FAIL_SEVERITY:-1}
export STRICT HARD_FAIL_SEVERITY
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
VERDICT="$SCRIPT_DIR/deps_audit_verdict.py"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); reset=$(printf '\033[0m')

# Ответы инструментов — файлами: разбор живёт в deps_audit_verdict.py, а
# различать «упал» и «нашёл» по коду возврата нельзя ни у pip-audit, ни у npm —
# оба выходят ненулём и когда честно нашли уязвимости. Признак — разобрался ли ответ.
work=$(mktemp -d "${TMPDIR:-/tmp}/deps-audit.XXXXXX") || exit 1
trap 'rm -rf "$work"' EXIT

skipped=()
# na — половина не применима (манифеста нет); missing — применима, инструмента нет;
# ran — инструмент запущен, ответ в файле. «Не проверена» и «проверена, ноль» —
# разные состояния: без этого ноль читается как доказательство, которого не было.
py_state=na; js_state=na; pa_src=""

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
py_manifest=""
for cand in "$BE_DIR/pyproject.toml" "$BE_DIR/requirements.txt" "$BE_DIR/setup.py" \
            "$BE_DIR/setup.cfg" "$BE_DIR/Pipfile" \
            pyproject.toml requirements.txt setup.py setup.cfg Pipfile; do
  [[ -f "$cand" ]] && { py_manifest="$cand"; break; }
done
if [[ -z "$py_manifest" ]]; then
  skipped+=("python-манифеста нет ни в $BE_DIR/, ни в корне — python-половина не применима")
elif [[ -z "$PIP_AUDIT" ]]; then
  # Манифест есть, инструмента нет — половина НЕ проверена, а не «не применима»:
  # до 20.11.2026 это был мягкий пропуск с exit 0 и при STRICT=1.
  py_state=missing
else
  # ⚠ НАСЕЛЕНИЕ аудита — дерево ПРОДУКТА, а не окружение, из которого зовут.
  # `pip-audit` без аргументов проверяет текущий venv, и это два разных населения
  # сразу: в CI джоба ставит в него инструменты гейтов, а локально venv отстаёт от
  # lock-файла. Замер `outreach-donors` 30.09: CI дал `py_total: 12`, локальный
  # venv — 1 (pyjwt 2.14.0), а по `uv export --frozen` — **ноль**, потому что в
  # lock давно 2.15.0 и прод ставится `uv sync --locked`. Три числа об одном
  # проекте, и ни одно не про то, что поедет в прод.
  #
  # Поэтому при наличии lock-файла спрашиваем ЕГО: `--no-deps --disable-pip`,
  # чтобы pip-audit не разрешал зависимости заново по сети (иначе `>=` уедет к
  # свежей версии с PyPI — ровно тот обход lock, который и дал 12).
  pa_src="окружение"
  pa_req=""
  # ⚠ `uv` ищется И В VENV ОСНАСТКИ, не только в PATH. Раннер CI не обязан его
  # иметь: замер 30.09 — ветвь с lock молча не сработала, гейт напечатал «мерено
  # по ОКРУЖЕНИЮ» и предъявил 12. Само сообщение спасло разбор (без него
  # молчаливый фолбэк неотличим от измерения продукта), но правка без
  # инструмента на раннере остаётся объявлением.
  UV_BIN=$(command -v uv 2>/dev/null || true)
  for c in "$VENV/bin/uv" "$BE_DIR/.venv/bin/uv" ".venv/bin/uv"; do
    [[ -n "$UV_BIN" ]] && break
    [[ -x "$c" ]] && UV_BIN="$c"
  done
  if [[ -n "$UV_BIN" ]]; then
    for lock in uv.lock "$BE_DIR/uv.lock"; do
      [[ -f "$lock" ]] || continue
      pa_req="$work/requirements.txt"
      if (cd "$(dirname "$lock")" && "$UV_BIN" export --frozen --no-hashes \
            --no-emit-project --all-extras) > "$pa_req" 2>/dev/null; then
        pa_src="uv.lock"
      else
        pa_req=""
      fi
      break
    done
  fi
  if [[ -n "$pa_req" ]]; then
    "$PIP_AUDIT" -r "$pa_req" --no-deps --disable-pip --format=json \
      --progress-spinner=off >"$work/pip.json" 2>"$work/pip.err"
  else
    "$PIP_AUDIT" --format=json --progress-spinner=off >"$work/pip.json" 2>"$work/pip.err"
  fi
  py_state=ran
fi

# --- frontend: npm audit ------------------------------------------------------
if [[ ! -f "$FE_DIR/package-lock.json" ]]; then
  skipped+=("npm audit: нет $FE_DIR/package-lock.json — js-половина не применима")
elif ! command -v npm >/dev/null 2>&1; then
  js_state=missing
else
  # Прежний разбор шёл пайплайном с `|| echo "0 0 0"`: ответ-ошибка реестра
  # («request to … failed») превращался в три нуля и «OK» (аудит 20.11.2026).
  npm audit --json --prefix "$FE_DIR" >"$work/npm.json" 2>"$work/npm.err"
  js_state=ran
fi

# ⚠ НАСЕЛЕНИЕ печатается рядом с числом, а не подразумевается. «12 уязвимостей»
# без ответа на вопрос «в чём именно» отправляет чинить продукт, когда дело в
# окружении гейтов (замер 30.09, три разных числа об одном проекте).
# if, а не `test && cmd`: под `set -e` ложный тест уронил бы шаг — грабля,
# которую канон уже разбирал у установки зависимостей (lab-11 F9).
if [[ "$pa_src" == "окружение" ]]; then
  printf 'deps-audit: python-половина мерена по ОКРУЖЕНИЮ (lock не найден) — числа включают оснастку гейтов\n' >&2
elif [[ "$pa_src" == "uv.lock" ]]; then
  printf 'deps-audit: python-половина мерена по uv.lock — дерево продукта, как в проде\n'
fi

for s in ${skipped[@]+"${skipped[@]}"}; do printf '  ○ пропущено: %s\n' "$s"; done

if [[ "$py_state" == na && "$js_state" == na ]]; then
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
  # ⚠ «Просмотрено N» здесь считает МАНИФЕСТЫ, а не файлы кода: гейт судит
  # зависимости. Ноль проверенных манифестов при нулях уязвимостей выглядит точно
  # так же, как чистый проект, — тот же класс, ради которого счётчик есть у
  # остальных гейтов формы (§6).
  printf '%sdeps-audit: 0 манифестов просмотрено%s — ни одна половина не\n' "$yellow" "$reset"
  printf 'проверена (см. причины выше): нули тут ничего не доказывают.\n'
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf '%sdeps-audit: python3 нет — ответы инструментов разобрать нечем, гейт НЕ проверил ничего%s\n' \
    "$red" "$reset" >&2
  [[ "$STRICT" == "0" ]] && exit 0
  exit 1
fi

args=(--py "$py_state" --py-out "$work/pip.json" --py-err "$work/pip.err"
      --js "$js_state" --js-out "$work/npm.json" --js-err "$work/npm.err"
      --baseline "$BASELINE" --status delivery/active/STATUS.md)
(( GENERATE )) && args+=(--generate)
python3 "$VERDICT" "${args[@]}"
