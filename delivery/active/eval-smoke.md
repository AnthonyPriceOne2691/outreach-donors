# Eval smoke — this shipment

Поставка — срез 4.6b `sales-send`, часть «общее»: мост общей почты к модулю продаж. Run during verify. Модуль продаж в
части отсутствует; в тестах — подставной, зарегистрированный тем же `register_sales`.

- [x] Без модуля продаж письмо продаж — прежний отказ 1.1b словами, учётка этапа не тронута, письмо в очереди:
      `tests/test_sales_stage_bridge.py::test_without_the_module_a_sales_letter_is_refused_as_before`,
      `tests/test_sales_stage_mail.py::TestSending` (точные слова 1.1b); мутант R1 убит.
- [x] Без модуля сроки добивок продаж не захватываются — срок цел и назван:
      `::test_without_the_module_sales_deadlines_wait_and_are_not_claimed`, 1.1b
      `TestFollowup::test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on`; мутант R2 убит.
- [x] С модулем: адрес и имя в From — его ответы, а не строка `contacts` домена; ящик — продаж; проверка модуля
      спрошена: `::test_sales_letter_goes_to_the_address_and_in_the_name_the_module_gives`.
- [x] Отказ проверки модуля останавливает письмо до ящика: `::test_refusal_of_the_module_check_stops_the_letter_before_the_mailbox`;
      мутант R4 убит.
- [x] Отправка очереди пачкой спрашивает тот же мост: модуль говорит «нет» — отказ словами до задачи, «да» — пачка
      ставится; без модуля — отказ 1.1b: `::test_queue_send_asks_the_module_whether_sales_are_connected` (×2),
      `::test_queue_send_without_the_module_is_refused_as_before`; 1.1b `TestSendQueue`; мутанты R5, R6 убиты.
- [x] «Не подключены» от модуля — словами и до учётки этапа: `::test_not_connected_answer_of_the_module_is_said_before_the_transport`.
- [x] Добивка продаж — в той же переписке: тема и `In-Reply-To` первого письма, ящик первого письма (второй ящик
      продаж свободен — не взят), текст модуля, ключ из ключа первого письма:
      `::test_sales_followup_goes_in_the_same_thread_with_the_text_of_the_module`.
- [x] Модуль говорит «не подключены» — срок цел, текст не спрошен: `::test_sales_deadline_waits_while_the_module_says_not_connected`.
- [x] Добивка, ждавшая ящик, уходит с нынешним текстом модуля, строка одна: `::test_followup_waiting_for_a_box_goes_with_the_text_of_today`.
- [x] Ключ добивки — из ключа предыдущего письма, у доноров — как прежде: `::test_followup_key_inherits_the_first_letter_key` (×5),
      `::test_followup_key_of_a_donor_is_the_same_as_before`.
- [x] Доноры и рекламодатели — мост отдаёт их адрес `contacts` нетронутым, модуль продаж не спрошен:
      `::test_bridge_gives_donors_and_advertisers_their_contact_untouched` (×2).
- [x] Пути доноров, которые продажи не ведут вовсе, — отказ словами «письма продаж собирает модуль продаж»:
      `tests/test_sales_stage_mail.py::test_every_mail_point_refuses_sales_in_words` (все точки), `TestQueue`.
- [x] Второй рубеж добивки: проход счёл продажи подключёнными, текста нет — срок возвращается, строки добивки нет:
      `TestFollowup::test_sending_refusal_gives_the_deadline_back`.
- [x] Застрявшая добивка (находка 07.10) — на базе с #202 воспроизведение зелёное: очередь этапа без неё, пачка её
      не трогает, добивка ждёт ящик своей переписки и уходит проходом в той же переписке (`../stuck-followup-repro.py.txt`).
- [x] Журнал прохода добивок: без модуля — предупреждение «подошли, срок не погашен» словами 1.1b; модуль
      зарегистрирован и говорит «не подключены» — срок цел, `PassReport.waiting` = 1, предупреждения нет:
      `::test_without_the_module_sales_deadlines_wait_and_are_not_claimed`,
      `::test_sales_deadline_waits_while_the_module_says_not_connected`, 1.1b
      `TestFollowup::test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on`; мутанты W1, W2 убиты.
- [x] Поломка модуля продаж (исключение, упавший запрос к базе, неподходящий отказ) не роняет проход добивок:
      донорская ушла, срок продаж цел — «подключены ли» упало: не взят; текст добивки: возвращён на час:
      `::test_a_broken_sales_module_does_not_stop_the_pass_for_donors` (×6); мутанты K1, K2, K4, K5, K6 убиты.
- [x] Поломка модуля на отправке — «не подключены» с причиной, письмо в очереди, ничего не ушло:
      `::test_a_broken_sales_module_is_said_in_words_and_the_letter_waits` (×2); пачка — отказ словами:
      `::test_queue_send_with_a_broken_module_is_refused_in_words`; отказ модуля словами почты (стоп-лист) — как
      есть: `::test_refusal_of_the_module_check_stops_the_letter_before_the_mailbox`; мутанты K3, K7 убиты.
- [x] Граница договора: модуль ответил на «кому писать» отказом почты не по смыслу — `NoSenderError` (идёт как
      есть) и `MaybeSentError` (мост переводит в «не подключены»). Пачка встаёт словами отказа, ничего не уходит,
      письмо в очереди: `::test_an_odd_refusal_of_the_module_stops_the_batch_in_words` (×2); проход добивок
      собирает добивку строкой и откладывает на час с причиной в журнале, «исход неизвестен» не назван, донорская
      ушла: `::test_an_odd_refusal_of_the_module_postpones_the_followup_and_donors_go` (×2); мутант K8 убит.
