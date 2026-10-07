# Active delivery status

- **slug:** sales-chain (модуль «Продажи», срез 4.6a — цепочка писем продаж в базе и экран правки; часть 1 среза 4.6)
- **stack:** delivery@1.99 · cqg@2.45 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общих точек — соседняя сессия outreach-donors, мерж — по решению владельца
- **human_ok_spec:** yes at=2026-10-05 by=human:anthony («даю да» — план фаз с примерами Spec 4.6 A1–A5 (01.10); модульный срез продаж одним PR под постоянный вейвер — решение владельца 05.10; примеры части 1 C1–C11 — к подписи при ревью)
- **waivers:** max_loc_diff=3988 max_files_touched=40 reason=модульный срез продаж, постоянный вейвер владельца 05.10; общая часть 126 строк (две миграции 107, значение журнала, реестр моделей, test_schema, cli/main.py, .secrets.baseline); из строк среза 1736 — тесты by=human:anthony
- **new_dependency:** no (Playwright — extra `browser` из `pyproject.toml`; Pillow для `ui_contrast.py` — `uv run --with pillow`, в зависимости не внесён)
- **shared_changes:** срез трогает 15 файлов вне масок `SALES_PATHS`; каждый — полным путём:
  `backend/features/core/domain.py` — значение журнала `AuditAction.SALES_CHAIN_CHANGED`;
  `backend/features/core/models/__init__.py` — экспорт `SalesChainTemplateModel`;
  `backend/migrations/versions/723e3ddab31f_sales_chain_templates.py` — таблица `sales_chain_templates`; после головы main `ad4a79bc6000`;
  `backend/migrations/versions/cbc5aadf4fc2_sales_chain_changed_audit_action.py` — `ADD VALUE 'sales_chain_changed'` отдельной ревизией;
  `tests/test_schema.py` — новая таблица в перечне сущностей;
  `backend/cli/main.py` — команды продаж берёт перечнем из `backend/cli/sales_commands.py` (`**SALES_COMMANDS`,
  `**SALES_KEPT`, `add_sales_parsers`), сам ни одной не называет;
  `backend/cli/sales_commands.py` — новый перечень команд консоли продаж: команды, подписи прерывания, разбор доводов
  (`main.py` у предела длины 500 строк); согласовано с соседней сессией;
  `backend/cli/sales.py` — `cmd_sales_chain_load`, `run_chain_load` и разбор доводов команды;
  `backend/api/sales/routes.py` — включение роутера `chain` в роутер раздела;
  `backend/api/sales/chain.py` — маршруты цепочки под `Permission.SALES`;
  `backend/api/sales/chain_schemas.py` — схемы ответов и тел запросов этих маршрутов;
  `frontend/src/api/sales.ts` — клиент вкладки «Цепочка писем»;
  `frontend/src/api/salesTypes.ts` — типы вкладки своим файлом раздела (`types.ts` не тронут);
  `frontend/src/api/salesLabels.ts` — подписи шагов, языков, видов зон и подстановок (`labels.ts` не тронут);
  `.secrets.baseline` — номер ревизии миграции `723e3ddab31f` (ложное срабатывание, как у прочих миграций).
  Не тронуты: `backend/api/errors.py` (`ChainError` — наследник `TemplateError`, 400 уже сопоставлен), `backend/api/app.py`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/api/labels.ts`, `frontend/src/theme.ts`, `frontend/src/styles/glass.css`, `scripts/ui_contrast.py`, `scripts/ui_hover.py`, `scripts/ui_screens.py`, `docs/UI_RULES.md`, `scripts/lint/*_baseline.txt`. В модулях 1.1a и 3.1 (маски продаж): `tests/test_sales_model.py` — цикл миграции лида и гипотезы откатывает сначала зависимую миграцию цепочки; `backend/features/sales/kb_load.py` — на общий `outside.py`; `frontend/src/sales/KbEntryModal.tsx` и `SenderPane.tsx` — на общую плашку `SaveRefusal`; `backend/features/sales/verifier.py` (1.4) — отказ сети без текста исключения у несобранного запроса (общий `reason_of`, `from None`), тест в `tests/test_sales_verifier.py`. Согласовано: с сессией outreach-donors (список общих файлов отправлен до PR, 05.10)
  После слияния среза — PR «общее: проверки проекта» (вне среза): `.github/workflows/ci.yml`, `.github/workflows/pr-text.yml`, `scripts/contour_waves.py`, `scripts/gates.py`, `scripts/public_repo.py`, `scripts/hooks/pre-push`, `tests/test_contour_waves.py`, `tests/test_gates.py` — волны читают `model_surface` разбором фазового гейта, гейт public-repo ловит имя закрытого документа целым словом и проверяет сообщения коммитов, заголовок и тело PR; согласовано с сессиями outreach-donors и контура (07.10), ревью у них.

## Что в срезе

- **Шаблоны** — таблица `sales_chain_templates`: набор (гипотеза или общий), шаг (1 — первое письмо,
  2 и 3 — добивки), язык (`ru`, `en`), тема (только у первого письма — и проверкой базы), тело в формате
  зон доноров, включён ли, кто и когда правил; ключ «набор, шаг, язык» (`NULLS NOT DISTINCT`). Шаблоны не
  удаляются — выключаются. Текстов в коде, миграциях и тестах нет.
- **Правила записи — одни для экрана, консоли и предпросмотра**: тема первого письма обязательна и без
  «Re:»/«Fwd:»/«Отв:»; у добивок нет темы и зон `rewrite`; метрик Ahrefs нет (`guards.assert_no_metrics`);
  подстановки — только `{{name}}`, `{{company}}`, `{{site}}`; подписи и физического адреса нет ни зоной, ни
  подстановкой, ни текстом из «Отправителя»; у первого письма коридор отличия достижим. Отказ — словами —
  C1–C4.
- **Набор гипотезы** — своя цепочка на языке целиком, если есть хоть один включённый свой шаг, иначе
  общая целиком; шаги наборов не смешиваются — C6.
- **Версия цепочки** — `chain-` и 12 знаков sha256 от включённых шагов цепочки языка; откат правки
  возвращает версию — C5. **Готовность для 4.6b** — `Chain.check_ready`: неполная цепочка — отказ словами — C7.
- **API** под `Permission.SALES`: `GET /api/sales/chain[?hypothesis=N]`, `POST /api/sales/chain`,
  `POST /api/sales/chain/preview` (без записи) — C9.
- **Консоль** — `outreach sales-chain-load --file цепочка.json [--hypothesis "…"] [--update] [--dry-run]`:
  файл вне копии репозитория, всё или ничего, повтор не задваивает — C8.
- **Журнал** — `AuditAction.SALES_CHAIN_CHANGED`: запись шага (прежние значения, версия до и после),
  загрузка одной записью (версии по языкам); без изменений — без журнала.
- **Экран** — вкладка «Цепочка писем»: набор, карточки языков, шаги, окно шага, «Показать письмо»;
  обе темы, 1440 и 390 — C11.

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy, `scripts/gates.py`, ратчет сложности, гейт слоёв (import-linter и depcruise), ESLint, Prettier, `tsc`, гейты дублей и сложности TS, длина файлов, хуки pre-commit
- **behavior-oracles:** tests-present — `tests/test_sales_chain.py` (правила записи, набор гипотезы, версия, журнал, ключ и проверки базы, цикл миграции), `tests/test_sales_chain_api.py` (набор, запись, предпросмотр, отказы словами, 403 на каждом маршруте), `tests/test_sales_chain_cli.py` (загрузка: всё или ничего, повтор, `--update`, `--dry-run`, набор гипотезы, файл в репозитории, Ctrl-C), `tests/test_sales_chain_screen.py` (шаги, языки, зоны и подстановки сервера = экрана); vitest `frontend/src/sales/ChainPane.test.tsx` через `serve()`
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`; шаг волн контура — в `check`
- **artifact_oracle:** n/a reason=сборка фронта — та же `npm run build` в джобе `web` и образе; новых точек входа нет
- **runtime_paths:** none reason=шаблоны, правила и журнал судятся тестами на настоящей базе дерева; живой прогон против своего сервера и своей базы — в verify-report (консоль, API, 403, журнал, контраст, рамка, наведение, клавиатура)
- **rule_enforcers:** n/a reason=срез не вызывает модель: шаблоны — данные для сборки 4.6b, зоны `rewrite` переписывает прежний код доноров; поверхность модели продукта прежняя, строка ниже — слово в слово
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** `backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), `LLM_JUDGE_MODEL` (деф. gpt-5-mini), `LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели`, DEAD)
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**

Четыре строки — `stack:`, `stack-selftest:`, `model_surface:` и `irreversible_surfaces:` — переносятся из
STATUS main слово в слово. Новых поверхностей необратимого срез не открывает: отправки в срезе нет —
шаблоны хранятся и правятся, `check_ready` только отвечает сборке 4.6b; правка шаблона обратима и видна в
журнале. Поверхность «письма продаж незваным адресатам» откроет часть 2 (4.6b) — переподпись тогда.

Строка `waivers:` — постоянный вейвер владельца для модульных срезов продаж (05.10): числа — `delivery_check`
после переноса на main; общая часть — 126 строк при пределе ~300; список общих файлов отправлен соседней
сессии до PR.

## Чего в срезе нет

- Сборки очереди продаж и отправки, веток продаж в `recipients`/`template`/`sending`, ключа с контактом,
  адреса получателя из лида, обязательного адреса по направлению — часть 2 (4.6b).
- Письма «вас посоветовал коллега» для лидов `referral` — открытый вопрос (вариант первого письма).
- Удаления шаблонов и истории текста (кроме журнала).
- Настоящих текстов писем — только данными в базе, не в репозитории.
