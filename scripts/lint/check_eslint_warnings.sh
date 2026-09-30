#!/usr/bin/env bash
# ESLint warning-count ратчет (фронт). Глобальный счётчик предупреждений.
#
# Per-file eslint-хук судит файлы вне снимка ошибок (без --max-warnings=0), поэтому warnings
# росли бы молча. Здесь: полный прогон eslint по фронт-src, снимок суммы warnings в
# eslint_warnings_baseline.txt; гейт падает, если счётчик ВЫРОС. Правишь файл с
# warnings -> счисти часть и пере-сними вниз (--generate). Только вниз.
#
# Бинарь eslint ищется в node_modules фронта, затем в PATH. Нет бинаря — гейт не
# блокирует, только предупреждает (свежий clone без npm ci). Нет самого каталога
# фронта — тоже skip: backend-only проект не должен падать на фронт-гейте.
#
# Настройка (env): LINT_FE_DIR — фронт-каталог с package.json/src (дефолт: frontend)
#
# Режимы:
#   check_eslint_warnings.sh             # проверка; exit 1 при росте
#   check_eslint_warnings.sh --generate  # пере-снять baseline (текущее число warnings)
#   STRICT=0 check_eslint_warnings.sh    # soft (warning, exit 0)

set -uo pipefail

STRICT=${STRICT:-1}
FE_DIR=${LINT_FE_DIR:-frontend}
GENERATE=0
[[ "${1:-}" == "--generate" ]] && GENERATE=1

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASELINE="$SCRIPT_DIR/eslint_warnings_baseline.txt"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# Каталога фронта нет (backend-only проект / другой layout) — не наша забота, skip.
# Без этой проверки `cd` печатал сырую ошибку шелла и ронял коммит на exit 1.
if [[ ! -d "$REPO_ROOT/$FE_DIR" ]]; then
  printf '%s⚠ нет каталога фронта (%s) — eslint warning-ратчет пропущен. Настройка: LINT_FE_DIR%s\n' \
    "$yellow" "$FE_DIR" "$reset"
  exit 0
fi
cd "$REPO_ROOT/$FE_DIR" || exit 1

if [[ -x "node_modules/.bin/eslint" ]]; then
  ESLINT="node_modules/.bin/eslint"
elif command -v eslint >/dev/null 2>&1; then
  ESLINT="eslint"
else
  printf '%s⚠ eslint не найден — warning-ратчет пропущен. Установка: npm ci%s\n' "$yellow" "$reset"
  exit 0
fi

# Полный прогон по src: сумма warnings И пофайловые счётчики ERRORS.
#
# ⚠ Ошибки тоже под ратчетом — с cqg@1.38. Раньше их судил только per-file
# eslint-хук, который валит на ЛЮБОЙ ошибке, и для легаси-фронта это была стена:
# у каждого соседнего гейта есть путь войти постепенно (снимок, `ignore_imports`,
# `[[tool.mypy.overrides]]`, `prettier --write`), а у eslint-ошибок не было
# НИЧЕГО — либо чини весь фронт до первого коммита, либо гейт красный навсегда.
# Найдено lab-9 (находка 5) и подтверждено развёртыванием lab-10.
#
# Семантика — та же, что у всех ратчетов канона, и она важнее удобства:
# файл В снимке проходит при errors <= снимок; файл ВНЕ снимка (новый или ранее
# чистый) — hard 0. То есть легаси живёт под запись, а новый код обязан быть чист.
# Пофайлово, а не суммой: иначе «починил один файл, сломал другой» проходило бы.
current_report() {
  "$ESLINT" src --format json 2>/dev/null \
    | node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{try{
const r=JSON.parse(d);
const w=r.reduce((a,f)=>a+f.warningCount,0);
const errs=r.filter(f=>f.errorCount>0)
  .map(f=>f.errorCount+":"+f.filePath.split("/frontend/").pop())
  .sort();
console.log(w);console.log("files="+r.length);console.log(errs.join("\n"));
}catch{console.log("");}})'
}

report=$(current_report)
count=$(printf '%s\n' "$report" | head -1)
# Число ПРОСМОТРЕННЫХ файлов — отдельной строкой `files=N`. Под фильтр ошибок
# (`^[0-9]+:`) она не подходит, поэтому разбор ниже её не заметит, а формат
# снимка остался прежним.
seen=$(printf '%s\n' "$report" | sed -n 's/^files=//p' | head -1)
seen=${seen:-0}
err_lines=$(printf '%s\n' "$report" | tail -n +2 | grep -E '^[0-9]+:' || true)
if [[ -z "$count" ]]; then
  printf '%sERROR%s: не удалось посчитать eslint warnings (пустой/битый JSON-вывод eslint).\n' "$red" "$reset"
  exit 1
fi

if [[ "$GENERATE" == "1" ]]; then
  {
    echo "# eslint_warnings_baseline.txt — снимок eslint-ратчета. Генерируется --generate, НЕ руками."
    echo "# Первая строка-число = сумма ESLint warnings полного прогона фронт-src."
    echo "# Далее <errors>:<path> — пофайловый долг ОШИБОК: файл проходит при errors <= снимок,"
    echo "# файл ВНЕ снимка (новый или ранее чистый) — hard 0. Оба списка только ТАЮТ."
    echo "$count"
    [[ -n "$err_lines" ]] && printf '%s\n' "$err_lines"
  } >"$BASELINE"
  n_err=$(printf '%s' "$err_lines" | grep -c '^[0-9]' || true)
  echo "${green}eslint baseline пересобран${reset}: warnings ${count}, файлов с ошибками ${n_err:-0}"
  exit 0
fi

# --- ратчет ОШИБОК: пофайлово --------------------------------------------------
err_base_for() { # $1 = путь; печатает разрешённое число ошибок (0, если не в снимке)
  sed -n "s|^\([0-9]\{1,\}\):$1\$|\1|p" "$BASELINE" 2>/dev/null | head -1
}
err_fails=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  now=${line%%:*}; path=${line#*:}
  was=$(err_base_for "$path"); was=${was:-0}
  if (( now > was )); then
    printf '%s  ✗  %s: ошибок %d, разрешено %d%s\n' "$red" "$path" "$now" "$was" "$reset" >&2
    err_fails=$((err_fails + 1))
  fi
done < <(printf '%s\n' "$err_lines")
if (( err_fails > 0 )); then
  printf '%sERROR%s: ESLint ошибки выросли в %d файл(ах).\n' "$red" "$reset" "$err_fails" >&2
  printf 'Файл вне снимка обязан быть чист (hard 0); файл в снимке — только вниз.\n' >&2
  printf 'Счистил часть — пере-сними: --generate. Снимок вверх не переснимается.\n' >&2
  [[ "$STRICT" == "0" ]] || exit 1
  printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2
fi

baseline=$(grep -m1 -oE '^[0-9]+' "$BASELINE" 2>/dev/null)
if [[ -z "$baseline" ]]; then
  printf '%sERROR%s: baseline не найден (%s) — сними снимок: --generate\n' "$red" "$reset" "$BASELINE"
  exit 1
fi

if [[ "$count" -gt "$baseline" ]]; then
  if [[ "$STRICT" == "1" ]]; then
    printf '%sERROR%s: ESLint warnings выросли: %d (baseline %d).\n' "$red" "$reset" "$count" "$baseline"
    echo "Новые предупреждения не проходят: почини правило (частые — max-lines-per-function, react-hooks/exhaustive-deps)."
    echo "Рост оправдан (редко) — пере-снять снимок: --generate."
    exit 1
  fi
  printf '%sWARNING%s: ESLint warnings выросли: %d (baseline %d).\n' "$yellow" "$reset" "$count" "$baseline"
elif [[ "$count" -lt "$baseline" ]]; then
  echo "${yellow}warnings уменьшились ($count < baseline $baseline) — ужми снимок: --generate${reset}"
else
  # Совпадение со снимком — тоже путь, и он обязан себя назвать (§6): молчаливый
  # exit 0 здесь неотличим от «eslint не запускался вообще». Тот же класс, что у
  # ast-гейта; найден тремя арками пятого развёртывания.
  # «warnings 0» доказывает вердикт, но НЕ доказывает, что смотрели: ноль
  # предупреждений на ноле файлов выглядит точно так же. §6 требует число
  # просмотренного от каждого гейта формы, и ратчет — не исключение. Полевой
  # аудит: три хука фронта стояли с шаблонной маской, каталога такого нет, и все
  # трое печатали успех, не увидев ни одного файла.
  if [[ "$seen" == "0" ]]; then
    printf '%seslint-warnings: 0 файлов просмотрено%s — проверь LINT_FE_DIR и маску\n' \
      "$yellow" "$reset"
    printf 'хука (§6): ноль предупреждений на непросмотренном — не «чисто».\n'
    exit 0
  fi
  printf '%seslint-warnings: OK%s — просмотрено %s файл(ов), warnings %d, снимок %d\n' \
    "$green" "$reset" "$seen" "$count" "$baseline"
fi
exit 0
