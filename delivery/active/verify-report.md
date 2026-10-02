# Verify report

**Date:** 2026-10-02
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=6 утверждений без примера спеки ждут подписи человека — заведение со склейкой пробелов и описанием (`test_hypothesis_is_stored_with_its_description`, 3), право в роли оператора и снятие поимённо (`test_operator_gets_sales_by_default_and_loses_it_by_name`, 1), список прав оператора при входе (`TestLogin::test_login_gives_token_and_card`, 1), первый импорт `sales.models` в чистом процессе (`test_sales_models_import_first_in_a_clean_process`, 1); человека в цепочке агента нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена: пуш, PR и прогон CI делает координатор после ревью
**Commit:** T9 — локальные прогоны сняты на дереве T9 (`76719e1`) с правками этого отчёта

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, 0 упавших,
      exit 0.
- [x] PASS — `ruff check backend/ tests/ scripts/` (как в CI) — exit 0;
      `ruff format --check` — 465 файлов, exit 0; `mypy backend/` — 298 файлов,
      exit 0; `scripts/gates.py` — 461 файл, нарушений нет, в том числе
      `public-repo`, exit 0; `lint-imports` — 3 контракта целы, 0 сломано,
      exit 0; `scripts/complexity.py` — 312 файлов, расхождений со снимком нет,
      exit 0 (худшая функция среди файлов продаж — `add` в
      `sales/hypotheses.py`, сложность 6).
- [x] PASS — DRY-гейт `check_jscpd_gate.sh`: 45 пар клонов при снимке 45
      (387 файлов), exit 0. Первая редакция миграции в T3 давала 46 (пара
      колонок `created_at`/`updated_at` против `b7e2c4a81f95`) — помощник
      `_stamps` переписан одной колонкой в цикле.
- [x] PASS — `delivery_check.py --require-ci --diff-base origin/main`:
      0 errors, 5 warnings, exit 0. Breakers: файлов 18, net_loc 724
      (+730/−6) при пределах 25 и 800; файл прибавил `tests/test_api_auth.py`.
- [x] PASS — `scripts/check_irreversible_signature.sh`: подпись сходится, exit 0.
- [x] PASS — `python scripts/contour_waves.py --base origin/main` (режим CI):
      exit 0, «нарушений нет», «В1: триггер сработал, предел — следующий PR
      продаж»; `shared_changes` покрывает все общие файлы диффа. Без строки
      о `tests/test_api_auth.py` в `shared_changes:` тот же прогон давал exit 1:
      «общий код тронут без объявления: tests/test_api_auth.py».
- [x] PASS — `check_gate_coverage.sh`: OK — 15 скриптов, подключено 11,
      осознанно нет 4, правил сверено 10, exit 0. `contour_doctor.py`: exit 0,
      DEAD 0 (AUTO 34 · WEAK 12 · ABSENT 6 · TOOL 3 · SKIP 2). WEAK — пропуски
      среды пробы (в ней нет `ruff`, `jscpd`, `eslint`, `gh`, `pip-audit`,
      `mutmut`, `pytest-cov`, `lint-imports`) и маски гейтов по языкам; путей
      продаж среди них нет.

Проверки по диффу (`delivery_check`, `contour_waves`, дайджест) берут общий
предок с `origin/main` — `def8d3e`. Сам `origin/main` с тех пор ушёл на
`038f85c` (#132); слияние ветки с ним без конфликтов (`git merge-tree`):
общие файлы — `.env.example` и `delivery/complexity-snapshot.json`, правки
в разных местах.

## Behavior oracles

- [x] PASS — `tests/test_sales_model.py`: 11 тестов на настоящей базе —
      A1 (2), A4 (2, с положительным контролем), A5, A6 (3, два — обратные
      прогоны), A7 (2), первый импорт `sales.models` в чистом процессе.
- [x] PASS — `tests/test_sales_setup.py`: 7 тестов — команда в разборе консоли,
      заведение с описанием, A8 (3), выключатель, право: в роли оператора
      и снимается поимённо.
- [x] PASS — `tests/test_api_auth.py`: 24 теста; вход оператора отдаёт список
      прав с `sales`.
- [x] PASS — `tests/test_schema.py`, `tests/test_migrations_match_models.py`:
      две таблицы в реестре, модели и цепочка сходятся (A1).
- [x] PASS — полный набор на своей базе `outreach_test_sales_a`: 3066 passed
      за 322,5 с, exit 0.
- [x] PASS — фронт: `tsc`, `eslint`, `prettier` — exit 0; vitest 442 из 442
      (49 файлов), exit 0.

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
| T9: без `Permission.SALES` в роли оператора — прежний выбор | тест права и список прав при входе красные | весь сьют: 2 failed, 3064 passed — `test_operator_gets_sales_by_default_and_loses_it_by_name` (`[False, False, True] == [True, False, True]`) и `TestLogin::test_login_gives_token_and_card` (список прав без `sales`); больше не краснеет ничего |
| T9: `has_permission` слушает только `true` в `users.permissions` — снять право поимённо нельзя | тест права красный | файлы прав, 82 теста: 3 failed — тест права продаж (`[True, True, True] == [True, False, True]`), `TestPermissions::test_permission_can_be_taken_away_from_admin`, `TestChangeAccess::test_right_can_be_taken_below_the_role`; `test_api_auth.py` эту порчу не ловит — снятие он не проверяет |
| файлом: ключ к адресу `CASCADE` / `RESTRICT` в транзакции теста | лид пропадает / удаление у доноров падает | `test_reverse_run_cascade_…` и `test_reverse_run_restrict_…` зелёные и в полном прогоне T9: тесты различают три выбора |

Каждую порчу вносили по одной и возвращали из копии; дерево после прогонов —
как в коммитах. Строки T9 сняты на дереве `76719e1`, после возврата файлы прав
снова зелёные (82 passed). Остальные строки — с T3–T8: код и тесты, к которым
они относятся (`backend/features/sales/`, миграция, `config/sales.py`,
`cli/sales.py`, `core/models/`, `tests/test_sales_model.py`, сверка моделей),
с T8 не менялись — `git diff ff9d37d HEAD` по ним пуст.

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

- **безопасность** — право `Permission.SALES` стоит в роли оператора: решение
  владельца 01.10, раздел продаж видят все. Риск — раздел с именами
  и должностями живых людей виден шире, чем при выдаче поимённо, — владелец
  принял осознанно. Сейчас под правом нет ни маршрута, ни экрана: открытым
  раздел станет со срезом экрана 1.5 — сразу всем операторам, включая
  заведённых раньше. Управление доступом держат: снять у одного —
  `{"sales": false}` в `users.permissions` через админку
  (`PATCH /api/users/{id}`; `sales` — известное действие для
  `_check_overrides`); `has_permission` читает точечное исключение раньше
  роли и в срезе не менялся; скрыть у всех — убрать право из
  `ROLE_PERMISSIONS`. Ловят: право в роли — тест права
  `test_operator_gets_sales_by_default_and_loses_it_by_name`
  и `TestLogin::test_login_gives_token_and_card`, оба краснеют без права
  в роли; снятие поимённо — тот же тест права и общие тесты исключений
  в `test_access_core.py` и `test_api_users.py` (обратные прогоны T9 выше).
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

Прогон T9: 0 errors, 5 warnings. После слияния main с #136 (`delivery@1.98`,
запись `stack:` поднята): 0 errors, 4 warnings — первое ниже ушло, канон
теперь ищет порядок в окне ветки (merge-base..HEAD).

- «у 5 из 6 примеров порядок не проверить — пример и тест приехали одним
  коммитом (A4, A5, A6, A7, A8)» — до `delivery@1.98`. Проверка брала самый старый коммит с id
  во всей истории `delivery/active/spec.md` и `tests/`, а id A1–A13 были
  у среза В0: его спека и тесты слиты одним сквош-коммитом `39c4e82` (#129),
  и первым находится он. У этого среза порядок виден: спека с A4–A8 — T0
  `1b46430`, тесты A4–A7 — T3 `626cfa9`, A8 — T5 `03e3d82`. Прежнее
  «A1, A6, A7, A8 появились позже тестов» ушло: канон `delivery@1.96`
  (пришёл с #131) больше не читает `%D0%A6` ссылкой на пример. A1 теперь
  без предупреждения: id в спеке впервые в `5af9f3d` (#117), раньше тестов.
- «asserts_reviewed_by deferred» — шесть утверждений ждут подписи человека
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

Новых/изменённых утверждений: **32**, из них без ссылки на пример спеки:
**6**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert body["user"]["permissions"] == ["prices", "run", "sales", "settings", "view"]
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
-	assert allowed == [True, False, True]
```

Привязаны к примерам: **A1 A4 A5 A6 A7 A8 L4 L5**. Остальные 6 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 6
