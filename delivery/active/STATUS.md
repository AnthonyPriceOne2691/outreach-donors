# Active delivery status

- **slug:** sales-agent-content (модуль «Продажи», агент продаж одним PR — части 3.2a, 3.5, 3.4 и 3.6: ситуация письма, ход, факты и бриф, судья кодом и моделью, строка этапа продаж на шве агента по тумблеру `SALES_AGENT_ENABLED` (по умолчанию выключен); сообщение о черновике в группу продаж очередью продаж и причины отклонения строгим списком; волна В3б — режим судьи, сигнатуры инъекций, канарейка, eval судьи с порчей; прогон версии агента на накопленных ответах с воротами «не хуже прежней»)
- **stack:** delivery@2.00 · cqg@2.55 · okf@1.19 · stack-map@1.52
- **class:** L
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общего кода (шов агента: реестр этапов, решения по черновику, потолок черновиков; детектор волн) — соседняя сессия outreach-donors; живой eval судьи и прогон агента с ключом — координатор по слову владельца; слив — по решению владельца
- **human_ok_spec:** yes at=2026-10-07 by=human:anthony («да» на Spec срезов продаж 07.10; план фаз 01.10 — Spec 3.2 A1–A7, 3.3 A1–A6, 3.4 A1–A3, 3.5 A1–A4, 3.6 A1–A2; режим судьи и детектор В3б — решения координатора 07.10; калибровка судьи (суммы только из базы, сигнатуры во всех письмах, `enforce` и пороги) — решения владельца 07.10; агент продаж в реестре этапов не включён — слово владельца 07.10)
- **human_ok_plan:** yes at=2026-10-08 by=human:anthony («да на план» — агент продаж (3.2a, 3.5, 3.4, 3.6) одним PR по плану `plan.md`: перенос готовых частей на main, агент продаж за тумблером `SALES_AGENT_ENABLED` (выключен), потолок черновиков у ситуации письма, строгий список причин 5.1, сообщения о черновиках очередью `sales`, волна В3б и прогон версии на синтетике)
- **waivers:** max_loc_diff=9224 max_files_touched=73 reason=модульные срезы агента продаж 3.2a, 3.5, 3.4, 3.6 одним PR — постоянный вейвер владельца 05.10, объединение PR — решение владельца 07.10; общая часть 28 файлов +436/−26 (шов агента: brief_operation, strict_reasons, reject_kind; миграции reject_kind и журнала сообщений; пины и настройки агента продаж; детектор волн; шаг CI промптов; подпись операций на экране «Расход»; реестры чистки и схема); eval, прогон и данные модуля в scripts/ — 9 файлов, 1093 строки; из строк PR 4386 — тесты by=human:anthony
- **new_dependency:** none
- **shared_changes:** PR трогает 28 файлов вне масок `SALES_PATHS` (+436/−26); каждый — полным путём один раз, через «;» — что делает каждая часть (3.2a, 3.5, 3.4, 3.6):
  `backend/features/agent/stages.py` — 3.2a: строка `SALES_STAGE` из частей продаж (`features/sales/agent/parts.py`: промпт и пин черновика, бриф, судья, операции `sales_draft`/`sales_judge`, три правки, автопилота нет, имя и пояснение этапа для экрана), в `AGENT_STAGES` — только по тумблеру `SALES_AGENT_ENABLED` (по умолчанию выключен — реестр этапов прежний, два этапа); поле `AgentStage.brief_operation` — операция расхода брифа, который сам зовёт модель, `agent_operations()` считает и её (свой потолок черновиков); 3.5: `OTHER_REASON` («другое» плашки 5.1), поле `AgentStage.strict_reasons` (по умолчанию нет; у продаж — да), у строки продаж — свой список причин Spec 5.1 и крючок `on_draft`;
  `backend/features/agent/drafts.py` — 3.5: вид причины отклонения (`_kind`: пункт списка этапа или «другое») ложится в черновик и журнал; у этапа со строгим списком причина своими словами без «другое: …» и «другое» без слов — `DraftReasonError` (422) со списком; у доноров и рекламодателей причина по-прежнему любыми словами;
  `backend/features/agent/drafting.py` — 3.5: «написать заново» обнуляет и `reject_kind` (одна строка в `_store`);
  `backend/features/core/models/agent.py` — 3.5: колонка `agent_drafts.reject_kind` (Text, пусто — вида нет);
  `backend/features/core/models/__init__.py` — 3.5: модель журнала `SalesDraftNoticeModel` в реестре моделей (её видит Alembic);
  `backend/migrations/versions/1cbf4c6b63f0_agent_drafts_reject_kind.py` — 3.5: колонка `reject_kind`, после головы базы `a9e76c0eb5b0`;
  `backend/migrations/versions/d64e2cd71614_sales_draft_notices.py` — 3.5: таблица `sales_draft_notices` (ссылка на черновик `CASCADE`), после `1cbf4c6b63f0`; голова одна;
  `backend/api/agent/schemas.py` — 3.5: `DraftDetail.reject_kind`; докстрока `RejectDraftBody` (тело — как в базе);
  `backend/api/agent/routes.py` — 3.5: докстрока маршрута «отклонить»;
  `backend/api/errors.py` — 3.5: `DraftReasonError` → 422;
  `backend/features/core/usage.py` — 3.2a: операции расхода `sales_draft`, `sales_situation`, `sales_judge` → `UsageProvider.LLM`;
  `backend/config/llm.py` — 3.2a: пины `LLM_SALES_DRAFT_MODEL` (gpt-5), `LLM_SALES_SITUATION_MODEL` и `LLM_SALES_JUDGE_MODEL` (gpt-5-mini) — рядом с `LLM_SALES_CLASSIFY_MODEL`;
  `backend/config/sales.py` — 3.2a: тумблер `SALES_AGENT_ENABLED` (по умолчанию `false`); 3.4: режим судьи `SALES_JUDGE_MODE` (`enforce` по умолчанию, `shadow` для замера), каталоги наборов eval `SALES_JUDGE_GOLDEN_DIR`, `SALES_STYLE_GOLDEN_DIR`; 3.6: каталог набора прогона `SALES_REPLAY_DIR`;
  `.env.example` — 3.2a: три пина и `SALES_AGENT_ENABLED=false`; 3.5: тумблер выключает и сообщения о черновиках (слова); 3.4: `SALES_JUDGE_MODE`, `SALES_JUDGE_GOLDEN_DIR`, `SALES_STYLE_GOLDEN_DIR`;
  `pyproject.toml` — 3.2a: package-data `backend.features.sales.agent` = `prompts/*.md`, `moves.toml`;
  `.github/workflows/ci.yml` — 3.2a: шаг «Шаблоны писем и промпты — внутри образа» читает промпты ситуации, черновика и судьи продаж и таблицу ходов (промпт черновика — явно: без тумблера реестр его не обходит);
  `scripts/contour_waves.py` — 3.4: детектор `sales-agent-prompt` (`backend/features/sales/agent/prompts/*.md`), проверка «промпт назван в `model_surface`» — для обоих детекторов промптов тем же разбором фазового гейта (`declared_surfaces`, `runtime_touched`), одна находка двух волн — один раз;
  `tests/test_contour_waves.py` — 3.4: промпт агента без В3б и не названный — красные, названный каталогом — зелёный, обратный прогон без детектора; зеркало реестра — В3а и В3б развёрнуты;
  `tests/conftest.py` — 3.5: страховка набора — тесты не ставят сообщения о черновиках в настоящую очередь продаж (`notify.sales_queue`);
  `.secrets.baseline` — 3.5: номера строк секретов `tests/conftest.py` после страховки;
  `tests/test_agent_decisions.py` — 3.5: вид причины у доноров — пункт списка, «другое: …» и свои слова (без вида);
  `tests/test_agent_settings.py`, `tests/test_agent_stages.py` — 3.5: циклы миграций шва снимают журнал сообщений продаж первым (он ссылается на черновик);
  `tests/test_prune.py` — 3.5: решение ссылки `sales_draft_notices.draft_id → agent_drafts CASCADE` в `REVIEWED` («сообщение о черновике — с черновиком»; `runs/prune.py` не тронут);
  `tests/test_prune_test_traces.py` — 3.5: то же решение во втором реестре (чистка следов проверки);
  `tests/test_schema.py` — 3.5: таблица `sales_draft_notices` в ожидаемых;
  `frontend/src/api/labels.ts` — 3.2a: три подписи операций на экране «Расход» (`sales_situation`, `sales_draft`, `sales_judge`);
  `frontend/src/settings/UsagePage.test.tsx` — 3.2a: тест подписей рядом с тестом `sales_reply_kind`;
  `delivery/complexity-snapshot.json` — снимок ратчета.
  После слияния PR — PR «общее: сборка очереди продаж одна за раз и шов агента без агента продаж» (вне PR, по ревью соседней сессии к #223 и #224): `backend/api/sales/queue.py` — номер сборки от гипотезы; идёт или ждёт повтора — 409 словами, закончилась — след убран и новая поставлена, `unique=True` на гонку двух нажатий; `tests/test_sales_queue_api.py` — очередь с правилами rq 2.12 и четыре случая; `tests/test_agent_stages.py` — чистый интерпретатор: импорт шва грузит от агента продаж только `parts`;
  После слияния PR — PR «Продажи, ревью стыков: почта и мост» (вне PR): модуль продаж — `backend/features/sales/mail.py` (неполная цепочка набора — `NotReadyError` письму, а не стоп всей пачки), `backend/features/sales/cleaning.py` (очистка видит строку общего стоп-листа с этапом «продажи»); **тесты в общем коде без правок кода** — пробелы закреплены `xfail(strict=True)` и `it.fails` до PR «общее»: `tests/test_mail_watch_seams.py` (сторож: ошибка базы в `_of_stage`, `Feed.told` в памяти, дребезг, опрос внутри транзакции), `tests/test_senders_screen_seams.py` (карточка ящика против капа, `_able` и домен чужого направления), `frontend/src/senders/SendersPage.seams.test.tsx` (бейдж и «Отправлять могут N из M» без паузы, выдержки и чужого направления), `frontend/src/letters/SendQueue.seams.test.tsx` (отказ сервера вне окна подтверждения), `frontend/src/theme.seams.test.tsx` (заголовок жёлтой плашки 4,26–4,39 при норме 4,5); согласовано: с сессией outreach-donors — ревью этих тестов в этом PR (08.10);
  Свои (модуль): `backend/features/sales/agent/` (`calling.py`, `situation.py`, `moves.py` + `moves.toml`, `reading.py`, `facts.py`, `brief.py`, `judge_rules.py`, `judge.py`, `parts.py`, `safety.py`, `notify.py`, `replay.py`, `replay_sets.py`, `replay_drafts.py`, `replay_gate.py`, промпты `prompts/situation.md`, `prompts/reply.md`, `prompts/judge.md`), `backend/features/sales/models.py` (журнал `sales_draft_notices`), `scripts/eval_sales_judge.py`, `scripts/sales_injection_canary.py`, `scripts/sales_replay.py`, `scripts/data/sales_judge_synthetic.jsonl`, `scripts/data/sales_judge_golden.manifest.json`, `scripts/data/sales_style_golden.manifest.json`, `scripts/data/sales_injection_corpus.jsonl`, `scripts/data/sales_replay/` (манифест и выдуманный набор), `delivery/contour-waves.md` (В3б → deployed); тесты `tests/test_sales_agent_*.py`, `tests/test_sales_draft_*.py`, `tests/test_sales_judge_eval.py` (+ `tests/test_sales_judge_eval_answers.json`), `tests/test_sales_injection_canary.py`, `tests/test_sales_replay.py`. Правки шва (поля `brief_operation`, `strict_reasons`, вид причины, тумблер строки продаж) — ревью соседней сессии. согласовано: с сессией outreach-donors — порядок (после 4.6b и 5.4) и общие точки шва агента: поле AgentStage.brief_operation в agent_operations(), strict_reasons и 422 у продаж, ревизия 1cbf4c6b63f0 (agent_drafts.reject_kind), детектор волн scripts/contour_waves.py, очередь sales для сообщений о черновиках — её ревью в этом PR (08.10)

## Что в PR

- **3.2a — содержимое агента продаж:** ситуация письма строгой формой от модели («нужен ли ответ» — кодом, сбой —
  `parse_failed`), ход — таблицей данными, факты под ход из базы знаний и настроек отправителя, бриф (пропуск со
  словами, подпись персоной продаж), судья — правила кодом первыми, затем модель; строка этапа продаж на шве —
  `SALES_STAGE`, в реестре этапов только по тумблеру; ситуация — в своём потолке черновиков агента.
- **3.5 — решения и весть:** причина отклонения черновика продаж — строгим списком Spec 5.1 (плашка 5.1 шлёт пункт
  или «другое: …»), вид причины в черновике и журнале; черновик, ждущий человека, — сообщение в группу продаж ботом
  5.3 задачей очереди продаж, журнал отправки, тревога при недоставке, черновик цел.
- **3.4 — волна В3б:** режим судьи `enforce`/`shadow`, сигнатуры инъекций T1–T6 во всех письмах собеседника до
  модели, канарейка (27 атак, 15 легитимных), eval судьи на синтетике и манифестах внешних наборов, порча промптов и
  обратный прогон, калибровка v3→v4 (суммы с валютой — только из базы, строгая схема и один повтор), детектор В3б.
- **3.6 — прогон версии:** версия агента на входящих с известным исходом (набор вне репозитория по манифесту и
  живые решения из `agent_drafts`), сравнение с решением человека, ворота «не хуже прежней» — судья прогона без
  режима и с промптом версии.

## Размер

Против головы стопки (main + Ф2 + 5.3): 49 коммитов, **75 файлов, net 9321** (+9376/−55; модуль 45 файлов net 8814,
общая часть 28 файлов net 410, `delivery/` 2 файла net 97; счёт `delivery_check` без `delivery/` и `ci.yml` — files=72 net_loc=9219) — одним PR по слову владельца, вейвер на размер — за
координатором (`waivers:` пишет он). По частям (против головы предыдущей): 3.2a — модуль 21 / net 3069, общая 9 /
net 84; 3.5 — модуль 6 / 1199, общая 20 / 216; 3.4 — модуль 23 / 2269, общая 4 / 106; 3.6 — модуль 8 / 2277, общая
1 / 4. Миграции: `a9e76c0eb5b0` → `1cbf4c6b63f0` → `d64e2cd71614`, голова одна.

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат (backend, tests, scripts), mypy strict, `scripts/gates.py` (вместе с `public-repo`, `config-access`, `inline-prompt`), ратчет сложности, гейт слоёв (import-linter и depcruise), jscpd, длина файлов, хуки pre-commit, detect-secrets, `alembic heads`
- **behavior-oracles:** tests-present — `tests/test_sales_agent_situation.py`, `tests/test_sales_agent_moves.py`, `tests/test_sales_agent_facts.py`, `tests/test_sales_agent_brief.py`, `tests/test_sales_agent_judge_rules.py`, `tests/test_sales_agent_judge.py`, `tests/test_sales_agent_stage.py` (тумблер в чистом процессе, потолок черновиков, черновик путём шва), `tests/test_sales_draft_decisions.py`, `tests/test_sales_draft_notify.py` (очередь продаж), `tests/test_sales_agent_judge_mode.py`, `tests/test_sales_agent_safety.py`, `tests/test_sales_injection_canary.py`, `tests/test_sales_judge_eval.py`, `tests/test_sales_replay.py` (судья прогона в `shadow`), `tests/test_contour_waves.py`, `tests/test_agent_decisions.py`; на настоящей базе дерева, модель — `httpx.MockTransport`, Telegram — подставной Bot API; vitest `frontend/src/settings/UsagePage.test.tsx`; полный pytest — в verify-report
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`; шаг волн контура — в `check`; канарейка и ворота eval на записанных ответах — тестами в `check`
- **artifact_oracle:** tests/test_package_data.py — промпты и таблица ходов агента продаж объявлены в package-data `backend.features.sales.agent`, а шаг CI «Шаблоны писем и промпты — внутри образа» читает их из установленной копии (промпт черновика — явно, без реестра)
- **runtime_paths:** none reason=агент продаж в реестре этапов не включён (тумблер `SALES_AGENT_ENABLED` выключен): в проде он не пишет черновиков и не шлёт сообщений; модель и Telegram в тестах — подставные; живой eval и прогон с ключом — шаг координатора по слову владельца
- **rule_enforcers:** backend/features/sales/agent/judge_rules.py — реестр «правило промпта → исполнитель ниже модели»: суммы и числа — только из базы и письма собеседника (суммы с валютой — только из базы), ссылки — белым списком отправителя, призыв — ровно один, язык письма, форма, отсрочка не повторяется; `situation.py` — «нужен ли ответ» кодом; `safety.py` и `brief.held_earlier` — сигнатуры инъекций до модели; `judge.py` — «не смог проверить» — человеку в обоих режимах; `agent/drafts.py` — строгий список причин; `scripts/eval_sales_judge.py`, `scripts/sales_injection_canary.py`, `agent/replay_gate.py` — ворота; утверждения «без опоры на базу», обещания и тон — advisory, меряет eval
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** backend/features/keywords/prompts/, backend/features/agent/prompts/, backend/features/donors/prompts/, backend/features/letters/prompts/, backend/features/replies/prompts/, backend/features/sales/prompts/, backend/features/sales/agent/prompts/, backend/config/llm.py <!-- промпты подбора ключей (guides · news · reviews · topics), агента переписки, судьи доноров, переписывания письма, разбора ответа, вида ответа лида продаж (reply_kind.md, версия sales-reply-kind-v1) и агента продаж — ситуация situation.md (sales-situation-v1), черновик reply.md (sales-reply-v3), судья judge.md (sales-judge-v4); выход агента продаж — строгий JSON, у судьи — схемой у провайдера (json_schema strict) и один повтор на битый ответ; пины в llm.py: LLM_KEYGEN_MODEL (gpt-5), LLM_JUDGE_MODEL (gpt-5-mini), LLM_LETTERS_MODEL (gpt-5), LLM_AGENT_MODEL (gpt-5), LLM_SALES_CLASSIFY_MODEL (gpt-5), LLM_SALES_DRAFT_MODEL (gpt-5), LLM_SALES_SITUATION_MODEL (gpt-5-mini), LLM_SALES_JUDGE_MODEL (gpt-5-mini); сэмплинг — reasoning_effort minimal у ситуации и судьи, low у черновика; режим судьи продаж SALES_JUDGE_MODE (enforce); агент продаж в реестре этапов — по тумблеру SALES_AGENT_ENABLED (выключен); вызовы модели живут в продукте, а не в срезе -->
  - ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (поверхность модели, DEAD).
  - Формат строки — пути от корня через запятую, без бэктиков, скобок и прозы, пояснения — в комментарии: так её видит проверка поверхности модели (delivery@2.00). Прежняя строка не совпадала ни с одним файлом дерева.
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**; **запись в Kommo при живом подключении (SALES_KOMMO_PROVIDER=live) — сделки, контакты, компании и примечания с именем, почтой, компанией и последним письмом лида уходят в стороннюю CRM без человека: по ответу лида, захотевшего говорить, и повтором по расписанию, пока Kommo не ответит; записанное из сервиса не отзывается**; **сообщения телемаркетологу и в группу продаж в Telegram — ботом продаж без человека: по ответу лида, ещё раз, когда сделка заведена после повтора, и о каждом черновике агента продаж, который ждёт человека (адрес и тема письма лида); отправленное не отзывается**; **письма продаж с доменов продаж — холодный оффер незваным адресатам: первые уходят по нажатию человека (по одному или пачкой), добивки по расписанию без человека, жалоба бьёт по репутации доменов продаж и не отзывается**; **отписка словами в треде продаж — по виду ответа, названному моделью не ниже порога, без человека: адрес закрывается во всех направлениях (стоп-лист без этапа), письма в очереди и сроки добивок любого направления снимаются и сами не возвращаются**; **ответы модели живым людям — ответ, написанный агентом переписки, уходит собеседнику по нажатию человека («Подходит · отправить» или после правки); отправленное не отзывается**

Строки `stack:`, `stack-selftest:` и `irreversible_surfaces:` — перенести дословно из STATUS main на момент PR.
**`model_surface:` — строка STATUS базы плюс каталог промптов агента продаж** `backend/features/sales/agent/prompts/`
первой строкой поля (пины агента продаж — в `backend/config/llm.py`, файл уже в строке); подпункты — слово в слово;
координатор берёт её из черновика (`--keep model_surface`). PR задевает оба элемента — в verify-report блок
«Изменение поверхности модели». **Необратимое:** новой поверхности сверх общей строки продаж, которую владелец подписывает в PR Ф2, PR не
открывает: сообщения о черновиках агента продаж в группу (3.5) и «ответы модели живым людям» — уже пункты этой строки;
к тому же агент продаж выключен тумблером. Если в main на момент PR строка ещё без этих пунктов — **к переподписи
владельцем** (текст строки — у координатора); строку PR не трогает.

## Чего в PR нет

- Включения агента продаж (тумблер `SALES_AGENT_ENABLED`, автопилот) — по слову владельца, после живого eval;
  экрана агента продаж сверх общего экрана настроек (этап появится там сам, когда тумблер включён).
- Живого eval судьи и прогона версии с ключом на наборах владельца — шаг координатора; наборы — вне репозитория.
- Исполнителя отправки ответа продаж почтой (продажи к почте не подключены: «как есть» и правка упираются в 409
  «продажи к почте ещё не подключены», черновик ждёт).
