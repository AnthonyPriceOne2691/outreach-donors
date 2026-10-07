# Verify report

**Поставка:** срез `sales-stage` (модуль «Продажи», 1.1b), часть «б» из трёх — ответ лида продаж ждёт человека:
не лид рекламодателя и не цена донора; экраны диалогов не падают на состоянии, которого ещё не знают.

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=27 утверждений части без ссылки на пример спеки (A3 назван заголовком теста, а не в теле — дайджест такие не привязывает); подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `a9d94da` — голова части «б», ветка `sales/1.1b-stage-202` поверх головы «а» `f1d7946` (база `0d65206` — #202; не запушена)

## Сборка

Перенос 07.10: коммит ответов (`5e714a4` ветки `sales/1.1b-stage-main`) переложен сначала на локальную базу `5a964d3` —
без правки, — затем на `origin/main` `f9819da`. Там #194 («цены списком») правит `replies/repository.py` (+5 строк,
импорт `typing.Any` — конфликт с импортом `assert_never` части) и `replies/pipeline.py` (слился сам). Ветки продаж в
`confirm` и `take_lead`, как были, дали бы файл в 501 строку при пределе 500: правила этапа вынесены в новый
`backend/features/replies/stage_rules.py` (`price_confirmable`, `lead_stage`; разбор этапа целиком). В
`repository.py` — по строке вызова; отказы рекламодателю (`NotAPriceError`) и «не лид» (`LeadError`) — код соседней
сессии — на месте, меняется только строка условия. **`replies/repository.py`: main `f9819da` — 490 строк, с частью —
491** (на старой базе часть давала 497 из 500).

На `0d65206` (#201 «цена руками» и #202): конфликт импорта в `replies/repository.py` с `put_price` #201 — склейка;
**`replies/repository.py`: 493 строки на базе, 494 с частью** (предел 500). Второй коммит — просьба соседней сессии:
запасное значение состояния диалога на экранах (`api/labels.threadState`), с
тестом. Просили его в часть «а» — она 795 строк из 800, правка с тестом её переполнила бы; «б» — часть, с которой
сервер начинает отдавать новое состояние (`sales_pending`).

| SHA | Суть | Файлов (без `delivery/`) | +/− |
|---|---|---|---|
| `75a42ba` | ответы: `outcome`, `pipeline`, `replies/repository`, новый `replies/stage_rules`, `outreach/threads` + тесты; снимок | 6 | +412/−35 |
| `a9d94da` | экраны диалогов: `threadState` — незнакомое состояние кодом; список и карточка через неё + тест | 4 | +79/−7 |
| **часть «б» против головы «а» `f1d7946`** | | **10** | **+491/−42 (net 449)** |

## Shape oracles

На голове `a9d94da` (`git checkout --detach`, дерево чистое):

| Проверка | Итог | exit |
|---|---|---|
| хуки pre-commit на коммитах части (staged) и `pre-commit run --from-ref f1d7946 --to-ref HEAD` (файлы части) | Passed (25 хуков) | 0 |
| `ruff check backend tests` / `ruff format --check backend tests` | чисто / 592 файла | 0 / 0 |
| `mypy backend` (strict) | 375 файлов, ошибок нет | 0 |
| `scripts/gates.py` | 615 файлов, нарушений нет | 0 |
| `scripts/complexity.py` | 390 файлов, расхождений нет | 0 |
| `lint-imports` | 4 контракта, 0 нарушено | 0 |
| `scripts/lint/check_file_length.sh` | OK (`replies/repository.py` 494, `api/labels.ts` 545 при пределе 554) | 0 |
| `detect-secrets-hook --baseline .secrets.baseline` | новых секретов нет | 0 |
| `scripts/check_irreversible_signature.sh` | подпись сходится | 0 |
| `alembic heads` | одна голова — `39e342cb2b21` | 0 |
| `tsc --noEmit` / ESLint (`api/labels.ts`, `src/threads/`) / Prettier | чисто | 0 / 0 / 0 |
| `pre-commit run --all-files`, полный pytest | на голове «б» не гонялись — на голове «в», см. её отчёт | — |

## Behavior oracles

- [x] PASS — тесты среза и соседей на голове (тот же набор, что у «а», плюс `test_sales_stage_replies`, 71 файл):
      **1728 passed** за 3:18, exit 0.
- [x] PASS — vitest `src/threads src/letters src/sales` (`--maxWorkers=2 --reporter=dot`): 17 файлов, **184 passed**, exit 0.

### Красный прогон до кода

- Ответы — код части при переносах по поведению не менялся, прежний прогон ветки `sales/1.1b-stage` действует: на
  значении без отказов (`d432e40` с подставленными именами `SALES_WAITING`, `priced_by_model`, `SALES_PENDING` и
  прежней логикой) — **9 из 20 красные**, зелёные 11 названы (страховка: прежний код уже был верен, часть свела
  правило в одно или сделала явным).
- Вынос правил этапа — те же тесты, красные без правила: мутанты R1–R5 ниже.
- Экраны диалогов — экраны main (`ThreadPage.tsx`, `ThreadsPage.tsx` без `threadState`) + новый тест: оба теста
  красные —
  ```
  FAIL  src/threads/UnknownThreadState.test.tsx > состояние, которого экран ещё не знает > список диалогов показывает его кодом, а не падает
  FAIL  src/threads/UnknownThreadState.test.tsx > состояние, которого экран ещё не знает > карточка диалога — тоже
  TypeError: Cannot read properties of undefined (reading 'color')
  ```

### Обратные прогоны

- Точки ответов, не переписанные при переносе (`outcome`, `pipeline`, `outreach/threads`), — прежний прогон ветки
  `sales/1.1b-stage`: B1-decide, B2-contact, B3-autoreply, B4-priced, B5-parser, B8-state, B9-review — **7 из 7 убиты**;
  mypy на новом этапе — ошибки `assert_never` в `outcome.py`, `pipeline.py`, `outreach/threads.py`. B6-confirm и
  B7-lead — точки, переписанные выносом правил: вместо них R1–R5 ниже.
- Правила этапа, вынесенные при переносе на main `f9819da` (на голове `a9d94da`; дерево ветки, замена ровно одного
  места, возврат со сверкой sha256), — тесты среза, `test_api_replies`, `test_letters_advertisers`,
  `test_api_letters_advertisers`, `test_lead_handoff`:

| Мутант | Файл | Убит |
|---|---|---|
| R1 — цена продажам без отказа | `replies/stage_rules.py` | `test_price_confirmation_is_refused_before_any_write` — `DID NOT RAISE SalesNotConnectedError` |
| R2 — цену рекламодателя можно подтвердить | `replies/stage_rules.py` | `test_letters_advertisers.py::TestAnAdvertiserAnswers::test_price_confirmation_is_refused` — `DID NOT RAISE NotAPriceError` |
| R3 — лид продажам без отказа | `replies/stage_rules.py` | `test_sales_answer_is_not_taken_as_an_advertiser_lead` — `LeadError` вместо отказа продажам |
| R4 — `confirm` не спрашивает правило (как на main) | `replies/repository.py` | R1-тест |
| R5 — `take_lead` не спрашивает правило (как на main) | `replies/repository.py` | R3-тест |
| R6 — новый этап `PROBE` | `core/domain.py` | mypy: `stage_rules.py:37` (`price_confirmable`) и `:55` (`lead_stage`) — `assert_never` |

- Запасное значение экрана (07.10):

| Мутант | Файл | Убит |
|---|---|---|
| V-c1 — запас без слов (`title: ''`) | `api/labels.ts` | `UnknownThreadState.test.tsx` — оба теста |
| V-c2 — значок строки без запаса (как на main) | `threads/ThreadsPage.tsx` | «список диалогов показывает его кодом, а не падает» |
| V-c3 — значок карточки без запаса (как на main) | `threads/ThreadPage.tsx` | «карточка диалога — тоже» |
| V-c4 — знакомые состояния тоже кодом | `api/labels.ts` | `ThreadPage.test.tsx` («ждёт разбора» в шапке), `ThreadsPage.test.tsx` ×2, `ThreadPageSales.test.tsx` |

**10 из 10 убиты** (R1–R6, V-c1…V-c4).

## Живой прогон

Не проводился: писем продаж нет, пока почта им отказывает, — ответа лида через вебхук быть не может; экран с
незнакомым состоянием живьём не смотрелся (значок — прежние стили). «Заложено».

## Product oracles

- [x] PASS — `eval-smoke.md`: A3, модель не зовётся, повтор вебхука, цена и лид отказывают, состояние диалога и
      причина в карточке, экраны с незнакомым состоянием; живой ответ — «заложено».

## Ревью рисковых мест

- **деньги** — главный риск части, и он закрыт: `ReplyRepository.confirm` спрашивает
  `stage_rules.price_confirmable(await self.stage_of(reply), reply.id)` ДО строк `reply.price_white = price_white`,
  `reply.price_grey = price_grey`, `reply.currency = currency`; для продаж правило бросает
  `SalesNotConnectedError(f"Цена из ответа №{reply_id} не подтверждена")` — цена из ответа лида не ложится ни в ответ,
  ни в карточку донора. Модель цены ответу продаж не отдаётся: `priced_by_model` — только `Stage.DONORS`;
  `Parser._not_for_model` для `Stage.SALES` — `skipped, why = "ответ продаж", outcome.SALES_WAITING`. Колонка «Цена» в
  «Диалогах» (#187, `settled_price`) и цены списком (#194, `offers`) у продаж пусты: цен в их ответах нет. Экран:
  `threadState` — только подпись значка, цены не касается.
- **безопасность** — риска нет, потому что права и секреты не тронуты: `SECRET` и фикстура `inbound_secret` —
  выдуманный секрет подписи адреса ответа в тесте; маршруты `/api/replies/…` прежние; `threadState` показывает код
  состояния сервера текстом React (без `dangerouslySetInnerHTML`).
- **транзакция БД** — риска нет, потому что отказы стоят до записи: правила `stage_rules` — чистые функции без
  сессии, `confirm` и `take_lead` спрашивают их до изменения строки ответа; приём (`Inbox`) пишет ответ и последствия
  тем же путём, что у рекламодателя, без `remember_answering_address`. `await session.commit()` в диффе — только в
  тестах API.
- **интеграция** — риска нет: внешних вызовов часть не добавляет, а убирает вызов модели (модель-счётчик — 0 вызовов);
  экраны зовут прежние маршруты, vitest — через `serve()`.
- **новый модуль** — `backend/features/replies/stage_rules.py` (две чистые функции разбора этапа, состояния нет), тесты
  `test_sales_stage_replies` и `UnknownThreadState.test.tsx`.

## Предохранитель

`breakers:` части «б» против головы «а» `f1d7946`, правилом исключений `delivery_check`:

```
breakers: files=10 net_loc=449 (+491/-42), excluded=1 ['delivery/complexity-snapshot.json']
```

В пределах 25 файлов и 800 строк.

## Предупреждения delivery_check, разобранные

Дословно (клон вершины `a9d94da`, черновики части в `delivery/active/`, `--require-ci --diff-base f1d7946`), exit 0:

```
breakers: files=10 net_loc=449 (+491/-42), excluded=1 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
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

Дословно (тот же клон, `--base f1d7946`), exit 0:

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

- A3 — тестом на базе дерева; живой ответ через вебхук — «заложено».
- Непокрытые строки изменённого кода части — ветки `case _: assert_never(...)` (недостижимы по построению).
- Ответы продаж на главной в «Ждут человека» не считаются — открытый вопрос владельцу.

## Находки (общий код — не чинил)

1. **`replies/repository.py` — 494 строки из 500** (база с #201 — 493): дальше места почти нет.
2. **`api/labels.ts` — 545 строк при пределе 554** (снимок 524 + 30).

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Готово к PR после части «а», STATUS части в `delivery/active/`, подписи дайджеста утверждений и ОК соседней сессии
на общие файлы (`replies/stage_rules.py` — новый общий модуль ответов).

## Assertion digest (ревью ожиданий, не кода)

База: `f1d7946` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **27**, из них без ссылки на пример спеки:
**27**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(within(row).getByText(NEW_STATE)).toBeInTheDocument();
-	expect(screen.getByText(NEW_STATE)).toBeInTheDocument();
-	assert donor is not None
-	assert (got.kind, got.bound, got.parse_pending) == (ReplyKind.HUMAN, True, False)
-	assert (got.needs_review, got.review_reason) == (True, outcome.SALES_WAITING)
-	assert saved is not None
-	assert (saved.thread_id, saved.message_id) == (world.thread.id, world.letter.id)
-	assert world.letter.next_action_at is None  # ответил — добивок больше нет
-	assert world.thread.status is ThreadStatus.REPLIED
-	assert remembered == 0  # адрес лида — не контакт донора
-	assert extractor.calls == 0
-	assert (parsed.skipped, parsed.review_reason) == ("ответ продаж", outcome.SALES_WAITING)
-	assert (parsed.needs_review, parsed.stored_price) == (True, False)
-	assert await _donor_price(session) is None
-	assert not await ReplyRepository(session).parse_never_ran(world.reply)
-	assert (world.reply.reviewed_at, world.reply.price_white) == (None, None)
-	assert response.status_code == 409
-	assert response.json()["detail"] == (
-	assert await _donor_price(session) is None
-	assert response.status_code == 409
-	assert SALES_NOT_CONNECTED in response.json()["detail"]
-	assert world.reply.reviewed_at is None
-	assert outcome.decide(kind, PRICE, stage=Stage.SALES, names_a_sum=True) == expected
-	assert outcome.priced_by_model(stage) is priced
-	assert summarize([_sent()], replies, Stage.SALES).state is state
-	assert review_of(_reply(ReplyKind.HUMAN), Stage.SALES) == Review(
-	assert review_of(_reply(ReplyKind.AUTO_REPLY), Stage.SALES) == Review(waiting=False)
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 27

## Проверки на голове PR

Ветка перенесена на main `307c393`; голова кода `8ee317a`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 39e342cb2b21 (head) | 0 |
| ратчет сложности | Ратчет сложности: 391 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 474 passed in 97.43s (0:01:37) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=10 net_loc=449 (+491/-42)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 27` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  68 passed (68) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `5158905`):

```
breakers: files=10 net_loc=449 (+491/-42), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `5158905`):

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
