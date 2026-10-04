# Verify report

**Поставка:** срез `sales-cleaning`, **часть 2 из 2 — 1.4b+c: очистка базы лидов** (дубли,
стоп-лист продаж, отписки, годность, почта домена, платный проверяльщик, расход, консоль).
Часть 1 (страна и пояс) — отдельный PR. Этой частью срез завершается.

**Date:** 2026-10-04
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; общий код части — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=38 утверждений без примера спеки ждут подписи человека — порядок правил, партии и коммит, выбор гипотезы, слова причин отказа, форма вывода консоли, настройка провайдера, расход и миграции, ключ в заголовке; человека в цепочке нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR; ветка перенесена на main после слияния части 1 (#155), пре-пуш гоняет полный набор ещё раз
**Commit:** T4б — тяжёлые прогоны сняты на дереве T3 до переноса (`cb77215`, то же содержимое на базе части 1); после переноса на main `c95e957` и правок T2в/T1б повторены волны, delivery_check, гейты DRY и снимков, mypy, тесты части и миграций; T4б меняет только документы среза

## Сборка

Ветка `sales/1.4bc-cleaning` собрана на вершине части 1 и после её слияния (#155) перенесена на main
`c95e957` (`git rebase --onto origin/main`; конфликт только в снимке сложности — пересобран). Код
части перенесён из ветки среза `sales/1.4-cleaning` патчем от её базы к вершине
(`git diff 4271973 3c30528 -- <файлы> | git apply -3 --index`) — трёхстороннее наложение,
потому что main после базы ветки среза ушёл вперёд (#148, #151, #152, #153); конфликтов не было.
Коммиты после переноса: `76e6b8f` T0 документы, `c8bd4ef` T1 код, `77edd17` T2 тесты, `9d00994` T3
снимок, `a53c67c` T4 verify, `35ee0e9` T2в (тест A3 под соседний #154), `20bc93e` T1б (миграция без клона).
Код части — 18 файлов, net 2706 строк.

Сверх переноса, в T1, T2, T2в и T1б (всё названо в сообщениях коммитов):
- `import httpx` возвращён в `backend/cli/sales.py`: main убрал его после базы ветки среза
  (клиент Google в API через `sheet.client()`), а очистка им пользуется — без него ruff F821;
- три строки `_KEPT_ON_INTERRUPT` для `sales-import`, `sales-stoplist-add` (одной
  транзакцией) и `sales-clean` (партии остались) — договорённость с соседями при ревью #152;
  под тестом `test_second_ctrl_c_says_what_each_sales_command_kept` (три команды);
- колонки `created_at`/`updated_at` таблицы `sales_stoplist` в миграции `95ee6522e0de` —
  с константой умолчания `_NOW = sa.text("now()")`: шаблон совпадал с миграциями `a4d7f1c92b63`
  и `b7e2c4a81f95` (одна пара jscpd), а гейт снимков в CI роняет рост базовой линии; DDL тот же,
  базовая линия DRY осталась 55 (`check_jscpd_gate.sh`: 55 пар при снимке 55;
  `check_baseline_ratchet.sh`: 15 снимков сверено с main);
- тест A3 «отписка через приём ответов» зовёт `ReplyRepository.suppress(email)` без этапа:
  соседний #154 убрал параметр (отписка закрывает адрес на обоих этапах); строки с этапом
  закрывает соседний параметризованный тест A3 — чтение очистки верно для старых и новых строк;
- пометки примеров спеки (A1–A9) первой строкой тела тестов — для дайджеста утверждений.

Голова Alembic одна: `2d9877260a6d`, цепочка `1f7b0ee634c2 → 95ee6522e0de → 2d9877260a6d`;
`ADD VALUE` — отдельной ревизией, значение в той же транзакции не используется.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков прошли, 0 упали, exit 0.
- [x] PASS — `ruff check` exit 0; `ruff format --check` — 485 files already formatted; `mypy backend/` — Success: no issues found in 314 source files;
      `scripts/gates.py` — гейты пройдены, 495 файлов, нарушений нет; `lint-imports` — Contracts: 4 kept, 0 broken. (`mail-does-not-know-sales` цел:
      `sales` зовёт `contacts`, `replies`, `core`, обратного импорта нет); ратчет сложности —
      расхождений нет (T3; новые файлы: `cleaning.py` сложность 8, `stoplist.py` 10,
      `verifier.py` 9).
- [x] PASS — подпись необратимого (`check_irreversible_signature.sh`): exit 0; четыре
      строки STATUS перенесены слово в слово.
- [x] PASS — голова Alembic одна: `2d9877260a6d`.
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=sales/1.4a-geo
      check_diff_coverage.sh` на JSON полного прогона (как в CI), exit 0:

      | файл | stmts | miss | cov% |
      |---|---|---|---|
      | backend/cli/main.py | 261 | 9 | 96.6 |
      | backend/cli/sales.py | 181 | 15 | 91.7 |
      | backend/config/sales.py | 9 | 0 | 100.0 |
      | backend/features/core/domain.py | 116 | 0 | 100.0 |
      | backend/features/core/models/__init__.py | 12 | 0 | 100.0 |
      | backend/features/core/usage.py | 15 | 1 | 93.3 |
      | backend/features/sales/cleaning.py | 218 | 1 | 99.5 |
      | backend/features/sales/models.py | 60 | 0 | 100.0 |
      | backend/features/sales/stoplist.py | 69 | 0 | 100.0 |
      | backend/features/sales/verifier.py | 95 | 0 | 100.0 |
      | backend/migrations/versions/2d9877260a6d_usage_provider_hunter.py | 9 | 0 | 100.0 |
      | backend/migrations/versions/95ee6522e0de_sales_cleaning_and_stoplist.py | 18 | 0 | 100.0 |

- [x] PASS — `contour_waves --base sales/1.4a-geo` (режим CI): contour-waves: нарушений нет; общий код части
      объявлен в `shared_changes:` полными путями.
- [x] PASS — `delivery_check --require-ci --diff-base sales/1.4a-geo`: 0 error(s), 4 warning(s) (разобраны ниже);
      предохранитель — 18 файлов, net 2706 при пределе 800 строк — **вейвер владельца**
      строкой `waivers:` в STATUS (решение 04.10: 1.4b без 1.4c роняет живой проверяльщик
      после оплаченной проверки, а 1.4b сам больше 800).

## Behavior oracles

- [x] PASS — тесты части на настоящей базе дерева, `tests/test_sales_cleaning.py`,
      `tests/test_sales_stoplist.py`, `tests/test_sales_verifier.py`,
      `tests/test_sales_clean_cli.py`, `tests/test_schema.py`: **107 passed** (T2).
      A1 дубли (в файле и в базе, любая гипотеза и судьба), A2 чужой диалог (`open`/`replied`
      держат, `closed`/`unsubscribed` — нет), A3 отписки (без этапа; через ответ с этапом;
      домен в общем стоп-листе; срок записи), A8 стоп-лист продаж (адрес, домен компании,
      домен адреса, корень; файл с заголовком, мусором и повтором; журнал; CHECK; консоль),
      A9 годность (общие правила и ролевой ящик на бесплатной почте), порядок правил, партии
      с коммитом, выбор гипотезы, A4 DNS по домену адреса (`NONE`/`NULL_MX` без платной
      проверки, `UNKNOWN` считается, один запрос на домен), A5 вердикты (`fixture` без
      расхода; Hunter за `MockTransport` — источник в статусе, одна строка расхода на
      партию), A6 отказы (15 отказов Hunter с классом и словами; сеть/429/5xx/202/мусор →
      `new` и повтор; квота/учётка → остановка, код 7), A7 `live` без ключа — отказ до
      первого лида; миграции — цикл откат–подъём `95ee6522e0de` и `ADD VALUE` в процессе.
- [x] PASS — полный набор на базе дерева (`cb77215`): **3558 passed in 317.93s (0:05:17), exit 0** — один прогон, он же дал
      JSON покрытия.

### Красный прогон до кода

Дерево T0 `6d71cc5` (`git archive`, без кода части) + четыре файла тестов из T2: сбор падает
на импорте —
`tests/test_sales_clean_cli.py`: `ImportError: cannot import name 'EXIT_VERIFIER_STOPPED' from 'backend.cli.sales'`;
`tests/test_sales_cleaning.py`: `ImportError: cannot import name 'cleaning' from 'backend.features.sales'`;
`tests/test_sales_stoplist.py`: `ImportError: cannot import name 'run_stoplist_add' from 'backend.cli.sales'`;
`tests/test_sales_verifier.py`: `ImportError: cannot import name 'verifier' from 'backend.features.sales'`.
Повторено 04.10 при verify. Красные прогоны строителя по задачам (на ветке среза): до T2 —
стоп-лист ImportError, очистка 5 падений (слова A5, отписка с этапом ×2, отписка через
`ReplyRepository.suppress`, корень `host_key`); до T3 — консоль ImportError, проверяльщик
2 падения (слова отказа 401).

### Обратные прогоны

Сняты строителем 04.10 на ветке среза на том же коде (копия дерева, одна подмена за раз,
свои тесты, восстановление); на этой ветке повторно не снимались — код тот же побайтно
(перенос патчем, правки сверх переноса названы выше).

| Мутант | Файл | Итог |
|---|---|---|
| дубль не ищется | cleaning.py | 6 failed, 31 passed |
| стоп-лист не смотрит адрес | cleaning.py | 1 failed, 36 passed |
| стоп-лист без корня домена адреса | cleaning.py | 1 failed, 36 passed |
| отписка только без этапа | cleaning.py | 3 failed, 34 passed |
| срок записи не уважается | cleaning.py | 1 failed, 36 passed |
| диалог `replied` не держит | cleaning.py | 1 failed, 36 passed |
| ролевой на бесплатной почте годен | cleaning.py | 2 failed, 35 passed |
| порядок правил: отписка раньше стоп-листа | cleaning.py | 1 failed, 36 passed |
| нулевой MX принимает почту | cleaning.py | 1 failed, 36 passed |
| доля UNKNOWN не считается | cleaning.py | 2 failed, 41 passed |
| квота не останавливает проход | cleaning.py | 2 failed, 35 passed |
| расход не пишется | cleaning.py | 2 failed, 35 passed |
| гипотеза не фильтрует | cleaning.py | 2 failed, 41 passed |
| партия не коммитится | cleaning.py | 1 failed, 36 passed |
| вердикт без имени источника | cleaning.py | 16 failed, 21 passed |
| `unknown` — не вердикт, а отказ | verifier.py | 3 failed, 73 passed |
| 429 — не отказ сервиса | verifier.py | 2 failed, 74 passed |
| `live` без ключа стартует | verifier.py | 1 failed, 75 passed |
| `fixture` стоит денег | verifier.py | 4 failed, 72 passed |
| заголовок стоп-листа — мусор | stoplist.py | 4 failed, 10 passed |
| журнал при пустом повторе | stoplist.py | 1 failed, 13 passed |

**21 мутант части — 21 убит, 0 выжило** (ещё 3 мутанта страны и пояса — в части 1).

### Замер ступени DNS — на заглушке

Строитель, `cleaning.mail_routes` на 5 000 уникальных доменах; `mail_route` подменён задержкой
30–50 мс (близкий резолвер); второй сценарий — 2 % доменов молчат 2 с (таймаут) и приходят
`UNKNOWN`.

| Параллельность | Молчащих | Время | Доменов/с |
|---|---|---|---|
| 8 (как `file_sweep`) | нет | 25,4 с | 197 |
| 32 | нет | 6,3 с | 794 |
| 8 | 2 % по 2 с | 51,2 с | 98 |
| 32 | 2 % по 2 с | 13,9 с | 360 |

При 8 параллельных база на 5 000 строк проходит DNS за полминуты без таймаутов и за минуту
при 2 % молчащих; доля `UNKNOWN` считается ровно (100 из 5 000). Живой замер на настоящей
базе — **заложено**: сеть и резолверы машины неизвестны (молчащий системный резолвер
превращает ступень в «неизвестно по всем» — урок соседей).

## Живой прогон

Живого прогона нет: сеть в тестах запрещена, DNS — заглушка, Hunter — `MockTransport`.
Три статуса: **написано и под тестами** — правила A1–A3, A8, A9, A4 по заглушке DNS,
вердикты и отказы Hunter за `MockTransport`, остановка платной части, расход, консоль,
миграции (в процессе и циклом); **замерено живьём** — ничего; **заложено** — живой DNS и живой
Hunter (`SALES_VERIFIER_PROVIDER=live` включает человек), доля `unknown`/`accept_all` и порог
к ним (Ф4), переезд стоп-листа в `suppressions` при `Stage.SALES`.

## Product oracles

- [x] PASS — `eval-smoke.md`: пункты части отмечены; живой DNS и Hunter — «заложено».

## Ревью рисковых мест

- **деньги** — трата платного проверяльщика. По умолчанию `fixture`: ни сети, ни денег,
  строка расхода не пишется, в консоли предупреждение «вердикты выдуманные». `live` — только
  явной переменной `SALES_VERIFIER_PROVIDER=live` и с ключом (`build_verifier` отказывает
  до первого лида — A7). Квота (`usage_exceeded`, 403) и закрытая учётка
  (`restricted_account`, 401) останавливают платную часть прохода целиком, а не повторяются —
  ключ общий с соседним модулем. Одна строка `usage_records` (`sales_verify` / `hunter`) на
  партию, коммит с партией: обрыв не теряет ни вердикты, ни запись о деньгах. Лид `ready`
  второй раз не проверяется; `NONE`/`NULL_MX` до проверяльщика не доходят.
- **безопасность** — ключ Hunter уходит заголовком, не в URL (тест
  `test_hunter_asks_by_address_with_the_key_in_a_header_not_in_the_url`); отказы сервиса
  пересказываются словами класса, тело ответа в лог целиком не пишется; файл стоп-листа
  читается общим `read_list` (строка — домен или адрес, остальное названо с номером), запросы
  к базе — через ORM. Новых прав и маршрутов нет: команды консоли — оператору с доступом к
  базе, как `sales-import`.
- **интеграция** — Hunter: 15 отказов разобраны в классы «повторить» (сеть, 429, 5xx, 202,
  не JSON, незнакомый статус → лид `new` с причиной) и «остановиться» (квота, учётка);
  маркеры и исключения — общие с поиском адресов (`contacts/provider.py`), не копия. DNS —
  общий `mail_route` с запасными резолверами; «не ответил» — `UNKNOWN`, идёт дальше и
  считается отдельно (`mx_unknown`): ступень, которая ничего не отсеивает, в поломке выглядит
  как работающая. Живого прогона обоих — нет (заложено).
- **транзакция БД и миграции** — две миграции, голова одна; `ADD VALUE` — отдельной ревизией
  после той, что создаёт таблицу; цикл откат–подъём под тестом; таблица `sales_stoplist`
  с CHECK «домен xor адрес» и уникальностью. Партия — одна транзакция: правила, вердикты,
  строка расхода; дубль через границу партий виден по базе, потому что партии коммитятся.
- **производительность** — одно чтение базы на партию (200 лидов); DNS параллельно по
  уникальным доменам партии (`Semaphore 8`, как `file_sweep`): 5 000 доменов — полминуты
  без таймаутов, минута при 2 % молчащих (таблица выше). Платный шаг — по одному адресу,
  только для дошедших.
- **новый модуль** — `cleaning.py`, `stoplist.py`, `verifier.py` внутри `sales/`: сложность
  8/10/9, покрытие в таблице выше; импортируют `contacts`, `replies`, `core` — направление
  «продажи → почта», разрешённое контрактом; обратного импорта нет (`lint-imports`).

## Предохранитель

18 файлов, net 2706 строк при пределах 25 файлов и 800 строк. Файлы — в пределе;
строки — **вейвер владельца** (`waivers: max_loc_diff=2706 … by=human:anthony`, решение
04.10 о двух PR). Исключений сверх вейвера не нужно.

## Предупреждения delivery_check, разобранные

- «необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясн…» — строка подписана владельцем и перенесена дословно; трата платного проверяльщика покрыта формулировкой «трата юнитов Ahrefs и платных провайдеров» — решение владельца 04.10, переподпись не нужна; по умолчанию `fixture` — денег нет.
- «class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff» — 38 утверждений без примера спеки перечислены в дайджесте в конце отчёта (порядок правил, партии, гипотеза, слова причин, консоль, настройка провайдера, расход, миграции, ключ в заголовке; пометка `L4` — ссылка на урок, не пример: эти утверждения тоже читать); подписывает владелец при ревью PR.
- «class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, …» — `hypothesis` не в зависимостях — новая зависимость решается владельцем; инварианты (вердикт всегда с источником, партия коммитится целиком, `UNKNOWN` считается ровно) проверены обычными тестами и 21 мутантом.
- «CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента» — было до среза, контур.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет (ни промптов, ни
порогов агента, ни маркера автоотправки). Общий код части объявлен полными путями —
проверка зелёная.

## Spec coverage gaps

- Экран лидов с фильтрами по статусу и причине — срез 1.5.
- `Stage.SALES` и переезд стоп-листа в общие `suppressions` — срез 1.1b.
- Порог по `unknown`/`accept_all` и окно отправки — Ф4.
- Живой DNS и живой Hunter на настоящей базе — заложено.

## Находки в общем коде — не чинились

- Отписка через ответ писалась с этапом кампании — передано соседям, чинится у них (#154);
  наше чтение «без этапа любой причины + `unsubscribed`/`complained` любого этапа» верно для
  старых и новых строк.
- `.gitignore` без `coverage.json` — закрыто соседним #152.
- Хук коммита `silent-except` ловит только широкий `except`, проектный `gates.py` — любой без
  лога и `raise` — расхождение канона и проекта названо координатору.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI, ревью общей части соседней сессией и вейверу владельца;
PR — после слияния части 1.

## Assertion digest (ревью ожиданий, не кода)

База: `sales/1.4a-geo` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **116**, из них без ссылки на пример спеки:
**38**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert (args.command, args.hypothesis) == ("sales-clean", "сайты EN")
-	assert build_parser().parse_args(["sales-clean"]).hypothesis is None
-	assert await _run(session) == EXIT_OK
-	assert capsys.readouterr().out == SUMMARY
-	assert await _run(session) == EXIT_OK
-	assert capsys.readouterr().out.endswith("Очистка продаж: лидов new нет — проверять нечего.\n")
-	assert await _run(session, "--hypothesis", "нет такой") == EXIT_NO_HYPOTHESIS
-	assert capsys.readouterr().out == (
-	assert await _statuses(session) == [LeadStatus.NEW, LeadStatus.NEW]
-	assert await _run(session, "--hypothesis", "сайты  EN") == EXIT_OK
-	assert "Очистка продаж: проверено 1; готово 1; отклонено 0; не проверено 0.\n" in (
-	assert await _statuses(session) == [LeadStatus.READY, LeadStatus.NEW]
A6	assert await _run(session, http=http) == EXIT_VERIFIER_STOPPED
A6	assert capsys.readouterr().out == (
A6	assert await _statuses(session) == [LeadStatus.NEW]
A6	assert await _run(session, http=http) == EXIT_OK
A6	assert capsys.readouterr().out.endswith(
A6	assert await _statuses(session) == [LeadStatus.NEW]
A7	assert capsys.readouterr().out == ""
A7	assert await _statuses(session) == [LeadStatus.NEW]
A7	assert {kind: code for kind, code, _ in _FAILURES}[ConfigError] == EXIT_MISCONFIGURED
-	assert code == EXIT_CANCELLED
-	assert kept in err
-	assert "домены остались" not in err
-	assert set(cleaning.REASON_LABELS) == set(RejectionReason)
-	assert cleaning.REASON_LABELS[RejectionReason.UNDELIVERABLE] == "адрес не существует"
A1	assert report == CleaningReport(
A1	assert await world.rows() == [
A1	assert report == CleaningReport(
A1	assert await world.rows() == [
A2	assert await world.rows() == [expected]
A2	assert (report.ready, report.rejected) == (
A3	assert await world.rows() == [rejected(RejectionReason.UNSUBSCRIBED, note) if closed else READY]
A3	assert await world.rows() == [
A3	assert await world.rows() == [
A8	assert await world.rows() == [
A8	assert report.rejected == Counter({"stoplist": 3})
A8	assert await world.rows() == [
A9	assert await world.rows() == [
-	assert [(row.reason, row.note) for row in await world.rows()] == [
-	assert chosen == CleaningReport(checked=1, ready=1, verified=1, verifier="fixture")
-	assert await world.rows() == [READY, Row(LeadStatus.READY), Row(LeadStatus.REJECTED), UNTOUCHED]
-	assert everyone == CleaningReport(checked=1, ready=1, verified=1, verifier="fixture")
-	assert await world.rows() == [READY, Row(LeadStatus.READY), Row(LeadStatus.REJECTED), READY]
-	assert await world.clean() == CleaningReport(verifier="fixture")
-	assert commits == 3
-	assert report == CleaningReport(
-	assert (await world.rows())[-1] == rejected(
A4	assert sorted(asked) == sorted(routes)  # каждый домен адреса — один раз, домен компании — нет
A4	assert sorted(verifier.asked) == [
A4	assert await world.rows() == [
A4	assert report == CleaningReport(
A5	assert await world.rows() == [
A5	assert report == CleaningReport(
A5	assert await _usage(world.session) == []
A6	assert await world.rows() == [
A6	assert report == CleaningReport(
A6	assert await _usage(world.session) == [
A6	assert healed.asked == [IVAN]
A6	assert again == CleaningReport(checked=1, ready=1, verified=1, paid_units=1, verifier="hunter")
A6	assert await world.rows() == [PAID_READY, PAID_READY]
A6	assert stopped.asked == [IVAN]
A6	assert await world.rows() == [
A6	assert report == CleaningReport(
A6	assert await _usage(world.session) == []
A5	assert await world.rows() == [
A5	assert report == CleaningReport(
A5	assert await _usage(world.session) == [
A8	assert stoplist.read_entries(_file(tmp_path)) == ENTRIES
A8	assert data_first == Entries(["acme.example.test"], [], [(2, "мусор")])
A8	assert header_first == Entries([], [], [(2, "мусор"), (4, ";")])  # пустая строка не в счёт
A8	assert str(refused.value).startswith(words)
A8	assert str(refused.value).startswith(f"файл {tmp_path / 'нет.csv'} не открылся: No such file")
A8	assert loaded == Loaded(added=4, known=0, unreadable=UNREADABLE)
A8	assert await _stored(session) == [
A8	assert await _journal(session) == [
A8	assert (again, partly) == (Loaded(0, 4, UNREADABLE), Loaded(1, 1, []))
A8	assert [host for host, *_ in await _stored(session)] == [*HOSTS, None, "new.example.test"]
A8	assert sources == ["тест", "тест"]  # без заметки источник — автор; повтор строки не даёт
A8	assert await _stored(session) == []
A8	assert await _run(session, path, "--note", "клиенты") == EXIT_OK
A8	assert capsys.readouterr().out == (
A8	assert await _run(session, path) == EXIT_OK
A8	assert capsys.readouterr().out.startswith("Стоп-лист продаж: добавлено 0, уже было 4 ")
A8	assert {note for *_, note in await _stored(session)} == {"клиенты"}
A8	assert await _run(session, _file(tmp_path, "acme.example.test\n")) == EXIT_OK
A8	assert await _stored(session) == [("acme.example.test", None, "консоль", "стоп.csv")]
A8	assert await _run(session, _file(tmp_path, "Домен\nмусор\n")) == EXIT_BAD_INPUT
A8	assert capsys.readouterr().out == (
A8	assert await _stored(session) == []
A8	assert await _run(session, tmp_path / "нет.csv") == EXIT_BAD_INPUT
A8	assert capsys.readouterr().out.startswith(
A8	assert spec is not None, MIGRATION
A8	assert spec.loader is not None, MIGRATION
-	assert await connection.run_sync(_present) == SCHEMA
-	assert (after_downgrade, after_upgrade) == (set(), SCHEMA)
A5	assert found == verdict
A5	assert (found.deliverable, found.words) == (deliverable, words)
A5	assert set(DELIVERABLE_BY_STATUS) == {
A5	assert dead == set(UNDELIVERABLE_WORDS) == {"invalid", "disposable"}
A6	assert type(refused.value) is kind
A6	assert str(refused.value) == words
-	assert (
-	assert request.headers["Authorization"] == f"Bearer {KEY}"
-	assert KEY not in str(request.url)
A5	assert (found, found.units, FixtureVerifier.name) == (verdict, 0, "fixture")
-	assert type(built) is kind
-	assert warned is (kind is FixtureVerifier)  # о выдуманных вердиктах — предупреждение
A7	assert str(refused.value) == words
L4	assert sales_cfg._Sales(_env_file=None).verifier_provider == "fixture"
L4	assert sales_cfg._Sales(_env_file=None).verifier_provider == "live"
-	assert usage.OPERATION_PROVIDERS["sales_verify"] is UsageProvider.HUNTER
-	assert (row.provider, row.operation, row.units, row.amount_usd, row.system, row.run_id) == (
-	assert spec is not None, MIGRATION
-	assert spec.loader is not None, MIGRATION
-	assert (await connection.run_sync(_hunter_values)).count("hunter") == 1
```

Привязаны к примерам: **A1 A2 A3 A4 A5 A6 A7 A8 A9 L4**. Остальные 38 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 38
