# Verify report

**Поставка:** срез `sales-send` (модуль «Продажи», 4.6b), часть «общее» — мост общей почты к модулю продаж:
отправка, пачка и добивки ведут письмо продаж ответами модуля, без модуля — прежний отказ 1.1b. Перенос на свежую
базу main. Модуль продаж — следующим PR (`pr-module/`), после 5.3 и Ф2.

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже; ревью общего кода почты — соседняя сессия
**asserts_reviewed_by:** deferred reason=55 новых утверждений, все без ссылки на пример спеки: ожидания моста записал исполнитель (поведение 1.1b — прежние слова); подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `2e53f5a` — ветка `sales/4.6b-common-main` от `c678dca` (main `59deaca` с #200–#204 + 1.1b «а»); не запушена

## Сборка и размер

| SHA | Суть | Файлов (без `delivery/`) | +/− |
|---|---|---|---|
| `4d531a9` | перенос `55d3e7c` (ветка `sales/4.6b-split`): мост с регистрацией; отправка, добивки, ключ добивки, отказы словами, 409, имя задачи; пачка 1.1b «а» (`check_connected`) — через мост; тесты моста с подставным модулем и 1.1b | 10 | +635/−120 |
| `5090693` | заметка соседней сессии: проход добивок молчит в журнале о сроках продаж, когда модуль зарегистрирован в мосту (`stages.sales_registered()`); проверки журнала в тестах | 3 | +20/−7 |
| `d232333` | докстрока `_check_review` — текстом main и строкой о лиде продаж (сжатие было нужно только у предела 500 строк) | 1 | +8/−3 |
| `433423f` | ревью соседней сессии — договор моста: от модуля почта получает только отказ словами (`MailRefusalError`), прочие ошибки модуля — «не подключены» с причиной и записью в журнал, «подключены ли» с ошибкой — «нет», ответ модуля — в точке сохранения; `SendError` — наследник `MailRefusalError`; 9 тестов | 3 | +165/−23 |
| `2e53f5a` | пожелание ревью: тест границы договора — модуль отвечает на «кому писать» отказом почты не по смыслу (`NoSenderError`, `MaybeSentError`): пачка и проход добивок предсказуемы | 1 | +78/−3 |

`breakers:` против `c678dca`: **files=10, net_loc=750 (+869/−119)** — в пределах 25/800, вейвер не нужен.
Общий код (вне `SALES_PATHS`) — 8 файлов, +327/−99; тесты в масках продаж — 2 файла, +542/−20. Миграций нет,
голова — `39e342cb2b21` (1.1b «а»). Длина: `sending.py` 463, `followups.py` 499, `core/stages.py` 258.

## Перенос: конфликты и как разрешены

`git cherry-pick 55d3e7c` на `c678dca` — конфликты в пяти файлах и снимке сложности; разрешены по смыслу, правила
#202 и #204 не ослаблены, мост — поверх.

- `letters/sending.py` — #202 перенёс выбор ящика в `mailbox.choose` (первое письмо — свободный ящик этапа;
  добивка и ответ — только ящик своей переписки, `OwnPathError`), #204 добавил `_Target.before`. Оставлено как в
  main; мост — поверх: `_Target.to: stages.Recipient` вместо `stage`, `mailbox.choose(stage=target.to.stage)`,
  From — `to.from_name(…)`, ветка продаж в `_check_review`.
- `letters/followups.py` — ветка `except NotQueuedError` (#204) не тронута; сборка текста добивки — внутри `try`;
  `SalesNotConnectedError` — в том же кортеже возврата срока, что и в 1.1b «а». После слияния файл — 511 строк;
  сжат до 499 без смены поведения (мост модулем `stages`, `Chain(extra=…)` и фильтр захвата в `__init__`, два
  комментария короче).
- `core/stages.py` — `check_connected` (1.1b «а», отказ продажам до задачи пачки) спрашивает тот же мост: модуль не
  подключён или говорит «нет» — отказ теми же словами; подключён — пачка ставится.
- `letters/repository.py` — оба импорта: `FIRST_STEP` (#202, очередь — только первые письма) и `SALES_ELSEWHERE`.
- `tests/test_sales_stage_mail.py` — импорт `batch` (1.1b «а») и `SALES_ELSEWHERE`; второй рубеж добивки
  подменяет `stages.sales_connected` (мост — модулем).
- `delivery/complexity-snapshot.json` — взят из `c678dca` и пересобран `--update`.
- Новое при переносе: 3 теста моста для пачки (`check_connected`).

## Shape oracles

На `433423f` (`2e53f5a` — только тест: хуки, gates, тесты части), если не сказано иное; логи — scratchpad сессии (`checks/`).

| Проверка | Итог | exit |
|---|---|---|
| хуки коммита (27) — на каждом из пяти коммитов | прошли все пять; на `433423f` — 22 Passed, 5 Skipped (нет файлов их типа), на `2e53f5a` (только тест) — 5 Passed, 22 Skipped | 0 |
| `mypy backend/` | 374 файла, ошибок нет | 0 |
| `scripts/gates.py` | 618 файлов, нарушений нет | 0 |
| `lint-imports` | 4 контракта kept, 0 broken (в т. ч. `mail-does-not-know-sales`: letters / outreach / replies / contacts не импортируют sales) | 0 |
| `scripts/complexity.py` | 390 файлов, расхождений нет (снимок обновлён в коммитах; против базы `sending.py` 458 → 464, `core/stages.py` 204 → 259) | 0 |
| `alembic heads` | одна голова `39e342cb2b21` (миграций в части нет) | 0 |
| `detect-secrets-hook --baseline .secrets.baseline` по файлам части | находок нет (10 файлов части) | 0 |
| `pre-commit run --files` по файлам части | 22 Passed, 5 Skipped (нет файлов их типа), правок нет | 0 |
| `check_baseline_ratchet.sh` (`BASE=c678dca`) | 15 снимков сверено, OK | 0 |
| diff-coverage (`SKIP_TESTS=1 STRICT=1 BASE=c678dca`, покрытие полного прогона на `2e53f5a` каталогом `--cov=backend`) | 8 изменённых файлов, все ≥ 70 %: `api/errors.py` 100, `letters/followups.py` 97,8, `letters/sending.py` 97,2, `letters/template.py` 95,3, `letters/building.py` 94,1, `letters/repository.py` 93,9, `core/stages.py` 93,3 (непокрыты только три `assert_never`), `ops/job_outcome.py` 90,9 (файл целиком) | 0 |
| `contour_waves --base c678dca` — клон с черновиком части в `delivery/active/` (четыре строки — дословно из STATUS main) | нарушений нет | 0 |
| `delivery_check --require-ci --diff-base c678dca` — клон с черновиком части | 0 ошибок; `breakers: files=10 net_loc=750 (+869/-119)`; 5 предупреждений: необратимое (к переподписи владельцем до первой отправки продаж), `asserts_reviewed_by` deferred, нет `@given`, нет `agent-permissions`, `model_surface` из STATUS main не разбирается в пути файлов — общее для проекта | 0 |
| `check_irreversible_signature.sh` (клон) | подпись сходится, строка не тронута | 0 |

## Behavior oracles

- [x] PASS — полный pytest с покрытием (`--cov=backend`) на `2e53f5a`: **4727 passed** за 9:53, exit 0 (на старте load 3,5 и две минуты без чужих прогонов с покрытием;
      минуты три по ходу шёл один чужой прогон с покрытием, начатый после старта). На `d232333` — **4714 passed** за 9:17, exit 0. На старте — load 4,4 и две минуты подряд без чужих прогонов с
      покрытием; в последние ~5 минут параллельно шёл чужой полный прогон, начатый уже после старта. На `4d531a9` — 4714 passed за 11:00
      (на старте load 9,6 и чужих прогонов с покрытием нет), exit 0.
- [x] PASS — часть, соседи и тесты #204 на `433423f` (35 файлов на `433423f`: часть — 58 тестов, письма, очередь, пачка, добивки,
      учётки, события, ответы, переписка, главная, очистка, схема) — 617 passed за 1:52, exit 0; на `2e53f5a` —
      часть, `test_followups.py`, `test_letters_own_path.py` — 95 passed, exit 0.
- [x] PASS — часть, `test_letters_own_path.py`, тесты #204 (`test_letters_requeued.py`, `test_letters_requeued_race.py`,
      `test_letters_accepted_without_number.py`, `test_letters_unknown_outcome.py`, `test_letters_unknown_race.py`),
      `test_followups.py`, `test_letters_send_queue.py` — 131 passed за 29 с на `d232333`, exit 0.
- [x] PASS — соседи общей почты (33 файла: письма, очередь, пачка, добивки, учётки, идентификаторы, события,
      ответы, переписка, главная, очистка, схема) — 605 passed за 2:39 на `4d531a9`, exit 0; на голове — в полном
      прогоне.
- vitest — фронт в части не тронут; на `55d3e7c` — 553 теста, exit 0; на новой базе не гонял.

### Красный прогон до кода

Дерево `c678dca` (`git archive`) + тесты части с головы поверх: `tests/test_sales_stage_bridge.py` — не собрался
(`cannot import name 'Recipient' from 'backend.features.core.stages'`), `tests/test_sales_stage_mail.py` — не
собрался (`cannot import name 'SALES_ELSEWHERE'`); exit 2 и 2. Пакет `backend` — из архива (путь в ошибке).
Дерево удалено, временной базы не создавалось.

Договор моста (`433423f`): 9 новых тестов поверх кода `d232333` — 9 failed, exit 1: проход падает на ошибке
модуля (`RuntimeError`, `ProgrammingError` — нет таблицы, `SuppressedError` из «подключены ли»), стоп-лист из
текста добивки даёт `UnboundLocalError` в `followups._deliver`; отправка и пачка получают голую `RuntimeError`.

### Обратные прогоны

Мутант — правка строки (`mutate_46b.py`: ровно одно вхождение, возврат исходных байтов с проверкой sha256 —
дерево чистое). Наборы: A — `test_sales_stage_bridge.py`, `test_sales_stage_mail.py`, `test_followups.py`,
`test_letters_own_path.py` (82 теста); B — первые два (49); C — первые три (73).

| Мутант | Правка | Голова, набор | Итог | Убит тестами |
|---|---|---|---|---|
| R1 продажи путём `contacts` доноров | мост `recipient`: `Recipient(stage, email)` вместо ответа модуля | `5090693`, A | 5 failed | без модуля — отказ (мост, 1.1b A2); с модулем — адрес и имя, «не подключены», добивка |
| R2 без модуля — «подключены» | `sales_connected`: `load is None or …` | `5090693`, A | 5 failed | без модуля сроки ждут; пачка без модуля (мост, 1.1b ×2); 1.1b A4 |
| R4 проверка модуля не спрошена | `_check_review` без `check_sales` | `5090693`, A | 2 failed | адрес и имя; отказ проверки модуля до ящика |
| R5 пачка не спрашивает модуль | `check_connected`: продажи подключены всегда | `4d531a9`, B | 4 failed | модуль говорит «нет» — отказ; без модуля (мост, 1.1b ×2) |
| R6 пачка отказывает и подключённым | `check_connected`: отказ всегда (как 1.1b) | `4d531a9`, B | 1 failed | модуль говорит «да» — пачка ставится |
| W1 журнал шумит при модуле | предупреждение и при зарегистрированном модуле | `5090693`, C | 1 failed | `test_sales_deadline_waits_while_the_module_says_not_connected` |
| W2 журнал молчит без модуля | предупреждения нет и без модуля | `5090693`, C | 2 failed | `test_without_the_module_sales_deadlines_wait_and_are_not_claimed`, 1.1b A4 |

7 из 7 убиты. `d232333` меняет только докстроку — код под мутантами тот же.

Договор моста — на `433423f`, набор A (теперь 91 тест):

| Мутант | Правка | Итог | Убит тестами |
|---|---|---|---|
| K1 «подключены ли» бросает | перехват в `sales_connected` снят | 4 failed | поломка «подключены ли» ×3, пачка |
| K2 ошибка модуля — как есть | перехват `Exception` в `_asked` снят | 9 failed | все новые тесты договора |
| K3 отказ отправки — «не подключены» | `passes` у отправки — только `SalesNotConnectedError` | 1 failed | `test_refusal_of_the_module_check_stops_the_letter_before_the_mailbox` |
| K4 текст добивки пропускает стоп-лист | `passes` у `sales_followup` — `MailRefusalError` | 1 failed | поломка текста добивки отказом |
| K5 без точки сохранения | `begin_nested` в `_asked` снят | 2 failed | поломка запросом к базе: «подключены ли» и текст добивки |
| K6 «подключены ли» пропускает отказ | `passes` у `sales_connected` — `MailRefusalError` | 1 failed | поломка «подключены ли» отказом |
| K7 `SendError` не отказ словами | `class SendError(RuntimeError)` | 1 failed | `test_refusal_of_the_module_check_stops_the_letter_before_the_mailbox` |

7 из 7 убиты, возврат байт в байт. Граница договора — на `2e53f5a`, тесты границы:

| Мутант | Правка | Итог | Убит тестами |
|---|---|---|---|
| K8 «могло уйти» от модуля — как есть | `_asked` пропускает любую `RuntimeError` (`2e53f5a`, тесты границы) | 2 failed | пачка: «связь с почтой оборвалась… ушло ли оно, неизвестно» вместо отказа словами; проход: `unknown=1`, срок не вернулся |

### Граница договора (`2e53f5a`, пожелание ревью)

Модуль ответил на «кому писать» отказом почты не по смыслу. `NoSenderError` — наследник `SendError`, идёт как
есть; `MaybeSentError` — не `SendError` (`transport.py`, `RuntimeError`): мост переводит его в «не подключены».
Пачка встаёт на письме словами отказа (`NoSenderError` — текст модуля, `MaybeSentError` — «Письмо №N: продажи к
почте ещё не подключены — …»), `sent 0`, `refused {}`, `left 1`, письмо `queued` без ящика; пачка встаёт целиком,
как при «ящиков нет». Проход добивок: строка добивки собрана и осталась `queued` без ящика, срок первого письма —
через час, в отчёте `postponed 1`, `unknown 0`, в журнале «отложена на час (причина)»; донорская того же прохода
ушла. Пропусти мост `MaybeSentError` как есть (мутант K8) — пачка назвала бы письмо «ушло ли, неизвестно», а
проход — «исход неизвестен» без отправки, срок не вернулся бы.

### Застрявшая добивка (находка 07.10) — на новой базе

Временный тест на базе с #202 (в репозиторий не коммитился, файл удалён): вариант 1 — проход откладывает добивку
(свой ящик на паузе), строка `queued`; очередь этапа — без неё (`LetterRepository.queued` берёт только первые
письма), пачка её не трогает (`sent 0`). Вариант 2 — проходы 1 и 2: свой ящик на паузе, чужой свободен —
`postponed=1`, чужой не взят; проход 3 (свой ожил) — `sent=1` с ящика первого письма, `In-Reply-To` первого
письма, строка добивки одна. Оба — зелёные; тест, шаги и вывод — `../stuck-followup-repro.py.txt`.

## Пересечения с соседней сессией

| Файл | Что меняет часть | Риск |
|---|---|---|
| `backend/features/letters/sending.py` | `_Target.to` рядом с `before` (#204), мост в `_target` до учётки этапа, ветка продаж в `_check_review`, `mailbox.choose(stage=…)` (#202), From; `SendError` — наследник `stages.MailRefusalError`; 463 строки | средний: общий файл отправки |
| `backend/features/letters/followups.py` | `Chain(extra=…)` — продажи, когда мост говорит «подключены»; текст добивки продаж из моста; ключ и нынешний текст в `materialize`; сборка текста внутри `try`; журнал прохода — только без модуля; 499 строк | средний: файл у предела — следующая правка потребует выноса |
| `backend/features/letters/building.py`, `template.py`, `repository.py` | `followup_key`; отказ продажам словами `SALES_ELSEWHERE` | низкий |
| `backend/features/core/stages.py` | протокол `SalesMail`, `register_sales`, `sales_registered`, ответы этапа; `check_connected` через мост; договор моста: `MailRefusalError`, `_asked` (перевод ошибки модуля, точка сохранения) | низкий: файл 1.1b |
| `backend/features/ops/job_outcome.py` | строка имени задачи сборки продаж рядом с `SEND_QUEUE_JOB` | низкий: Ф2 и шов агента дописывают свои строки туда же |
| `backend/features/letters/batch.py`, `mailbox.py`, `backend/api/letters/routes.py`, `frontend/src/letters/*` | не тронуты | 1.1b «а» в базе уже отказывает продажам в пачке словами; часть меняет только ответ `check_connected` |

## Ревью рисковых мест

- **Безопасность — кому уходит письмо продаж.** Адрес — ответ модуля продаж через мост (`Recipient`), а не строка
  `contacts` домена; без модуля — отказ до учётки этапа. Сломаться может мост: путь доноров для продаж дал бы
  письмо адресу сайта. Держат: R1 (5 тестов), контракт `mail-does-not-know-sales`.
- **Безопасность — с какого ящика.** Выбор ящика #202 перенос не трогает: письмо продаж идёт тем же
  `mailbox.choose` по этапу ответа моста; добивка продаж — только с ящика своей переписки (тест моста: второй ящик
  продаж свободен и не взят; `test_letters_own_path.py` зелёный).
- **Безопасность — регистрация.** Реестр — состояние процесса; подменить модуль может только код, загруженный в
  процесс (тот же уровень доверия, что любой импорт). Тесты подменяют его через `monkeypatch` и возвращают
  прежнюю регистрацию после себя.
- **Транзакция БД — добивки.** Захват гасит срок до вызова почты (прежнее правило); сборка текста продаж — внутри
  `try`: отказ модуля возвращает срок и строки добивки не оставляет; шаг, решённый другим путём (`NotQueuedError`,
  #204), — без возврата срока, как в main; ждавшая строка добивки продаж обновляет текст в той же транзакции до
  отправки. Держат: второй рубеж 1.1b, тест «нынешний текст», тесты #204.
- **Журнал прохода (заметка соседней сессии).** Без модуля — предупреждение на каждом проходе, как в 1.1b; модуль
  зарегистрирован — молчит, число — в `PassReport.waiting`. Цена: модуль есть, а продажи долго не подключены —
  сроки копятся без строки в журнале: воркер добивок пишет отчёт прохода («ждут этапа N») только когда что-то
  отправлено, отложено или остановлено. Почему продажи не подключены — на экране продаж (модульная часть).
  Держат: W1, W2.
- **Поломка модуля продаж (ревью соседней сессии).** Проход добивок, пачка и кнопка общие: ошибка модуля после
  захвата роняла проход, срок добивки продаж терялся, донорские не уходили. Теперь от модуля почта получает
  только отказ словами (`MailRefusalError`); прочее — «не подключены» с причиной и записью в журнал (с трассой),
  «подключены ли» с ошибкой — «нет». Отказ словами почты от отправки идёт как есть: стоп-лист модуля кончает
  цепочку, а не возвращает срок каждый час. Ответ модуля — в точке сохранения: упавший запрос к базе не ломает
  транзакцию почты. Цена: поломка модуля выглядит для человека как «не подключены — причина»; трасса — в
  журнале. Держат: K1–K7.
- **Интеграция — внешних вызовов часть не добавляет.** `httpx` в диффе нет; транспорт и учётки — прежние; модуль
  продаж — локальный вызов через протокол.
- **Производительность.** Мост — один вызов загрузчика на письмо и один «подключены» на проход; `sales_registered`
  — чтение поля; у ответа модуля — SAVEPOINT и RELEASE, других новых запросов к базе в общей почте нет.
- **Находка о застрявшей добивке** — на базе с #202 не воспроизводится (выше); правило то же и для доноров.

## grep (публичный репозиторий)

Добавленные строки `c678dca..2e53f5a` (869) и сообщения пяти коммитов: имена документов планирования, слова
клиента, имена людей и компаний, адреса и ссылки (только `*.example.test`), числа с разрядами, лимиты аккаунтов —
совпадений нет. Ссылка с именем владельца в докстроке `_check_review` — строка main: в `d232333` докстрока
возвращена текстом main, строка в дифф не попадает. `limit=5` в тестах — размер прохода добивок, не лимит учётки.

## Три статуса

- **Написано и под тестами:** мост с регистрацией; отправка, пачка и добивки ответами модуля; без модуля — отказ
  1.1b, срок цел и назван в журнале; при модуле журнал не шумит; поломка модуля почту не роняет; ключ добивки;
  отказы словами; граница договора — 62 теста части, соседи и #204, полный прогон, 15 мутантов.
- **Замерено живьём:** нет — без модуля продаж письма продаж не уходят по построению; живой стенд вкладки и API —
  в модульной части.
- **Заложено:** регистрация настоящего модуля и всё письмо продаж — модульная часть (после 5.3 и Ф2); при ней —
  тест 1.1b `TestFollowup::test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on` (ждёт предупреждения) и
  комментарий `Stage.SALES` в `core/domain.py`.

## Assertion digest (ревью ожиданий, не кода)

База: `c678dca` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **55**, из них без ссылки на пример спеки:
**55**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert isinstance(transport, Recording)
-	assert str(refused.value) == f"Письмо №{world.letter.id}: {SALES_NOT_CONNECTED}"
-	assert source.asked == []
-	assert world.letter.status is MessageStatus.QUEUED
-	assert (report.sent, report.waiting) == (0, 1)
-	assert world.letter.next_action_at == NOW - timedelta(days=1)
-	assert f"1 подошли, срок не погашен — {SALES_NOT_CONNECTED}" in caplog.text
-	assert found == Recipient(stage, "editor@site.example.test", None)
-	assert fake.asked == []
-	assert (outgoing.to, outgoing.from_name, outgoing.from_email) == (
-	assert source.asked == ["sales"]
-	assert fake.asked == [f"recipient {world.letter.id}", f"check {world.letter.id}"]
-	assert world.letter.status is MessageStatus.SENT
-	assert _seen(source) == []
-	assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert str(refused.value) == (
-	assert source.asked == []
-	assert box is not None
-	assert (report.sent, report.waiting, report.postponed) == (1, 0, 0)
-	assert outgoing.subject == first.subject
-	assert outgoing.in_reply_to == first.internet_message_id
-	assert (outgoing.from_email, outgoing.to, outgoing.body) == (
-	assert step is not None
-	assert (step.thread_id, step.idempotency_key) == (first.thread_id, f"sales:{LEAD}:1")
-	assert f"followup {first.thread_id}:1" in fake.asked
-	assert (report.sent, report.waiting) == (0, 1)
-	assert first.next_action_at == NOW - timedelta(minutes=1)
-	assert fake.asked == []
-	assert "срок не погашен" not in caplog.text
-	assert (first_try.postponed, second_try.sent) == (1, 1)
-	assert [outgoing.body for outgoing in _seen(source)] == ["Another made-up reminder."]
-	assert len(list(rows)) == 1
-	assert (report.sent, report.postponed, report.waiting) == counts
-	assert world.letter.next_action_at == later[broken]
-	assert donor.next_action_at is None  # донорская ушла, срок следующей — у её добивки
-	assert "ошибка модуля продаж" in caplog.text
-	assert str(refused.value) == (
-	assert isinstance(refused.value.__cause__, RuntimeError)
-	assert _seen(source) == []
-	assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert (report.sent, dict(report.refused), report.left) == (0, {}, 1)
-	assert report.stopped is not None
-	assert report.stopped.endswith(said)
-	assert _seen(source) == []
-	assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert (report.sent, report.postponed, report.unknown, report.waiting) == (1, 1, 0, 0)
-	assert world.letter.next_action_at == NOW + followups.POSTPONE
-	assert donor.next_action_at is None
-	assert step is not None
-	assert (step.status, step.sender_id) == (MessageStatus.QUEUED, None)
-	assert postponed.endswith(f"{said})")
-	assert followup_key(previous, step) == expected
-	assert followup_key(first, 1) == idempotency_key(
-	assert SALES_ELSEWHERE in response.json()["detail"]
-	assert materialized == 0
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 55

## Проверки на голове PR

Ветка перенесена на main `cdb32f9`; голова кода `94915e3`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 7ccbaf6d840a (head) | 0 |
| ратчет сложности | Ратчет сложности: 394 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 720 passed in 150.46s (0:02:30) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=10 net_loc=750 (+869/-119)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 55` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | см. лог | — |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `1b4c399`):

```
breakers: files=10 net_loc=750 (+869/-119), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `1b4c399`):

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
