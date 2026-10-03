# Verify report

**Поставка:** срез `sales-import`, **часть 3 из 3 — 1.3c, API предпросмотра и загрузки**.
Часть 1 (ядро) — #147, часть 2 (ссылка и консоль) — #150. Этой частью срез завершается.

**Date:** 2026-10-03
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=4 утверждения без примера спеки ждут подписи человека — коды и тексты отказов сервера и форма ответа; человека в цепочке нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена; PR — после слияния #150 (иначе дифф против main несёт обе части и пробивает предохранитель)
**Commit:** T7 — прогоны сняты на дереве T6 `5f80a70`; T7 меняет только документы среза

## Сборка

Ветка стеком на части 2 (`6eda12b`); коммиты части перенесены из ветки среза через ветку-базу
без правки кода и без конфликтов: `e467b80` (маршруты, схемы, роутер, отказы), `b065fbe`
(тесты), `5f80a70` (снимок сложности). Код части — 6 файлов, +367/−0: `api/app.py`,
`api/errors.py`, `api/sales/{__init__,routes,schemas}.py`, `tests/test_api_sales_intake.py`.
Перед PR ветка перебазируется на main после слияния #150 — проверки повторяются против
`origin/main`.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0 (после `npm ci` во фронте дерева: без него ESLint и Prettier падают на окружении, фронт частью не тронут).
- [x] PASS — `ruff check` exit 0; `ruff format --check` — 488 файлов; `mypy backend/` — 309
      файлов, ошибок нет; `scripts/gates.py` — 484 файла, нарушений нет (в том числе
      `public-repo`); `lint-imports` — 4 контракта целы; ратчет сложности — 323 файла,
      расхождений нет.
- [x] PASS — подпись необратимого: exit 0; четыре строки STATUS не тронуты.
- [x] PASS — голова Alembic одна: `1f7b0ee634c2` (миграций в части нет).
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=sales/1.3b-sheet-cli
      check_diff_coverage.sh` на JSON полного прогона: `api/app.py` 100 %, `api/errors.py`
      100 %, `api/sales/routes.py` 95,5 %, `api/sales/schemas.py` 100 % — выше цели 70 %.
- [x] PASS — `contour_waves --base sales/1.3b-sheet-cli` (режим CI): «нарушений нет»,
      общий код части объявлен в `shared_changes:`.
- [x] PASS — `delivery_check --require-ci --diff-base sales/1.3b-sheet-cli`: 0 errors,
      4 warnings (разобраны ниже); предохранитель — 6 файлов, net 367 при пределах 25 и 800.

## Behavior oracles

- [x] PASS — `tests/test_api_sales_intake.py`: 13 тестов настоящим приложением на базе
      дерева — A5 (закрытая таблица через сервер → 400 со словами), A6 (ручное сопоставление
      в форме), A7 (5 000 строк: предпросмотр укладывается в порог 5 с, в базе ни лида, ни
      домена, ни записи журнала), A8 (загрузка: лиды, `contact_id` пуст, строк `contacts` нет,
      журнал с автором), отказы 404 (гипотеза) и 502 («Google не ответил»), 403 без права.
      Google — только `httpx.MockTransport`.
- [x] PASS — полный набор на базе дерева: **3396 passed за 299 с**, exit 0 (один прогон,
      он же дал JSON покрытия).

### Красный прогон до кода

Дерево с `backend/` = часть 2 (без API): `tests/test_api_sales_intake.py` — 1 error при
сборе: `ImportError: cannot import name 'routes' from 'backend.api.sales'`.

### Обратные прогоны

Сняты строителем 03.10 на ветке среза на том же коде: 6 обратных прогонов и 32 ручных мутанта
по всему срезу — все красные; среди них коды отказов сервера и «ничего в базе» при
предпросмотре. На этой ветке повторно не снимались.

## Живой прогон

Маршруты живьём не гонялись — исполняются сьютом настоящим приложением (`AsyncClient`
поверх `app`) на базе дерева; статус «написано и под тестами». Экран-вызывающий — срез 1.5.

## Product oracles

- [x] PASS — `eval-smoke.md`: все пункты среза отмечены.

## Ревью рисковых мест

- **безопасность** — маршруты под `Permission.SALES` (без права — 403 словами, тест);
  источник — байты `multipart/form-data` или ссылка, которую сервер не берёт как есть:
  `sheet.export_url` принимает только `docs.google.com` и собирает адрес из ключа таблицы.
  Состояния на сервере нет (мастер присылает источник второй раз) — нечего угнать между
  шагами; предел размера — 10 МБ. Отказы — через общий `api/errors.py`: сырой текст
  исключения наружу не уходит.
- **интеграция** — Google: сеть, 429 и 5xx → 502 «повторите позже или загрузите CSV»;
  в сьюте транспорт подменён, живой замер — часть 2.
- **производительность** — A7: предпросмотр 5 000 строк без записи в базу, порог в тесте
  5 с (замер ядра: чтение 0,04 с, предпросмотр 0,25 с); загрузка — пачками по 1 000
  доменов, как в ядре.
- **транзакция БД** — загрузка тем же `intake.load`: один `commit` после успеха; при нуле
  годных лидов ничего не пишется, даже журнал.
- **деньги** — риска нет: трат и провайдеров нет.

## Предохранитель

6 файлов, net 367 при пределах 25 файлов и 800 строк. Исключений не нужно.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана и перенесена
  дословно; переподпись владельца позже. Часть внешних поверхностей не открывает.
- «asserts_reviewed_by deferred» — 4 утверждения ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis не в зависимостях.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет. Общий код части
объявлен — проверка зелёная.

## Spec coverage gaps

- Страна словом и часовой пояс — срез 1.4 (решение владельца 03.10).
- Экран и мастер загрузки поверх этого API — срез 1.5.

## Находки в общем коде — не чинились

Новых нет.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, решение координатора и владельца; PR — после #150.

## Assertion digest (ревью ожиданий, не кода)

База: `sales/1.3b-sheet-cli` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **17**, из них без ссылки на пример спеки:
**4**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A7	assert response.status_code == 200, response.text
A7	assert counts == ["leads.csv", 5000, 5000, 0, None]
A7	assert (body["mapping"], body["needs_mapping"]) == (
A7	assert [lead["line"] for lead in body["leads"]] == list(range(2, 52))  # первые 50
A7	assert spent < 5, f"предпросмотр 5000 строк шёл {spent:.1f} с"
A7	assert await _written(session) == before
A1	assert response.status_code == 200, response.text
A1	assert [response.json()[name] for name in ("loaded", "accepted", "rejected")] == [2, 2, 0]
A1	assert author == await session.scalar(select(UserModel.id).where(UserModel.email == SELLER))
A1	assert await _written(session) == (2, 2, 1)
-	assert (response.status_code, asked) == (403, [])
A5	assert (response.status_code, response.json()["detail"]) == (400, sheet.CLOSED)
-	assert (response.status_code, response.json()["detail"]) == (status, words)
A6	assert (body["header"], body["needs_mapping"], body["accepted"]) == (False, False, 1)
A6	assert body["leads"][0]["name"] == "Иван"
-	assert (response.status_code, response.json()["detail"]) == (400, refused)
-	assert (response.status_code, response.json()["rows"]) == (200, 1024)
```

Привязаны к примерам: **A1 A5 A6 A7**. Остальные 4 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 4
