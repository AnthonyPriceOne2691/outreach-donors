#!/usr/bin/env bash
# Вердикт мутационного гейта: что считать доказательством и когда краснеть.
#
# Часть `check_mutation_gate.sh` (`cqg@1.86`). Отделено от запуска сознательно:
# главный урок этого гейта — код возврата инструмента вердиктом НЕ является, и
# судить можно только по счётчикам. Держать это рядом с логикой запуска значило
# смешивать «что мы сделали» с «что это доказывает».

if ( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" export-cicd-stats >/dev/null 2>&1 ) && [[ -f "$stats" ]]; then
  killed=$(grep -oE '"killed"[[:space:]]*:[[:space:]]*[0-9]+' "$stats" | grep -oE '[0-9]+$' | head -1)
  survived=$(grep -oE '"survived"[[:space:]]*:[[:space:]]*[0-9]+' "$stats" | grep -oE '[0-9]+$' | head -1)
fi
if [[ -z "$killed" && -z "$survived" ]]; then
  killed=$(grep -oE '🎉 *[0-9]+' "$out" | grep -oE '[0-9]+' | tail -1)
  survived=$(grep -oE '🙁 *[0-9]+' "$out" | grep -oE '[0-9]+' | tail -1)
fi
if [[ -z "$killed" && -z "$survived" ]]; then
  res=$( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results 2>/dev/null )
  killed=$(printf '%s\n' "$res" | grep -oE '🎉 *[0-9]+' | grep -oE '[0-9]+' | head -1)
  survived=$(printf '%s\n' "$res" | grep -oE '🙁 *[0-9]+' | grep -oE '[0-9]+' | head -1)
fi

# Ни один источник не дал числа. Причин несколько, и они РАЗНЫЕ: ключи мутантов не
# совпали с путями импорта, mutmut не смог запустить pytest, мутанты не покрыты,
# mutmut упал (rc != 0), формат вывода незнаком. Каждая печатается с уликами, и ни
# одна не притворяется диагнозом «нет тестов».
# Один разборщик на ВСЕ исходы «гейт не судит»: конкретная причина обязана бить
# общую, а улика печатается всегда. До lab-12 конкретные причины были распределены
# по двум местам, и та, что стояла позже, была недостижима: mutmut выходил rc=1,
# срабатывала общая ветка «завершился с кодом 1», а настоящая причина (не смог
# запустить pytest / мутанты не покрыты) не называлась никогда. Тест поймал это
# на первой же попытке — ветку писали, а достижимость не проверили.
undecided() {
  # Расхождение ключей — самый вероятный исход на непривычной раскладке, и апстрим
  # называет его сам. Ветка стоит ПЕРВОЙ, потому что mutmut при этом выходит rc=0:
  # общая ветка «завершился с кодом N» его бы не поймала, а «мутанты не
  # сгенерированы» соврала бы про тесты.
  if grep -q 'Filtered for specific mutants, but nothing matches' "$out"; then
    # Изменённые файлы не дали ни одного мутанта: константы, только импорты,
    # `__all__`. Это честный «нечего судить», а НЕ «тесты плохие» и не поломка
    # гейта — иначе исполнитель увидел бы трассировку AssertionError апстрима.
    printf '%smutation: изменённые файлы не дали мутантов (константы? только импорты?)\n' "$yellow"
    printf 'Область была: %s. Гейт не судит — судить нечего.%s\n' "${mut_globs[*]}" "$reset"
  elif grep -q 'none match any mutant key' "$out"; then
    printf '%s⚠ mutation: путь импорта пакета не совпал с путём файла — гейт не судит\n' "$yellow"
    printf '(тесты тут ни при чём). mutmut выводит ключ мутанта ИЗ ПУТИ ФАЙЛА, и он обязан\n'
    printf 'совпасть с тем, как тесты импортируют пакет. Гейт уже запускается из `%s`,\n' "$MUT_CWD"
    printf 'то есть ждёт `import %s...`. Расхождение обычно даёт лишний `pythonpath` или\n' "$MUT_SRC"
    printf 'sys.path-инъекция в conftest. Ключи, которые не сошлись, — в выводе ниже.%s\n' "$reset"
  elif grep -qE 'BadTestExecutionCommandsException|Failed to run pytest' "$out"; then
    printf '%s⚠ mutation: mutmut не смог ЗАПУСТИТЬ pytest — гейт не судит (тесты тут ни при чём).\n' "$yellow"
    printf 'mutmut копирует `source_paths` в `%s/mutants/` и гоняет pytest ИЗ НЕГО,\n' "$MUT_CWD"
    printf 'поэтому путь до тестов обязан резолвиться оттуда. `also_copy` в дефолте берёт\n'
    printf '`tests/` относительно `%s` — если тесты лежат иначе, добавь их туда.\n' "$MUT_CWD"
    printf 'Диагноз апстрима включается `debug=true` в [tool.mutmut].%s\n' "$reset"
  elif grep -qE 'could not find any test case|do not cover any code' "$out"; then
    printf '%s⚠ mutation: pytest запустился, но НИ ОДИН мутант не покрыт тестами — гейт не судит.\n' "$yellow"
    printf 'Тесты импортируют установленный пакет, а не мутированную копию из\n'
    printf '`%s/mutants/` — измерено на mutmut %s. Это НЕ «тесты ничего не\n' "$MUT_CWD" "$MUT_VER"
    printf 'утверждают»: см. §3.7.%s\n' "$reset"
  elif (( rc != 0 )); then
    printf '%s⚠ mutation: mutmut %s завершился с кодом %s — гейт не судит, вывод ниже%s\n' \
      "$yellow" "$MUT_VER" "$rc" "$reset" >&2
  elif [[ -n "${1:-}" ]]; then
    printf '%s⚠ mutation: мутанты не сгенерированы (пустые файлы? нет тестов?) — гейт не судит%s\n' \
      "$yellow" "$reset"
  else
    printf '%s⚠ mutation: счётчики не найдены в выводе mutmut %s — формат незнаком,\n' \
      "$yellow" "$MUT_VER"
    printf 'гейт не судит (это НЕ «мутантов нет»). Вывод ниже.%s\n' "$reset" >&2
  fi
  tail -15 "$out" >&2
  exit 0
}

if [[ -z "$killed" && -z "$survived" ]]; then
  undecided
fi

killed=${killed:-0}; survived=${survived:-0}
total=$((killed + survived))

if (( total == 0 )); then
  # Счётчики есть, но нули: тот же разборщик, аргумент включает ветку
  # «мутанты не сгенерированы» как ПОСЛЕДНЮЮ, а не как единственную.
  undecided empty
fi

pct=$(( killed * 100 / total ))
printf 'mutation: убито %d из %d (%d%%), выжило %d\n' "$killed" "$total" "$pct" "$survived"

if (( pct >= MIN_KILLED )); then
  printf '%smutation: OK%s\n' "$green" "$reset"
  exit 0
fi

printf '\n%sERROR%s: убито %d%% мутантов, цель ≥%d%%.\n' "$red" "$reset" "$pct" "$MIN_KILLED" >&2
# Улика обязательна: красный гейт без списка выживших учит искать не там.
# Список даёт `results`, а НЕ `show`: у 3.x `show` требует имя мутанта
# (`Error: Missing argument 'MUTANT_NAME'`), и голый вызов печатал только эту
# ошибку — под `2>/dev/null` не печатал вообще ничего. То есть обвинение
# предъявлялось без улик всё время существования ветки.
printf 'Выжившие мутанты = поведение, которое тесты НЕ проверяют:\n' >&2
( cd "$REPO_ROOT/$MUT_CWD" && "$MUTMUT" results 2>/dev/null ) | grep -F survived | head -30 >&2
printf 'Разобрать конкретный: cd %s && mutmut show <имя-из-списка>\n' "$MUT_CWD" >&2
printf 'Починка: добавить утверждения на выжившие случаи, а не поднимать порог.\n' >&2
[[ "$STRICT" == "0" ]] && { printf '%sWARNING (STRICT=0)%s\n' "$yellow" "$reset" >&2; exit 0; }
exit 1
