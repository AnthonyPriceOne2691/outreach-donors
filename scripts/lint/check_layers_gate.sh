#!/usr/bin/env bash
# Направление зависимостей между слоями — одна роль, два инструмента.
#
# Зачем обёртка, если раньше в pre-commit звался голый `lint-imports`. Две причины,
# и обе замерены, а не выведены из общих соображений.
#
# ① TS-инструмент падает ОТКРЫТО, python-овский — закрыто. Замер на одинаковых
#    стендах (dependency-cruiser 18.1.1 / import-linter 2.13):
#
#      путь/пакет существует, но модулей в нём нет
#        depcruise    → exit 0, «✔ no dependency violations found (0 modules cruised)»
#        lint-imports → exit 1, «Missing layer in container»
#
#    То есть на TS «зелёный на непроверенном» получался штатно и молча: настроил
#    путь мимо исходников — и гейт слоёв больше ничего не судит, оставаясь зелёным
#    навсегда. Ровно тот класс, ради которого каждый гейт канона печатает число
#    просмотренного (§6). Python-половину при этом чинить НЕ надо, и она здесь
#    проходит насквозь без изменения поведения — обёртка написана ради TS.
#
# ② Роль была невидима мета-гейту. `check_gate_coverage.sh` обходит
#    `scripts/lint/check_*.sh|py`, а `lint-imports` — внешний бинарь, вызываемый
#    из конфига напрямую. Значит «гейт слоёв отключили» мета-гейт не заметил бы:
#    единственная роль каталога §3, за подключением которой никто не следил.
#
# Это ПОДСТАНОВКА инструмента в существующую строку каталога §3, а не новый гейт:
# роль одна («направление зависимостей»), порог один, сообщение одно. Бюджет
# §9.1a не трогается — единица счёта там строка таблицы, а не файл скрипта.
#
# Настройка (env):
#   LINT_BE_DIR, LINT_TS_SRC, LINT_FE_DIR, LINT_VENV
#   LINT_DEPCRUISE_CONFIG — конфиг dependency-cruiser, если он не в корне
#   STRICT=0              — soft (warning, exit 0)
#
# Baseline у роли нет и не было: легаси замораживается в САМОМ конфиге
# (`ignore_imports` с TODO у import-linter, `forbidden[].from.pathNot` у
# dependency-cruiser) — тот же принцип тающего снимка, только хранится в контракте.

set -uo pipefail

STRICT=${STRICT:-1}
BE_DIR=${LINT_BE_DIR:-backend}
FE_DIR=${LINT_FE_DIR:-frontend}
TS_SRC=${LINT_TS_SRC:-$FE_DIR/src}
VENV=${LINT_VENV:-$BE_DIR/.venv}
# Оба написания `LINT_VENV`, как у остальных потребителей (§6, lab-9 №7).
[[ -d "$VENV" || ! -d "$BE_DIR/$VENV" ]] || VENV="$BE_DIR/$VENV"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT" || exit 1

red=$(printf '\033[31m'); yellow=$(printf '\033[33m'); green=$(printf '\033[32m'); reset=$(printf '\033[0m')

failures=0
judged=0
skipped=()

# --- python: import-linter -----------------------------------------------------
IL_CFG=""
for c in "$BE_DIR/.importlinter" .importlinter setup.cfg; do
  [[ -f "$c" ]] && grep -qs '^\[importlinter' "$c" && { IL_CFG="$c"; break; }
done
if [[ -n "$IL_CFG" ]]; then
  LI="$VENV/bin/lint-imports"
  [[ -x "$LI" ]] || LI=$(command -v lint-imports 2>/dev/null || true)
  if [[ -z "$LI" ]]; then
    skipped+=("python: есть $IL_CFG, но lint-imports не найден (pip install import-linter)")
  else
    # cwd сменится — путь до инструмента обязан стать абсолютным. Та же строка
    # есть у мутационного гейта, и не для красоты: при `LINT_VENV=backend/.venv`
    # относительный `backend/.venv/bin/lint-imports` после `cd backend` не
    # существует, и гейт уходил бы в «инструмент не найден» ПРИ установленном
    # инструменте — мягкий пропуск с ложным диагнозом. Поймано классовым оракулом
    # `BothVenvSpellingsAreHonoured`, а не прогоном на стенде.
    [[ "$LI" == /* ]] || LI="$REPO_ROOT/$LI"
    # Запускать из каталога с конфигом: import-linter читает его из cwd.
    il_out=$( cd "$(dirname "$IL_CFG")" && "$LI" 2>&1 ); il_rc=$?
    # «Analyzed N files» — то самое число просмотренного. Ноль означает, что
    # контракты прошли, не увидев кода; зелёным это быть не может.
    il_seen=$(printf '%s\n' "$il_out" | grep -oE 'Analyzed [0-9]+ file' | grep -oE '[0-9]+' | head -1)
    il_seen=${il_seen:-0}
    if (( il_rc != 0 )); then
      printf '%s✗ слои (python): контракты нарушены%s\n' "$red" "$reset" >&2
      printf '%s\n' "$il_out" | sed 's/^/  /' >&2
      failures=$((failures + 1))
    elif (( il_seen == 0 )); then
      printf '%s✗ слои (python): контракты прошли, просмотрев 0 файлов — это не «чисто».\n' "$red"
      printf 'Проверь root_package в %s: гейт судил пустоту.%s\n' "$IL_CFG" "$reset"
      failures=$((failures + 1))
    else
      printf '%sслои (python): OK%s — просмотрено %s файл(ов), конфиг %s\n' \
        "$green" "$reset" "$il_seen" "$IL_CFG"
    fi
    judged=$((judged + 1))
  fi
else
  skipped+=("python: конфига import-linter нет ($BE_DIR/.importlinter)")
fi

# --- ts: dependency-cruiser ----------------------------------------------------
DC_CFG=${LINT_DEPCRUISE_CONFIG:-}
if [[ -z "$DC_CFG" ]]; then
  for c in .dependency-cruiser.cjs .dependency-cruiser.js .dependency-cruiser.json \
           .dependency-cruiser.mjs "$FE_DIR/.dependency-cruiser.cjs" \
           "$FE_DIR/.dependency-cruiser.js" "$FE_DIR/.dependency-cruiser.json"; do
    [[ -f "$c" ]] && { DC_CFG="$c"; break; }
  done
fi
if [[ -n "$DC_CFG" ]]; then
  DC="$FE_DIR/node_modules/.bin/depcruise"
  [[ -x "$DC" ]] || DC="node_modules/.bin/depcruise"
  [[ -x "$DC" ]] || DC=$(command -v depcruise 2>/dev/null || true)
  if [[ -z "$DC" ]]; then
    skipped+=("ts: есть $DC_CFG, но depcruise не найден (npm i -D dependency-cruiser)")
  elif [[ ! -d "$TS_SRC" ]]; then
    skipped+=("ts: нет каталога $TS_SRC (настрой LINT_TS_SRC, §6)")
  else
    dc_out=$("$DC" "$TS_SRC" --config "$DC_CFG" --output-type err 2>&1); dc_rc=$?
    # Число просмотренного печатает сам инструмент: «(N modules, M dependencies
    # cruised)». Достаём его ДО разбора вердикта — именно оно отличает «чисто» от
    # «смотрел не туда», и именно на нём инструмент даёт зелёное молча.
    dc_seen=$(printf '%s\n' "$dc_out" | grep -oE '\([0-9]+ modules' | grep -oE '[0-9]+' | head -1)
    dc_seen=${dc_seen:-0}
    if printf '%s\n' "$dc_out" | grep -q '^ *ERROR:'; then
      # Код возврата тут ТОТ ЖЕ (1), что у настоящего нарушения, — различать
      # приходится по тексту. Пропуск с чужим диагнозом хуже красного гейта:
      # «нарушение слоёв» вместо «путь не читается» отправляет чинить не туда.
      printf '%s✗ слои (ts): инструмент не смог прочитать область — это НЕ вердикт.%s\n' \
        "$red" "$reset" >&2
      printf '%s\n' "$dc_out" | sed 's/^/  /' >&2
      failures=$((failures + 1))
    elif (( dc_rc != 0 )); then
      printf '%s✗ слои (ts): контракты нарушены%s\n' "$red" "$reset" >&2
      printf '%s\n' "$dc_out" | sed 's/^/  /' >&2
      failures=$((failures + 1))
    elif (( dc_seen == 0 )); then
      printf '%s✗ слои (ts): 0 модулей просмотрено — «нарушений нет» тут означает\n' "$red"
      printf '«гейт не видел кода». Проверь LINT_TS_SRC (§6) и include в %s.%s\n' \
        "$DC_CFG" "$reset"
      failures=$((failures + 1))
    else
      printf '%sслои (ts): OK%s — просмотрено %s модул(ей), конфиг %s\n' \
        "$green" "$reset" "$dc_seen" "$DC_CFG"
    fi
    judged=$((judged + 1))
  fi
else
  skipped+=("ts: конфига dependency-cruiser нет (.dependency-cruiser.cjs)")
fi

# --- итог ----------------------------------------------------------------------
if (( ${#skipped[@]} )); then
  printf '%s⚠ половина(ы) НЕ проверены:%s\n' "$yellow" "$reset"
  printf '  · %s\n' "${skipped[@]}"
fi

if (( judged == 0 )); then
  printf '%s⚠ гейт слоёв пропущен — ни одного конфига контрактов не найдено.\n' "$yellow"
  printf 'Слои без контракта не проверяет никто: заведи %s/.importlinter (§3.6) или\n' "$BE_DIR"
  printf '.dependency-cruiser.cjs (§3.6a). Это НЕ «нарушений нет».%s\n' "$reset"
  exit 0
fi

if (( failures == 0 )); then
  printf '%slayers: OK%s — половин просужено %d\n' "$green" "$reset" "$judged"
  exit 0
fi

printf '\n%sERROR%s: направление зависимостей нарушено (%d половина(ы)).\n' \
  "$red" "$reset" "$failures" >&2
printf 'Слой не разворачивают импортом обратно: вынеси общий тип вниз или объяви\n' >&2
printf 'Protocol/интерфейс в потребителе (§2.3). Легаси — во frozen-ignore конфига.\n' >&2
[[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
exit 1
