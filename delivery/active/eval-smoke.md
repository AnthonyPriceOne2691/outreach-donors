# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 1.1b `sales-stage`, часть «в» (экран и сводка знают этап продаж), перенос на main `f9819da` (07.10).

- [x] A3 на экране — переписка продаж: шапка «кампания «Продажи» · продажи», состояние «ответ продаж — ждёт
      человека», строка «Ждёт человека: ответ продаж ждёт разбора: продажи к почте ещё не подключены.»; поля
      «Белая цена», кнопок «Взять в работу» и «Ответить» нет: `frontend/src/threads/ThreadPageSales.test.tsx`.
- [x] A3 через API — список диалогов отдаёт `sales` / `sales_pending`, ответ — `needs_review` с причиной сервера,
      `lead=false`: `tests/test_sales_stage_screens.py::test_dialogs_show_a_sales_thread_with_its_own_state_and_reason`;
      главная — продажи не в ценах, не в лидах и не в числах доноров, сводка писем по этапу продаж — своя.
- [x] Значения экрана = коды сервера: `Stage` (`stages.ts`) = `Stage`, `LetterStage` = `MailStage`, `ThreadState` =
      `ThreadState`, `THREAD_STATES` и `THREAD_STAGE_NOTES` — все значения: `test_front_types_are_the_server_codes`,
      `test_front_labels_name_every_stage_and_thread_state`.
- [x] Журнал сборки называет каждый этап словами: `test_audit_names_every_stage_in_words` (3), `test_audit_calls_sales_by_name`.
- [x] Отбор прогона: донорские ответы продаже не наследуются, как рекламодателю.
- [x] Донор после #193: пометки «· продажи» и «Ждёт человека:» нет, «Ответить» есть, у поля ответа — пояснение «с того же
      ящика, что вёл переписку»: `ThreadPageSales.test.tsx` — «у донора пометки этапа нет, а пояснение у поля ответа на месте»;
      мутанты (условие ответа, донор как продажи, пометка у донора) — 3 из 3 убиты.
- [x] Новые тесты на коде до изменений — красные: на main pytest не собирается (нет `backend.features.core.stages`);
      прежний прогон vitest на main — экран падает `TypeError` на незнакомом состоянии (ветка `sales/1.1b-stage`).
- [x] Обратные прогоны переписанных мест — журнал без продаж, `Stage` без `sales`, `LetterStage` с `sales`, пометка
      без продаж — 4 из 4 убиты (тест сверки, tsc, vitest); остальное — прежняя таблица (8 из 8).
- [ ] Живой экран диалога продаж (контраст обеих тем, 1440 и 390) — «заложено»: диалогов продаж нет, пока почта
      им отказывает; новые элементы — текст прежних стилей.
