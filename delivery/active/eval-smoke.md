# Eval smoke — шов агента, части «Б»–«Д» одним PR

Derived from the agreed seam interface (05.10), the agent phase plan (3.2, 3.3, 3.5) and the neighbour session's
conditions (07.10: settings save, drafts cap). Run during verify.

## Б

- [x] B1 `block` → правка с причинами у писателя (ключ `rewrite`) → `allow`; попытки в `meta`; судья видит письмо собеседника — `tests/test_agent_guarding.py::TestLoop::test_block_goes_back_to_the_writer_and_allow_ends_it`.
- [x] B2 Петля стоит на `max_rewrites` и отдаёт человеку с причиной — `TestLoop::test_loop_stops_at_the_limit_and_goes_to_a_human`.
- [x] B3 `escalate` — человеку сразу — `TestLoop::test_escalate_goes_to_a_human_at_once`.
- [x] B4 Сомнение писателя судью не зовёт — `TestLoop::test_writer_in_doubt_does_not_call_the_judge`.
- [x] B5 Расход судьи — его операцией — `TestLoop::test_judge_spend_goes_under_its_operation`.
- [x] B6 Исключение, таймаут и `block` без причины — `escalated`, никогда не `drafted` — `TestClosedFailure::test_judge_failure_is_a_human_never_ready[3]`.
- [x] B7 Очистка: невидимые знаки и разметка ролей убраны, в `meta["cleaned"]` — `test_cleaning_reaches_the_brief_and_the_meta; tests/test_agent_cleaning.py (4)`.
- [x] Красный прогон (ночь, до переноса; код и тесты при переносе не менялись): на `d3480df` — `test_agent_cleaning.py` не собирается, `test_agent_guarding.py` — 8 failed, 1 passed (страховка), exit 1.

## В

- [x] V1 Переписка показывает черновик и `agent_writes` — `tests/test_agent_thread.py::TestScreen::test_thread_shows_the_draft_and_that_the_agent_writes`.
- [x] V2 «Написать заново» — 403 без права send; 409 «не настроен», 200 с текстом, 404 чужой переписке — `TestScreen::test_redraft_needs_the_send_right, test_redraft_writes_now_and_refusal_says_why`.
- [x] V3 «Всё же написать» поверх пропуска брифа — `TestScreen::test_force_writes_over_the_skip_of_the_brief`.
- [x] V4 Задача ставится одна на ответ и только где агент пишет; сбой очереди не роняет — `TestQueue::*`.
- [x] V5 Потолок и отказ ключа — итог задачи, сеть — повтор — `TestQueue::test_cap_and_key_are_the_job_outcome_and_network_is_a_retry[3]`.
- [x] V6 Потолок черновиков почти выбран: разбор ответа донора проходит, следующий черновик — отказ словами, модель не вызвана; разбор в счёт черновиков не идёт — `tests/test_agent_cap.py::test_spent_drafts_cap_lets_the_parse_go_and_refuses_the_draft_in_words`.
- [x] V7 Не задан — 30 % общего; общий 0 без своего — потолка нет; свой при общем 0 — действует — `tests/test_agent_cap.py::test_drafts_cap_is_the_setting_or_a_share_of_the_general_one[4]`.
- [x] Красный прогон: ночь — на `7b9388b` (`test_agent_thread.py` не собирается, `test_api_outreach.py` 3 failed); потолок — на `464f776`, 5 failed, exit 1; мутанты «свой по всем операциям», «0 — не нет потолка», «черновик без своего потолка» убиты.

## Г

- [x] G1 Суммы против предела по стороне цены, чья переписка и предел ответов — `tests/test_agent_autopilot.py::TestBounds::*`.
- [x] G2 Черновик в границах уходит путём человека — `TestFlight::test_draft_within_bounds_goes_out_by_the_human_path`.
- [x] G3 Сумма за пределом / без предела, отказ пути отправки, неуверенный разбор, переписка человека — человеку с причиной — `TestFlight::*`.
- [x] G4 Без флага этапа автопилота нет и при включённом сервере — `TestOff::test_no_stage_has_it_even_with_the_server_switch_on`.
- [x] G5 Выключенный сервер и режим черновиков почту не трогают — `TestOff::*`.
- [x] G6 Режим не сохранить без разрешения этапа и сервера (409); включает только право send (403) — `TestSwitch::*`.
- [x] G7 Ревизия вниз и вверх — `test_autopilot_migration_goes_down_and_up`.
- [x] G8 Сохранение без режима и предела их не меняет, и при снятом выключателе — `TestSwitch::test_save_without_mode_and_turns_keeps_them`.
- [x] G9 Присланное меняет только себя; явный `null` — 422 — `TestSwitch::test_sent_mode_or_turns_change_only_themselves`.
- [x] G10 Право send — по итоговому режиму — `TestSwitch::test_send_right_is_checked_on_the_mode_that_will_stand`.
- [x] G11 Экран отдаёт режим и предел обратно — `frontend/src/agent/agentDraft.test.ts`.
- [x] Красный прогон: ночь — на `008b3ba`; правка сохранения — на коде до правки: сервер 3 failed, экран 1 failed; мутанты «409 по итоговому режиму» и «null как не присланное» убиты.

## Д

- [x] D1 Тело задачи разбора на настоящей базе: черновик поставлен одной задачей там, где агент пишет, и не поставлен там, где нет — `tests/test_agent_after_parse.py::test_draft_is_queued_after_the_parse_only_where_the_agent_writes[True|False]`.
- [x] D2 Экран задач знает задачу черновика — `test_the_screen_names_the_draft_job`.
- [x] Красный прогон: ночь — на `5fc6446` (1 failed, 2 errors); мутант «вызова после разбора нет» убит.

## Не замерено

- [ ] Живая модель и живое письмо — «заложено»: модель — подставной HTTP, почта — нулевой транспорт; стенд не поднимался.
