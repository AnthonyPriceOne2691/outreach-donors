# Spec: модуль «Продажи» — срез 4.6b (модульная часть: очередь продаж и отправка цепочки) и срез 5.4 (воронка) одним PR

Источники: Spec 4.6 плана фаз (примеры A1–A5, «даю да» 01.10; разрез 4.6 на части — 05.10) и Spec 5.4 (примеры
A1–A4, «даю да» плана Ф2–Ф5 06.10). Обе части написаны раньше базы этого PR и перенесены на её голову: main с мостом
почты к модулю продаж (договор #211/#216), окнами получателя и лимитами (#219), разбором ответов продаж (Ф2) и
передачей лида (5.3). Номера примеров ниже — буквой части: S — 4.6b, F — 5.4; в скобках — номер исходной Spec.

## Problem

Цепочка продаж лежит в базе, мост почты к модулю продаж есть, окна, лимиты и разбор ответов — тоже, но модуля, который
отвечает мосту, в main нет: письма продаж не из чего собрать и некому отправить, а сколько лидов на каком шаге — не
видно нигде. Модуль обязан встать на общую почту (отправка, пачка, добивки, ящики, окна — общие) и отвечать ей по
договору моста; воронка — считать лидов одним правилом у экрана, счётчиков и выгрузки.

## In scope

- **4.6b:** регистрация модуля в мосту; ответы мосту — кому (адрес лида по явной связи `sales_threads`, имя из
  «Отправителя», пояса получателя), проверка (снят, передан — шов 5.3, стоп-лист; письмо цело), подключены ли, текст
  добивки, политика почты (окно, мягкие сигналы, сторож — из настроек продаж); отказ — только словами почты, внутри
  ответа — ничего не сохранять. Подключение, сборка очереди, ключ с контактом, повторная сверка, API, консоль, вкладка
  «Очередь писем» с общей кнопкой пачки на этапе продаж.
- **Стыки с базой:** окно получателя для писем продаж (4.3); пачка — только первые письма (#219 и d6); «пишите
  другому» (2.3a) в диалоге сборки; тесты 1.1b при подключённом к мосту модуле; второй реестр чистки (#192).
- **5.4:** подсчёт функциями по гипотезам и периоду; API под правом `sales`; вкладка «Воронка».

## Out of scope

- Своё письмо для `referral` и `{{first_name}}` — вопросы владельцу; до ответа — тот же шаг 1 и полное имя.
- Провод «хочет говорить» → `handoff.start` в разборе ответа (в базе — пометка `mark_for_handoff`): провод 5.3 ↔ Ф2.
- Окна автоотправки (4.4), домены и DNS (4.7), отдельный разгон доменов продаж.
- Открытия и клики (пиксель вредит доставляемости); MQL и SQL — в Kommo; ответы по видам и CSV — отчёт 6.3.

## Acceptance examples

| # | Вход | Ожидаемый выход | Где проверено |
|---|---|---|---|
| S1 (A1) | лид EN | письмо EN: подпись и адрес блоком настроек в конце, `List-Unsubscribe`, тема без «Re:»; уходит с ящика продаж, From — имя «Отправителя» | `tests/test_sales_queue.py::test_a1_en_lead_gets_an_en_first_letter_signed_by_the_settings`, `tests/test_sales_send.py::test_a1_letter_goes_to_the_lead_from_the_sales_box_with_list_unsubscribe` |
| S2 (A2) | нет физического адреса в «Отправителе» | сборка — отказ словами, письма нет; адрес стёрли после сборки — письмо не уходит | `test_a2_without_a_physical_address_nothing_is_built`, `test_a2_address_removed_after_assembly_stops_the_letter` |
| S3 (A3) | два лида одной компании | два письма, ключи `sales:{домен}:{адрес}:{шаг}` разные; пачка отправляет оба | `test_a3_two_leads_of_one_company_get_two_letters_with_their_own_keys`, `test_a3_batch_of_sales_sends_both_leads_of_one_company` |
| S4 (A4) | переписанное письмо вне коридора 15–25% | в очередь не встаёт; первое письмо вне коридора не уходит | `test_a4_letter_outside_the_corridor_is_not_queued`, `test_a4_first_letter_outside_the_corridor_does_not_go` |
| S5 (A5) | репозиторий | ни одного коммерческого текста: тексты — строками базы, в тестах выдуманные | `scripts/gates.py` (public-repo, и по сообщениям коммитов), grep — verify-report |
| S6 | первое письмо ушло, срок добивки | добивка в той же переписке: тема и `In-Reply-To` первого письма, тот же ящик, ключ `…:{адрес}:1`; последняя кончает цепочку | `test_followups_go_in_the_same_thread_with_the_first_subject`, `test_last_followup_ends_the_chain`, `test_two_leads_of_one_company_get_their_own_followups` |
| S7 | политика почты продаж мостом | окно пн–пт 9–17 из настроек продаж, мягкие сигналы с порогом жалоб продаж, сторож включён | `tests/test_sales_send_zones.py::test_policy_of_sales_comes_from_the_settings_through_the_bridge` |
| S8 | понедельник 14:00 в Берлине | пояс лида — Берлин: уходит; пояса нет, страна `de` — уходит; пояс лида Нью-Йорк (08:00), страна `de` — ждёт: пояс лида раньше страны; пояса нет ни у лида, ни у страны — «пояс получателя неизвестен», письмо в очереди | `test_window_counts_the_zone_of_the_lead_then_of_his_country[…]`, `test_lead_without_a_zone_waits_and_the_reason_is_said` |
| S9 | суббота | первое письмо ждёт в очереди («уйдёт не раньше пн 19.10 09:…»); добивка — в понедельник с открытием окна плюс сдвиг, уходит в той же переписке | `test_first_letter_on_saturday_waits_in_the_queue`, `test_followup_due_on_saturday_goes_at_the_opening_plus_shift` |
| S10 | лид передан точкой входа 5.3 по диалогу продаж | добивка ему не уходит, цепочка остановлена; второму лиду компании — уходит; передан до отправки — письмо не уходит | `test_lead_handed_off_by_the_handoff_entry_gets_no_more_letters`, `tests/test_sales_send.py::test_lead_that_must_not_be_written_stops_the_letter[handed_off]` |
| S11 | пять вопросов почты модулю (кому, проверка, подключены ли, добивка, политика) | ни одного `commit` и `rollback` сессии почты внутри ответа | `tests/test_sales_mail_contract.py::test_module_answers_neither_commit_nor_roll_back[…]` |
| S12 | имя лида стёрли после первого письма | добивка отложена на час словами «не собрана — в письме подстановка без значения», без записи «ошибка модуля продаж» | `test_followup_that_cannot_be_built_waits_and_is_not_a_module_failure` |
| S13 | вкладка «Очередь писем», в очереди 7 первых писем всех гипотез | общая кнопка «Отправить очередь · 7» с этапом `sales`, вкладка говорит «всех гипотез»; добивка, застрявшая без ящика, в число не входит, пачка уходит ровно названным числом | vitest `QueuePane.test.tsx` «уходит этапом продаж…», `tests/test_sales_queue_api.py::test_followup_stuck_in_the_queue_is_not_in_the_number_of_the_batch` |
| S14 | «пишите другому» в диалоге, заведённом сборкой | новый лид той же гипотезы, домена и пояса, `referral`, прошёл очистку; диалог закрыт | `tests/test_sales_referral_link.py::test_referral_in_a_dialog_of_the_queue_finds_its_lead_by_the_link` |
| S15 | 1.1b, добивка продаж раньше донорской | срок продаж цел, донорская уходит; модуля в мосту нет — сказано вслух, модуль есть, продажи выключены — журнал молчит | `tests/test_sales_stage_mail.py::TestFollowup::test_a4_deadline_is_kept_and_said_aloud_while_donors_go_on[…]` |
| S16 | ответ продаж ждёт разбора | на карточке «Ждёт человека: вид ответа продаж ещё не разобран — …» — без второго «ждёт» | `tests/test_sales_stage_screens.py::test_sales_reason_does_not_say_waits_twice` |
| F1 (A1) | 3 письма одному лиду | «отправлено» = 1 лид | `tests/test_sales_funnel.py::test_a1_three_letters_to_one_lead_are_one_sent_lead` |
| F2 (A2) | автоответ | не «ответ»; автоответ, затем человек — один ответ | `test_a2_out_of_office_is_not_an_answer`, `test_a2_out_of_office_then_a_person_is_one_answer` |
| F3 (A3) | отказ доставки | в «отказ», не в «доставлено»; поздний отказ сильнее ранней доставки | `test_a3_bounced_letter_is_bounced_not_delivered`, `test_a3_a_later_bounce_outweighs_an_earlier_delivery` |
| F4 (A4) | числа экрана | сходятся с запросом к базе в тесте (сырой SQL и счёт циклом), восемь сочетаний гипотезы и периода | `tests/test_sales_funnel_api.py::test_a4_screen_numbers_match_a_query_to_the_base`, `test_a4_numbers_of_the_whole_period_in_words` |
| F5 | письмо «отправляется» (исход неизвестен) | ещё не «отправлено»: «ушло» у воронки — общее определение почты | `test_letter_whose_outcome_is_unknown_is_not_sent_yet` |
| F6 | передача заведена | «передан» — тем же условием, что останавливает цепочку | `test_handed_off_by_the_rule_that_stops_the_chain` |
| F7 | период | по первому ушедшему письму лида, полуинтервал; соседние периоды делят лидов каждого шага без дыр | `test_period_is_by_the_first_letter_of_the_lead`, `test_period_takes_its_start_and_leaves_its_end`, `test_adjacent_periods_split_every_step_without_gaps_or_double_counting` |
| F8 | вкладка «Воронка» на 390 px | период столбиком во всю ширину; отказ сервера — словами | vitest `FunnelPane.test.tsx` «на узком окне период — столбиком…», «отказ сервера — словами» |
