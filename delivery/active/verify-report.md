# Verify report

**Поставка:** срез `sales-kb` (модуль «Продажи», 3.1) — база знаний агента и настройки отправителя.
Одним PR под постоянный вейвер владельца для модульных срезов продаж (05.10) — числа в разделе «Предохранитель».

**Date:** 2026-10-05
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; общие точки — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=157 утверждений без примера спеки ждут подписи человека (36 привязаны к A1–A3 и уроку L5); подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR; пре-пуш гоняет полный набор
**Commit:** ветка `sales/3.1-kb-pr` на main `b07ca62`; полные прогоны агента — на вершине его ветки `06722e8` (тот же код среза), после переноса — проверки ниже

## Сборка

Ветка агента `sales/3.1-kb` (на main `b1f0f60`) перенесена на main `b07ca62` (после #171 и #173): документы — первым
коммитом (`sales-kommo` в архив, активный срез `sales-kb`), затем 11 коммитов агента в том же порядке. При переносе:

- миграция `c6efe4e5de7e` перецеплена с `8dbc46c01caf` на голову main `d7da62eb8165` (настройки агента переписки, #171):
  `down_revision` и строка `Revises:` — больше ничего; голова одна — `a51e5688f79e`; база тестов поднимается
  `alembic upgrade head` с нуля, `test_migrations_match_models` зелёный;
- конфликты T1 с #171 — только перечни, куда обе стороны дописали своё: `AuditAction` (`AGENT_SETTINGS_CHANGED` и
  `SALES_KB_CHANGED`), таблицы в `tests/test_schema.py`; снимок сложности пересчитан `complexity.py --update` на каждом
  коммите, `.secrets.baseline` пересканирован;
- сообщение коммита T5 «WIP … остановлен на ночь» заменено сутью коммита (новые коммиты, запушенная история не
  переписывалась);
- файлы среза совпадают с вершиной агента `06722e8` байт в байт — отличаются только две строки миграции.

| SHA | Суть | Файлов (без `delivery/`) | +/− | net |
|---|---|---|---|---|
| `9fe2fec` | T1: таблицы `sales_kb_entries`/`sales_settings`, версия базы, выборка `facts`, правила записи, журнал, `check_ready`; миграции; тесты A1–A3, цикл миграции | 12 | +1 434/−6 | 1 428 |
| `65dbf71` | подпись операции расхода `sales_verify` + тест функцией `operationTitle` (добавка координатора) | 2 | +7/−0 | 7 |
| `a06d92e` | T2: API под `Permission.SALES` — записи, предпросмотр агента, отправитель; отказы через `api/errors.py` | 5 | +593/−0 | 593 |
| `71f42b8` | T3: `outreach sales-kb-load` — JSON вне репозитория, всё или ничего, повтор не задваивает | 4 | +597/−3 | 594 |
| `2f07aac` | правило имени отправителя в `check_ready` (решение владельца 05.10) + тесты A3 | 3 | +42/−16 | 26 |
| `6a85286` | шапки `SalesSettingsModel` и миграции называют имя среди обязательного | 2 | +4/−3 | 1 |
| `24334f2` | T4: вкладки «База знаний» и «Отправитель», клиент, типы, подписи, сверка кодов с экраном | 15 | +1 467/−29 | 1 438 |
| `58db76b` | T5: правки экрана по снимкам и каталог замеров шести экранов (сообщение «WIP» ветки агента заменено сутью при переносе) | 5 | +147/−13 | 134 |
| `5b31856` | плашка «готова» не перечисляет поля — правило держит сервер | 2 | +16/−8 | 8 |
| `fc3a787` | каталог замеров: значения однострочных полей окна не меряются (кромка в вырезке), текст записи — длинным текстом | 1 | +23/−5 | 18 |
| `202da46` | тест консоли очистки (1.4) не настраивает журнал всерьёз — изоляция соседнего `test_cli_main.py` | 1 | +3/−0 | 3 |

## Shape oracles

pytest, pre-commit и фронт — на `4670e7d`: его дерево равно дереву головы `202da46` после переноса на main `b07ca62`
(`git rev-parse …^{tree}` совпали — перенос не менял ни байта). Миграции, ратчет, подпись, волны, `delivery_check` и дайджест —
на голове `202da46` против main `b07ca62`. Всё — по одному разу.

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | одна голова `a51e5688f79e` (после `d7da62eb8165` main) | 0 |
| pytest среза и соседних (`test_sales_*`, `test_api_sales_*`, `test_cli_main`, `test_schema`, `test_migrations_match_models`, `test_agent_settings`, `test_contour_waves`) с покрытием; база — `alembic upgrade head` с нуля | 582 passed in 45.98s | 0 |
| `SKIP_TESTS=1 STRICT=1 check_diff_coverage.sh` | ядро и API среза, обе миграции — 100 %; `cli/sales.py` — 92,7 %, `cli/main.py` — 96,6 % (по файлу целиком) | 0 |
| `pre-commit run --all-files` | 27 хуков Passed или Skipped, ни одного Failed (ruff, формат, mypy, гейты, DRY, слои, сложность, длина, секреты) | 0 |
| `check_baseline_ratchet.sh` (BASE — main) | снимки не выросли | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=40 net_loc=4250 (+4288/-38)` — вейвер владельца; ниже, дословно | 0 |
| `assert_digest.sh` | `asserts_without_example: 157` | 0 |
| `tsc --noEmit` / `eslint src/` / `prettier --check` | чисто | 0 / 0 / 0 |
| vitest `src/sales` + `src/settings` (`--maxWorkers=2`) | Tests  79 passed (79) | 0 |

## Behavior oracles

- [x] PASS — после переноса: 582 passed in 45.98s; vitest `src/sales` + `src/settings` — Tests  79 passed (79).
- [x] PASS — полный pytest с покрытием на вершине агента `06722e8`: **3771 passed** за 5:56, exit 0 (код среза тот же;
      полный набор на голове PR гоняет пре-пуш).
- [x] PASS — полный vitest `--maxWorkers=2` на вершине агента: **53 файла, 498 тестов**, exit 0.
- [x] PASS — новые тесты среза: `tests/test_sales_kb.py`, `tests/test_sales_sender.py`, `tests/test_sales_kb_api.py`,
      `tests/test_sales_kb_cli.py`, `tests/test_sales_kb_screen.py`; vitest `KbPane.test.tsx` (10), `SenderPane.test.tsx` (4).
- [x] PASS — изоляция журнала: `pytest tests/test_sales_clean_cli.py tests/test_cli_main.py` — до правки 5 failed,
      после — 102 passed; обратный порядок — 102 passed и до, и после; с `test_sales_kb_cli.py` между ними — 119 passed.

### Красный прогон до кода

Дерево main `3695a7f` (`git archive`) + тесты 3.1 поверх, своя временная база (снесена):

- pytest: 5 файлов не собрались — `ImportError: cannot import name 'kb'` / `'sender' from 'backend.features.sales'`,
  `cannot import name 'run_kb_load' from 'backend.cli.sales'`; `tests/test_schema.py` — 2 failed, 6 passed
  (`test_all_entities_registered`, `test_models_exported`). exit 2 / 1.
- vitest: 16 failed — `KbPane.test.tsx` 10/10, `SenderPane.test.tsx` 4/4, `SalesPage.test.tsx` 1/19 (сводка с вкладками),
  `UsagePage.test.tsx` 1/8 (подпись `sales_verify`). exit 1.
- Правило имени (`2f07aac`) — отдельно на прежнем `sender.py`: 5 failed, 36 passed. Плашка «готова» (`5b31856`) —
  на прежней строке: 1 failed.

### Обратные прогоны

Мутант — одна правка строки, прогон тестов правила, возврат файла с проверкой байтов; дерево после — чистое.

| Мутант | Файл | Итог | Убит тестами |
|---|---|---|---|
| M1 версия без сортировки записей | `kb.py` `version_of` | 1 failed | `test_a1_order_numbers_and_whitespace_do_not_change_the_version` |
| M2 версия без сведения пробелов | `kb.py` `version_of` | 1 failed | то же |
| M3 номер записи входит в версию | `kb.py` `version_of` | 6 failed | `test_a1_version_is_content_not_row_numbers`, A2, журнал, API |
| M4 текст не входит в версию | `kb.py` `version_of` | 3 failed | `test_a1_price_edit_changes_the_version_…`, `…each_field…[text]`, API A1 |
| M5 выборка агента без «включена» | `kb.py` `facts` | 2 failed | `test_a2_switched_off_entry_is_not_seen_by_the_agent_by_any_selection`, API A2 |
| M6 предпросмотр из всех записей | `api/sales/kb.py` `agent_preview` | 1 failed | `test_a2_switched_off_entry_stays_on_the_list_and_leaves_the_agent_preview` |
| M7 готовность без правила адреса | `sender.py` `REQUIRED` | 5 failed | A3 ×3, `test_saving_nothing_over_nothing_writes_no_row`, API |
| M8 адрес доноров засчитан продажам | `sender.py` `Sender.missing` | 1 failed | `test_a3_no_address_refuses_in_words_even_with_the_donor_address_in_the_environment` |
| M9 готовность без правила имени | `sender.py` `REQUIRED` | 5 failed | `test_a3_no_sender_name_refuses_in_words` ×2, A3 пустые, API |

9 из 9 убиты. M1 и M2 держит один тест — метаморфные отношения примером (см. «реляционный оракул» ниже).

## Живой прогон

Стенд: своя база `outreach_live_sales_j` (на голове миграций), API `:8104`, фронт `:5178` (прокси), учётка стенда;
стенд остановлен. Снято прошлым агентом (05.10 ночью, код T1–T4): миграции `downgrade 8dbc46c01caf` → `upgrade head` на
живой базе — чисто; консоль — 5 сценариев (добавлено 13; повтор — «без изменений 13»; правка без `--update` —
«отличается 1 — не тронуто», версия не сдвинулась; файл в копии репозитория — отказ, exit 2; чужой вид — отказ с номером);
API — A2 («Дорого» ушла из выборки, в списке осталась), A1 (kb-3c10… → kb-67e7… → откат kb-3c10…), A3 (оба отказа, затем
только адрес), ссылка без https — 400, двойник — 409, нет записи — 404; журнал — 6 записей, цепочка версий сходится.

Снято в этой сессии (вершина до переноса на `b1f0f60`; экран раздела с тех пор не менялся):

**Контраст** (`ui_contrast.py`, норма 4,5 текст / 3,0 крупное) — шесть экранов, обе темы, все точки «ок», exit 0:

| Экран | Точек | Свет: минимум текста / крупного | Тьма: минимум текста / крупного |
|---|---|---|---|
| `sales-kb` (1440) | 28 | 5,12 версия базы / 5,35 «Добавить запись» | 6,80 / 6,61 |
| `sales-kb-phone` (390) | 10 | 5,19 / 5,43 | 6,68 / 6,64 |
| `sales-kb-entry` (окно записи) | 14 | 5,02 текст записи / 4,81 «Отмена» | 10,40 / 7,33 |
| `sales-kb-agent` («Что увидит агент») | 12 | 5,34 / 8,74 | 9,17 / 13,98 |
| `sales-sender` (1440) | 16 | 6,34 / 5,10 «Сохранить» | 5,09 кто и когда правил / 6,52 |
| `sales-sender-phone` (390) | 10 | 6,30 / 5,14 | 7,40 / 6,26 |

**Окно записи на свету — 2,74 / 2,74 / 3,85 у прошлого агента: слепое пятно замера, не провал.** Разбор на стенде
(снимок `contrast-kb-entry-light.png` рядом с черновиком):

| Поле (свет) | Проба (рамка + 6 px) | Строка букв | Ядро букв | По цветам CSS |
|---|---|---|---|---|
| значение вида «о компании» | 2,74 | 6,41 | 15,46 | 14,53 |
| значение заголовка «Who we are» | 2,74 | 5,00 | 15,44 | 14,53 |
| текст записи (2 строки) | 3,85 | 6,21 | 15,57 | 14,66 |
| тот же текст длинный (5 строк) | 5,02 | 6,50 | 15,50 | 14,66 |
| для сравнения: поле «Имя отправителя» на панели, «Ива» | 6,36 | 6,39 | 16,44 | 16,61 |

Текст поля — `rgb(14, 32, 36)` на подложке `rgba(12, 40, 50, 0.035)` поверх плотного стекла; кромка поля на плотном
стекле светлой темы — чернила `rgb(12 40 50 / 0.42)` (`glass.css`, `.glassSolid`), у поля текста ещё кольцо фокуса.
Порог Оцу делит вырезку на «кромка + буквы» и «подложка»; у короткой строки пикселей кромки больше, чем букв, и среднее
«чернил» съезжает к фону. На панели кромка белая — она уходит в группу фона, и проба видит буквы. Тот же класс записан в
`scripts/ui_screens.py:161` («вырезка берёт кромку поля (3,27 при 6,37)»). Правка — в своём каталоге (`fc3a787`):
значения вида и заголовка не меряются с объяснением у проб, текст записи — длинным `ENTRY_TEXT`; после неё все 14 точек
окна «ок».

**Наведение** (`ui_hover.py --path`, общий `PATHS` не тронут): `/sales?tab=kb` — 39 элементов, сдвигов 0, не проверено 0,
exit 0; `/sales?tab=sender` — 25, 0, 0, exit 0.

**Рамка (A4):** документ 1440/1440 и 390/390 во всех состояниях обеих тем (список, окно записи, «что увидит агент»,
отправитель); таблица базы на 390 — 1040 в своей прокрутке 316; окна — 620 и 780 на 1440, 351 на 390; элементов за краем
окна вне прокрутки — 0.

## Product oracles

- [x] PASS — `eval-smoke.md`: A1–A4, консоль, API, сверка кодов, красный прогон, мутанты отмечены; настоящие тексты
      компании и версия в черновике — «заложено».

## Ревью рисковых мест

- **деньги** — риска нет, потому что денег в срезе нет: `PRICE_POLICY = "price_policy"` в `backend/features/sales/models.py` —
  вид записи базы знаний («что можно говорить о цене и чего нельзя»), то есть текст для агента, а не сумма; `PRICE` в
  `tests/test_sales_kb.py` — выдуманная запись «Цена аудита»; `{"kind": "pricing"}` — проверка отказа незнакомому виду;
  «не в счёт» — комментарий теста. Подпись `sales_verify` в `frontend/src/api/labels.ts` — только слово для строки расхода,
  суммы считает прежний код.
- **безопасность** — каждый маршрут `backend/api/sales/kb.py` под `_seller = Depends(needs(Permission.SALES))`, 403 словами
  проверен на каждом маршруте (`tests/test_sales_kb_api.py`). В `sales_settings` нет места секретам —
  `test_settings_table_has_no_place_for_secrets` (колонок с `key`/`secret`/`token`/`password`/`dsn` нет); чужое поле
  `{"api_token": …}` отказывается словами (`sender.cleaned`). Коммерческие тексты — только в базе: файл базы внутри копии
  репозитория `kb_load._repository_of` отказывает до чтения. `hashed_secret` в `.secrets.baseline` — номер ревизии миграции
  (ложное срабатывание, как у соседних миграций). `bearer(await sign_in(SELLER))` — помощник тестов.
- **транзакция БД** — правка записи и её строка журнала пишутся одной транзакцией: `kb.add`/`kb.change`/`sender.save` зовут
  `AccessRepository(session).record` до `await session.commit()` маршрута — правка без журнала не ляжет. Загрузка — одна
  транзакция: `kb_load.apply` → `session.commit()` в `run_kb_load` (`backend/cli/sales.py`); ошибка в любой записи — ничего
  не записано (тест «всё или ничего»). `session.begin_nested()` — только в тестах ограничений. Опасно здесь: две
  одновременные правки одной записи — побеждает последняя (журнал покажет обе); два одновременных заведения одного ключа —
  `_key_free` их не разведёт, второе упадёт на `uq_sales_kb_entries_key` ответом 500, а не 409 словами. Для десятков записей и
  одного-двух правящих — приемлемо, записано в spec gaps.
- **производительность** — `kb.facts` и `GET /api/sales/kb` отдают базу без страниц: база — десятки записей по замыслу
  (файл загрузки ограничен `MAX_BYTES` 2 МБ), гейт `unbounded-list` пройден. `version_of` — `json.dumps` отсортированного
  содержимого включённых записей: микросекунды на десятки записей. `json.loads` файла загрузки — в потоке
  (`asyncio.to_thread(kb_load.read, …)`), цикл событий не держит. `re.compile` — шаблоны модулей (`LANGUAGE_CODE`,
  `_TELEGRAM_NAME`) собираются один раз; `re.search` — в тестах сверки кодов.
- **интеграция** — риска нет, потому что срез никуда не ходит: `httpx` — только `AsyncClient` тестов API
  (`tests/test_sales_kb_api.py`); `urllib.parse.urlsplit` в `sender._link`/`sender._telegram` разбирает форму ссылки, сети нет.
  Ссылки сайта, созвона и Telegram хранятся, а не открываются.
- **новый модуль** — `kb.py`, `kb_load.py`, `sender.py`, `kb_schemas.py`, миграции и файлы экрана: правила — в ядре
  (`features/sales/`), экран и консоль зовут одни функции; покрытие изменённого — 100 % у модулей ядра и API.

## Предохранитель

40 файлов, net 4 250 (+4 288 / −38) при пределах 25 и 800 — **постоянный вейвер
владельца для модульных срезов продаж (05.10)** строкой `waivers: max_loc_diff=4250 max_files_touched=40 … by=human:anthony`
в STATUS. Условия владельца соблюдены:

1. числа конкретные — из `delivery_check` после переноса;
2. общая часть — **152 строки** нетто при пределе ~300: миграции `c6efe4e5de7e` (78) и `a51e5688f79e` (36),
   `backend/api/errors.py` (8), `backend/features/core/domain.py` (4), `backend/features/core/models/__init__.py` (4),
   `backend/cli/main.py` (3), `tests/test_schema.py` (3), `frontend/src/settings/UsagePage.test.tsx` (6),
   `frontend/src/api/labels.ts` (1), `.secrets.baseline` (+10/−1); свои файлы раздела вне масок `SALES_PATHS`
   (`backend/api/sales/*`, `backend/cli/sales.py`, `frontend/src/api/sales*.ts`) — модуль продаж, не общая часть;
3. список общих файлов отправлен соседней сессии до PR (05.10).

Из строк среза 1 828 — тесты. Разрезы, посчитанные агентом до решения владельца (сервер / экран; четыре PR), не
понадобились.

## Предупреждения delivery_check, разобранные

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `202da46`):

```
breakers: files=40 net_loc=4250 (+4288/-38), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 40, 'max_loc_diff': 4250, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

- «`irreversible_surfaces:` не называет отправка наружу» — детектор `_outward_surface` (`scripts/delivery_runtime.py`)
  находит маркеры отправки во всём продуктовом коде (письма доноров) — предупреждение стоит и на STATUS main. Строка
  подписана владельцем и перенесена дословно; срез отправки не добавляет — `check_ready` только проверяет готовность для Ф4.
- «asserts_reviewed_by deferred» — 157 утверждений ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта (новая зависимость — решение
  владельца). Метаморфные отношения версии (перестановка, пробелы, номер записи, откат правки) — примерами в
  `test_a1_order_numbers_and_whitespace_do_not_change_the_version` и `test_a1_price_edit_…`; мутанты M1–M4 убиты.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

Дословно (`contour_waves --base origin/main`, голова `202da46`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 17 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```

Волны В2–В4 — pending: их триггеров (промпт, пороги, автоотправка) в срезе нет. Общий код объявлен в `shared_changes:`
полными путями со словом «согласовано».

## Spec coverage gaps

- A3 расширен решением владельца 05.10: готовность требует и имя отправителя; спека и тесты — по новому правилу.
- A4 (контраст, рамка, наведение) — замером живьём, не тестом сьюта (нужен браузер и сервер).
- Гонка двух заведений одного ключа — 500 вместо 409 словами (ключ держит база); правки одной записи — побеждает последняя.
- Ключ записи сводит пробелы в заголовке, но не регистр: «Цена» и «цена» — две записи.
- Строка лога «база знаний загружена» (`kb_load.apply`) пишется и при повторе без записи — журнал аудита при этом не пишется
  (верно); правка — одна строка, в бэклоге продаж вместе с гонкой ключа.
- `check_ready` пока никто не зовёт (Ф4), версию никто не пишет (Ф3.2) — «заложено».

## Находки (общий код — не чинил)

1. **Кромка полей ниже нормы границы элемента.** `frontend/src/styles/glass.css:480` (`.glassSolid` светлой темы,
   `--field-edge: rgb(12 40 50 / 0.42)`) — кромка поля в окне 2,48 : 1 к полю и 2,52 : 1 к стеклу; на обычной панели
   (`glass.css:83`, `--field-edge: var(--glass-edge)`) кромка поля «Имя отправителя» — 1,05 : 1, подложка поля к стеклу —
   1,04 : 1; в тёмной теме в окне — 1,62 : 1. Норма проекта для границы элемента управления — 3 : 1 (`docs/UI_RULES.md`,
   24.09: флажок переведён на чернила, 4,2 и 4,6). Воспроизвести: пиксели по вертикали через поле на снимке пробы
   (скрипт разбора — в scratchpad сессии; снимок `contrast-kb-entry-light.png`). Поля узнаются по подписи и тексту, но
   норма записана для границ — решение за владельцем `glass.css`.
2. **Жёлтые `Alert`:** заголовок `--status-amber-ink` на `--mantine-color-yellow-light` — 4,30 : 1 на свету (замер прошлого
   агента на «Отправителе»); у себя — `INK_TITLE`, общее правило для `Alert` в `glass.css` — кандидат в общую правку.
3. Падение `/usage` на `hunter` (находка прошлого агента) — закрыто соседями #169, в main.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI и ОК соседней сессии на общие точки.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **193**, из них без ссылки на пример спеки:
**157**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(price.getByText('Цену называем после короткого созвона.')).toBeInTheDocument();
-	expect(price.getByText('цены')).toBeInTheDocument();
-	expect(price.getByText('ru')).toBeInTheDocument();
-	expect(price.getByText('аудит, цена')).toBeInTheDocument();
-	expect(price.getByText('seller@ours.example.test')).toBeInTheDocument();
-	expect(price.getByRole('switch', { name: 'Агент видит «Цена аудита»' })).toBeChecked();
-	expect(old.getByText('кейс')).toBeInTheDocument();
-	expect(old.getByRole('switch', { name: 'Агент видит «Made-up shop»' })).not.toBeChecked();
-	expect(screen.getByText('kb-3f2a9c1d0b7e')).toBeInTheDocument();
-	expect(screen.getByText(/агент видит 1 из 2/)).toBeInTheDocument();
-	expect(screen.getByText('База знаний — 2')).toBeInTheDocument();
-	expect(
-	expect(screen.getByText(/sales-kb-load/)).toBeInTheDocument();
-	expect(
-	expect(screen.getByText('База не ответила — повторите')).toBeInTheDocument();
-	await waitFor(() => expect(calls(recorded, 'PATCH', '/api/sales/kb/7')).toHaveLength(1));
-	expect(calls(recorded, 'PATCH', '/api/sales/kb/7')[0]?.body).toEqual({ active: false });
-	expect(calls(recorded, 'GET', '/api/sales/kb').length).toBeGreaterThan(before),
-	expect(
-	expect(screen.getByText('Не переключили')).toBeInTheDocument();
-	expect(dialog.queryByText(/Впишите/)).not.toBeInTheDocument();
-	expect(dialog.getByRole('button', { name: 'Завести' })).toBeDisabled();
-	await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/kb')).toHaveLength(1));
-	expect(calls(recorded, 'POST', '/api/sales/kb')[0]?.body).toEqual({
-	expect(
-	await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
-	expect(dialog.getByText('Впишите заголовок — по нему запись узнают')).toBeInTheDocument();
-	expect(dialog.getByText('Длиннее 40 знаков: сейчас 44')).toBeInTheDocument();
-	expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
-	expect(
-	expect(calls(recorded, 'PATCH', '/api/sales/kb/7')[0]?.body).toEqual({
-	expect(title).toHaveValue('Кто мы');
-	expect(await dialog.findByText('цены · ru', {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(dialog.getByText('Цена аудита')).toBeInTheDocument();
-	expect(dialog.getByText('теги: цена')).toBeInTheDocument();
-	expect(dialog.getByText(/Выключенных здесь нет/)).toBeInTheDocument();
-	expect(dialog.queryByText('Made-up shop')).not.toBeInTheDocument();
-	expect(calls(recorded, 'GET', '/api/sales/kb/preview')).toHaveLength(1);
-	expect(
-	expect(await screen.findByText('База знаний — 3', {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(screen.getByText('Отправитель')).toBeInTheDocument();
-	expect(warning).toHaveTextContent(
-	expect(field('Физический адрес')).toHaveValue('');
-	expect(screen.getByText('Ещё не заполнялся.')).toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
-	await waitFor(() => expect(recorded.calls.filter((c) => c.method === 'POST')).toHaveLength(1));
-	expect(recorded.calls.find((c) => c.method === 'POST')?.body).toEqual({
-	expect(ready).toHaveTextContent('Всё, без чего письмо продаж не уходит, задано.');
-	expect(screen.getByText(/Правил seller@ours\.example\.test/)).toBeInTheDocument();
-	expect(field('Подпись')).toHaveValue('Ива Тестова');
-	expect(
-	expect(field('Сайт')).toHaveValue('studio.example.test');
-	expect(screen.getByText('Длиннее 5 знаков: сейчас 11')).toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
-	expect(recorded.calls.filter((c) => c.method === 'POST')).toEqual([]);
-	expect(operationTitle('sales_verify')).toBe('проверка адресов продаж');
A1	assert edited != kept
A1	assert await kb.version(session) == kept
A1	assert versions == [{"было": kept, "стало": edited}, {"было": edited, "стало": kept}]
A1	assert kb.version_of([BRIEF, PRICE]) == kb.version_of([PRICE, BRIEF])
A1	assert kb.version_of([BRIEF, spaced]) == kb.version_of([BRIEF, PRICE])
A1	assert kb.version_of([fact]) == kb.version_of([PRICE])
A1	assert kb.version_of(seen) != kb.version_of([BRIEF, PRICE])
A1	assert kb.version_of([]) == f"kb-{empty}"
A1	assert len(kb.version_of([PRICE])) == len("kb-") + 12
A1	assert again.id != price.id
A1	assert await kb.version(session) == before == kb.version_of([BRIEF, PRICE])
A2	assert _titles(await kb.facts(session)) == ["Кто мы", "Made-up shop"]
A2	assert await kb.facts(session, kinds=[KbKind.PRICE_POLICY]) == []
A2	assert await kb.facts(session, tags=["цена"]) == []
A2	assert _titles(await kb.facts(session, language="ru")) == ["Кто мы"]
A2	assert await kb.version(session) == kb.version_of([BRIEF, CASE])
A2	assert [(group.kind, group.language) for group in groups] == [
A2	assert [row.title for row in await kb.entries(session)] == [
A2	assert _titles(await kb.facts(session, kinds=[KbKind.PRICE_POLICY])) == ["Цена аудита"]
A2	assert await kb.version(session) == both
-	assert _titles(await kb.facts(session, kinds=[KbKind.CASE, KbKind.BRIEF])) == [
-	assert _titles(await kb.facts(session, language=" EN ")) == ["Made-up shop"]
-	assert _titles(await kb.facts(session, tags=["SEO", "нет такого"])) == ["Made-up shop"]
-	assert _titles(await kb.facts(session, tags=[])) == ["Кто мы", "Made-up shop", "Цена аудита"]
-	assert await kb.facts(session, kinds=[]) == []
-	assert [(g.kind, g.language) for g in kb.grouped(found)] == [
-	assert found == Entry(
-	assert str(refused.value).startswith(words)
-	assert str(refused.value) == (
-	assert len(await kb.entries(session)) == 2
-	assert (edited.title, edited.text) == ("Цена аудита", "Новый текст.")
-	assert str(refused.value) == "записи базы знаний №999999 нет — обновите список"
-	assert (price.text, len(await _journal(session))) == (PRICE.text, 1)
-	assert (row.updated_by, row.tags, row.active) == (
-	assert (row.created_at is not None, row.updated_at is not None) == (True, True)
-	assert await _journal(session) == [
-	assert (edited.updated_by, edited.updated_at is not None) == ("admin@ours.example.test", True)
-	assert details is not None
-	assert (details["поля"], details["было"]) == (
-	assert (same.updated_by, len(await _journal(session))) == ("тест", 1)
-	assert spec is not None, path
-	assert spec.loader is not None, path
L5	assert await connection.run_sync(_present) == SCHEMA
L5	assert (after_downgrade, after_upgrade) == (set(), SCHEMA)
-	assert (await connection.run_sync(_journal_values)).count("sales_kb_changed") == 1
-	assert response.status_code == 201, response.text
-	assert {k: card[k] for k in ("kind", "language", "title", "tags", "active", "updated_by")} == {
-	assert (listed["total"], listed["active"], [row["id"] for row in listed["rows"]]) == (
-	assert listed["version"] == kb.version_of([kb.entry(**PRICE)])
-	assert listed["kinds"] == [kind.value for kind in KbKind]
-	assert listed["limits"] == {"title": 255, "text": 20_000, "tag": 64, "tags": 20}
-	assert list(authors) == [user.id]
A1	assert edited.status_code == 200, edited.text
A1	assert edited.json()["text"] == "Цену называем сразу."
A1	assert after != before
A2	assert (off.status_code, off.json()["active"]) == (200, False)
A2	assert (listed["total"], listed["active"]) == (3, 2)
A2	assert [
A2	assert preview["total"] == 2
A2	assert (
-	assert response.status_code == code, response.text
-	assert response.json()["detail"].startswith(words)
-	assert response.status_code == 422
-	assert (missing.status_code, missing.json()["detail"]) == (
-	assert (kept.status_code, kept.json()["title"], kept.json()["text"]) == (
-	assert body["missing"] == [
-	assert (body["physical_address"], body["updated_at"]) == (None, None)
-	assert body["limits"] == {
-	assert saved.status_code == 200, saved.text
-	assert (read["sender_name"], read["missing"], read["updated_by"]) == ("Ива Тестова", [], SELLER)
-	assert (read["website"], read["telegram"]) == (None, "@studio_example")
-	assert (bad.status_code, bad.json()["detail"]) == (
-	assert typo.status_code == 422
-	assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
-	assert in_app == in_table
-	assert problems == []
-	assert entries == [kb.entry(**BRIEF), kb.entry(**PRICE), kb.entry(**CASE)]
-	assert entries == [kb.entry(**BRIEF)]
-	assert problems == [
-	assert str(refused.value).startswith(words)
-	assert str(refused.value).startswith(f"файл {tmp_path / 'нет.json'} не открылся: No such file")
-	assert str(refused.value) == (
-	assert not inside.exists()
-	assert await _run(session, _file(tmp_path)) == EXIT_OK
-	assert capsys.readouterr().out == (
-	assert await _rows(session) == [
-	assert await _journal(session) == [
-	assert await _run(session, path) == EXIT_OK
-	assert "добавлено 0, без изменений 3, отличается 0" in capsys.readouterr().out
-	assert (len(await _rows(session)), len(await _journal(session))) == (3, 1)
-	assert price is not None
-	assert await _run(session, edited) == EXIT_OK
-	assert capsys.readouterr().out.splitlines()[1:4] == [
-	assert [text for title, text, *_ in await _rows(session) if title == "Цена аудита"] == [
-	assert len(await _journal(session)) == 1
-	assert (
-	assert [text for title, text, *_ in await _rows(session) if title == "Цена аудита"] == [
-	assert details is not None
-	assert (details["обновлено"], details["версия"]["было"]) == (1, before)
-	assert details["версия"]["стало"] == await kb.version(session) != before
-	assert "обновлено 1" in capsys.readouterr().out
-	assert await _run(session, _file(tmp_path), "--dry-run") == EXIT_OK
-	assert capsys.readouterr().out.endswith("Предпросмотр: в базу ничего не записано.\n")
-	assert (await _rows(session), await _journal(session)) == ([], [])
-	assert await _run(session, path) == EXIT_BAD_INPUT
-	assert capsys.readouterr().out == (
-	assert await _rows(session) == []
-	assert await _run(session, tmp_path / "нет.json") == EXIT_BAD_INPUT
-	assert capsys.readouterr().out.startswith("База знаний не прочитана: файл ")
-	assert main(["sales-kb-load", "--file", "база.json"]) == EXIT_CANCELLED
-	assert "База знаний пишется одной транзакцией: в базе ничего не осталось." in err
-	assert "домены остались" not in err
-	assert found is not None, f"в salesTypes.ts нет типа {name}"
-	assert found is not None, f"в salesLabels.ts нет таблицы {name}"
-	assert _union("KbKind") == kinds
-	assert _keys("KB_KINDS") == kinds
-	assert _union("SenderField") == fields
-	assert _keys("SENDER_FIELDS") == fields
A3	assert str(refused.value) == (
A3	assert "; ".join(every) in str(refused.value)
A3	assert (await sender.read(session)).missing == every
A3	assert saved.values["physical_address"] is None
A3	assert str(refused.value) == (
A3	assert (ready.missing, ready.values["physical_address"]) == ([], ADDRESS)
-	assert saved.values == FILLED
-	assert (saved.updated_by, saved.updated_at is not None) == ("admin@ours.example.test", True)
-	assert (await sender.read(session)).values == FILLED
-	assert await session.scalar(select(func.count()).select_from(SalesSettingsModel)) == 1
-	assert saved.values == dict.fromkeys(sender.FIELDS) | {
-	assert str(refused.value).startswith(words)
-	assert await session.get(SalesSettingsModel, 1) is None
-	assert [target for target, _ in journal] == ["sales_settings", "sales_settings"]
-	assert journal[-1][1] == {"поля": ["physical_address"], "было": {"physical_address": ADDRESS}}
-	assert saved.missing == [
-	assert (await session.get(SalesSettingsModel, 1), await _journal(session)) == (None, [])
-	assert secret_like == []
-	assert columns == {*sender.FIELDS, "id", "updated_by", "created_at", "updated_at"}
```

Привязаны к примерам: **A1 A2 A3 L5**. Остальные 157 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 157
