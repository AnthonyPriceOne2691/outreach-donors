# Verify report

**Поставка:** модуль «Продажи» одним PR — срез 4.6b, модульная часть (очередь продаж и отправка цепочки: диалог с лидом,
подключение, письмо, сборка, API, консоль, вкладка «Очередь писем»; модуль отвечает мосту почты по его договору) и
срез 5.4 (воронка продаж по гипотезам и периоду). Обе части перенесены на голову стопки: main с мостом почты (договор
#211/#216), окнами получателя и лимитами (#219); разбор ответов продаж (Ф2); передача лида (5.3).

**Date:** 2026-10-08
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов частей без ссылки на пример спеки в теле (пример назван именем теста, id S/F — в спеке); подписывает ревьюер или владелец при ревью, дайджест — в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `9235098` — голова PR (голова части 5.4), ветка `sales/4.6b-5.4-0710` (не запушена); база PR — голова стопки
словами выше. Головы частей: 4.6b `3daa0ae` (восемь коммитов), 5.4 `9235098` (двенадцать). Перенос — cherry-pick по
порядку, конфликты по смыслу; временные копии Ф2 из ветки 5.4 не перенесены.

## Shape oracles (на головах частей)

| Проверка | 4.6b `3daa0ae` | 5.4 `9235098` (голова PR) |
|---|---|---|
| `mypy backend` (strict; модуль `sales/mail.py` сверяется с протоколом `SalesMail` вместе с `policy`) | 0 (411 файлов) | 0 (413) |
| `ruff check` / `ruff format --check` | 0 / 0 | 0 / 0 |
| `scripts/gates.py --commits <база части>` (public-repo — и по сообщениям коммитов) | 0 (696 файлов, 8 сообщений) | 0 (702 файла, 12 сообщений); против базы PR — 0 (20 сообщений) |
| `lint-imports` (4 контракта, `mail-does-not-know-sales` — kept) | 0 | 0 |
| `scripts/complexity.py` (ратчет) | 0 (429 файлов) | 0 (432) |
| гейт дублей jscpd | 0 (55 пар = снимок) | 0 (55) |
| `alembic heads` (одна) | `fa927869a835` | `fa927869a835` |
| шаг образа (`docker_step_local.py`) | 0 | 0 |
| `tsc --noEmit` / ESLint / Prettier (раздел продаж и его клиент) | 0 / 0 / 0 | 0 / 0 / 0 |
| хуки pre-commit на коммитах | Passed — коммиты с конфликтами шли через `git commit`; перенесённые без конфликтов — `pre-commit run --from-ref … --to-ref …` по их файлам | Passed — так же |
| `pre-commit run --all-files` | — | 27 хуков Passed, правок нет, 52 с — 0 (нагрузка 5,1; чужих полных прогонов не было — дождался конца чужого `pytest --cov`) |

## Behavior oracles

| Проверка | 4.6b `3daa0ae` | 5.4 `9235098` |
|---|---|---|
| тесты части и соседей (все `tests/test_sales_*`, `test_letters*`, `test_followups`, `test_sending_limits`, `test_api_outreach`, `test_overview`, `test_schema`, `test_prune*`, `test_migrations_match_models`, окно, сторож, учётки, исход задачи, консоль, сторож прохода, входящие, передача) | 74 файла, 1567 passed — 0 | в полном прогоне |
| полный pytest `--cov=backend`, явный список `tests/` (264 файла) | — | **5348 passed за 8:42 — 0** (нагрузка на старте 3,9; чужих `pytest --cov` не было) |
| vitest `--maxWorkers=2 --reporter=dot src/sales src/letters` | 12 файлов, 139 — 0 | 13 файлов, 153 — 0 |
| diff-coverage `STRICT=1` против базы (отчёт полного прогона) | — | 0: 21 изменённый файл, наименьшее — `sales/referral.py` 87,9 %, `replies/outcome.py` 92,2 %, `sales/mail.py` 94,7 %, прочие ≥ 97 % |

**Полный pytest — честно о порядке.** Первый полный прогон стартовал на прежней голове 5.4 (нагрузка 3,9, чужих
`--cov` нет) и был остановлен мной на 52 %: (1) пока он шёл, я запустил в том же дереве один точечный тест, а сеанс
pytest пересоздаёт схему базы дерева при старте (`tests/conftest.py::_schema`) — полный прогон мог сломаться не по
коду; (2) после его старта нашёлся стык с пачкой базы (число на кнопке), и код менялся. Полный прогон, который дошёл
до конца, — один: на коде с обеими доводками (`494e24c` до перестановки коммитов), 5348 passed. Затем две доводки 4.6b
переставлены в свою часть до 5.4 cherry-pick'ом; дерево головы `9235098` совпадает с проверенным байт в байт (хэш
дерева `93d13899478e`), поэтому результат относится к голове PR; diff-coverage снят на том же коммите, что и
покрытие.

**Красный прогон новых тестов** (на коде до своей правки): `test_followup_that_cannot_be_built_waits_and_is_not_a_module_failure`
— 1 failed («ошибка модуля продаж» в журнале); `test_letter_whose_outcome_is_unknown_is_not_sent_yet` — 1 failed (своя
копия «ушло»); `test_followup_stuck_in_the_queue_is_not_in_the_number_of_the_batch` — `(2, 2) != (1, 1)`;
`test_referral_in_a_dialog_of_the_queue_finds_its_lead_by_the_link` — «лид исходного диалога не найден — завести
лида … руками»; тесты окна и поясов (`test_sales_send_zones.py`) — красные без `policy` и `zones` (мутанты P1, Z1 —
состояние до правки переноса); `test_sales_reason_does_not_say_waits_twice` — красный на прежней причине (W1).

**Мутанты переписанных при переносе мест — 18 из 18 убиты** (правка в файл, прогон тестов, откат, sha256 сверен):

| # | Мутант | Чем убит |
|---|---|---|
| P1 | `policy` модуля — нынешняя политика (ни окна, ни сигналов, ни сторожа) | `test_sales_send_zones.py` |
| P2 | `policy` без окна | `test_sales_send_zones.py` |
| Z1 | пояса получателя почте не отданы | `test_sales_send.py` (письма ждут «пояс неизвестен»), `test_sales_send_zones.py` |
| Z2 | пояс лида не отдан — только страны | `test_window_counts_the_zone_of_the_lead_then_of_his_country[пояс лида раньше страны]` |
| C1 | несобираемая добивка — `NotReadyError`, как до переноса | `test_followup_that_cannot_be_built_waits_and_is_not_a_module_failure` |
| C2 | «подключены ли» коммитит внутри ответа моста | `test_module_answers_neither_commit_nor_roll_back[connected]` |
| C3 | проверка письма откатывает сессию внутри ответа | `test_module_answers_neither_commit_nor_roll_back[check]` |
| H1 | отправка не спрашивает шов `handed_off` | `test_lead_handed_off_by_the_handoff_entry_gets_no_more_letters` |
| H2 | передача ищет лида без явной связи | то же (точка входа 5.3 по диалогу без `contacts`) |
| B1 | пачка вкладки — этапом доноров | vitest «уходит этапом продаж после подтверждения…» |
| B2 | число на кнопке — письма гипотезы, а не этапа | vitest (5 тестов) |
| B3 | кнопка пачки открыта при неподключённых продажах | vitest «не подключены: … кнопки закрыты» |
| B4 | кнопка пачки без права отправки | vitest «без права на отправку кнопки пачки нет» |
| W1 | причина ответа продаж снова с «ждёт» | `test_sales_reason_does_not_say_waits_twice` |
| G1 | воронка считает «отправляется» ушедшим | `test_letter_whose_outcome_is_unknown_is_not_sent_yet` |
| Q1 | число на кнопке — все письма этапа в очереди, и добивки | `test_followup_stuck_in_the_queue_is_not_in_the_number_of_the_batch` |
| Q2 | «писем гипотезы в очереди» — и добивки | то же |
| R1 | лид исходного диалога — только по адресу контакта (как в 2.3a) | `test_referral_in_a_dialog_of_the_queue_finds_its_lead_by_the_link` |

Мутанты неизменённого кода частей — прежние таблицы частей: 4.6b — 22 из 22 на ветке-образце и 8 из 8 на разрезе
(адрес из `contacts`, ключ без контакта, письмо без физического адреса, добивка с темой, продажи без своей учётки,
сроки без подключения, регистрация в мосту, пересборка ушедшего письма и др.); 5.4 — 22 из 23 убиты, один
эквивалентный (отказ в признаке доставки).

## Исполнение рисковых путей

На голове `9235098`, at=2026-10-07T21:38Z, без базы и Redis:

- Задача сборки очереди продаж — импорт строкой пути, как берёт воркер: `backend.features.sales.queue_jobs.build_sales_queue`
  → функция `build_sales_queue`, имя задачи на экране задач — «сборка очереди продаж»; exit 0. Ставится в общую
  очередь `runs` прежнего воркера (`api/sales/queue.py`), не в очередь `sales` Ф2.
- Модуль продаж подключён к мосту в процессе, который грузит модели (как API, воркер и консоль): `sales_registered()`
  — `True`, у модуля `mail` есть все пять ответов протокола (`recipient`, `check`, `connected`, `followup`, `policy`);
  exit 0.
- Консоль: `outreach sales-queue --help` — exit 0; без `--hypothesis` — отказ разбора, exit 2 (до базы).
- Compose и образ против базы не тронуты (`docker-compose.yml`, `docker-compose.prod.yml`, `Dockerfile` — диффа нет).
- Цикл миграции `sales_threads` (вниз и вверх) — тестом на базе дерева
  (`tests/test_sales_send_model.py::test_downgrade_drops_the_table_and_upgrade_runs_again`, в полном прогоне).
- Сам воркер и живая задача не поднимались: общий Redis — воркер забрал бы чужие задачи. Живых писем нет.

## Живой прогон

При переносе стенд не поднимался. Вкладки «Очередь писем» и «Воронка» мерены живьём на образцах до переноса (обе
темы, 1440 и 390, клавиатура, контраст — все точки в норме; находки исправлены). После переноса на вкладке очереди
кнопка пачки — общая кнопка почты (подпись короче: «Отправить очередь · N»); её замер на 390 px на этой вкладке —
«заложено».

## Product oracles

Нет: продукт меряется на живых письмах продаж, а их до переподписи и ключей нет.

## Ревью рисковых мест

- **Безопасность — кому и чьей учёткой.** Адрес — лида по явной связи (не `contacts`); без своего ключа учётки продаж
  письмо не уходит. Мост отказывает до выбора учётки этапа. Держат прежние мутанты 4.6b (M1, M5) и контракт
  `mail-does-not-know-sales`.
- **Договор моста.** Модуль отвечает в точке сохранения моста: ни `commit`, ни `rollback` внутри ответа (C2, C3);
  отказ — только словами почты, добивка — «пока нельзя» (C1). Нарушение договора не роняет проход добивок, пачку и
  кнопку, но молча становится «не подключены» с трассой в журнале — поэтому договор держат тесты со стороны модуля.
- **Окно получателя.** Модуль отдаёт пояса лида и страны (Z1, Z2) и окно из настроек продаж (P1, P2). Пояса нет —
  письмо ждёт словами, не теряется; добивка вне окна — открытие окна плюс сдвиг, не «час».
- **Пачка.** Одна кнопка на экране — общая, этап `sales` (B1); число на ней — первые письма всех гипотез, как берёт
  пачка базы (Q1, Q2); добивка, застрявшая без своего ящика, уходит только проходом добивок со своего ящика и с
  `In-Reply-To`.
- **Транзакции БД.** Сборка фиксирует каждое письмо отдельно, пересборка — только письма ещё в очереди (прежний M10);
  передача 5.3 и «пишите другому» коммитят в разборе ответа, а не в ответе моста.
- **Стыки с Ф2 и 5.3.** «Пишите другому» в диалоге сборки — лид по связи (R1); передача — лид по связи (H2); переданному
  лиду письма не идут (H1). Провода «хочет говорить» → передача в базе нет — см. находки.
- **Сторож почты продаж (4.5b).** С регистрацией модуля политика продаж включает сторож (`watch=True`) — см. находки.
- **Необратимое.** PR открывает письма продаж живым людям: первые — по нажатию (пачкой или по одному), добивки — без
  человека. Предохранители: подключение (выключатель, своя учётка, отписка, «Отправитель»), коридор, повторная сверка,
  стоп-листы, передача, окно получателя, лимиты доменов и направления (#219). Строка `irreversible_surfaces:` — к
  переподписи владельцем до первой отправки продаж.
- **Интеграция.** Новых внешних вызовов нет: модель — прежним клиентом доноров, почта — общим транспортом этапа,
  Telegram и Kommo не тронуты; воронка только читает базу.
- **Деньги.** Риска нет, потому что денег PR не двигает: маячок подняли слова «счёт» (подсчёт воронки — `features/sales/funnel.py`, «двойной счёт» в тесте разреза периода) и «балансировщик» (общий выбор ящика `sender_rules.pick`); цен, платежей и счетов в диффе нет. Расход на модель — прежний: сборка зовёт общий клиент модели на каждое письмо под потолком расхода (`usage.ensure_llm_within_cap` до каждого письма, тест потолка части 4.6b); воронка модель не зовёт.
- **Производительность.** Счётчики вкладки — четыре запроса `count`; воронка — один запрос пути по письмам продаж
  (индекс `messages.thread_id`); на тысячах лидов — материализовать путь (не нужно для MVP).

## Предохранитель

`delivery_check --require-ci --diff-base <база>` в клоне с черновиками в `delivery/active/` (четыре строки — из STATUS
базы): **`breakers: files=54 net_loc=6497 (+6534/-37), excluded=1`** — две ошибки предохранителя (54 > 25, 6497 > 800)
до строки вейвера; предупреждения — необратимое без названия отправки продаж (к переподписи владельцем),
`asserts_reviewed_by` deferred, нет `@given` и блока `agent-permissions` (общее для проекта); exit 1 только по
предохранителю. `contour_waves --base <база>` в том же клоне — нарушений нет, exit 0; подпись необратимого
(`check_irreversible_signature.sh`) — сходится, строка не тронута, exit 0.

| Часть | files | net (+/−) | из них общая часть (вне масок продаж) |
|---|---|---|---|
| 4.6b (голова `3daa0ae`) | 44 | 4346 (+4380/−34) | 14 файлов, 318 (+326/−8) |
| 5.4 (голова `9235098`) | 17 | 2151 (+2161/−10) | 5 файлов, 177 (+181/−4) |
| весь PR | 54 | 6497 (+6534/−37) | 16 файлов, 495 (+503/−8) |

Модульные срезы продаж — постоянный вейвер владельца; части объединены одним PR его словом 07.10; строку `waivers:`
вписывает координатор. Длина файлов — в пределе 500 (`sales/queue.py` 410, `sales/handoff.py` 456, `sales/models.py`
431, `sales/cleaning.py` 493 — как в базе).

## Spec coverage gaps

- S5 (A5) — гейтом `public-repo` и grep, не тестом.
- Живьём на голове PR — ничего: стенд не поднимался; замер общей кнопки пачки на вкладке очереди на 390 px — заложено.
- Сдвиг открытия окна проверен одним зерном генератора (как в тестах 4.3).

## Находки (общий код — не чинил)

1. **Сторож почты продаж при выключенных продажах** (`backend/features/ops/mail_watch.py` + политика модуля): сторож
   смотрит `policy.watch`, а не «подключены». Продажи выключили после писем — добивки ждут, и «ящик молчит» /
   «отправить некому» поднимутся тревогой. Предложение — решение владельца: `watch` только у подключённых продаж
   (одна строка в `sales/mail.policy`) или правило в сторожа.
2. **Провод «хочет говорить» → `handoff.start`** в разборе ответа продаж: в базе стоит пометка
   (`backend/features/sales/replies.py`, `mark_for_handoff`). Передача и остановка цепочки по ней сработают, когда
   провод 5.3 ↔ Ф2 будет в main.
3. **Отказ общей пачки без перечня**: `backend/features/core/stages.py::check_connected` отказывает «продажи к почте ещё
   не подключены» без того, чего не хватает (модуль знает: `connection.missing`). Перечень говорит вкладка очереди.
4. **Устаревшие слова «почта продажи ещё не ведёт»** в общих местах: `backend/api/errors.py:113`,
   `backend/api/letters/routes.py:247`, `backend/features/replies/stage_rules.py:5`, `backend/api/threads/schemas.py:180`,
   `frontend/src/threads/ThreadPage.tsx:175`, фикстура `frontend/src/threads/ThreadPageSales.test.tsx:18`.
5. **Сеанс pytest пересоздаёт схему базы дерева** (`tests/conftest.py::_schema`): два прогона в одном дереве ломают
   друг друга; урок процесса.
6. Прежние находки 4.6b — в силе, не перепроверял: признак «своя учётка» — имя переменной ключа; `UnknownHypothesisError`
   не «повтор не поможет»; карточка диалога продаж без `contacts` может не показать адрес собеседника; отписка лида
   закрывает домен компании для всех этапов. Находка «застрявшая добивка уходит пачкой» закрыта базой (пачка — только
   первые письма).

## Verdict

Готово к ревью. До слияния — у координатора: четыре строки из STATUS main дословно, строка вейвера на размер,
перенос на main после слияния Ф2 и 5.3 (`rebase --onto`), переподпись необратимого владельцем до первой отправки
продаж.

## Assertion digest (ревью ожиданий, не кода)

База: голова стопки (словами в начале отчёта) · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **358**, из них без ссылки на пример спеки:
**284**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A4	expect(tile('В очереди')).toEqual(['В очереди', '3', 'ещё ничего не ушло']);
A4	expect(tile('Отправлено')).toEqual(['Отправлено', '14', 'лидов, не писем']);
A4	expect(tile('Доставлено')).toEqual(['Доставлено', '12', '85,7% от отправленных']);
A4	expect(tile('Отказ')).toEqual(['Отказ', '2', '14,3% от отправленных']);
A4	expect(tile('Ответ')).toEqual(['Ответ', '4', '28,6% от отправленных']);
A4	expect(tile('Лид передан')).toEqual(['Лид передан', '1', '25% от ответивших']);
A4	expect(texts).toEqual([
-	await waitFor(() => expect(calls(recorded, `${FUNNEL}?hypothesis=8`)).toHaveLength(1));
-	await waitFor(() => expect(tile('Отправлено')[1]).toBe('1'), SCREEN_WAIT);
-	expect(screen.queryByRole('table', { name: 'Воронка по гипотезам' })).toBeNull();
-	expect(tile('Лид передан')).toEqual(['Лид передан', '0', 'нет ответивших']);
-	await waitFor(() => expect(calls(recorded, `${FUNNEL}?${since}`)).toHaveLength(1));
-	await waitFor(() => expect(calls(recorded, `${FUNNEL}?${from}`)).toHaveLength(1));
-	expect(
-	expect(recorded.calls.length).toBe(before);
-	await waitFor(() => expect(calls(recorded, `${FUNNEL}?${asked}`)).toHaveLength(1));
-	expect(recorded.calls.slice(before).map((call) => call.path)).toEqual([`${FUNNEL}?${asked}`]);
-	expect(
-	expect(tile('Доставлено')).toEqual(['Доставлено', '0', 'нет отправленных']);
-	expect(period).toHaveAttribute('data-orientation', 'vertical');
-	expect(period).toHaveAttribute('data-full-width');
-	expect(alert).toHaveTextContent('гипотезы №8 нет — обновите список гипотез');
-	expect(alert).toHaveTextContent('гипотезы №5 нет — обновите список гипотез');
-	expect(funnelQuery(NO_FUNNEL_FILTERS, NOW)).toEqual({});
-	expect(funnelQuery({ ...NO_FUNNEL_FILTERS, period: 'month', hypothesis: 5 }, NOW)).toEqual({
-	expect(funnelQuery({ ...custom, from: '2026-10-01', to: '2026-10-07' }, NOW)).toEqual({
-	expect(funnelQuery({ ...custom, to: '2026-10-07' }, NOW)).toEqual({
-	expect(periodProblem({ ...custom, from: '2026-10-07', to: '2026-10-07' })).toBeNull();
-	expect(periodProblem({ ...custom, from: '2026-10-08', to: '2026-10-07' })).toBe(
-	expect(stepHint(FIRST, 'handed_off')).toBe('25% от ответивших');
-	expect(stepHint(counts({ answered: 3 }), 'answered')).toBe('нет отправленных');
-	expect(screen.getByText('продажи подключены')).toBeInTheDocument();
-	expect(
-	expect(
-	expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
-	expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeEnabled();
-	expect(
-	expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
-	expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeDisabled();
-	await waitFor(() => expect(calls(recorded, 'POST', QUEUE)).toHaveLength(1), SCREEN_WAIT);
-	expect(calls(recorded, 'POST', QUEUE)[0]?.body).toEqual({ hypothesis_id: 5, limit: 17 });
-	expect(
-	() => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=5`).length).toBeGreaterThan(1),
-	expect(alert).toHaveTextContent(refusal);
-	expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
-	expect(
-	() => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=8`)).toHaveLength(1),
-	expect(await screen.findByText('11', {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(
-	expect(
-	expect(within(dialog).getByText(/В очереди 7 писем/)).toBeTruthy();
-	expect(calls(recorded, 'POST', '/api/letters/send-queue')[0]?.body).toEqual({
-	expect(
-	expect(await screen.findByText(refusal, {}, SCREEN_WAIT)).toBeInTheDocument();
-	expect(calls(recorded, 'POST', '/api/letters/send-queue')).toEqual([]);
-	expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
-	expect(screen.queryByRole('button', { name: /Отправить очередь/ })).not.toBeInTheDocument();
-	expect(
A1	assert (letters, total.sent, total.delivered) == (3, 1, 1)
A2	assert (total.sent, total.answered) == (1, answered)
A2	assert (await _total(session)).answered == 1
A3	assert (total.sent, total.delivered, total.bounced) == (2, 1, 1)
A3	assert await funnel.leads(session, Step.BOUNCED, ALL) == [gone.lead_id]
A3	assert await funnel.leads(session, Step.DELIVERED, ALL) == [reached.lead_id]
A3	assert (total.sent, total.delivered, total.bounced) == (1, 0, 1)
-	assert (total.queued, total.sent, total.delivered, total.bounced) == (0, 1, 0, 0)
-	assert funnel.GONE is GONE_STATUSES
-	assert (total.queued, total.sent) == (0, 0)
-	assert (total.queued, total.sent) == (1, 1)
-	assert await funnel.leads(session, Step.QUEUED, ALL) == [waiting.lead_id]
-	assert await funnel.leads(session, Step.SENT, ALL) == [written.lead_id]
-	assert await funnel.leads(session, Step.QUEUED, week) == [fresh.lead_id]
-	assert (await _total(session)).queued == 2
-	assert (found.sent, found.delivered, found.answered) == (1, 1, 1)
-	assert await funnel.leads(session, Step.SENT, week) == [inside.lead_id]
-	assert await funnel.leads(session, Step.SENT, before) == [earlier.lead_id]
-	assert await funnel.leads(session, Step.SENT, period) == [at_start.lead_id]
-	assert await funnel.leads(session, Step.SENT, after) == [at_end.lead_id]
-	assert await funnel.leads(session, Step.SENT, week) == [lead.lead_id]
-	assert (total.queued, total.sent, total.delivered, total.bounced, total.answered) == (
-	assert (total.answered, total.handed_off) == (2, 1)
-	assert await funnel.leads(session, Step.HANDED_OFF, ALL) == [warm.lead_id]
-	assert await handoff.handed_off(session, warm.lead_id) is True
-	assert await handoff.handed_off(session, cold.lead_id) is False
-	assert [(row.hypothesis_id, row.name) for row in found.rows] == [
-	assert [row.funnel for row in found.rows] == [
-	assert found.total == Funnel(queued=1, sent=2, delivered=1, bounced=1)
-	assert [row.hypothesis_id for row in narrowed.rows] == [second]
-	assert narrowed.total == Funnel(queued=1)
-	assert (await _total(session)).sent == 1
-	assert len(await funnel.leads(session, step, ALL)) == getattr(total, step.value), step
-	assert total == Funnel(queued=1, sent=3, delivered=1, bounced=1, answered=1, handed_off=1)
-	assert whole == await _total(session)
-	assert before + after == whole, f"разрез {days} сут. назад"
-	assert whole.delivered + whole.bounced <= whole.sent
-	assert max(whole.answered, whole.handed_off) <= whole.sent
-	assert min(whole.sent, whole.queued, whole.answered) > 0
A4	assert response.status_code == 200, response.text
A4	assert list(shown) == wanted
A4	assert shown == {of: expected.get(of, _zero()) for of in wanted}
A4	assert body["total"] == {step: sum(row[step] for row in shown.values()) for step in STEPS}
A4	assert all(shown[first][step] > 0 for step in STEPS)
A4	assert rows[first] == {
A4	assert rows[second] == {
A4	assert body["total"]["sent"] == 7
-	assert (body["hypothesis_id"], body["until"]) == (first, None)
-	assert datetime.fromisoformat(body["since"]) == since
-	assert (response.status_code, response.json()["detail"]) == (422, EMPTY_PERIOD)
-	assert response.status_code == 422
-	assert (response.status_code, response.json()["detail"]) == (
-	assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
-	assert found is not None, f"в salesTypes.ts нет интерфейса {name}"
-	assert _screen_fields("SalesFunnelView") == set(SalesFunnelView.model_fields)
-	assert _screen_fields("FunnelRow") == set(FunnelRow.model_fields)
-	assert _screen_fields("FunnelCounts") == set(FunnelCounts.model_fields) == set(STEPS)
-	assert in_app == {("GET", FUNNEL)}
-	assert seen == []
-	assert (report.sent, report.postponed) == (0, 1)
-	assert first.next_action_at == FIRST_DUE + followups.POSTPONE
-	assert f"Добивка шага 1 в переписке №{first.thread_id}: не собрана — " in caplog.text
-	assert "подстановка без значения" in caplog.text
-	assert "ошибка модуля продаж" not in caplog.text
T3	assert found is not None
A1	assert (report.prepared, report.refreshed, report.off_corridor) == (1, 0, 0)
A1	assert report.tokens_spent == 37
A1	assert letter.status is MessageStatus.QUEUED
A1	assert letter.step == 0
A1	assert letter.subject == "A made-up question for Example Test Co"
A1	assert not letter.subject.lower().startswith(("re:", "fwd:", "fw:"))
A1	assert letter.body is not None
A1	assert letter.body.endswith(f"Made-up test offer: nothing real is sold here.{SIGNED}")
A1	assert letter.body.count(w.SIGNATURE) == 1
A1	assert letter.uniqueness_pct is not None
A1	assert in_corridor(letter.uniqueness_pct)
A1	assert letter.idempotency_key == f"sales:acme.example.test:{JANE}:0"
A1	assert letter.contact_id is None
A1	assert letter.domain_id == jane.domain_id
A1	assert [about.host for about in rewriter.seen] == ["acme.example.test"]
A1	assert (link.lead_id, link.chain_hypothesis_id, link.language, link.chain_version) == (
A1	assert thread is not None
A1	assert campaign is not None
A1	assert thread.contact_id is None
A1	assert (campaign.stage, campaign.name, campaign.followup_days) == (
-	assert letter.subject == "Выдуманный вопрос для Бета"
-	assert letter.body is not None
-	assert "Выдуманное тестовое предложение" in letter.body
-	assert (await _link(session, letter)).language == "ru"
-	assert letter.contact_id is None
-	assert (await _link(session, letter)).lead_id == jane.id
A2	assert str(refused.value).startswith(
A2	assert (campaigns, await _letters(session)) == (0, [])
A3	assert report.prepared == 2
A3	assert [letter.idempotency_key for letter in letters] == [
A3	assert len({letter.thread_id for letter in letters}) == 2
A3	assert len({letter.domain_id for letter in letters}) == 1
A4	assert (report.prepared, report.off_corridor) == (0, 1)
A4	assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})
A4	assert await _letters(session) == []
A4	assert await session.scalar(select(func.count()).select_from(SalesThreadModel)) == 0
-	assert report.prepared == 0
-	assert report.waiting == Counter(
-	assert report.prepared == 1
-	assert report.waiting == Counter({"цепочка на языке ru задана не целиком": 1})
-	assert letter.idempotency_key.endswith(f"{JANE}:0")
-	assert (report.prepared, sum(report.waiting.values())) == (0, 0)
-	assert letter.subject == "A made-up question for Example Test Co"
-	assert (await _link(session, letter)).chain_hypothesis_id == world.hypothesis_id
-	assert (report.prepared, report.refreshed) == (0, 0)
-	assert report.waiting == Counter({queue.UP_TO_DATE: 1})
-	assert len(await _letters(session)) == 1
-	assert (report.prepared, report.refreshed) == (0, 1)
-	assert after.id == before.id
-	assert after.body is not None
-	assert after.body.endswith(f"\n\nMira Testova\nAnother Made-up Agency\n\n{w.ADDRESS}")
-	assert report.refreshed == 1
-	assert letter.subject == "Another made-up question"
-	assert (await _link(session, letter)).chain_version == common.version
-	assert (report.prepared, report.refreshed) == (0, 0)
-	assert report.waiting == Counter({queue.SENT_MEANWHILE: 1})
-	assert tuple(stored.one()) == sent
-	assert report.prepared == 0
-	assert count == 1
-	assert problem.startswith("подпись из настроек отправителя стоит и в тексте письма")
-	assert (report.prepared, report.waiting) == (1, Counter({queue.NO_LANGUAGE: 1}))
-	assert (report.prepared, report.tokens_spent, len(rewriter.seen)) == (2, 74, 2)
-	assert report.stopped is not None
-	assert report.stopped.startswith("потолок расхода на модель за день достигнут: 74 из 53")
-	assert len(await _letters(session)) == 2
-	assert code == 0
-	assert "новых писем: 1, собрано заново: 0" in out
-	assert f"ждут — {queue.NO_LANGUAGE}: 1" in out
-	assert "Ничего не отправлено" in out
-	assert code == EXIT_NO_HYPOTHESIS
-	assert "Гипотезы «Нет такой» нет" in capsys.readouterr().out
-	assert code == 5
-	assert "Гипотезы «Нет такой гипотезы» нет" in capsys.readouterr().out
-	assert response.status_code == 200, response.text
-	assert (body["connected"], body["missing"], body["unwritten"], body["queued"]) == (
-	assert (body["stage_queued"], body["limit_max"]) == (3, 200)
-	assert [item["language"] for item in body["chains"]] == ["ru", "en"]
-	assert body["chains"][1] == {
-	assert first is not None
-	assert stuck.postponed == 1
-	assert (body["queued"], body["stage_queued"]) == (1, 1)
-	assert (report.sent, report.left) == (1, 0)
-	assert body["connected"] is False
-	assert body["missing"] == [
-	assert [item["missing"] for item in body["chains"]] == [
-	assert (response.status_code, response.json()["detail"]) == (
-	assert response.status_code == 200, response.text
-	assert response.json() == {"job_id": "job-7"}
-	assert args == (queue_jobs.QUEUE_JOB, world.hypothesis_id, 17)
-	assert journal is not None
-	assert journal.details == {
-	assert response.status_code == 409
-	assert response.json()["detail"] == (
-	assert jobs.enqueued == []
-	assert response.status_code == 422
-	assert jobs.enqueued == []
-	assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
-	assert found is not None, f"в salesTypes.ts нет интерфейса {name}"
-	assert _screen_fields("SalesQueueView") == set(SalesQueueView.model_fields)
-	assert _screen_fields("SalesQueueBody") == set(SalesQueueBody.model_fields)
-	assert _screen_fields("SalesQueueReport") == set(queue.QueueReport(campaign_id=1).as_dict())
-	assert in_app == {("GET", QUEUE), ("POST", QUEUE)}
-	assert job_outcome.KINDS[queue_jobs.QUEUE_JOB] == "сборка очереди продаж"
-	assert result == {
-	assert seen == [(5, 10)]
-	assert (result["campaign_id"], result["prepared"]) == (3, 2)
L63	assert len(seen) == 2
L63	assert seen[0] == seen[1] == {"модель": RewriteClient, "hypothesis_id": hypothesis, "limit": 17}
-	assert [lead.id for lead in found] == [jane.id, olga.id]
-	assert set(letters) == {None}
-	assert thread is not None
-	assert thread.contact_id is None  # строки `contacts` у диалога продаж нет
-	assert referred.lead_id is not None, referred.words
-	assert new is not None
-	assert (new.source, new.status) == (LeadSource.REFERRAL, LeadStatus.READY)
-	assert (new.hypothesis_id, new.domain_id) == (origin.hypothesis_id, origin.domain_id)
-	assert (new.referred_from_thread_id, new.timezone) == (thread.id, origin.timezone)
-	assert thread.status is ThreadStatus.CLOSED
A1	assert source.asked == ["sales"]
A1	assert outcome.sender_email == w.SALES_BOX
A1	assert (outgoing.to, outgoing.from_email, outgoing.from_name) == (
A1	assert outgoing.from_name != outreach_cfg.SENDER_NAME
A1	assert outgoing.subject == "A made-up question for Example Test Co"
A1	assert outgoing.body.endswith(SIGNED)
A1	assert outgoing.unsubscribe_url.startswith("https://unsub.example.test/u/u")
A1	assert outgoing.in_reply_to is None
A1	assert letter.status is MessageStatus.SENT
A1	assert letter.sender_id == world.sales_box.id
A1	assert letter.next_action_at == w.NOW + timedelta(days=3)
-	assert [outgoing.to for outgoing in _seen(source)] == [JANE]
-	assert source.asked == []
-	assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)
A2	assert str(refused.value).startswith(
A2	assert source.asked == []
A2	assert letter.status is MessageStatus.QUEUED
-	assert _seen(source) == []
-	assert letter.status is MessageStatus.QUEUED
A4	assert link is not None
A4	assert found is not None
-	assert letter.status is MessageStatus.QUEUED
A3	assert (report.sent, dict(report.refused), report.stopped, report.left) == (2, {}, None, 0)
A3	assert sorted(outgoing.to for outgoing in _seen(source)) == [JANE, OLGA]
A3	assert len({letter.idempotency_key for letter in letters}) == 2
-	assert (report.sent, dict(report.refused)) == (1, {"стоп-лист": 1})
-	assert [outgoing.to for outgoing in _seen(source)] == [OLGA]
-	assert (report.sent, report.waiting, report.postponed) == (1, 0, 0)
-	assert outgoing.subject == first.subject
-	assert outgoing.in_reply_to == first.internet_message_id
-	assert (outgoing.to, outgoing.from_email) == (JANE, w.SALES_BOX)
-	assert outgoing.body == f"A made-up first reminder for Jane.{SIGNED}"
-	assert step is not None
-	assert step.thread_id == first.thread_id
-	assert step.idempotency_key == f"sales:acme.example.test:{JANE}:1"
-	assert step.next_action_at == FIRST_DUE + timedelta(days=5)
-	assert report.sent == 1
-	assert last.body == f"A made-up last reminder about Example Test Co.{SIGNED}"
-	assert step is not None
-	assert step.idempotency_key == f"sales:acme.example.test:{JANE}:2"
-	assert step.next_action_at is None
-	assert report.sent == 2
-	assert list(keys) == [f"sales:acme.example.test:{JANE}:1", f"sales:acme.example.test:{OLGA}:1"]
-	assert (report.sent, report.stopped) == (0, 1)
-	assert len(_seen(source)) == 1
-	assert step is not None
-	assert step.status is MessageStatus.STOPPED
-	assert first.next_action_at is None
-	assert (report.sent, report.waiting) == (0, 1)
-	assert first.next_action_at == w.NOW + timedelta(days=3)
-	assert _seen(source)[-1].body == f"A made-up first reminder for Jane.{SIGNED}"
-	assert (first_try.postponed, second_try.sent) == (1, 1)
-	assert _seen(source)[-1].body.endswith(
-	assert response.status_code == 200, response.text
-	assert response.json()["sender_email"] == w.SALES_BOX
-	assert response.status_code == 409
-	assert response.json()["detail"] == (
-	assert response.status_code == 200, response.text
-	assert response.json()["queued"] == 2
-	assert [args[1] for args in jobs.enqueued] == ["sales"]
-	assert SenderModel.__tablename__ == "senders"
-	assert await connection.run_sync(_down_and_up) == (False, True)
-	assert found is not None
-	assert (found.lead_id, found.chain_hypothesis_id, found.language, found.chain_version) == (
-	assert await session.scalar(select(SalesThreadModel.thread_id)) is None
-	assert said == [
-	assert await connection.missing(session) == []
-	assert await mail.connected(session) is True
-	assert await stages.sales_connected(session) is True
-	assert str(refused.value) == (
-	assert len(said) == 1
-	assert said[0].startswith(words)
-	assert await connection.missing(session) == [f"не задан физический адрес — {sender.WHERE}"]
-	assert [letter.template_step(step) for step in (0, 1, 2)] == [1, 2, 3]
-	assert body == f"Hello Jane,\n\nA made-up text.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}"
-	assert letter.problem(body, _sender()) is None
-	assert problem is not None
-	assert words in problem
-	assert letter.problem(body, _sender(physical_address=None)) == (
-	assert letter.lacking(steps, values) == ["{{company}}"]
-	assert letter.lacking(steps, values | {"company": "Acme"}) == []
-	assert jane == "sales:acme.example.test:jane@acme.example.test:0"
-	assert jane != olga
-	assert MessageModel.__table__.c.idempotency_key.type.length == letter.KEY_LENGTH
-	assert await mail.stopped_by(session, jane, "acme.example.test", now=w.NOW) == (
-	assert await mail.stopped_by(session, olga, "beta.example.test", now=w.NOW) == (
-	assert await mail.stopped_by(session, ivan, "gamma.example.test", now=w.NOW) == (
-	assert await mail.stopped_by(session, petr, "delta.example.test", now=w.NOW) is None
-	assert await mail.stopped_by(session, jane, "acme.example.test", now=w.NOW) is None
-	assert stages._SALES.load is not None
-	assert stages._SALES.load() is mail
-	assert second.status is status
-	assert found.window == WEEKDAYS_9_17
-	assert found.soft is not None
-	assert found.soft.complaints == sales_cfg.COMPLAINT_PAUSE
-	assert found.watch is True
-	assert [outgoing.to for outgoing in _seen(source)] == ([JANE] if goes else [])
-	assert _seen(source) == []
-	assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert "вне окна получателя (пн–пт 09:00–17:00 по его часам)" in str(late.value)
-	assert "уйдёт не раньше пн 19.10 09:" in str(late.value)
-	assert _seen(source) == []
-	assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert (report.sent, report.postponed) == (0, 1)
-	assert first.next_action_at == MONDAY_NINE + shift
-	assert later.sent == 1
-	assert _seen(source)[-1].in_reply_to == first.internet_message_id
-	assert jane.thread_id is not None
-	assert (report.sent, report.stopped) == (1, 1)
-	assert [outgoing.to for outgoing in _seen(source)] == [JANE, OLGA, OLGA]
-	assert jane.next_action_at is None
-	assert str(refused.value).startswith(f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — ")
-	assert detail.startswith(f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — ")
-	assert report.stopped is not None
-	assert report.stopped.startswith(f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED} — ")
-	assert (SALES_NOT_CONNECTED in caplog.text) is not registered
-	assert "ждёт" not in SALES_WAITING.lower()
```

Привязаны к примерам: **A1 A2 A3 A4 L63 T3**. Остальные 284 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 284

## Проверки на голове PR

Ветка перенесена на main `b7280d3`; голова кода `428e680`, проверки — по одному разу. pytest (2127 passed), diff-coverage, pre-commit и фронт прошли на голове ветки поверх головы #221 (`1434886` — её дерево и есть main после сквоша, код среза тот же); после них — правки «сборка — в очередь продаж» (`6b226de`: тесты API очереди продаж, отправки и исхода задач — 59 passed) и «сторож почты продаж — только у подключённых продаж» по ревью (`ee11ef4`: тесты окна, договора, моста и сторожа почты — 84 passed), обе на старом коде красные; после переноса на main — быстрые проверки таблицы и pre-push проекта. Полный pytest на голове ветки у агента — 5348 passed; полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | fa927869a835 (head) | 0 |
| ратчет сложности | Ратчет сложности: 432 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 2127 passed in 188.43s (0:03:08) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=54 net_loc=6526 (+6563/-37)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 288` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  254 passed (254) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `fbd4aa2`):

```
breakers: files=54 net_loc=6526 (+6563/-37), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 54, 'max_loc_diff': 6526, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `fbd4aa2`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а deployed · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 39 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
