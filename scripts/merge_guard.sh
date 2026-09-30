#!/usr/bin/env bash
# Мерж через гейты, а не кнопкой. Замена required status checks там, где их нет
# (бесплатный тариф, приватный репо, не-GitHub хостинг).
#
# Отличие от pre-push: проверяется СЛИТОЕ состояние — то, что реально окажется в
# целевой ветке. Ветка может быть зелёной, а после слияния с ушедшим вперёд main
# — красной; required checks в режиме strict ловят именно это.
#
# Механика: отдельный worktree на целевой ветке -> merge --no-commit -> прогон
# гейтов там -> мерж в основном клоне только при зелёном. Рабочее дерево не
# трогается, конфликт не оставляет репо в merge-состоянии.
#
# Usage:
#   scripts/merge_guard.sh <source-branch> [target-branch]   # default target: main
#   DRY_RUN=1 scripts/merge_guard.sh feat/x                  # только проверить
# ⚠ ОТКУДА звать: мерж — из дерева, где целевая ветка НЕ занята другим worktree
# (обычно основной клон). Из worktree поставки (§5.1) — только `DRY_RUN=1`.
set -euo pipefail

SOURCE=${1:?usage: merge_guard.sh <source-branch> [target-branch]}
TARGET=${2:-main}
DRY_RUN=${DRY_RUN:-0}
# squash по умолчанию — ради `git bisect` (§8.8). Проверяется СЛИТОЕ состояние,
# значит доказана зелёность результата, а не промежуточных коммитов ветки. При
# `no-ff` эти непроверенные коммиты попадают в историю, и bisect садится на них.
MERGE_MODE=${MERGE_MODE:-squash}   # squash | no-ff

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

red=$(printf '\033[31m'); green=$(printf '\033[32m'); yellow=$(printf '\033[33m'); reset=$(printf '\033[0m')
die() { printf '%smerge_guard: %s%s\n' "$red" "$1" "$reset" >&2; exit 1; }

# Интерпретатор резолвим, а не хардкодим: на macOS и в свежих дистрибутивах
# бинаря `python` нет вовсе, есть только `python3` (или venv проекта).
PY=${PYTHON:-}
if [[ -z "$PY" ]]; then
  for cand in backend/.venv/bin/python .venv/bin/python python3 python; do
    if [[ -x "$cand" ]] || command -v "$cand" >/dev/null 2>&1; then PY=$cand; break; fi
  done
fi
[[ -n "$PY" ]] || die "не найден python (задай PYTHON=/path/to/python)"

# $PY резолвится ОТНОСИТЕЛЬНЫМ путём (`backend/.venv/bin/python`), а гейты ниже
# гоняются после `cd "$WT"` — в другом дереве, где такого пути нет. Итог был:
# `merge_guard.sh: line NN: backend/.venv/bin/python: No such file or directory`,
# и мерж блокировал ДЕФЕКТ ГЕЙТА, а не код. Абсолютизируем до всякого cd.
if [[ "$PY" != /* ]]; then
  if [[ -x "$PY" ]]; then PY="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"
  else PY="$(command -v "$PY")"; fi
fi

git rev-parse --verify --quiet "$SOURCE" >/dev/null || die "нет ветки '$SOURCE'"
git rev-parse --verify --quiet "$TARGET" >/dev/null || die "нет ветки '$TARGET'"
[[ -z "$(git status --porcelain)" ]] || die "рабочее дерево грязное — закоммить или спрячь правки"

# Целевая ветка может быть ЗАНЯТА другим рабочим деревом, и тогда мерж отсюда
# невозможен: последний шаг делает `git checkout "$TARGET"` в дереве, откуда
# позван. Полевой симптом (проект вёл поставку в worktree, как велит §5.1): шесть
# гейтов зелёные, затем `fatal: 'main' is already used by worktree at …`. Диагноз
# читается как поломка репозитория, а не как «позвал не оттуда», и приходит в
# самом дорогом месте — после всей проверки. §5.1 велит вести поставку в worktree,
# значит это штатный случай, а не редкость.
#
# Проверка РАННЯЯ и только для настоящего мержа: `DRY_RUN=1` ничего не
# переключает, и падать на занятости ему не за что — иначе законный способ
# «проверить слитое состояние из своего worktree» перестал бы работать.
if [[ "$DRY_RUN" != "1" ]]; then
  busy=$(git worktree list --porcelain | awk -v t="refs/heads/$TARGET" \
    '/^worktree /{wt=substr($0,10)} /^branch /{if (substr($0,8)==t) print wt}')
  if [[ -n "$busy" && "$busy" != "$REPO_ROOT" ]]; then
    printf '%smerge_guard: ветка %s занята рабочим деревом:%s\n' "$red" "$TARGET" "$reset" >&2
    printf '  %s\n' "$busy" >&2
    printf 'Мерж переключает целевую ветку В ТОМ дереве, откуда позван, поэтому\n' >&2
    printf 'отсюда он невозможен. Два законных хода:\n' >&2
    printf '  1) мержить из того дерева:  cd %s && bash scripts/merge_guard.sh %s %s\n' \
      "$busy" "$SOURCE" "$TARGET" >&2
    printf '  2) проверить отсюда без мержа:  DRY_RUN=1 bash scripts/merge_guard.sh %s %s\n' \
      "$SOURCE" "$TARGET" >&2
    die "не то дерево для мержа (§8.5.2) — гейты не гонялись, время не потрачено"
  fi
fi

WT=$(mktemp -d)/merge-check
cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

printf 'merge_guard: проверяю %s -> %s на слитом состоянии\n' "$SOURCE" "$TARGET"
git worktree add --quiet --detach "$WT" "$TARGET" || die "не смог создать worktree"

# `git worktree add` даёт дерево только из ОТСЛЕЖИВАЕМЫХ файлов, а `backend/.venv` и
# `frontend/node_modules` лежат в `.gitignore`. При этом конфиг Приложения B зовёт
# именно проектное окружение (`.venv/bin/mypy`, `npx --no-install eslint`), поэтому в
# свежем worktree `pre-commit run --all-files` падал ВСЕГДА: гейт мержа был
# структурно красным на любом проекте канонной раскладки. Это тот же дефект, который
# §8.5.1 ④ разобрал у `main-guard` и не перенёс сюда.
#
# Симлинк, а не установка: merge_guard проверяет СЛИТОЕ ДЕРЕВО КОДА, а не свежесть
# зависимостей — установка стоила бы минуты на каждый мерж (бюджет §8.6), а чистое
# окружение всё равно проверяет CI на своём раннере. Список каталогов настраиваемый:
# у не-Python/не-JS проекта он свой (или пустой).
#
# Так решили три независимых развёртывания на настоящем проекте — каждое пришло к
# симлинкам само, поэтому дизайн здесь не мой выбор, а воспроизведённый результат.
BORROW_DIRS=${MERGE_GUARD_BORROW:-"backend/.venv frontend/node_modules .venv node_modules"}
for dev in $BORROW_DIRS; do
  if [[ -e "$REPO_ROOT/$dev" && ! -e "$WT/$dev" ]]; then
    mkdir -p "$WT/$(dirname "$dev")"
    ln -s "$REPO_ROOT/$dev" "$WT/$dev"
    printf '  одолжено окружение: %s\n' "$dev"
  fi
done

# Мерж в worktree КОММИТИТСЯ, а не остаётся в индексе (--no-commit).
# Иначе гейты, считающие дифф по коммитам (`merge-base..HEAD`: delivery_check
# --diff-base, okf_sync_gate --base), видят ПУСТОЙ дифф и пропускают всё:
# HEAD ещё равен $TARGET, а изменения лежат в индексе. Worktree одноразовый,
# коммит в нём никуда не уезжает.
if ! git -C "$WT" merge --no-ff --no-edit "$SOURCE" >/dev/null 2>&1; then
  git -C "$WT" merge --abort >/dev/null 2>&1 || true
  # lab-11 F13: после squash-мержа предыдущей поставки ветка, отведённая от НЕЁ,
  # а не от свежего $TARGET, несёт уже слитые изменения повторно — merge-base
  # остаётся на старой базе, и merge накладывает их поверх их же squash-версии.
  # Конфликт создан РЕЖИМОМ МЕРЖА, а не правкой, но по прежнему сообщению
  # («разреши в ветке») исполнитель шёл разрешать руками 12 тысяч чужих строк.
  # Диагноз условный, потому что настоящий параллельный конфликт выглядит так же;
  # rebase --onto безопасен в обоих случаях: уже слитое реплей пропустит, а
  # настоящий конфликт всплывёт на СВОЁМ коммите, а не на чужих.
  mb=$(git merge-base "$SOURCE" "$TARGET" 2>/dev/null || true)
  if [[ -n "$mb" && -n "$(git rev-list -n 1 "$mb..$TARGET" 2>/dev/null)" ]]; then
    printf '⚠ %s ушёл вперёд от общей базы ветки. Если изменения ветки уже слиты туда\n' "$TARGET" >&2
    printf '  squash-мержем (первая поставка после bootstrap — всегда этот случай, §8.8),\n' >&2
    printf '  конфликт создан режимом мержа, а не правкой:\n' >&2
    printf '  git rebase --onto %s %s %s — и повтори.\n' "$TARGET" "${mb:0:12}" "$SOURCE" >&2
  fi
  die "конфликт при слиянии $SOURCE в $TARGET — разреши его в ветке и повтори"
fi

# Гейты гоняются в слитом дереве. Каждый — только если он в проекте есть:
# контур разворачивается послойно, и отсутствующий слой не должен ронять мерж.
failed=()
# REPEAT — сколько раз гонять каждый оракул на слитом состоянии. Дефолт 1.
#
# Зачем настройка вообще: §8.5.2 обещает «мержит только зелёное», и для
# ДЕТЕРМИНИРОВАННЫХ оракулов это так. Флакающий оракул это обещание нарушает —
# приёмочное развёртывание намерило на ОДНОМ коммите три прогона подряд: 0, 0, 1
# (утечка всплывала по моменту GC). Два прогона из трёх смержили бы поставку с живым
# дефектом. Перепрогон N раз — не бесплатная механика (время мержа × N), поэтому по
# умолчанию 1, но проект с известной флакостью обязан поднять и записать это в
# STACK-ACCEPTANCE.md. Молчаливое «зелёное с первого раза» для флакающего сьюта —
# непокрытая область, а не гарантия.
REPEAT=${MERGE_GUARD_REPEAT:-1}

run_gate() {
  local label=$1; shift
  printf '  → %s%s\n' "$label" "$([[ "$REPEAT" -gt 1 ]] && printf ' ×%s' "$REPEAT")"
  local i
  for (( i = 1; i <= REPEAT; i++ )); do
    if ! ( cd "$WT" && "$@" >/tmp/merge_guard_out 2>&1 ); then
      printf '    %sFAIL%s (прогон %d из %d)\n' "$red" "$reset" "$i" "$REPEAT"
      sed -n '1,15p' /tmp/merge_guard_out
      failed+=("$label")
      return 0
    fi
  done
  printf '    %sOK%s\n' "$green" "$reset"
  return 0
}

# Гейт, которого может не быть: ПРОПУСК ОБЯЗАН БЫТЬ НАЗВАН.
#
# ⚠ Раньше здесь стояло `условие && run_gate …`, а печать метки живёт ВНУТРИ
# `run_gate` — значит непройденное условие не оставляло в выводе ни строки.
# Замер: прогон с `PATH` без `pre-commit` печатает «merge_guard: всё зелено» и
# выходит НУЛЁМ, не прогнав ни одного коммит-гейта; на стенде без файлов молча
# выпали ПЯТЬ из шести, и вывод сказал то же самое. То есть гейт мержа — тот
# самый, что стоит между веткой и main, — подтверждал «зелено» по пустоте.
# Это дословно класс F10 канона («названный пропуск читается как успех»), только
# здесь пропуск даже не назван, и живёт он в проверяющем слое.
skipped=()
gate_or_skip() {                 # gate_or_skip <метка> <причина|пусто> <команда…>
  local label=$1 why=$2; shift 2
  if [[ -n "$why" ]]; then
    printf '  → %s\n' "$label"
    printf '    %sПРОПУЩЕН%s: %s\n' "$yellow" "$reset" "$why"
    skipped+=("$label")
    return 0
  fi
  run_gate "$label" "$@"
}

why=""
command -v pre-commit >/dev/null 2>&1 || why="нет pre-commit в PATH"
[[ -n "$why" || -f .pre-commit-config.yaml ]] || why="нет .pre-commit-config.yaml"
gate_or_skip "pre-commit (all files)" "$why" pre-commit run --all-files

why=""; [[ -f scripts/lint/check_baseline_ratchet.sh ]] || why="нет scripts/lint/check_baseline_ratchet.sh"
gate_or_skip "baseline ratchet" "$why" env BASE="$TARGET" bash scripts/lint/check_baseline_ratchet.sh

why=""; [[ -f scripts/delivery_check.py ]] || why="нет scripts/delivery_check.py"
gate_or_skip "delivery gate + breakers" "$why" "$PY" scripts/delivery_check.py --diff-base "$TARGET"

why=""; [[ -f scripts/okf_sync_gate.py ]] || why="нет scripts/okf_sync_gate.py"
gate_or_skip "canon sync" "$why" "$PY" scripts/okf_sync_gate.py --base "$TARGET"

why=""; [[ -f delivery/evals/smoke/run.sh ]] || why="нет delivery/evals/smoke/run.sh"
gate_or_skip "smoke evals" "$why" bash delivery/evals/smoke/run.sh

# ПОСЛЕДНИМ и в основном клоне, а не в worktree: всё выше гонялось локально —
# своим Python, своим venv. Этот шаг спрашивает у CI, зелено ли оно ТАМ. Без него
# merge_guard подтверждает лишь «на моей машине прошло» (см. §3, гейт ci-status, и §8.7).
if [[ -f scripts/lint/check_ci_status.sh ]]; then
  printf '  → %s\n' "ci status ($SOURCE)"
  if bash scripts/lint/check_ci_status.sh "$SOURCE"; then
    printf '    %sOK%s\n' "$green" "$reset"
  else
    printf '    %sFAIL%s\n' "$red" "$reset"
    failed+=("ci status")
  fi
else
  printf '  → %s\n' "ci status ($SOURCE)"
  printf '    %sПРОПУЩЕН%s: нет scripts/lint/check_ci_status.sh\n' "$yellow" "$reset"
  skipped+=("ci status")
fi

# «Зелено» без этой строки означало бы «зелено по пустоте». Пропуск не роняет
# мерж — стенд может быть беден законно, — но и молчать о нём нельзя: цена
# решения видна тому, кто мержит, а не тому, кто потом ищет причину.
if (( ${#skipped[@]} )); then
  printf '\n%s⚠ merge_guard: ПРОПУЩЕНО гейтов: %s%s\n' "$yellow" "${skipped[*]}" "$reset"
  printf '  «зелено» ниже относится только к прогнанным (§8.5.2).\n'
fi

if (( ${#failed[@]} )); then
  printf '\n%smerge_guard: МЕРЖ ЗАБЛОКИРОВАН — красные гейты: %s%s\n' "$red" "${failed[*]}" "$reset" >&2
  printf 'Починить в ветке %s и повторить. Не мержить кнопкой в обход.\n' "$SOURCE" >&2
  exit 1
fi

if [[ "$DRY_RUN" == "1" ]]; then
  printf '\n%smerge_guard: всё зелено (DRY_RUN=1, мерж не выполнен)%s\n' "$green" "$reset"
  exit 0
fi

git checkout --quiet "$TARGET"
case "$MERGE_MODE" in
  squash)
    # Один коммит на поставку: каждая точка истории проверена целиком.
    git merge --squash "$SOURCE"
    git commit --no-edit
    ;;
  no-ff)
    # Только если КАЖДЫЙ коммит ветки зелёный сам по себе — иначе ломается bisect.
    printf '%s⚠ MERGE_MODE=no-ff: промежуточные коммиты ветки в историю попадут\n' "$yellow"
    printf '  непроверенными. Убедись, что каждый из них собирается (§8.8).%s\n' "$reset"
    git merge --no-ff --no-edit "$SOURCE"
    ;;
  *) die "неизвестный MERGE_MODE='$MERGE_MODE' (squash|no-ff)" ;;
esac
printf '\n%smerge_guard: %s слит в %s (%s) после зелёных гейтов%s\n' \
  "$green" "$SOURCE" "$TARGET" "$MERGE_MODE" "$reset"
