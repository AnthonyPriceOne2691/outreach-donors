# Active delivery status

- **slug:** agent-seam (общий шов агента переписки по этапам — части «Б», «В», «Г» и «Д» одним PR: судья с петлёй правки и очистка переписки; черновик задачей и по кнопке и свой дневной потолок черновиков; автопилот — механизм и флаг, ни на одном этапе не включён; постановка черновика после разбора)
- **stack:** delivery@2.00 · cqg@2.55 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общего кода агента — соседняя сессия outreach-donors; слив — по решению владельца
- **human_ok_spec:** yes at=2026-10-06 by=human:anthony (весь план Ф2–Ф5 06.10 ~23:00; согласованный интерфейс шва 05.10 ~18:20/~18:40; шов отдан модулю продаж 06.10 ~22:55: «общий код объявляй, ревью моё»)
- **waivers:** max_loc_diff=1889 max_files_touched=34 reason=объединённый PR шва агента (части Б, В, Г, Д) — решение владельца 07.10: мелкие PR объединяем, чтобы не гонять CI на каждый; ревью общего кода — соседняя сессия by=human:anthony
- **new_dependency:** none
- **shared_changes:** части «Б»–«Д» — общий код агента переписки всех этапов; каждый файл полным путём один раз, через «;» — что делает каждая часть:
  `backend/features/agent/guarding.py` — Б, новый: петля правки до `max_rewrites`, судья с таймаутом, отказ закрыт, расход судьи; В: `drafts_cap()` — оба потолка перед каждым вызовом писателя;
  `backend/features/agent/cleaning.py` — Б, новый: очистка переписки одной функцией (NFKC, невидимые, разметка ролей модели, цитата и подпись);
  `backend/features/agent/drafting.py` — Б: черновик через петлю (`guarding.compose`), переписка через очистку, `meta["attempts"]`, `meta["cleaned"]`; В: `wants_draft` — ставить ли задачу;
  `backend/features/agent/stages.py` — Б: `Conversation.cleaned`, в докстроке — ключ `rewrite` запроса; В: `agent_operations()` — операции агента всех этапов реестра (сейчас `agent_draft`);
  `backend/features/agent/writer.py` — Б: `Request.corrections`, `Request.previous`; ключ `rewrite` в запросе — только при правке;
  `backend/features/agent/drafts.py` — В: `drafts_of` (черновики переписки), `agent_writes`, `Decider.of(user)` — кто решает, из пользователя;
  `backend/features/agent/autopilot.py` — Г, новый (из заготовки `110d89c`): три ключа, границы по `PriceSide`, отправка путём решения человека, отказ — человеку;
  `backend/features/agent/settings.py` — Г: режимы `drafts`/`autopilot`, `max_turns`, `AutopilotOffError`;
  `backend/features/core/models/agent.py` — Г: `AgentSettingsModel.mode`, `AgentSettingsModel.max_turns`;
  `backend/features/core/usage.py` — В: `OwnCap`, `ensure_llm_within_cap(own=)`, `llm_tokens_spent(operations=)` — свой потолок тем же днём и журналом, без второго подсчёта;
  `backend/features/ops/job_outcome.py` — Д: имя задачи черновика в `KINDS` — строкой пути функции;
  `backend/migrations/versions/75242c2ed7ba_agent_autopilot.py` — Г: две колонки настроек после `3924977db911`;
  `backend/workers/agent_jobs.py` — В, новый (из заготовки `cc68357`): задача `draft_answer`, `queue_draft`, имя и номер задачи, итог потолка и ключа; Г: автопилот после записи — только готовому черновику и где разрешён, уведомление — если письмо не ушло; Д: `after_parse` — агент на этапе пишет — поставить задачу;
  `backend/workers/jobs.py` — Д, путь соседней сессии, поверх #209: после разбора ответа (`_parse_reply`) — импорт и вызов `agent_jobs.after_parse` (+2; файл у предела 500 строк);
  `backend/config/llm.py` — В: `AGENT_DAILY_TOKEN_CAP` (свой дневной потолок черновиков; не задан — `AGENT_CAP_SHARE` 30 % общего; 0 — своего нет; пустое значение в `.env` — «не задан»);
  `backend/config/outreach.py` — Г: выключатель сервера `OUTREACH_AGENT_AUTOPILOT` (по умолчанию выкл.);
  `.env.example` — Г: `OUTREACH_AGENT_AUTOPILOT=0`; В: `AGENT_DAILY_TOKEN_CAP=` рядом с `LLM_DAILY_TOKEN_CAP`;
  `backend/api/agent/routes.py` — В: `Decider.of(author)` в маршрутах «отправить» и «отклонить»; Г: включить автопилот — только где этап и сервер разрешают (409), версию в автопилоте, в том числе правку без режима поверх него, сохраняет только право send (403, по итоговому режиму), `autopilot_allowed` этапа;
  `backend/api/agent/schemas.py` — Г: `mode`, `max_turns` в теле настроек — `| None = None`, не присланное (нет в `model_fields_set`) — из текущей версии этапа, явный `null` — 422; `AgentStageView.autopilot_allowed`;
  `backend/api/errors.py` — В: `UnknownDraftReplyError` → 404, `DraftUnavailableError` → 503, `DraftRefusedError` → 409; Г: `AutopilotOffError` → 409;
  `backend/api/threads/routes.py` — В: черновики и `agent_writes` в переписке; `POST /api/threads/{id}/replies/{reply}/draft` (`?force=true`); сведение с #206: `one_thread` отдаёт и ящик переписки (`mail`), и черновики; ответ из переписки — `Decider.of`;
  `backend/api/threads/schemas.py` — В: `ThreadView.drafts`, `ThreadView.agent_writes`; `ThreadView.of(detail, files, mail, *, drafts, agent_writes)` — `mail` третьим позиционным, как у #206;
  `frontend/src/api/agent.ts` — Г: `mode?`, `max_turns?` в `AgentSettingsBody` (в ответе есть всегда, в теле — по желанию);
  `frontend/src/agent/agentDraft.ts` — Г: режим и предел — в `draftOf`, `bodyOf`, `sameDraft`: экран отдаёт их обратно как пришли;
  `tests/test_agent_drafting.py` — Г: подмена реестра в тестах — и в модуле реестра;
  `tests/test_api_outreach.py` — В: таблица прав маршрутов переписки: «написать заново» — право send;
  `.secrets.baseline` — Г: хэш ревизии `75242c2ed7ba` (ложное срабатывание);
  `delivery/complexity-snapshot.json` — снимок ратчета;
  тесты частей — Б: `tests/test_agent_guarding.py`, `tests/test_agent_cleaning.py`; В: `tests/test_agent_thread.py`, `tests/test_agent_cap.py`; Г: `tests/test_agent_autopilot.py`, `frontend/src/agent/agentDraft.test.ts`; Д: `tests/test_agent_after_parse.py`. согласовано: с сессией outreach-donors — «общий код объявляй, ревью моё» (06.10); объединённый PR частей Б–Д с потолком черновиков — ревью в этом PR (07.10) (ревью общего кода — соседняя сессия outreach-donors: «общий код объявляй, ревью моё», 06.10)

## Что в PR

- **Б — судья этапа** (`Guard` → `Verdict`): `allow` — готов, `block` — причины словами писателю и правка, до `max_rewrites`; не сошлось — `escalated` с историей попыток; `escalate` — сразу. **Отказ закрыт**: исключение, таймаут, `block` без причины — человеку, никогда не «готов». **Очистка переписки** одной общей функцией; что убрано — брифу и в `meta`.
- **В — задача черновика** своим модулем (имя и номер — в нём, без `shared/queue.py`); **«написать заново»** под правом send, отказ словами, «всё же написать» поверх пропуска брифа; **переписка** отдаёт черновики и пишет ли агент — рядом с ящиком переписки и следующей добивкой (#206). **Свой дневной потолок черновиков** (`AGENT_DAILY_TOKEN_CAP`, не задан — 30 % общего): выбран — отказ словами, черновик не пишется, модель не зовётся; разбор ответов доноров и прочие операции модели не встают.
- **Г — автопилот**: механизм из заготовки `110d89c`, три ключа выключены (флаг этапа, выключатель сервера, режим), границы механические, отправка путём решения человека; сохранение настроек без режима и предела их не сбрасывает, право send — по итоговому режиму, 409 — только при включении.
- **Д — постановка черновика после разбора** — только где агент на этапе пишет; экран задач называет задачу черновика словами.

## Размер

Против А3: **файлов 34, net 1889 (+1940/−51)** — четыре части одним PR (Б 7 / 460, В 15 / 571, Г 16 / 791, Д 4 / 67) —
`delivery_check --diff-base`, строка `breakers:` (без `delivery/` и `.github/workflows/`). Предел 25 файлов и 800 строк
превышен: части объединены одним PR по слову владельца, вейвер на размер — за координатором. Голова миграций —
75242c2ed7ba (одна голова).

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy strict, `scripts/gates.py`, ратчет сложности, import-linter, хуки pre-commit, подпись необратимого, `alembic heads`
- **behavior-oracles:** tests-present — `tests/test_agent_guarding.py`, `tests/test_agent_cleaning.py`, `tests/test_agent_thread.py`, `tests/test_agent_cap.py`, `tests/test_agent_autopilot.py`, `tests/test_agent_after_parse.py` на настоящей базе дерева, vitest `frontend/src/agent/agentDraft.test.ts`; соседи — агент, переписка, ответы, письма (`tests/test_agent_*`, `test_thread_*`, `test_api_outreach`, `test_api_replies`, `test_replies_*`, `test_reply_*`, `test_api_letters*`, `test_letter_*`, `test_letters_*`) и `test_schema`, `test_prune`, `test_prune_test_traces`, `test_migrations_match_models`, `test_parse_requeue`, `test_job_outcome`, `test_llm_cap`
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`
- **artifact_oracle:** n/a reason=сборка не меняется: модули в пакете `backend`, промпты — та же package-data `backend.features.agent`, ревизии — та же `alembic upgrade head` сервиса `migrate`
- **runtime_paths:** none reason=черновики, решения, судья и автопилот судятся тестами на настоящей базе дерева с подставной моделью (`httpx.MockTransport`/писатель-подмена) и `NullTransport`; живой модели и живого письма нет — «заложено» в verify-report
- **rule_enforcers:** `backend/features/agent/writer.py` (`checked`: адреса, метрики Ahrefs, длина, флаг сомнения) и `backend/features/agent/guarding.py` (судья этапа с петлёй правки, отказ закрыт); у доноров и рекламодателей судьи нет — как до шва
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** backend/features/keywords/prompts/, backend/features/agent/prompts/, backend/features/donors/prompts/, backend/features/letters/prompts/, backend/features/replies/prompts/, backend/config/llm.py <!-- промпты подбора ключей (guides · news · reviews · topics), агента переписки, судьи доноров, переписывания письма и разбора ответа; пины в llm.py: LLM_KEYGEN_MODEL (gpt-5), LLM_JUDGE_MODEL (gpt-5-mini), LLM_LETTERS_MODEL (gpt-5), LLM_AGENT_MODEL (gpt-5); вызовы модели живут в продукте, а не в срезе -->
  - ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (поверхность модели, DEAD).
  - Формат строки — пути от корня через запятую, без бэктиков, скобок и прозы, пояснения — в комментарии: так её видит проверка поверхности модели (delivery@2.00). Прежняя строка не совпадала ни с одним файлом дерева.
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**

Четыре строки — `stack:`, `stack-selftest:`, `model_surface:` и `irreversible_surfaces:` — перенести дословно из
STATUS main на момент PR. Строка `model_surface:` в main — пути через запятую (с 1.1b), переносится дословно. PR
задевает её элемент `backend/config/llm.py` (настройка потолка, не пин модели) — в verify-report блок «Изменение
поверхности модели». Новой поверхности необратимого PR не открывает: автопилот (Г) влит выключенным тремя ключами;
до включения на любом этапе — дописать поверхность в `irreversible_surfaces:` и переподписать владельцем.

## Чего в PR нет

- Б: содержимого судьи продаж (детерминированные проверки, промпт судьи — 3.2a), каталога сигнатур инъекций, живых прогонов судьи (3.4).
- В: экрана черновика (5.1).
- Г: включения автопилота на каком-либо этапе, экрана переключателя, подписи новой поверхности необратимого.
- Д: выноса `_settled` в общий модуль (заготовка) — после окна соседней сессии, если нужен.
