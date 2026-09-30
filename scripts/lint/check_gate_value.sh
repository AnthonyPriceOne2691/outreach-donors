#!/usr/bin/env bash
# Сколько раз каждый гейт реально сработал — данные для решения «оставить или убрать».
#
# Зачем. Контур умеет только затвердевать: гейтов становится больше, меньше не
# становится никогда (Delivery §9.1a). Чтобы применить к контуру тот же ратчет,
# что к коду, нужны ДАННЫЕ, а не мнения: какой гейт ловил дефекты, а какой год
# молчит и просто тратит секунды на каждом коммите.
#
# Источник — логи CI-прогонов: там видно и имя гейта, и результат. Локальные
# срабатывания не считаются: они не сохраняются нигде и по ним нельзя судить.
#
# Настройка (env):
#   CI_WORKFLOW  — какой workflow смотреть (дефолт quality)
#   RUNS         — сколько последних прогонов (дефолт 30)
#
# Нет `gh` — честно говорит, что данных нет, и не притворяется.

set -uo pipefail

CI_WORKFLOW=${CI_WORKFLOW:-quality}
RUNS=${RUNS:-30}

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

# `gh auth token` — локальная проверка учёток; `gh auth status` ходит в СЕТЬ и
# на порванном канале объявляет отсутствие логина (`cqg@1.89`, пришло полем).
# Хостинг спрашивается ПЕРЕД `gh` (`cqg@2.01`). Иначе на GitLab-машине с
# установленным и авторизованным `gh` (обычное дело у разработчика) скрипт шёл
# дальше, `gh run list` отдавал пусто, и печаталось «нет прогонов workflow
# quality» — то есть «данных нет» вместо «роль на этом хостинге не закрыта».
# Диагноз по симптому вместо причины отправляет чинить не то.
ORIGIN=$(git config --get remote.origin.url 2>/dev/null || true)
if printf '%s' "$ORIGIN" | grep -qi 'gitlab' || [[ -z "$ORIGIN" && -f .gitlab-ci.yml ]]; then
  printf '%s⚠ gate-value: проект на GitLab — роль «гейты хоть раз срабатывали» контуром не закрыта.%s\n' \
    "$yellow" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a). Закрой роль\n' >&2
  printf 'своим скриптом на `glab ci list` либо объяви её в `not-applicable.json`.\n' >&2
  exit 0
fi
if ! command -v gh >/dev/null 2>&1 || ! gh auth token >/dev/null 2>&1; then
  printf '%s⚠ gate-value: нужен авторизованный gh — без него данных о срабатываниях нет.%s\n' \
    "$yellow" "$reset" >&2
  printf 'Решение «убрать гейт» без данных принимать нельзя (§9.1a).\n' >&2
  exit 0
fi

# Инвентарь гейтов = скрипты в scripts/lint (git ls-files: только отслеживаемые).
# NOT_A_GATE — скрипты, которые лежат в scripts/lint/, но гейтами не являются:
# отчёты (exit 0 всегда). Без этого списка инвентарь считал гейтом САМ ЭТОТ СКРИПТ и
# выносил ему вердикт «0 срабатываний -> нужен gate_kept_reason или удалить», то есть
# инструмент §9.1a предлагал удалить инструмент §9.1a. Заодно врал счёт молчащих.
NOT_A_GATE='check_gate_value.sh'
gates=()
while IFS= read -r p; do
  b=$(basename "$p")
  [[ -n "$b" && "$b" != "$NOT_A_GATE" ]] && gates+=("$b")
done < <(git ls-files 'scripts/lint/check_*.sh' 'scripts/lint/check_*.py')
if (( ${#gates[@]} == 0 )); then
  echo "gate-value: гейтов не найдено" >&2; exit 0
fi

ids=$(gh run list --workflow "$CI_WORKFLOW" --limit "$RUNS" \
        --json databaseId,conclusion --jq '.[] | "\(.databaseId) \(.conclusion)"' 2>/dev/null)
if [[ -z "$ids" ]]; then
  printf '%s⚠ gate-value: нет прогонов workflow "%s"%s\n' "$yellow" "$CI_WORKFLOW" "$reset" >&2
  exit 0
fi

total=$(printf '%s\n' "$ids" | wc -l | tr -d ' ')
red_runs=$(printf '%s\n' "$ids" | grep -c 'failure' || true)
printf 'gate-value: %s прогонов workflow "%s", из них красных %s\n\n' \
  "$total" "$CI_WORKFLOW" "$red_runs"

logs=$(mktemp); trap 'rm -f "$logs"' EXIT
# Логи красных прогонов: срабатывание = гейт назван в логе упавшего шага.
while read -r id concl; do
  [[ "$concl" == "failure" ]] || continue
  gh run view "$id" --log-failed 2>/dev/null >>"$logs" || true
done <<<"$ids"

printf '%-34s %-10s %s\n' "гейт" "срабатал" "вердикт по §9.1a"
printf '%-34s %-10s %s\n' "----" "--------" "----------------"
for g in "${gates[@]}"; do
  hits=$(grep -cF "$g" "$logs" 2>/dev/null || true)
  hits=${hits:-0}
  if (( hits > 0 )); then
    verdict="держим (ловит)"
  else
    verdict="0 за $total прогонов -> нужен gate_kept_reason или удалить"
  fi
  printf '%-34s %-10s %s\n' "$g" "$hits" "$verdict"
done

printf '\n%sПрочти как данные, а не как приговор:%s гейт-страховка может годами\n' "$green" "$reset"
printf 'молчать и один раз спасти прод. Но тогда это решение, записанное в\n'
printf 'delivery/STACK-ACCEPTANCE.md, а не молчаливое «пусть лежит».\n'
exit 0
