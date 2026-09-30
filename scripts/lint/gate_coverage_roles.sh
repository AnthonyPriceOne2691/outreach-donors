#!/usr/bin/env bash
# Мета-гейт, часть вторая: карта ролей обязана совпадать с деревом.
#
# Часть `check_gate_coverage.sh` (`cqg@1.86`) — разрезан по планке 300 строк
# (Delivery §9.1a п.5). Здесь вопрос «что проект о себе ЗАЯВИЛ»: карта ролей
# `delivery/STACK-ACCEPTANCE.md`, объявления `not-applicable.json` и обратная
# сверка «конфиг ссылается на файл, которого нет». Перечисление гейтов и решение
# «подключён ли» остались во входном скрипте.
#
# Подключается через `source` и выполняется линейно, в том же процессе.

# --- Карта ролей обязана совпадать с деревом (cqg@1.70) -----------------------
# `delivery/STACK-ACCEPTANCE.md` — то, чем проект ОТВЕЧАЕТ на вопрос «что здесь
# закрыто». До сих пор её не читал никто: чистая проза, заполняемая руками.
#
# Замер, из которого правило родилось (Astro/TS): в карте стояло четыре ✅ —
# `file-length` «маска изменена», `eslint`, его ратчет и `prettier`
# «подключён», — а в конфиге маски остались шаблонными, и все четыре смотрели в
# пустоту. Плюс ✅ у `jscpd`, которого в проекте не установлено вовсе. Пять
# ложных строк в документе, который для читателя и есть ответ про покрытие.
#
# Сверяется НЕ «работает ли гейт» (это A1 и канарейка), а согласованность
# ЗАЯВЛЕНИЯ с механикой: ✅ против объявленного `n/a`, `n/a` без объявления,
# ссылка на несуществующий скрипт. Заявление и механика расходятся молча — и
# именно это молчание документ и продаёт как покрытие.
MAP_FILE="delivery/STACK-ACCEPTANCE.md"
if [[ -f "$MAP_FILE" && -n "${PY_NA:-$(command -v python3 2>/dev/null || true)}" ]]; then
  # Причина бывает ДВУХ законных происхождений, и обе печатаются на прогоне:
  # проектная (not-applicable.json) и канонная (not_wired_reason() — ручной
  # DoD-шаг, измерительный инструмент, CI-only). Первая редакция знала только
  # первую и обвинила check_diff_coverage.sh, у которого причина канонная и
  # видна на каждом прогоне. Ложное срабатывание — дефект проверки (§4.3b).
  # ⚠ Список считается ЗДЕСЬ, а не из массива `gates`: он заполняется ниже по
  # файлу, и первая редакция читала его пустым — то есть канонные причины не
  # учитывались вовсе, и проверка обвиняла всех подряд. Молчаливая пустота
  # массива вместо ошибки — ровно тот сорт отказа, против которого весь контур.
  canon_na=""
  while IFS= read -r g; do
    [[ -n "$g" ]] || continue
    b=$(basename "$g")
    not_wired_reason "$b" >/dev/null 2>&1 && canon_na="$canon_na$b "
  done < <(git ls-files "$LINT_DIR/check_*.sh" "$LINT_DIR/check_*.py" 2>/dev/null)
  map_out=$("${PY_NA:-python3}" - "$MAP_FILE" "$NA_FILE" "$LINT_DIR" "$canon_na" 2>&1 <<'PYMAP'
import json, os, re, sys
mp, na_file, lint_dir = sys.argv[1], sys.argv[2], sys.argv[3]
canon_na = set((sys.argv[4] if len(sys.argv) > 4 else "").split())
declared = {}
if os.path.isfile(na_file):
    try:
        declared = json.load(open(na_file, encoding="utf-8")) or {}
    except Exception:
        declared = {}          # битый файл ругается в своём месте, не тут
# ⚠ ДВА множества, и путать их — смысловая ошибка, а не небрежность:
#   not-applicable.json — роль НЕ покрыта: на этом стеке у неё нет предмета;
#   not_wired_reason()  — роль покрыта, просто не на коммите (pre-push, CI,
#                         ручной DoD-шаг) либо это инструмент, а не гейт.
# Первая редакция слила их и обвинила check_new_dependency.py: в карте честное
# (роль закрыта на pre-push), а проверка прочла канонную причину как
# «объявлен неприменимым». Для «плюс против n/a» годится ТОЛЬКО первое
# множество; для «n/a без объявления» — оба: причина печатается в обоих случаях.
na_scripts = {str(k).split(":")[0] for k in declared}
reasoned = na_scripts | canon_na

# Строка карты называет роль ЛИБО именем файла (check_jscpd_gate.sh), либо
# ярлыком (jscpd-DRY) — обе формы живые, и вторая встречается чаще: карта
# пишется человеком про роли, а не про файлы. Псевдоним выводится из имени
# скрипта, а не берётся из выдуманной таблицы соответствий: такая таблица
# разъехалась бы с каталогом §3 на первом же новом гейте.
#
# ⚠ Ни одного символа-бэктика в этом блоке: heredoc живёт внутри $( … ), и
# разбор bash на них спотыкается («unexpected EOF while looking for matching»)
# даже при закавыченном разделителе. Поймано прогоном, а не чтением.
def aliases(fname):
    stem = re.sub(r"^check_|_gate$|\.(sh|py)$", "", fname)
    stem = re.sub(r"_gate$", "", stem)
    return {fname.lower(), stem.lower(), stem.replace("_", "-").lower()}

scripts = [f for f in sorted(os.listdir(lint_dir))
           if f.startswith("check_") and f.endswith((".sh", ".py"))] \
    if os.path.isdir(lint_dir) else []
by_alias = {a: f for f in scripts for a in aliases(f)}

problems = []
for line in open(mp, encoding="utf-8"):
    if not line.lstrip().startswith("|"):
        continue
    names = []
    for tok in re.findall(r"\x60([^\x60]+)\x60", line):
        key = tok.strip().lower()
        # jscpd-DRY -> ярлык jscpd;  check_x.sh --rule y -> имя файла
        key = re.split(r"\s|--", key)[0].strip()
        for cand in (key, key.split("-")[0]):
            if cand in by_alias:
                names.append(by_alias[cand])
                break
    names = sorted(set(names))
    if not names:
        continue
    low = line.lower()
    claims_na = "n/a" in low or "⛔" in line
    claims_on = ("✅" in line or "подключ" in low) and not claims_na
    for n in names:
        if claims_on and n in na_scripts:
            problems.append(
                "%s: в карте ролей ✅, а в not-applicable.json объявлен n/a" % n)
        if claims_na and n not in reasoned and os.path.isfile(
                os.path.join(lint_dir, n)):
            problems.append(
                "%s: в карте ролей n/a, а объявления нет — причина живёт только "
                "в прозе и на прогонах не печатается" % n)
        if not os.path.isfile(os.path.join(lint_dir, n)) and claims_on:
            problems.append("%s: в карте ролей ✅, а скрипта в %s нет"
                            % (n, lint_dir))
for p in sorted(set(problems)):
    sys.stderr.write(p + "\n")
raise SystemExit(1 if problems else 0)
PYMAP
)
  if (( $? != 0 )); then
    printf '%s✗ карта ролей разошлась с деревом (%s):%s\n' "$red" "$MAP_FILE" "$reset" >&2
    printf '%s\n' "$map_out" | sed 's/^/  /' >&2
    printf 'Карта ролей — то, чем проект отвечает на вопрос «что закрыто».\n' >&2
    printf 'Заявление, разошедшееся с механикой, продаёт покрытие, которого нет.\n' >&2
    exit 1
  fi
fi

# --- Свежесть карты ролей (`cqg@1.99`) ----------------------------------------
# Сверка выше судит СОГЛАСОВАННОСТЬ заявления с механикой и ничего не знает о
# том, когда карту последний раз читали глазами. А половина карты негейтируема по
# природе — «полнота, не только зелёность» и разбор слепых пятен заполняются
# руками, значит деградируют первыми. Это тот же класс, что протухшая стоимость
# чтения: объявление живёт, факт уехал, никто не сверяет.
#
# Поэтому у карты две даты, и семантика взята у OKF §7.2 ЦЕЛИКОМ, чтобы не
# заводить вторую механику свежести: `checked_at:` — когда сверяли,
# `stale_after:` — абсолютная дата, stale когда `today >= stale_after`.
# Сравнение лексикографическое: на ISO-датах это верно и не требует GNU `date`,
# которого на macOS нет (тот же урок, что у мутационного гейта с `timeout`).
#
# Печатается ADVISORY, а не отказ, и это решение с ценой: просроченная карта —
# повод её перечитать, а не остановить поставку, и слот бюджета §9.1a (22 из 22)
# новая проверка не занимает. Красное здесь означало бы, что проект, к которому
# три месяца не прикасались, не может сдать ни одной поставки, не переписав
# документ, — §4.3b случай 1, дальше гейт снимают вместе со сверкой.
if [[ -f "$MAP_FILE" ]]; then
  map_today=$(date +%F)
  map_checked=$(grep -m1 -oE '^checked_at:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}' "$MAP_FILE" 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' || true)
  map_stale=$(grep -m1 -oE '^stale_after:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}' "$MAP_FILE" 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' || true)
  if [[ -z "$map_checked" || -z "$map_stale" ]]; then
    printf '%s  ○  карта ролей без даты сверки: нет checked_at/stale_after — заявления не старятся%s\n' \
      "$yellow" "$reset"
  elif [[ ! "$map_today" < "$map_stale" ]]; then
    printf '%s  ○  карта ролей просрочена: сверена %s, годна была до %s — перечитать и продлить%s\n' \
      "$yellow" "$map_checked" "$map_stale" "$reset"
  else
    printf '  ✓  карта ролей сверена %s, годна до %s\n' "$map_checked" "$map_stale"
  fi
fi

unwired=()
unwired_rules=()
exempted=0
rules_checked=0

# Все правила, РЕАЛЬНО вызванные в конфигах — собираем один раз и сравниваем
# точно (`grep -qxF`). Первая версия проверяла каждое имя anchored-регуляркой
# `--rule[[:space:]]+$rule([[:space:]]|$|")` и давала ложные срабатывания на
# подключённых правилах: `$` внутри группы ERE ведёт себя по-разному в
# реализациях grep — тот же класс, что §3.1a про замер портируемости.
wired_rules=$(grep -ohsE -- '--rule[[:space:]]+[A-Za-z0-9_-]+' "${CONFIGS[@]}" 2>/dev/null \
  | sed -E 's/^--rule[[:space:]]+//' | sort -u)
for path in "${gates[@]}"; do
  base=$(basename "$path")
  if reason=$(not_wired_reason "$base"); then
    printf '  ○ %-30s не подключён осознанно: %s\n' "$base" "$reason"
    exempted=$((exempted + 1))
    continue
  fi
  # Роль неприменима НА ЭТОМ СТЕКЕ — объявлено проектом, причина печатается.
  if reason=$(na_reason "$base"); then
    printf '  ○ %-30s n/a на этом стеке: %s\n' "$base" "$reason"
    exempted=$((exempted + 1))
    continue
  fi
  # -F: имя содержит точку, как regex она матчила бы лишний символ.
  if ! grep -qsF -- "$base" "${CONFIGS[@]}"; then
    unwired+=("$base")
    continue
  fi

  # --- Гранулярность ПРАВИЛА, а не файла (полевая находка F7).
  # Многоправильный скрипт считался подключённым, если в конфиге упомянут хотя
  # бы один его вызов. Правило `unstructured-log` прожило так целый релиз: было
  # в каталоге §3, в таблице §3.1 и в теле скрипта — и ни в одном конфиге.
  #
  # Поддержку флага определяем ГРЕПОМ, и только потом исполняем. Первая версия
  # этой проверки запускала `--list-rules` у каждого скрипта подряд — и повесила
  # мета-гейт: он вызвал сам себя (он тоже check_*.sh) и ушёл в рекурсию, а
  # `check_deps_audit.sh` при таком «опросе» полез бы в сеть. Опрос обязан быть
  # безопасным: только те скрипты, которые флаг объявили.
  if [[ "$base" != "$(basename "${BASH_SOURCE[0]}")" ]] \
     && grep -qsF -- '--list-rules' "$path"; then
    case "$path" in
      *.py) rules=$(python3 "$path" --list-rules 2>/dev/null) ;;
      *)    rules=$(bash "$path" --list-rules 2>/dev/null) ;;
    esac
    while IFS= read -r rule; do
      [[ -n "$rule" ]] || continue
      # Гранулярность ПРАВИЛА и здесь: `check_ast_gate.py` несёт четыре правила,
      # и на чужом стеке неприменимы обычно все — но объявлять их надо поимённо,
      # иначе «выключить одно» неотличимо от «выключить весь скрипт».
      if reason=$(na_reason "$base:$rule"); then
        printf '  ○ %-30s n/a на этом стеке: %s\n' "$base --rule $rule" "$reason"
        exempted=$((exempted + 1))
        continue
      fi
      rules_checked=$((rules_checked + 1))
      if ! printf '%s\n' "$wired_rules" | grep -qxF -- "$rule"; then
        unwired_rules+=("$base --rule $rule")
      fi
    done < <(printf '%s\n' "$rules")
  fi
done

# Обратная сторона: конфиг ссылается на скрипт, которого нет.
#
# Ссылка ПОД ОХРАНОЙ `[[ -f … ]]` пропускается. `merge_guard.sh` вызывает
# опциональные слои именно так — `[[ -f scripts/okf_sync_gate.py ]] && run_gate …`, —
# потому что контур разворачивается послойно (AGENT_STACK §2.C прямо называет
# конфигурацию без OKF рабочей, а CQG §5 шаг 9 ставит merge_guard внутри ② — до ③).
# Без этого исключения мета-гейт был НАВСЕГДА красным в разрешённой конфигурации
# ①②④: ругался на опциональный скрипт опционального слоя, который уже защищён
# охраной. Это §4.3b случай 1 — «гейт ругается на то, чего проверять не должен», а
# дальше по тому же §4.3b такой гейт сначала бесит, потом его снимают. Нашло восьмое
# развёртывание, в момент когда ② уже стоял, а ③ ещё нет.
missing=()
while IFS= read -r ref; do
  [[ -n "$ref" ]] || continue
  [[ -e "$ref" ]] && continue
  # Охрана в той же строке, где ссылка. Признаются ВСЕ ходовые формы:
  # `[[ -f X ]]` (bash), `[ -f X ]` (POSIX) и `test -f X`, с флагами -f/-x/-e.
  #
  # lab-12: признавалась только двойная скобка, а §8.3 этого же канона
  # поставляет `if [ -f scripts/okf_sync_gate.py ]; then` — одинарную, потому что
  # `run:` в GitHub Actions пишут в POSIX-стиле. В полном развёртывании дефект
  # МАСКИРУЕТСЯ: грепа ищет по всем конфигам сразу, и двойная скобка из
  # `merge_guard.sh` закрывает ссылку, найденную в workflow. Виден он там, где
  # merge_guard не развёрнут или форма другая, — и тогда мета-гейт красен на
  # конфигурации ①②④ без OKF, которую AGENT_STACK §2.C прямо называет рабочей.
  # То есть F6 второй раз: правку сделали на форму, а не на свойство «ссылка
  # защищена проверкой существования».
  guard="(\[\[?[[:space:]]+-[fxe][[:space:]]+\"?${ref//\//\\/}\"?[[:space:]]+\]\]?"
  guard="$guard|test[[:space:]]+-[fxe][[:space:]]+\"?${ref//\//\\/}\"?)"
  if grep -qhsE -- "$guard" "${CONFIGS[@]}" 2>/dev/null; then
    printf '%s  ○  %s: опционален (вызов под охраной -f) — слой не развёрнут%s\n' \
      "$yellow" "$ref" "$reset"
    continue
  fi
  missing+=("$ref")
done < <(grep -ohsE 'scripts/[A-Za-z0-9_/.-]+\.(sh|py)' "${CONFIGS[@]}" | sort -u)

