# Verify report

**Поставка:** срез `sales-chain` (модуль «Продажи», 4.6a — часть 1 среза 4.6) — цепочка писем продаж в базе и
экран правки. Одним PR под постоянный вейвер владельца для модульных срезов продаж (05.10).

**Date:** 2026-10-05
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; общие точки — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=192 утверждений без примера спеки ждут подписи человека: примеры C1–C11 записаны после кода, связь примеров с тестами — в eval-smoke; подписывает владелец при ревью, дайджест в конце
**CI run:** нет — снимается на PR; пре-пуш гоняет полный набор
**Commit:** ветка PR на main `94e5945`; полные прогоны агента — на вершине его ветки `c1ba8a2` (тот же код среза), после переноса — проверки ниже

## Сборка

Ветка агента (на main `7085826`, после 3.1) перенесена на main `94e5945` (после #174, #172, #177, #175, #179, #176, #182, #181, #183, #184, #186, #185, #187, #188, #189, #190, #191, #193, #192, #195, #196, #194): документы —
первым коммитом (`sales-kb` в архив, активный срез `sales-chain`), затем 7 коммитов агента в том же порядке и правка
проверяльщика адресов последним коммитом кода. При переносе:

- миграция `723e3ddab31f` перецеплена с `a51e5688f79e` на голову main `ad4a79bc6000` — `down_revision` и строка `Revises:`;
  голова одна — `cbc5aadf4fc2`; база тестов поднимается `alembic upgrade head` с нуля;
- конфликты — только в общих перечнях и снимках: `cli/main.py` — обе стороны; снимок сложности пересчитан,
  `.secrets.baseline` — сторона main и пересканирование; в файлах среза конфликтов нет;
- сообщение WIP-коммита ветки агента заменено сутью (новые коммиты, запушенная история не переписывалась).

| SHA | Суть | Файлов (без `delivery/`) | +/− | net |
|---|---|---|---|---|
| `9811dda` | ядро: таблица, миграции, правила записи `chain_text.py`, набор/версия/запись с журналом `chain.py`, общий `outside.py` (`kb_load.py` на нём); тесты ядра (77) | 12 | +1 384/−49 | 1 335 |
| `9baa050` | API под `Permission.SALES`: набор, запись шага, предпросмотр; тесты API (19) | 4 | +551/−2 | 549 |
| `9041a2f` | консоль `outreach sales-chain-load`; тесты консоли (21) | 4 | +679/−2 | 677 |
| `bd10037` | вкладка «Цепочка писем», окно шага, письмо глазами адресата, `SaveRefusal`; vitest (12) и сверка кодов (4) | 15 | +1 283/−17 | 1 266 |
| `b08895c` | экраны цепочки в каталоге замеров, классы-якоря (сообщение «WIP» ветки агента заменено сутью при переносе) | 4 | +87/−5 | 82 |
| `ab93f19` | замер окна шага: подготовка вписывает плотное выдуманное тело вместо щелчка по подписи переключателя; снимок длины каталога 372 → 474 | 1 (+ снимок) | +27/−3 | 24 |
| `9098df4` | цикл миграции 1.1a (`tests/test_sales_model.py`) откатывает сначала зависимую миграцию цепочки — полный pytest на `ab93f19` упал на `DROP TABLE sales_hypotheses` | 1 | +9/−1 | 8 |
| `a9a78ed` | проверяльщик адресов 1.4: отказ сети без текста исключения у несобранного запроса (`reason_of`, `from None`, как Hunter и выдача в #174); тест | 2 | +24/−1 | 23 |
| `0ee7838` | команды консоли продаж одним перечнем — `backend/cli/sales_commands.py`; `cli/main.py` их больше не называет (у предела длины: срезы продаж вместе с соседним PR давали 507 строк при пределе 500); согласовано с соседней сессией | 3 (+ снимок) | +50/−23 | 27 |

## Shape oracles

На голове `8dfe13d` после переноса на main `94e5945`, по одному разу.

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | одна голова `cbc5aadf4fc2` (`723e3ddab31f` после `ad4a79bc6000` main) | 0 |
| pytest среза и соседей (`test_sales_*`, `test_api_sales_*`, `test_cli_main`, `test_schema`, `test_migrations_match_models`, `test_agent_settings`, `test_contour_waves`, `test_prune`, `test_probe_donor`) с покрытием; база — `alembic upgrade head` с нуля | 751 passed in 92.39s (0:01:32) | 0 |
| `SKIP_TESTS=1 STRICT=1 check_diff_coverage.sh` (BASE — main) | см. лог; миграции среза — под циклом «вниз и вверх» | 0 |
| `pre-commit run --all-files` | 27 хуков Passed или Skipped, ни одного Failed | 0 |
| `check_baseline_ratchet.sh` (BASE — main) | снимки не выросли | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=40 net_loc=3988 (+4078/-90)` — вейвер владельца; ниже, дословно | 0 |
| `assert_digest.sh` | `asserts_without_example: 192` | 0 |
| `tsc --noEmit` / `eslint src/` / `prettier --check` | чисто | 0 / 0 / 0 |
| vitest `src/sales` + `src/settings` (`--maxWorkers=2`) | Tests  91 passed (91) | 0 |

## Behavior oracles

- [x] PASS — после переноса: 751 passed in 92.39s (0:01:32); vitest `src/sales` + `src/settings` — Tests  91 passed (91).
- [x] PASS — полные прогоны агента на вершине `c1ba8a2` (тот же код среза): pytest 3994 passed, vitest 54 файла / 510 тестов,
      `pre-commit --all-files` — 27 хуков, exit 0; полный набор на голове PR гоняет пре-пуш.
      Первый полный прогон — на `ab93f19`: **1 failed**, 3993 passed, exit 1 —
      `tests/test_sales_model.py::test_downgrade_drops_tables_and_types_and_upgrade_runs_again` (срез 1.1a) откатывал только
      свою миграцию поверх схемы головы, и `DROP TABLE sales_hypotheses` упёрся в новый ключ
      `sales_chain_templates_hypothesis_id_fkey` (`DependentObjectsStillExistError`). В выборочных прогонах прошлого агента
      этого файла не было. Правка `9098df4`: откат, как у `alembic downgrade`, — сначала поздние зависимые миграции
      (`DEPENDENTS`), затем своя; что проверяет тест, не изменилось.
- [x] PASS — новые тесты среза: `tests/test_sales_chain.py` (77), `tests/test_sales_chain_api.py` (19),
      `tests/test_sales_chain_cli.py` (21), `tests/test_sales_chain_screen.py` (4) — 121 passed за 10 с; vitest
      `src/sales` — 5 файлов, 59 тестов (`ChainPane.test.tsx` — 12), exit 0.

### Красный прогон до кода

Дерево `origin/main` `448ef4c` (`git archive`) + тесты среза поверх (`tests/test_sales_chain*.py`, `tests/test_schema.py`,
`frontend/src/sales/ChainPane.test.tsx`), своя временная база `outreach_test_<хэш дерева>` — снесена, дерево удалено.
Пакет `backend` брался из архива (путь в тексте ошибки).

- pytest: 4 файла не собрались — `ImportError: cannot import name 'chain' from 'backend.features.sales'`
  (`test_sales_chain.py`, `test_sales_chain_api.py`), `cannot import name 'run_chain_load' from 'backend.cli.sales'`,
  `cannot import name 'chain_text' …` (`test_sales_chain_screen.py`), exit 2; `tests/test_schema.py` — 2 failed, 6 passed
  (`test_all_entities_registered`, `test_models_exported`), exit 1.
- vitest `ChainPane.test.tsx`: 12 из 12 failed — вкладки «Цепочка писем» на main нет: тест не дожидается её содержимого
  (5 с) и ходит мимо записанных ответов; exit 1.

### Обратные прогоны

Мутант — одна правка строки (`mutate.py`: ровно одно вхождение), прогон `tests/test_sales_chain.py`,
`tests/test_sales_chain_api.py`, `tests/test_sales_chain_cli.py` (117 тестов) или vitest `ChainPane.test.tsx`, возврат
файла `git checkout` с проверкой sha256 — байт в байт у всех; дерево после — чистое. Сняты на `ab93f19` (правка
`9098df4` — только тест 1.1a, файлы мутантов не тронуты).

| Мутант | Правка | Итог | Убит тестами |
|---|---|---|---|
| M1 тема с «Re:» проходит | `chain_text._subject`: `if False and (prefix := _REPLY.match(text)) …` | 9 failed | `test_first_letter_subject_never_pretends_to_be_a_reply` ×5, `test_russian_reply_prefix_is_refused_too` ×2, API `test_refusals_come_in_words_and_nothing_is_written[Re:]`, консоль `test_every_bad_template_is_named_by_its_number` |
| M2 метрики в теле не проверяются | `step_template`: `guards.assert_no_metrics(letter.subject)` вместо темы и тела | 6 failed | `test_ahrefs_metrics_never_get_into_the_template` ×4, API `…[метрики Ahrefs]`, консоль `test_every_bad_template_is_named_by_its_number` |
| M3 незнакомая подстановка проходит | `_placeholders`: `if name not in PLACEHOLDERS and False:` | 5 failed | `test_unknown_or_broken_placeholder_is_refused_in_words[{{first_name}}]`, `…[{{nam}}]`, `test_unknown_placeholder_in_the_subject_is_refused_too`, API `…[{{nam}}]`, консоль `test_every_bad_template_is_named_by_its_number` |
| M4a версия без тела — правка текста её не меняет | `version_of`: `[step, language, subject]` | 4 failed | `test_each_field_of_a_step_changes_the_version[body]`, `test_edit_changes_the_version_and_reverting_the_edit_returns_it`, `test_version_is_chain_and_twelve_hex_digits_of_sha256`, API `test_editing_the_text_on_the_screen_changes_the_chain_version` |
| M4b версия без темы | `version_of`: `[step, language, body]` | 2 failed | `test_each_field_of_a_step_changes_the_version[subject]`, `test_version_is_chain_and_twelve_hex_digits_of_sha256` |
| M5 шаги двух наборов смешиваются | `resolve`: `{**общий, **свой}` | 1 failed | `test_own_chain_replaces_the_common_one_whole_steps_never_mix` |
| M6 выключенный шаг входит в цепочку | `_active` без `active.is_(True)` | 3 failed | `test_switched_off_own_steps_leave_the_hypothesis_on_the_common_chain`, `test_switched_off_step_is_not_in_the_chain`, консоль `test_hypothesis_set_goes_to_the_named_hypothesis` |
| M7 подпись из настроек в теле проходит | `unsigned`: `if value and … in body and False:` | 6 failed | `test_settings_signature_or_address_inside_the_body_is_refused` ×2, `test_save_refuses_the_signature_written_in_the_sender_settings`, API `test_preview_refuses_a_body_that_repeats_the_settings_signature`, консоль ×2 |
| M8 экран не говорит «цепочка не задана» | `ChainPane.tsx`: `state.missing.length === 2` | 1 failed | vitest «пустой набор — у каждого языка «цепочка не задана» словами и дорога к загрузке» |
| M9 тема добивки уходит на сервер | `chainDraft.ts`: `subject: draft.subject` | 1 failed | vitest «новая добивка гипотезы: поля темы нет, тема уходит null, набор — гипотезы» |

10 из 10 убиты. Четыре мутанта задания — M1 (тема с «Re:»), M2 (метрики в теле), M3 (незнакомая подстановка), M4a/M4b
(версия не меняется при правке). M4b держат два теста — правка темы видна только им; M5 — один тест.

## Живой прогон

Стенд: своя база `outreach_live_sales_m` (на голове миграций), API `:8107` (`SALES_ENABLED=true`), фронт `:5181`
(прокси на `:8107`), учётка стенда; Playwright `1.63.0` с Pillow через `uv run --with`. Выдуманные тексты — файлы в
scratchpad, вне репозитория. После прогона стенд остановлен, база снесена (`DROP DATABASE`), чужие базы стендов не тронуты.
Вершина замеров — `ab93f19` (до неё — `b08895c`: код экрана тот же, правился только каталог).

**Миграции на живой базе:** `downgrade a51e5688f79e` → таблицы нет, значение журнала осталось (1 — `ADD VALUE` необратим) →
`upgrade head` — таблица есть, значение одно; чисто.

**Консоль — 10 сценариев** (`outreach sales-chain-load`, файл вне репозитория):

| Сценарий | Вывод | exit |
|---|---|---|
| `--dry-run` | «добавлено 4 … Предпросмотр: в базу ничего не записано.» | 0 |
| первая загрузка (EN 1–3, третий выключен; RU 1) | «добавлено 4»; версии ru `chain-4f53…` (пустая) → `chain-c1cd…`, en → `chain-b190…` | 0 |
| повтор | «без изменений 4», версии те же, журнал не пишется | 0 |
| другой текст добивки без `--update` | «отличается 1 — не тронуто (перезаписать: --update)», «№2: первая добивка, en», версия прежняя | 0 |
| то же с `--update` | «обновлено 1», en `chain-b190…` → `chain-7485…`, журнал одной записью | 0 |
| файл с 4 плохими шаблонами из 5 («Re:», «DR 45», `{{first_name}}`, тема у добивки) | «Файл не загружен: шаблонов с ошибками 4 из 5 — в базе ничего не изменилось», каждый — номером, шагом и языком | 2 |
| файл в копии репозитория | «…лежит в копии репозитория … — тексты писем уехали бы в публичную историю», до чтения | 2 |
| `--hypothesis "Нет такой"` | «Гипотезы «Нет такой» нет — её набору некуда лечь» | 5 |
| `--hypothesis "Стенд-гипотеза А"` (свой EN-шаг 1) | «набор гипотезы №1», en `chain-4f53…` → `chain-78b1…`, ru не тронута | 0 |
| файла нет | «не открылся: No such file or directory» | 2 |

**API** (`httpx`, учётка стенда): общий набор — ru `common`, нет первой и второй добивки; en `common`, нет второй добивки
(третий шаг выключен); гипотеза А — en `own`, нет обеих добивок (свой набор целиком, общие добивки не подмешаны); гипотеза Б —
обе `common`. Отказы словами: «Re:» и «FW:» — 400; «Domain Rating» — 400 (общая проверка метрик); `{{first_name}}` — 400 с
перечнем подстановок; тема у добивки — 400; `rewrite` в добивке — 400; подпись стенда в теле — 400; зона `[signature]` —
400 с номером строки; недостижимый коридор — 400 («2% письма — нижний край 15% недостижим»); чужое поле — 422; гипотезы
№999 нет — 404 словами. Правка текста первого письма EN: `chain-b190…` → `chain-76ef…`, откат → `chain-b190…`; запись
без изменений — 200 без журнала. Предпросмотр: тема и зоны с «Alex Example», «Example Company», «example.com»; добивка RU —
«Алекс Пример», тема `null`; подпись и имя из «Отправителя», `missing: ["не задан физический адрес"]`.
Порядок прогона поймал слепую зону правила (C4): пока подпись в «Отправителе» не задана, текст подписи в шаблоне
проходит (сверять не с чем) — тот же шаблон после записи подписи отказывается; уже записанный шаблон перепроверит только
сборка 4.6b (Spec gaps).

**403:** учётка оператора с `{"sales": false}` — `GET /api/sales/chain`, `POST /api/sales/chain`,
`POST /api/sales/chain/preview` — 403 «Действие «sales» недоступно этой учётке».

**Журнал:** 10 строк `sales_chain_changed` за прогон: 3 загрузки (`target=sales_chain`, `user_id` пуст, версии по языкам) и
7 записей с экрана и API (`sales_chain_template:<номер>`, автор, `поля`, `было`, версия до и после); цепочка версий
сходится; повторы и запись без изменений строк не дали.

**Экран живьём** (Playwright): выключение первого письма RU в окне → «Сохранено: Первое письмо · Русский», карточка RU —
«цепочка не задана», шаг — «выключен»; включение обратно — «цепочка неполна»; тема «Re: stand question» → «Не сохранили» и
слова сервера над формой, введённое осталось; «Показать письмо» с той же темой — «Письмо не собралось».

**Контраст** (`ui_contrast.py`, норма 4,5 текст / 3,0 крупное) — три экрана, обе темы, все точки «ок», exit 0:

| Экран | Точек | Свет: минимум текста / крупного | Тьма: минимум текста / крупного |
|---|---|---|---|
| `sales-chain` (1440) | 14 | 6,48 пояснение над цепочками / — | 7,65 значок «не задан» / — |
| `sales-chain-phone` (390) | 8 | 6,51 пояснение / — | 8,70 пояснение / — |
| `sales-chain-step` (окно шага с письмом) | 17 | 4,86 текст поля / 3,93 «Показать письмо» | 9,31 что за шаг / 7,33 «Сохранить» |

**Окно шага: каталог не снимался, текст поля — слепое пятно замера.** (1) Подготовка `open_step` (WIP) щёлкала по подписи
переключателя — Playwright отклонял щелчок 30 с: подпись Mantine накрыта скрытым полем переключателя; экран не снимался
вовсе. (2) С шаблоном стенда (пустые строки между зонами) текст поля на свету — 4,39 «МАЛО». Разбор на стенде:

| Текст поля (свет) | Проба каталога (рамка + 6 px) | Внутри поля без рамки | Первая строка букв | Ядро букв |
|---|---|---|---|---|
| шаблон стенда | 4,39 | 6,11 | 5,86 | 15,55 |
| плотное выдуманное тело `STEP_BODY` | 4,86 | — | — | — |
| тьма, шаблон стенда | 13,78 | 13,85 | 11,96 | 13,33 |

Текст — `oklch(0.23 0.025 215)` на подложке `rgba(12, 40, 50, 0.035)` поверх окна `rgba(255, 255, 255, 0.93)`; кромка поля на
плотном стекле светлой темы — чернила `rgba(12, 40, 50, 0.42)`. Порог делит вырезку на «кромка + буквы» и «подложка», и у
поля с пустыми строками кромка — большая доля вырезки; по строке букв и внутри поля норма выполнена. Тот же класс — у окна
записи базы знаний (3.1) и в `scripts/ui_screens.py:161`. Правка — в своём каталоге (`ab93f19`): подготовка вписывает
плотное выдуманное тело (годное для показа письма), разбор — у пробы; после неё все 17 точек окна «ок» в обеих темах.
У «Показать письмо» (кнопка `default` с кромкой) 3,93 — по норме кнопок каталога (3,0), класс тот же.

**Рамка:** документ 1440/1440 и 390/390 во всех состояниях обеих тем — общий набор, гипотеза А (своя неполная цепочка),
гипотеза Б (пустой набор, «Шаблонов в наборе нет…»), окно шага с письмом; элементов вкладки за краем окна — 0; окно шага —
780 px на 1440 и 351 px на 390, элементов за краем окна — 0.

**Наведение:** `ui_hover.py --path /sales?tab=chain` — 39 элементов, сдвигов 0, не проверено 0, exit 0 (общий `PATHS` не
тронут); в окне шага — 4 кнопки (рамка и `transform` до и после наведения), сдвигов 0, обе темы, 1440 и 390.

**Клавиатура:** Tab по вкладке — «Набор» → «Править»/«Задать» шести шагов → меню; фокус виден на каждом элементе; окно шага
открывается Enter с «Править», Tab идёт тема → текст → переключатель → «Показать письмо» → «Отмена» → закрыть → по кругу
внутри окна, Escape закрывает.

**Шапка окна при прокрутке — находка в общем коде (не чинил).** Окно шага с письмом выше окна браузера (1045 из 808 px на
1440, 1501 из 758 на 390), и при прокрутке заголовок «Первое письмо · Английский — общий набор» и крестик ложатся поверх
кнопок и текста: шапка Mantine `position: sticky`, а фон её — `transparent` из общего `frontend/src/theme.ts:222`.
Нарушает правило `docs/UI_RULES.md` «ничего прозрачного поверх содержимого». Снимки — `modal-header-1440-light.png` и `modal-header-390-dark.png` рядом с черновиком.

## Product oracles

- [x] PASS — `eval-smoke.md`: C1–C11, сверка кодов, красный прогон, мутанты отмечены; настоящие тексты и версия в письме —
      «заложено».

## Ревью рисковых мест

Классы, поднятые диффом (`delivery_risk.risky_classes` от `origin/main`): безопасность (`auth`, `bearer`, `permission`,
`secret`), транзакция БД (`commit`, `session.begin_nested`), производительность (`json.dumps`, `json.loads`, `re.compile`,
`re.search`), интеграция (`httpx`), новый модуль. Деньги не задеты.

- **безопасность** — каждый маршрут `backend/api/sales/chain.py` под `_seller = Depends(needs(Permission.SALES))`; 403
  словами проверен на каждом маршруте (`test_without_the_sales_right_every_route_refuses_in_words`, таблица маршрутов
  сверена `test_the_table_covers_every_route_of_the_chain`) и живьём. Тела запросов — `ConfigDict(extra="forbid")`: опечатка
  в поле — 422, а не молча. Коммерческие тексты — только в базе: файл внутри копии репозитория `outside.read_json` отказывает
  до чтения (`repository_of`). `hashed_secret` в `.secrets.baseline` — номер ревизии `723e3ddab31f` (ложное срабатывание,
  как у соседних миграций). `bearer(await sign_in(SELLER))` и `'GET /api/auth/me': { body: ADMIN }` — помощники тестов.
- **транзакция БД** — запись шага и её строка журнала — одна транзакция: `chain.save` зовёт
  `AccessRepository(session).record` до `await session.commit()` маршрута `save_step`; шаблон без журнала не ляжет, отказ
  `ChainError` до `commit` — ничего (тест `test_refusals_come_in_words_and_nothing_is_written`). Загрузка — одна транзакция:
  `chain_load.apply` → `await session.commit()` в `run_chain_load`; ошибка в любом шаблоне — ничего (проверка до записи).
  `session.begin_nested()` — только в тестах ограничений базы. Опасно здесь: две одновременные записи одного нового шага —
  `_row` обеих видит пусто, вторая упадёт на `uq_sales_chain_templates_key` ответом 500, а не 409 словами (как у базы знаний
  3.1) — открытый вопрос; две правки одного шага — побеждает последняя, журнал покажет обе.
- **производительность** — `json.dumps` в `version_of` — отпечаток не больше трёх шагов цепочки; `json.loads` — файл
  загрузки не больше `MAX_BYTES` 1 МБ, читается в потоке (`asyncio.to_thread(chain_load.read, path, found)`), цикл событий
  не держит. `re.compile` — шаблоны модуля (`_REPLY`, `_LOOSE_HEADER`, `_BRACE`) собираются один раз; разбор тела
  (`zones_of`, `_BRACE.search`) — по строкам текста не длиннее `BODY_LENGTH` 10 000 знаков (гейт `cpu-in-async` пройден).
  `chain.rows` без страниц — в наборе не больше шести строк (ключ «набор, шаг, язык»: 3 шага × 2 языка), гейт
  `unbounded-list` пройден; `GET /api/sales/chain` — от 3 запросов к базе (общий набор: 2 языка и строки набора) до 6 (гипотеза: её проверка, по 1–2 на язык, строки).
- **интеграция** — риска нет, потому что срез никуда не ходит: `httpx` — только `AsyncClient` тестов API
  (`from httpx import AsyncClient` в `tests/test_sales_chain_api.py`); консоль читает локальный файл, сети нет.
- **новый модуль** — `chain.py`, `chain_text.py`, `chain_load.py`, `outside.py`, `chain_schemas.py`, API `chain.py`,
  миграции и файлы экрана (`ChainPane`, `ChainStepModal`, `ChainLetter`, `chainData`, `chainDraft`, `SaveRefusal`): правила —
  в ядре (`features/sales/`), экран, консоль и предпросмотр зовут одни `chain_text.step_template` и `unsigned`; покрытие
  изменённого — 100 % у ядра (кроме недостижимой строки 221 `chain_text.py`), API и миграций.
- **деньги** — риска нет, потому что денег в срезе нет: «offer» — имя выдуманной зоны в тестах (`[offer] fixed\nTest offer:
  nothing real is sold here.`), сумм и цен нет.
- **безопасность (правка 1.4)** — `backend/features/sales/verifier.py`: у несобранного запроса текст исключения httpx несёт заголовок `Authorization` с ключом Hunter; отказ теперь собран из `reason_of(exc)` и поднят `from None` — ключа нет ни в отказе, ни в журнале, ни в цепочке причин (`test_unsent_request_refusal_carries_no_key_or_cause`).

## Предохранитель

40 файлов, net 3988 (+4078/-90) при пределах 25 и 800 — **постоянный вейвер владельца для модульных срезов
продаж (05.10)** строкой `waivers:` в STATUS. Условия владельца соблюдены:

1. числа конкретные — из `delivery_check` после переноса;
2. общая часть — **111 строк** нетто при пределе ~300: миграции `723e3ddab31f` (+72) и `cbc5aadf4fc2` (+35),
   `backend/cli/main.py` (−12), `backend/features/core/domain.py` (+3), `backend/features/core/models/__init__.py` (+2),
   `tests/test_schema.py` (+2), `.secrets.baseline` (+10/−1); свои файлы раздела вне масок `SALES_PATHS`
   (`backend/api/sales/*`, `backend/cli/sales.py`, `backend/cli/sales_commands.py`, `frontend/src/api/sales*.ts`) —
   модуль продаж, не общая часть;
3. список общих файлов отправлен соседней сессии до PR и принят (05.10).

## Предупреждения delivery_check, разобранные

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `8dfe13d`):

```
breakers: files=40 net_loc=3988 (+4078/-90), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 40, 'max_loc_diff': 3988, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

- «`irreversible_surfaces:` не называет отправка наружу» — детектор видит отправку во всём продуктовом коде (письма
  доноров); строка подписана владельцем и перенесена дословно. Срез отправки не добавляет: шаблоны хранятся и правятся,
  письма продаж откроет часть 2 (4.6b) — переподпись тогда.
- «asserts_reviewed_by deferred» — 192 утверждений ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта; метаморфные отношения версии
  набора — примерами и мутантами M4a, M4b.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

Дословно (`contour_waves --base origin/main`, голова `8dfe13d`):

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

Волны В2–В4 — pending: их триггеров (промпт, пороги, автоотправка) в срезе нет. Общий код объявлен в `shared_changes:`
полными путями со словом «согласовано».

## Spec coverage gaps

- Подпись и адрес из «Отправителя» сверяются с шаблоном при записи шаблона: шаблон, записанный до того, как задали подпись,
  её текст может содержать (живой прогон это показал) — сборка 4.6b должна сверить ещё раз на готовом письме.
- Первый включённый свой шаг гипотезы переключает её язык на свою цепочку целиком: пока там нет трёх шагов, письма гипотезы
  на этом языке не соберутся. Экран это говорит («цепочка неполна»); заготовка — выключенными шагами. Подтвердить у владельца.
- Гонка двух записей одного нового шага — 500 вместо 409 словами (ключ держит база) — открытый вопрос.
- Письмо «вас посоветовал коллега» для `referral` — в хранении T1 варианта первого письма нет (открытый вопрос).
- `{{first_name}}` — нет: у лида только полное имя (открытый вопрос).
- C11 (контраст, рамка, наведение) — замером живьём, не тестом сьюта (нужен браузер и сервер).
- `check_ready` и версию пока никто не зовёт (4.6b) — «заложено».
- `GET /api/sales/chain?hypothesis=0` — 422 с текстом pydantic по-английски (`ge=1`), как у прочих маршрутов проекта; экран
  такого не шлёт.

## Находки (общий код — не чинил)

1. **Прозрачная шапка окна поверх содержимого при прокрутке.** `frontend/src/theme.ts:222` —
   `styles: { header: { background: 'transparent' } }` (#98, 25.09) при `position: sticky` шапки Mantine: в любом окне выше
   окна браузера заголовок и крестик ложатся поверх прокрученного текста и кнопок. Нарушает `docs/UI_RULES.md` («ничего
   прозрачного поверх содержимого»). Воспроизвести: «Продажи» → «Цепочка писем» → «Править» у первого письма → «Показать
   письмо» → прокрутить окно вниз (1440 × 900 или 390 × 844); снимки `modal-header-1440-light.png`, `modal-header-390-dark.png` рядом с черновиком.
   Решение — за владельцем общей темы (фон шапки — стекло окна, а не прозрачный).
2. **Крестик окна без доступного имени.** У `.mantine-Modal-close` нет `aria-label` (в теме нет `closeButtonProps`, в
   `frontend/src/` — ни одного): программа чтения экрана назовёт его просто «кнопка». Во всех окнах приложения.
3. Нумерация шагов: у шаблонов продаж 1–3, у `messages.step` доноров 0–2 (`letters/chain.py: FIRST_STEP = 0`) — сборке 4.6b
   переводить в одном месте (находка прошлого агента).

Находки общего кода переданы соседней сессии 05.10.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI и ОК соседней сессии на общие точки.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **188**, из них без ссылки на пример спеки:
**188**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(card.getByText('цепочка не задана')).toBeInTheDocument();
-	expect(
-	expect(card.getAllByText('не задан')).toHaveLength(3);
-	expect(card.getAllByRole('button', { name: 'Задать' })).toHaveLength(3);
-	expect(screen.getByText(/sales-chain-load --file/)).toBeInTheDocument();
-	expect(first.getByText('задан')).toBeInTheDocument();
-	expect(first.getByText('Тема: Test for {{company}}')).toBeInTheDocument();
-	expect(first.getByText('Hello {{name}}, · Test offer body.')).toBeInTheDocument();
-	expect(first.getByText(/seller@ours\.example\.test/)).toBeInTheDocument();
-	expect(within(stepOf('Английский', '3. Вторая добивка')).getByText('выключен')).toBeVisible();
-	expect(english.getByText('цепочка неполна')).toBeInTheDocument();
-	expect(
-	expect(screen.queryByText(/sales-chain-load/)).not.toBeInTheDocument();
-	expect(english.getByText('цепочка полна')).toBeInTheDocument();
-	expect(english.getByText('Версия chain-en0000000000.')).toBeInTheDocument();
-	() => expect(calls(recorded, 'GET', '/api/sales/chain?hypothesis=5')).toHaveLength(1),
-	expect(
-	expect(english.getAllByText('не задан')).toHaveLength(3);
-	expect(
-	expect(screen.getByText('База не ответила — повторите')).toBeInTheDocument();
-	await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/chain')).toHaveLength(1));
-	expect(calls(recorded, 'POST', '/api/sales/chain')[0]?.body).toEqual({
-	expect(calls(recorded, 'GET', '/api/sales/chain').length).toBeGreaterThan(before),
-	expect(await screen.findByText('Сохранено: Первое письмо · Английский')).toBeInTheDocument();
-	() => expect(calls(recorded, 'GET', '/api/sales/chain?hypothesis=5')).toHaveLength(1),
-	expect(dialog.queryByRole('textbox', { name: 'Тема' })).not.toBeInTheDocument();
-	await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/chain')).toHaveLength(1));
-	expect(calls(recorded, 'POST', '/api/sales/chain')[0]?.body).toEqual({
-	expect(within(alert.closest('[role="alert"]') as HTMLElement).getByText(refusal)).toBeVisible();
-	expect(subject).toHaveValue('Re: test');
-	expect(dialog.getByText('Впишите тему — у первого письма она обязательна')).toBeVisible();
-	expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
-	expect(dialog.getByRole('button', { name: 'Показать письмо' })).toBeDisabled();
-	expect(calls(recorded, 'POST', '/api/sales/chain/preview')[0]?.body).toEqual({
-	expect(letter.getByText('Test for Example Company')).toBeInTheDocument();
-	expect(letter.getByText('Hello Alex Example,')).toBeInTheDocument();
-	expect(letter.getByText('переписывает модель')).toBeInTheDocument();
-	expect(letter.getByText('уходит как есть')).toBeInTheDocument();
-	expect(letter.getByText(/Test lead/)).toBeInTheDocument();
-	expect(
-	expect(letter.getByText('Отправка продаж не готова')).toBeInTheDocument();
-	expect(
-	expect(dialog.queryByLabelText('Письмо глазами адресата')).not.toBeInTheDocument();
-	expect(await dialog.findByText('Письмо не собралось', {}, SCREEN_WAIT)).toBeVisible();
-	expect(dialog.getByText(refusal)).toBeVisible();
-	assert (made.step, made.language, made.subject) == (1, "en", "Test question for {{company}}")
-	assert made.body == FIRST_BODY
-	assert [(zone.name, zone.kind) for zone in made.zones] == [
-	assert f"начинается с «{prefix}»" in str(refused.value)
-	assert "обман адресата" in str(refused.value)
-	assert first(subject=subject).subject == subject
-	assert follow().subject is None
-	assert follow(step=3, subject="  ").subject is None
-	assert words in str(refused.value)
-	assert words in str(refused.value)
-	assert "{{name}}, {{company}}, {{site}}" in str(refused.value)
-	assert chain.version_of([first()]) == "chain-" + digest[:12]
-	assert chain.version_of([first(), follow()]) == chain.version_of([follow(), first()])
-	assert chain.version_of([first(), off]) == chain.version_of([first()])
-	assert chain.version_of([first(**changes)]) != chain.version_of([first()])
-	assert edited != kept
-	assert await chain.set_version(session, None, "en") == kept
-	assert versions == [{"было": kept, "стало": edited}, {"было": edited, "стало": kept}]
-	assert (found.hypothesis_id, found.language, sorted(found.steps)) == (None, "en", [1, 2, 3])
-	assert found.missing == []
-	assert (found.hypothesis_id, sorted(found.steps)) == (hypothesis_id, [1])
-	assert found.steps[1].subject == "Own test question"
-	assert found.missing == ["первой добивки", "второй добивки"]
-	assert str(refused.value) == (
-	assert (found.hypothesis_id, found.version) == (
-	assert (english.hypothesis_id, russian.hypothesis_id) == (hypothesis_id, None)
-	assert sorted(russian.steps) == [1, 2]
-	assert found.steps == {}
-	assert (sorted(found.steps), found.missing) == ([1, 2], ["второй добивки"])
-	assert (row.hypothesis_id, row.step, row.language, row.updated_by) == (
-	assert (row.subject, row.body, row.active) == (SUBJECT, FIRST_BODY, True)
-	assert await _journal(session) == [
-	assert details is not None
-	assert (details["поля"], details["было"], details["набор"]) == (
-	assert (row.active, row.updated_by) == (False, "другой")
-	assert (same.id, same.updated_by, len(await _journal(session))) == (row.id, "тест", 1)
-	assert await chain.rows(session, None) == []
-	assert listed == [(1, "en", True), (1, "ru", True), (2, "ru", False)]
-	assert [row.hypothesis_id for row in await chain.rows(session, hypothesis_id)] == [
-	assert shown.subject == "Test question for Example Company"
-	assert [zone.text for zone in shown.zones][:2] == [
-	assert shown.values == chain_text.SAMPLE["en"]
-	assert (shown.sender.values["signature"], shown.sender.missing) == (
-	assert chain_text.preview(follow(language="ru"), settings).subject is None
-	assert spec is not None, path
-	assert spec.loader is not None, path
-	assert (after_downgrade, after_upgrade) == ([], ["sales_chain_templates"])
-	assert (await connection.run_sync(_journal_values)).count("sales_chain_changed") == 1
-	assert response.status_code == 200, response.text
-	assert response.status_code == 200, response.text
-	assert (view["hypothesis_id"], view["rows"]) == (None, [])
-	assert _states(view) == {
-	assert (view["steps"], view["languages"]) == ([1, 2, 3], ["ru", "en"])
-	assert view["placeholders"] == ["name", "company", "site"]
-	assert view["limits"] == {"subject": 255, "body": 10_000}
-	assert {k: card[k] for k in ("hypothesis_id", "step", "language", "subject", "updated_by")} == {
-	assert [(zone["name"], zone["kind"]) for zone in card["zones"]] == [
-	assert [row["id"] for row in view["rows"]] == [card["id"]]
-	assert _states(view)["en"] == ("common", ["первой добивки", "второй добивки"])
-	assert list(authors) == [user.id]
-	assert after["en"] != before["en"]
-	assert after["ru"] == before["ru"]
-	assert after["en"] == chain.version_of(
-	assert (before["rows"], _states(before)["en"][0]) == ([], "common")
-	assert [row["subject"] for row in after["rows"]] == ["Own test"]
-	assert {lang: source for lang, (source, _) in _states(after).items()} == {
-	assert [row["hypothesis_id"] for row in (await _chain(client, headers))["rows"]] == [None]
-	assert response.status_code == code, response.text
-	assert words in response.json()["detail"]
-	assert await chain.rows(session, None) == []
-	assert response.status_code == 422
-	assert (response.status_code, response.json()["detail"]) == (
-	assert response.status_code == 200, response.text
-	assert shown["subject"] == "Test for Example Company"
-	assert shown["zones"][0]["text"] == "Hello Alex Example,"
-	assert (shown["sender_name"], shown["signature"], shown["address"], shown["missing"]) == (
-	assert shown["values"] == {
-	assert await chain.rows(session, None) == []
-	assert response.status_code == 200, response.text
-	assert (response.json()["subject"], response.json()["missing"]) == (
-	assert response.status_code == 400
-	assert "подпись из настроек отправителя" in response.json()["detail"]
-	assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
-	assert in_app == {(method, path) for method, path, _ in ROUTES}
-	assert problems == []
-	assert templates == [step_template(**FIRST), step_template(**SECOND), step_template(**THIRD)]
-	assert templates == [step_template(**FIRST)]
-	assert [(p.number, p.where) for p in problems] == [
-	assert "начинается с «Re:»" in reasons[0]
-	assert reasons[1:8] == [
-	assert "метрики Ahrefs" in reasons[8]
-	assert reasons[9] == "подстановки {{host}} нет; есть только {{name}}, {{company}}, {{site}}"
-	assert problems == [Problem(3, "шаг 1, en", "тот же шаг и язык, что у №1: в наборе он один")]
-	assert [p.reason for p in problems] == [
-	assert str(refused.value).startswith(words)
-	assert str(refused.value) == (
-	assert not inside.exists()
-	assert await _run(session, _file(tmp_path)) == EXIT_OK
-	assert capsys.readouterr().out == (
-	assert await _rows(session) == [
-	assert await _journal(session) == [
-	assert await _run(session, path) == EXIT_OK
-	assert "добавлено 0, без изменений 3, отличается 0" in capsys.readouterr().out
-	assert (len(await _rows(session)), len(await _journal(session))) == (3, 1)
-	assert await _run(session, _file(tmp_path, edited)) == EXIT_OK
-	assert "отличается 1 — не тронуто (перезаписать: --update)" in out
-	assert f"    №{row_id}: первое письмо, en\n" in out
-	assert "  в наборе, но не в файле: 1 — не тронуты\n" in out
-	assert (await chain.rows(session, None))[0].subject == "Test for {{company}}"
-	assert await _run(session, _file(tmp_path, edited), "--update") == EXIT_OK
-	assert (first_row.subject, first_row.updated_by) == ("Edited test subject", "консоль")
-	assert details is not None
-	assert (details["обновлено"], details["без изменений"]) == (1, 0)
-	assert await _run(session, _file(tmp_path), "--dry-run") == EXIT_OK
-	assert capsys.readouterr().out.endswith("Предпросмотр: в базу ничего не записано.\n")
-	assert (await _rows(session), await _journal(session)) == ([], [])
-	assert await _run(session, _file(tmp_path), "--hypothesis", " Тестовая  гипотеза ") == EXIT_OK
-	assert f"шаблонов 3, набор гипотезы №{owner.id}." in capsys.readouterr().out
-	assert {row[0] for row in await _rows(session)} == {owner.id}
-	assert (found.hypothesis_id, sorted(found.steps)) == (owner.id, [1, 2])
-	assert await _run(session, _file(tmp_path), "--hypothesis", "Нет такой") == EXIT_NO_HYPOTHESIS
-	assert capsys.readouterr().out == (
-	assert await _rows(session) == []
-	assert await _run(session, path) == EXIT_BAD_INPUT
-	assert capsys.readouterr().out == (
-	assert await _rows(session) == []
-	assert await _run(session, path) == EXIT_BAD_INPUT
-	assert "подпись из настроек отправителя" in capsys.readouterr().out
-	assert await _rows(session) == []
-	assert await _run(session, tmp_path / "нет.json") == EXIT_BAD_INPUT
-	assert capsys.readouterr().out.startswith(
-	assert main(["sales-chain-load", "--file", "цепочка.json"]) == EXIT_CANCELLED
-	assert "Цепочка писем пишется одной транзакцией: в базе ничего не осталось." in err
-	assert "домены остались" not in err
-	assert found is not None, f"в salesTypes.ts нет типа {name}"
-	assert found is not None, f"в salesLabels.ts нет таблицы {name}"
-	assert _union("ChainLanguage") == languages
-	assert _keys("CHAIN_LANGUAGES") == languages
-	assert _union("ChainPlaceholder") == placeholders
-	assert _keys("CHAIN_PLACEHOLDERS") == placeholders
-	assert _union("ZoneKind") == kinds
-	assert _keys("ZONE_KINDS") == kinds
-	assert {int(step): title.lower() for step, title in titles.items()} == chain_text.STEP_TITLES
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 188
