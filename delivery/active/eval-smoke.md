# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 1.1b `sales-stage`, часть «б» (ответ лида продаж ждёт человека).

- [x] A3 — ответ человека в треде продаж через `Inbox.accept` (настоящая подпись адреса ответа; домен лида —
      заодно принятый донор; в ответе «$300 a month»): вид — человек, привязан к письму и треду, разбор цены
      не поставлен, «ждёт человека» с причиной `SALES_WAITING`, срок добивки погашен, тред — «ответил», адрес
      `boss@…` в контакты не записан:
      `tests/test_sales_stage_replies.py::test_a3_answer_is_kept_and_waits_for_a_human_not_as_a_lead_or_a_price`.
- [x] Задача разбора в обход: модель-счётчик — 0 вызовов, «ответ продаж» с причиной, цена в карточку донора не
      легла: `test_parse_job_that_slipped_through_calls_no_model_and_writes_no_price`; повтор вебхука модели не
      отдаёт: `test_webhook_repeat_does_not_hand_it_to_the_model`.
- [x] Подтверждение цены — отказ до записи (сервис) и 409 словами с пустой карточкой донора (API):
      `test_price_confirmation_is_refused_before_any_write`, `test_screen_confirmation_gets_409_and_the_donor_card_stays_empty`.
- [x] «Взять лид» — отказ словами продаж (сервис и API 409): `test_sales_answer_is_not_taken_as_an_advertiser_lead`.
- [x] Последствия ответа продаж по видам и правило модели по этапам — без базы:
      `test_consequences_of_a_sales_answer` (4), `test_only_donor_answers_go_to_the_price_model` (4).
- [x] Диалог продаж — своё состояние (`sales_pending`; взятый — «ответил»; отписка сильнее), карточка
      говорит причину: `test_sales_thread_has_its_own_state_not_a_price_or_a_lead` (5), `test_card_says_why_a_sales_answer_waits`.
- [x] Новые тесты на коде до изменений — красные (прогон ветки `sales/1.1b-stage`, код части при переносе не менялся): на значении без отказов (`d432e40`, имена `SALES_WAITING`,
      `priced_by_model`, `SALES_PENDING` подставлены с прежней логикой) — 9 из 20 красные; зелёные 11 названы
      (verify-report).
- [x] Обратные прогоны — 9 мутантов точек ответов — 9 из 9 убиты (прогон ветки `sales/1.1b-stage`; при переносе код
      части не менялся — повторно не гонялись).
- [x] Экраны диалогов — состояние, которого экран не знает: список и карточка не падают и показывают его кодом:
      `frontend/src/threads/UnknownThreadState.test.tsx` (2); без правки — оба падают
      `TypeError: Cannot read properties of undefined (reading 'color')`; мутанты запаса — 4 из 4 убиты.
- [x] Правила этапа вынесены из `replies/repository.py` в `replies/stage_rules.py` (перенос на main `f9819da`): те же тесты
      зелёные; мутанты вынесенного — R1 цена продажам без отказа, R2 цена рекламодателю можно, R3 лид продажам без отказа,
      R4/R5 вызовы правил сняты (как на main), R6 mypy на новом этапе — 6 из 6 убиты.
- [x] Тесты части и соседей на голове `a9d94da` (main `f9819da`) — 1590 passed (66 файлов); vitest `src/threads` — 65 passed.
- [ ] Живой ответ лида продаж через вебхук — «заложено»: писем продаж нет, пока почта им отказывает.
