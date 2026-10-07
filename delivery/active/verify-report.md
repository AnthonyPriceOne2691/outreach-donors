# Verify report

**Поставка:** срез 5.1 `sales-approval`, части «А», «Б» и «В» одним PR — решения по черновику агента на сервере шва
(409 «черновик устарел», «как есть» у отданного человеку закрыт и правкой прежним текстом, вид этапа, причины
отклонения и почему автопилот не действует — из реестра); плашка черновика в переписке, подсказка у поля ответа,
этапы экрана настроек из реестра; предупреждение «автопилот выбран, но письма сами не уходят».

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов частей — в дайджесте в конце отчёта; подписывает ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `0042387` — голова PR (голова части «В»), ветка `sales/3.0-agent-seam-0710` на main `9ee3d9e` (#210; не
запушена); база PR — голова шва (часть «Д») `c7ccc8f`. Головы частей: А `8b3ab4c`, Б `827d87f`, В `0042387`. Перенос
с ветки `sales/5.1-approval` (голова на момент переноса — `026ead5`, на вершине шва `bd27d6f`); правка координатора в
А3 (маршрут ответа; PR #214, голова `bd77ba7`) в этой ветке не стоит — сводит координатор при сборке: пробная накладка
ветки на `bd77ba7` конфликтует в `backend/api/threads/routes.py` и снимке сложности.

## Shape oracles (на головах частей, после переноса)

| Проверка | А `8b3ab4c` | Б `827d87f` | В `0042387` |
|---|---|---|---|
| `mypy backend` (strict) | 0 | 0 | 0 |
| `scripts/gates.py` | 0 | 0 | 0 |
| `scripts/complexity.py` | 0 | 0 | 0 |
| шаг образа (`docker_step_local.py`) | 0 | 0 | 0 |
| `alembic heads` (одна) | `75242c2ed7ba` | `75242c2ed7ba` | `75242c2ed7ba` |
| хуки pre-commit на коммитах (ruff, mypy, ESLint, Prettier, длина файлов, jscpd, гейты) | Passed | Passed | Passed |
| typecheck фронта на каждом коммите переноса | 0 | 0 | 0 |
| тесты шва, соседей и `tests/test_agent_approval.py` | 49 файлов, 987 passed | 49 файлов, 987 passed | 49 файлов, 987 passed |
| vitest `src/agent src/threads --maxWorkers=2 --reporter=dot` | — | 11 файлов, 94 теста (`98cebcf`, тот же фронт) | 11 файлов, 96 тестов |
| `delivery_check --diff-base` (строка `breakers:`) против предыдущей головы | 7 / 416 (+432/−16) | 8 / 779 (+818/−39) | 3 / 56 (+65/−9) |

На голове части «Б» `827d87f` и всего PR:

| Проверка | Итог | exit |
|---|---|---|
| полный pytest `--cov=backend` на `827d87f` (чужих полных прогонов нет, на старте нагрузка ≈4) | **4851 passed** за 13:31 | 0 |
| `delivery_check --diff-base c7ccc8f` (строка `breakers:`) — весь PR | файлов 15, net 1251 (+1314/−63) — сверх предела по строкам, вейвер — координатор | — |

На ветке 5.1 до переноса (агент 5.1, вершина `026ead5` на `bd27d6f`): полный pytest — 4549 passed; diff-coverage
`BASE=bd27d6f STRICT=1` — `backend/features/agent/drafts.py` 94,7 %, `stages.py` 98,9 %, маршруты и схемы — 100 %;
`pre-commit run --all-files` — 27 хуков Passed; vitest `src/agent src/threads src/api src/components src/letters` —
19 файлов, 182 теста; `contour_waves` и `delivery_check` по частям — нарушений нет, 0 errors; подпись необратимого —
сходится; `lint-imports` — 4 контракта целы.

## Behavior oracles

- [x] PASS — тесты частей и соседей на каждой голове (таблица выше).
- [x] **А** — красный прогон до кода: «устарел» — 2 failed, 1 passed (граница с автоответчиком держится и на старом
  коде), exit 1; прежний текст у `escalated` — 1 failed, 3 passed, exit 1; реестр и причины — модуль тестов не
  собирается (`REJECT_REASONS`), exit 2; слова отказа автопилота — 1 failed, 6 passed, exit 1. Мутанты: проверки
  «устарел» нет; любое входящее делает устаревшим; наше письмо — только по ответу, без времени; сверки прежнего
  текста нет; сверка без обрезки; этапы экрана вшитым списком; причины общие у всех этапов; вердикт первый вместо
  последнего; выключатель автопилота не в счёт; слов отказа нет; автопилот разрешён всегда — 11 из 11 убиты.
- [x] **Б** — красный прогон: плашка — без компонента и строки в `ThreadPage.tsx` — 9 failed из 9, exit 1; экран
  настроек — на прежнем экране 1 failed, 7 passed, exit 1; причина одной фразой — на прежнем коде 1 failed, 8 passed.
  Мутанты: отклонить без причины; «Подходит» у отданного человеку; прежний текст правкой у отданного; «ответ не
  нужен» не виден; подсказки у поля ответа нет; первый черновик вместо последнего; переключатель этапов вшитым
  списком; пояснение этапа не из реестра — 8 из 8 убиты.
- [x] **В** — красный прогон: на прежнем экране 1 failed, 9 passed, exit 1. Мутанты: выключатель не в счёт; предупреждения
  нет никогда; слова сервера не показаны — 3 из 3 убиты.

## Замер экрана (B6, V3)

С подменой ответов сервера в браузере (`page.route`), меркой `scripts/ui_contrast.py` и `scripts/ui_hover.py`; замер
делал агент 5.1 на своей ветке — код экрана при переносе не менялся, кроме строки импорта типа. Живого сервера нет —
это не живой прогон.

**Плашка — 68 точек, все в норме** (норма 4,5 — текст, 3,0 — кнопки):

| Точка | 1440 свет | 1440 тьма | 390 свет | 390 тьма |
|---|---|---|---|---|
| значок «черновик агента» | 10,55 | 8,80 | 10,57 | 10,44 |
| значки «ход», судьи, попыток | 11,45–12,06 | 8,95–9,34 | 11,94–12,22 | 10,44–10,86 |
| текст черновика | 6,26 | 10,97 | 6,31 | 11,80 |
| «Подходит · отправить» / «Править» | 5,22 | 6,62 | 5,18 | 6,83 |
| «Отклонить» | 4,20 | 7,87 | 4,21 | 8,99 |
| подсказка у поля ответа | 5,03 | 6,13 | 5,05 | 6,30 |
| «Почему отклоняете» / причина в списке | 6,53 / 6,28 | 11,07 / 10,92 | 6,56 / 6,28 | 12,27 / 12,37 |
| значок «агент отдал ответ человеку» | 10,24 | 8,77 | 10,19 | 10,05 |
| причина у отданного человеку | 6,24 | 10,76 | 6,35 | 11,06 |
| «Править» у отданного человеку | 4,26 | 7,42 | 4,24 | 8,06 |
| значок «агент: ответ не нужен» | 11,84 | 7,02 | 11,37 | 7,83 |
| причина пропуска | 6,16 | 7,76 | 6,04 | 8,66 |
| «Ответить всё же» | 5,62 | 7,79 | 5,77 | 7,09 |

**Наведение** — 7 элементов плашки, сдвиг 0. **Клавиатура** — таб доходит до каждой кнопки, `:focus-visible` и рамка
`solid 2px`; контраст рамки к стеклу в тёмной теме — 2,00–2,53 : 1 при норме 3 : 1 — общий стиль `glass.css`, путь
соседней сессии: не правлено, передано находкой. **Узкое окно** — документ равен окну во всех 12 состояниях. **Экран
настроек с третьим этапом из реестра** — переключатель 294 px влезает в 390; контраст сегментов 5,35–8,86, пояснения
5,28–6,67, подписи «Не дешевле, $» 6,23–10,01; наведение — сдвиг 0 из 3.

**Предупреждение об автопилоте:**

| Точка | 1440 свет | 1440 тьма | 390 свет | 390 тьма |
|---|---|---|---|---|
| заголовок «Автопилот выбран, но письма сами не уходят» | 6,59 | 6,76 | 6,73 | 6,96 |
| слова сервера | 7,33 | 8,01 | 7,31 | 8,05 |

До подтягивания заголовка к чернилам — 4,39 : 1 на свету; прежнее жёлтое предупреждение экрана на свету — 4,26–4,29 : 1
(общее правило `Alert` — находка, не правлено). Наведение — сдвиг 0; документ не шире окна.

## Живой прогон

Не выполнялся: почта — `NullTransport`, живого сервера нет (замер экрана — с подменой ответов в браузере); стенд не
поднимался, база `outreach` не тронута, воркеры очереди не поднимались. Статус — «написано, под тестами и замерено в
браузере»; живьём — «заложено».

## Ревью рисковых мест

- **деньги** — риска нет: суммы не считаются; текст уходит тем же `answer_reply`, где `guards.assert_no_metrics`
  (правила 1.1b — шлюз `mail_stage`, ящик первого письма — действуют и здесь).
- **безопасность** — права прежние: решения — право send (сервер — 403, экран — решения видны только с правом
  send, без права — объяснение на месте), просмотр — view; новых маршрутов нет; новые поля ответов — из кода реестра
  и `meta` черновика, без данных собеседника; текст черновика и ответа на экране — текстом, без HTML.
- **транзакция БД** — проверка «устарел» — два чтения до `answer_reply` (тот коммитит письмо до отправки); отказ —
  исключение до любой записи: черновик остаётся `drafted`, письма нет. Окно между проверкой и отправкой: ответ на тот
  же ответ закрыт ключом письма и захватом строки (`sending._claim`). Экран — только маршруты шва.
- **производительность** — два запроса с `LIMIT 1` по индексам на одно решение; `_judged` — до `max_rewrites + 1`
  записей; у плашки своего запроса нет (наблюдатель запроса переписки), после решения — два `invalidateQueries`.
- **интеграция** — внешних вызовов нет; почта маршрутов — `Transports()` через `in_use`, в тестах — нулевой
  транспорт.
- **сведение с main** — `ThreadPage.tsx`: строка «Ждёт человека» ответа лида продаж (1.1b) и плашка под ней;
  `ThreadView.agent_reasons` — именованным рядом с `mail` (#206); тип переписки — из `frontend/src/api/thread.ts`.
- **новые модули** — `frontend/src/agent/AgentDraftBanner.tsx` и его тесты; новые функции — `drafts.stale`,
  `drafts.reject_reasons`, `_refuse_as_is`, `schemas._judged`; тесты — `tests/test_agent_approval.py`.

## Предохранитель

Файлов 15 ≤ 25, net 1251 > 800 — три части одним PR по слову владельца; вейвер на размер — за координатором. Каждая
часть в отдельности — в пределе (А 7 / 416, Б 8 / 779, В 3 / 56).

## Verdict

Готово к ревью общего кода и места в `ThreadPage.tsx` соседней сессией; вейвер и сборка с А3 (PR #214) —
координатор; слив — после шва (Б+В+Г+Д), по решению владельца. **К переподписи владельцем** — поверхность «ответы
модели живым людям» (`STATUS.md`).

## Assertion digest (ревью ожиданий, не кода)

База: `c7ccc8f` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **72**, из них без ссылки на пример спеки:
**49**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A1	expect(within(banner()).getByText('черновик агента')).toBeInTheDocument();
A1	expect(within(banner()).getByText('ход 1')).toBeInTheDocument();
A1	expect(within(banner()).getByText('судья: пропустил')).toBeInTheDocument();
A1	expect(within(banner()).getByText('попыток: 1')).toBeInTheDocument();
A1	expect(await screen.findByText('Ответ отправлен с anna@mail.example')).toBeInTheDocument();
A1	expect(posted(recorded.calls, '/api/agent/drafts/11/send')?.body).toEqual({});
A1	expect(screen.queryByRole('region', { name: 'Черновик агента' })).not.toBeInTheDocument();
A2	expect(field).toHaveValue(DRAFT.body);
A2	expect(posted(recorded.calls, '/api/agent/drafts/11/send')?.body).toEqual({
A3	expect(reject).toBeDisabled();
A3	expect(within(banner()).getByRole('radio', { name: 'длинно' })).toBeInTheDocument();
A3	expect(reject).toBeDisabled(); // «другое» без слов — не причина
A3	expect(reject).toBeEnabled();
A3	expect(posted(recorded.calls, '/api/agent/drafts/11/reject')?.body).toEqual({
A4	expect(within(banner()).getByText('агент отдал ответ человеку')).toBeInTheDocument();
A4	expect(within(banner()).getByText(/Почему: цена за пределом\. Как есть/)).toBeInTheDocument();
A4	expect(within(banner()).queryByRole('button', { name: /Подходит/ })).not.toBeInTheDocument();
A4	expect(send).toBeDisabled();
A4	expect(send).toBeEnabled();
A5	expect(screen.getByText('агент: ответ не нужен')).toBeInTheDocument();
A5	expect(screen.getByText('собеседник поблагодарил')).toBeInTheDocument();
A5	expect(field).toHaveValue('');
A5	expect(posted(recorded.calls, '/api/threads/3/answer')?.body).toEqual({
-	expect(await screen.findByText(/Черновик №11 устарел/)).toBeInTheDocument();
-	expect(banner()).toBeInTheDocument();
-	expect(screen.getByText(/Есть черновик агента выше/)).toBeInTheDocument();
-	expect(screen.getByRole('button', { name: 'Ответить' })).toBeEnabled();
-	expect(within(banner()).getByText(DRAFT.body)).toBeInTheDocument();
-	expect(within(banner()).queryByRole('button')).not.toBeInTheDocument();
-	expect(within(banner()).getByText(/у кого есть право отправки/)).toBeInTheDocument();
-	expect(screen.getAllByRole('region', { name: 'Черновик агента' })).toHaveLength(1);
-	expect(within(banner()).getByText('Second draft.')).toBeInTheDocument();
-	expect(within(banner()).getByText('ход 2')).toBeInTheDocument();
-	expect(screen.getByText('Лидам мы отвечаем фактами из базы знаний.')).toBeInTheDocument();
-	expect(screen.getByLabelText('Цель разговора')).toHaveValue('Довести разговор до созвона');
-	expect(screen.getByLabelText('Не дешевле, $')).toBeInTheDocument();
-	expect(recorded.calls.some((call) => call.path === '/api/agent/settings/sales')).toBe(true);
-	expect(screen.getByText('Автопилот выбран, но письма сами не уходят')).toBeInTheDocument();
-	expect(screen.getByText(OFF_WORDS)).toBeInTheDocument();
-	expect(screen.getByText(/Действует версия 2/)).toBeInTheDocument();
-	expect(
-	assert got.reply_id is not None
-	assert reply is not None
-	assert reply.thread_id == first.thread_id
-	assert (as_is.status_code, edited.status_code) == (409, 409)
-	assert "устарел" in as_is.json()["detail"]
-	assert "собеседник написал ещё" in edited.json()["detail"]
-	assert (await stored(session, reply.id)).status is DraftStatus.DRAFTED
-	assert await _answers_to(session, reply.id) == 0  # письма не собралось
-	assert manual.status_code == 200, manual.text
-	assert refused.status_code == 409
-	assert f"наше письмо №{manual.json()['id']}" in refused.json()["detail"]
-	assert (await stored(session, newer.id)).status is DraftStatus.DRAFTED
-	assert await _answers_to(session, newer.id) == 0
-	assert robot.kind.value == "auto_reply"
-	assert sent_now.status_code == 200, sent_now.text
-	assert (await stored(session, reply.id)).status is DraftStatus.SENT
-	assert draft.status is DraftStatus.ESCALATED
-	assert same.status_code == 409
-	assert "прежним текстом через правку тоже" in same.json()["detail"]
-	assert edited.status_code == 200, edited.text
-	assert (await stored(session, reply.id)).final_body == "We could do $100."
-	assert shown.status_code == 200, shown.text
-	assert [(one["stage"], one["title"], one["price_side"]) for one in stages] == [
-	assert [one["lead"] for one in stages] == [
-	assert plain["agent_reasons"] == list(REJECT_REASONS)
-	assert [(card["verdict"], card["attempts"]) for card in plain["drafts"]] == [(None, 0)]
-	assert judged["agent_reasons"] == ["своя причина"]
-	assert [(card["verdict"], card["attempts"]) for card in judged["drafts"]] == [("allow", 2)]
-	assert (off["stage"], off["autopilot_allowed"], on["autopilot_allowed"]) == (
-	assert "OUTREACH_AGENT_AUTOPILOT" in off["autopilot_refusal"]
-	assert on["autopilot_refusal"] is None
```

Привязаны к примерам: **A1 A2 A3 A4 A5**. Остальные 49 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 49

## Проверки на голове PR

Ветка перенесена на main `3d0b012`; голова кода `8bd8b0b`, проверки — по одному разу. pytest (232 passed), diff-coverage, pre-commit и фронт прошли на стопке поверх #217 (`e54023e` — его дерево и есть main `3d0b012` после сквоша, код среза тот же); после переноса на `3d0b012` заново — быстрые проверки таблицы. Полный pytest на голове 5.1-Б у агента — 4851 passed; полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 75242c2ed7ba (head) | 0 |
| ратчет сложности | Ратчет сложности: 400 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 232 passed in 64.22s (0:01:04) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=15 net_loc=1251 (+1314/-63)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 49` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  140 passed (140) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `0d161a0`):

```
breakers: files=15 net_loc=1251 (+1314/-63), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 15, 'max_loc_diff': 1251, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `0d161a0`):

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
