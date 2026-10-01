# Verify report

**Date:** 2026-10-01
**Verifier:** process:ci — джобы `check`, `gates` и `delivery` на PR; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** n/a (все утверждения ведут к примерам A1–A13 спеки; подпись под самими примерами — `human_ok_spec`, сейчас deferred, закрывается до handoff)
**CI run:** нет — ветка не запушена: пуш, PR и прогон CI делает координатор после ревью
**Commit:** 7173edd — локальные прогоны сняты на нём и на правках T8

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 0 упавших.
- [x] PASS — `ruff check` и `ruff format --check` по `backend/ tests/ scripts/`
      (448 файлов), `mypy backend/` (290 файлов), `scripts/gates.py` (444 файла,
      нарушений нет, в том числе `public-repo`), `scripts/complexity.py`
      (304 файла, снимок совпадает; `scripts/contour_waves.py` — 460 строк,
      худшая функция `collect` — 14 по мере ратчета, ruff C901 чист).
- [x] PASS — `contour_doctor.py`: AUTO 36 · WEAK 10 · ABSENT 6 · TOOL 3 · SKIP 2 ·
      **DEAD 0**. До среза в этом же дереве: AUTO 33 · WEAK 13 — три WEAK
      «записи о версии» стали AUTO: `stack:` STATUS теперь совпадает с
      CONSTITUTION и STACK-ACCEPTANCE. ABSENT 6 — четыре файла канонов: вариант D,
      в worktree их нет, они лежат только в основной копии.
- [x] PASS — `check_gate_coverage.sh`: OK — 15 скриптов, подключено 11,
      осознанно нет 4; правил сверено 10 (5 конфигов).
- [x] PASS — `delivery_check.py --require-ci --diff-base origin/main`: 0 errors.
      Breakers: файлов кода 3, net_loc 761 (+761/−0) при пределе 800; всего
      в диффе 16 файлов.
- [x] PASS — `scripts/check_irreversible_signature.sh`: подпись сходится с
      объявлением, exit 0.

## Behavior oracles

- [x] PASS — `tests/test_contour_waves.py`: 25 тестов, по тесту на пример A1–A13.
- [x] PASS — полный набор на своей базе `outreach_test_sales_a`: 2977 passed за 264 с.

### Обратные прогоны

| Что сломано | Ожидание | Факт |
|---|---|---|
| проверка заменена заглушкой: `main` молчит и выходит 0 | новые тесты падают | 20 из 25 упали; прошли 4 положительных контроля и тест обратного прогона — они и ждут зелёного |
| маска кода продаж не совпадает ни с чем (`sales-code`, `sales-in-base`) | тесты A2 и A5 падают | упали 4 из 4: A2, A3, A4, A5; та же порча — тест `test_reverse_run_empty_sales_mask_…`, файлом |
| в STATUS среза нет строки `shared_changes:` (настоящее дерево) | A13 красный | exit 1, названы 5 файлов: `quality.yml`, `contour_waves.py`, `pre-push`, `adapted.json`, `test_contour_waves.py`; с `--warn` — exit 0 |
| нет файла `scripts/contour_waves.py` | мета-гейт красный | `check_gate_coverage.sh` exit 1: «упомянут в конфиге, но файла нет» |

### Настоящее дерево ветки

`python scripts/contour_waves.py` на дереве этой ветки. Подложенный файл удалён
и не закоммичен, реестр после прогона B возвращён.

| Пример | Вход | Выход |
|---|---|---|
| A1 | дерево ветки без `sales/`, `--base origin/main` | exit 0, «продаж в дереве нет — судить нечего» |
| A4 | подложен `backend/features/sales/__init__.py`; реестр как в ветке — В0 `deployed` этим PR | exit 0, «В1: триггер сработал, предел — следующий PR продаж» и четыре строки «пока волну судит человек» (В3б, В3в, В4, В2) |
| A2 | то же, В0 в рабочей копии реестра — `pending` | exit 1, «В0: в дереве есть backend/features/sales/, волна не развёрнута — разверни или запиши weak/absent/n/a reason=…» |
| A3 | то же, `--warn` | exit 0, та же строка с пометкой «предупреждение» |
| A5 | подложен `__init__.py`; база PR — коммит-объект поверх точки ветвления с `sales/__init__.py`, без ссылки | exit 1, «В1: в базе PR уже есть backend/features/sales/, волна не развёрнута — …» |

Один подложенный `__init__.py` на этой ветке красным не бывает, и это по
спеке: В0 разворачивает сам этот PR, а первый PR продаж при В1 `pending`
законен (A4). Красное — у пропущенной В0 (A2) и у второго PR продаж (A5).

## Product oracles

- [x] PASS — `delivery/evals/smoke`: продуктового кода срез не трогает.
- [x] PASS — `active/eval-smoke.md`: пять пунктов, все отмечены.

## Ревью рисковых мест

- **транзакция БД** — риска нет: `commit` в диффе — это `git commit` в помощнике
  `repo` из `tests/test_contour_waves.py`, во временном каталоге теста; базы
  данных ни проверка, ни её тесты не касаются.
- **производительность** — риска нет: `re.search` и `re.compile` в `fires`, `_row`
  и `field` идут по десятку строк реестра и STATUS и по путям под
  `backend/features/sales/`; `collect` читает несколько файлов `delivery/`,
  `okf/*.md`, `.importlinter` и `.py` продаж и зовёт git дважды (`ls-tree`
  базы и `diff`), с таймаутом 60 с.
- **новый модуль** `contour_waves` — состояния нет: ядро без ввода-вывода, ввод
  только в `collect`. Ошибка git не глотается: `_git` печатает причину в stderr
  и возвращает None, а судья превращает пропавшую базу в нарушение `no-base`
  на PR продаж — громко, а не тихо.
- Что может сломаться: шаг CI зависит от `$BASE` шага «Diff base». Пустая
  `$BASE` на PR продаж — красное `no-base`; на PR доноров — предупреждение.

## Предупреждения delivery_check, разобранные

- «Примеры появились в истории позже тестов: A1, A6, A7, A8» — ложное
  срабатывание канона на старых тестах. Спека с примерами закоммичена раньше
  тестов (T7 первым), а совпадения найдены в чужих строках: `"sku": "A1"` —
  `tests/test_home_signals.py:34`; `%D0%A6` — `tests/test_inbound_text.py:293`;
  `%D8%A7`, `%D8%A8` — `tests/test_contact_pages_links.py:116`. Граница токена
  в `_first_commit_with` (`scripts/delivery_history.py`) считает `%` и кавычку
  границей, и URL-кодированный байт читается id примера. Канон не правится
  из проекта — находка передана координатору.
- «`irreversible_surfaces:` не называет отправка наружу» — было и у
  `front-polish`: строка подписана и перенесена дословно. На handoff это
  станет ошибкой — решение за владельцем (переподпись дополненной строки).

## Spec coverage gaps

- Детекторы `later` (`sales-agent-prompt`, `sales-segment-prompt`,
  `sales-autosend`, `sales-thresholds`) не судят ничего, пока их не назовут
  срезы-хозяева. Проверка печатает это при каждом прогоне с кодом продаж.
- Реляционного оракула (`@given`) нет: hypothesis не стоит в зависимостях
  проекта, а новая зависимость — вне этого среза. `delivery_check` предупреждает.

## Verdict

- [x] READY FOR HANDOFF — после ревью координатора, зелёного CI и подписи
      спеки (`human_ok_spec`).
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED
## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **35**, из них без ссылки на пример спеки:
**0**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A1	assert code == 0
A1	assert "○ продаж в дереве нет — судить нечего" in out
A1	assert all(f"{wave} pending" in out for wave in ROWS)
A2	assert got == code
A2	assert mark + expected in out
A4	assert got == code
A4	assert line in out
A6	assert got == code
A6	assert ("✗ промпт reply_kind.md не назван в model_surface" in out) is (code == 1)
A7	assert got == code
A7	assert ("✗ реестр: строка 10 — В4: absent без reason=" in out) is (code == 1)
A8	assert got == 1
A8	assert "✗ реестр волн не найден" in out
A9	assert got == code
A9	assert ("✗ stack-selftest: нет ни в delivery/STACK-ACCEPTANCE.md, ни в STATUS" in out) is (
A10	assert got == code
A10	assert ("✗ срез sales-x: в delivery/active/tasks.md нет раздела «Уроки»" in out) is (code == 1)
A11	assert warned[0] == 0
A11	assert "⚠ предупреждение: канон впереди: delivery 1.95 → 1.96" in warned[1]
A11	assert judged[0] == 0
A11	assert "канон впереди" not in judged[1]
A12	assert got == 1
A12	assert "✗ реестр не разобран: строка 5 — " in out
A12	assert "нарушений нет" not in out
A13	assert got == code
A13	assert line in out
A13	assert got == 0
A13	assert "общий код тронут" not in out
A13	assert "⚠ предупреждение: В1: в базе PR уже есть backend/features/sales/" in out
A13	assert "○ PR не несёт работы продаж" in out
A2	assert run(tmp_path / "a2", capsys, "--base", a2)[0] == 0
A2	assert run(tmp_path / "a5", capsys, "--base", a5)[0] == 0
A12	assert errors == []
A12	assert {w.name: (w.triggers, w.limit) for w in waves} == ROWS
A12	assert {w.name for w in waves if w.state == "deployed"} == {"В0", "В-обн"}
```

✅ **Каждое утверждение ведёт к примеру спеки** (A1 A10 A11 A12 A13 A2 A4 A6 A7 A8 A9), а примеры человек
подписал до кода (`human_ok_spec`). Подпись под дайджестом здесь
**не требуется**: она уже стоит, заранее и на числах. Пиши в verify-report
`asserts_reviewed_by: n/a (все утверждения ведут к одобренным примерам)`.

asserts_without_example: 0
