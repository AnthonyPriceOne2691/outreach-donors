# Verify report

**Поставка:** срез `sales-import`, **часть 1 из 3 — 1.3a, ядро загрузки**. Части
1.3b (ссылка и консоль) и 1.3c (API) — следующими PR.
**Date:** 2026-10-03
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=21 утверждение без примера спеки ждёт подписи человека — сверка заголовков без регистра и разделителей, «левая колонка побеждает» и один заголовок — одно поле (5), форма и граница адреса 254 (2), граница имени сайта 253 (1), сборка имени (1), коды страны и языка (2), обрезка 255 (2), отказы файла (2), отказы загрузки и пустая загрузка (5), миграция журнала в процессе (1); человека в цепочке агента нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена: пуш, PR и прогон CI делает координатор
**Commit:** T8 — прогоны сняты на дереве T8; код — T7 `752f944`, T8 меняет только документы среза

## Сборка

Ветка `sales/1.3a-intake-core` от main `a578200` (#135 и #146 уже в main).
Документы среза — первым коммитом T0 `324e64b`, раньше кода и тестов. Ядро
перенесено cherry-pick из ветки среза `sales/1.3-import` по порядку:

| Источник | Здесь | Задача |
|---|---|---|
| `87ecd71` | `65df5b9` | T1 чтение и синонимы |
| `91ff354` | `417d17a` | T2 предпросмотр, нормализация, отчёт |
| `a1c7d1f` | `e28530f` | T4 запись и журнал, миграция |
| `917a02d` | `e4fd9cb` | T5 тесты A1–A4, A6, A8 |
| `7b06691` | `76cb797` | T2 упрощение `preview` |
| `8e88b26` | `ce2ebec` | T5 миграция журнала в процессе |

Текстовых конфликтов не было. Смысловой — один: #137 перенёс общий список
`FREE_MAILBOX_DOMAINS` из `contacts/quality.py` в `contacts/known_addresses.py`,
а `quality` берёт его оттуда импортом. mypy strict отказывал продажам брать список
через `quality` (неявный реэкспорт) — импорт исправлен в том же коммите T2
`417d17a`, где появился; список прежний, второго нет. Остальные перенесённые
файлы совпадают с веткой среза байт в байт.

После переноса: T6 `c27b053` — номер ревизии миграции записан в
`.secrets.baseline` тем же detect-secrets 1.5.0, что зовёт хук (`scan --baseline`:
одна новая запись, у остальных изменилась только отметка `generated_at`),
прагма `allowlist secret` снята; T7 `752f944` — снимок сложности.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0.
- [x] PASS — `ruff check backend/ tests/ scripts/` — exit 0; `ruff format --check` —
      477 файлов, exit 0; `mypy backend/` — 304 файла, exit 0; `scripts/gates.py` —
      473 файла, нарушений нет (в том числе `public-repo`), exit 0; `lint-imports` —
      4 контракта целы, в том числе `mail-does-not-know-sales`, 0 сломано, exit 0;
      `scripts/complexity.py` — 318 файлов, расхождений со снимком нет, exit 0.
      Худшие функции части: `preview` 8, `guess` 3.
- [x] PASS — подпись необратимого `scripts/check_irreversible_signature.sh`: exit 0.
- [x] PASS — голова Alembic одна: `alembic heads` → `1f7b0ee634c2 (head)`, цепочка
      `b3e8d1f04a62 → 715bbf374195 → 1f7b0ee634c2`.
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=origin/main
      check_diff_coverage.sh`, coverage — из полного прогона ниже: exit 0.

      | файл | stmts | miss | cov% |
      |---|---|---|---|
      | backend/features/core/domain.py | 115 | 0 | 100.0 |
      | backend/features/sales/columns.py | 27 | 0 | 100.0 |
      | backend/features/sales/intake.py | 174 | 0 | 100.0 |
      | backend/migrations/versions/1f7b0ee634c2_… | 9 | 0 | 100.0 |
- [ ] FAIL — `delivery_check --require-ci --diff-base origin/main`: exit 1, одна ошибка —
      предохранитель, 801 строка при пределе 800 (раздел «Предохранитель»).

## Behavior oracles

- [x] PASS — `tests/test_sales_intake.py`: 36 тестов на базе дерева — A1, A2,
      A3 (2), A4 (5), A6, A8; сверка заголовков (2), граница и форма адреса (4),
      имя сайта (2), обрезка (2), сборка имени (5), коды страны и языка, отказы
      файла (4 + 1), колонки, загрузки (2), пустая загрузка.
- [x] PASS — `tests/test_sales_model.py`: 12 тестов, из них новый — миграция
      журнала исполняется в процессе, `sales_leads_imported` — последнее значение
      `auditaction`.
- [x] PASS — полный набор на своей базе дерева, дерево T8: **3277 passed за 390 с,
      exit 0**. Прогон один.

### Красный прогон до кода

Новые тесты ядра на старом коде (ветка среза, `cbd7d6f`, до первой строки
реализации):

```
ERROR tests/test_sales_intake.py
ERROR tests/test_sales_intake_sheet.py
ERROR tests/test_api_sales_intake.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
3 errors in 0.14s
```

`ImportError: cannot import name 'intake' from 'backend.features.sales'` — модуля
нет. Файлы ссылки и API приедут с частями 1.3b и 1.3c.

### Обратные прогоны ядра

Правило портилось по одному, тест краснел, файл возвращался. Все красные:

- A3: без отсева бесплатной почты `gmail.com` становился доменом компании;
- A6: заголовок «всегда» вместо угадывания;
- A2: плохая строка роняла всю загрузку;
- A4: домен компании только из сайта — оба теста A4;
- A8 и решение (а): загрузка заводила строку `contacts`.

Ручные мутанты под мутационный гейт — 15 из 15 красные:
- границы `>`→`>=`: адрес 254, имя сайта 253, текст 255, колонка;
- образец строк +1, пачки доменов с 1, `[2:]` вместо `[1:]`;
- `key` без `strip` и с «XX»;
- `"".join` и умолчания `""`→`"XXXX"` у сайта и имени;
- `or`→`and` у кода страны;
- `i <= len(cells)`;
- `loaded=False` у замечания.

## ADD VALUE — проверка по просьбе хозяина выкатки

- Миграция `1f7b0ee634c2` только добавляет значение: одна строка
  `op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'sales_leads_imported'")`,
  ничего не пишет; откат пустой, как у соседей.
- Устроена как соседние: из 11 миграций с `ADD VALUE` (включая эту)
  `autocommit_block` нет ни в одной — все простым `op.execute … IF NOT EXISTS`.
  `b3e8d1f04a62` записывает то же правило: новое значение в миграции не пишется.
- `env.py` гонит все ожидающие ревизии одной транзакцией. После `1f7b0ee634c2`
  ревизий нет, а до неё значение никто не использует. На выкатке «`715bbf374195`
  и `1f7b0ee634c2` одним `upgrade head`» безопасно.
- Тест миграции в процессе (`test_sales_import_journal_value_is_there_and_survives_a_rerun`)
  нового значения в своей транзакции не заводит: подъём сьюта уже закоммитил его,
  повтор `IF NOT EXISTS` — пустой, дальше — только чтение `enum_range`. Пишут
  значение тесты загрузки (A8) — в своих транзакциях, после коммита подъёма.
- Опытом на базе дерева (Postgres 16.14), с откатом:
  - повтор `ADD VALUE IF NOT EXISTS 'sales_leads_imported'` и затем
    `'sales_leads_imported'::auditaction` в той же транзакции — значение
    используется;
  - настоящее новое значение в той же транзакции — отказ
    `UnsafeNewEnumValueUsageError: unsafe use of new value`;
  - после отката пробного значения в перечислении нет.

Итог: разносить на две ревизии нечего.

## Product oracles

- [x] PASS — `eval-smoke.md`: четыре пункта части 1.3a отмечены, два — пути частей
      1.3b и 1.3c.

## Ревью рисковых мест

- **производительность** — поднята `re.compile` в `sales/columns.py`
  и `sales/intake.py`, `.all()` в тестах:
  - регулярки компилируются один раз на модуль; разбор строк — синхронный
    `_rows`, асинхронных обёрток в части нет;
  - замер ядра на ветке среза: 5000 строк — чтение 0,04 с, предпросмотр 0,25 с;
  - `.all()` — только в тестах;
  - запись: домены — пачками по 1000 (`CHUNK`, тест A8 гонит пачками по одному),
    лиды — массовой вставкой ORM, её SQLAlchemy сам режет на страницы.
- **транзакция БД** — `intake.load` не коммитит: коммит — у вызывающего, одной
  транзакцией с журналом (вызывающие — консоль и API частей 1.3b и 1.3c):
  - отказы — нет колонки почты, нет гипотезы — до записи;
  - домены — `ON CONFLICT DO NOTHING` через общий `ensure_domains`, две загрузки
    не падают друг о друга;
  - повторная загрузка того же файла даст повторных лидов — их режет очистка
    1.4.
- **новый модуль** — `sales/columns.py`, `sales/intake.py`: состояния нет. Риск
  для доноров — общие строки `domains`: загрузка заводит их так же, как доноры,
  строк `contacts` не заводит (A8 держит это тестом).
- `.secrets.baseline` — одна новая запись о номере ревизии миграции; остальные
  записи не менялись.

## Предохранитель

```
ERROR: circuit breaker: net loc_diff 801 > 800 — split the PR or add a human waiver line to STATUS (§3.4)
breakers: files=7 net_loc=801 (+806/-5), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
```

Часть 1.3a — 7 файлов, 801 строка (+806 −5) против `origin/main`:
- код и тесты ядра — 792, ровно как в ветке среза;
- запись номера ревизии миграции в `.secrets.baseline` — ещё 9.

Сверх переноса в коде нет ни строки. Уложиться в предел можно тремя способами, и
каждый — не решение исполнителя:
- waiver владельца строкой в STATUS (`max_loc_diff=801 reason=… by=human:…`);
- снять одну строку в тестах — правка кода сверх переноса;
- вернуть прагму `allowlist secret` вместо записи в снимке — минус 9 строк,
  вопреки заданию сборки.

## Предупреждения delivery_check, разобранные

Вывод дословно (`--require-ci --diff-base origin/main`, exit 1):

```
ERROR: circuit breaker: net loc_diff 801 > 800 — split the PR or add a human waiver line to STATUS (§3.4)
breakers: files=7 net_loc=801 (+806/-5), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 1 error(s), 4 warning(s)
```

- Предохранитель — раздел выше.
- «`irreversible_surfaces:` не называет отправка наружу» — было у В0, 1.1a
  и 1.2; строка подписана и перенесена дословно.
- «asserts_reviewed_by deferred» — перечень в шапке, подпись — при ревью.
- «Ни одного реляционного оракула» — `hypothesis` нет в зависимостях, новая
  зависимость — вне среза.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

Вывод дословно (`contour_waves.py --base origin/main`, exit 0):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 5 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```

В1 развёрнута срезом 1.2; `shared_changes:` этого STATUS покрывает общий код части,
раздел «Уроки» в `tasks.md` судится и проходит.

## Spec coverage gaps

- A5, A7 и A9, а также A2 и A6 через консоль и API — пути частей 1.3b и 1.3c,
  в этой части их нет; в `eval-smoke.md` они названы пунктами следующих частей.
- Мутационный гейт в main пока не настроен (`[tool.mutmut]` нет, cqg@2.45) — тесты
  писались под него, ручные мутанты — выше.

## Находки в общем коде — не чинились

- `backend/features/donors/host.py:54` — `normalize_host` бросает
  `ValueError: Invalid IPv6 URL` на ячейке со знаком `[`, хотя обещает пустую
  строку на мусоре; задевает `runs/planning.py:266`. Продажи обходят это через
  `host_from_cell`. Передано координатору.
- `backend/features/contacts/known_addresses.py:26` — в `FREE_MAILBOX_DOMAINS` нет
  распространённой бесплатной почты (`bk.ru`, `list.ru`, `inbox.ru`, `ya.ru`,
  `web.de`, `gmx.de`, `t-online.de` и др.). Передано координатору.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — решение координатора и владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **47**, из них без ссылки на пример спеки:
**21**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A1	assert (found.source, found.header, found.rows) == ("база.csv", True, 2)
A1	assert found.columns == ["Почта", "Имя", "Компания", "Сайт"]
A1	assert found.mapping == {
A1	assert (found.leads, found.problems) == (A1_LEADS, [])
-	assert guess(one_per_field) == {field: index for index, field in enumerate(LeadField)}
-	assert guess(["Почта", "Email", "Заметка"]) == {LeadField.EMAIL: 0}
-	assert key("  First_Name: ") == "first name"
-	assert len(titles) == len(set(titles))
-	assert set(SYNONYMS) == set(LeadField)
A2	assert found.problems == [Problem(7, intake.NO_ADDRESS, "")]
A2	assert (found.rows, len(found.leads), found.rejected, len(found.sample)) == (9, 8, 1, 5)
A2	assert [lead.line for lead in found.leads] == [2, 3, 4, 5, 6, 8, 9, 10]
-	assert [lead.email for lead in found.leads] == ([email] if taken else [])
-	assert found.problems == ([] if taken else [Problem(2, "не адрес почты", email)])
A3	assert (found.leads, found.problems) == ([], [problem])
A4	assert ([(lead.domain, lead.name) for lead in found.leads], found.problems) == (
L67	assert [lead.domain for lead in found.leads] == ["acme.example.test"]
L67	assert found.problems == [Problem(2, reason, "Acme Inc", loaded=True)]
-	assert found.leads[0].domain == (site if longest else "acme.example.test")
A6	assert (found.header, found.needs_mapping, found.rows, found.mapping) == (False, True, 2, {})
A6	assert found.columns == ["колонка 1", "колонка 2"]
A6	assert found.sample == [
A6	assert (found.leads, found.problems) == ([], [])
A6	assert [(lead.line, lead.name) for lead in mapped.leads] == [(1, "Иван"), (2, "Мария")]
-	assert found.leads[0].name == name
-	assert [(lead.country, lead.language) for lead in found.leads] == [
-	assert found.problems == [
-	assert found.leads[0].position == title[:255]
-	assert found.problems == (cut if length > 255 else [])
-	assert str(refused.value).startswith(words)
-	assert str(refused.value).startswith(f"файл {tmp_path / 'нет.csv'} не открылся: No such file")
A1	assert str(refused.value) == "колонки 5 нет, их в файле 4: email не сопоставить"
A1	assert await intake.load(session, _preview(tmp_path, A1), hypothesis.id, author_id=None) == 2
A1	assert [(r.email, hosts[r.domain_id], r.name, r.company) for r in rows] == [
A1	assert {(r.contact_id, r.source, r.status, r.hypothesis_id) for r in rows} == {
A1	assert await _count(session, ContactModel) == 0
A1	assert journal is not None
A1	assert (journal.user_id, journal.target) == (None, f"sales_hypothesis:{hypothesis.id}")
A1	assert journal.details == {"источник": "база.csv", "загружено": 2, "отклонено": 0}
-	assert str(refused.value) == words
-	assert await _count(session, SalesLeadModel) == 0
-	assert await intake.load(session, found, hypothesis.id, author_id=None) == 0
-	assert await _count(session, DomainModel, DomainModel.host == "gmail.com") == 0
-	assert (
A1	assert spec is not None, path
A1	assert spec.loader is not None, path
-	assert (await connection.run_sync(_journal_values))[-1] == "sales_leads_imported"
```

Привязаны к примерам: **A1 A2 A3 A4 A6 L67**. Остальные 21 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 21
