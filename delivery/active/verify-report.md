# Verify report

**Поставка:** срез `sales-stage` (модуль «Продажи», 1.1b), часть «в» из трёх — экран и сводка знают этап продаж;
журнал сборки и отбор прогона знают каждый этап.

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=25 утверждений части без ссылки на пример спеки (A3 назван заголовком теста, а не в теле — дайджест такие не привязывает); подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `24b838b` — голова части «в» и вершина ветки `sales/1.1b-stage-202` (база `0d65206` — #202; не запушена)

## Сборка

Перенос 07.10: коммит части (`92eaeea` ветки `sales/1.1b-stage-main`) переложен на голову «б» `cherry-pick` — сначала
на базе `5a964d3`, затем на `origin/main` `f9819da`, затем на `0d65206` (#202). Код части не менялся; конфликты — снимок сложности (пересобран по
дереву) и импорт в `frontend/src/api/types.ts`: #194 добавил `import type { Offer } from './offers';` на то же место,
что и `import type { Stage } from './stages';` части, — склейка двух строк (стиль main — без пустой строки после
импорта), файл 1014 строк из 1018. `ThreadPage.tsx` с #194 (`ReplyOffers`) и с запасным значением части «б» слился
сам. На `0d65206` (#201, #202): `types.ts` слился сам — импорт `Stage` рядом с `Offer` (#194) и `PriceOrigin` (#201), файл
1015 строк из 1018; снимок — пересборка. Второй коммит — просьба соседней сессии после #193: тест донора — пометка этапа и условие продаж не прячут ответ и
его пояснение у поля. Просили его в часть «а» — пометка этапа и условие `!sales` приходят частью «в».

| SHA | Суть | Файлов (без `delivery/`) | +/− |
|---|---|---|---|
| `9541fd4` | журнал, отбор прогона, фронт (`api/stages.ts`, типы, подпись, переписка) + тесты; снимок | 9 | +322/−22 |
| `24b838b` | тест донора после #193 (`ThreadPageSales.test.tsx`) | 1 | +22/−0 |
| **часть «в» против головы «б» `a9d94da`** | | **9** | **+344/−22 (net 322)** |

## Shape oracles

На голове `24b838b` (вершина ветки, дерево чистое):

| Проверка | Итог | exit |
|---|---|---|
| хуки pre-commit на коммитах части и `pre-commit run --from-ref a9d94da --to-ref HEAD` (файлы части) | 25 хуков Passed | 0 |
| `pre-commit run --all-files` | на этой базе не гонялся; на прежней вершине (`sales/1.1b-stage-f98`, main `f9819da`) — 27 Passed | — |
| `ruff check backend tests` | чисто | 0 |
| `mypy backend` (strict) | 375 файлов, ошибок нет | 0 |
| `scripts/gates.py` | 616 файлов, нарушений нет | 0 |
| `scripts/complexity.py` | 390 файлов, расхождений нет | 0 |
| `lint-imports` | 4 контракта, 0 нарушено | 0 |
| `scripts/lint/check_file_length.sh` | OK (`frontend/src/api/types.ts` 1015 при пределе 1018) | 0 |
| `detect-secrets-hook --baseline .secrets.baseline` | новых секретов нет | 0 |
| `scripts/check_irreversible_signature.sh` | подпись сходится | 0 |
| `alembic heads` | одна голова — `39e342cb2b21` | 0 |
| `tsc --noEmit` | чисто | 0 |
| `BASE=0d65206 scripts/lint/check_baseline_ratchet.sh` | «baseline-ratchet: OK (15 снимков сверено с 0d65206)» | 0 |
| diff-coverage `SKIP_TESTS=1 STRICT=1 MIN_PCT=70 LINT_PY_SRC=backend LINT_BE_DIR=. LINT_COV_PKG=backend BASE=0d65206` (отчёт полного прогона, `coverage.json.head` = `24b838b`) | 25 изменённых prod-файлов, все ≥ 70 %; минимум — `replies/stage_rules.py` 81,8 % и `core/stages.py` 84,6 % (непокрыто — ветки `case _: assert_never`) | 0 |
| `contour_waves --base 0d65206` — в дереве (STATUS базы — срез 4.6a) | «общий код тронут без объявления» (STATUS среза — черновик) | 1 |
| `delivery_check --require-ci --diff-base 0d65206` — в дереве | 0 ошибок, 5 предупреждений, `files=39 net_loc=1567` (лимиты — вейвер STATUS 4.6a) | 0 |

## Behavior oracles

- [x] PASS — полный pytest на голове, покрытие каталогом (`--cov=backend`): **4675 passed** за 9:06, exit 0 (запущен, когда чужих прогонов с покрытием не было и нагрузка была < 10).
- [x] PASS — тесты среза и соседей (72 файла, с тестами #202 и #201): **1741 passed** за 3:40, exit 0.
- [x] PASS — vitest `--maxWorkers=2 --reporter=dot` разделов `src/threads src/letters src/sales`: 18 файлов, **186 passed**,
      exit 0. Полный vitest не гонялся.

### Красный прогон до кода

- main + `tests/test_sales_stage_screens.py`: не собирается — `ModuleNotFoundError: No module named
  'backend.features.core.stages'` (прогон 06.10 на `05d75d9` действует; тест при переносе не менялся).
- vitest `ThreadPageSales.test.tsx` на голове «б» (запасное значение есть, пометки этапа и подписи `sales_pending` части
  «в» нет): тест продаж красный — `TestingLibraryElementError: Unable to find an element with the text: /кампания
  «Продажи» · продажи/`; без запасного значения экран падал `TypeError … reading 'color'` (прежний прогон ветки
  `sales/1.1b-stage`).
- Тест донора после #193 — проверка того, что правка части не ломает чужое: на голове «б» (до пометки этапа) он
  зелёный по построению (там же: `1 failed | 1 passed`); красный прогон — мутантами ниже.

Прогоны на голове «б» сделаны на базе `5a964d3`; файлы экрана части на main `f9819da` и на `0d65206` те же, кроме контекста #194 и #201.

### Обратные прогоны

Места переноса 06.10 (код части с тех пор не менялся) — прежняя таблица: T1 (журнал без продаж), FE1 (`Stage` без
`sales`), FE2 (`LetterStage` с `sales`), FE3 (пометка продаж пропала) — 4 из 4; не переписанные места — V2, V4–V8.

Тест донора (07.10; дерево ветки, замена ровно одного места, возврат со сверкой sha256; на базе `5a964d3` и на main
`f9819da` — итог один):

| Мутант | Файл | Убит |
|---|---|---|
| M-d1 — условие ответа `!sales` → `sales` | `threads/ThreadPage.tsx` | оба теста `ThreadPageSales` |
| M-d2 — донор как продажи (`stage !== 'advertisers'`) | `threads/ThreadPage.tsx` | только тест донора |
| M-d3 — у донора пометка «продажи» | `api/stages.ts` | только тест донора |

**3 из 3 убиты**, два — только новым тестом.

## Живой прогон

Не проводился: диалогов продаж нет, пока почта им отказывает. Контраст и ширины 1440/390 — «заложено».

## Product oracles

- [x] PASS — `eval-smoke.md`: A3 на экране и через API, сверка значений, журнал, отбор, донор после #193; живой экран —
      «заложено».

## Ревью рисковых мест

- **деньги** — риска нет: у ответа продаж форма цены не показывается (`const reviewable = !sales && (incoming.kind === 'human' || Boolean(incoming.review_reason));`),
  сервер цену продаж и так не принимает (часть «б»). Сумм часть не считает.
- **безопасность** — риска нет: права и маршруты прежние, `bearer(admin_token)` — помощник тестов API; на
  экране — только скрытие действий, проверку прав делает сервер.
- **транзакция БД** — риска нет: `_donor_answers` только читает (`await self._declined(hosts, moment)`,
  `await self._rejected(hosts)`), `_STAGE_TITLES` — запись в журнал тем же путём, что прежде.
- **интеграция** — риска нет: внешних вызовов нет; экран зовёт прежние маршруты, vitest — через `serve()`.
- **производительность** — риска нет, потому что `re.search` — только в тесте сверки (`tests/test_sales_stage_screens.py`,
  `_union` и `_keys` читают три файла фронта по разу); в коде продукта выборок без границы и разбора в цикле часть не
  добавляет: `_donor_answers` — те же два запроса по списку хостов, что и прежде.
- **новый модуль** — `frontend/src/api/stages.ts` (`export type Stage = 'donors' | 'advertisers' | 'sales';`
  и словарь пометок; состояния нет), тесты `test_sales_stage_screens`, `ThreadPageSales.test.tsx`.

## Предохранитель

`breakers:` части «в» против головы «б» `a9d94da`, правилом исключений `delivery_check`:

```
breakers: files=9 net_loc=322 (+344/-22), excluded=1 ['delivery/complexity-snapshot.json']
```

В пределах 25 файлов и 800 строк. Весь срез против `0d65206`: `files=39 net_loc=1567 (+1700/-133)` —
сверх предела, потому три PR.

## Предупреждения delivery_check, разобранные

Дословно (клон вершины `24b838b`, черновики части в `delivery/active/`, `--require-ci --diff-base a9d94da`), exit 0:

```
breakers: files=9 net_loc=322 (+344/-22), excluded=1 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: `model_surface`: 6 из 6 элемент(ов) не совпадают ни с одним файлом дерева — правку по ним проверка не увидит: DEAD), LLM_JUDGE_MODEL` (деф. gpt-5-mini), LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели. Форма поля — пути от корня и глобы через запятую, без бэктиков и прозы; пояснения — в <!-- … --> на той же строке или строкой ниже
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 5 warning(s)
```

- «`model_surface`: 6 из 6 элементов не совпадают ни с одним файлом дерева» — новая проверка контура (#198, delivery@2.00):
  строка `model_surface:` в STATUS прозой; та же строка и то же предупреждение — у STATUS main. Строку при сборке
  подставит координатор (как и `stack:`), черновик её не меняет.
- «`irreversible_surfaces:` не называет отправка наружу» — детектор находит маркеры отправки во всём продуктовом
  коде (письма доноров); предупреждение стоит и на STATUS main. Строка подписана владельцем и переносится
  дословно; часть отправки не добавляет (подпись сходится: `check_irreversible_signature.sh` — exit 0).
- «asserts_reviewed_by deferred» — утверждения ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта (новая зависимость —
  решение владельца). Инвариант части держат параметризованные тесты.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

Дословно (тот же клон, `--base a9d94da`), exit 0:

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

В рабочем дереве (в `delivery/active/` — STATUS main, срез 4.6a) та же проверка против базы `0d65206` на вершине
ветки красная одной строкой «общий код тронут без объявления» — по файлам среза (exit 1); черновик `shared_changes:` называет
каждый файл части полным путём со словом «Согласовано». База каждой части — голова предыдущей: PR сливаются по
порядку, и после слива «а» и «б» база «в» — main.

## Spec coverage gaps

- A3 — тестами (vitest и API); живой экран — «заложено».
- Ответы продаж на главной в «Ждут человека» — открытый вопрос владельцу.

## Находки (общий код — не чинил)

1. **`workers/jobs.py` (#189): `_search_contacts` и `_contacts_what` — `if stage is Stage.ADVERTISERS`, иначе доноры.**
   Продажам туда хода нет (задачу ставят экраны доноров и рекламодателей), но новый этап пойдёт путём доноров молча;
   путь соседней сессии — предложить ей `match` с `assert_never` (находка 06.10, на main та же).
2. Падение экранов диалогов на незнакомом состоянии (находка 06.10) — закрыто частью «б» по просьбе соседней сессии.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Готово к PR после частей «а» и «б», STATUS части в `delivery/active/`, подписи дайджеста утверждений и ОК соседней
сессии на общие файлы.

## Assertion digest (ревью ожиданий, не кода)

База: `a9d94da` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **25**, из них без ссылки на пример спеки:
**25**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(screen.getByText(/кампания «Продажи» · продажи/)).toBeInTheDocument();
-	expect(screen.getByText('ответ продаж — ждёт человека')).toBeInTheDocument();
-	expect(screen.getByText(`Ждёт человека: ${REASON}.`)).toBeInTheDocument();
-	expect(screen.queryByLabelText('Белая цена')).not.toBeInTheDocument();
-	expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument();
-	expect(screen.queryByRole('button', { name: 'Ответить' })).not.toBeInTheDocument();
-	expect(screen.getByLabelText('Текст ответа')).toHaveAccessibleDescription(
-	expect(screen.queryByText(/· продажи|Ждёт человека:/)).not.toBeInTheDocument();
-	assert found is not None, f"в {file} нет типа {name}"
-	assert found is not None, f"в {file} нет {constant}"
-	assert _union(name, file) == values
-	assert _keys(constant, file) == values
-	assert _STAGE_TITLES[stage]
-	assert _STAGE_TITLES[Stage.SALES] == "продажи"
-	assert (view.waiting.prices, view.waiting.leads) == (0, 0)
-	assert (view.donors.written, view.donors.replied) == (0, 0)
-	assert (sales.sent, sales.delivered) == (1, 1)
-	assert view.letters[Stage.DONORS].sent == 0
-	assert response.status_code == 200
-	assert set(response.json()["letters"]) == STAGES
-	assert (card["stage"], card["state"]) == ("sales", "sales_pending")
-	assert (incoming["needs_review"], incoming["review_reason"]) == (True, SALES_WAITING)
-	assert incoming["lead"] is False
-	assert for_donors == {"declined.example.test": ExclusionReason.REJECTED}
-	assert for_sales == for_advertisers == {}
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 25

## Проверки на голове PR

Ветка перенесена на main `7ba28c6`; голова кода `2944e25`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 39e342cb2b21 (head) | 0 |
| ратчет сложности | Ратчет сложности: 393 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 298 passed in 33.26s | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=9 net_loc=322 (+344/-22)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 25` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  215 passed (215) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `74a6c1f`):

```
breakers: files=9 net_loc=322 (+344/-22), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `74a6c1f`):

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
