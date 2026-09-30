#!/usr/bin/env bash
# Гейт: последний прогон CI на этой ветке — ЗЕЛЁНЫЙ.
#
# Зачем. `ci-oracles: tooling` в STATUS — это строка, которую пишет агент. Всё
# остальное (pre-commit, merge_guard, тесты) гоняется ЛОКАЛЬНО: своим Python,
# своим venv, своими установленными пакетами. Расхождение с CI-окружением такой
# набор не видит в принципе. Реальный случай (2026-07-28, второе развёртывание):
# CI был красным 6 прогонов подряд, в STATUS стояло `tooling`, две поставки
# закрыты как done. DoD требовал «ссылку на зелёный прогон» — прозой, без oracle.
# Это он.
#
# Проверяется прогон ИСХОДНОЙ ветки, не целевой. На PR хостинг гоняет workflow на
# merge-состоянии, а проверка target'а создала бы тупик: красный main блокировал
# бы мерж собственного фикса.
#
# Настройка (env):
#   CI_WORKFLOW — имя workflow (дефолт: quality)
#   CI_BRANCH   — ветка (дефолт: аргумент $1, иначе текущая)
#   STRICT=0    — soft (warning, exit 0)
#
# Нет `gh`, нет авторизации, нет ни одного прогона — WARNING и exit 0: гейт честно
# говорит «не могу проверить», а не выдаёт тишину за зелёное.

set -uo pipefail

STRICT=${STRICT:-1}
CI_WORKFLOW=${CI_WORKFLOW:-quality}

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# ⚠ Единственная строка приёмки §6, закрывавшаяся САМООЦЕНКОЙ, — `ci-oracles`.
# Гейт печатал WARNING и выходил 0, а значение в STATUS писал агент; три
# независимых развёртывания пометили `weak` честно, то есть правило работало, но
# держалось на честности исполнителя, а не на гейте (замер lab-9: 1 строка из 33).
#
# Различие, на котором держится правка: **отсутствие CI остаётся предупреждением**
# (это законный режим §10.4), а ошибкой становится ЛОЖНОЕ ЗАЯВЛЕНИЕ — когда гейт
# не увидел ни одного прогона, а STATUS утверждает `deployed`/`tooling`. Заявление
# сильнее факта — это не приёмка, а её имитация.
no_ci_evidence() { # $1 = причина, которую увидел гейт
  local st=delivery/active/STATUS.md val
  [[ -f "$st" ]] || return 0
  # ⚠ `\**` ПОСЛЕ двоеточия обязательна: в STATUS поле пишут как
  # `- **ci-oracles:** deployed`, то есть закрывающие звёздочки стоят за
  # двоеточием, а не перед ним. Без этого регулярка читала ПУСТО из штатной
  # строки, `val` выходил пустым, гейт возвращал 0 — и ветка «в STATUS deployed,
  # а CI не подтверждён» не срабатывала НИ РАЗУ. Единственная проверка против
  # самозаявленного `ci-oracles` была мертва для канонического формата.
  # ТРЕТИЙ рецидив класса: то же чинили в `check_new_dependency.py` (`:\**`) и
  # в `delivery_check.field()`. Нашло первое живое применение гейта мержа —
  # он печатал «⚠ gh не авторизован» и следом «OK».
  val=$(sed -n 's/^[[:space:]]*[-*]\{0,1\}[[:space:]]*\**ci-oracles\**[[:space:]]*:\**[[:space:]]*\([a-z]*\).*/\1/p' \
        "$st" | head -1)
  # Строки нет вовсе — это отдельный дефект, его ловит delivery_check, не этот гейт.
  [[ -n "$val" ]] || return 0
  if [[ "$val" == "weak" ]]; then
    printf 'ci-status: `ci-oracles: weak` в STATUS соответствует факту (%s)\n' "$1"
    return 0
  fi
  # Роль, ОБЪЯВЛЕННАЯ неприменимой, — законный выход, и он обязан работать
  # (`cqg@2.01`). Первая редакция GitLab-ветки советовала «объяви в
  # `not-applicable.json`», а `no_ci_evidence` этот файл не читал вовсе: совет
  # обещал выход, которого механика не давала, — тот же класс «невыполнимый
  # совет», от которого правка и защищала. Хуже: на GitLab с ЖИВЫМ зелёным
  # пайплайном гейт возвращал 1 при `ci-oracles: deployed`, то есть проект
  # становился красным на мерже за то, что у контура нет своего скрипта под этот
  # хостинг. §10.4 определяет `weak` как «CI нет вообще» — на GitLab это неправда,
  # значит честного значения не существовало ни одного.
  local na=scripts/lint/not-applicable.json reason=""
  if [[ -f "$na" ]] && command -v python3 >/dev/null 2>&1; then
    reason=$(NA="$na" python3 -c 'import json, os, sys
try:
    d = json.load(open(os.environ["NA"], encoding="utf-8"))
except Exception:
    sys.exit(0)
v = d.get("check_ci_status.sh") or d.get("ci-status")
sys.stdout.write(v if isinstance(v, str) else "")' 2>/dev/null)
  fi
  if [[ -n "$reason" ]]; then
    printf 'ci-status: роль объявлена неприменимой (%s) — причина: %s\n' "$1" "$reason"
    printf '   Объявление живёт в `not-applicable.json`, поэтому попадает в карту\n'
    printf '   ролей и печатается на каждом прогоне, а не забывается в прозе.\n'
    return 0
  fi
  printf '%sERROR%s: в STATUS `ci-oracles: %s`, а CI не подтверждён: %s\n' \
    "$red" "$reset" "$val" "$1" >&2
  printf 'Честное значение — `weak` плюс blocker (Delivery §10.4). Гейт не требует\n' >&2
  printf 'поднять CI: он требует, чтобы запись совпадала с наблюдением.\n' >&2
  [[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; return 0; }
  return 1
}

BRANCH=${CI_BRANCH:-${1:-}}
if [[ -z "$BRANCH" ]]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  # merge_guard гоняет гейты в detached worktree: там HEAD не ветка, и имя ветки
  # обязано прийти аргументом.
  [[ "$BRANCH" == "HEAD" ]] && BRANCH=""
fi
if [[ -z "$BRANCH" ]]; then
  printf '%s⚠ ci-status: не знаю ветку (detached HEAD?) — передай её аргументом%s\n' "$yellow" "$reset" >&2
  exit 0
fi

# Хостинг определяется по origin и по конфигу CI, потому что совет зависит от
# него (`cqg@2.00`). До этого гейт на GitLab-проекте печатал «Установи gh» —
# совет, который там не выполним: `gh` про Actions, а прогонов Actions на
# GitLab нет вовсе. Неверный совет хуже отсутствия совета: по нему идут чинить
# не то, а роль остаётся незакрытой молча.
#
# ⚠ Разбор вывода `glab` здесь СОЗНАТЕЛЬНО не написан. Проверить его нечем:
# полигона на GitLab у контура пока нет, а механика, написанная под
# непроверенное предположение, — ровно то, за что этот канон краснеет
# (§3.1a: «правило без прогона живёт, пока его читают»). Вместо этого роль
# называется незакрытой и даются два законных хода.
# Спрашиваем СНАЧАЛА remote и только потом файл (`cqg@2.01`). Обратный порядок
# ломал GitHub-проект, который держит зеркальный `.gitlab-ci.yml`: файл бил
# remote, и реальная проверка прогонов Actions подменялась предупреждением —
# ложное «роль не закрыта» там, где она закрыта. Признак «где лежит origin»
# сильнее признака «какой конфиг лежит рядом».
HOST="github"
ORIGIN=$(git config --get remote.origin.url 2>/dev/null || true)
if printf '%s' "$ORIGIN" | grep -qi 'gitlab'; then
  HOST="gitlab"
elif [[ -z "$ORIGIN" && -f .gitlab-ci.yml ]]; then
  HOST="gitlab"      # origin ещё не заведён — судим по конфигу
fi

if [[ "$HOST" == "gitlab" ]]; then
  printf '%s⚠ ci-status: проект на GitLab — роль «прогон CI действительно зелёный» контуром не закрыта.%s\n' \
    "$yellow" "$reset" >&2
  printf '   Два законных хода: (1) закрыть роль своим скриптом на `glab ci list`;\n' >&2
  printf '   (2) объявить её в `scripts/lint/not-applicable.json` с причиной — тогда\n' >&2
  printf '   она попадёт в карту ролей и будет видна на приёмке, а не забудется.\n' >&2
  no_ci_evidence "GitLab: роль не закрыта" || exit 1
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  printf '%s⚠ ci-status: нет `gh` — статус CI не проверен. Установи gh или проверь прогон руками%s\n' \
    "$yellow" "$reset" >&2
  no_ci_evidence "нет gh" || exit 1
  exit 0
fi
# ⚠ Авторизация проверяется `gh auth token`, а НЕ `gh auth status`. Второй ходит
# В СЕТЬ валидировать токен и на порванном канале выходит ненулём — гейт печатал
# «gh не авторизован» и отправлял чинить `gh auth login` там, где авторизация в
# порядке, а лежала сеть. Диагноз по симптому — тот же класс, что «реестр
# недоступен» у npm. `gh auth token` читает сохранённые учётки ЛОКАЛЬНО, поэтому
# различает «учёток нет» и «не дозвонились»: второе всплывёт ниже, на реальном
# вызове API, и уже со своим текстом.
#
# Найдено полем (portfolio-site, 2026-08-07) — и это лучше первой канонной
# правки, которая осталась на `gh auth status` и лишь разбирала текст ошибки.
# Разбор оставлен ниже, для СЕТЕВЫХ отказов самого вызова: там причина
# по-прежнему бывает неотличима, и тогда её называют неизвестной.
if ! gh auth token >/dev/null 2>&1; then
  printf '%s⚠ ci-status: gh без сохранённых учёток (`gh auth login`) — статус CI не проверен%s\n' \
    "$yellow" "$reset" >&2
  no_ci_evidence "gh без учёток" || exit 1
  exit 0
fi

# stderr НЕ глушим: «репозитория на хостинге нет» и «workflow ни разу не гонялся» —
# разные состояния, и раньше оба выглядели одинаково («нет прогонов»). Для первого
# ci-oracles обязан быть `weak` навсегда, для второго достаточно дождаться прогона.
# Три развёртывания споткнулись об это независимо.
gh_err=$(mktemp); trap 'rm -f "$gh_err"' EXIT
run=$(gh run list --workflow "$CI_WORKFLOW" --branch "$BRANCH" --limit 1 \
        --json status,conclusion,url,headSha,displayTitle 2>"$gh_err")
if [[ -s "$gh_err" ]] && grep -qiE 'no.*repositor|not a git repositor|could not resolve|base repo' "$gh_err"; then
  printf '%s⚠ ci-status: у репозитория нет хостинга с Actions — CI не существует, а не «пока нет прогонов»%s\n' \
    "$yellow" "$reset" >&2
  sed -n '1,3p' "$gh_err" >&2
  printf 'Это не «дождись прогона»: ci-oracles обязан остаться weak, пока хостинга нет (§10.4).\n' >&2
  no_ci_evidence "у репозитория нет хостинга с Actions" || exit 1
  exit 0
fi
if [[ -z "$run" || "$run" == "[]" ]]; then
  printf '%s⚠ ci-status: workflow "%s" на ветке %s ни разу не гонялся — запушь ветку и дождись CI%s\n' \
    "$yellow" "$CI_WORKFLOW" "$BRANCH" "$reset" >&2
  [[ -s "$gh_err" ]] && sed -n '1,2p' "$gh_err" >&2
  no_ci_evidence "ни одного прогона на ветке $BRANCH" || exit 1
  exit 0
fi

status=$(printf '%s' "$run" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
conclusion=$(printf '%s' "$run" | sed -n 's/.*"conclusion":"\([^"]*\)".*/\1/p')
url=$(printf '%s' "$run" | sed -n 's/.*"url":"\([^"]*\)".*/\1/p')
head_sha=$(printf '%s' "$run" | sed -n 's/.*"headSha":"\([^"]*\)".*/\1/p')

fail() {
  printf '%sci-status: %s%s\n' "$red" "$1" "$reset" >&2
  [[ -n "$url" ]] && printf 'Прогон: %s\n' "$url" >&2
  if [[ "$STRICT" == "0" ]]; then
    printf '%sci-status: WARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2
    exit 0
  fi
  exit 1
}

[[ "$status" == "completed" ]] || fail "прогон ещё не завершён (status=$status) — дождись CI"
[[ "$conclusion" == "success" ]] || fail "последний прогон \"$CI_WORKFLOW\" на $BRANCH: $conclusion"

# Зелёный прогон ДРУГОГО коммита ничего не доказывает про текущий код.
local_sha=$(git rev-parse "refs/heads/$BRANCH" 2>/dev/null || echo "")
if [[ -n "$local_sha" && -n "$head_sha" && "$local_sha" != "$head_sha" ]]; then
  fail "зелёный прогон относится к другому коммиту (${head_sha:0:8}), а ветка на ${local_sha:0:8} — запушь и дождись CI"
fi

printf '%sci-status: OK%s — "%s" на %s зелёный (%s)\n' \
  "$green" "$reset" "$CI_WORKFLOW" "$BRANCH" "${head_sha:0:8}"
exit 0
