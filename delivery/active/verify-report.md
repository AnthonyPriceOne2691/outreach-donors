# Verify report

**Поставка:** срез `sales-cleaning`, **часть 1 из 2 — 1.4a, страна кодом по таблице названий
и часовой пояс лида**. Часть 2 — 1.4b+c (правила очистки, стоп-лист, проверяльщик адресов,
расход, консоль) — следующим PR под вейвер владельца (решение владельца 04.10 о двух PR).

**Date:** 2026-10-04
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** n/a (все 23 утверждения ведут к одобренным примерам спеки A1–A4 — дайджест в конце отчёта; примеры подписаны `human_ok_spec` до кода)
**CI run:** нет — ветка не запушена; PR открывается этим коммитом, слив — по зелёному CI
**Commit:** T4 — тяжёлые прогоны сняты на дереве T0б `6946394` (код части — T1 `00878a1`, снимок — T3 `792323e`); T2б `ffcb824` добавил только пометки примеров в тестах, T4 — только документы среза; тесты части повторены на `ffcb824`

## Сборка

Ветка `sales/1.4a-geo` от main `c14b2ab` (после #153 — срез 1.3 завершён). Код части перенесён
из ветки среза `sales/1.4-cleaning` @ `3c30528` по файлам, без правки (`git checkout
sales/1.4-cleaning -- …`): `cf78cef` T0 (документы: `sales-import` в архив, активный срез —
часть 1 из 2), `00878a1` T1 (`sales/geo.py` — новый, `columns.py`, `intake.py`), `0accb72` T2
(`tests/test_sales_geo.py` — новый; ожидания `test_sales_intake.py`, `test_sales_intake_cli.py`),
`792323e` T3 (снимок сложности), `6946394` T0б (decisions и plan по форме), `ffcb824` T2б
(пометки примеров спеки в блоках тестов). Код части — 6 файлов, +662/−40 (net 622). Миграций
нет: колонки `country` и `timezone` у лида есть с 1.1a; голова Alembic прежняя — `1f7b0ee634c2`.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0.
- [x] PASS — `ruff check` exit 0; `ruff format --check` — 492 файла; `mypy backend/` — 311
      файлов, ошибок нет; `scripts/gates.py` — 488 файлов, нарушений нет (в том числе
      `public-repo`); `lint-imports` — 4 контракта целы (`mail-does-not-know-sales` — цел);
      ратчет сложности — расхождений нет (`geo.py` — сложность 4).
- [x] PASS — подпись необратимого (`check_irreversible_signature.sh`): exit 0; четыре строки
      STATUS перенесены слово в слово.
- [x] PASS — голова Alembic одна и прежняя: `1f7b0ee634c2` (миграций в части нет).
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=origin/main
      check_diff_coverage.sh` на JSON полного прогона (как в CI: `LINT_BE_DIR=.`,
      `LINT_COV_PKG=backend`, `LINT_PY_SRC=backend`), exit 0:

      | файл | stmts | miss | cov% |
      |---|---|---|---|
      | backend/features/sales/columns.py | 28 | 0 | 100.0 |
      | backend/features/sales/geo.py | 34 | 0 | 100.0 |
      | backend/features/sales/intake.py | 221 | 2 | 99.1 |

      Две строки `intake.py` мимо — ветки 1.3, не этой части.
- [x] PASS — `contour_waves --base origin/main` (режим CI): «нарушений нет»; общего кода
      часть не трогает (`shared_changes:` так и говорит), в дереве продаж 7 файлов.
- [x] PASS — `delivery_check --require-ci --diff-base origin/main`: 0 errors, 3 warnings
      (разобраны ниже); предохранитель — 6 файлов, net 622 при пределах 25 и 800.

## Behavior oracles

- [x] PASS — `tests/test_sales_geo.py`: 29 тестов (A1–A4) на базе дерева — форма таблицы
      (249 кодов уникальны и строчные, каждый пояс таблицы и `CAPITAL_ZONES` есть в базе поясов
      машины, столичный пояс ровно у стран без единого пояса и со столицей), названия RU/EN
      и псевдонимы, столичный пояс с замечанием, колонка побеждает, непонятое не роняет
      строку, запись `country` и `timezone` в базу. Вместе с ожиданиями `test_sales_intake.py`
      и `test_sales_intake_cli.py` — **73 passed за 1,1 с** (повтор на `ffcb824`).
- [x] PASS — полный набор на базе дерева (`6946394`): **3456 passed за 294 с**, exit 0 —
      один прогон, он же дал JSON покрытия.
- [x] PASS — A7 среза 1.3 с кодом части: `test_api_sales_intake.py` 13 passed; предпросмотр
      5 000 строк — 0,52 с при пороге 5 с (таблица стран теперь на пути каждой строки).

### Красный прогон до кода

Дерево T0 `cf78cef` (`git archive`, без `geo.py`) + `tests/test_sales_geo.py` из T2: сбор
тестов падает — `ImportError: cannot import name 'geo' from 'backend.features.sales'`,
«no tests collected, 1 error». Повторено 04.10 при verify.

### Обратные прогоны

Сняты строителем 04.10 на ветке среза `sales/1.4-cleaning` на том же коде (одна подмена за раз,
свои тесты): «столичный пояс без замечания» (`geo.py`) — 2 failed, 27 passed; «нет столичного
пояса» (`geo.py`) — 3 failed, 26 passed; «замечание о столице не пишется» (`intake.py`) —
1 failed, 28 passed. **3 мутанта — 3 убито.** На этой ветке повторно не снимались: код тот же
побайтно (перенос файлами).

## Живой прогон

Живого прогона нет и не нужно: сети и внешних вызовов часть не приносит. Таблица судится по
форме против базы поясов машины, путь строки файла и запись — сьютом на настоящей базе дерева.
Три статуса: **написано и под тестами** — страна кодом, пояс из колонки и по стране, замечания
в отчёте строки; **замерено живьём** — ничего; **заложено** — окно отправки по поясу (Ф4) и
порог к лидам без пояса.

## Product oracles

- [x] PASS — `eval-smoke.md`: все четыре пункта части отмечены.

## Ревью рисковых мест

- **безопасность** — вход части — ячейки файла пользователя (страна, пояс). `geo._key`
  оставляет только буквы после NFKD (`isalpha`), регулярок по пользовательскому тексту нет;
  `geo.zone_name` ищет имя в словаре `_zones()` (из `available_timezones()` и таблицы), а не
  строит `ZoneInfo(text)` — путь вида `../..` до файлов базы поясов не доходит. В базу уходят
  только код из таблицы (две буквы) и имя пояса из словаря; длина полей не растёт. Прав,
  маршрутов и общего кода часть не трогает (`shared_changes:`).
- **производительность** — индексы `_by_name`, `_zones`, `_timezones` строятся один раз на
  процесс (`lru_cache(maxsize=1)`), `available_timezones()` читает каталог базы поясов тоже
  один раз; на строку — нормализация двух ячеек и поиск в словарях. Замер: A7 среза 1.3
  (предпросмотр 5 000 строк) с кодом части — 0,52 с при пороге 5 с.
- **новый модуль** — `sales/geo.py`: данные (249 строк таблицы, `ALIASES`, `CAPITAL_ZONES`) и
  пять функций, сложность 4, покрытие 100 %; импортирует только стандартную библиотеку
  (`unicodedata`, `functools`, `zoneinfo`) — `new_dependency: no`; снаружи его зовёт один
  `intake.py`, контракт `mail-does-not-know-sales` цел. Риск нового модуля — база поясов
  в образе: без неё `zone_name` принимал бы только пояса таблицы. Проверено на образе
  `python:3.12-slim` (локальная сборка backend): `available_timezones()` — 486 имён,
  `America/Chicago` и `Europe/Kyiv` на месте; `_zones()` в любом случае добавляет пояса
  таблицы, так что пояс по стране без базы не отвергался бы.
- **транзакция БД** — путь записи прежний (`intake.load`, один `commit` после успеха);
  новых запросов нет: два поля в той же строке лида.
- **деньги** — риска нет: трат и провайдеров в части нет.

## Предохранитель

6 файлов, net 622 при пределах 25 файлов и 800 строк. Исключений не нужно.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана владельцем и
  перенесена дословно; часть внешних поверхностей не открывает (ни сети, ни трат, ни записи вне
  своих колонок лида); переподпись не нужна (решение владельца 04.10).
- «Ни одного реляционного оракула» — `hypothesis` не в зависимостях; инвариант формы таблицы
  (каждый пояс есть в базе поясов, столичный пояс ровно у стран без единого) — обычным тестом.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет (ни промптов, ни
порогов агента, ни маркера автоотправки). Общий код не тронут — проверка зелёная.

## Spec coverage gaps

- Правила очистки, стоп-лист, проверка адресов, расход, `outreach sales-clean` — часть 1.4b+c
  (следующий PR, вейвер владельца).
- Экран лидов с фильтрами по стране и поясу — срез 1.5.
- Окно отправки по поясу и порог к лидам без пояса — Ф4.

## Находки в общем коде — не чинились

Новых нет. Находка 1.4 «`.gitignore` без `coverage.json`» закрывается соседним #152.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI и решению владельца о двух PR (04.10); общего кода нет —
ревью соседней сессии не требуется.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **23**, из них без ссылки на пример спеки:
**0**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A4	assert len(set(codes)) == len(codes) == 249
A4	assert all(len(code) == 2 and code.isascii() and code.islower() for code in codes)
A4	assert all(en and ru for _, en, ru, _ in geo.COUNTRIES)
A4	assert {zone for *_, zone in geo.COUNTRIES if zone} <= zones
A4	assert all(code in dict.fromkeys(codes) for code in geo.ALIASES.values())
A4	assert set(geo.CAPITAL_ZONES) == without_zone - NO_ZONE_AT_ALL
A4	assert set(geo.CAPITAL_ZONES.values()) <= zones
A1	assert [(code, geo.timezone_for(code)) for code, _ in ONE_ZONE] == list(ONE_ZONE)
A1	assert [geo.by_capital(code) for code, _ in ONE_ZONE] == [False] * len(ONE_ZONE)
A2	assert [(code, geo.timezone_for(code)) for code, _ in SEVERAL_ZONES] == list(SEVERAL_ZONES)
A2	assert [geo.by_capital(code) for code, _ in SEVERAL_ZONES] == [True] * len(SEVERAL_ZONES)
A4	assert [geo.timezone_for(code) for code in ("xx", *sorted(NO_ZONE_AT_ALL))] == [None] * 5
A1	assert geo.country_code(text) == code
A3	assert geo.zone_name(text) == zone
A3	assert guess(titles) == {
A3	assert guess(["email", "time zone"])[LeadField.TIMEZONE] == 1
A3	assert found.mapping[LeadField.TIMEZONE] == 2
A3	assert [(lead.country, lead.timezone) for lead in found.leads] == FILE_LEADS
A2	assert found.problems == FILE_NOTES
A2	assert found.rejected == 0
A4	assert loaded == 7
A4	assert [(country, zone) for country, zone in rows] == FILE_LEADS
A1	assert [(lead.country, lead.timezone, lead.language) for lead in found.leads] == [
```

✅ **Каждое утверждение ведёт к примеру спеки** (A1 A2 A3 A4), а примеры человек
подписал до кода (`human_ok_spec`). Подпись под дайджестом здесь
**не требуется**: она уже стоит, заранее и на числах. Пиши в verify-report
`asserts_reviewed_by: n/a (все утверждения ведут к одобренным примерам)`.

asserts_without_example: 0
