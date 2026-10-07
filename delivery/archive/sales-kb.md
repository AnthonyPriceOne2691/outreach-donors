# Active delivery status

- **slug:** sales-kb (модуль «Продажи», срез 3.1 — база знаний агента и настройки отправителя)
- **stack:** delivery@1.99 · cqg@2.45 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общих точек — соседняя сессия outreach-donors, мерж — по решению владельца
- **human_ok_spec:** yes at=2026-10-05 by=human:anthony («даю да» — план фаз с примерами среза 3.1 A1–A4 (01.10); имя отправителя в проверке готовности (A3) и модульный срез одним PR под постоянный вейвер — решения владельца 05.10)
- **waivers:** max_loc_diff=4250 max_files_touched=40 reason=модульный срез продаж, постоянный вейвер владельца 05.10; общая часть 152 строки (две миграции 114, значение журнала, реестр моделей, test_schema, cli/main.py, api/errors.py, подпись в labels.ts, .secrets.baseline); из строк среза 1828 — тесты by=human:anthony
- **new_dependency:** no (Playwright — extra `browser` из `pyproject.toml`; Pillow для `ui_contrast.py` — `uv run --with pillow`, в зависимости не внесён)
- **shared_changes:** срез трогает 17 файлов вне масок `SALES_PATHS`; каждый — полным путём:
  `backend/features/core/domain.py` — значение журнала `AuditAction.SALES_KB_CHANGED`;
  `backend/features/core/models/__init__.py` — экспорт `SalesKbEntryModel` и `SalesSettingsModel`;
  `backend/migrations/versions/c6efe4e5de7e_sales_kb_and_settings.py` — таблицы `sales_kb_entries` (тип `sales_kb_kind`) и `sales_settings`; после головы main `d7da62eb8165`;
  `backend/migrations/versions/a51e5688f79e_sales_kb_changed_audit_action.py` — `ADD VALUE 'sales_kb_changed'` отдельной ревизией;
  `tests/test_schema.py` — две новые таблицы в перечне сущностей;
  `backend/cli/main.py` — команда `sales-kb-load` в `_COMMANDS` и подсказка второго Ctrl-C;
  `backend/cli/sales.py` — `cmd_sales_kb_load`, `run_kb_load` и разбор доводов команды;
  `backend/api/errors.py` — отказы `KbError` (400), `KbKeyTakenError` (409), `UnknownKbEntryError` (404), `SenderSettingsError` (400);
  `backend/api/sales/routes.py` — включение роутера `kb` в роутер раздела;
  `backend/api/sales/kb.py` — маршруты базы знаний и отправителя под `Permission.SALES`;
  `backend/api/sales/kb_schemas.py` — схемы ответов и тел запросов этих маршрутов;
  `frontend/src/api/sales.ts` — клиент вкладок «База знаний» и «Отправитель»;
  `frontend/src/api/salesTypes.ts` — типы вкладок своим файлом раздела (`types.ts` не тронут);
  `frontend/src/api/salesLabels.ts` — подписи видов записи и полей отправителя (`labels.ts` — только строка ниже);
  `frontend/src/api/labels.ts` — подпись операции расхода `sales_verify` в `OPERATION_TITLES` (добавка к срезу по находке 1.4);
  `frontend/src/settings/UsagePage.test.tsx` — проверка этой подписи функцией `operationTitle`, без рендера страницы с провайдером `hunter`;
  `.secrets.baseline` — номер ревизии миграции `a51e5688f79e` (ложное срабатывание, как у прочих миграций).
  Не тронуты: `backend/api/app.py`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/styles/glass.css`, `scripts/ui_hover.py`, `scripts/ui_screens.py`, `docs/UI_RULES.md`, `scripts/lint/*_baseline.txt`. Согласовано: с сессией outreach-donors (список общих файлов отправлен до PR, 05.10)

## Что в срезе

- **База знаний** — таблица `sales_kb_entries`: вид (`brief`, `service`, `case`, `objection`,
  `price_policy`, `forbidden`, `cta`), язык, заголовок, текст, включена ли, теги, кто и когда
  правил; ключ «вид, язык, заголовок». Записи не удаляются — выключаются.
- **Что видит агент** — одна выборка `kb.facts` (только включённые; сужение по виду, языку,
  тегам); ею же предпросмотр экрана и **версия базы** — `kb-` и 12 знаков sha256 от
  отсортированного содержимого включённых записей (порядок, номера и пробелы не влияют;
  правка текста меняет; откат правки возвращает прежнюю) — A1, A2.
- **Отправитель** — одна строка `sales_settings` (проверка `id = 1`): имя, должность, подпись,
  сайт, Telegram, физический адрес, ссылка на созвон; текстов в окружении нет, секретов в
  таблице нет. Проверка готовности для Ф4 `sender.check_ready`: нет адреса, подписи или имени —
  отказ словами («не задан физический адрес», «не задана подпись», «не задано имя отправителя»);
  адрес доноров из окружения не подставляется — A3.
- **API** под `Permission.SALES`: `GET/POST /api/sales/kb`, `PATCH /api/sales/kb/{id}`
  (включение — правка `active`), `GET /api/sales/kb/preview`, `GET/POST /api/sales/sender`.
- **Консоль** — `outreach sales-kb-load --file база.json [--update] [--dry-run]`: файл вне копии
  репозитория, всё или ничего, повтор не задваивает, отличия без `--update` не трогаются.
- **Журнал** — `AuditAction.SALES_KB_CHANGED`: заведение, правка, выключение, загрузка (версия до
  и после), правка отправителя (прежние значения).
- **Экран** — вкладки «База знаний» (таблица, переключатель «агент видит», окно записи, «Что
  увидит агент») и «Отправитель» (форма, готовность словами сервера); обе темы, 1440 и 390 — A4.

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy, `scripts/gates.py`, ратчет сложности, гейт слоёв (import-linter и depcruise), ESLint, Prettier, `tsc`, гейты дублей и сложности TS, длина файлов, хуки pre-commit
- **behavior-oracles:** tests-present — `tests/test_sales_kb.py` (A1, A2, ключ, правила полей, журнал, цикл миграции), `tests/test_sales_sender.py` (A3, правка, журнал, схема), `tests/test_sales_kb_api.py` (A1, A2 через API, отказы 400/404/409, 403 на каждом маршруте), `tests/test_sales_kb_cli.py` (загрузка: всё или ничего, повтор, `--update`, файл в репозитории), `tests/test_sales_kb_screen.py` (коды сервера = типы и подписи экрана); vitest `frontend/src/sales/KbPane.test.tsx`, `SenderPane.test.tsx`, `SalesPage.test.tsx` через `serve()`
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`; шаг волн контура — в `check`
- **artifact_oracle:** n/a reason=сборка фронта — та же `npm run build` в джобе `web` и образе; новых точек входа нет
- **runtime_paths:** none reason=записи и настройки судятся тестами на настоящей базе дерева; живой прогон против своего сервера и своей базы — в verify-report (A1–A3 через API, консоль, контраст, наведение)
- **rule_enforcers:** n/a reason=срез не вызывает модель: база знаний — данные для агента Ф3.2; поверхность модели продукта прежняя, строка ниже — слово в слово
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** `backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), `LLM_JUDGE_MODEL` (деф. gpt-5-mini), `LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели`, DEAD)
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**

Четыре строки — `stack:`, `stack-selftest:`, `model_surface:` и `irreversible_surfaces:` —
переносятся из STATUS main слово в слово. Новых поверхностей необратимого срез не открывает:
отправки в срезе нет — `sender.check_ready` только проверяет готовность для Ф4; база знаний
и отправитель — строки своей базы, правка обратима и видна в журнале. Переподпись не нужна.

Строка `waivers:` — постоянный вейвер владельца для модульных срезов продаж (05.10): числа — `delivery_check`
после переноса на main; общая часть — 152 строки при пределе ~300; список общих файлов отправлен соседней
сессии до PR.

## Чего в срезе нет

- Черновика агента и записи версии в него (Ф3.2), выборки под ход и таблицы ходов (Ф3.2).
- Самой отправки и подстановки подписи и адреса в письмо (Ф4).
- Удаления записей и истории текста записи (кроме журнала), загрузки настроек из файла.
- Настоящих текстов компании — только данными в базе, не в репозитории.
