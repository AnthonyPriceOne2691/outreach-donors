# Plan: срез `mail-windows-limits` — части 4.3, 4.5a и 4.5b одним PR

## Approach

- **4.3 — окно получателя.**
  - `backend/features/core/window.py` — чистые функции на `zoneinfo`: `SendWindow`, `zone_of`, `is_open`,
    `defer_until`, `check` → `Late(words, delay)`; переходы часов — первая существующая минута и первое из двух;
    сдвиг — `random.Random` (`JITTER` модуля или аргумент), не дальше закрытия окна; `DeferredError`, `postpone`.
  - `core/stages.py` — `MailPolicy(window)`, `CURRENT`, `mail_policy(session, stage, what)` (`match` +
    `assert_never`): продажам — ответ модуля `SalesMail.policy(session)` через `_asked` моста (сброс своего до вопроса,
    точка сохранения, перевод ошибки модуля в «не подключены» с причиной и журналом), пропускает только «не
    подключены»; модуль не подключён — `CURRENT`. `Recipient.zones` — рядом с полями моста.
  - `letters/sending.py` — `_check_window` после `check_ready`, до выбора ящика: ответ (шаг ≥ `ANSWER_STEP`) —
    сразу дальше, без вопроса о политике; иначе `UnknownZoneError` / `OutsideWindowError` (`DeferredError` со сроком).
  - `letters/followups.py` — только `window.postpone(exc, POSTPONE)` в общем `except`; `letters/batch.py` — слова
    отказов в `why`; `config/sales.py` — окно из настроек; `sales/policy.py` — `sales_policy()`, `zones_of(lead)`.
- **4.5a — домены и лимиты.**
  - `SendingDomainModel` и ревизия `e054d221b2df` (после головы стопки; без значения `sales`).
  - `outreach/limits.py` — `screen(...) -> Screened(fit, refused)`, `Screened.why()`, `sending_domains(session)`.
  - `letters/mailbox._free_box` — `screen` → прежний `pick`; отказ называет причину, без неё — прежние слова.
  - `config/outreach.py` — `DIRECTION_LIMITS` из `OUTREACH_<ЭТАП>_DAILY_LIMIT`, `direction_limit(stage)`.
  - API `GET /api/senders` — `stage`, `domains`, `directions`; консоль — `cli/sending_domains.py`.
  - Экран — `api/senders.ts`, `SenderCard` (пометка и строка лимита), `SendersPage` (разделы при ≥2 этапах);
    `SendQueue`/`sendQueue` — на `Stage`.
- **4.5b — мягкие сигналы и сторож.**
  - Журнал здоровья — `SenderHealthModel` (`sender_health`, ревизия `3f9663d69a56` после `e054d221b2df`).
  - `outreach/health.py` — `SoftSignals`, `listen(session, box_id, event, moment)` (`Heard(ruled, paused)`),
    `cuts`, `_soft_day`; политика — `await stages.mail_policy(session, …)`: модуль не ответил — у `listen` прежнее
    правило парковки, у `cuts` — «не подключены» наверх.
  - `letters/events.apply_events` — `health.listen`; прежняя парковка — только у этапа без политики.
  - `outreach/limits.screen(..., cuts)`, `mailbox._free_box` — `health.cuts`.
  - `core/stages.MailPolicy` — `soft`, `watch`; `sales/policy.py` — `SoftSignals(complaints=SALES_COMPLAINT_PAUSE)`.
  - `ops/mail_watch.py` — три правила и тревога «политика не получена»; `ops/alarms.py` — `Alarm`;
    `silence.alarms()` добавляет тревоги почты; `ops/alarm_feed.Feed.tell` — по смене состояния;
    `workers/reaper.watch` — `FEED.tell(await silence_report(...))`.

## Rejected alternatives

- Окно колонкой `campaigns` или полем `sales_settings` — отклонено, потому что нужна миграция общей таблицы или
  модуля, а мост читал бы окно из двух мест.
- Второй реестр политики рядом с `register_sales` — отклонено, потому что условие d6: «одним механизмом».
- Синхронная `policy()` со своим переводом ошибки — отклонено, потому что второй путь к модулю мимо `_asked`: повтор
  перевода и без сброса своего и точки сохранения.
- Пропускать от политики любой отказ почты (`MailRefusalError`) — отклонено, потому что политика — об этапе, а не о
  письме: стоп-лист оттуда кончил бы цепочку не по смыслу.
- Лимиты внутри `pick` и второй счётчик отправленного — отклонено, потому что условия d6: отдельным фильтром до
  `pick`, один источник «отправлено сегодня».
- Лимит домена на все письма — отклонено, потому что добивки съели бы квоту новых адресатов (решение 21.09).
- Расширить `LetterStage` до `Stage` — отклонено, потому что на нём держатся `Record<LetterStage, …>` в трёх экранах.
- Журнал здоровья в `audit_log`, счётчик снижения в строке ящика — отклонено, потому что значение перечисления —
  отдельная ревизия, а счётчик некому обнулять.
- Модуль не ответил о политике — молча пропустить у вебхука и сторожа — отклонено, потому что ящик продаж остался бы
  без правила парковки, а сторож молчал бы ровно тогда, когда почта продаж стоит.
- Состояние ленты тревог в базе — отклонено, потому что процесс сторожа один и долгий.
- Три PR — отклонено решением владельца 07.10: одним PR.

## Risks

- Размер: 43 файла, 2379 строк — вейвер на размер (координатор); каждая часть — в пределе.
- Модуль продаж (4.6b-модуль) обязан отдать `policy(session)` и положить `zones_of(lead)` в `Recipient` при своём
  переносе; без первого краснеет mypy, без второго письма продаж ждут с «пояс получателя неизвестен».
- Файлы у предела: `followups.py` 499, `cli/main.py` 496, `sending.py` 485 строк из 500.
- Миграция `e054d221b2df` — после головы стопки `75242c2ed7ba` (шов агента Г); на голову main — при сборке.
- Тревоги сторожа в Telegram видны и донорам — на ревью d6; в текстах — адреса ящиков рассылки, не адресатов.
- Пороги: `SALES_COMPLAINT_PAUSE`, лимит направления, лимиты доменов — решения владельца.

## Rollout / migration

Две новые таблицы; строк нет — у Этапов 1–2 поведение прежнее. Окно, мягкие сигналы и сторож продаж включаются, когда
модуль продаж подключается к мосту и отдаёт политику. Домены заводит `outreach sending-domain` по решению владельца.

## Test / eval strategy

Чистые функции окна — на поясах и неделях перевода часов; поток — на настоящей базе дерева с подставным модулем продаж
(`FakeSalesMail`: `rules`, `zones`, поломка любого ответа) и записывающим транспортом; договор моста — параметрами
тестов поломки модуля (исключение, упавший запрос к базе, отказ не по смыслу) и сброса своего до вопроса; фильтр и
пачка — на базе с `NullTransport`; экран — vitest; события — `apply_events` на базе; сторож — на базе, Telegram —
подменой. Красный прогон — по частям; мутанты — на правилах и на стыке с мостом.
