# Verify report

**Поставка:** срез `sales-import`, **часть 2 из 3 — 1.3b, Google-таблица по ссылке и консоль**.
Часть 1 (ядро) слита — #147; часть 3 (API) — следующим PR.

**Date:** 2026-10-03
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=13 утверждений без примера спеки ждут подписи человека — коды выхода и полный вывод консоли, адрес экспорта по ссылке, предел размера, байты с экрана; человека в цепочке нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена: пуш, PR и прогон CI делает координатор
**Commit:** T7 — прогоны сняты на дереве T6 `fb3ff27`; T7 меняет только документы среза

## Сборка

Коммиты части перенесены на main `360032c` из ветки среза через ветку-базу без правки кода
и без конфликтов: `9ca9292` (ссылка и байты), `234adc5` (команда), `b250dc6` (предел
в мегабайтах), `7037858` (тесты), `fb3ff27` (снимок сложности). Код части — 7 файлов,
+579/−8: `sales/sheet.py` (новый), `sales/intake.py`, `sales/hypotheses.py`, `cli/sales.py`,
`cli/main.py`, `tests/test_sales_intake_sheet.py`, `tests/test_sales_intake_cli.py`.

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, exit 0.
- [x] PASS — `ruff check backend/ tests/ scripts/` exit 0; `ruff format --check` — 484 файла,
      exit 0; `mypy backend/` — 306 файлов, ошибок нет; `scripts/gates.py` — 480 файлов,
      нарушений нет (в том числе `public-repo`); `lint-imports` — 4 контракта целы, включая
      `mail-does-not-know-sales`; ратчет сложности — 320 файлов, расхождений нет.
- [x] PASS — подпись необратимого `scripts/check_irreversible_signature.sh`: exit 0; четыре
      строки STATUS не тронуты.
- [x] PASS — голова Alembic одна: `1f7b0ee634c2` (миграций в части нет).
- [x] PASS — покрытие изменённых prod-файлов, `STRICT=1 BASE=origin/main
      check_diff_coverage.sh` на JSON полного прогона: `sales/sheet.py` 100 %,
      `sales/hypotheses.py` 100 %, `sales/intake.py` 99,5 %, `cli/main.py` 96,5 %,
      `cli/sales.py` 88,3 % — все выше цели 70 %, exit 0.
- [x] PASS — `contour_waves --base origin/main` (режим CI): после объявления `cli/main.py`
      и `cli/sales.py` в `shared_changes:` — «нарушений нет»; до объявления — красный
      «общий код тронут без объявления» (проверка работает).
- [x] PASS — `delivery_check --require-ci --diff-base origin/main`: 0 errors, 4 warnings
      (разобраны ниже); предохранитель — 7 файлов, net 571 при пределах 25 и 800.

## Behavior oracles

- [x] PASS — `tests/test_sales_intake_sheet.py` и `tests/test_sales_intake_cli.py`:
      32 теста — A5 (закрытая таблица при 200 со страницей входа, 401, 403; нет таблицы;
      нет листа), A9 (сеть, 429, 5xx — «Google не ответил», не «закрыта»), предел размера
      в мегабайтах, байты с экрана, адрес экспорта по двум видам ссылок; A2 и A6 через
      консоль на настоящей базе — вывод сверяется целиком, коды выхода свои.
      Google — только `httpx.MockTransport`, в сеть сьют не ходит.
- [x] PASS — полный набор на базе дерева: **3383 passed за 354 с**, exit 0 (один прогон,
      он же дал JSON покрытия).

### Красный прогон до кода

Дерево с `backend/` = `origin/main` (ядро есть, части 2 нет), тесты части: 2 errors при
сборе — `ImportError: cannot import name 'sheet' from 'backend.features.sales'`,
`cannot import name 'run_import' from 'backend.cli.sales'`.

### Обратные прогоны

Сняты строителем 03.10 на ветке среза на том же коде (коммиты перенесены без правки):
6 обратных прогонов правил и 32 ручных мутанта по всему срезу — все красные; в их числе
правила части 2: закрытая таблица, «Google не ответил», предел размера, коды выхода консоли.
На этой ветке повторно не снимались — «замерено на ветке среза», не «на этой».

## Живой прогон

Google, 03.10, через код среза, четыре настоящие ссылки (в репозиторий не попали):
открытая таблица читается; закрытая — «таблица не открыта по ссылке — откройте доступ или
загрузите CSV»; несуществующая таблица и несуществующий лист — каждая со своими словами.
Команда `outreach sales-import` живьём на этой ветке не гонялась — покрыта тестами с полным
выводом; статус «написано и под тестами».

## Product oracles

- [x] PASS — `eval-smoke.md`: пункты части 2 отмечены; API — путь части 3.

## Ревью рисковых мест

- **безопасность** — сервер ходит по ссылке, которую дал человек. Риск — запрос на чужой
  хост (SSRF) или по произвольному адресу. Держит `sheet.export_url`: ссылка разбирается,
  принимается только `docs.google.com` со схемой `/spreadsheets/d/…`, а адрес запроса
  собирается заново из ключа таблицы и `gid` по двум шаблонам (`_EXPORT`, `_PUBLISHED`) —
  произвольный путь или хост наружу не уходит; тест на «не ссылка на Google-таблицу».
  Ключей и учёток нет: читаются только таблицы, открытые по ссылке, и это сказано словами
  в отказе. Право на команду — консоль, не API (API — часть 3 под `Permission.SALES`).
- **интеграция** — `httpx` к `docs.google.com`: таймаут и предел размера заданы, сеть/429/5xx
  → свой отказ «Google не ответил — повторите позже или загрузите CSV» (A9), а не 500 и не
  «закрыта» (урок L64). В сьюте транспорт подменён; живой замер — выше.
- **транзакция БД** — запись лидов из консоли идёт тем же `intake.load`, что в части 1:
  один `commit` после успеха, отказ файла — до записи; `--dry-run` базу не трогает.
- **деньги** — риска нет: трат нет, провайдеров нет; чтение Google бесплатно и без ключей.
- **производительность** — предел размера ответа Google задан в мегабайтах, как у файла,
  и судится до разбора; таймаут запроса задан; одна ссылка — один GET, параллельных запросов
  нет. Чтение 5 000 строк — замер ядра на ветке среза: 0,04 с чтение, 0,25 с предпросмотр;
  A7 — путь части 3 через API.

## Предохранитель

Часть 1.3b — 7 файлов, net 571 (+579/−8) против `origin/main` при пределах 25 файлов и 800
строк. Исключений не нужно; `waivers:` части 1 из STATUS снят.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана и перенесена
  дословно; на handoff — ошибка, переподпись владельца позже. Часть внешних поверхностей
  не открывает: один GET к Google без ключей, ничего не меняет и не тратит.
- «asserts_reviewed_by deferred» — 13 утверждений ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis не в зависимостях, новая зависимость — вне
  среза.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.
- «Рисковый дифф: безопасность, интеграция, производительность» и «decisions без цены» —
  разобраны: блок ревью выше и строка решения о `normalize_host` с номером PR и числом.

## Проверка волн

Волны: В0 deployed · В1 deployed · В-обн deployed; В2, В3а–в, В4 — pending, их триггеров
в части нет (ни промптов, ни порогов, ни автоотправки). В дереве продаж — 6 файлов.
Общий код части объявлен в `shared_changes:` — проверка зелёная.

## Spec coverage gaps

- A7 (5 000 строк — предпросмотр за секунды, без базы) — путь части 3 через API;
  замер ядра на ветке среза: чтение 0,04 с, предпросмотр 0,25 с.
- Страна словом и часовой пояс — срез 1.4 (решение владельца 03.10).

## Находки в общем коде — не чинились

Новых нет. Прежние (`normalize_host` на `[`; местная бесплатная почта) переданы хозяину
и взяты им 03.10.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, решение координатора и владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **23**, из них без ссылки на пример спеки:
**13**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A2	assert await _run(session, _source(tmp_path, BASE), "--dry-run") == 0
A2	assert capsys.readouterr().out == PREVIEW + "\nПредпросмотр: в базу ничего не записано.\n"
A2	assert await _leads(session) == 0
-	assert await _run(session, _source(tmp_path, BASE)) == 0
-	assert capsys.readouterr().out == PREVIEW + loaded
-	assert await _leads(session) == 5
A6	assert await _run(session, source) == 2
A6	assert capsys.readouterr().out == (
A6	assert await _run(session, source, "--map", "email=1", "--map", "name=2") == 0
A6	assert "  1. колонка 1 → email\n  2. колонка 2 → name\n" in capsys.readouterr().out
A6	assert await session.scalar(select(SalesLeadModel.name)) == "Иван"
-	assert await _run(session, source, *extra, name=name) == code
-	assert line in capsys.readouterr().out
-	assert asked[0] == ("GET", EXPORT)
-	assert (found.source, [(lead.email, lead.name) for lead in found.leads]) == (
A5	assert await _refusal(httpx.Response(status, content=body)) == f"SheetError: {sheet.CLOSED}"
-	assert (
A9	assert str(refused.value) == words
-	assert (str(refused.value), asked) == (words, [])
-	assert sheet.export_url(link) == export
-	assert len((await intake.read_link(LINK, http)).records) == 1024
-	assert str(refused.value) == "таблица больше 1 МБ — выгрузите её в CSV частями"
-	assert intake.read_bytes(b"email\n", name).source == source
```

Привязаны к примерам: **A2 A5 A6 A9**. Остальные 13 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 13
