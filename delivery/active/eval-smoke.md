# Eval smoke — this shipment

Derived from spec acceptance (S1–S16 — срез 4.6b, F1–F8 — срез 5.4). Run during verify. Поставка — модуль продаж
на мосту почты (очередь продаж, отправка цепочки, вкладка «Очередь писем») и воронка продаж, одним PR на голове
стопки (main с мостом, окнами и лимитами; Ф2; 5.3). Живых писем, задач и вызовов модели нет: транспорт — записывающий
и `NullTransport`, модель — подставная.

- [x] S1 — лид EN → письмо EN с подписью и адресом блоком в конце, `List-Unsubscribe`, тема без «Re:», с ящика продаж:
      `tests/test_sales_queue.py::test_a1_en_lead_gets_an_en_first_letter_signed_by_the_settings`,
      `tests/test_sales_send.py::test_a1_letter_goes_to_the_lead_from_the_sales_box_with_list_unsubscribe`.
- [x] S2 — нет физического адреса → письма нет; стёрли после сборки → не уходит:
      `test_a2_without_a_physical_address_nothing_is_built`, `test_a2_address_removed_after_assembly_stops_the_letter`.
- [x] S3 — два лида одной компании → два письма с разными ключами, пачка отправляет оба:
      `test_a3_two_leads_of_one_company_get_two_letters_with_their_own_keys`, `test_a3_batch_of_sales_sends_both_leads_of_one_company`.
- [x] S4 — вне коридора 15–25% → в очередь не встаёт и не уходит:
      `test_a4_letter_outside_the_corridor_is_not_queued`, `test_a4_first_letter_outside_the_corridor_does_not_go`.
- [x] S5 — коммерческих текстов нет: `scripts/gates.py --commits` (public-repo по файлам и сообщениям коммитов) — 0;
      grep добавленных строк и сообщений — verify-report.
- [x] S6 — добивки в той же переписке, с темой и `In-Reply-To` первого письма, с того же ящика; ключи с контактом:
      `test_followups_go_in_the_same_thread_with_the_first_subject`, `test_last_followup_ends_the_chain`,
      `test_two_leads_of_one_company_get_their_own_followups`.
- [x] S7 — политика продаж мостом из настроек: `test_policy_of_sales_comes_from_the_settings_through_the_bridge`;
      мутанты P1 (нынешняя политика), P2 (без окна) убиты.
- [x] S8 — пояс лида, затем страны; без пояса — ждёт словами: `test_window_counts_the_zone_of_the_lead_then_of_his_country[…]` (3),
      `test_lead_without_a_zone_waits_and_the_reason_is_said`; мутанты Z1 (пояса не отданы), Z2 (без пояса лида) убиты.
- [x] S9 — суббота: первое письмо ждёт в очереди, добивка — с открытием окна в понедельник плюс сдвиг:
      `test_first_letter_on_saturday_waits_in_the_queue`, `test_followup_due_on_saturday_goes_at_the_opening_plus_shift`.
- [x] S10 — передан точкой входа 5.3 → добивка не уходит, второму лиду компании — уходит:
      `test_lead_handed_off_by_the_handoff_entry_gets_no_more_letters`; мутанты H1 (отправка не спрашивает шов),
      H2 (передача без явной связи) убиты.
- [x] S11 — договор моста: пять вопросов почты не коммитят и не откатывают сессию: `test_module_answers_neither_commit_nor_roll_back[…]` (5);
      мутанты C2, C3 убиты.
- [x] S12 — несобираемая добивка — отложена словами, без «ошибки модуля»: `test_followup_that_cannot_be_built_waits_and_is_not_a_module_failure`;
      мутант C1 (прежний `NotReadyError`) убит.
- [x] S13 — общая кнопка пачки с этапом `sales`, число — первые письма всех гипотез: vitest `QueuePane.test.tsx` (12),
      `test_followup_stuck_in_the_queue_is_not_in_the_number_of_the_batch`; мутанты B1–B4, Q1, Q2 убиты.
- [x] S14 — «пишите другому» в диалоге сборки → новый лид: `test_referral_in_a_dialog_of_the_queue_finds_its_lead_by_the_link`;
      мутант R1 (только адрес контакта) убит.
- [x] S15 — 1.1b: срок цел, вслух — только без модуля: `TestFollowup::test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on[модуля нет]`, `[модуль есть]`.
- [x] S16 — причина без второго «ждёт»: `test_sales_reason_does_not_say_waits_twice`; мутант W1 убит.
- [x] F1 — 3 письма одному лиду → «отправлено» = 1: `tests/test_sales_funnel.py::test_a1_three_letters_to_one_lead_are_one_sent_lead`.
- [x] F2 — автоответ — не ответ: `test_a2_out_of_office_is_not_an_answer`, `test_a2_out_of_office_then_a_person_is_one_answer`.
- [x] F3 — отказ — в «отказ»: `test_a3_bounced_letter_is_bounced_not_delivered`, `test_a3_a_later_bounce_outweighs_an_earlier_delivery`.
- [x] F4 — числа экрана = запрос к базе: `tests/test_sales_funnel_api.py::test_a4_screen_numbers_match_a_query_to_the_base`,
      `test_a4_numbers_of_the_whole_period_in_words`.
- [x] F5 — «отправляется» — не «отправлено», «ушло» — общее: `test_letter_whose_outcome_is_unknown_is_not_sent_yet`; мутант G1 убит.
- [x] F6 — «передан» — условием шва цепочки: `test_handed_off_by_the_rule_that_stops_the_chain`.
- [x] F7 — период по первому письму, полуинтервал, разрез без дыр: `test_period_is_by_the_first_letter_of_the_lead`,
      `test_period_takes_its_start_and_leaves_its_end`, `test_adjacent_periods_split_every_step_without_gaps_or_double_counting`.
- [x] F8 — вкладка «Воронка»: vitest `FunnelPane.test.tsx` (14) — на узком окне период столбиком, отказ сервера словами.
- [ ] Живьём на голове PR — не мерено: стенд не поднимался при переносе. Вкладки «Очередь писем» и «Воронка» мерены
      на образцах до переноса (обе темы, 1440 и 390, клавиатура); кнопка пачки на вкладке очереди теперь общая —
      её замер на 390 — «заложено».
