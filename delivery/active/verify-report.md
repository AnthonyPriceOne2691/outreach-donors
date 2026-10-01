# Verify report

**Date:** 2026-10-01
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=5 утверждений без примера спеки ждут подписи человека — заведение со склейкой пробелов и описанием (`test_hypothesis_is_stored_with_its_description`, 3), право оператора только поимённо (`test_operator_gets_sales_only_by_name`, 1), первый импорт `sales.models` в чистом процессе (`test_sales_models_import_first_in_a_clean_process`, 1); человека в цепочке агента нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена: пуш, PR и прогон CI делает координатор после ревью
**Commit:** T8 — локальные прогоны сняты на дереве T5a (`2932b8e`) с правками этого отчёта

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 0 упавших.
- [x] PASS — `ruff check` по `backend/ tests/`, `mypy backend/` (298 файлов),
      `scripts/gates.py` (458 файлов, нарушений нет, в том числе `public-repo`),
      `lint-imports` (3 контракта целы), `scripts/complexity.py` (снимок
      совпадает, новые файлы приняты: худшая функция — `add` в
      `sales/hypotheses.py`, сложность 6).
- [x] PASS — DRY-гейт: 45 пар клонов при снимке 45. Первая редакция миграции
      дала 46 (пара колонок `created_at`/`updated_at` против
      `b7e2c4a81f95`) — помощник `_stamps` переписан одной колонкой в цикле.
- [x] PASS — `delivery_check.py --require-ci --diff-base origin/main`:
      0 errors. Breakers: файлов 17, net_loc 722 (+726/−4) при пределах 25 и 800.
- [x] PASS — `scripts/check_irreversible_signature.sh`: подпись сходится, exit 0.
- [x] PASS — `python scripts/contour_waves.py --base origin/main` (режим CI):
      exit 0, «В1: триггер сработал, предел — следующий PR продаж»,
      `shared_changes` покрывает все общие файлы диффа.
- [x] PASS — `check_gate_coverage.sh`: OK. `contour_doctor.py`: DEAD 0
      (AUTO 34 · WEAK 12 · ABSENT 6 · TOOL 3 · SKIP 2). WEAK — пропуски среды
      пробы (в ней нет `ruff`, `jscpd`, `eslint`, `gh`) и маски гейтов по языкам;
      путей продаж среди них нет.

## Behavior oracles

- [x] PASS — `tests/test_sales_model.py`: 11 тестов на настоящей базе —
      A1 (2), A4 (2, с положительным контролем), A5, A6 (3, два — обратные
      прогоны), A7 (2), первый импорт `sales.models` в чистом процессе.
- [x] PASS — `tests/test_sales_setup.py`: 7 тестов — команда в разборе консоли,
      заведение с описанием, A8 (3), выключатель, право.
- [x] PASS — `tests/test_schema.py`, `tests/test_migrations_match_models.py`:
      две таблицы в реестре, модели и цепочка сходятся (A1).
- [x] PASS — полный набор на своей базе `outreach_test_sales_a`: 3032 passed
      за 302 с.
- [x] PASS — фронт: `tsc`, `eslint`, `prettier`, vitest 442 из 442 (49 файлов).

### Обратные прогоны

| Что сломано | Ожидание | Факт |
|---|---|---|
| новые тесты на старом коде (до T3) | красные | 2 из 2 модулей не собрались: `ModuleNotFoundError` (`backend.features.sales`, `backend.cli.sales`) |
| откат миграции без `DROP TYPE` | цикл A1 красный | 1 failed — `test_downgrade_drops_tables_and_types_and_upgrade_runs_again` |
| ключ к адресу `CASCADE` в модели и миграции | A6 красный | 1 failed — `test_donor_removes_shared_address_and_the_lead_stays` |
| ключ к адресу `CASCADE` только в модели | сверка моделей красная | `remove_fk` / `add_fk` в `test_migrations_produce_exactly_the_schema_models_expect` |
| `domain_id` необязателен в миграции | A4 и сверка красные | 2 failed |
| без строки порядка импорта в `sales/__init__.py` | тест первого импорта красный | 1 failed; сам импорт — `ImportError: cannot import name 'SalesHypothesisModel' from partially initialized module` |
| алиас выключателя `SALES_ENABLE` | тест окружения красный (урок L4) | 1 failed |
| оператору право `SALES` в роли | тест права красный | 1 failed |
| файлом: ключ к адресу `CASCADE` / `RESTRICT` в транзакции теста | лид пропадает / удаление у доноров падает | `test_reverse_run_cascade_…` и `test_reverse_run_restrict_…` зелёные: тесты различают три выбора |

Каждую порчу вносили по одной и возвращали из копии; дерево после прогонов —
как в коммитах.

### Живой прогон команды

`outreach sales-hypothesis-add` на своей базе, вне сьюта, at=2026-10-01:
- `--name "проверка консоли" --description …` → rc=0, «Заведена гипотеза №3»;
- тот же текст с лишними пробелами → rc=3, «имя «проверка консоли» уже у
  гипотезы №3»;
- `--name "   "` → rc=4, «нет имени (передано '   ') …».
Строка в базе одна; после прогона удалена.

## Product oracles

- [x] PASS — `active/eval-smoke.md`: шесть пунктов, все отмечены.

## Ревью рисковых мест

- **безопасность** — новое право `Permission.SALES`. Риск — выдать раздел
  с именами и должностями живых людей шире, чем нужно. Держит матрица:
  оператору право не положено, только поимённо через `users.permissions`;
  `has_permission` не менялся, тест `test_operator_gets_sales_only_by_name`
  краснеет на порче матрицы. Маршрутов под правом в срезе нет.
  `.secrets.baseline` — только сдвиг `line_number` известной находки
  в `core/domain.py` (157 → 158), новых находок нет.
- **деньги** — риска нет, потому что `prices` в диффе — только перенос строки
  TS-типа `Permission`: союз разбит по строкам, право подтверждать цены
  не менялось ни на сервере, ни во фронте.
- **транзакция БД** — `session.commit` в `run_hypothesis_add` после
  `hypotheses.add`: отказ (`BadNameError`, `NameTakenError`) приходит до
  записи, коммит — один, после успеха. Два одновременных заведения одного
  имени: проверка `add` пропустит оба, второй упадёт на уникальности
  `sales_hypotheses.name` — громко, трассировкой команды, а не второй строкой.
  В тестах `session.begin_nested` и DDL (`ALTER TABLE sales_leads`, откат
  и подъём миграции) идут в транзакции теста и откатываются с ней.
- **новый модуль** `models`, `hypotheses`, `sales`: состояния нет. Риск
  для доноров — ключи `sales_leads`: `contact_id` с `ON DELETE SET NULL`
  и индекс `idx_sales_leads_contact` — удаление адреса у доноров проходит
  и не читает всю таблицу лидов; `domain_id` с `RESTRICT` — боевые потоки
  доменов не удаляют.
- Что может сломаться: первый импорт `sales.models` в чистом процессе —
  круг держит строка в `sales/__init__.py` и тест; миграция `stage` среза
  1.1b обязана встать после `715bbf374195`.

## Предупреждения delivery_check, разобранные

- «Примеры появились в истории позже тестов: A1, A6, A7, A8» — известный дефект
  канона (хозяин канона подтвердил при срезе В0): гейт ищет id подстрокой,
  и `%D0%A6`, `%D8%A7` в чужих тестах читаются id примеров. Спека с примерами
  закоммичена первой (T0 `1b46430`), тесты — в T3 и позже; у каждого теста
  есть ссылка `# A<n>` в строке сигнатуры. id не переименованы.
- «Пример и тест приехали одним коммитом: A4, A5» — те же id были у среза В0:
  его спека и тесты с `# A4 A5` слиты одним сквош-коммитом `39c4e82`, и гейт
  находит первым его. У этого среза порядок виден: спека — `1b46430`, тесты
  A4 и A5 — `626cfa9`.
- «asserts_reviewed_by deferred» — пять утверждений ждут подписи человека
  (перечень — в шапке, строки — в дайджесте ниже); на handoff это ошибка.
- «`irreversible_surfaces:` не называет отправка наружу» — было и у В0 и
  `front-polish`: строка подписана и перенесена дословно. На handoff это
  ошибка; дополненную строку владелец переподпишет позже.
- «Ни одного реляционного оракула» — hypothesis не стоит в зависимостях,
  новая зависимость — вне среза.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Spec coverage gaps

- Строка `contacts` общая на домен и адрес: что делать, когда адрес лида
  совпал с адресом донора, решает срез загрузки (открытый вопрос спеки).
- Выключатель `SALES_ENABLED` только заложен: читать его нечему, пока срезы
  не принесут работу продаж.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, решение координатора и владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **31**, из них без ссылки на пример спеки:
**5**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A6	assert donor is not None
A6	assert contact is not None
A1	assert spec is not None, MIGRATION
A1	assert spec.loader is not None, MIGRATION
A1	assert await connection.run_sync(_present) == {*TABLES, *TYPES}
L5	assert after_downgrade == set()
L5	assert after_upgrade == {*TABLES, *TYPES}
A4	assert await _leads(session) == []
A4	assert await _leads(session) == [(None, "ivan@acme.example")]
A5	assert list(domains) == [domain.id, domain.id]
A6	assert removed.email == SHARED
A6	assert await session.get(ContactModel, contact.id) is None
A6	assert await _leads(session) == [(None, SHARED)]
A6	assert await _leads(session) == []
A6	assert await _leads(session) == [(contact.id, SHARED)]
A7	assert await session.scalar(select(func.count()).select_from(SalesLeadModel)) == 1
-	assert done.returncode == 0, done.stderr
A8	assert (args.command, args.name, args.description) == (
-	assert code == EXIT_OK
-	assert await _hypotheses(session) == [("сайты EN", "кому и зачем")]
-	assert "«сайты EN»" in capsys.readouterr().out
A8	assert await run_hypothesis_add(session, "сайты EN", None) == EXIT_OK
A8	assert code == EXIT_TAKEN
A8	assert await _hypotheses(session) == [("сайты EN", None)]
A8	assert "имя «сайты EN» уже у гипотезы №" in capsys.readouterr().out
A8	assert code == EXIT_BAD_NAME
A8	assert await _hypotheses(session) == []
A8	assert words in capsys.readouterr().out
L4	assert sales_cfg._Sales(_env_file=None).enabled is False
L4	assert sales_cfg._Sales(_env_file=None).enabled is True
-	assert allowed == [False, True, True]
```

Привязаны к примерам: **A1 A4 A5 A6 A7 A8 L4 L5**. Остальные 5 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 5
