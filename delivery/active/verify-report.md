# Verify report

**Поставка:** срез `sales-stage` (модуль «Продажи», 1.1b), часть «а» из трёх — значение `sales` у этапа и
громкий отказ продажам в письмах (очередь, сборка, отправка, добивки, ответ в переписке, отправка очереди
пачкой); учётка продаж — механизмом #175.

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=40 утверждений части без ссылки на пример спеки (пример назван заголовком раздела теста, а не в теле — дайджест такие не привязывает); подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `f1d7946` — голова части «а», ветка `sales/1.1b-stage-202` от `0d65206` (#202 поверх main `06604c3`; после слияния #202 — main `ca90fad`, перенос сделает координатор; не запушена)

## Сборка

Перенос 07.10 — в два шага. Сначала коммиты (а) ветки `sales/1.1b-stage-main` (main `05d75d9`) переложены
`cherry-pick` на локальную базу `5a964d3` (#180 до слияния + перечень консоли продаж) с разрешением по смыслу и
дописаны просьбы соседней сессии (ветка `sales/1.1b-stage-next`, голова «а» — `cef7aec`); затем, когда #180 слит,
— на `origin/main` `f9819da` (#196, #194, #180, #198, #197) новой веткой `sales/1.1b-stage-202`. Что изменилось:

- **Консоль (команды продаж — перечнем `cli/sales_commands.py`, с #180 — в main).** Импорт `SalesNotConnectedError`,
  код выхода 9 и строка `_FAILURES` ушли из `cli/main.py` в перечень `FAILURES` модуля `sales_commands.py`; `main.py`
  вливает его целиком (`*SALES_FAILURES`, +2 строки) и продаж не называет.
- **Миграция** — `39e342cb2b21` перецеплена на `cbc5aadf4fc2` — голову main (#180 поверх `ad4a79bc6000` #194); голова одна.
- **#192 (пробный рекламодатель)** — `crawl/probe_advertiser._taken_first` звал `Recipients.candidates(Stage.ADVERTISERS,
  limit=1)`: после среза имени нет (mypy: `"Recipients" has no attribute "candidates"`; тест — `AttributeError`). Вызов —
  по новому имени `advertiser_candidates(limit=1)` (тот же запрос сборки), тест #192 подменяет то же имя; правка — в
  коммите `3c3118c`, где переименование.
- **#197 (`sending.check_ready` одной строкой через `compose.unset_reason`)** — слился сам: мои строки в `sending.py`
  (импорт, `_Target.stage`, `_target`, `donor_path`) его не касаются; `check_ready` — как в main. Файл 490 строк из 500.
- **Снимок сложности** — конфликт разрешён пересборкой по дереву (`scripts/complexity.py --update`).
- **#191 (отправка очереди пачкой), просьбы соседней сессии** — коммит `f1d7946`: мост подключения этапа
  `core/stages.check_connected`; маршрут `POST /api/letters/send-queue` спрашивает его до счёта очереди и до задачи
  (продажам — 409 словами); пачка (`batch._send_one`) ловит `SalesNotConnectedError` вместе с `NoSenderError` до общего
  `except Exception` — встаёт с причиной словами, а не «связь с почтой оборвалась».
- Коммиты `3fd500f` и `b3d1dbb` легли без правки.
- **Третий шаг — на `0d65206` (#202 поверх #199–#201), ветка `sales/1.1b-stage-202`.** Конфликты: `api/errors.py` — в
  `STATUSES` обе строки, `ResolveError` #202 и за ней `SalesNotConnectedError`; `letters/repository.py` — импорты
  (`stages.SalesNotConnectedError` и `chain.FIRST_STEP` #202; правило `queued` «только первые письма» не тронуто);
  `letters/sending.py` — импорты #202 и одно `from backend.features.core import stages` (шлюз `mail_stage` в `_target` —
  до `of_stage` и до выбора ящика `mailbox.choose`/`OwnPathError` #202); `api/letters/routes.py` — импорты
  `unknown_outcome` #202 и `check_connected`; `.secrets.baseline` — сторона main и пересчёт `detect-secrets` (строка
  173 → 178, `generated_at` — как в main); снимок — пересборка. `batch._send_one` и `followups._deliver` слились сами:
  версии #202 плюс мой кортеж `except`. Ревизия `39e342cb2b21` перецеплена на `7bfc6c880f0a` (#201).

| SHA | Суть | Файлов (без `delivery/`) | +/− |
|---|---|---|---|
| `192bdbc` | `Stage.SALES`, ревизия `39e342cb2b21`, `core/stages.py`, 409, код 9 перечнем консоли продаж, тест A1, `tests/test_overview.py` | 9 | +137/−4 |
| `3c3118c` | точки писем на механике #175 + вызов #192 + тесты | 13 | +620/−64 |
| `3fd500f` | учётка направления: событие учётки продаж, A2 без транспорта продаж | 1 | +39/−2 |
| `895e6eb` | снимок сложности (только `delivery/`) | 0 | — |
| `b3d1dbb` | тест липового донора — через общий вход отбора | 1 | +4/−2 |
| `f1d7946` | отправка очереди пачкой: мост, 409 кнопки, остановка пачки словами + тесты | 4 | +71/−3 |
| **часть «а» против `0d65206`** | | **23** | **+865/−69 (net 796)** |

## Shape oracles

На голове `f1d7946` (`git checkout --detach`, дерево чистое):

| Проверка | Итог | exit |
|---|---|---|
| хуки pre-commit на коммитах с правкой (staged) и `pre-commit run --from-ref 0d65206 --to-ref HEAD` (все файлы части) | Passed (22 хука) | 0 |
| `ruff check backend tests` / `ruff format --check backend tests` | чисто / 590 файлов | 0 / 0 |
| `mypy backend` (strict) | 374 файла, ошибок нет | 0 |
| `scripts/gates.py` | 613 файлов, нарушений нет | 0 |
| `scripts/complexity.py` | 389 файлов, расхождений нет | 0 |
| `lint-imports` | 4 контракта, 0 нарушено | 0 |
| `scripts/lint/check_file_length.sh` | OK | 0 |
| `scripts/check_irreversible_signature.sh` | подпись сходится с объявлением | 0 |
| `detect-secrets-hook --baseline .secrets.baseline` (все файлы `backend`, `tests`) | новых секретов нет | 0 |
| `alembic heads` | одна голова — `39e342cb2b21` | 0 |
| `pre-commit run --all-files`, полный pytest | на голове «а» не гонялись (правило нагрузки) — на голове «в», см. её отчёт | — |
| `contour_waves` / `delivery_check` — клон с черновиками части | см. «Проверка волн» и «Предупреждения» | см. ниже |

## Behavior oracles

- [x] PASS — тесты среза и соседей на голове (65 файлов: `test_sales_stage_mail`, `test_letters*`, `test_letters_send_queue`,
      `test_api_letters*`, `test_replies*`, `test_reply_*` (с `test_reply_offers` #194), `test_sendgrid*`, `test_thread*`,
      `test_overview`, `test_schema`, `test_migrations_match_models`, `test_followups`, `test_mail_*`, `test_send_race`,
      `test_delivery_events`, `test_probe_donor`, `test_probe_advertiser`, `test_prune*`, `test_next_address*`, `test_cli_main`,
      консольные тесты продаж, `test_job_outcome`, `test_pipeline`, `test_unsubscribe`, `test_selection_gates`,
      `test_sales_model`, `test_sales_chain*`; с #202 — `test_letters_own_path`, `test_letters_unknown_outcome`,
      `test_letters_unknown_race`; с #201 — `test_manual_price`, `test_api_manual_price`; 70 файлов): **1708 passed** за 2:54,
      exit 0. Полный pytest — на голове «в».

### Красный прогон до кода

1. **main + тесты среза** (прогон 06.10 на `05d75d9` действует: файл теста не собирается без
   `backend.features.core.stages`); событие учётки продаж на main — `assert 403 == 200`.
2. **Пачка без правки** (`batch.py` main, остальное — голова):
   ```
   E       AssertionError: assert 'связь с почт...але платформы' == 'Письмо №1: п...не подключены'
   E         - Письмо №1: продажи к почте ещё не подключены
   E         + связь с почтой оборвалась на письме №1: ушло ли оно, неизвестно, и дальше пачка не шла — проверьте его в журнале платформы
   ```
3. **Кнопка без моста** (`routes.py` main): с письмом продаж в очереди — `assert 200 == 409` (задача поставлена); на
   пустой очереди — «В очереди этого этапа писем нет — отправлять нечего» вместо причины.
4. **Вызов #192 без правки** — `tests/test_probe_advertiser.py`: `AttributeError: 'Recipients' object has no attribute
   'candidates'` (`probe_advertiser.py:271`); тест отказа — `AttributeError: … has no attribute 'candidates'` в подмене.

Прогоны 2–4 сделаны на базе `5a964d3`; `batch.py`, `api/letters/routes.py`, `crawl/probe_advertiser.py` и тест #192 на
main `f9819da` — те же (`git diff 5a964d3 f9819da` по ним пуст).

### Обратные прогоны — места, переписанные при переносе 07.10, и правки для соседей

Мутант — замена строк (каждая находится ровно один раз) в дереве ветки, прогон, возврат файла со сверкой sha256.
Прогнаны на вершине ветки и на базе `5a964d3`, и на `origin/main` `f9819da` — итог один.

| Мутант | Файл | Убит |
|---|---|---|
| C1 — перечень отказов продаж пуст | `cli/sales_commands.py` | `test_console_says_it_with_its_own_exit_code` (трассировка вместо кода 9) |
| C2 — `main.py` не вливает перечень продаж | `cli/main.py` | то же |
| C3 — код выхода продаж 2 (занят) | `cli/sales_commands.py` | то же: `assert 2 == 9` |
| P1 — пробный: отбор доноров вместо рекламодателей | `crawl/probe_advertiser.py` | `tests/test_probe_advertiser.py` — «Сборка офферов первым берёт не пробного» ×4 |
| M1 — ревизия на прежней голове `fc721be3d031` | `39e342cb2b21_stage_sales.py` | conftest: `alembic upgrade head` — «Multiple head revisions are present» |
| S4 — новый этап `PROBE` | `core/domain.py` | mypy: `stages.py:58` (`mail_stage`) и `stages.py:76` (`check_connected`) — `assert_never` |
| M-a1 — пачка: отказ продажам не в остановке (как на main) | `letters/batch.py` | `test_the_batch_stops_on_a_sales_letter_in_words` |
| M-a2 — пачка: отказ продажам её не останавливает | `letters/batch.py` | то же: `stopped=None` |
| M-b1 — кнопка без моста | `api/letters/routes.py` | `test_screen_gets_409_before_the_job_queue[0]` (слова), `[1]` (`200`) |
| M-b2 — мост пропускает продажи | `core/stages.py` | то же |
| M-b3 — мост отказывает донорам и рекламодателям | `core/stages.py` | `tests/test_letters_send_queue.py` (#191): `409 == 200`, «писем нет» |
| M-b4 — мост после счёта очереди | `api/letters/routes.py` | `…[0]`: «писем нет» вместо причины |
| M-b5 — мост после постановки задачи | `api/letters/routes.py` | `…[0]`, `…[1]` |

**13 из 13 убиты.** Места переноса 06.10 (шлюз, шлюз после транспорта, `donor_path` mypy, возврат срока, отчёт
прохода, ключ событий) — 6 из 6 (отчёт среза, «Перенос на main 06.10»); точки, не переписанные при переносах, —
16 из 16 (ветка `sales/1.1b-stage`).

## Живой прогон

Не проводился: стенд не поднимался. Почта продажам по построению не уходит — отказ стоит до выбора транспорта
этапа, до заведения рассылки, до захвата добивки и до задачи пачки. Статус — «заложено».

## Product oracles

- [x] PASS — `eval-smoke.md`: A1, A2, A4, A5, точки писем, ответ в переписке, пачка и кнопка, пробный рекламодатель,
      консоль, учётка направления, красный прогон, мутанты; живое письмо и проход добивок — «заложено».

## Ревью рисковых мест

- **деньги** — риска нет, потому что денег часть не считает и не пишет: слово «цена» — в докстроке
  `backend/features/core/stages.py` («цена из ответа лида в карточке донора» — от чего отказ защищает).
- **безопасность** — ключи учётки продаж в срезе не читаются и не пишутся: `monkeypatch.setenv("OUTREACH_SALES_EVENTS_PUBLIC_KEY", own_public)`
  в тесте — открытый ключ из `_keypair()` теста. `secret` — путь `.secrets.baseline`, где у записи
  `backend/features/core/domain.py` сдвинулся только `"line_number": 161` → `166` (ложное срабатывание, хэш тот же).
  Маршруты и права прежние: `send_queue` — под тем же `_sender` (право `send`); в `STATUSES` —
  `SalesNotConnectedError: status.HTTP_409_CONFLICT`.
- **транзакция БД** — главный риск части — захват добивок: фильтр стоит в выборе строки —
  `.where(*_due(moment), _chained())`, — письмо продаж под `UPDATE` не попадает, погашать нечего. Второй
  рубеж — `except (NoSenderError, SendError, SalesNotConnectedError) as exc:` с `chain.restore(…)` и
  `await session.commit()`; ветка `except MaybeSentError` (#175) стоит раньше и срок не возвращает. Пачка: отказ —
  в `_target`, до `_claim` (`UPDATE … status=SENDING`), письмо продаж остаётся `queued`; кнопка — `await check_connected(session, body.stage, "Очередь писем не отправлена")`
  до `LetterRepository(session).queued(…)` и до `runs_queue().enqueue(…)`: в базе и в очереди задач ничего не меняется.
- **интеграция** — внешних вызовов часть не добавляет: `return _Target(message, host, email, stages.mail_stage(stage, f"Письмо №{message_id}"))`
  — до `transport = of_stage(self._transports, target.stage.value)`; учётка продаж для письма продаж не собирается;
  пачка продаж не доходит до транспорта. Вебхук событий — прежний маршрут, ключи — по всем значениям `Stage`.
- **новый модуль** — `backend/features/core/stages.py` (константа, исключение, шлюз, мост `check_connected`, пометка
  `donor_path`; состояния нет), ревизия `39e342cb2b21_stage_sales` (одна команда `ALTER TYPE stage ADD VALUE IF NOT EXISTS 'sales'`).

## Предохранитель

`breakers:` части «а» против `0d65206`, тем же правилом исключений, что у `delivery_check` (`out_of_blast_radius`):

```
breakers: files=23 net_loc=796 (+865/-69), excluded=1 ['delivery/complexity-snapshot.json']
```

В пределах 25 файлов и 800 строк. Части «б» и «в» — в их отчётах (10 / 449 и 9 / 322, каждая против
предыдущей головы); весь срез одним PR — 39 файлов / 1567.

## Предупреждения delivery_check, разобранные

Дословно (клон вершины `f1d7946`, черновики части в `delivery/active/`, `--require-ci --diff-base 0d65206`), exit 0:

```
breakers: files=23 net_loc=796 (+865/-69), excluded=1 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: `model_surface`: 6 из 6 элемент(ов) не совпадают ни с одним файлом дерева — правку по ним проверка не увидит: DEAD), LLM_JUDGE_MODEL` (деф. gpt-5-mini), LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели. Форма поля — пути от корня и глобы через запятую, без бэктиков и прозы; пояснения — в <!-- … --> на той же строке или строкой ниже
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 5 warning(s)
```

- «`model_surface`: 6 из 6 элементов не совпадают ни с одним файлом дерева» — новая проверка контура (#198, delivery@2.00):
  строка `model_surface:` в STATUS прозой; та же строка и то же предупреждение — у STATUS main. Строку при сборке
  подставит координатор (как и `stack:`), черновик её не меняет.
- «`irreversible_surfaces:` не называет отправка наружу» — детектор находит маркеры отправки во всём продуктовом
  коде (письма доноров); предупреждение стоит и на STATUS main. Строка подписана владельцем и переносится
  дословно; часть отправки не добавляет (подпись сходится: `check_irreversible_signature.sh` — exit 0).
- «asserts_reviewed_by deferred» — утверждения ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта (новая зависимость —
  решение владельца). Инвариант части держат параметризованные тесты.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

Дословно (тот же клон, `--base 0d65206`), exit 0:

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 21 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```

В рабочем дереве (в `delivery/active/` — STATUS main, срез 4.6a) та же проверка против базы `0d65206` на вершине
ветки красная одной строкой «общий код тронут без объявления» — по файлам среза (exit 1); черновик `shared_changes:` называет
каждый файл части полным путём со словом «Согласовано». База каждой части — голова предыдущей: PR сливаются по
порядку, и после слива «а» и «б» база «в» — main.

## Spec coverage gaps

- A2, A4, A5 — тестами на базе дерева; живой прогон — «заложено».
- Непокрытые строки изменённого кода части — ветки `case _: assert_never(...)` (недостижимы по построению).
- `mail-test --stage sales` — отказ словами (первого письма продаж нет): учётку продаж пробным письмом
  проверить нечем до шаблонов продаж — открытый вопрос в отчёте среза.
- Правило «продажи подключены» — мост отказывает продажам всегда; правило встанет срезом отправки продаж.

## Находки (общий код — не чинил)

1. **Ветка соседней сессии в работе («цена руками») правит те же общие перечни**, что и часть: `backend/cli/main.py`
   (+3), `backend/cli/sales_commands.py`, `backend/api/errors.py` (+5), `backend/features/core/domain.py` (+12),
   `.secrets.baseline` — слита в main как #201; при переносе на `0d65206` перечни склеены, строка секрета пересчитана,
   ревизия `39e342cb2b21` перецеплена на её `7bfc6c880f0a`.
2. **`backend/cli/mail_test.py` `_announce`** печатает предохранитель общей учётки для любого `--stage` (находка 06.10,
   на main та же).

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Готово к PR после: STATUS части в `delivery/active/` (строки `stack:` и `model_surface:` — подставит координатор при
сборке); подписи дайджеста утверждений; ОК соседней сессии на общие файлы. Слив — не раньше первого письма Этапа 2
(решение владельца 01.10).

## Assertion digest (ревью ожиданий, не кода)

База: `0d65206` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **40**, из них без ссылки на пример спеки:
**40**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert set(body["letters"]) == {stage.value for stage in Stage}
-	assert await connection.run_sync(_stage_values) == ["donors", "advertisers", "sales"]
-	assert world.letter.status is MessageStatus.QUEUED
-	assert is_permanent(SalesNotConnectedError("Очередь писем не собрана"))
-	assert str(refused.value) == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
-	assert source.asked == []
-	assert world.letter.status is MessageStatus.QUEUED
-	assert world.letter.sender_id is None
-	assert world.letter.internet_message_id is None
-	assert world.letter.next_action_at is None
-	assert response.status_code == 409
-	assert response.json()["detail"] == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
-	assert report.stopped == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
-	assert (report.sent, dict(report.refused), report.left) == (0, {}, 1)
-	assert response.status_code == 409
-	assert response.json()["detail"] == f"Очередь писем не отправлена: {SALES_NOT_CONNECTED}"
-	assert jobs_queue.enqueued == []
-	assert response.status_code == 200, response.text
-	assert response.json()["accepted"] is True
-	assert (campaigns, letters) == (0, 0)
-	assert [candidate.host for candidate in picked] == ["donor-a.example.test"]
-	assert response.status_code == 409
-	assert response.json()["detail"] == f"Очередь писем не собрана: {SALES_NOT_CONNECTED}"
-	assert jobs_queue.enqueued == []
-	assert response.status_code == 409
-	assert SALES_NOT_CONNECTED in response.json()["detail"]
-	assert code == 9
-	assert (
-	assert result == {
-	assert (report.waiting, report.sent, report.postponed) == (1, 1, 0)
-	assert "ждут этапа 1" in report.as_report
-	assert world.letter.next_action_at == due
-	assert donor.next_action_at is None  # её добивка ушла, срок следующей — у добивки
-	assert SALES_NOT_CONNECTED in caplog.text
-	assert followups_of_sales == 0
-	assert template.CHAINED == (Stage.DONORS, Stage.ADVERTISERS)
-	assert (report.sent, report.postponed, report.waiting) == (0, 1, 0)
-	assert world.letter.next_action_at == NOW + followups.POSTPONE
-	assert waiting is MessageStatus.QUEUED
-	assert answers == 0
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 40

## Проверки на голове PR

Ветка перенесена на main `15f0e55`; голова кода `8575cf7`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 39e342cb2b21 (head) | 0 |
| ратчет сложности | Ратчет сложности: 390 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 611 passed in 152.78s (0:02:32) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=23 net_loc=796 (+865/-69)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 40` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | см. лог | — |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `9b6cccc`):

```
breakers: files=23 net_loc=796 (+865/-69), excluded=10 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `9b6cccc`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 21 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
