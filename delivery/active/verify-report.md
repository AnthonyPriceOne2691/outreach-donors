# Verify report

**Поставка:** срез `sales-kommo` (модуль «Продажи», 5.2) — клиент Kommo `fixture`/`live`: поиск контакта,
сделка с контактом и компанией одним запросом, примечание, ссылка на сделку. Вызывающих нет — их добавит
передача лида (5.3).

**Date:** 2026-10-05
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; общие точки — на ревью соседней сессии outreach-donors
**asserts_reviewed_by:** deferred reason=77 утверждений без примера спеки ждут подписи человека — тексты отказов словами, паузы и потолок ожидания, частота, формы ответов Kommo по документации, проверка ключа; подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR; пре-пуш гоняет полный набор
**Commit:** T4 — прогоны агента сняты на вершине его ветки `24c24b4` (тот же код); после переноса на main `b1f0f60` повторены тесты среза (89 passed), гейты, ратчет, волны, `delivery_check`, подпись

## Сборка

Ветка `sales/5.2-kommo-pr` от main `b1f0f60`: документы — первым коммитом (`sales-leads-screen` в архив, активный
срез `sales-kommo`), затем три коммита ветки агента без правки кода: `17e41ba` T0. Продажи 5.2; `27dd9e1` Продажи 5.2 (T1); `4cbfa98` Продажи 5.2 (T2); `edb19d1` Продажи 5.2. Код разбит на три файла
(`kommo.py` — протокол, `fixture`, фабрика; `kommo_live.py` — сеть, повторы, разбор ответов; `kommo_types.py` —
учётка, сущности, отказы): один файл вышел бы за предел длины 500. Миграций нет; голова Alembic прежняя.

## Три статуса

| Что | Статус |
|---|---|
| Клиент `fixture`/`live`, фабрика, отказ на старте (A5), поиск контакта и привязка (A1), сделка одним запросом, примечание, ссылка, повторы и пауза Kommo (A2), 401 (A3), форма ответа (A4), запись без повтора после отправки, частота, ключ не в адресе, тексте и журнале | **написано и под тестами** — `tests/test_sales_kommo.py`, 89 тестов на `httpx.MockTransport` |
| Утечка ключа через текст `LocalProtocolError` и `repr` `UnicodeEncodeError` (обоснование проверки ключа на старте) | **замерено** — локальным сокетом, без внешней сети (ниже) |
| Формы запросов и ответов Kommo API v4: привязка контакта номером, ответ `/leads/complex`, 204 на пустой поиск, `retry_after` в теле 429, блокировка 403, формат ошибок | **сверено с документацией**, не живьём |
| Запись в настоящий Kommo, обязательные поля воронки, поведение под пределом частоты | **заложено** — живого доступа нет; первая живая запись — на тестовой воронке после переподписи необратимого (5.5) |

## Красные прогоны

| Что | На каком коде | Итог | Лог |
|---|---|---|---|
| Тесты T1 (65) | main `3695a7f` | `ImportError: cannot import name 'kommo'` — exit 2 | `logs/red-1-T1-on-main-3695a7f.log` |
| Тесты T2 (24) | T1 `c21fdb3` | 23 failed, 66 passed — exit 1. Не упал один: «пауза дольше потолка — отказ, не сон» — на T1 повторов нет вовсе, тест сторожит будущий цикл (убивает мутант M8) | `logs/red-2-T2-on-c21fdb3.log` |

## Обратные прогоны (мутанты)

Скрипт `logs/mutants.py`: одна правка на месте — прогон `tests/test_sales_kommo.py` — откат;
в конце дерево проверяется на чистоту. Четыре первых — обязательные по заданию.

| Мутант | Итог | Прогон |
|---|---|---|
| M1 повтор на 401 (A3) | убит | 4 failed, 85 passed in 0.84s |
| M2 нет повтора на 429 (A2) | убит | 4 failed, 85 passed in 0.75s |
| M3 «успех» без id сделки (A4) | убит | 9 failed, 80 passed in 0.79s |
| M4 ключ в тексте ошибки (текст httpx наружу) | убит | 7 failed, 82 passed in 0.79s |
| M5 цепочка к исключению httpx (from exc) | убит | 2 failed, 87 passed in 0.73s |
| M6 запись повторяется после отправки | убит | 5 failed, 84 passed in 0.92s |
| M7 пауза из тела 429 не читается | убит | 2 failed, 87 passed in 0.76s |
| M8 пауза дольше потолка — сон вместо отказа | убит | 1 failed, 88 passed in 0.74s |
| M9 совпадение почты подстрокой (A1) | убит | 2 failed, 87 passed in 0.71s |
| M10 незнакомая форма поиска — «никого» | убит | 5 failed, 84 passed in 0.72s |
| M11 live без ключа собирается (A5) | убит | 2 failed, 87 passed in 0.71s |
| M12 частота без шага | убит | 3 failed, 86 passed in 0.71s |
| M13 страховку набора сняли (_no_real_kommo — не фикстура) | убит | 1 failed, 88 passed in 0.69s |
| M14 fixture не ищет известный контакт (A1) | убит | 1 failed, 88 passed in 0.70s |
| M15 неушедшая запись не повторяется | убит | 3 failed, 86 passed in 0.73s |
| M16 401 — общий отказ, не KommoAuthError (A3) | убит | 3 failed, 86 passed in 0.74s |

Итог: 16 мутантов — 16 убито; дерево после прогонов чистое (`logs/mutants.log`).
Предел: пустую страховку `_no_real_kommo` (фикстура есть, но ничего не ставит) тест различит
только при живом ключе в `.env` — снятую различает (M13).

## Замер: ключ в тексте ошибок httpx

`logs/h11_leak_probe.py` — запрос на локальный сокет (`127.0.0.1`, без внешней сети) с ключом,
в котором перевод строки, пробел или кириллица. httpx 0.28.1, httpcore 1.0.9, h11 0.16.0:

```
LocalProtocolError | token in str: True | Illegal header value b'Bearer <ключ>\n'
LocalProtocolError | token in str: True | Illegal header value b'Bearer <ключ> '
UnicodeEncodeError | token in repr: True | 'ascii' codec can't encode characters in position 7-10: ordinal not in range(128)
```

Вывод для среза: ключ проверяется при сборке `live` (ASCII, печатный, без пробелов — иначе
`ConfigError` без значения ключа), а наружу из транспорта идёт только имя типа ошибки и
`from None`. Тот же замер — основание находки в общем коде (ниже).

## Сверка с документацией Kommo API v4

- `POST /api/v4/leads/complex`: `_embedded[contacts][0][id]` привязывает существующий контакт,
  `_embedded[tags]` — теги с `name`; успех — 200, список `{id, contact_id, company_id,
  request_id, merged}`; не больше 50 сделок за запрос (у нас одна).
- `GET /api/v4/contacts?query=`: полнотекстовый поиск; пустой результат — 204 без тела;
  `custom_fields_values` у контакта бывает `null`.
- Коды: 401, 402 (подписка), 403 (в том числе блокировка за частые 429), 429 с `retry_after` в
  теле (пример — 300), предел — 7 запросов в секунду с IP; ошибки — `title`, `type`, `status`,
  `detail`, у 400 — `validation-errors` (`path`, `detail`).
- Примечание: `POST /api/v4/leads/{id}/notes`, `note_type: common`, `params.text`; ответ —
  `_embedded.notes[].id`.

Источники: developers.kommo.com — reference/complex-leads, docs/http-codes; amocrm.ru/developers —
crm_platform/leads-api, contacts-api, events-and-notes; github.com/amocrm/amocrm-api-php
(исключение на 204 без содержимого).

## Проверки

Все — на голове `24c24b4` ветки агента (тот же код; после переноса на main повторены тесты среза, гейты, волны, `delivery_check`, подпись); логи — `logs/check-*.log`, `logs/pytest-full.log`,
`logs/pre-commit-all-files.log`, `logs/diff-coverage.log`.

| Проверка | exit | Итог |
|---|---|---|
| `pytest tests/test_sales_kommo.py` | 0 | 89 passed |
| полный `pytest -q --cov=backend --cov-report=json` | 0 | 3766 passed за 418 с — один прогон. Оговорка: в момент старта в соседнем дереве (агент 3.1) шёл крупный прогон тестов продаж и API; нагрузка 8.08 (< 10), оба прогона прошли |
| `check_diff_coverage.sh` (`STRICT=1 BASE=origin/main SKIP_TESTS=1`) | 0 | 100% — `backend/config/sales.py`, `kommo.py`, `kommo_live.py`, `kommo_types.py` |
| `pre-commit run --all-files` | 0 | 27 хуков Passed, дерево не изменилось |
| `scripts/gates.py` | 0 | 509 файлов, нарушений нет |
| `scripts/complexity.py` | 0 | 335 файлов, расхождений со снимком нет |
| `lint-imports` | 0 | 4 контракта kept, 0 broken |
| `mypy backend/` | 0 | 320 файлов без замечаний |
| `ruff check backend tests scripts` / `ruff format --check backend tests` | 0 / 0 | чисто / 498 файлов |
| `check_jscpd_gate.sh` | 0 | clone-пар 55 при снимке 55 |
| `check_complexity_gate.sh` | 0 | OK |
| `BASE=origin/main check_baseline_ratchet.sh` | 0 | 15 снимков сверено |
| `alembic heads` | 0 | одна голова `8dbc46c01caf` — миграций в срезе нет |
| `check_irreversible_signature.sh` | 0 | подпись сходится: строка не тронута |
| `contour_waves.py --base origin/main` | 0 | нарушений нет: общий код объявлен в `shared_changes:` (после переноса, STATUS среза в `delivery/active/`) |
| `delivery_check.py --require-ci --diff-base origin/main` | 0 | 0 errors; предупреждения разобраны ниже; `breakers: files=8 net_loc=1716` — вейвер владельца |
| vitest, `tsc`, ESLint, Prettier | — | фронт не тронут; ESLint и Prettier — в составе `pre-commit --all-files` |
| Красные прогоны | 2 / 1 | T1 на main — `ImportError`; T2 на T1 — 23 из 24 |
| Обратные прогоны | — | 16 мутантов, 16 убито |

## Ревью рисковых мест

- **деньги** — трат нет: Kommo в тарифе владельца не тарифицирует запросы; запись во внешнюю CRM — не деньги,
  а необратимое (ниже, «интеграция»). Расход в журнал не пишется.
- **безопасность** — ключ `SALES_KOMMO_TOKEN` уходит заголовком `Authorization`, не в адрес; свои исключения
  `KommoAuthError`, `KommoUnavailableError`, `KommoFormatError`, `KommoUnconfirmedError` собираются без адреса
  и текста httpx (`from None`) — урок #160; проверка ключа на старте (ASCII, печатный, без пробелов) не даёт
  httpx положить ключ в `LocalProtocolError` (замер выше); тесты «ключа нет в тексте ошибки и журнале» и мутант
  «ключ в тексте ошибки» (убит).
- **интеграция** — запись во внешнюю CRM, где с ней работают люди: повторы только на таймаут, обрыв, 429, 5xx
  и только для чтения; запись после отправки (`POST /api/v4/leads/complex`) не повторяется —
  `KommoUnconfirmedError` (у Kommo нет защиты от дублей; исход разбирает 5.3); пауза 429 — из заголовка
  `Retry-After` или поля `retry_after` тела; просьба ждать дольше потолка — отказ с величиной, а не сон;
  неожиданная форма ответа (нет `id` сделки) — громкий `KommoFormatError`, а не «успех без ссылки».
- **производительность** — частота не выше `KOMMO_RATE_PER_SEC` (публичный предел Kommo, 7 запросов в секунду
  с IP) ровным шагом `Pace`, на каждой попытке; лидов единицы в день — счёт на клиента, а не на процесс,
  достаточен (открытый вопрос — при росте); таймаут запроса — `KOMMO_TIMEOUT_SEC`.
- **новый модуль** — `backend/features/sales/kommo.py`, `kommo_live.py`, `kommo_types.py`: сложность в ратчете,
  покрытие 100 %; импортируют только общие `backend/shared/net/retry.py` (`delay_for`, `RETRY_STATUSES`,
  `MAX_DELAY_SEC`) и `startup_checks.ConfigError`; контракт `mail-does-not-know-sales` цел.

## Предохранитель

8 файлов, net 1 716 (+1 719 / −3) при пределах 25 и 800 строк — **постоянный вейвер владельца для модульных
срезов продаж (05.10)** строкой `waivers: max_loc_diff=1716 … by=human:anthony` в STATUS. Условия владельца
соблюдены: число конкретное; общая часть около 57 строк (`.env.example` 13, `tests/conftest.py` 10,
`.secrets.baseline` 2/2, `backend/config/sales.py` 32/1) при пределе ~300; список общих файлов соседней
сессии отправлен до PR. Из строк среза 897 — тесты.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — строка подписана владельцем и перенесена дословно.
  Новая поверхность (запись во внешнюю CRM) достижима только с 5.3 — там её и подписывает владелец (план Ф5);
  в этой части клиент никем не зовётся, `live` — только явной настройкой.
- «asserts_reviewed_by deferred» — 77 утверждений из 107 ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis не в зависимостях проекта.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.

## Проверка волн

В0, В1, В-обн deployed; В2, В3а–в, В4 pending — их триггеров в части нет. Общий код объявлен — проверка зелёная.

## Spec coverage gaps

- Передача лида, Telegram, таблица передач — 5.3; живая запись на тестовой воронке — 5.5.
- Отклонение от буквы Spec (координатор): запись после отправки не повторяется — см. «интеграция».
- Строже Spec: `live` требует пять значений (поддомен, ключ, воронка, этап, ответственный), а не два.

## Находки

- **Общий код, `backend/shared/llm.py:117-122` (`_unreached`)** — текст отказа модели при
  `LocalProtocolError` собирается как `f"запрос не собран: {exc}"`, а h11 кладёт в этот текст
  значение заголовка целиком, то есть ключ модели (замер выше). `_unusable_key`
  (`backend/shared/llm.py:94`) ловит пустой и не-ASCII ключ, но не пробел и перевод строки;
  `backend/config/llm.py:55` ключ не обрезает. Ключ с пробелом или переводом строки (значение в
  кавычках в `.env`, переменная окружения контейнера) уходит в журнал (`logger.exception`) и в
  причину отказа прогона. Как воспроизвести: значение ключа модели с пробелом в конце (в кавычках в `.env`) и
  любой вызов модели; или `logs/h11_leak_probe.py`. Не исправлено — передано координатору
  (правило «общий код чинится один раз»).
- **Общий код, `backend/shared/net/retry.py` (`delay_for`, `with_retries`)** — не ошибка, а
  граница: паузу читает только из заголовка `Retry-After` в секундах; при просьбе дольше
  `MAX_DELAY_SEC` спит потолок и повторяет внутри окна запрета; повторяет любой обрыв, в том
  числе после отправки неидемпотентного запроса. Для Kommo это не годится (пауза в теле 429,
  блокировка IP за частые 429, запись без ключа идемпотентности) — в срезе свой цикл поверх
  `delay_for`, `RETRY_STATUSES`, `MAX_DELAY_SEC`. Решать ли это в общем помощнике — соседям.

Обе переданы соседней сессии 05.10 (утечка ключа модели — к починке у владельца модуля).

## Открытые вопросы

- Компания заводится с каждой сделкой, даже когда контакт найден: в CRM возможны дубли компаний. Искать
  компанию или не заводить её при найденном контакте — решение владельца (к 5.3).
- Обязательные поля воронки станут известны с доступом: сейчас отказ 400 называет поле словами Kommo.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, по зелёному CI и ОК соседней сессии на общие точки.

## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **107**, из них без ссылки на пример спеки:
**77**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A1	assert created == CreatedLead(
A1	assert (find.method, str(find.url)) == ("GET", f"{API}/contacts?query=ivan%40acme.example.test")
A1	assert (create.method, str(create.url)) == ("POST", f"{API}/leads/complex")
A1	assert script.body(1)[0]["_embedded"]["contacts"] == [{"id": 77031}]
A1	assert (created.contact_id, created.contact_found) == (77043, False)
A1	assert script.body(1)[0]["_embedded"]["contacts"] == [
A1	assert created.contact_found is False
A1	assert script.body(1)[0]["_embedded"]["contacts"][0]["custom_fields_values"] == EMAIL_FIELD
-	assert found == KommoContact(6029, "Ivan Petrov")
-	assert "несколько контактов с одной почтой" in caplog.text
A1	assert (first.contact_id, first.contact_found) == (known.id, True)
A1	assert (second.contact_id, second.contact_found) == (known.id, True)
A1	assert list(fixture.contacts.values()) == [known]
A1	assert second.id == first.id + 1
-	assert script.body(1) == [
-	assert sent["name"] == "Email: acme.example.test"
-	assert sent["_embedded"]["companies"][0]["name"] == "acme.example.test"
-	assert sent["_embedded"]["contacts"] == [{"custom_fields_values": EMAIL_FIELD}]
-	assert note == 8263
-	assert (request.method, str(request.url)) == ("POST", f"{API}/leads/9341/notes")
-	assert script.body(0) == [
-	assert script.requests == []
A2	assert found == KommoContact(77031, "Ivan Petrov")
A2	assert len(script.requests) == 2
A2	assert pauses == [2.0]
A2	assert created.id == 9341
A2	assert [request.method for request in script.requests] == ["GET", "POST", "POST"]
A2	assert pauses == [2.0]
-	assert await _live(script, lambda client: client.find_contact(EMAIL)) is None
-	assert pauses == [3.0]
-	assert str(refused.value) == (
-	assert refused.value.retry_after == 317
-	assert (len(script.requests), pauses) == (1, [])
-	assert not is_permanent(refused.value)
-	assert await _live(script, lambda client: client.find_contact(EMAIL)) is None
-	assert (len(script.requests), len(pauses)) == (2, 1)
-	assert str(refused.value) == words
-	assert refused.value.retry_after == asked
-	assert (len(script.requests), len(pauses)) == (3, 2)
-	assert str(refused.value) == (
-	assert (refused.value.__cause__, refused.value.__suppress_context__) == (None, True)
-	assert (len(script.requests), len(pauses)) == (3, 2)
-	assert "kommo: повтор запроса" in caplog.text
-	assert API not in text
-	assert TOKEN not in text
-	assert str(refused.value) == (
-	assert [request.method for request in script.requests] == ["GET", "POST"]
-	assert (is_permanent(refused.value), pauses) == (True, [])
-	assert created.id == 9341
-	assert [request.method for request in script.requests] == ["GET", "POST", "POST"]
-	assert len(pauses) == 1
-	assert (len(script.requests), pauses) == (1, [])
-	assert await _live(script, lambda client: client.find_contact(EMAIL)) is None
-	assert min(second - first, third - second) >= 1 / 7 - 1e-9
-	assert len(pauses) == 2
A3	assert str(refused.value) == words
A3	assert len(script.requests) == 1
A3	assert is_permanent(refused.value)
A3	assert [request.method for request in script.requests] == ["GET", "POST"]
-	assert type(refused.value) is KommoRefusedError
-	assert str(refused.value) == words
-	assert len(script.requests) == 1
-	assert is_permanent(refused.value)
-	assert str(refused.value) == (
-	assert len(script.requests) == 1
A4	assert words.startswith(f"Kommo ответил HTTP {reply.status_code} без номера сделки")
A4	assert "сделка могла создаться" in words
A4	assert is_permanent(refused.value)
A4	assert len(script.requests) == 2  # ни одного повтора: вслепую завели бы вторую
-	assert "формат поменялся" in str(refused.value)
-	assert is_permanent(refused.value)
-	assert str(refused.value).startswith(f"Kommo ответил HTTP {reply.status_code} без номера")
-	assert await _live(script, lambda client: client.find_contact(EMAIL)) is None
-	assert request.headers["Authorization"] == f"Bearer {TOKEN}"
-	assert TOKEN not in str(request.url)
-	assert TOKEN not in repr(ACCOUNT)
-	assert TOKEN not in str(refused.value)
-	assert TOKEN not in caplog.text
-	assert refused.value.__suppress_context__ is True
-	assert refused.value.__cause__ is None
-	assert max(sum(t <= s < t + second for s in starts) for t in starts) == 7
-	assert starts[7] == pytest.approx(1.0)
-	assert create - find >= 1 / 7 - 1e-9
-	assert one == other  # те же вызовы — те же номера
-	assert one.url == f"https://fixture.kommo.com/leads/detail/{one.id}" == first.lead_url(one.id)
-	assert (first.leads[one.id].draft, first.leads[one.id].notes) == (
-	assert str(refused.value) == "сделки №4243 нет в fixture — примечание некуда положить"
-	assert type(built) is kind
-	assert isinstance(built, KommoClient)
-	assert ("сделки выдуманные" in caplog.text) is (kind is KommoFixture)
-	assert TOKEN not in caplog.text
-	assert script.requests == []
-	assert found is not None
-	assert (found.id, found.name) == (created.contact_id, "Ivan Petrov")
-	assert script.requests == []
A5	assert str(refused.value) == words
A5	assert script.requests == []
-	assert str(refused.value) == words
-	assert TOKEN not in str(refused.value)
-	assert str(refused.value) == (
L4	assert [getattr(empty, field) for _, field, _, _ in SETTINGS] == [d for *_, d, _ in SETTINGS]
L4	assert [getattr(filled, field) for _, field, _, _ in SETTINGS] == [v for *_, v in SETTINGS]
-	assert "_no_real_kommo" in request.fixturenames
-	assert (sales_cfg.KOMMO_PROVIDER, sales_cfg.KOMMO_TOKEN) == ("fixture", "")
-	assert not is_permanent(KommoUnavailableError("Kommo не ответил (проверка)"))
-	assert is_permanent(KommoFormatError("форма (проверка)"))
-	assert is_permanent(KommoAuthError("ключ (проверка)"))
```

Привязаны к примерам: **A1 A2 A3 A4 A5 L4**. Остальные 77 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 77
