# Active delivery status

- **slug:** sales-cleaning (модуль «Продажи», срез 1.4, часть 2 из 2 — 1.4b+c: правила очистки, стоп-лист продаж, проверка адресов, расход и консоль `sales-clean`; часть 1 — страна кодом и часовой пояс — слита отдельным PR)
- **stack:** delivery@1.99 · cqg@2.45 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общего кода — соседняя сессия outreach-donors, мерж — по решению владельца
- **human_ok_spec:** yes at=2026-10-04 by=human:anthony («даю да» — план фаз с примерами среза; решения владельца 03.10 и 04.10: разрез 1.4 на два PR под вейвер, `unknown`/`accept_all` → `ready`, код выхода 7, переподпись необратимого не нужна)
- **new_dependency:** no
- **waivers:** max_loc_diff=2706 reason=часть 2 среза 1.4 одним PR по решению владельца 04.10 — 1.4b без 1.4c роняет живой проверяльщик после оплаченной проверки (UnknownOperationError), а 1.4b сам больше 800 строк; код перенесён из ветки среза без правки, 24 мутанта убиты, покрытие изменённых файлов 91–100 % by=human:anthony
- **shared_changes:** общий код части — согласовано с сессией outreach-donors 04.10 (список передан до PR и принят):
  `backend/features/core/domain.py` — значение `UsageProvider.HUNTER = "hunter"` (общее для обоих модулей: один счёт Hunter; соседи после слияния добавят свою операцию под то же значение);
  `backend/features/core/usage.py` — операция `sales_verify` в `OPERATION_PROVIDERS` (наша);
  `backend/features/core/models/__init__.py` — `SalesStoplistModel` в реестре моделей (реестр читает Alembic, `test_models_exported` считает экспорт по числу таблиц);
  `backend/migrations/versions/95ee6522e0de_sales_cleaning_and_stoplist.py` — только свои колонки `sales_leads` (`rejection_reason`, `cleaning_note`, `verification_*`, `verified_at`), индекс и таблица `sales_stoplist`; после `1f7b0ee634c2`;
  `backend/migrations/versions/2d9877260a6d_usage_provider_hunter.py` — `ALTER TYPE usageprovider ADD VALUE IF NOT EXISTS 'hunter'` отдельной ревизией после `95ee6522e0de`; голова одна;
  `backend/cli/main.py` — две строки `_COMMANDS` (`sales-stoplist-add`, `sales-clean`) и две строки `_KEPT_ON_INTERRUPT` для команд продаж (договорённость при ревью соседнего #152);
  `backend/cli/sales.py` — команды продаж живут в `cli/`, как `sales-import`;
  `backend/config/sales.py`, `.env.example` — `SALES_VERIFIER_PROVIDER` (конфиг модуля, как `SALES_ENABLED`);
  `tests/test_schema.py` — `sales_stoplist` в `EXPECTED_TABLES`;
  `delivery/complexity-snapshot.json` — снимок ратчета (новые файлы и рост соседних).
  `scripts/lint/jscpd_baseline.txt` — базовая линия DRY-гейта 55 → 56: шаблон колонок таблицы в миграции `95ee6522e0de` совпадает с миграциями `a4d7f1c92b63` и `b7e2c4a81f95`, миграции код не делят.
  Общие `contacts/mx.mail_route`, `contacts/quality.rejection_reason`, `contacts/provider` (исключения и разбор отказов), `SuppressionModel`, `ThreadModel` только вызываются и читаются; в `contacts` не пишем.

Часть 1 этого среза (страна и пояс) слита; её STATUS — в истории `delivery/active/` на момент
слияния. Прежний срез `sales-import` — в `delivery/archive/sales-import.md`.

## Что в срезе

Срез 1.4 сливается двумя PR — решение владельца 04.10: 1.4a страна и пояс (слита) →
1.4b+c (этот) под вейвер владельца.

- `backend/features/sales/cleaning.py` — проход очистки партиями по 200 лидов `new`: правила
  без сети (дубль → стоп-лист продаж → общие отписки → чужой диалог → годность), DNS по домену
  самого адреса (`mail_route`, параллельно по уникальным доменам), платный проверяльщик для
  дошедших; коммит каждой партии, одна строка расхода `sales_verify` на партию; сводка.
- `backend/features/sales/stoplist.py` + таблица `sales_stoplist` — домен или адрес, файл
  с заголовком и повторами, запись журнала; команда `outreach sales-stoplist-add`.
- `backend/features/sales/verifier.py` — `Protocol EmailVerifier`: `fixture` (без сети и денег,
  источник виден в статусе) и `live` — Hunter Email Verifier тем же ключом; отказы сервиса
  с классом и словами, квота и закрытая учётка — остановка платной части.
- `backend/features/sales/models.py` — статусы `new / ready / rejected`, `RejectionReason`
  кодом и слова `cleaning_note`, `verification_status` / `verification_score` / `verified_at`.
- `backend/cli/sales.py`, `backend/cli/main.py` — команды `sales-clean [--hypothesis]` (код
  выхода 7 при остановке платной части) и `sales-stoplist-add`; подсказки второго Ctrl-C.
- `backend/config/sales.py`, `.env.example` — `SALES_VERIFIER_PROVIDER` (`fixture` по умолчанию).
- Две миграции: `95ee6522e0de` (свои колонки и таблица), `2d9877260a6d` (`ADD VALUE 'hunter'`).
- Нужно 1.5 (экран фильтрует по статусу и причине) и Ф4 (отправка только `ready`, статусы
  `fixture:*` не слать, порог по `unknown`/`accept_all`).

## Размер и разрез

| Часть | Что | Файлов | Строк |
|---|---|---|---|
| 1.4a — слита | таблица стран, пояс по стране и из колонки; тесты A1–A4 | 6 | 622 |
| **1.4b+c — эта поставка** | дубли, стоп-лист, отписки, годность, MX, проверяльщик fixture/live, расход, `sales-clean`; тесты | 18 | 2 706 (вейвер владельца) |

Разрез на 1.4b и 1.4c отклонён владельцем: 1.4b без 1.4c роняет живой проверяльщик после
оплаченной проверки (`UnknownOperationError`), а 1.4b сам больше 800 строк.

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy, `scripts/gates.py`, ратчет сложности, гейт слоёв, хуки pre-commit, покрытие изменённых файлов ≥ 70%
- **behavior-oracles:** tests-present — `tests/test_sales_cleaning.py` (A1–A3, A8, A9, порядок правил, партии, A4 по заглушке DNS, A5–A6 на уровне прохода, Hunter за `MockTransport`), `tests/test_sales_stoplist.py` (файл, запись, журнал, CHECK, консоль, цикл миграции `95ee6522e0de`), `tests/test_sales_verifier.py` (вердикты, 15 отказов с классом и словами, `fixture`, фабрика, A7, расход, миграция `usageprovider` в процессе), `tests/test_sales_clean_cli.py` (сводка, коды 0/5/7, A7 без касания лидов, второй Ctrl-C), `tests/test_schema.py` — все на настоящей базе дерева
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`; шаг волн контура — в `check`
- **artifact_oracle:** n/a reason=сборка не меняется: новые `.py` в пакетах `backend` берёт поиск пакетов; миграции сервис `migrate` исполняет той же `alembic upgrade head`, что и сьют (цикл откат–подъём — в тестах); `.env.example` — образец
- **runtime_paths:** none reason=DNS в тестах — заглушка `mail_route` с задержками и молчащими доменами, Hunter — `httpx.MockTransport`; живой DNS и живой Hunter — «заложено»: `live` включает человек переменной `SALES_VERIFIER_PROVIDER`, по умолчанию `fixture` без сети и денег
- **rule_enforcers:** n/a reason=срез не трогает модель: в `sales/` нет ни промптов, ни вызовов модели; поверхность модели продукта прежняя, строка ниже — слово в слово
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** `backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), `LLM_JUDGE_MODEL` (деф. gpt-5-mini), `LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели`, DEAD)
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**

Четыре строки — `stack:`, `stack-selftest:`, `model_surface:` и `irreversible_surfaces:` —
перенесены из STATUS части 1 слово в слово. Последняя подписана ключом владельца и сверяется
в CI с `delivery/active/irreversible.sig`. Новой поверхности необратимого часть не открывает:
трата платного проверяльщика при `SALES_VERIFIER_PROVIDER=live` покрыта формулировкой «трата
юнитов Ahrefs и платных провайдеров» (решение владельца 04.10, переподпись не нужна); по
умолчанию `fixture` — денег не тратится.

## Чего в срезе нет

- Экрана лидов и фильтров по статусу и причине — срез 1.5.
- `Stage.SALES` и переезда стоп-листа в общие `suppressions` — срез 1.1b.
- Порога по `unknown`/`accept_all` и окна отправки — Ф4.
- Живого замера DNS и Hunter на настоящей базе — «заложено».
