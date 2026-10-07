# Verify report

**Поставка:** срез `mail-windows-limits` (Ф4, общий код почты), части 4.3, 4.5a и 4.5b одним PR — окно получателя
продаж по его поясу и политика почты по этапу по договору моста; домены рассылки и лимиты домена и направления
фильтром до выбора ящика, экран «Домены рассылки» по этапу; мягкие сигналы ящика продаж, пауза по окну последних
писем, сторож почты и тревоги в Telegram по смене состояния.

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов частей без ссылки на пример спеки в теле (пример назван именем теста, id W/L/H — в спеке); подписывает ревьюер общего кода или владелец при ревью, дайджест в конце отчёта
**CI run:** нет — снимается на PR
**Commit:** `e40d1eb` — голова PR (голова части 4.5b), ветка `sales/4.3-4.5-0710` (не запушена); база PR — голова
стопки: main (#214, #215) + мост с правкой #216 + шов агента Б–Д + 5.1. Головы частей: 4.3 `c3b9ef2`; 4.5a `d6c5d25`
(четыре коммита); 4.5b `e40d1eb`. Перенос — `git rebase --onto` с прежней базы (мост #211): конфликты по смыслу — в
докстроке моста (#216 и абзац политики — оба), снимки сложности — `--update`; `.secrets.baseline` — сторона базы и
одна запись ревизии.

## Shape oracles (на головах частей)

| Проверка | 4.3 `c3b9ef2` | 4.5a `d6c5d25` | 4.5b `e40d1eb` |
|---|---|---|---|
| `mypy backend` (strict) | 0 (386 файлов) | 0 (388) | 0 (392) |
| `ruff check` / `ruff format --check` | 0 / 0 | 0 / 0 | 0 / 0 |
| `scripts/gates.py --commits <предыдущая голова>` (public-repo — и по сообщениям коммитов) | 0 | 0 | 0 |
| `lint-imports` (4 контракта) | 0 | 0 | 0 |
| `scripts/complexity.py` (ратчет) | 0 (402 файла) | 0 (404) | 0 (408) |
| шаг образа (`docker_step_local.py`) | 0 | 0 | 0 |
| `alembic heads` (одна) | `75242c2ed7ba` (миграции нет) | `e054d221b2df` | `3f9663d69a56` |
| хуки pre-commit на коммитах переноса | Passed | Passed | Passed |
| тесты части и соседей (окна, мост, почта 1.1b, письма и свой ящик, тесты #204, добивки, пачка, события, ящики, сторож, переписка, обзор, схема, чистка, миграции, агент) | 43 файла, 814 passed | 44 файла, 828 passed | 46 файлов, 843 passed |
| `delivery_check --diff-base` против предыдущей головы (строка `breakers:`) | 11 / 792 (+805/−13) | 20 / 794 (+826/−32) | 20 / 793 (+817/−24) |

На голове PR `e40d1eb`:

| Проверка | Итог | exit |
|---|---|---|
| `scripts/gates.py --commits` против базы PR (все шесть сообщений) | 656 файлов, нарушений нет | 0 |
| полный pytest `--cov=backend` — один, нарочно, список тестов не пуст по замыслу (чужих полных прогонов нет, нагрузка на старте 5,0) | **4986 passed** за 9:52 | 0 |
| diff-coverage (`STRICT=1`) против базы PR из полного прогона | новые модули и ревизии — 100%, `stages.py` 92,7%, `sending.py` 97,5%, `followups.py` 97,8%, `events.py` 96,8%, `mailbox.py` 100%, `cli/main.py` 96,8%, `reaper.py` 98,2%, `silence.py` 77,4%, `api/watchdog/routes.py` 77,8% | 0 |
| `pre-commit run --all-files` (нагрузка 2,4, чужих полных прогонов нет) | 27 хуков Passed, дерево чистое | 0 |
| vitest `src/senders src/letters --maxWorkers=2 --reporter=dot` | 7 файлов, 76 тестов | 0 |
| фронт: `tsc --noEmit`, `eslint src/`, `prettier --check` | чисто | 0 / 0 / 0 |
| `contour_waves --base` против базы PR (клон с черновиками) | нарушений нет | 0 |
| `delivery_check --require-ci --diff-base` против базы PR (клон с черновиками; четыре строки — из STATUS стопки) | `breakers: files=43 net_loc=2379 (+2440/-61)`; 2 ошибки — только предохранитель (43 > 25, 2379 > 800) до строки вейвера владельца; 4 предупреждения — прежние (детектор отправки, подпись дайджеста, реляционных оракулов нет, `agent-permissions`) | 1 (ждёт вейвер) |
| пробный перенос до этого — на main с мостом #211 и швом А2 | без конфликтов | — |

## Behavior oracles

- [x] PASS — тесты частей и соседей на каждой голове (таблица выше), полный pytest на голове PR.
- [x] **4.3** — красный прогон: на коде моста (до части) тесты `test_sales_stage_bridge.py` и `test_sales_send_window.py`
  не собираются (нет `MailPolicy`, `CURRENT`). Мутанты стыка с мостом, на голове PR после переноса на #216:
  S1 (политика мимо `_asked`), S2 (пропускает любой отказ почты — стоп-лист кончает цепочку), S3 (ответ спрашивает
  политику), S4 (без модуля — вопрос мосту), M1′ (отправка не спрашивает окно), M6′ (ответ ждёт окна), M9′ (продажи без
  своей политики) — **7 из 7 убиты**; прежние на неизменённых строках (пояса, перевод часов, день по UTC, сдвиг,
  `postpone`, сроки из рассылки, закрытие окна) — 10 из 10 (прогон до переноса).
- [x] **4.5a** — красный прогон: на голове 4.3 `test_sending_limits.py` не собирается (нет `outreach/limits.py`); vitest
  экрана на коде 4.3 — «разделы этапов…» красный, «у одних доноров экран прежний» — зелёный (регресс); пачка продаж —
  красный `tsc` (`"sales"` не в `LetterStage`). Мутанты (`pick` мимо фильтра, лимит домена по одному ящику, направление
  строго больше, выдержка наоборот, счёт всех писем, чужое направление, отказ без причины, пауза домена, экран без
  счёта, консоль без паузы) — 10 из 10; код части переносы не меняли.
- [x] **4.5b** — красный прогон: на бэкенде 4.5a `test_sales_soft_signals.py` и `test_mail_watch.py` не собираются (нет
  `outreach/health.py`, `ops/mail_watch.py`). Мутанты стыка и подрезанных мест, на голове PR: M7′ (доноры под мягкими
  сигналами), M14′ (сторож не в общем списке), N1 (вебхук падает без политики), N2 (без политики — окно по умолчанию),
  N3 (сторож падает), N4 (сторож молчит), N5 (сутки — двое), N6 (окно считает отказом любое письмо), N7 (время
  прихода вместо времени платформы) — **9 из 9 убиты**; прежние на неизменённых строках — 12 из 12.
- [x] **Стык с #216** — мост сбрасывает несохранённое вызывающего до вопроса модулю: политика — пятым вопросом в тесте
  `test_own_unsaved_change_failure_surfaces_as_is_and_the_module_is_not_asked[policy]` (исходный `IntegrityError`, модуль не
  загружен); тесты договора с поломкой `policy` — зелёные.

## Живой прогон

Не выполнялся: модуль продаж в main к мосту не подключён — письмо продаж уходит только с подставным модулем;
живая тревога в Telegram не отправлялась (ключей бота в дереве нет; канарейка канала — у выкатки d6); экран «Домены
рассылки» не снимался; стенд и воркеры очереди не поднимались (общий Redis). Статус — «написано и под тестами»,
живьём — «заложено».

## Product oracles

- [x] PASS — `eval-smoke.md`: W1–W11, L1–L9, H1–H14; живое письмо продаж, живой экран, живая тревога, A5, A6 — «заложено».

## Ревью рисковых мест

- **транзакция БД** — отказ окна, лимиты и «не подключены» стоят до `_claim` (`UPDATE … status=SENDING`): письмо
  остаётся `queued`, ящик не выбран; добивка — срок возвращается в общем `except` прохода (`window.postpone`: до окна
  или час), строка добивки ждёт в базе и берётся той же. Вопрос модулю о политике — через `_asked`: сначала мост
  сбрасывает своё вызывающего (#216; сбой — ошибка почты, всплывает как есть), затем ответ модуля в его точке
  сохранения — упавший запрос модуля откатывает только её. `health.listen` пишет журнал и паузу ящика в сессии
  событий, фиксирует вызывающий (как прежняя парковка); фильтр и сторож только читают; консоль доменов — одна фиксация.
- **интеграция** — внешних вызовов почты PR не добавляет: окно, лимиты и «не подключены» — до транспорта (тесты
  проверяют пустой записывающий транспорт). Telegram — общий `send_alert` (таймаут, не роняет вызывающего), только на
  смене состояния; без бота — строка ERROR; тревога «политика не получена» — тем же путём.
- **миграция** — две новые таблицы, внешний ключ журнала на `senders` с каскадом (реестры чистки зелёные); значение
  `sales` в ревизиях не используется; обе ревизии гоняются откатом и подъёмом в процессе теста; голова одна —
  `3f9663d69a56`, `e054d221b2df` стоит после головы стопки `75242c2ed7ba` — при другом порядке слияний перецепить.
- **время** — сутки и переходы часов — по поясу получателя (`zoneinfo`), а не по UTC; сутки мягкого сигнала — от
  времени платформы (`event.at`), а не прихода события; тесты на неделях перевода часов.
- **производительность** — у письма продаж плюс один ответ модуля (сброс, SAVEPOINT, RELEASE), как у адреса и
  проверки; у доноров и рекламодателей вопроса нет (`CURRENT` без базы). Фильтр читает таблицу доменов целиком на
  первое письмо (строк — десятки); `listen` — 2–3 запроса на событие письма продаж (окно 50 писем по индексу);
  сторож — раз в 10 минут.
- **безопасность** — `GET /api/senders` под тем же правом `senders`; консоль — только с доступом к базе; в текстах
  тревог — адреса ящиков рассылки (наши), не адресатов, и причина моста (текст исключения модуля продаж — нашего
  кода); токен бота в журнал не попадает.
- **деньги** — риска нет, потому что PR не считает и не тратит денег: лимиты — число писем, не цена.
- **сведение с main и стопкой** — мост (#211, #216): `_asked` не тронут, политика — его вопросом; карточка переписки
  (#206, `thread_mail`) и путь ящика переписки не тронуты; файлы у предела: `followups.py` 499, `cli/main.py` 496,
  `sending.py` 485 строк; тест моста #211 держался за текст журнала «отложена на час» — фильтр «— отложена (».
- **новые модули** — `backend/features/core/window.py`, `backend/features/sales/policy.py`,
  `backend/features/outreach/limits.py`, `backend/cli/sending_domains.py`, `frontend/src/api/senders.ts`,
  `backend/features/outreach/health.py`, `backend/features/ops/mail_watch.py`, `backend/features/ops/alarm_feed.py`,
  `backend/features/ops/alarms.py`, ревизии `e054d221b2df`, `3f9663d69a56`.

## Предохранитель

Файлов 43 > 25, net 2379 > 800 — три части одним PR по решению владельца 07.10; вейвер на размер — за координатором.
Каждая часть в отдельности — в пределе (4.3 — 11 / 792, 4.5a — 20 / 794, 4.5b — 20 / 793).

## Spec coverage gaps

- Пояс гипотезы — поля у гипотезы нет; подключение политики в модуле продаж — при переносе 4.6b-модуля.
- Живой экран не снят; засева доменов нет — по решению владельца.
- A5 (канарейка выкатки), A6 (восстановление копии), строка реестра В4 — у выкатки d6; после перезапуска сторожа
  действующие тревоги приходят ещё раз.

## Находки (общий код — не чинил)

1. Карточка ящика (`GET /api/senders`) считает все письма, дневной кап — только первые: «отправлено 7 из 20» смешивает
   добивки с капом (было до среза).
2. `backend/cli/senders_admin.py` — у `sender-add` и `senders` нет тестов (покрытие файла 61%): команда доменов — своим модулем.
3. `scripts/healthwatch.sh` и лента сторожа — два источника тревог в один чат; дедуп у каждого свой.
4. Вебхук событий не читает `sg_event_id`: повтор пачки событий платформой — второй раз те же строки журнала здоровья.
5. `_asked` пишет WARNING с трассой на каждый сломанный ответ модуля — при сломанной политике на каждое письмо продаж
   каждого прохода (не больше `limit`). Находка про сброс своего в точке сохранения закрыта #216.

## Verdict

Готово к ревью общего кода соседней сессией; вейвер на размер, порядок слияний и голова миграций — координатор; слив —
по решению владельца.

## Assertion digest (ревью ожиданий, не кода)

База: голова стопки (main + мост #216 + шов Б–Д + 5.1) · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **134**, из них без ссылки на пример спеки:
**134**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(await screen.findByText(refusal)).toBeInTheDocument();
-	expect(recorded.calls.find((call) => call.path === '/api/letters/send-queue')?.body).toEqual({
-	expect(screen.getByRole('heading', { name: 'Доноры' })).toBeInTheDocument();
-	expect(screen.getByRole('heading', { name: 'Продажи' })).toBeInTheDocument();
-	expect(screen.getByText('продажи')).toBeInTheDocument();
-	expect(
-	expect(screen.getByText('Лимит направления: 4 из 50 первых писем сегодня')).toBeInTheDocument();
-	expect(screen.getByText('Отправлять нечем')).toBeInTheDocument();
-	expect(screen.queryByRole('heading', { name: 'Доноры' })).not.toBeInTheDocument();
-	expect(screen.queryByText(/Лимит направления/)).not.toBeInTheDocument();
-	expect(screen.queryByText(/лимит домена/)).not.toBeInTheDocument();
-	assert [alarm.code for alarm in found] == [f"quiet-box:{box.email}"]
-	assert early == []
-	assert found[0] in await silence.alarms(session, now=NOW)
-	assert await mail_watch.alarms(session, NOW) == []
-	assert codes == ["all-paused:sales", "nobody-to-send:sales"]
-	assert paused_domain == ["nobody-to-send:sales"]
-	assert await mail_watch.alarms(session, NOW) == []
-	assert (alarm.code, alarm.title) == ("no-policy:sales", "Политика почты «sales» не получена")
-	assert alarm.detail.endswith("не подключены — выдуманная поломка модуля: policy")
-	assert said == ["тревога: Ящик x молчит. ждут его", "прошло: Ящик x молчит"]
-	assert caplog.text.count("ТРЕВОГА НЕ ОТПРАВЛЕНА") == 1
-	assert (first, feed.told) == ({}, {QUIET.code: QUIET.title})
-	assert (len(seen), told) == (1, [["тревога"]])
-	assert (report.sent, report.postponed) == (0, 1)
-	assert _seen(source) == []
-	assert first.next_action_at == MONDAY_NINE + shift
-	assert (local.weekday(), time(9) <= local.time() < time(9, 30)) == (0, True)
-	assert later.sent == 1
-	assert (outgoing.to, outgoing.from_email) == (LEAD_EMAIL, SALES_BOX)
-	assert "вне окна получателя (пн–пт 09:00–17:00 по его часам)" in str(refused.value)
-	assert "пн 12.10 09:" in str(refused.value)
-	assert batch.why(refused.value) == "вне окна получателя"
-	assert source.asked == ["sales"]
-	assert _seen(source) == []
-	assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)
-	assert world.letter.status is MessageStatus.SENT
-	assert batch.why(refused.value) == "пояс получателя неизвестен"
-	assert world.letter.status is MessageStatus.QUEUED
-	assert [outgoing.body for outgoing in _seen(source)] == ["Thanks, here is more."]
-	assert (report.sent, report.postponed) == (1, 0)
-	assert donor.next_action_at is None
-	assert policy_of == [CURRENT, CURRENT, MailPolicy(window=WEEKDAYS_9_17)]
-	assert CURRENT.window is None
-	assert await stages.mail_policy(session, Stage.SALES, "Политика") is CURRENT
-	assert campaign is not None
-	assert world.letter.next_action_at == MONDAY + timedelta(days=3)
-	assert (await followups.send_due(session, transport=_transports(), limit=5, now=thursday)).sent
-	assert second is not None
-	assert (second.sent_at, second.next_action_at) == (thursday, thursday + timedelta(days=5))
-	assert (await followups.send_due(session, transport=_transports(), limit=5, now=tuesday)).sent
-	assert third is not None
-	assert (third.sent_at, third.next_action_at) == (tuesday, None)
-	assert found is not None
-	assert (found.words, found.spread) == ("пн–пт 09:00–17:00", timedelta(minutes=30))
-	assert (found.key if found else None) == expected
-	assert (sales_cfg._days(days), *sales_cfg._hours(hours)) == expected
-	assert two == {}
-	assert await _journal(session, box) == ["deferred", "deferred", "blocked", "limit_cut"]
-	assert cut == {box.id: 10}
-	assert await health.cuts(session, Stage.SALES, NOW + timedelta(hours=23, minutes=1)) == {}
-	assert screened.why() == f"{limits.BOX_CUT}: {box.email} — 10 из 10"
-	assert box.enabled
-	assert await _journal(session, box) == []
-	assert await health.cuts(session, Stage.DONORS, NOW) == {}
-	assert (said, box.enabled) == (([box.email], False) if paused else ([], True))
-	assert await _journal(session, box) == (["paused"] if paused else [])
-	assert box.pause_reason == "отказов 3 в окне 50 писем — порог 5.0%: пауза"
-	assert await _events(session, box, "bounce") == []
-	assert box.enabled
-	assert said == [box.email]
-	assert box.pause_reason == "жалоб 1 в окне 50 писем — порог 0.1%: пауза"
-	assert await _journal(session, box) == ["complaint", "paused"]
-	assert await (await session.connection()).run_sync(_journal_cycle) == (False, True)
-	assert world.letter.next_action_at == later
-	assert shifts[11] == monday_nine + timedelta(minutes=30) * random.Random(11).random()
-	assert len(set(shifts)) > 1
-	assert all(
-	assert until is not None
-	assert window.is_open(short, BERLIN, until)
-	assert until == (_at("Europe/Berlin", *expected) if expected else None)
-	assert window.is_open(WEEKDAYS_9_17, ZoneInfo(zone), moment) is open_now
-	assert until == monday_utc
-	assert until is not None
-	assert (until.hour, until.minute, until.tzinfo) == (*opens, UTC)
-	assert window.zone_of(["Asia/Tokyo", "Europe/Berlin", "America/New_York"]) == tokyo
-	assert window.zone_of([None, "Europe/Berlin", "America/New_York"]) == BERLIN
-	assert window.zone_of([None, None, "America/New_York"]) == new_york
-	assert window.zone_of([None, "", None]) is None
-	assert window.zone_of(["Mars/Olympus_Mons", "Europe/Berlin"]) == BERLIN
-	assert "Mars/Olympus_Mons" in caplog.text
-	assert late is not None
-	assert late.delay is not None
-	assert "пн–пт 09:00–17:00" in late.words
-	assert window.local_words(saturday + late.delay, BERLIN) in late.words
-	assert window.local_words(saturday + late.delay, BERLIN).startswith("пн 12.10 09:")
-	assert window.check(WEEKDAYS_9_17, [None, None], saturday) == window.Late(window.NO_ZONE, None)
-	assert window.check(None, [], saturday) is None
-	assert SendWindow(days=days, start=time(9), end=time(17)).words == words
-	assert window.postpone(window.DeferredError("ждёт окна", delay=term), hour) == term
-	assert window.postpone(window.DeferredError("пояса нет"), hour) == hour
-	assert window.postpone(RuntimeError("ящик на паузе"), hour) == hour
-	assert [box.id for box in before.fit] == [1, 2, 3]
-	assert [box.id for box in after.fit] == [3]
-	assert after.refused == dict.fromkeys(
-	assert sales.fit == ()
-	assert sales.why() == "у направления кончился дневной лимит (3 из 3 первых писем)"
-	assert [box.id for box in donors.fit] == [3]
-	assert found.refused == ({1: f"домен {words}"} if words else {})
-	assert [box.id for box in found.fit] == ([2] if words else [1, 2])
-	assert first is not None
-	assert (report.sent, report.left) == (3, 2)
-	assert report.stopped is not None
-	assert "домен исчерпан на сегодня: mail.example.test — 3 из 3" in report.stopped
-	assert (report.sent, report.left) == (2, 2)
-	assert report.stopped is not None
-	assert "у направления кончился дневной лимит (2 из 2 первых писем)" in report.stopped
-	assert (report.sent, report.left) == (2, 1)
-	assert report.stopped == (
-	assert due is not None
-	assert (report.sent, report.postponed) == (0, 1)
-	assert await session.scalar(select(MessageModel.id).where(MessageModel.step == 1)) is None
-	assert (view["senders"][0]["id"], view["senders"][0]["stage"]) == (box.id, "donors")
-	assert (domain["domain"], domain["daily_limit"], domain["sent_today"]) == (
-	assert [(d["stage"], d["daily_limit"]) for d in view["directions"]] == [
-	assert (incomplete, created) == (sending_domains.EXIT_INCOMPLETE, sending_domains.EXIT_OK)
-	assert "заводится с --stage и --daily-limit" in out
-	assert "Домен mail-b.example.test (sales): лимит 25, на паузе (жалоба)" in out
-	assert paused is not None
-	assert resumed is not None
-	assert (paused.domain, paused.stage, paused.pause_reason) == (
-	assert (resumed.paused_at, resumed.daily_limit) == (None, 25)
-	assert resumed.young_until < paused.young_until  # type: ignore[operator]
-	assert await (await session.connection()).run_sync(_cycle) == (False, True)
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 134

## Проверки на голове PR

Ветка перенесена на main `21cd8c3`; голова кода `9140d0b`, проверки — по одному разу. pytest (790 passed), diff-coverage, pre-commit и фронт прошли на стопке поверх #218 (`694b7a1` — его дерево и есть main `21cd8c3` после сквоша, код среза тот же); после переноса на `21cd8c3` заново — быстрые проверки таблицы. Полный pytest на голове ветки у агента — 4986 passed; полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 3f9663d69a56 (head) | 0 |
| ратчет сложности | Ратчет сложности: 408 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 790 passed in 81.77s (0:01:21) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=43 net_loc=2379 (+2440/-61)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 134` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  102 passed (102) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `3f31966`):

```
breakers: files=43 net_loc=2379 (+2440/-61), excluded=10 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 43, 'max_loc_diff': 2379, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `3f31966`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 22 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
