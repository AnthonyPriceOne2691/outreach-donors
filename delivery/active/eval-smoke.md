# Eval smoke — шов агента, часть «А2»

Derived from the agreed seam interface (05.10) and the agent phase plan (3.2, 3.3, 3.5). Run during verify.

- [x] Черновик из всей переписки: наши письма и ответ без цитаты, разобранная цена, подпись, промпт и модель этапа; статус `drafted`, `meta` пустая — `tests/test_agent_drafting.py::TestWho::test_writes_a_draft_from_the_whole_conversation`.
- [x] Не положен: не настроен, выключен, автоответчик, отписка — модель не зовётся; отвеченный ответ — без черновика — `TestWho::test_no_draft_where_nobody_will_answer, test_answered_reply_gets_no_draft`.
- [x] Повтор не платит, «заново» переписывает, сомнение — `escalated`, решённый не переписывается — `TestAgain::test_retry_does_not_pay_twice_but_again_rewrites_until_decided`.
- [x] Расход — операцией этапа; потолок — до модели — `TestAgain::test_spend_is_journaled_as_the_stage_operation, test_cap_stops_before_the_model`.
- [x] Пропуск брифа двух видов: модель не зовётся, текста нет, статус `skipped`/`escalated`, `meta` с видом пропуска, расхода нет — `TestBrief::test_skip_does_not_call_the_model_and_leaves_no_text[no_reply|human]`.
- [x] Факты брифа — у писателя, `meta` — в черновике (сумма строкой) — `TestBrief::test_facts_reach_the_writer_and_meta_lands_in_the_draft`.
- [x] «Всё же написать» поверх пропуска — пропуск в `meta` — `TestBrief::test_forced_draft_writes_over_the_skip_and_remembers_it`.
- [x] Крючок слышит готовый черновик, его сбой черновик не теряет; пропуск не объявляется — `TestAnnounce::*`.
- [x] Красный прогон (07.10 ночью, до переноса на main; код и тесты коммита А2 (`4d28f59`, на `-main` — `ed34806`) при переносах не менялись, `Brief.sign_as` — отдельным коммитом и отдельным красным прогоном): на `34ce1c5`: `tests/test_agent_drafting.py` не собирается (`ImportError: drafting`), exit 1.
- [x] Бриф назвал имя — черновик подписан им, а не общим; не назвал — общим, как раньше — `TestSignAs::test_draft_is_signed_by_the_brief_name_and_without_it_as_before[Ivo Test-Ivo Test|None-Anna]`.
- [x] `Brief.sign_as` (07.10 днём): красный прогон — `TestSignAs` на коде `ed34806` (поля нет): 2 failed, exit 1; мутант «писатель игнорирует `sign_as`» (поле есть, в запрос писателю — общее имя) — 1 failed (имя брифа), 1 passed (`None`), exit 1 — убит; второе прочтение — `writer.user_message` не кладёт `sign_as` в запрос модели — 2 failed, exit 1 — убит.
- [x] Пустое имя подписи — отказ словами, а не письмо без подписи — `TestSignAs::test_empty_name_is_refused_in_words[|  ]`.
- [x] Пустое имя подписи (07.10, перестановка на `58b9806`): `Brief(sign_as="")` и пробелы — `ValueError` «пустое имя подписи — задай имя или None»; красный прогон на `2a42af5` (проверки нет) — 2 failed, exit 1; мутант «проверка без `strip()`» — 1 failed (пробелы), 1 passed, exit 1 — убит.
- [ ] Живая модель и живое письмо — «заложено»: модель — подставной HTTP, почта — нулевой транспорт; стенд не поднимался.
