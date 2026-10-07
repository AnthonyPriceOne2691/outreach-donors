# Eval smoke — шов агента, часть «А3»

Derived from the agreed seam interface (05.10) and the agent phase plan (3.2, 3.3, 3.5). Run during verify.

- [x] 422 без причины и с пустой причиной; отклонение пишет причину, кто и событие журнала; второе решение и «отправить» после — 409 — `tests/test_agent_decisions.py::TestDecisions::test_reject_needs_a_reason_and_is_the_only_decision`.
- [x] «Как есть» у `escalated` — 409 словами; с правкой — уходит, `edited`, `final_body`, `sent_message_id` — `TestDecisions::test_escalated_draft_does_not_go_as_is_but_goes_edited`.
- [x] Готовый — как есть: ящик переписки, письмо-ответ с текстом черновика, `edited=False`, решил человек — `TestDecisions::test_ready_draft_goes_as_is_by_the_answer_path`.
- [x] Ответ из переписки мимо черновика закрывает его (`sent`, `edited`) — `TestDecisions::test_answer_from_the_thread_closes_the_draft`.
- [x] Список «ждут человека», черновик целиком с `meta`, 404, 403 без права send — `TestDecisions::test_waiting_list_and_detail_with_meta`.
- [x] Ревизия журнала ещё раз в процессе — значение одно — `test_journal_value_is_there_once_and_survives_a_rerun`.
- [x] Красный прогон (07.10 ночью, до переноса на main; код и тесты части при переносе не менялись): на `001d935`: 6 failed (маршрутов и значения журнала нет), exit 1.
- [ ] Живая модель и живое письмо — «заложено»: модель — подставной HTTP, почта — нулевой транспорт; стенд не поднимался.
