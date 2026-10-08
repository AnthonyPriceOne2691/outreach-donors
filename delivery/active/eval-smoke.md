# Eval smoke — агент продаж одним PR (3.2a, 3.5, 3.4, 3.6)

Примеры Spec одного PR → тесты. Модель ситуации и судьи — подставной HTTP (`httpx.MockTransport`), писатель —
подставной, Bot API — подставной; всё, что уходит в базу, — на настоящей базе дерева. Живой модели нет.

## 3.2 — ситуация → ход → черновик

- [x] **A1** вопрос о цене при «цены не называем» — ход `price`, без сумм, ссылка на созвон, подпись персоной —
  `tests/test_sales_agent_brief.py::test_a1_price_question_with_no_prices_named_gets_the_call_link_and_no_sum`,
  `tests/test_sales_agent_facts.py::test_price_move_takes_the_policy_and_the_call_link`.
- [x] **A2** «спасибо, получил» и автоответ — без ответа, флаг модели не читается —
  `tests/test_sales_agent_situation.py::test_a2_*`, `tests/test_sales_agent_brief.py::test_a2_thanks_needs_no_reply_and_gives_no_facts`;
  путём шва — `skipped`, писатель не звался, расход только ситуации —
  `tests/test_sales_agent_stage.py::test_thanks_costs_only_the_situation`.
- [x] **A3** не-JSON ситуации — `parse_failed`, человеку —
  `tests/test_sales_agent_situation.py::test_a3_unreadable_answer_is_parse_failed`,
  `tests/test_sales_agent_brief.py::test_a3_unreadable_situation_goes_to_a_human`.
- [x] **A6** обещали кейс — факт кейса и прежняя отсрочка строкой; повтор отсрочки — `block` —
  `tests/test_sales_agent_brief.py::test_a6_promised_case_comes_as_a_fact_with_our_earlier_deferral`,
  `tests/test_sales_agent_judge_rules.py::test_deferral_already_said_in_the_thread_is_not_repeated`.
- [x] **A7** язык письма в `meta` — `tests/test_sales_agent_brief.py::test_a7_language_of_the_letter_is_in_meta`.

## 3.3 — судья

- [x] **J1** сумма не из базы — `block`; путём шва правка доходит до писателя —
  `tests/test_sales_agent_judge_rules.py::test_j1_*`,
  `tests/test_sales_agent_stage.py::test_sum_not_in_the_base_goes_back_to_the_writer_and_comes_out_without_it`.
- [x] **J2** два призыва — `tests/test_sales_agent_judge_rules.py::test_j2_two_calls_to_action_are_blocked`.
- [x] **J3** чужая ссылка или адрес — `tests/test_sales_agent_judge_rules.py::test_j3_*`.
- [x] **J4** язык черновика — `tests/test_sales_agent_judge_rules.py::test_j4_draft_not_in_the_letters_language_is_blocked`.
- [x] Не смог проверить — человеку; три правки — человеку с историей —
  `tests/test_sales_agent_judge.py::test_judge_that_could_not_check_does_not_pass`,
  `tests/test_sales_agent_stage.py::test_judge_that_could_not_check_hands_the_draft_to_a_human`,
  `tests/test_sales_agent_stage.py::test_three_failed_rewrites_go_to_a_human_with_the_attempts`.

## 3.4 — волна В3б

- [x] **A1** eval судьи: доли и ворота — `tests/test_sales_judge_eval.py::test_a1_*`.
- [x] **A2** канарейка — `tests/test_sales_injection_canary.py` (атаки не прошли, легитимные прошли).
- [x] **A3** порча — eval красный, обратный прогон файлом —
  `tests/test_sales_judge_eval.py::test_a3_spoiled_prompts_turn_the_eval_red_and_the_reverse_run_is_written`.
- [x] **M1–M3** режим судьи — `tests/test_sales_agent_judge_mode.py`.
- [x] **E1** набора нет — `tests/test_sales_judge_eval.py` (метка `E1`); **E2** атака путём шва —
  `tests/test_sales_injection_canary.py::test_attack_from_the_corpus_gets_no_draft_and_no_model`.
- [x] **W1** волна В3б — `tests/test_contour_waves.py::test_agent_prompt_brings_wave_v3b`,
  `tests/test_contour_waves.py::test_reverse_run_without_the_agent_prompt_detector_v3b_is_silent`.
- [x] **O1** сумма с валютой из письма — `tests/test_sales_agent_judge_rules.py` (метка `O1`); **O2** инъекция в
  прежнем письме — `tests/test_sales_agent_safety.py` (метка `O2`).
- [x] **C1** отправитель и строгая схема —
  `tests/test_sales_agent_judge.py::test_the_judge_sees_the_sender_and_answers_by_a_strict_schema`; **C2** один
  повтор — `tests/test_sales_agent_judge.py` (метка `C2`).

## 3.5 — решения и весть

- [x] **A1** новый черновик — сообщение со ссылкой и строка «отправлено» —
  `tests/test_sales_draft_notify.py::test_a1_new_draft_is_announced_with_a_link_and_a_sent_row`.
- [x] **A2** Telegram недоступен — 3 попытки, тревога, черновик цел —
  `tests/test_sales_draft_notify.py::test_a2_telegram_down_three_attempts_then_ops_alert_and_the_draft_stays`.
- [x] **A3** отклонить без причины из списка — 422 словами —
  `tests/test_sales_draft_decisions.py::test_a3_discard_without_a_listed_reason_is_422_in_words` (5 случаев),
  `::test_a3_discard_with_a_listed_reason_keeps_its_kind_in_the_draft_and_journal` (3 случая).
- [x] **A4** «как есть» у отданного человеку — 409 —
  `tests/test_sales_draft_decisions.py::test_a4_send_as_is_of_an_escalated_sales_draft_is_409_in_words`.

## 3.6 — прогон версии

- [x] **A1** хуже по ложному молчанию — exit 1 с разбором — `tests/test_sales_replay.py` (метка `A1`).
- [x] **A2** набора нет — exit 1 «набор не найден» — `tests/test_sales_replay.py` (метка `A2`).

## Стыки переноса

- [x] **T1** тумблер `SALES_AGENT_ENABLED`: выключен — строки продаж в реестре нет, включён — есть, и потолок
  черновиков считает её операции — `tests/test_sales_agent_stage.py::test_sales_joins_the_registry_only_when_switched_on`
  (чистый процесс, оба положения), `::test_sales_agent_switch_is_off_by_default`.
- [x] **K1** свой потолок черновиков останавливает ситуацию и считает её расход —
  `tests/test_sales_agent_stage.py::test_drafts_cap_stops_the_situation_and_counts_its_spend`.
- [x] **P1** причина пунктом списка и «другое: …» — вид в черновике и журнале; у доноров свои слова — без вида —
  `tests/test_sales_draft_decisions.py::test_a3_discard_with_a_listed_reason_keeps_its_kind_in_the_draft_and_journal`,
  `tests/test_agent_decisions.py::test_reason_kind_is_the_listed_reason_and_donors_may_use_own_words`;
  плашке — список продаж в переписке — `tests/test_sales_draft_decisions.py::test_thread_offers_the_sales_reasons_to_the_banner`.
- [x] **Q1** весть — в очередь продаж — `tests/test_sales_draft_notify.py::test_notice_goes_to_the_sales_queue_and_its_worker`.
- [x] **R1** прогон в `shadow` — ворота закрыты, как в `enforce` —
  `tests/test_sales_replay.py::test_shadow_judge_of_the_stage_does_not_blind_the_gate`.

## Не замерено

- [ ] Живая модель: ситуация, черновик, судья на наборах владельца; канарейка на модели; прогон версии с ключом —
  шаг координатора по слову владельца. Числа калибровки судьи на синтетике (части 3.4) — в verify-report.
- [ ] Живой Telegram (бот продаж в группе, `SALES_APP_URL`) — после включения агента продаж.
