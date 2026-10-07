# Spec: модуль «Продажи», Ф2 одним PR — ответ лида продаж: своя очередь, вид моделью, другой контакт, автоответ и отписка

## Problem

После 1.1b ответ человека в треде продаж сохраняется и ждёт человека, но никуда не уходит: разбирать его нечем, а
разбор в единственной очереди `runs` ждал бы часовой прогон доноров. Правила приёма отличают человека от
автоответа, отписки и отказа доставки, но что хочет человек, не знают; продажам нужен вид ответа и путь по нему,
а «пишите другому», автоответ и отписка словами — свои последствия. Четыре части одним PR (решение владельца 07.10):
2.1, 2.2, 2.3a, 2.3b.

## In scope

- **2.1 — своя очередь.** Ответ человека в треде продаж — задача `SALES_REPLY_JOB` в очереди `sales`
  (`outcome.to_sales_queue`, одно правило для приёма и повтора вебхука); разбора цены нет, «лида рекламодателя» нет.
  Свой процесс воркера `--queue sales`, проверка здоровья `health sales`, сервис compose `worker-sales` с лимитами на
  проде, `restore.sh` останавливает его. Время повтора задачи — из её очереди (`_next_try`).
- **2.2 — вид ответа моделью (волна В3а).** Промпт файлом `backend/features/sales/prompts/reply_kind.md`; вход как у
  разбора цены (письмо — данные, цитата снята, ≤ 20 000 знаков, адреса замаскированы, утёкший адрес — запроса нет);
  строгий JSON `{kind, confidence, quote, contact?}`; цитата и адрес — дословно из письма, иначе уверенность вниз; сбой
  разбора — `parse_failed`; порог `SALES_REPLY_CONFIDENCE`; пин `LLM_SALES_CLASSIFY_MODEL`, версия промпта, операция
  `sales_reply_kind`, снимок ответа модели. Путь по виду решает код:

  | Вид | Путь |
  |---|---|
  | `wants_to_talk` | точка передачи лида (`mark_for_handoff`; вход передачи 5.3 — коммитом стыка) |
  | `question` / `interested` | агент (Ф3); пока — ручная очередь с видом |
  | `referral` | новый лид той же компании (2.3a) |
  | `not_interested` / `not_now` | закрыть диалог, без давления |
  | `unsubscribe` | адрес закрыт во всех направлениях (2.3b) |
  | `parse_failed` и ниже порога | ручная очередь продаж |

  Eval `scripts/eval_sales_reply.py`: синтетика 28 примеров, манифест внешнего набора (`SALES_GOLDEN_DIR`), порча
  промпта закрывает ворота. Подпись операции на экране «Расход».
- **2.3a — другой контакт.** «Пишите другому» с адресом — лид той же компании (домен тот же, источник `referral`,
  ссылка на исходный тред), очистка 1.4 — как у любого лида, исходный тред закрыт. Идущий диалог продаж — не «другое
  направление» очистки. Неверная настройка проверки адресов не роняет задачу после вызова модели.
- **2.3b — автоответ и отписка.** Автоответ цепочку продаж не останавливает: следующий шаг — не раньше даты
  возвращения из текста или `SALES_OOO_DELAY_DAYS`. Отписка в треде продаж (правилами или видом модели не ниже порога)
  закрывает адрес во всех направлениях (стоп-лист без этапа), назначенное снято. `ReplyRepository.suppress`: пропуск —
  только при бессрочной строке без этапа.

## Out of scope

- Передача лида (`handoff.start`, срез 5.3) — здесь только точка; стык — два коммита после слива 5.3 и Ф2.
- Агент на вопросы (Ф3); письмо лиду из «пишите другому» (очередь Ф4, 4.6b); экран продаж.
- Живой eval на наборе владельца; правка скрипта выкатки соседней сессии (12 healthy, `worker-sales` в списке образов).
- Изменение решения владельца для доноров.

## Acceptance examples

| # | Вход и ожидаемый выход | Тест |
|---|---|---|
| Q1 | Ответ человека в треде продаж — задача `SALES_REPLY_JOB` в очереди `sales`, разбора цены нет, «лида рекламодателя» нет, цепочка остановлена | `tests/test_sales_reply_routing.py::test_a1_human_answer_goes_to_the_sales_queue_not_to_price_or_lead`, `test_a1_inbox_hands_the_answer_to_sales_and_stops_the_chain` |
| Q2 | В `runs` часовой прогон — задачу продаж берёт свой воркер: флаг `--queue`, сервис compose, здоровье, тело задачи на базе без общей очереди | `test_a2_worker_listens_to_the_queue_it_is_given_with_a_scheduler`, `test_a2_compose_runs_a_separate_sales_worker_that_restore_stops`, `test_a2_health_checks_the_sales_worker_against_its_own_queue`, `test_a2_the_job_body_takes_the_answer_without_the_runs_queue` |
| Q3 | Redis недоступен — 503 с причиной, повтор вебхука ставит задачу продаж; разобранному — нет | `test_a3_queue_down_means_503_and_the_retry_queues_the_sales_job`, `test_a3_retry_after_the_answer_was_sorted_queues_nothing` |
| Q4 | Автоответ и отказ доставки в треде продаж решают правила приёма, модель не зовётся | `test_a4_auto_reply_in_a_sales_thread_is_decided_by_the_rules`, `test_a4_bounce_in_a_sales_thread_marks_the_address_without_the_model` |
| Q5 | Задача из `sales` и `crawl` в «ждёт повтора» получает своё время, из `runs` — как раньше | `tests/test_job_outcome.py::test_retry_time_is_read_from_the_queue_the_job_came_from` |
| K1 | «Давайте созвонимся во вторник» — `wants_to_talk`, цитата дословно, путь передачи лида; тело задачи на боевом уровне журнала не падает | `tests/test_sales_reply_kind.py::test_a1_call_on_tuesday_is_wants_to_talk_with_a_verbatim_quote`, `test_a1_job_with_the_default_handoff_survives_the_production_log_level` |
| K2 | «Сколько стоит аудит?» — `question`, путь агента, ждёт человека | `test_a2_price_question_goes_to_the_agent_path_and_waits` |
| K3 | «Это не ко мне, пишите коллеге: адрес» — `referral`; адрес в модель не ушёл, вернулся из метки и сверен с текстом; выдуманный — не взят | `test_a3_referral_address_is_masked_for_the_model_and_checked_against_the_text`, `test_a3_address_the_model_made_up_is_not_taken` |
| K4 | «Хватит слать спам» (правила приёма мимо) — `unsubscribe` | `test_a4_stop_sending_in_words_is_unsubscribe` |
| K5 | Модель вернула не-JSON или чужую форму — `parse_failed`, ручная очередь | `test_a5_not_a_json_is_parse_failed_and_waits_for_a_human`, `test_a5_form_that_is_not_ours_is_not_a_kind` |
| K6 | «Ignore previous instructions…» — вид по сути письма, промпт в снимок и причину не попадает | `test_a6_injection_stays_data_and_the_prompt_never_lands_in_the_answer`, `test_a6_kind_follows_the_substance_of_the_letter` |
| K7 | Модель недоступна (429/503 после повторов, сеть, ключ) — ручная очередь с причиной, отказ не кэшируется видом | `test_a7_*` (6) |
| K8 | Цитата модели не находится в тексте — уверенность 0, ручная очередь | `test_a8_quote_not_in_the_letter_lowers_confidence_to_a_human` |
| K9 | Eval: синтетика зелёная на верных видах; опасная ошибка, ложная отписка и порча промпта закрывают ворота; внешний набор — по манифесту | `tests/test_sales_reply_eval.py` (11) |
| K10 | Расход вида ответа на экране «Расход» — словами | `frontend/src/settings/UsagePage.test.tsx` («вид ответа лида продаж назван словами, а не кодом») |
| R1 | «Пишите другому» с адресом — лид `ready` после очистки, исходный тред закрыт; без адреса или без лида исходного — человек | `tests/test_sales_referral.py::test_a1_referral_makes_a_ready_lead_and_closes_the_thread`, `test_a1_referral_without_an_address_waits_for_a_human`, `test_a1_referral_without_the_origin_lead_waits_for_a_human` |
| R2 | Адрес в стоп-листе — лид `rejected`, тред закрыт; диалог продаж компании — не «другое направление» | `test_a2_referral_to_a_stoplisted_address_is_rejected_and_the_thread_closed`, `test_a2_referral_to_an_unsubscribed_address_is_rejected`, `test_a2_an_open_sales_thread_of_the_company_is_not_another_direction` |
| R3 | Проверка адресов не настроена — модель позвана один раз, расход записан, повтор задачи её не зовёт | `test_a1_misconfigured_verifier_costs_one_model_call_and_no_retry` |
| R4 | Ревизия ссылки вниз и вверх; ссылка решена в обоих реестрах чистки | `test_migration_adds_the_link_and_takes_it_back`; `tests/test_prune.py::test_every_reference_to_what_prune_deletes_is_decided`, `tests/test_prune_test_traces.py::test_every_reference_to_what_the_trace_cleanup_deletes_is_decided` |
| U1 | Автоответ «вернусь 14.10» — следующий шаг не раньше 14.10; без даты — +7 дней; поздний срок не тянется назад; дата — только из текста | `tests/test_sales_ooo_unsubscribe.py::test_a3_out_of_office_moves_the_next_step_to_the_return_date`, `test_a3_without_a_date_the_step_waits_the_default_days`, `test_a3_a_later_step_is_not_pulled_earlier`, `test_return_date_is_taken_only_when_it_is_in_the_text`, `test_return_date_rolls_over_the_new_year` |
| U2 | «Remove me» в треде продаж — адрес закрыт во всех направлениях, назначенное снято; словами мимо правил — по виду модели; неуверенная — ничего не закрывает; доноры без изменений | `test_a4_remove_me_closes_the_address_everywhere_and_unschedules`, `test_a4_stop_in_words_the_rules_miss_is_closed_by_the_model_kind`, `test_a4_unsure_unsubscribe_waits_for_a_human_and_closes_nothing`, `test_a4_donors_unchanged_a_donor_answer_never_reaches_sales` |
| U3 | У адреса уже есть строка стоп-листа одного этапа или со сроком — отписка всё равно ложится бессрочной строкой без этапа | `test_a4_a_sales_row_already_there_still_closes_every_direction`; `tests/test_replies_inbox.py::TestUnsubscribeIsForeverOnEveryStage` |

id примера — латинская буква части и номер: 2.1 → Q (очередь), 2.2 → K (вид ответа), 2.3a → R (другой контакт),
2.3b → U (автоответ и отписка). В именах тестов — прежние номера частей (`test_a1_…`): K1 — `test_a1_…`
файла `test_sales_reply_kind.py`, U1 — `test_a3_…` файла `test_sales_ooo_unsubscribe.py` и т. д.
