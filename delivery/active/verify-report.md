# Verify report

**Поставка:** срез `sales-leads-screen`, **часть 1 из 2 — 1.5a, списки раздела на сервере**
(гипотезы со счётчиками, лиды с фильтрами под правом `sales`). Часть 2 — экран, мастер
загрузки, замеры — следующим PR под вейвер строк владельца (решение владельца 04.10).

**Date:** 2026-10-04
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=29 утверждений без примера спеки ждут подписи человека — счётчики гипотез, порядок лидов, страница и её размер, незнакомые коды, отказ схемы 422; человека в цепочке нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR; пре-пуш гоняет полный набор
**Commit:** T4 — прогоны сняты на дереве T1б `4e4e419`; T4 меняет только документы среза

## Сборка

Ветка `sales/1.5a-lists` от main `9bee976` (после #155, #159 и #157). `c7e53af` T0 — документы:
`sales-cleaning` в архив, активный срез `sales-leads-screen`, часть 1 из 2. `9571684` T1 — код
и тесты из ветки среза `sales/1.5-screen` (коммит списков) без правки кода; из теста убрана
сверка кодов сервера со словами экрана — она читает файлы экрана, которые приходят частью 2,
и без них красная (5 падений на голове T1 ветки среза). Сверка вернётся в 1.5b вместе с
файлами экрана. `4e4e419` T1б — пометка примера A2 в тесте, ссылка на код в решении.
Код части — 4 файла, +639/−5 (net 634). Миграций нет; голова Alembic прежняя.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0.
- [x] PASS — `ruff check` exit 0; `mypy backend/` — 315 файлов, ошибок нет; `scripts/gates.py` —
      497 файлов, нарушений нет; `lint-imports` — 4 контракта целы; ратчет сложности —
      расхождений нет (новый `browse.py`).
- [x] PASS — гейт дублей `check_jscpd_gate.sh`: 55 пар при снимке 55; ратчет снимков
      `check_baseline_ratchet.sh`: 15 снимков сверено с main.
- [x] PASS — подпись необратимого: сходится; четыре строки STATUS перенесены слово в слово.
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=origin/main check_diff_coverage.sh`
      на JSON прогона тестов раздела (`test_api_sales_screen.py`, `test_api_sales_intake.py`),
      exit 0:

      | файл | stmts | miss | cov% |
      |---|---|---|---|
      | backend/api/sales/routes.py | 64 | 4 | 93.8 |
      | backend/api/sales/schemas.py | 69 | 0 | 100.0 |
      | backend/features/sales/browse.py | 76 | 8 | 89.5 |

- [x] PASS — `contour_waves --base origin/main` (режим CI): нарушений нет; свои файлы раздела вне
      `SALES_PATHS` объявлены в `shared_changes:`.
- [x] PASS — `delivery_check --require-ci --diff-base origin/main`: 0 errors (предупреждения ниже);
      предохранитель — 4 файла, net 634 при пределах 25 и 800.

## Behavior oracles

- [x] PASS — `tests/test_api_sales_screen.py`: 13 тестов настоящим приложением на базе дерева —
      гипотезы со счётчиками по состояниям, лиды новейшими первыми с доменом и гипотезой, A2
      (состояние и причина сужают список по именам адреса), гипотеза и поиск по адресу, имени,
      компании и домену, 20 на странице и размер от сервера, незнакомая причина или гипотеза
      ничего не находит, состояние вне перечня — 422, A4 (оба списка без права — 403 словами).
      Вместе с `test_api_sales_intake.py` (1.3c, тот же роутер) — 26 passed.
- [x] Полный набор — пре-пуш и CI на PR. На всём срезе (обе части) строитель снял полный
      набор: 3 576 passed за 5 мин 19 с.

### Красный прогон до кода

Дерево T0 `c7e53af` (`git archive`, без кода части) + `tests/test_api_sales_screen.py`: 13 failed
из 13 — маршрутов списков нет.

### Обратные прогоны

Одна подмена в `browse.py` за раз, тесты списков, восстановление (04.10, это дерево):

| Мутант | Файл | Итог | |
|---|---|---|---|
| состояние не фильтрует | browse.py | 4 failed, 9 passed | убит |
| причина не фильтрует | browse.py | 3 failed, 10 passed | убит |
| старые лиды первыми | browse.py | 5 failed, 8 passed | убит |
| страница не сдвигается | browse.py | 1 failed, 12 passed | убит |
| нулевые коды сводки не названы | browse.py | 2 failed, 11 passed | убит |
| сводка причин считает и не отклонённых | browse.py | 13 passed | выжил |

**5 из 6 убиты.** Выживший неотличим при нынешних данных: очистка (1.4) пишет причину только
вместе с состоянием `rejected`, и лида с причиной в другом состоянии код не производит; условие
в `_reasons` — защита от ручной правки базы. Тест на такой лид — заложено.

## Живой прогон

Живьём маршруты не гонялись: исполняются сьютом настоящим приложением на базе дерева; живой
прогон экрана поверх них — часть 2 (строитель снял его на своей базе: A1, A2, контраст).
Три статуса: **написано и под тестами** — списки, фильтры, страница, сводка, 403 и 422;
**замерено живьём** — ничего в этой части; **заложено** — поиск подстрокой на базе в десятки
тысяч лидов (без индекса под подстроку).

## Product oracles

- [x] PASS — `eval-smoke.md`: пункты части отмечены.

## Ревью рисковых мест

- **безопасность** — оба маршрута под `Permission.SALES` (без права — 403 словами, тест на оба);
  только чтение; фильтры — параметры запроса через схему (`state` — перечень, иначе 422),
  поиск идёт параметром в `ilike`, а не строкой в SQL; наружу — только поля лида и гипотезы,
  без служебных.
- **транзакция БД** — только чтение: ни `commit`, ни записи; три запроса на страницу (строки,
  счёт, сводка состояний и причин) в одной сессии запроса.
- **производительность** — страница 20, предел 100 (`MAX_PAGE_SIZE`); счётчики гипотез — один
  запрос с группировкой, а не запрос на гипотезу; сводка — две группировки по всей таблице
  лидов. Поиск `ilike '%…%'` индекс не берёт — на базе в десятки тысяч лидов это полный
  просмотр с пределом страницы; замер на большой базе — заложено.
- **интеграция** — внешних вызовов нет; роутер продаж зарегистрирован в 1.3c, клиент Google
  того же модуля (`sheet.client()`) часть не трогает.
- **новый модуль** — `backend/features/sales/browse.py`: чтение для экрана, сложность в ратчете,
  покрытие 89,5 %; импортирует только модели продаж и домены; контракт
  `mail-does-not-know-sales` цел.

## Предохранитель

4 файла, net 634 при пределах 25 файлов и 800 строк. Исключений не нужно.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана владельцем и
  перенесена дословно; часть внешних поверхностей не открывает (только чтение базы).
- «asserts_reviewed_by deferred» — 29 утверждений ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — `hypothesis` не в зависимостях проекта.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет. Свои файлы раздела вне
`SALES_PATHS` объявлены — проверка зелёная.

## Spec coverage gaps

- A1, A3 и экранная сторона A2 и A4 — часть 1.5b.
- Сверка кодов сервера со словами экрана — часть 1.5b, вместе с файлами экрана.

## Находки в общем коде — не чинились

Новых нет в этой части. Находки среза (контурные значки в `glass.css`, форма в общем
`client.ts`, общий помощник строки запроса) — в verify-report части 2.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI; общего кода нет, свои файлы раздела объявлены.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **33**, из них без ссылки на пример спеки:
**29**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert response.status_code == 200, response.text
-	assert body["total"] == 2
-	assert (en["id"], en["name"], en["description"]) == (
-	assert (en["leads"], en["total"]) == ({"new": 1, "ready": 1, "rejected": 2}, 4)
-	assert (ru["leads"], ru["total"]) == ({"new": 1, "ready": 0, "rejected": 0}, 1)
-	assert ru["created_at"] is not None
-	assert response.status_code == 200, response.text
-	assert (body["total"], body["page"], body["limit"]) == (5, 1, 20)
-	assert _emails(body) == [
-	assert {
-	assert twin["cleaning_note"] == "дубль: адрес уже у лида №1"
-	assert twin["hypothesis_id"] == field.en.id
-	assert body["states"] == {"new": 2, "ready": 1, "rejected": 2}
-	assert set(body["reasons"]) == REASONS
-	assert {code: n for code, n in body["reasons"].items() if n} == {"duplicate": 1, "no_mail": 1}
A2	assert response.status_code == 200, response.text
A2	assert (_emails(response.json()), response.json()["total"]) == (emails, len(emails))
A2	assert response.json()["states"] == {"new": 2, "ready": 1, "rejected": 2}
-	assert response.status_code == 200, response.text
-	assert await found(f"hypothesis={field.ru.id}") == ["olga@delta.example.test"]
-	assert await found("search=ACME") == ["twin@acme.example.test", "ivan@acme.example.test"]
-	assert await found("search=петров") == ["ivan@acme.example.test"]
-	assert await found("search=beta") == ["maria@beta.example.test"]
-	assert await found("search=acme-twin") == ["twin@acme.example.test"]
-	assert await found(f"hypothesis={field.en.id}&state=new") == ["ivan@acme.example.test"]
-	assert (len(first["rows"]), first["total"], first["page"], first["limit"]) == (20, 25, 1, 20)
-	assert (len(second["rows"]), second["page"]) == (5, 2)
-	assert second["rows"][-1]["email"] == "ivan@acme.example.test"  # самый старый — последним
-	assert (beyond["rows"], beyond["total"]) == ([], 25)
-	assert (len(narrow["rows"]), narrow["limit"]) == (5, 5)
-	assert (response.status_code, response.json()["total"]) == (200, 0), query
-	assert response.status_code == 422
A4	assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
```

Привязаны к примерам: **A2 A4**. Остальные 29 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 29
