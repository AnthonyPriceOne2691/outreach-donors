#!/usr/bin/env bash
# Ратчет над самими снимками: baseline может только ТАЯТЬ.
#
# Локальные гейты сравнивают код со снимком, но никто не сравнивает СНИМОК с его
# прошлой версией. `--generate` на грязном дереве легализует свежие нарушения —
# гейт зелёный, правило мертво. Этот скрипт закрывает дыру: рост счётчика или
# новая запись в baseline = fail.
#
# Форматы: "<count>:<path>" (per-path) и "<N>" (global). Комментарии (#) и
# секции ([baseline]/[exemption]) игнорируются.
#
# Настройка (env):
#   BASE                    — ref для сравнения (дефолт: origin/main)
#   LINT_DIR                — каталог снимков (дефолт: scripts/lint)
#   STRICT=0                — soft (warning, exit 0); в CI ЗАПРЕЩЁН
#   ALLOW_BASELINE_GROWTH=1 — осознанный рост (например, массовое переименование
#                             файлов); обязан быть виден и объяснён в PR
set -euo pipefail

BASE=${BASE:-origin/main}
STRICT=${STRICT:-1}
LINT_DIR=${LINT_DIR:-scripts/lint}
ALLOW_BASELINE_GROWTH=${ALLOW_BASELINE_GROWTH:-0}

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
  echo "baseline-ratchet: ref '$BASE' недоступен (shallow clone? нужен fetch-depth: 0)" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Waiver из STATUS — основной механизм, env — аварийный. Причина: env-переменную
# в CI иначе как правкой workflow не задать (то есть навсегда), а локально она не
# оставляет следа в диффе, и ревьюер обхода не видит. Строка в STATUS попадает в
# дифф, видна в PR и умирает вместе с поставкой (Delivery §4.3a).
status_waiver() {
  local f="delivery/active/STATUS.md" v
  [[ -f "$f" ]] || return 1
  v=$(sed -n 's/^[[:space:]]*[-*]\{0,1\}[[:space:]]*\**baseline_growth_waiver\**[[:space:]]*:\**[[:space:]]*\(.*\)$/\1/p' "$f" | head -1)
  v=${v%%<!--*}
  v=$(printf '%s' "$v" | sed 's/[[:space:]]*$//')
  case "$v" in
    ''|no|none|-|…|'<'*) return 1 ;;
  esac
  printf '%s' "$v"
}

# Снимок -> "path<TAB>count". Global-число получает ключ __global__.
normalize() {
  awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    /^[[:space:]]*\[/ { next }
    # Строка ПАР `ключ=число` — так пишет снимок `deps-audit` (четыре счётчика
    # уязвимостей). Форму нашло поле: `voice-interview-coach` на первом же пуше
    # после `cqg@2.23` получил «формат не читается» — то есть ратчет не охранял
    # этот снимок НИКОГДА, а каталог §3 всё это время называл его global-count.
    # Каждая пара становится своей записью, и семантика та же: счётчик уязвимостей
    # только тает.
    /^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=[0-9]+([[:space:]]+[A-Za-z_][A-Za-z0-9_]*=[0-9]+)*[[:space:]]*$/ {
      n = split($0, kv, /[[:space:]]+/)
      for (j = 1; j <= n; j++) {
        if (kv[j] == "") continue
        e = index(kv[j], "=")
        print substr(kv[j], 1, e - 1) "\t" substr(kv[j], e + 1)
      }
      next
    }
    {
      i = index($0, ":")
      if (i > 0) { c = substr($0, 1, i - 1); p = substr($0, i + 1) }
      else       { c = $0;                    p = "__global__" }
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", c)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", p)
      if (c ~ /^[0-9]+$/) print p "\t" c
    }' "$1"
}

# Сколько в снимке строк ДАННЫХ: не комментарий, не секция, не пустая. Вопрос
# другой, чем у normalize(): та отвечает «сколько записей разобрано», эта — «было
# ли что разбирать». Разность двух чисел и есть ложь: файл под глобом ратчета, из
# которого не разобрано ни одной записи, сверяется с базой ПУСТЫМ множеством и
# уходит в «OK, снимков сверено N».
data_lines() {
  awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    /^[[:space:]]*\[/ { next }
    { n++ }
    END { print n + 0 }' "$1"
}

violations=0
unreadable=0
checked=0

shopt -s nullglob
for cur in "$LINT_DIR"/*_baseline.txt; do
  name=$(basename "$cur")
  # Формат проверяется ДО обращения к BASE, и порядок здесь несущий: снимок в
  # чужом формате приходит НОВЫМ файлом, а новый уходит в `skip` строкой ниже —
  # то есть единственный случай, ради которого проверка написана, разбирался бы
  # последним и молча. Класс тот же, что закрыт ниже про `FILENAME == oldf`, но
  # на уровне ЗАПИСИ, а не файла: сверено ноль читается как «проверено».
  data=$(data_lines "$cur")
  if [[ "$data" -gt 0 && "$(normalize "$cur" | wc -l | tr -d ' ')" -eq 0 ]]; then
    echo "  $name: формат не читается ратчетом — 0 записей из $data строк(и) данных" >&2
    unreadable=$((unreadable + 1))
    continue
  fi
  if ! git show "$BASE:$cur" >"$tmp/old.raw" 2>/dev/null; then
    echo "  skip (нет в $BASE, новый гейт): $name"
    continue
  fi
  checked=$((checked + 1))
  normalize "$tmp/old.raw" | sort >"$tmp/old.tsv"
  normalize "$cur" | sort >"$tmp/new.tsv"

  # `FILENAME == oldf`, а НЕ `NR == FNR`: если снимок в базе не содержит ни одной
  # числовой записи (частый случай — проект стартовал чистым), old.tsv пуст, и
  # тогда `NR == FNR` истинно для КАЖДОЙ строки new.tsv, потому что первый файл не
  # добавил ничего в NR. Все новые записи уходили в массив `old[]` вместо сравнения,
  # и мета-гейт превращался в полный no-op: любое число свежих нарушений
  # легализовалось молча — ровно то, от чего он и поставлен. Найдено регрессионным
  # сьютом (tests/) на первом же прогоне, полевые развёртывания это пропустили.
  if ! awk -F'\t' -v f="$name" -v oldf="$tmp/old.tsv" '
      FILENAME == oldf { old[$1] = $2; next }
      {
        if (!($1 in old)) {
          printf "  %s: НОВАЯ запись %s (count %s) — свежее нарушение легализовано\n", f, $1, $2
          bad = 1
        } else if (($2 + 0) > (old[$1] + 0)) {
          printf "  %s: %s вырос %s -> %s\n", f, $1, old[$1], $2
          bad = 1
        }
      }
      END { exit bad ? 1 : 0 }' "$tmp/old.tsv" "$tmp/new.tsv"; then
    violations=$((violations + 1))
  fi
done

# Нечитаемый снимок судится ОТДЕЛЬНО от роста и waiver'ом роста не гасится:
# `baseline_growth_waiver` объявляет законным РОСТ, а здесь неизвестно даже, был
# ли он — сравнивать было нечем. Разные вопросы, разные выходы.
if [[ "$unreadable" -gt 0 ]]; then
  if [[ "$STRICT" == "0" ]]; then
    echo "baseline-ratchet: WARNING (STRICT=0) — $unreadable снимок(ов) нечитаем(ы)" >&2
  else
    echo "baseline-ratchet: FAIL — $unreadable снимок(ов) под глобом ратчета в чужом формате." >&2
    echo "Читаются '<count>:<path>' и '<N>'; из остального разбирается НОЛЬ записей, и" >&2
    echo "мета-гейт отчитывается «сверено», не сверив ничего. Варианты: (1) привести" >&2
    echo "снимок к формату; (2) если у него ДРУГАЯ семантика (например «не меняется без" >&2
    echo "сопровождения» вместо «только вниз») — держать его вне '$LINT_DIR', у своего" >&2
    echo "владельца: два ратчета с противоположными правилами в одном каталоге разъедутся." >&2
    exit 1
  fi
fi

if [[ "$violations" -eq 0 ]]; then
  # Сверено НОЛЬ — это не «проверено», а «сверять было нечего»: снимки в базе
  # отсутствуют. Так выглядит первое развёртывание и первый прогон каждого нового
  # гейта — то есть ровно те моменты, когда пересъём вверх наиболее вероятен.
  # Статус WARNING, а не OK: зелёный мета-гейт, сверивший ноль, читается как
  # «проверено» (Delivery §3.1a). Ошибкой делать нельзя — на bootstrap это штатно.
  # Найдено полем (lab-2).
  if [[ "$checked" -eq 0 ]]; then
    # Без цветовых переменных: этот скрипт их не определяет, а под `set -u`
    # ссылка на неопределённую переменную роняет гейт. Поймано обратным прогоном
    # правки — четвёртый такой случай за сессию, и все четыре нашёл он же.
    echo "baseline-ratchet: WARNING — сверено 0 снимков с $BASE: в базе их нет" \
         "(bootstrap или новый гейт). Ратчет на снимки в этом прогоне НЕ работал."
    exit 0
  fi
  echo "baseline-ratchet: OK ($checked снимков сверено с $BASE)"
  exit 0
fi

if waiver=$(status_waiver); then
  echo "baseline-ratchet: рост разрешён waiver'ом из delivery/active/STATUS.md: $waiver" >&2
  echo "(виден в PR, умрёт вместе с поставкой)" >&2
  exit 0
fi
if [[ "$ALLOW_BASELINE_GROWTH" == "1" ]]; then
  echo "baseline-ratchet: рост разрешён ALLOW_BASELINE_GROWTH=1." >&2
  echo "⚠ env-обход НЕ виден ревьюеру и в CI задаётся только правкой workflow." >&2
  echo "Предпочитай строку 'baseline_growth_waiver: reason=… by=human:…' в STATUS." >&2
  exit 0
fi
if [[ "$STRICT" == "0" ]]; then
  echo "baseline-ratchet: WARNING (STRICT=0) — $violations снимков выросли" >&2
  exit 0
fi
echo "baseline-ratchet: FAIL — снимок переснят ВВЕРХ в $violations файл(ах)." >&2
echo "Варианты: (1) убрать нарушения в коде, затем --generate/--tighten — снимок" >&2
echo "только вниз (§7); (2) если рост законен (массовое переименование) — строка" >&2
echo "'baseline_growth_waiver: reason=… by=human:…' в delivery/active/STATUS.md." >&2
exit 1
