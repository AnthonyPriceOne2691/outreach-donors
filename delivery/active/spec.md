# Spec — срез 5.3 `sales-handoff` (черновик, 06.10)

Передача лида продаж телемаркетологу: сделка в Kommo → сообщение в Telegram, запасной путь,
повторы. Механизм и одна точка входа; триггер (вид ответа Ф2, ситуация агента Ф3) не строится —
его позовут 2.2 и 3.2.

## Problem

Лид продаж — человек, который в ответе выразил желание пообщаться. Он должен попасть в Kommo
сделкой и дойти до телемаркетолога сообщением в Telegram тремя строками со ссылкой на сделку.
Ни один из отказов по пути — Kommo не подключён, Kommo не ответил, ответ потерян, Telegram
недоступен — не должен кончаться тишиной: лид не теряется молча.

## In scope

- **Таблица `sales_handoffs`** — одна строка на диалог (ключ `uq_sales_handoffs_thread`): лид,
  диалог, номер сделки Kommo, два состояния — Kommo (`pending`, `retry`, `unconfirmed`, `failed`,
  `done`, `off`) и Telegram (`pending`, `sent`, `undelivered`), попытки, последний сбой словами,
  отмеченный ответ, доставленная ссылка и когда, срок прохода, захват задачи.
- **Точка входа** `backend/features/sales/handoff.start(session, thread_id)`: лид продаж диалога
  по правилу (`lead_of`), передача (одна на диалог), задача в общую очередь (`with_retries()`).
  Идемпотентна: без нового ответа — ничего; новый ответ — примечание к той же сделке. Коммитит
  сессию.
- **Задача** `backend/features/sales/handoff_jobs.hand_off_lead` → `handoff.process`: Kommo
  (сделка `create_complex_lead` или примечание), затем Telegram; копия в группу — настройкой.
- **Запасной путь**: Kommo не подключён (`SALES_KOMMO_PROVIDER=fixture`), не ответил, ответ
  потерян, отказал — телемаркетологу ссылка на диалог с пометкой; тревога владельцу через
  `shared/alerts` (кроме «не подключён»).
- **Повторы по расписанию**: третий цикл процесса разбора (`backend/workers/reaper.py`) раз в
  5 минут ставит в очередь передачи, которым пора (`handoff.due`): Kommo не ответил (срок —
  15 минут) или задача потерялась.
- **Бот продаж** `backend/features/sales/telegram.py`: три попытки, отказы словами и с советом,
  токен только в адресе Bot API и нигде в журнале и текстах. Команда
  `outreach sales-telegram-chat-id` — `getUpdates` один раз, печать номеров чатов.
- **Шов цепочки продаж** — `handoff.handed_off(session, lead_id)`: «заложено» для 4.6b.
- **Настройки** (`backend/config/sales.py`, `.env.example`, пустые): `SALES_TELEGRAM_BOT_TOKEN`,
  `SALES_TELEGRAM_CHAT_ID`, `SALES_TELEGRAM_GROUP_CHAT_ID`, `SALES_TELEGRAM_GROUP_COPY` (true),
  `SALES_APP_URL`.

## Out of scope

- Триггер — вид ответа и ситуация агента (2.2, 3.2). Экран передач.
- Цепочка продаж и её остановка (4.6b) — только шов.
- Обратная синхронизация статусов Kommo; поиск и слияние компаний в Kommo (открытый вопрос 5.2).
- Живые Kommo и Telegram — 5.5, после доступа и переподписи.

## Acceptance examples

| # | Вход | Ожидаемый выход | Тест |
|---|---|---|---|
| A1 | ответ «давайте созвонимся» | сделка в Kommo (fixture) с примечанием → телемаркетологу ровно три строки со ссылкой на сделку → копия в группу → `handed_off` = да | `test_a1_deal_then_three_lines_then_group_copy_and_chain_held` |
| A2 | тот же человек пишет ещё раз | примечание к той же сделке, второй сделки нет; телемаркетологу второй раз не пишем | `test_a2_next_answer_is_a_note_to_the_same_deal`, `test_a2_same_trigger_twice_is_one_handoff` |
| A3 | Kommo 5xx после повторов клиента | телемаркетологу ссылка на диалог + «сделка в Kommo не создана, повторяем»; тревога владельцу; `retry`, срок +15 мин; позже — сделка и ссылка на неё | `test_a3_kommo_down_sends_dialog_link_alerts_and_waits_for_retry`, `test_a3_retry_makes_the_deal_and_sends_its_link_once`, `test_a3_note_that_failed_is_written_later_once` |
| A4 | Telegram недоступен | 3 попытки → тревога эксплуатации; `undelivered`; токена нет ни в ошибке, ни в тревоге | `test_a4_telegram_down_three_attempts_ops_alert_undelivered`, `test_a4_token_from_network_error_stays_out_of_error_and_alert`, `test_a4_three_attempts_then_temporary_refusal` |
| A5 | копия в группу выключена | только личное сообщение | `test_a5_group_copy_off_sends_only_personal` |
| A6 | ответ до подключения Kommo | сообщение со ссылкой на диалог, `off`, без тревоги — не тишина | `test_a6_kommo_not_connected_sends_dialog_link`, `test_job_hands_off_with_dialog_link_before_kommo_is_connected` |
| A7 | запись в Kommo ушла, ответ потерян | без повтора записи; контакт есть (или был) — `unconfirmed`, тревога «проверить руками»; контакта не было ни до, ни после — `retry` | `test_unconfirmed_write_*`, `test_contact_that_was_there_before_the_write_*` |
| A8 | у диалога ноль или два лида продаж | громкий отказ с номерами, передачи нет | `test_dialog_without_sales_lead_is_refused_loudly`, `test_two_leads_for_one_dialog_are_not_guessed` |
