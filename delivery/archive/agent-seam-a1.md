# Active delivery status

- **slug:** agent-seam (общий шов агента переписки по этапам — часть «А1» из семи: реестр этапов и таблица черновиков)
- **stack:** delivery@2.00 · cqg@2.51 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общего кода агента — соседняя сессия outreach-donors; слив — по решению владельца
- **human_ok_spec:** yes at=2026-10-06 by=human:anthony (весь план Ф2–Ф5 06.10 ~23:00; согласованный интерфейс шва 05.10 ~18:20/~18:40; шов отдан модулю продаж 06.10 ~22:55: «общий код объявляй, ревью моё»)
- **new_dependency:** none
- **shared_changes:** часть «А1» — общий код агента переписки всех этапов; каждый файл полным путём:
  `backend/features/agent/stages.py` — новый: `AgentStage`, `PriceSide`, `Brief`/`Skip`/`SkipKind`, `Conversation`, `Guard`/`GuardInput`/`Verdict`/`VerdictKind`, `DraftNotice`/`OnDraft`, реестр `AGENT_STAGES`, `agent_stage`;
  `backend/features/agent/settings.py` — кортеж этапов снят (реестр в `stages.py`), докстрока `defaults`;
  `backend/features/agent/writer.py` — `Request.facts`, `Request.prompt`, `Request.model`; `load_prompt(path)`; ключ `facts` в запросе — только при фактах;
  `backend/api/agent/routes.py` — этапы и умолчания — из реестра, 404 этапу без агента через `agent_stage`;
  `backend/features/core/domain.py` — перечисление `DraftStatus`;
  `backend/features/core/models/agent.py` — модель `AgentDraftModel` (статус, текст, причина, `meta`, решение);
  `backend/features/core/models/__init__.py` — экспорт `AgentDraftModel`;
  `backend/migrations/versions/7ccbaf6d840a_agent_drafts.py` — таблица `agent_drafts` и тип `draftstatus` после `39e342cb2b21` (1.1b, голова main на `0dcbcf2`);
  `tests/test_prune.py` — две строки `REVIEWED`: `agent_drafts.reply_id → replies CASCADE`, `agent_drafts.sent_message_id → messages SET NULL`;
  `tests/test_prune_test_traces.py` — те же две ссылки в `REVIEWED` чистки следов проверки (`prune --test-traces`): черновик ответа своему ящику уходит с ним, раньше писем;
  `tests/test_schema.py` — реестр таблиц знает `agent_drafts`;
  `tests/test_agent_settings.py` — откат и подъём настроек — с ревизией черновиков, которая на них ссылается;
  `.github/workflows/ci.yml` — шаг «промпты читаются» грузит промпт каждого этапа реестра;
  `.secrets.baseline` — хэш ревизии `7ccbaf6d840a` (ложное срабатывание) и сдвиг строки `domain.py`;
  `delivery/complexity-snapshot.json` — снимок ратчета;
  тесты части — `tests/test_agent_stages.py`. согласовано: с сессией outreach-donors — «общий код объявляй, ревью моё» (06.10); ревью части на свежем main — в этом PR (07.10) (ревью общего кода — соседняя сессия outreach-donors: «общий код объявляй, ревью моё», 06.10)

## Что в части

- **Реестр этапов** `AGENT_STAGES` и части этапа `AgentStage` — согласованный интерфейс 05.10: умолчания, сторона цены, промпт и версия, модель, операция расхода, `autopilot` (выкл.), `brief`, `guard` (+ операция и таймаут), `on_draft`, `max_rewrites`. У доноров и рекламодателей части прежние.
- **Писатель** несёт факты брифа, промпт и модель этапа; без брифа запрос прежний.
- **Таблица `agent_drafts`** со статусами, `meta`, решением и ссылкой на ушедшее письмо (из заготовки `cc68357`, доведена до интерфейса).

## Размер

Против main `0dcbcf2` (#208): **файлов 14, net 474 (+512/−38)** — `delivery_check --diff-base`, строка
`breakers:` (без `delivery/` и `.github/workflows/`). Предел 25 файлов и 800 строк — в пределах, вейвер не нужен.
Голова миграций — `7ccbaf6d840a`, одна: после `39e342cb2b21` (1.1b (а), голова main на `0dcbcf2`). Если раньше
сольётся PR с другой ревизией на ту же голову, `down_revision` перецепляется на неё одной строкой до слияния.

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy strict, `scripts/gates.py`, ратчет сложности, import-linter, хуки pre-commit, подпись необратимого, `alembic heads`
- **behavior-oracles:** tests-present — `tests/test_agent_stages.py` на настоящей базе дерева, соседи — агент, переписка, ответы, письма (`tests/test_agent_*`, `test_thread_*`, `test_api_outreach`, `test_api_replies`, `test_replies_*`, `test_reply_*`, `test_api_letters*`, `test_letter_*`, `test_letters_*`) и `test_schema`, `test_prune`, `test_prune_test_traces`, `test_migrations_match_models`, `test_parse_requeue`, `test_job_outcome`, `test_llm_cap`
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

Три строки — `stack:`, `stack-selftest:` и `irreversible_surfaces:` — перенести дословно из STATUS main на момент PR.
Строка `model_surface:` **заменена**, а не перенесена: строка main (бэктики и проза) не ловила ни одного пути —
разбор `declared_surfaces` давал шесть кусков прозы и ни одного пути; новая — три пути (промпты подбора ключей,
промпт агента переписки, файл пинов `backend/config/llm.py`), пины сверены на `6aba351`, `356251d` и `58b9806`. Ни одна часть
шва эти пути не задевает — блок «Изменение поверхности модели» в verify-report не нужен. Следующие части шва
переносят её из STATUS main (после слива А1 — эту строку с двумя подпунктами). Новой поверхности необратимого часть
не открывает.

## Чего в части нет

Записи черновиков (А2), решений и маршрутов (А3), судьи и очистки (Б), задачи и кнопки (В), автопилота (Г), постановки после разбора (Д), экрана.
