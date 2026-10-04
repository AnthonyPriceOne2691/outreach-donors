# Verify report

**Поставка:** срез `sales-leads-screen`, **часть 2 из 2 — 1.5b, раздел «Продажи» на экране**:
меню под правом `sales`, вкладки «Лиды» и «Гипотезы» с фильтрами в адресе, мастер загрузки
базы, замеры; дедупликация и разбиение функций под гейты дублей и сложности TS. Часть 1
(списки на сервере) — отдельный PR. Сверка кодов сервера со словами экрана — отдельным
маленьким PR после этой части (решение владельца 04.10).

**Date:** 2026-10-04
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; общие точки фронта — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=72 утверждений без примера спеки ждут подписи человека — слова экрана (подписи состояний и причин, тексты пустоты и отказов, названия шагов мастера), порядок групп в итоге загрузки, предел 300 строк отчёта, разбор отказа формы, номера страниц; человека в цепочке нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR; пре-пуш гоняет полный набор
**Commit:** T4 — прогоны сняты на дереве `4234c37` (T6); T4 меняет только документы среза

## Сборка

Ветка `sales/1.5b-screen` стеком на части 1 (`sales/1.5a-lists`, main `9bee976` + 1.5a); после
слияния части 1 переносится на main. Коммиты ветки среза `sales/1.5-screen` (T2…«T6»)
перенесены по одному без правки кода; тест `tests/test_api_sales_screen.py` оставлен версией
части 1 — сверка кодов со словами экрана (55 строк) уходит отдельным PR, чтобы часть
уложилась в одобренный вейвер. Код экрана совпадает с вершиной ветки среза побайтно (сверено
`git diff`). Коммиты: `5ad7a80` T0; `e53a5b2` T2; `574ebb5` WIP; `7067e1a` Починка; `c776cfd` T3; `0ee52d4` T4; `91faa17` WIP; `a68c0d4` T5; `4234c37` T6. Часть — 24 файла, +3 461 / −3 (net 3 458); миграций нет.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0 (в том числе гейты дублей и сложности TS).
- [x] PASS — `tsc --noEmit` exit 0; ESLint и Prettier — в хуках; гейт слоёв
      `check_layers_gate.sh` — exit 0 (ts — 123 модуля, import-linter — 4 контракта).
- [x] PASS — `ruff check backend/ tests/ scripts/` exit 0; `mypy backend/` — 315 файлов, ошибок нет;
      `scripts/gates.py` — 498 файлов, нарушений нет (в том числе `public-repo`); ратчет сложности —
      330 файлов, расхождений нет.
- [x] PASS — гейт дублей `check_jscpd_gate.sh`: 55 пар при снимке 55; гейт сложности TS
      `check_complexity_gate.sh` — OK (новым файлам 0 нарушений); ратчет снимков
      `check_baseline_ratchet.sh` — 15 снимков сверено с main, рост не нужен.
- [x] PASS — подпись необратимого: сходится; четыре строки STATUS перенесены слово в слово.
- [x] PASS — голова Alembic прежняя `2d9877260a6d` (миграций нет).
- [x] PASS — `contour_waves --base sales/1.5a-lists` (режим CI): нарушений нет; общие точки фронта
      и свои файлы раздела вне `SALES_PATHS` объявлены полными путями.
- [x] PASS — `delivery_check --require-ci --diff-base sales/1.5a-lists`: 0 errors (предупреждения
      ниже); предохранитель — 24 файла, net 3 458 при вейвере владельца 3 458.

## Гейты кода — до и после

### jscpd — 63 → 55 пар при снимке 55 (jscpd 5.4.0; снимок не пересобирался)

На `9983638`: TS 35 + python 28 = 63; на `192a4c2`: TS 27 + python 28 = 55; пар с файлами
продаж 8 → 0, новых пар нет. Python-половина не менялась (28 — та же, что у базы).

| Пара на `9983638` (токенов) | Как снята (`2c6c59b`) |
|---|---|
| `api/donors.ts:20-25` ↔ `api/sales.ts:34-39` (51) | строка запроса — чистая `searchOf` (фильтр и `URLSearchParams` из пар) |
| `forms/FormsPage.tsx:192-201` ↔ `sales/FixedTable.tsx:69-78` (64) | ширины колонок — компонент `Widths`, шапка — из той же колонки |
| `sales/ImportWizard.tsx:225-238` ↔ `sales/SalesPage.tsx:179-192` (85) | ворота «раздел не прочитан» — один `Pending` у экрана и у мастера |
| `sales/LeadsTable.tsx:264-271` ↔ `selection/SelectionTable.tsx:326-333` (62) | пустота: слова отдельно, действие — `EmptyAction` |
| `sales/SalesPage.tsx:39-44` ↔ `selection/SelectionPage.tsx:54-59` (58, импорты) | вкладка лидов с `PageSwitch` — свой файл `LeadsPane.tsx` |
| `sales/SalesPage.tsx:169-174` ↔ `selection/SelectionPage.tsx:208-214` (74) | возврат страницы «за концом» — чистая `pageBack`, эффект по её ответу |
| `sales/SalesPage.tsx:201-208` ↔ `selection/SelectionPage.tsx:252-259` (50) | вкладки — компонент `SalesTabs` |
| `sales/leadFilters.ts:61-69` ↔ `selection/selectionFilters.ts:86-94` (104) | номер из адреса — своя `numberOf` (снизу всегда единица); `known` в продажах — та же строка, что у отбора (40 токенов, ниже порога): у отбора не экспортирована |

### Сложность TS — 3 файла → 0 (предел 10, новым файлам разрешено 0)

| Файл | До (`9983638`) | После (`192a4c2`) |
|---|---|---|
| `frontend/src/api/sales.ts` | `refused` 13 | `refused` 4, `detailFrom` 9 |
| `frontend/src/sales/ImportWizard.tsx` | `ImportWizard` 16 (15 после `Pending` в `2c6c59b`) | `ImportWizard` 4, `WizardSteps` 7, `useImportWizard` 4, `nameOf` 3, `WizardHead` 1 |
| `frontend/src/sales/SalesPage.tsx` | `SalesPage` 19 | `SalesPage` 3, `SalesTabs` 2; в `LeadsPane.tsx`: `useLeads` 8, `LeadsPane` 5, `pageBack` 5 |

`check_complexity_gate.sh` без `--report` — `complexity: OK`; `--report` печатает и
старые нарушения чужих экранов (в снимке, не тронуты).

## Сверка экрана до/после — DOM и запросы

Как: временная обвязка vitest (вне `src`, не в коммите; конфиг и setup — в scratchpad
сессии) после каждого теста раздела пишет `document.body.innerHTML` и список вызовов
`fetch` (метод + адрес). Нормализованы: id и связанные атрибуты Mantine (`id`, `for`,
`name`, `aria-*`) — по порядку появления; шум анимаций (`data-resizing`, стили перехода,
монтаж значка шага); класс адаптивных стилей `__m__-_r_…_` (счётчик `useId`). Три прогона
одного кода совпали между собой — сверка детерминирована.

| Сверка | Состояний | Расхождений |
|---|---|---|
| `9983638` ↔ код «T5» `2c6c59b` (29 старых тестов) | 29 | 0 |
| старый код + новый тест «за концом» ↔ «T5» | 30 | 0 |
| старый код + новый тест ↔ «T6» `192a4c2` | 30 | 0 (три теста отказа формы DOM не рисуют) |

## Behavior oracles

- [x] PASS — vitest полный, `--maxWorkers=2`: **475 passed**, 51 файл, 46 с (это дерево);
      раздел — `SalesPage.test.tsx` 19 и `ImportWizard.test.tsx` 14 (33 passed): A1, A2 ×2,
      A4 (меню и адрес; адрес мастера), фильтры под колонками и на сервер, список причин из
      ответа, страницы и «страница за концом», пусто и отказ, сопоставление, заголовок,
      нулевой итог, отказ загрузки, «Загрузить ещё», отказ формы ×3.
- [x] Полный pytest — пре-пуш и CI на PR; строитель снял его на коде этой части: 3 576 passed за
      5 мин 19 с (тесты части 1 — 13 passed на этом дереве).

### Красный прогон до кода

| Старый код | Тесты | Итог |
|---|---|---|
| часть 1 (до T2) | `SalesPage.test.tsx` | файл не загружался — нет `./leadFilters` |
| T2 (до T3) | `ImportWizard.test.tsx` | файл не загружался: «Failed to resolve import "./importMapping"» |
| починка (до T3) | `ImportWizard.test.tsx` | 4 failed (маршрута мастера нет), 2 passed (чистые правила) |
| до «T5» | «страница из старой ссылки за концом» | зелёный по определению — страховка переноса; красным его делает мутант |
| до «T6» | три теста отказа формы | зелёные на старом `refused` — страховки; красными их делают мутанты |

### Обратные прогоны

Мастер, T3 (прежний агент; `reverse_runs_15.py`: одна подмена, свои тесты `src/sales`,
восстановление; дерево `87a416e`):

| Мутант | Файл | Итог | |
|---|---|---|---|
| заголовок всегда `true` при переключении | ImportWizard.tsx | 1 failed, 28 passed | убит |
| отказ загрузки не показан | ImportWizard.tsx | 1 failed, 28 passed | убит |
| «К отчёту» без колонки почты не выключается | ImportColumns.tsx | 1 failed, 28 passed | убит |
| `withField` не освобождает прежнее поле колонки | importMapping.ts | 2 failed, 27 passed | убит |
| замечание названо отказом | ImportReport.tsx | 1 failed, 28 passed | убит |
| «Загрузить ещё» не сбрасывает файл | ImportWizard.tsx | 1 failed, 28 passed | убит |
| итог группирует замечания вместо отказов | ImportReport.tsx | 1 failed, 28 passed | убит |
| адрес мастера без права | App.tsx | 1 failed, 28 passed | убит |
| устаревший ответ предпросмотра перебивает выбор | ImportWizard.tsx | 0 failed, 29 passed | **выжил** — заглушка `serve()` отвечает мгновенно и по порядку, порядок ответов не подделать |
| смена источника не обнуляет прочитанное | ImportWizard.tsx | 0 failed, 29 passed | **выжил** — правило дублирует запрет Mantine выбирать следующие шаги (`allowNextStepsSelect={false}`); второй засов без наблюдаемого поведения |

Страховки «T5»–«T6» (эта сессия; одна подмена, тест, восстановление — `diff` с копией пуст):

| Мутант | Файл | Итог | |
|---|---|---|---|
| `pageBack` никогда не возвращает страницу | LeadsPane.tsx | 1 failed | убит |
| список причин отказа формы не разбирается | api/sales.ts | 1 failed, 2 passed | убит |
| 401 не сбрасывает пропуск | api/sales.ts | 1 failed, 2 passed | убит |
| тело не JSON роняет разбор (`.catch` снят) | api/sales.ts | 1 failed, 2 passed | убит |

**T3: 10 мутантов — 8 убито, 2 выжило** (оба названы в plan.md «что заложено»);
**«T5»–«T6»: 4 мутанта — 4 убито.**

## Замеры (A3) — живьём, свой сервер

После «T5»–«T6» не переснимались: разметка экрана не изменилась (сверка DOM выше,
30 из 30 состояний побайтно), CSS не тронут. Числа ниже — замер кода `8e7d93b`.

Как: своя база `outreach_live_sales_e` (схема `alembic upgrade head`, голова `2d9877260a6d`),
учётка `ui@sales.example.test` (пароль сменён через `POST /api/auth/password`), гипотезы
«сайты EN» и «сервисы RU», файл `leads_live.csv` на 100 строк (две без адреса, одна
«ivan at acme», дубли, `privacy@`, `info@gmail.com`, `bounce.box@`, реальные домены с MX и
`*.example.test` без MX). API `uvicorn backend.api.main:app --port 8100`, фронт `npm run dev`
(5173). Playwright — extra `browser` (`uv sync --extra browser`, Chromium 1243); Pillow для
`ui_contrast.py` — `uv run --no-sync --with pillow` (в зависимостях проекта нет).
После загрузок — `outreach sales-clean` (fixture, DNS машины): проверено 194, готово 28,
отклонено 166 (дубль 99, негодный адрес 5, домен не принимает почту 61, адрес не
существует 1); плюс 9 новых через `sales-import`. Снимки и логи — рядом: `shots/`,
`contrast-*.log`, `hover.log`.

### Контраст — `ui_contrast.py --screen …` (норма 4,5 обычный / 3,0 крупный), после правок T4

| Экран | Тема | Точек | Минимум | Мало / не измерено | exit |
|---|---|---|---|---|---|
| `sales` (лиды, 1440) | light | 23 | 5,11 (кнопка «Загрузить базу», норма 3,0); текст — от 6,17 | — | 0 |
| | dark | 23 | 4,89 (номер текущей страницы) | — | |
| `sales-phone` (лиды, 390) | light | 6 | 6,31 (выбранная вкладка) | — | 0 |
| | dark | 6 | 7,54 (заголовок, норма 3,0) | — | |
| `sales-hypotheses` | light | 6 | 4,82 (число-ссылка) | — | 0 |
| | dark | 6 | 6,94 | — | |
| `sales-import` (источник) | light | 11 | 4,75 («К продажам») | — | 0 |
| | dark | 11 | 4,91 («К продажам») | — | |
| `sales-import-columns` | light | 9 | 4,78 (кнопка «К источнику», норма 3,0); текст — от 5,29 | — | 0 |
| | dark | 9 | 6,10 | — | |
| `sales-import-report` | light | 13 | 4,78 (кнопка «К колонкам», норма 3,0); текст — от 5,31 | — | 0 |
| | dark | 13 | 6,10 | — | |

До правок T4 (первый замер): значок «строка отклонена» контурный красный — **4,23** (light)
/ 5,15 (dark); значение поля гипотезы — **1,32** (light) при читаемом по снимку тексте
(слепое пятно просторного поля; проба снята с комментарием, те же стили — имя файла 6,09 / 7,30);
пробы вкладок без `.glassPanel` мерили переключатель тем в меню (на телефоне —
«одна краска»). Значки `light`: 10,33 / 8,44 (лиды), 10,39 / 7,74 (отчёт).

### Наведение — `ui_hover.py --path …` (`hover.log`)

| Путь | Наведено | Сдвинулось | Не проверено | exit |
|---|---|---|---|---|
| `/sales` | 81 | 0 | 0 | 0 |
| `/sales?tab=hypotheses` | 28 | 0 | 0 | 0 |
| `/sales/import` | 25 | 0 | 0 | 0 |

### Узкое окно 390 px — документ и прокрутка (`scrollWidth / clientWidth`, Playwright)

| Экран | Документ / окно | Контейнеры таблиц |
|---|---|---|
| `/sales` | 390 / 390 | 1184 / 316, `overflow-x: auto` |
| `/sales?tab=hypotheses` | 390 / 390 | 912 / 316 |
| `/sales/import` колонки | 390 / 390 | 720 / 284 |
| `/sales/import` отчёт | 390 / 390 | 1008 / 284 и 760 / 284 |

Рамка цела на всех; до правки T4 блок «заголовок + пояснение» рядом с кнопкой сжимался
до половины ширины (снимок), после — 284 px из 284 на лидах и в шапке шага колонок.

### Снимки (`shots/`, 2× масштаб, вся страница)

`leads-{1440,390}-{light,dark}`, `leads-filtered-1440-light`, `hypotheses-{1440,390}-{light,dark}`,
`import-1-source-*`, `import-2-columns-*`, `import-3-report-*` (четыре сочетания),
`import-4-outcome-1440-light` и `-390-dark` (после загрузки; 1440-light — переснят после
правки значков: «Загружено 9, отклонено 1.» на малом файле). Снимки шагов 1440-light,
390-dark, 390-light сняты до правки значков — на них контурный значок.

### Живой прогон A1 и A2 (`scratchpad/live_wizard.py`, `live_leads.py`)

- A1: файл на 100 строк → колонки угаданы (почта, имя целиком, компания, сайт компании,
  страна, должность) → плитки «100 / 97 / 3 / 46» → «Загрузить 97 лидов» → **«Загружено 97,
  отклонено 3.»**; причины: «нет адреса — 2 · строки 7, 12», «не адрес почты — 1 · строка 20»;
  замечания по группам (пояс по столице 20/16/8, сайт не разобран 1, страна не распознана 1);
  «К лидам» → `/sales?hypothesis=1`. В обеих темах на 1440 и 390 (вторая загрузка — в
  гипотезу №2 → `?hypothesis=2`).
- A2: `/sales?state=rejected&reason=duplicate` → поля «отклонён» / «дубль», 20 строк, все
  значки причины — «дубль»; после `reload` — те же значения и тот же адрес.

## Product oracles

- [x] PASS — `eval-smoke.md`: пункты части отмечены; живой прогон на настоящей базе — заложено.

## Ревью рисковых мест

- **безопасность** — пункт меню и оба маршрута раздела под `RequireAccess permission="sales"`:
  без права пункта нет, прямой адрес — «раздел недоступен», к серверу продаж экран не ходит
  (тесты A4). Форма загрузки уходит своим `fetch` на свой сервер с пропуском в заголовке; 401
  сбрасывает пропуск, 403 называет действие (тесты отказа формы ×3, мутанты убиты). Ссылка на
  таблицу — строкой формы, сервер берёт её только с `docs.google.com` (1.3c). Чужой ввод из
  адреса (`?state=…&reason=…&page=…`) разбирается строго (`leadFilters.ts`: незнакомое — пусто,
  номер — целый и не меньше единицы).
- **интеграция** — клиент `frontend/src/api/sales.ts` зовёт только свои маршруты (списки части 1
  и загрузку 1.3c); отказ сервера — строкой в таблице или у шага мастера, словами сервера.
- **производительность** — лиды по 20 со страницей в адресе, запрос на смену фильтра один;
  отчёт мастера — первые 300 строк и «Показать все»; гонка ответов предпросмотра прикрыта
  номером запроса (тестом не покрыта — выживший мутант, разобран выше).
- **новый модуль** — `frontend/src/sales/` (экран, мастер, каркас таблиц, ворота): правило
  depcruise держит экраны почты от импорта продаж (гейт слоёв зелёный); сложность каждой
  функции ≤ 10, пар дублей с файлами продаж нет.
- **транзакция БД** — риска нет: экран не пишет в базу сам, загрузка — та же `intake.load` 1.3c.
- **деньги** — риска нет: трат и провайдеров экран не зовёт.

## Предохранитель

24 файла, net 3 458 при пределах 25 файлов и 800 строк — **вейвер владельца** строкой
`waivers:` в STATUS (решение 04.10: срез двумя PR; гейты кода зелёные только на головах после
части 1 и в конце). Файлы — в пределе.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана владельцем и
  перенесена дословно; детектор ищет имена отправителей по всему продуктовому python, а не
  по диффу; часть внешних поверхностей не открывает.
- «asserts_reviewed_by deferred» — 72 утверждений ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — fast-check и hypothesis не в зависимостях проекта.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет. Общие точки и свои
файлы раздела объявлены — проверка зелёная.

## Spec coverage gaps

- Сверка кодов сервера с типами и подписями экрана — отдельным маленьким PR после этой части
  (решение владельца 04.10).
- Разделитель CSV на экране, заведение гипотезы с экрана, клавиатура (UI_RULES п. 3) — не в
  срезе.

## Находки (не чинились — владельцам)

1. **Экспорт у отбора** — `frontend/src/selection/selectionFilters.ts:86` (`known`) и `:90`
   (`wholeNumber`) не экспортированы. Продажи держат копию `known` (3 строки, 40 токенов —
   ниже порога jscpd) и свою `numberOf`. Нужно: слово `export` в двух местах — продажи
   импортируют обе (слой разрешает: продажи → экраны почты).
2. **Экспорт у клиента** — `frontend/src/api/client.ts:96` (`raise`) и `:111` (`withPass`) не
   экспортированы, а `request` умеет только JSON. Форма загрузки (`multipart/form-data`) уходит
   своим `fetch` в `frontend/src/api/sales.ts` с копией правила отказа (`refused` +
   `detailFrom`: 401 — пропуск сброшен, 403 — действие) и сборки пропуска. Нужно: `export` в
   двух местах (или общий `sendForm` в `client.ts` отдельным PR) — тогда ~30 строк продаж уходят.
3. **Строка запроса — четыре копии**: `frontend/src/api/runs.ts:49-53`,
   `frontend/src/api/donors.ts:21-25`, `frontend/src/api/selection.ts:33-37` и `searchOf` в
   продажах. Пара `api/donors.ts:20-26` ↔ `api/runs.ts:48-54` (60 токенов) уже сидит в базовой
   линии jscpd. Общий помощник в `client.ts` снял бы её (снимок 55 → 54) — отдельным общим PR.
4. **Пустая таблица словами — четыре копии**: `frontend/src/donors/DonorsTable.tsx:419`,
   `frontend/src/threads/ThreadsPage.tsx:326`, `frontend/src/selection/SelectionTable.tsx:326`,
   продажи (`LeadsTable.tsx`, `Empty`). Кандидат на общий компонент в `components/`.
5. **`frontend/src/styles/glass.css`** — правило 25.09 подтягивает к чернилам только
   `.mantine-Badge-root[data-variant='light']`; контурный красный значок на светлом стекле —
   4,23 : 1 (воспроизвести: `Badge variant="outline" color="red"` на `glassPanel`, светлая
   тема, `ui_contrast.py`). Контурные значки `JudgeVerdict.tsx:43,58,64`, `CandidateRow.tsx:73`,
   `SuppressionsPage.tsx:274` не мерились. В срезе — значки переведены в `light`.
6. **`scripts/ui_contrast.py`** — просторное поле с коротким значением (560 px, два слова)
   меряется как «заливка поля против полотна» (1,32 при читаемом тексте); предел метода,
   записано в UI_RULES.
7. **`scripts/contour_waves.py` `SALES_PATHS`** не покрывают `backend/api/sales/*`,
   `frontend/src/api/sales*.ts`, `tests/test_api_sales_*.py`: свои файлы раздела считаются
   общим кодом и требуют объявления (вопрос 1.2).
9. **Живой прогон** — dev-база `outreach` стоит на `b7d3e9a15c42` (без таблиц продаж); живые
   прогоны продаж — только на своей базе (`outreach_live_sales_e`; снести — `DROP DATABASE`).

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI, ревью общих точек фронта соседней сессией и вейверу
владельца; PR — после слияния части 1.

## Assertion digest (ревью ожиданий, не кода)

База: `sales/1.5a-lists` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **97**, из них без ссылки на пример спеки:
**72**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A1	expect(fileOf(previewForm, 'file').name).toBe('leads.csv');
A1	expect(textOf(previewForm, 'mapping')).toBeNull();
A1	expect(await screen.findByText('Станут лидами', {}, SCREEN_WAIT)).toBeInTheDocument();
A1	expect(within(report).getAllByText('строка отклонена')).toHaveLength(3);
A1	expect(within(report).getAllByText('загружен с замечанием')).toHaveLength(2);
A1	expect(within(report).getByText('ivan at acme')).toBeInTheDocument();
A1	expect(
A1	expect(screen.getByText('нет адреса — 2')).toBeInTheDocument();
A1	expect(screen.getByText('не адрес почты — 1')).toBeInTheDocument();
A1	expect(screen.getByText(/строки 7, 12/)).toBeInTheDocument();
A1	expect(textOf(loadForm, 'hypothesis_id')).toBe('1');
A1	expect(mappingSent(loadForm)).toEqual(PREVIEW.mapping);
A1	expect(fileOf(loadForm, 'file').name).toBe('leads.csv');
A1	expect(screen.getByRole('link', { name: 'К лидам' })).toHaveAttribute(
-	expect(field).toHaveValue('сайт компании');
-	() => expect(formsSentTo('/api/sales/import/preview')).toHaveLength(2),
-	expect(mappingSent(again)).toEqual({ email: 0, name: 1, company: 2, country: 3 });
-	expect(textOf(again, 'header')).toBe('true');
-	expect(screen.getByText(/Колонка почты не найдена/)).toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'К отчёту' })).toBeDisabled();
-	expect(screen.getByRole('switch', { name: 'Первая строка — заголовок' })).not.toBeChecked();
-	expect(await screen.findByText(closed, {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(textOf(form, 'link')).toBe('https://docs.google.com/spreadsheets/d/abc/edit');
-	expect(form?.get('file')).toBeNull();
-	expect(screen.getByRole('button', { name: 'Прочитать' })).toBeInTheDocument();
-	expect(toggle).toBeChecked();
-	() => expect(formsSentTo('/api/sales/import/preview')).toHaveLength(2),
-	expect(textOf(again, 'header')).toBe('false');
-	expect(mappingSent(again)).toEqual(PREVIEW.mapping);
-	expect(
-	expect(await screen.findByText(gone, {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'Загрузить 97 лидов' })).toBeInTheDocument();
-	expect(screen.queryByText(/Загружено/)).not.toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'Прочитать' })).toBeDisabled();
-	expect(screen.getByText('Выберите файл.')).toBeInTheDocument();
-	expect(screen.getByRole('textbox', { name: 'Гипотеза' })).toHaveValue('сайты EN');
A4	expect(refusal.closest('[role="alert"]')).toHaveTextContent('«раздел продаж» не выдано');
A4	expect(recorded.calls.map((call) => call.path)).toEqual(['/api/auth/me']);
-	expect(withField(mapping, 2, 'name')).toEqual({ email: 0, name: 2 });
-	expect(withField(mapping, 1, null)).toEqual({ email: 0 });
-	expect(withField(mapping, 1, 'company')).toEqual({ email: 0, company: 1 });
-	expect(groupRejections(PREVIEW.problems)).toEqual([
-	expect(failure).toBeInstanceOf(ApiError);
-	expect(failure).toHaveProperty('message', 'нет ни файла, ни ссылки');
-	expect(failure).toHaveProperty('status', 422);
-	expect(await refusalOn({ status: 422, body: { detail: [] } })).toHaveProperty(
-	expect(await refusalOn({ status: 502, raw: '<html>шлюз</html>' })).toHaveProperty(
-	expect(expired).toBeInstanceOf(AuthError);
-	expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
-	expect(denied).toBeInstanceOf(DeniedError);
-	expect(denied).toHaveProperty('message', 'Действие «sales» недоступно этой учётке');
A4	expect(screen.queryByText('Продажи')).not.toBeInTheDocument();
A4	expect(refusal.closest('[role="alert"]')).toHaveTextContent('«раздел продаж» не выдано');
A4	expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
-	expect(screen.getAllByText('Продажи')).toHaveLength(2);
-	expect(screen.getByRole('heading', { name: 'Продажи' })).toBeInTheDocument();
-	expect(ivan.getByText('Иван Петров · редактор')).toBeInTheDocument();
-	expect(ivan.getByText('Acme')).toBeInTheDocument();
-	expect(ivan.getByRole('link', { name: 'acme.example.test' })).toHaveAttribute(
-	expect(ivan.getByText('сайты EN')).toBeInTheDocument();
-	expect(ivan.getByText('Германия')).toBeInTheDocument();
-	expect(ivan.getByText('новый')).toBeInTheDocument();
-	expect(twin.getByText('отклонён')).toBeInTheDocument();
-	expect(twin.getByText('дубль')).toBeInTheDocument();
-	expect(twin.getByText('дубль: адрес уже у лида №1')).toBeInTheDocument();
-	expect(screen.getByText('Лиды — 5')).toBeInTheDocument();
-	expect(screen.getByText('Гипотезы — 2')).toBeInTheDocument();
-	expect(screen.getByRole('link', { name: 'Загрузить базу' })).toHaveAttribute(
-	expect(within(head).getByRole('textbox', { name })).toBeInTheDocument();
A2	expect(asked(recorded)).toEqual(['state=rejected&reason=duplicate']);
A2	expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('отклонён');
A2	expect(screen.getByRole('textbox', { name: 'Причина отказа' })).toHaveValue('дубль');
A2	expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
A2	expect(writeLeadFilters(readLeadFilters(new URLSearchParams(address))).toString()).toBe(
A2	expect(writeLeadFilters(readLeadFilters(new URLSearchParams(full))).toString()).toBe(full);
-	expect(asked(recorded)).toEqual(['']);
-	expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('все');
-	expect(asked(recorded)).toEqual(['state=new']);
-	expect(screen.queryByRole('textbox', { name: 'Причина отказа' })).toBeNull();
-	await waitFor(() => expect(asked(recorded).at(-1)).toBe('state=ready'), SCREEN_WAIT);
-	expect(options).toEqual([
-	await waitFor(() => expect(asked(recorded).at(-1)).toBe('hypothesis=2'), SCREEN_WAIT);
-	expect(await screen.findByText('Под фильтр ничего не попало.', {}, SCREEN_WAIT)).toBeTruthy();
-	expect(screen.getByText('Условия: гипотеза «сервисы RU». Всего лидов — 5.')).toBeTruthy();
-	await waitFor(() => expect(asked(recorded).at(-1)).toBe('search=acme'), SCREEN_WAIT);
-	expect(asked(recorded).filter((query) => query.includes('search='))).toEqual(['search=acme']);
-	expect(within(pages).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
-	expect(asked(recorded)).toEqual(['', 'page=2']);
-	expect(asked(recorded)).toEqual(['page=9', 'page=3']);
-	expect(screen.queryByText('Под фильтр ничего не попало.')).toBeNull();
-	expect(
-	expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('готов');
-	expect(screen.getByText(/Загрузите базу/)).toBeInTheDocument();
-	expect(row.getByRole('link', { name: '1' })).toHaveAttribute(
-	expect(row.getByText('5')).toBeInTheDocument();
-	expect(screen.queryByText('ivan@acme.example.test')).not.toBeInTheDocument();
-	expect(screen.getByText(/sales-hypothesis-add/)).toBeInTheDocument();
```

Привязаны к примерам: **A1 A2 A4**. Остальные 72 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 72
