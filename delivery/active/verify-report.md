# Verify-report — срез 5.3 `sales-handoff` (черновик; перенос на main 07.10)

**Поставка:** срез `sales-handoff` (модуль «Продажи», 5.3) — передача лида телемаркетологу: сделка в Kommo, сообщение
в Telegram, запасной путь, повторы. Одним PR под постоянный вейвер владельца для модульных срезов продаж (строку
`waivers:` пишет координатор).

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; покрытие — необязательные `coverage` и `diff-coverage` (#199); до пуша — локальные прогоны ниже; общие точки — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=214 утверждений, 213 без ссылки на пример спеки ждут подписи человека: тесты примеров названы `test_a1_…`–`test_a6_…` строчными, дайджест их не связывает; подписывает владелец при ревью, дайджест в конце
**CI run:** нет — снимается на PR
**Commit:** ветка `sales/5.3-handoff-0710` на main `9ee3d9e` (1.1b целиком), голова `4705e52`; все прогоны ниже — на `4705e52`

Дерево `sales-n`. Перенос: `sales/5.3-handoff` (`3199b64` на `05d75d9`) → `sales/5.3-handoff-main` (`70776fe` на
`1fd24ca`, утро 07.10) → `sales/5.3-handoff-0710` (`4705e52` на `9ee3d9e`, вечер 07.10) — cherry-pick'ом по порядку.
Логи вечернего переноса — `logs/main-0710b/`, утреннего — `logs/main-0710/`, 06.10 — `logs/`.

## Перенос на main 07.10, вечер (`9ee3d9e`) — конфликты и как разрешены

| Файл | Конфликт | Решение |
|---|---|---|
| `backend/cli/sales_commands.py` | 1.1b добавил `FAILURES` и `EXIT_NOT_CONNECTED` (отказ этапу продаж в общих командах) на место импортов | обе стороны: импорт `sales, sales_telegram` и `SalesNotConnectedError`, `FAILURES` 1.1b не тронут; команда — в `COMMANDS`, `KEPT_ON_INTERRUPT`, `add_parsers` |
| `.secrets.baseline` | соседние записи | сторона main + `detect-secrets scan --baseline .secrets.baseline`; итог против main: +запись `a9e76c0eb5b0`, `tests/conftest.py` 380 → 391 |
| `delivery/complexity-snapshot.json` | соседние записи | сторона main + `scripts/complexity.py --update` |
| миграция `a9e76c0eb5b0` | без конфликта, но на старой голове | `Revises`/`down_revision` → `39e342cb2b21` (значение `sales` типа этапа); `alembic heads` — одна голова |
| `tests/test_sales_handoff_rows.py` | без конфликта, по смыслу (стык с 1.1b) | диалог тестов — рассылка `Stage.SALES` вместо `Stage.ADVERTISERS` (`4705e52`); передача этап не читает |

`core/models/__init__.py`, `tests/test_schema.py`, `tests/conftest.py`, `tests/test_prune_test_traces.py` смержились без
конфликта: main их с `1fd24ca` не менял (кроме реестра — тесты, не `REVIEWED`); строка второго реестра на месте.
`git range-diff 1fd24ca..70776fe origin/main..4705e52~1`: коммиты 3–10 равны, T1 и T2 — разрешения выше и сообщения
под новую базу.

**Стык с 1.1b.** Передача этап не читает; «ответ продаж» берёт тем же правилом, что main для `Stage.SALES`
(`ReplyKind.HUMAN` — `outreach/threads.py::_answered`, `review_of`). Пробник на базе (не коммитился): диалог продаж с
ответом человека до передачи — `sales_pending` (`review_of`: ждёт, «ответ продаж ждёт разбора: продажи к почте ещё не
подключены»), передача — `done/sent`, после — снова `sales_pending`, `reviewed_at` пуст. На `sales_pending` передача
срабатывать не должна: это любой неразобранный ответ человека в треде продаж, а передача — только «хочет говорить»
(2.2, 3.2). Нового пути нет.

## Перенос на main 07.10, утро (`1fd24ca`) — конфликты и как разрешены

| Файл | Конфликт | Решение |
|---|---|---|
| `backend/features/sales/models.py` | #180 добавил `SalesChainTemplateModel` на то же место | оба блока: цепочка, затем передача; заголовок модуля — обе таблицы; импорты `BigInteger` и `SmallInteger` |
| `backend/features/core/models/__init__.py` | экспорт `SalesChainTemplateModel` | оба экспорта по алфавиту |
| `tests/test_schema.py` | `sales_chain_templates` в перечне | обе таблицы |
| `tests/test_sales_model.py` | #180 ввёл `DEPENDENTS` и обход зависимых ревизий; 5.3 — своя `HANDOFFS` | один кортеж `DEPENDENTS` из двух ревизий по порядку миграций (`723e3ddab31f`, `a9e76c0eb5b0`), обход — из main |
| `backend/cli/main.py` | #180 вынес команды продаж в `backend/cli/sales_commands.py` | сторона main (файл не тронут); команда — в `COMMANDS`, `KEPT_ON_INTERRUPT` и `add_parsers` перечня |
| `tests/test_sales_telegram.py` | склейка соседних тестов (T2/T3) | оба теста; проверка подписи прерывания добавлена |
| `delivery/complexity-snapshot.json` | соседние записи | сторона main + `scripts/complexity.py --update` на каждом коммите с конфликтом |
| `.secrets.baseline` | запись ревизии `ad4a79bc6000` рядом | сторона main + `detect-secrets scan --baseline .secrets.baseline`; итог: +запись `a9e76c0eb5b0`, `tests/conftest.py` 380 → 391 |
| миграция `a9e76c0eb5b0` | без конфликта, но на старой голове | `Revises`/`down_revision` → `cbc5aadf4fc2`; `alembic heads` — одна голова |
| второй реестр чистки (#192) | без конфликта, но тест красный | строка `sales_handoffs.thread_id → threads CASCADE` в `tests/test_prune_test_traces.py::REVIEWED` |

`git range-diff 05d75d9..3199b64 origin/main..HEAD`: коммиты 5, 6, 8, 9, 10 — равны (`=`); 1, 2, 3, 4, 7 — разрешения
выше и контекст снимка сложности.

## Красный прогон

Новое при переносе (`logs/main-0710/`):

| Что | Прогон | Итог |
|---|---|---|
| второй реестр чистки без строки передачи | `tests/test_prune_test_traces.py::test_every_reference_to_what_the_trace_cleanup_deletes_is_decided` | `не разобраны: ['sales_handoffs.thread_id → threads CASCADE']`, 1 failed, exit 1 — `red-prune-test-traces.txt` |
| перечень без подписи прерывания команды | `tests/test_sales_telegram.py::test_command_is_registered_in_the_console` | `KeyError: 'sales-telegram-chat-id'`, 1 failed, exit 1 — `red-cli-kept.txt` |

06.10 (база `05d75d9`, код среза при переносе не менялся): T1 без модели — `ImportError` (exit 2); модель без
миграции — 6 failed, 4 errors (`relation "sales_handoffs" does not exist`); T2, T3, T4 — `ImportError` (exit 2);
гонка — `assert 1 == 2`. Логи — `logs/red-*.txt`.

## Мутанты

19 из 19 убиты на базе `05d75d9` (`logs/mutants.txt`): обязательные — вторая сделка на повторе (A2), тишина при отказе
Kommo (A3), повтор записи после `KommoUnconfirmedError`, токен в тексте ошибки, копия в группу при выключенной
настройке; ещё 14 — по правилам среза. При переносе код среза не менялся, кроме регистрации команды в перечне;
мутанты заново не гонялись — новая проверка подписи прерывания показана красным прогоном выше.

## Проверки (голова `4705e52`)

| Проверка | exit | Итог |
|---|---|---|
| полный `pytest -q --cov=backend --cov-report=json:coverage.json` (нагрузка 5,7 на старте, прогонов с покрытием нет — `pgrep -f "[p]ytest.*--cov"` пуст) | 0 | 4847 passed за 653 с (`logs/main-0710b/full-pytest.txt`); `coverage.json.head` = `4705e52` |
| `check_diff_coverage.sh` как джоба `diff-coverage` (`STRICT=1 MIN_PCT=70 LINT_PY_SRC=backend LINT_BE_DIR=. LINT_COV_PKG=backend LINT_COV_FILE=coverage.json BASE=origin/main`) | 0 | 11 изменённых файлов — 100 %, `backend/workers/reaper.py` — 98,2 % (непокрыта строка `if __name__`) (`diff-coverage.txt`) |
| тесты среза и соседей: `tests/test_sales_*.py` (вкл. `test_sales_stage_*` 1.1b), `test_prune.py`, `test_prune_test_traces.py`, `test_schema.py`, `test_migrations_match_models.py`, `test_reaper.py`, `test_workers_health.py`, `test_followups.py`, `test_cli_main.py`, `test_alerts.py`, `test_replies_rules.py`, `test_thread_answer.py`, `test_thread_replied.py` | 0 | 1003 passed за 48 с (`logs/main-0710b/slice-and-neighbours-pytest.txt`) |
| `pre-commit run --from-ref origin/main --to-ref HEAD` | 0 | 22 хука, 25 с (`pre-commit-files.txt`); на коммитах T1, T2 и `4705e52` хуки шли при коммите |
| ruff check / ruff format --check | 0 / 0 | чисто / 736 файлов |
| `mypy backend/` | 0 | 383 файла |
| `scripts/gates.py --commits origin/main` / `scripts/complexity.py` / `lint-imports` | 0 / 0 / 0 | 635 файлов и 11 сообщений коммитов (public-repo) / 399 файлов, снимок совпадает / 4 kept |
| `alembic heads` | 0 | одна голова `a9e76c0eb5b0` (на `39e342cb2b21`) |
| `check_irreversible_signature.sh` | 0 | подпись сходится — строка main не тронута |
| `check_complexity_gate.sh` / `check_jscpd_gate.sh` / `BASE=origin/main check_baseline_ratchet.sh` | 0 / 0 / 0 | OK; clone-пар 38 при снимке 55; 15 снимков |
| `detect-secrets-hook --baseline .secrets.baseline` на изменённых файлах | 0 | baseline актуален |
| шаг образа локально: `docker_step_local.py <дерево>` + `outreach --help` | 0 / 0 | 1 шаг (шаблоны писем и промпты читаются); команда в списке (`docker-step-local.txt`) |
| `contour_waves.py --base origin/main` — клон с черновиками, четыре строки STATUS из main `9ee3d9e` | 0 | нарушений нет («Согласовано» — заглушка координатора) |
| `delivery_check.py --require-ci --diff-base origin/main` — тот же клон | 1 | 1 error — `net loc_diff 3315 > 800` (до `waivers:`), 5 warnings — ниже (`delivery-check-clone.txt`) |
| vitest | — | фронт не тронут — не гонялся |
| grep добавленных строк и сообщений коммитов | — | имена закрытых документов, слова о клиенте, имена людей, числа с разрядами, лимиты — 0 совпадений |

## Ревью рисковых мест

Классы, поднятые диффом (`delivery_risk.risky_classes` от `origin/main`): деньги (`счёт`), безопасность (`secret`),
транзакция БД (`commit`, `rollback`, `session.begin_nested`), производительность (`json.loads`), интеграция (`httpx`,
`webhook`), новый модуль.

- **деньги** — риска нет, потому что денег в срезе нет: «счёт» — из «не в счёт» в докстроке `handoff.lead_of`
  (отсеянные лиды не считаются); сумм, цен и платежей передача не знает.
- **безопасность** — токен бота продаж (`SALES_TELEGRAM_BOT_TOKEN`) живёт только в адресе Bot API: свой фильтр
  журнала httpx `_HideSalesToken` в `backend/features/sales/telegram.py`, тексты `TelegramError` — без адреса и без
  текста httpx (`from None`); тесты `test_httpx_log_line_hides_the_token`,
  `test_a4_token_from_network_error_stays_out_of_error_and_alert`, мутант M4. Адрес лида в тревоги эксплуатации не
  уходит (`handoff_kommo.headline` — только номера лида и диалога). В Kommo уходят имя, почта, компания и письмо лида —
  это новая поверхность необратимого (в строке продаж, которую владелец подписал в #220). `secret` в диффе — `# pragma:
  allowlist secret` у выдуманного `TOKEN` тестов и `hashed_secret` номера ревизии `a9e76c0eb5b0` в `.secrets.baseline`
  (ложное срабатывание, как у соседних миграций). Сети в тестах нет: автофикстура `_no_real_sales_bot` чистит токен и
  номера чатов набора.
- **транзакция БД** — `handoff.start` коммитит сессию и только потом ставит задачу (задача, взятая до коммита, не
  увидела бы строку): вызывающий получает свою транзакцию закоммиченной — сказано в docstring; разбор ответа продаж
  поэтому зовёт её после коммита снимка ответа (`SalesReplies.pass_on`, стык ниже). `handoff_kommo.write` коммитит номер сделки сразу после ответа Kommo: смерть задачи до примечания и
  Telegram не заведёт вторую сделку на повторе. Захват `claimed_at` — условный `UPDATE` с коммитом (`_claim`),
  снимается в конце `process` и после сбоя (`_release_after_failure`: `session.rollback()`, снятие захвата, `commit`;
  тест `test_claim_after_database_failure_is_released_after_rollback`). `session.begin_nested()` — только в тестах
  ограничений базы (`tests/test_sales_handoff_table.py`). Опасно: ответ пришёл во время идущей задачи и очередь в тот же
  миг недоступна — итог идущей задачи затирает срок прохода, передача ждёт следующего ответа (нужны два отказа разом;
  записано в находках, не чинил).
- **производительность** — `json.loads` — только в тестах (тела запросов подставного Bot API). Проход повторов — один
  запрос раз в `HANDOFF_PASS_SEC` (5 мин); индекса по `due_at` нет — таблица растёт на единицы строк в день.
- **интеграция** — Kommo через клиент 5.2 (`create_complex_lead`, примечание, поиск контакта), Telegram — свой
  `SalesBot` поверх `httpx.AsyncClient`: три попытки с паузами 2 и 5 с, 429 — пауза Telegram до 30 с, 400/401/403/404 —
  сразу отказ с советом; 409 (`webhook` у бота) — отказ с советом `deleteWebhook` (`test_webhook_on_the_bot_is_named`).
  Запись в Kommo после потерянного ответа не повторяется вслепую (поиск контакта до и после, иначе `unconfirmed` —
  человеку), мутант M3. Живых вызовов не было: Kommo — `KommoFixture`, Telegram — `httpx.MockTransport`.
- **новый модуль** — `handoff.py`, `handoff_kommo.py`, `handoff_text.py` (чистые функции текста), `handoff_jobs.py`
  (задача и проход), `telegram.py`, команда `backend/cli/sales_telegram.py`, миграция `a9e76c0eb5b0_sales_handoffs`;
  покрытие изменённого — в таблице проверок.

## Стык с разбором ответов Ф2 (в этом PR)

Два коммита поверх 5.3. «Хочет говорить» больше не пишет в журнал «передача ждёт своего среза»: `SalesReplies.handle`
запоминает диалог (`Handled.handoff_thread`), задача ответа коммитит снимок вида и только потом зовёт
`SalesReplies.pass_on` → `handoff.start` — внешнее (Kommo, Telegram) идёт задачей передачи уже после записи ответа.
Отказ передачи разбор не роняет: исключение — в журнал с номером ответа и диалога, ответ ждёт человека по снимку,
заведённую строку передачи повторит её проход. Удачная передача снимает с ответа ожидание человека тем же снимком,
по которому его ждут (`waits: false`, причина «хочет говорить: передан телемаркетологу»); поля решения человека
(`reviewed_*`) не трогаются. Тесты — `tests/test_sales_reply_handoff.py` (15; на коде без стыка красные 13 и 2):
передача после коммита снимка, отказ передачи — ответ разобран, другие виды — передача не зовётся, снятие ожидания.
На голове 5.3 со стыком — тесты передачи, бота, Kommo и разбора ответов продаж 302 passed; mypy, ратчет, ruff — 0.

## Предохранитель

`delivery_check --require-ci --diff-base origin/main` в клоне с черновиками и четырьмя строками STATUS из main
`9ee3d9e` (`logs/main-0710b/delivery-check-clone.txt`): `breakers: files=25 net_loc=3315 (+3329/-14), excluded=1`
(исключён `delivery/complexity-snapshot.json`) при пределах 25 и 800 — **единственная ошибка: `net loc_diff 3315 > 800`**;
файлов — ровно на пределе (25). Без строки `waivers:` так и задумано. Против main всего 26 файлов, +3367/−22.

Модульный срез продаж — одним PR под постоянный вейвер владельца; строку `waivers:` пишет координатор. Общая часть —
**256 строк** (+246/−10) в 13 файлах при пределе ~300: миграция `a9e76c0eb5b0` (+78), `backend/cli/sales_telegram.py`
(+68), `backend/config/sales.py` (+39/−1), `.env.example` (+12), `backend/workers/reaper.py` (+11/−4), `.secrets.baseline`
(+11/−2), `tests/conftest.py` (+11), `tests/test_reaper.py` (+6/−2), `backend/cli/sales_commands.py` (+4/−1),
`backend/features/core/models/__init__.py` (+2), `tests/test_schema.py` (+2), `tests/test_prune.py` (+1),
`tests/test_prune_test_traces.py` (+1). Код и настройки среза — 1393 добавленных строки (из них `.env.example` и
`.secrets.baseline` — 23), тесты — 1936.

## Предупреждения delivery_check, разобранные

Дословно (клон, голова `4705e52`, четыре строки STATUS из main `9ee3d9e`):

```
ERROR: circuit breaker: net loc_diff 3315 > 800 — split the PR or add a human waiver line to STATUS (§3.4)
breakers: files=25 net_loc=3315 (+3329/-14), excluded=1 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: class M: human_ok_spec deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 1 error(s), 5 warning(s)
```

- «human_ok_spec deferred» — «да» владельца на Spec 5.3 вписывает координатор (`yes at=… by=human:…`).
- «asserts_reviewed_by deferred» — 214 утверждений ждут подписи человека (дайджест ниже).
- «`irreversible_surfaces:` не называет отправка наружу» — строка main, подписана владельцем (объединённая строка
  продаж — в #220); предупреждение останется: детектор ищет пункт ровно «отправка наружу».
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта; идемпотентность `start`
  (повтор триггера — одна передача) — примерами A2 и мутантами M1, M13.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.
- С четырьмя строками из STATUS main на `1fd24ca` (как они стоят в черновике) — шестое предупреждение:
  «`model_surface`: 6 из 6 элементов не совпадают»; строки вписывает координатор из STATUS main на момент PR.

## Проверка волн

Клон с черновиками 5.3 в `delivery/active/` (голова `4705e52`, четыре строки STATUS из main `9ee3d9e`): exit 0,
«нарушений нет»; волны В3б, В3в, В4, В2 — «пока волну судит человек». Зелёное держится на строке «Согласовано:
(координатор)» — без слова «Согласовано» проверка красная («shared_changes без «согласовано: …»», exit 1; проверено
утром на `70776fe`, `logs/main-0710/contour-waves-clone.txt`). **До PR заглушку заменяет координатор настоящим
согласием.** В дереве (STATUS в `delivery/active/` — чужой среза) проверка красная по построению.

## Не замерено живьём

- Kommo и Telegram — только `KommoFixture` и `httpx.MockTransport`; сети не было.
- Задача в настоящей очереди и третий цикл процесса разбора — воркеры локально не поднимались (общий Redis);
  проводка — тестами с подменой сессии и очереди.
- Отставание поиска Kommo от записи (риск в decisions) — не замерено: доступа нет.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **214**, из них без ссылки на пример спеки:
**213**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert started == [
T3	assert row is not None
-	assert queued == [row.id]
-	assert list(kommo.leads) == [9301]
-	assert (deal.draft.email, deal.draft.site, deal.draft.title) == (
-	assert deal.draft.hypothesis == "гипотеза acme.example.test"
-	assert deal.draft.name == "Иван Примеров"
-	assert "Давайте созвонимся во вторник после обеда." in note
-	assert f"{APP}/threads/{dialog.thread.id}" in note
-	assert "ГЕО: de" in note
-	assert message.count("\n") == 2
-	assert sent(api) == [(PERSONAL, message), (GROUP, message)]
-	assert (row.kommo, row.telegram, row.kommo_lead_id) == (
-	assert (row.notified_link, row.notified_at, row.due_at, row.claimed_at) == (
-	assert row.noted_reply_id == dialog.reply.id
-	assert alerts == []
-	assert await handoff.handed_off(session, dialog.lead.id) is True
-	assert await handoff.handed_off(session, dialog.lead.id) is False
-	assert queued == [row.id]
-	assert list(kommo.leads) == [9301], "вторая сделка на повторе"
-	assert len(notes) == 2
-	assert "аудит ссылок" in notes[1]
-	assert len(api.seen) == 2, "новый лид не новый: телемаркетологу второй раз не пишем"
-	assert (row.kommo, row.noted_reply_id) == (HandoffKommo.DONE, later.id)
-	assert row.kommo is HandoffKommo.DONE, "итог прошлой задачи — «записано»"
-	assert len(kommo.leads[9301].notes) == 2
-	assert len(kommo.leads) == 1
-	assert again.id == row.id
-	assert queued == []
-	assert len(kommo.leads[9301].notes) == 1
-	assert len(api.seen) == 2
-	assert (
-	assert sent(api) == [(PERSONAL, message), (GROUP, message)], "тишина при отказе Kommo"
-	assert (row.kommo, row.telegram, row.kommo_lead_id, row.attempts) == (
-	assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
-	assert row.last_error is not None
-	assert "HTTP 503" in row.last_error
-	assert "Kommo не ответил" in alert
-	assert f"диалог №{dialog.thread.id}" in alert
-	assert "ivan@" not in alert, "адрес лида не уходит в чат эксплуатации"
-	assert list(kommo.leads) == [9301]
-	assert sent(api)[2:] == [(PERSONAL, deal_message), (GROUP, deal_message)]
-	assert len(api.seen) == 4, "повтор без сделки не шлёт ту же ссылку второй раз"
-	assert (row.kommo, row.attempts, row.due_at) == (HandoffKommo.DONE, 3, None)
-	assert len(alerts) == 2, "тревога — на входе в повтор и на выходе, не на каждом круге"
-	assert "заведена" in alerts[1]
-	assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.RETRY, 9301)
-	assert len(api.seen) == 2, "сделка есть — ссылка на неё уже у телемаркетолога"
-	assert len(kommo.leads[9301].notes) == 2
-	assert (await reread(session, row.id)).kommo is HandoffKommo.DONE
-	assert kommo.writes == 1, "повтор записи после KommoUnconfirmedError"
-	assert len(kommo.leads) == 1
-	assert (row.kommo, row.kommo_lead_id, row.due_at) == (HandoffKommo.UNCONFIRMED, None, None)
-	assert queued == [], "неподтверждённую запись не трогает и новый ответ"
-	assert sent(api) == [(PERSONAL, message), (GROUP, message)]
-	assert "проверить в Kommo руками" in alert
-	assert kommo.writes == 1
-	assert row.kommo is HandoffKommo.RETRY
-	assert row.last_error is not None
-	assert "запись не дошла" in row.last_error
-	assert kommo.writes == 1
-	assert row.kommo is HandoffKommo.UNCONFIRMED
-	assert (kommo.searches, kommo.writes, row.kommo) == (2, 1, HandoffKommo.UNCONFIRMED)
-	assert kommo.notes_tried == 1
-	assert (row.kommo, row.noted_reply_id) == (HandoffKommo.DONE, dialog.reply.id)
-	assert row.last_error is not None
-	assert "примечание" in row.last_error
-	assert len(alerts) == 1
-	assert (row.kommo, row.due_at) == (HandoffKommo.FAILED, None)
-	assert sent(api)[0] == (PERSONAL, message)
-	assert "ключ Kommo отклонён" in alert
-	assert queued == [row.id], "новый ответ — новая попытка: ключ могли починить"
-	assert [chat for chat, _ in sent(api)] == [PERSONAL, PERSONAL, PERSONAL]
-	assert (row.kommo, row.telegram, row.notified_link) == (
-	assert "не доставлено" in alert
-	assert "HTTP 503" in alert
-	assert row.telegram is HandoffTelegram.UNDELIVERED
-	assert row.last_error is not None
-	assert TOKEN not in row.last_error
-	assert all(TOKEN not in alert for alert in alerts)
-	assert [chat for chat, _ in sent(api)] == [PERSONAL]
-	assert row.telegram is HandoffTelegram.SENT
-	assert [chat for chat, _ in sent(api)] == [PERSONAL]
-	assert row.last_error is not None
-	assert "SALES_TELEGRAM_GROUP_CHAT_ID" in row.last_error
-	assert row.telegram is HandoffTelegram.SENT
-	assert "копия в группу" in alert
-	assert sent(api) == [(PERSONAL, dialog_line(dialog)), (GROUP, dialog_line(dialog))]
-	assert (row.kommo, row.telegram, row.kommo_lead_id, row.attempts) == (
-	assert alerts == []
-	assert queued == []
-	assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.DONE, 9301)
-	assert sent(api)[-1] == (GROUP, f"{HEAD}Ссылка на сделку в коммо: {DEAL}")
-	assert sent(api)[0][1].endswith(f"диалог №{dialog.thread.id} (SALES_APP_URL не задан)")
-	assert caught.value.permanent is True
-	assert row.lead_id == dialog.lead.id
-	assert row.lead_id == dialog.lead.id
-	assert getattr(caught.value, "permanent", False) is False
-	assert (await reread(session, row.id)).telegram is HandoffTelegram.SENT
-	assert (await reread(session, row.id)).claimed_at is None
-	assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
-	assert "не поставлена" in caplog.text
-	assert await handoff.due(session, now=later) == [row.id]
-	assert sorted(taken) == sorted([rows["retry"].id, rows["forgotten"].id])
-	assert pushed.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
-	assert await handoff.due(session, now=NOW) == [], "взятое проходом не берётся вторым кругом"
-	assert (row.kommo, row.kommo_lead_id, row.noted_reply_id) == (HandoffKommo.DONE, 9301, None)
-	assert kommo.leads[9301].notes == {}
-	assert sent(api)[0] == (PERSONAL, f"{HEAD}Ссылка на сделку в коммо: {DEAL}")
-	assert (kommo.writes, row.kommo) == (1, HandoffKommo.UNCONFIRMED)
-	assert row.last_error is not None
-	assert "поиск после записи не ответил" in row.last_error
-	assert calls == [(handoff.HANDOFF_JOB, 4127, ["result_ttl", "retry"])]
-	assert handoff.HANDOFF_JOB == "backend.features.sales.handoff_jobs.hand_off_lead"
-	assert broken.calls == ["rollback", "execute", "commit"]
-	assert "захват передачи не снят" in caplog.text
-	assert handoff_jobs.connected_kommo(http) is None
-	assert isinstance(handoff_jobs.connected_kommo(http), KommoLive)
-	assert kommo is not None
-	assert kommo.lead_url(9301) == ""
-	assert outcome == {
-	assert json.loads(request.content)["text"].endswith(f"{APP}/threads/{dialog.thread.id}")
-	assert outcome["kommo"] == HandoffKommo.FAILED.value
-	assert outcome["telegram"] == "sent"
-	assert "SALES_KOMMO_PIPELINE_ID" in alert_text
-	assert outcome == {
-	assert remembered == [
-	assert handoff_jobs.hand_off_lead(31) == {"handoff": 31}
-	assert queued == [row.id]
-	assert pushed is not None
-	assert pushed.due_at is not None
-	assert pushed.due_at > datetime.now(UTC) + timedelta(seconds=cfg.HANDOFF_RETRY_SEC - 60)
-	assert "не поставлен" in caplog.text
-	assert queued == []
-	assert (cfg.HANDOFF_PASS_SEC, "Повтор передачи лидов продаж") in started
-	assert isinstance(http, httpx.AsyncClient)
-	assert loop.time() < deadline, "циклы разбора не дошли до условия"
-	assert not loops.done(), "исключение прохода передачи вышло из циклов разбора"
-	assert calls["sweep"] >= 2
-	assert calls["watch"] >= 2
-	assert calls["handoffs"] >= 2
-	assert beats["Разбор мёртвых прогонов"]["failures"] == 0
-	assert beats["Сторож тишины"]["failures"] == 0
-	assert beats[HANDOFF_LOOP]["failures"] >= 2
-	assert sorted(tried) == sorted(ids), "отказ очереди на первой передаче остановил вторую"
-	assert called == []
-	assert caplog.text.count("повтор передачи лида не поставлен") == 2
-	assert reaper.retry_handoffs is handoff_jobs.retry_pass
-	assert len(tried) == 2
-	assert called == []
-	assert _beats(tmp_path)[HANDOFF_LOOP]["failures"] == 0
-	assert health.beat_problems(tmp_path) == []
-	assert await connection.run_sync(_present) == SCHEMA
-	assert down == set()
-	assert up == SCHEMA
-	assert row is not None
-	assert (row.kommo, row.telegram, row.attempts) == (
-	assert row.kommo_lead_id is None
-	assert await session.scalar(select(SalesHandoffModel.id)) is None
-	assert "_no_real_sales_bot" in request.fixturenames
-	assert chats == ("", "", "")
-	assert [getattr(empty, field) for _, field, _, _ in SETTINGS] == [d for *_, d, _ in SETTINGS]
-	assert [getattr(filled, field) for _, field, _, _ in SETTINGS] == [v for *_, v in SETTINGS]
-	assert len(api.seen) == 1
-	assert str(api.seen[0].url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
-	assert (body["chat_id"], body["text"]) == (PERSONAL, "Новый лид\nЭмейл Рассылка\nСсылка")
-	assert pauses == []
-	assert len(api.seen) == 3
-	assert pauses == [2.0, 5.0]
-	assert caught.value.permanent is False
-	assert "HTTP 503" in str(caught.value)
-	assert "3 попытки" in str(caught.value)
-	assert len(api.seen) == 3
-	assert pauses == [2.0, 5.0]
-	assert len(api.seen) == 1
-	assert pauses == []
-	assert caught.value.permanent is True
-	assert advice in str(caught.value)
-	assert TOKEN not in str(caught.value)
-	assert TOKEN not in str(caught.value)
-	assert "ConnectError" in str(caught.value)
-	assert caught.value.__cause__ is None
-	assert caught.value.__suppress_context__ is True
-	assert pauses == [3.0]
-	assert pauses == []
-	assert len(api.seen) == 1
-	assert caught.value.permanent is False
-	assert caught.value.permanent is True
-	assert pauses == []
-	assert api.seen == []
-	assert caught.value.permanent is True
-	assert api.seen == []
-	assert "sendMessage" in log, "строка httpx о запросе должна быть — иначе проверять нечего"
-	assert TOKEN not in log
-	assert telegram.HIDDEN in log
-	assert len(api.seen) == 1
-	assert str(api.seen[0].url).endswith("/getUpdates")
-	assert found == [
-	assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_OK
-	assert "583920471\tprivate\tТест Тестов" in out
-	assert "-1009384756102\tsupergroup\tВыдуманная группа" in out
-	assert "SALES_TELEGRAM_CHAT_ID" in out
-	assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_NOBODY
-	assert "Start" in capsys.readouterr().out
-	assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_NOT_CONFIGURED
-	assert "SALES_TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
-	assert api.seen == []
-	assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_REFUSED
-	assert "HTTP 401" in err
-	assert TOKEN not in err
-	assert "sales-telegram-chat-id" in _COMMANDS
-	assert build_parser().parse_args(["sales-telegram-chat-id"]).command == "sales-telegram-chat-id"
-	assert "ничего не изменилось" in _KEPT_ON_INTERRUPT["sales-telegram-chat-id"]
-	assert isinstance(http, httpx.AsyncClient)
```

Привязаны к примерам: **T3**. Остальные 213 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 213

## Проверки на голове PR

Ветка перенесена на main `9eca0c6`; голова кода `92c12b6`, проверки — по одному разу. pytest (2418 passed), diff-coverage, pre-commit и фронт прошли на голове ветки поверх головы #220 (`2bfd3fb`, дерево main `b57bc30`); после переноса на main `9eca0c6` (#222 — Этапы 1–2, файлов среза не задевает) и правки очереди передачи по ревью (`31654cc`: 4 новых теста, на старом коде красные) заново — тесты передачи, бота, Kommo, разбора ответов продаж, процесса разбора, здоровья воркеров, очереди, схемы и чистки (424 passed), mypy, `lint-imports` и быстрые проверки таблицы. Полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | a9e76c0eb5b0 (head) | 0 |
| ратчет сложности | Ратчет сложности: 421 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 2418 passed in 447.31s (0:07:27) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=29 net_loc=3674 (+3712/-38)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 248` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | см. лог | — |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `a354cae`):

```
breakers: files=29 net_loc=3674 (+3712/-38), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 29, 'max_loc_diff': 3674, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `a354cae`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а deployed · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 33 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
