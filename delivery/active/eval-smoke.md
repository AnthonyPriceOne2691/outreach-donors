# Eval smoke — срез `mail-windows-limits`, части 4.3, 4.5a и 4.5b одним PR

Derived from spec acceptance (W — 4.3, L — 4.5a, H — 4.5b), the neighbour session's conditions (07.10 ~17:05) and
the bridge contract (#211, #216). Run during verify.

## 4.3 — окно получателя

- [x] W1 Добивка продаж в субботу 10:00 (Берлин) ждёт до понедельника 09:00 + сдвиг, в 9:00–9:30; в 9:30 ушла с ящика переписки — `tests/test_sales_send_window.py::test_a1_saturday_followup_waits_for_monday_nine_to_half_past`; чистая функция — `tests/test_send_window.py::test_a1_saturday_moves_to_monday_nine_to_half_past_by_the_generator`.
- [x] W2 Пояс по порядку лида, страны (DE → Europe/Berlin), гипотезы — `test_a2_zones_of_a_lead_go_lead_country_hypothesis`, `test_a2_the_first_known_zone_decides_and_an_unknown_name_gives_way`.
- [x] W3 Добивка доноров ночью уходит; `CURRENT` у доноров, рекламодателей и у продаж без модуля — `test_a3_donor_followup_at_night_goes_as_before`, `test_donors_and_advertisers_keep_the_policy_they_had`.
- [x] W4 `[3, 5]` — второе через 3 дня, третье через 5 после второго, дальше срока нет — `test_a4_sales_chain_goes_three_then_five_days_after_the_previous_letter`.
- [x] W5 Первое письмо вне окна: `OutsideWindowError`, письмо `queued`, ящик не выбран, `why` — «вне окна получателя»; в окне — ушло — `test_first_letter_outside_the_window_stays_queued_and_is_named`.
- [x] W6 Пояса нет: «пояс получателя неизвестен» — `test_without_any_zone_the_first_letter_waits_and_the_refusal_says_why`.
- [x] W7 Ответ в переписке в субботу уходит, и при сломанной политике — `test_an_answer_in_the_thread_does_not_wait_for_the_window`.
- [x] W8 Модуль бросает в `policy`: проход добивок идёт, донорская ушла, срок продаж через час, причина в журнале — `tests/test_sales_stage_bridge.py::test_a_broken_sales_module_does_not_stop_the_pass_for_donors[policy-…]` (исключение, запрос к базе, стоп-лист не по смыслу).
- [x] W9 Модуль бросает в `policy` при отправке: «не подключены» с причиной, письмо в очереди — `test_a_broken_sales_module_is_said_in_words_and_the_letter_waits[policy]`.
- [x] W10 Сбой своих несохранённых изменений почты у вопроса о политике — как есть, модуль не спрошен — `test_own_unsaved_change_failure_surfaces_as_is_and_the_module_is_not_asked[policy]`.
- [x] W11 Недели перевода часов (Берлин, Нью-Йорк, Сидней), время, которого нет (Берлин 02:30, Гавана 00:30), и время, которое бывает дважды — `test_the_week_of_a_clock_change_opens_at_local_nine`, `test_a_start_inside_a_clock_change`.
- [ ] Живое письмо продаж в окне и вне его — «заложено»: модуль продаж подключается к мосту в 4.6b-модуле.

## 4.5a — домены и лимиты

- [x] L1 Лимит домена на два ящика: фильтр и пачка (ушло 3, осталось 2, причина словами; добивка домена лимит не съела) — `tests/test_sending_limits.py::test_a1_domain_limit_is_shared_by_all_its_boxes`, `TestBatch::test_a1_batch_stops_with_the_domain_in_words`.
- [x] L2 Лимит направления — `test_direction_limit_stops_every_box_of_the_stage_and_only_it`, `TestBatch::test_direction_limit_stops_the_batch_in_words`.
- [x] L3 Пауза, выдержка, чужое направление, выдержка прошла — `test_paused_young_or_foreign_domain_is_named` (4 случая).
- [x] L4 Этапы 1–2 без строк — прежние слова дословно (`TestBatch::test_stages_1_2_without_rows_stop_with_the_old_words`); экран у одних доноров прежний, «Отправлять нечем» — vitest; 409 на пустой очереди — `tests/test_letters_send_queue.py` (зелёный).
- [x] L5 Свой ящик исчерпан, свободен чужой — добивка ждёт — `test_a_followup_waits_for_its_own_box_even_when_another_is_free`.
- [x] L6 Экран: разделы, пометка, лимиты — vitest «разделы этапов, пометка продаж, лимит домена и направления»; API — `TestScreen::test_the_senders_screen_shows_stage_domains_and_directions`.
- [x] L7 Пачка продаж: `{stage: 'sales'}`, 409 словами — vitest «продажи уходят своим этапом, отказ сервера — словами».
- [x] L8 Консоль: завести, поправить, пауза, снять — `TestConsole::test_a_domain_is_added_tuned_paused_and_resumed`.
- [x] L9 Ревизия — откат и подъём в процессе — `test_the_revision_goes_down_and_up`.
- [ ] Живой экран (обе темы, 1440 и 390, клавиатура) — не снимался.

## 4.5b — мягкие сигналы и сторож

- [x] H1 Три мягких сигнала — журнал `deferred, deferred, blocked, limit_cut`, лимит 20 → 10, фильтр называет причину; через сутки от времени платформы — полный — `tests/test_sales_soft_signals.py::test_a2_three_soft_signals_cut_the_box_for_a_day`.
- [x] H2 Доноры — следов нет — `test_donors_soft_signals_leave_no_trace`.
- [x] H3 3 отказа из 20: продажи — пауза, доноры — нет, продажи с молчащим о политике модулем — прежнее правило, журнал пуст — `test_three_bounces_in_twenty_pause_sales_by_the_window_and_not_donors[…]`.
- [x] H4 Одна жалоба — пауза — `test_one_complaint_in_the_window_pauses_the_sales_box`.
- [x] H5 Отказы старше окна — паузы нет — `test_bounces_older_than_the_window_do_not_pause`.
- [x] H6 Ящик молчит 15 минут — тревога (через 10 минут — нет; письмо ушло 5 минут назад — нет; тревога в общем списке) — `tests/test_mail_watch.py::test_a3_a_box_with_waiting_letters_and_nothing_sent_is_quiet`.
- [x] H7 Все на паузе, очередь есть — «некому» и «все на паузе»; домен на паузе — «некому» — `test_a4_queue_and_nobody_to_send_while_all_boxes_are_paused`.
- [x] H8 Доноры — новых тревог нет — `test_donors_get_no_new_alarms`.
- [x] H9 Модуль не ответил о политике — тревога `no-policy:sales` — `test_a_module_without_a_policy_is_an_alarm_and_the_watch_goes_on`.
- [x] H10 «тревога» и «прошло» — по одному разу на 4 прохода — `test_an_alarm_is_told_once_and_its_end_once`.
- [x] H11 Без бота — строка «ТРЕВОГА НЕ ОТПРАВЛЕНА» один раз — `test_without_a_bot_the_journal_line_is_said_once`.
- [x] H12 Telegram не принял — повтор — `test_a_refused_alarm_is_said_again_next_pass`.
- [x] H13 Проход сторожа отдаёт найденное ленте — `tests/test_reaper.py::test_watch_reports_silence_and_tells_the_feed`.
- [x] H14 Ревизия журнала — откат и подъём — `test_the_journal_revision_goes_down_and_up`.
- [ ] Живая тревога в Telegram, канарейка выкатки (A5), восстановление копии (A6) — «заложено», у выкатки d6.

## Все части

- [x] Красный прогон по частям — новые тесты не собираются на коде до части; мутанты — в verify-report.
