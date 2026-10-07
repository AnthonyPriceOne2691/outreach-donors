# Eval smoke — шов агента, часть «А1»

Derived from the agreed seam interface (05.10) and the agent phase plan (3.2, 3.3, 3.5). Run during verify.

- [x] Части первых двух этапов прежние (сторона цены, промпт, версия, модель, операция, бриф, судьи и автопилота нет) — `tests/test_agent_stages.py::TestFirstStagesUnchanged::test_registry_keeps_their_parts`.
- [x] Запрос к модели без брифа прежний (подставной HTTP `httpx.MockTransport`): модель, системный промпт, ключи без `facts` — `TestFirstStagesUnchanged::test_request_without_brief_is_what_it_was`.
- [x] Факты брифа, промпт и модель этапа доходят до модели — `test_brief_facts_stage_prompt_and_model_reach_the_model`.
- [x] Миграция вниз и вверх; тип `draftstatus` — пять значений по порядку — `test_drafts_migration_goes_down_and_up`.
- [x] Обе ссылки на удаляемое чисткой разобраны — `tests/test_prune.py::test_every_reference_to_what_prune_deletes_is_decided`.
- [x] Красный прогон (07.10 ночью, до переноса на main; при переносе к тестам добавлены только две строки `REVIEWED` в `tests/test_prune_test_traces.py` — их красный прогон отдельной строкой): на `7c28116`: `tests/test_agent_stages.py` не собирается (`ModuleNotFoundError: backend.features.agent.stages`); `test_schema` — 2 failed (нет `agent_drafts`), `test_prune` — 1 failed (разобраны ссылки, которых нет), `test_agent_settings::test_migration_goes_down_and_up` — failed (нет ревизии черновиков); итог 4 failed, 1 error, exit 1.
- [x] Обе ссылки на удаляемое разобраны и в чистке следов проверки (#192) — `tests/test_prune_test_traces.py::test_every_reference_to_what_the_trace_cleanup_deletes_is_decided`.
- [x] Красный прогон строк `REVIEWED` чистки следов (07.10 днём): `tests/test_prune_test_traces.py` части на коде `356251d` — 1 failed («больше нет: agent_drafts.reply_id → replies CASCADE, agent_drafts.sent_message_id → messages SET NULL»), exit 1; обратно — код части без строк: «не разобраны: …» те же две, exit 1.
- [ ] Живая модель и живое письмо — «заложено»: модель — подставной HTTP, почта — нулевой транспорт; стенд не поднимался.
