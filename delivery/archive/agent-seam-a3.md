# Active delivery status

- **slug:** agent-seam (общий шов агента переписки по этапам — часть «А3» из семи: решения по черновику — отправить как есть, с правкой, отклонить с причиной)
- **stack:** delivery@2.00 · cqg@2.55 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общего кода агента — соседняя сессия outreach-donors; слив — по решению владельца
- **human_ok_spec:** yes at=2026-10-06 by=human:anthony (весь план Ф2–Ф5 06.10 ~23:00; согласованный интерфейс шва 05.10 ~18:20/~18:40; шов отдан модулю продаж 06.10 ~22:55: «общий код объявляй, ревью моё»)
- **new_dependency:** none
- **shared_changes:** часть «А3» — общий код агента переписки всех этапов; каждый файл полным путём:
  `backend/features/agent/drafts.py` — новый: список и черновик целиком, `send_draft` (как есть / с правкой, путём `answer_reply`), `settle_sent`, `reject_draft`, журнал решения;
  `backend/api/agent/routes.py` — `GET /api/agent/drafts`, `GET /api/agent/drafts/{id}`, `POST …/send`, `POST …/reject`;
  `backend/api/agent/schemas.py` — `DraftCard`, `DraftDetail`, `SendDraftBody`, `RejectDraftBody` (причина обязательна);
  `backend/api/errors.py` — `UnknownDraftError` → 404, `DraftDecisionError` → 409;
  `backend/api/threads/routes.py` — ответ из переписки закрывает черновик (`settle_sent`) и коммитит — в точке сохранения после ушедшего письма: сбой закрытия пишется в журнал, черновик ждёт человека, ответ — 200 (ревью соседней сессии); `logger`;
  `tests/test_agent_decisions.py` — и тест «закрытие черновика упало — ушедший ответ не становится «не отправили»»;
  `backend/features/core/domain.py` — значение журнала `AuditAction.AGENT_DRAFT_DECIDED` (после `SALES_CHAIN_CHANGED` из #180, в порядке ревизий);
  `backend/migrations/versions/3924977db911_agent_draft_decided_audit_action.py` — `ADD VALUE IF NOT EXISTS 'agent_draft_decided'` отдельной ревизией;
  `delivery/complexity-snapshot.json` — снимок ратчета;
  тесты части — `tests/test_agent_decisions.py`. согласовано: с сессией outreach-donors — «общий код объявляй, ревью моё» (06.10); ревью части и правка маршрута ответа — в этом PR (07.10) (ревью общего кода — соседняя сессия outreach-donors: «общий код объявляй, ревью моё», 06.10)
  После слияния части — PR «общее: мост продаж — сбой своих изменений почты не выдаётся за ошибку модуля» (вне части): `backend/features/core/stages.py` — `_asked` сбрасывает несохранённое вызывающего (`session.flush()`) до точки сохранения модуля, докстринги `_asked`, `sales_connected` и модуля; `tests/test_sales_stage_bridge.py` — тест «дубль ключа письма у вызывающего → исходный `IntegrityError`, модуль не спрошен» на всех четырёх вопросах моста; `delivery/complexity-snapshot.json`; строка `stack:` — с CONSTITUTION после #215 (cqg@2.55). Согласовано с сессией outreach-donors (07.10, находка агента 4.3/4.5), ревью у неё.

## Что в части

- **Решения общие для всех этапов, проверяет сервер**: отклонить — только с причиной (422), второе решение — 409, «как есть» у `escalated` — 409, с правкой — уходит.
- **Отправка — путь ответа человека** (`answer_reply`): стоп-лист, решение по донору, метрики, ящик переписки; ответ из переписки мимо черновика тоже закрывает черновик.
- **Список «ждут человека»** (`GET /api/agent/drafts`, по умолчанию `escalated`, предел страницы) и черновик целиком с `meta`.

## Размер

Против А2: **файлов 8, net 614 (+620/−6)** — `delivery_check --diff-base`, строка
`breakers:` (без `delivery/` и `.github/workflows/`). Предел 25 файлов и 800 строк — в пределах, вейвер не нужен.
Голова миграций — 3924977db911 (одна голова; после 7ccbaf6d840a).

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy strict, `scripts/gates.py`, ратчет сложности, import-linter, хуки pre-commit, подпись необратимого, `alembic heads`
- **behavior-oracles:** tests-present — `tests/test_agent_decisions.py` на настоящей базе дерева, соседи — агент, переписка, ответы, письма (`tests/test_agent_*`, `test_thread_*`, `test_api_outreach`, `test_api_replies`, `test_replies_*`, `test_reply_*`, `test_api_letters*`, `test_letter_*`, `test_letters_*`) и `test_schema`, `test_prune`, `test_prune_test_traces`, `test_migrations_match_models`, `test_parse_requeue`, `test_job_outcome`, `test_llm_cap`
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
STATUS main на момент PR. Строка `model_surface:` в main — пути через запятую (с 1.1b), переносится дословно. Новой поверхности необратимого часть не открывает.

## Чего в части нет

Черновиков в карточке переписки и «написать заново» (В), экрана.
