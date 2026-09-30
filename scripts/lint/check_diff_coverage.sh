#!/usr/bin/env bash
# coverage-on-diff — DoD-отчёт покрытия ИЗМЕНЁННЫХ prod-файлов.
#
# НЕ pre-commit хук (полный сьют = минуты): запускается руками перед push / в DoD.
# Гоняет pytest c coverage-json, затем печатает покрытие каждого изменённого
# prod-файла (diff против BASE, дефолт origin/main) и сравнивает с целью (70%).
#
# База диффа:
#   • дифф до BASE пуст, но есть НЕзакоммиченные правки -> ЖЁЛТЫЙ ворнинг (рабочее
#     дерево скрипт не меряет — закоммить и повторить); STRICT=1 -> exit 1;
#   • дифф пуст и дерево чистое (только что запушено) -> авто-fallback на origin/main@{1}
#     (прошлая позиция = последний пуш); fallback отключается, если BASE задан снаружи.
#
# Настройка (env):
#   LINT_BE_DIR   — каталог backend с venv/pyproject/pytest (дефолт: backend)
#   LINT_COV_PKG  — пакет для --cov (дефолт: features)
#   LINT_PY_SRC   — прод-Python корень от repo-root (дефолт: $BE_DIR/$COV_PKG)
#   LINT_VENV     — venv: принимается И относительно backend, И от repo-root
#                   (дефолт: .venv рядом с backend)
#
# Режимы:
#   check_diff_coverage.sh              # отчёт (exit 0 всегда)
#   STRICT=1 check_diff_coverage.sh     # exit 1: файл < MIN_PCT / грязное дерево
#   BASE=<ref> …                        # база диффа (дефолт origin/main)
#   SKIP_TESTS=1 …                      # переиспользовать существующий coverage.json

set -uo pipefail

STRICT=${STRICT:-0}
BASE_WAS_SET=${BASE+set}
BASE=${BASE:-origin/main}
MIN_PCT=${MIN_PCT:-70}
SKIP_TESTS=${SKIP_TESTS:-0}

BE_DIR=${LINT_BE_DIR:-backend}
COV_PKG=${LINT_COV_PKG:-features}
PY_SRC=${LINT_PY_SRC:-$BE_DIR/$COV_PKG}
VENV=${LINT_VENV:-.venv}

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# `LINT_VENV` принимается в ДВУХ написаниях, потому что канон сам их путал: §6
# документировал «относительно backend», а три скрипта из четырёх читали значение
# от repo-root. Документированное `.venv` глушило мутационный гейт мягким пропуском
# ПРИ УСТАНОВЛЕННОМ mutmut — то есть исполнитель, настроивший пути по таблице §6,
# терял гейты молча (полевая находка lab-9 №7). Одно значение не могло удовлетворить
# обе трактовки, поэтому здесь и в трёх соседях разрешаются обе.
if [[ ! -d "$REPO_ROOT/$BE_DIR/$VENV" && -d "$REPO_ROOT/$VENV" ]]; then
  VENV="$REPO_ROOT/$VENV"
fi

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# Каталога backend нет (другой layout, не-Python проект) — назвать и пропустить.
# Без этой проверки `cd` печатал СЫРУЮ ошибку шелла и ронял коммит на exit 1 —
# ровно то, что уже было починено у eslint-ратчета и не перенесено сюда. Нашлось
# не глазами, а пробником по всем path-зависимым гейтам сразу (см. регрессию
# `test_no_gate_dies_with_a_raw_shell_error`).
# Применимость — по МАНИФЕСТУ проекта, а не по наличию каталога. Тот же класс,
# что закрыт у check_deps_audit.sh (cqg@2.17), и здесь он оставался жив: джоба
# §8.3 ставит оснастку гейтов в $BE_DIR/.venv и тем СОЗДАЁТ $BE_DIR, после чего
# охрана -d истинна на проекте, где питона нет. Замер третьего развёртывания:
# без каталога — честный пропуск, с созданным соседним шагом — гейт отчитывается
# об 11 не-python файлах. Правка была на СЛУЧАЙ, а не на класс.
# ⚠ Применимость судится по ОТСЛЕЖИВАЕМОМУ КОДУ, а не по наличию каталога:
# джоба §8.3 ставит оснастку гейтов в `$BE_DIR/.venv` и тем СОЗДАЁТ `$BE_DIR`,
# после чего охрана `-d` истинна на проекте, где питона нет. Тот же класс закрыт
# у `check_deps_audit.sh` (`cqg@2.17`), но признак ТАМ другой и переносить его
# сюда нельзя: аудит зависимостей спрашивает про манифест, покрытие — про КОД.
# Перенос признака через границу класса сьют поймал шестью красными прогонами.
# `git ls-files` заодно отсекает `.venv`: он не отслеживается.
py_tracked=$(git -C "$REPO_ROOT" ls-files -- "$PY_SRC" 2>/dev/null | grep -cE '\.py$' || true)
if [[ "${py_tracked:-0}" -eq 0 ]]; then
  printf '%s⚠ нет отслеживаемого python-кода в %s — diff-coverage пропущен. Настройка: LINT_PY_SRC%s\n' \
    "$yellow" "$PY_SRC" "$reset"
  printf 'Покрытие изменённого кода НЕ измерено — отметь непокрытость в verify-report.md.\n'
  exit 0
fi
cd "$REPO_ROOT/$BE_DIR" || exit 1

# git diff — от repo-root (git -C): pathspec от корня не матчится из cwd backend/.
list_changed() { # $1 = base-реф; закоммиченный дифф prod-файлов (без тестов)
  git -C "$REPO_ROOT" diff --name-only "$1"...HEAD -- "$PY_SRC/*.py" 2>/dev/null \
    | grep -vE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.' \
    | sed "s#^$BE_DIR/##"
}

# Незакоммиченные правки — их дифф-списком не увидеть, а сьют их исполняет:
# источник ложного зелёного.
dirty=$(git -C "$REPO_ROOT" status --porcelain -- "$PY_SRC/*.py" 2>/dev/null \
  | cut -c4- | grep -vE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.' || true)

changed=$(list_changed "$BASE")

if [[ -z "$changed" && -n "$dirty" ]]; then
  echo "${yellow}diff-coverage: коммитов относительно $BASE нет, но есть НЕзакоммиченные правки —${reset}"
  echo "${yellow}рабочее дерево скрипт не меряет. Закоммить и повторить. Незакоммичено:${reset}"
  echo "$dirty" | sed 's/^/  • /'
  [[ "$STRICT" == "1" ]] && exit 1
  exit 0
fi

# Запушено (origin/main == HEAD) и дерево чистое -> меряем последний пуш через reflog
# remote-tracking ветки. Только для дефолтного BASE: явный BASE=<ref> уважаем как есть.
if [[ -z "$changed" && -z "$BASE_WAS_SET" ]] \
  && git -C "$REPO_ROOT" rev-parse --verify -q 'origin/main@{1}' >/dev/null 2>&1; then
  changed=$(list_changed 'origin/main@{1}')
  if [[ -n "$changed" ]]; then
    prev_short=$(git -C "$REPO_ROOT" rev-parse --short 'origin/main@{1}')
    BASE="origin/main@{1} = $prev_short"
    echo "${yellow}diff-coverage: origin/main == HEAD (всё запушено) — меряю последний пуш: BASE=$BASE${reset}"
  fi
fi

if [[ -z "$changed" ]]; then
  # ⚠ Пустой список Python-файлов НЕ значит «prod-код не менялся»: этот гейт мерит
  # только Python, а дефолтный стек канона — Python+TS. До cqg@1.32 здесь стояло
  # зелёное «изменённых prod-файлов нет» на диффе, целиком состоящем из `.ts`/`.tsx`,
  # то есть половина заявленного стека молча не мерилась, а строка приёмки
  # «Diff-coverage ≥70% на изменённом» закрывалась этим зелёным (F9).
  # Отсутствие оракула для языка — непокрытая область, и по доктрине канона её
  # НАЗЫВАЮТ, а не роняют DoD-шаг (так же ведут себя ветки «нет pytest-cov» и jscpd).
  other=$(git -C "$REPO_ROOT" diff --name-only "$BASE"...HEAD 2>/dev/null \
    | grep -vE '(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.' \
    | grep -vE '\.(md|txt|json|ya?ml|toml|cfg|ini|lock|svg|png|jpg)$' \
    | grep -vE "${LINT_SRC_EXT_RE:-\.py$}" || true)
  if [[ -n "$other" ]]; then
    cnt=$(printf '%s\n' "$other" | wc -l | tr -d ' ')
    printf '%s⚠ diff-coverage: Python-файлов в диффе нет, но изменено %s prod-файл(ов)\n' \
      "$yellow" "$cnt"
    printf 'на других языках — этот гейт их НЕ мерит (он про Python):%s\n' "$reset"
    printf '%s\n' "$other" | head -10 | sed 's/^/  • /'
    printf 'Покрытие этих файлов не измерено. Либо подключи покрытие фронта\n'
    printf '(vitest/jest --coverage) как отдельный шаг, либо отметь непокрытость\n'
    printf 'в verify-report.md — иначе DoD §3.2 закрывается на непроверенном.\n'
    exit 0
  fi
  # Тот же класс F15, что у мутационного гейта (lab-12): «изменений нет» — вывод
  # ПО СУЩЕСТВУ, и его нельзя печатать, не убедившись, что смотрели туда, куда надо.
  # Путь от КОРНЯ РЕПОЗИТОРИЯ: к этому месту скрипт уже сделал `cd $REPO_ROOT/$BE_DIR`,
  # и относительный `-d "$PY_SRC"` резолвился бы как `backend/backend/app`. Поймано
  # обратным прогоном: первая версия правки краснила на ВЕРНОЙ настройке.
  if [[ ! -d "$REPO_ROOT/$PY_SRC" ]]; then
    printf '%s⚠ diff-coverage: нет каталога %s — покрытие диффа не измерено\n' "$yellow" "$PY_SRC"
    printf '(настрой LINT_PY_SRC, §6). Это НЕ «изменений нет»: гейт не смотрел никуда.%s\n' "$reset"
    exit 0
  fi
  echo "${green}diff-coverage: изменённых prod-файлов нет (BASE=$BASE, смотрел в $PY_SRC)${reset}"
  exit 0
fi

if [[ -n "$dirty" ]]; then
  echo "${yellow}внимание: есть незакоммиченные правки — сьют их исполняет, но в отчёте ниже их нет:${reset}"
  echo "$dirty" | sed 's/^/  • /'
fi

if [[ "$SKIP_TESTS" != "1" ]]; then
  echo "diff-coverage: гоняю сьют с coverage (может занять минуты)…"
  # Без pytest-cov гейт раньше падал `ERROR: unrecognized arguments: --cov=…`,
  # то есть отказом, хотя рядом в каноне jscpd в такой же ситуации честно
  # пропускается («инструмента нет»). Один класс — два разных ответа; найдено
  # независимым развёртыванием (lab-4). Отсутствие инструмента — не нарушение
  # правила, а непокрытая область: об этом предупреждают, а не роняют DoD-шаг.
  if ! "$VENV/bin/python" -c 'import pytest_cov' >/dev/null 2>&1; then
    printf '%s⚠ pytest-cov не установлен — diff-coverage пропущен.%s\n' "$yellow" "$reset"
    printf 'Установка: pip install pytest-cov. Покрытие изменённого кода НЕ измерено —\n'
    printf 'отметь это в verify-report.md, иначе DoD §3.2 закрывается на непроверенном.\n'
    exit 0
  fi
  "$VENV/bin/python" -m pytest -q --cov="$COV_PKG" --cov-report=json:coverage.json >/dev/null 2>&1
fi
if [[ ! -f coverage.json ]]; then
  echo "${red}coverage.json не найден (сьют не отработал?)${reset}"
  exit 1
fi

# changed — через env: пайп в `python - <<heredoc` не работает (heredoc занимает stdin).
CHANGED="$changed" "$VENV/bin/python" - "$MIN_PCT" "$STRICT" <<'PY'
import json
import os
import sys

min_pct = float(sys.argv[1])
strict = sys.argv[2] == "1"
changed = [line.strip() for line in os.environ.get("CHANGED", "").splitlines() if line.strip()]
files = json.load(open("coverage.json"))["files"]

# Корни, которые coverage ФАКТИЧЕСКИ измерял (--cov=<pkg>). Отсутствие файла в
# отчёте имеет ДВЕ разные причины, и путать их нельзя:
#   * файл вне измеряемого пакета (harness-скрипты, tooling, миграции) — coverage
#     его не видел вовсе. Это НЕ «0% покрытия», и падать тут нельзя: иначе гейт
#     становится стабильно красным на заведомо безопасных правках;
#   * файл ВНУТРИ пакета, но не исполнялся ни одним тестом — настоящий сигнал.
# Триггер расхождения: LINT_PY_SRC (что считается прод-кодом) шире LINT_COV_PKG
# (что мерит coverage) — §6 разрешает задать их независимо.
measured_roots = {p.split("/", 1)[0] for p in files}

# ТРЕТИЙ случай, которого не было: файл ВНУТРИ пакета, но исключённый из измерения
# самим проектом (`[tool.coverage.run] omit`). До cqg@1.30 он попадал во второй
# случай — «не исполнялся тестами» — и гейт был красным на каждой поставке, трогающей
# точку входа. §3.5 объявляла этот случай закрытым и посылала к «механизму исключений
# с печатью причины», которого в скрипте не существовало (`grep exempt` — пусто), а
# `STRICT=0` в CI запрещён §8.4: у проекта, следующего рекомендации канона держать
# `main.py` в `omit`, честного выхода не было. Нашло восьмое развёртывание на своей
# же поставке. Читаем `omit` из pyproject, а не держим список в скрипте: иначе он
# разойдётся с проектом молча.
omitted_globs = []
try:
    import tomllib
    with open("pyproject.toml", "rb") as fh:
        omitted_globs = (
            tomllib.load(fh).get("tool", {}).get("coverage", {}).get("run", {}).get("omit", [])
        )
except (OSError, ImportError, ValueError):
    pass


def is_omitted(path: str) -> bool:
    """Путь исключён самим проектом из измерения. Сверяем и полный путь, и путь
    относительно измеряемого пакета: в `omit` пишут и `app/main.py`, и `*/main.py`."""
    import fnmatch

    tails = {path, path.split("/", 1)[-1]}
    return any(fnmatch.fnmatch(tail, g) for g in omitted_globs for tail in tails)
if not measured_roots:
    print("coverage.json не содержит ни одного файла — сьют ничего не измерил")
    sys.exit(1 if strict else 0)

fails = 0
outside = []
print(f"{'файл':70s} {'stmts':>6s} {'miss':>5s} {'cov%':>6s}")
for path in changed:
    info = files.get(path)
    if info is None:
        if path.split("/", 1)[0] not in measured_roots:
            outside.append(path)
            print(f"{path:70s} {'—':>6s} {'—':>5s} {'n/a':>6s}  ○ вне измеряемого пакета")
            continue
        if is_omitted(path):
            outside.append(f"{path} (в coverage omit проекта)")
            print(f"{path:70s} {'—':>6s} {'—':>5s} {'n/a':>6s}  ○ исключён из измерения (omit)")
            continue
        print(f"{path:70s} {'—':>6s} {'—':>5s} {'0.0':>6s}  <- не исполнялся тестами вовсе")
        fails += 1
        continue
    s = info["summary"]
    pct = s["percent_covered"]
    mark = "" if pct >= min_pct else f"  <- ниже цели {min_pct:.0f}%"
    if pct < min_pct:
        fails += 1
    print(f"{path:70s} {s['num_statements']:6d} {s['missing_lines']:5d} {pct:6.1f}{mark}")

if outside:
    # Печатаем явно: пропуск без следа читался бы как «проверено».
    print(
        f"\n○ {len(outside)} файл(ов) вне измеряемого пакета (coverage их не мерил): "
        + ", ".join(outside)
    )
    print("  Если их НАДО мерить — расширь --cov; если нет — так и должно быть.")

if fails:
    print(f"\n{fails} изменённых файл(ов) ниже цели {min_pct:.0f}%.")
    sys.exit(1 if strict else 0)
PY
rc=$?
[[ "$STRICT" == "1" ]] && exit "$rc"
exit 0
