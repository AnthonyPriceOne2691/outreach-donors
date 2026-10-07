# Eval smoke — this shipment

Derived from spec acceptance. Run during verify. Поставка — срез 4.6a `sales-chain` (цепочка писем продаж в базе и экран правки, часть 1 среза 4.6).

- [x] C1 — тема первого письма «Re:», «RE:», «Re[2]:», «Fwd:», «FW:», «Отв:», «ответ:» → отказ словами
      «…выдаёт первое письмо за ответ или пересылку: это обман адресата…», в базе ничего; «Regarding …»,
      «Ответ на ваш вопрос», «Fwding tips» проходят: `tests/test_sales_chain.py::test_first_letter_subject_never_pretends_to_be_a_reply`
      (×5), `::test_russian_reply_prefix_is_refused_too` (×2), `::test_subject_that_only_starts_like_a_prefix_is_fine` (×3),
      `tests/test_sales_chain_api.py::test_refusals_come_in_words_and_nothing_is_written`,
      `tests/test_sales_chain_cli.py::test_every_bad_template_is_named_by_its_number`; живьём — API 400 («Re:», «FW:»),
      консоль — «№1 (шаг 1, en): тема «Re: stand question» начинается с «Re:»…», экран — «Не сохранили» с теми же словами.
- [x] C2 — метрика Ahrefs в теме или теле → отказ словами общей проверки:
      `test_ahrefs_metrics_never_get_into_the_template` (×4, тело и тема); живьём — «DR 45» в файле и «Domain Rating» через API.
- [x] C3 — незнакомая или битая подстановка → отказ «подстановки {{…}} нет; есть только {{name}}, {{company}}, {{site}}»:
      `test_unknown_or_broken_placeholder_is_refused_in_words` (×7), `test_unknown_placeholder_in_the_subject_is_refused_too`;
      живьём — `{{first_name}}` в файле и через API.
- [x] C4 — подпись или адрес зоной, подстановкой или текстом из «Отправителя» → отказ «…допишет сборка из настроек
      отправителя»; предпросмотр — подпись и адрес из настроек на своём месте:
      `test_signature_and_address_placeholders_are_left_to_the_assembly` (×5), `…zones_are_left_to_the_assembly` (×2),
      `test_settings_signature_or_address_inside_the_body_is_refused` (×2), `test_save_refuses_the_signature_written_in_the_sender_settings`,
      `test_preview_fills_made_up_values_and_takes_signature_and_address_from_the_settings`, API `test_preview_*` (×3),
      консоль `test_the_sender_signature_inside_a_template_is_named`, `test_template_with_the_sender_signature_is_refused_by_the_console`;
      живьём — подпись стенда в теле — 400, зона `[signature]` — 400, предпросмотр показал подпись и «Физический адрес не задан».
- [x] C5 — правка текста → версия сменилась, откат вернул прежнюю; порядок и выключенные шаги версию не меняют; журнал —
      прежние значения и версии до и после: `test_version_is_chain_and_twelve_hex_digits_of_sha256`,
      `test_order_and_switched_off_steps_do_not_shape_the_version`, `test_each_field_of_a_step_changes_the_version` (×4),
      `test_edit_changes_the_version_and_reverting_the_edit_returns_it`, `test_new_step_writes_the_author_and_one_journal_line_with_both_versions`,
      `test_change_journals_only_the_changed_fields_with_their_old_values`, API `test_editing_the_text_on_the_screen_changes_the_chain_version`;
      живьём — chain-b190… → chain-76ef… → откат chain-b190…, цепочка версий журнала сходится (10 строк за прогон).
- [x] C6 — набор гипотезы: свой включённый шаг — своя цепочка целиком, только выключенные свои — общая, своих нет — общая;
      шаги наборов не смешиваются: `test_common_chain_serves_a_hypothesis_without_its_own_steps`,
      `test_own_chain_replaces_the_common_one_whole_steps_never_mix`, `test_switched_off_own_steps_leave_the_hypothesis_on_the_common_chain`,
      `test_own_chain_is_chosen_per_language`, API `test_hypothesis_uses_the_common_chain_until_it_has_its_own_steps`,
      консоль `test_hypothesis_set_goes_to_the_named_hypothesis`, vitest «набор гипотезы — свой запрос…»; живьём — гипотеза А
      (свой EN-шаг) — `own`, неполна; гипотеза Б — `common`.
- [x] C7 — пустая или неполная цепочка → «цепочка не задана» / «цепочка неполна» и чего нет; `check_ready` — отказ словами;
      умолчаний-текстов нет: `test_empty_chain_refuses_in_words_there_is_no_default_text`, `test_switched_off_step_is_not_in_the_chain`,
      API `test_empty_set_says_the_chain_is_not_set_in_each_language`, vitest «пустой набор — у каждого языка «цепочка не задана»…»;
      живьём — выключение первого письма RU на экране → «цепочка не задана», включение → «неполна».
- [x] C8 — загрузка файлом: `tests/test_sales_chain_cli.py` (21): первая загрузка — журнал одной записью, повтор — без журнала,
      отличие без `--update` — «не тронуто» и номер, `--update` — перезапись с журналом, `--dry-run` — ничего, набор гипотезы,
      незнакомая гипотеза — ничего, плохой шаблон — ничего и номер, файл в копии репозитория — отказ до чтения, Ctrl-C;
      живьём — 10 сценариев консоли на своей базе (verify-report).
- [x] C9 — без права `sales` → 403 словами на каждом маршруте: `test_without_the_sales_right_every_route_refuses_in_words` (×3),
      таблица маршрутов сверена `test_the_table_covers_every_route_of_the_chain`; живьём — учётка оператора с `{"sales": false}` —
      403 «Действие «sales» недоступно этой учётке» на трёх маршрутах.
- [x] C10 — в репозитории ни одного коммерческого текста: тесты и каталог замеров — выдуманные заготовки («Hello {{name}}, test
      body», «Made-up letter for the contrast check»); `scripts/gates.py` (в т. ч. `public-repo`) — exit 0; grep по добавленным строкам
      и сообщениям коммитов — чисто (verify-report).
- [x] C11 — обе темы, 1440 и 390: контраст трёх экранов — все точки «ок» (14 + 8 + 17 в каждой теме), рамка 1440/1440 и 390/390
      во всех состояниях, окно 780 и 351, наведение — 39 элементов вкладки и 4 кнопки окна без сдвига, фокус виден (verify-report).
- [x] Коды сервера = подписи экрана (шаги, языки, виды зон, подстановки): `tests/test_sales_chain_screen.py` (4).
- [x] Новые тесты на main без кода среза — красные: pytest — 4 файла не собрались, `test_schema.py` — 2 падения; vitest — 12 из 12.
- [x] Обратные прогоны — 10 мутантов (8 сервера, 2 экрана) — 10 из 10 убиты (verify-report).
- [ ] Настоящие тексты писем в базе — «заложено»: наполнение файлом через консоль вне репозитория.
- [ ] Версия цепочки в письме и `check_ready` в сборке — «заложено» (4.6b).
