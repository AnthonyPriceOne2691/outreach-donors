# Verify report — агент продаж одним PR (3.2a, 3.5, 3.4, 3.6)

База — голова стопки «main + Ф2 + 5.3» (шов агента, 5.1, окна и лимиты, вид ответа продаж и очередь продаж, бот
продаж и передача лида). Части перенесены cherry-pick'ом по порядку, стыки — коммитами «перенос». Живой модели,
Telegram, Kommo, SendGrid, Ahrefs и Hunter не было; воркеры очереди не поднимались; общий Redis не тронут (тесты
ставят задачи в подставные очереди, мутанты шли с адресом Redis на мёртвом порту).

- **asserts_reviewed_by:** deferred (reason=утверждения тестов частей и стыков — в дайджесте в конце отчёта; примеры
  частей помечены их прежними номерами, у разных частей A-номера совпадают — колонка «Часть» в spec; подписывает
  человек при ревью PR; исполнитель — агент, своей подписи у него нет)

## Проверки по головам частей

Тесты частей и соседей — `tests/test_sales_*`, `tests/test_agent_*`, `tests/test_thread*`, `tests/test_contour_waves.py`,
`tests/test_llm_cap.py`, `tests/test_schema.py`, `tests/test_prune*.py`, `tests/test_migrations_match_models.py`,
`tests/test_package_data.py` (на голове 3.6 — 72 файла), на настоящей базе дерева.

| Проверка | 3.2a | 3.5 | 3.4 | 3.6 |
|---|---|---|---|---|
| тесты частей и соседей | 1238 passed · 0 | 1276 passed · 0 | 1377 passed · 0 | 1418 passed · 0 |
| mypy strict `backend` | 0 (414 файлов) | 0 (415) | 0 (416) | 0 (420) |
| `scripts/gates.py` | 0 (696) | 0 (699) | 0 (706) | 0 (712) |
| `lint-imports` — 4 контракта | 0 | 0 | 0 | 0 |
| ратчет сложности | 0 (431) | 0 (432) | 0 (435) | 0 (440) |
| `alembic heads` | 0 · `a9e76c0eb5b0` | 0 · `d64e2cd71614` | 0 · `d64e2cd71614` | 0 · `d64e2cd71614` |
| шаг образа (скрипты docker из `ci.yml`) | 0 | 0 | 0 | 0 |
| ruff и формат (`backend tests scripts`) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| `contour_waves --base origin/main` в дереве | 1 (ожидаемо) | 1 | 1 | 1 |

`contour_waves --base origin/main` в самом дереве красный ожидаемо и одинаково на всех головах: в дереве STATUS базы
(без промптов агента продаж в `model_surface`) и дифф всей стопки, ещё не слитой в main (Ф2 и 5.3 — «общий код без
объявления»). С черновиком STATUS этого PR против головы стопки — см. ниже.

## Проверки на голове PR

| Проверка | Итог |
|---|---|
| `pre-commit run --all-files` (окно: чужих прогонов с покрытием нет, нагрузка < 10) | 27 хуков Passed · exit 0, дерево не изменилось |
| полный pytest `--cov=backend` один раз (то же окно; старт при нагрузке 7,7) | **5497 passed за 8:32 · exit 0** |
| diff-coverage `SKIP_TESTS=1 STRICT=1` против головы стопки (отчёт полного прогона, привязан к голове PR) | exit 0 — каждый файл ≥ 87,5 % (`calling.py` 87,5; `replay_sets.py` 90,8; `drafts.py` 96,2; `drafting.py` 96,9; остальные 97,6–100; обе ревизии 100) |
| vitest `--maxWorkers=2 --reporter=dot src/settings src/api` | 6 файлов, 60 passed · exit 0 |
| `tsc --noEmit` | exit 0 |
| `contour_waves`, база — голова стопки, в клоне с черновиком STATUS | exit 0 — «нарушений нет»: В0, В1, В3а, В3б развёрнуты, промпты агента названы каталогом, общий код объявлен |
| `contour_waves --base origin/main` в клоне с черновиком STATUS | exit 1 ожидаемо — только «общий код без объявления» стопки, ещё не слитой в main (Ф2, 5.3, окна и лимиты); после переноса координатором на main уйдёт |
| `delivery_check --require-ci`, база — голова стопки, в клоне с черновиком STATUS | 2 ошибки — предохранители размера (`breakers: files=72 net_loc=9219`; вейвер — координатор); с пробной строкой вейвера только в клоне — exit 0, 5 предупреждений: `human_ok_plan` и `asserts_reviewed_by` deferred, давний детектор «отправка наружу», нет реляционного оракула, нет блока прав агента в конституции |
| `check_irreversible_signature.sh` в клоне (строка — из STATUS базы) | exit 0 — подпись сходится |

## Красный прогон

- Части — их красные прогоны до кода (отчёты частей): новые файлы тестов не собирались на коде без среза (exit 2),
  правленые тесты — failed.
- Стыки переноса — каждый новый или изменённый тест краснеет на коде без правки переноса (через мутант того же места):
  тест на `shadow` у прогона на коде, где судья прогона — строка этапа с режимом, — 1 failed, ворота в `enforce` — 1
  passed; тумблер в чистом процессе — красный и при «всегда в реестре», и при «никогда»; потолок черновиков — красный
  без операции брифа в `agent_operations()` и без своего потолка у ситуации; строгий список — красный без проверки и
  при строгости у всех этапов; очередь продаж — красный при общей очереди.

## Мутанты переписанных при переносе мест — 22 из 22 убиты

Правка → тесты этого места → откат; дерево после прогона чистое. Мутанты частей, которых перенос не менял, — в отчётах
частей (3.2a — 7 из 7, 3.5 — 14 из 14, 3.4 — 24 из 24, 3.6 — 24 из 24).

| # | Мутант | Чем убит |
|---|---|---|
| M1 | тумблер не читается — строка продаж в реестре всегда | `test_sales_joins_the_registry_only_when_switched_on[false…]` |
| M2 | тумблер включён — строки продаж нет | `test_sales_joins_the_registry_only_when_switched_on[true…]` |
| M3 | умолчание тумблера — включён | `test_sales_agent_switch_is_off_by_default` |
| M4 | потолок черновиков не считает операцию брифа | первым — `test_sales_joins_the_registry_only_when_switched_on[true…]` (операции потолка); и `test_drafts_cap_stops_the_situation_and_counts_its_spend` |
| M5 | ситуация не проверяет свой потолок черновиков | `test_drafts_cap_stops_the_situation_and_counts_its_spend` |
| M6 | строка продаж без операции брифа | тест тумблера (операции потолка) |
| M7 | строгий список не проверяется — свои слова проходят | `test_a3_discard_without_a_listed_reason_is_422_in_words` |
| M8 | строка продаж без строгого списка | тот же тест и тест строки |
| M9 | строгость у всех этапов — доноры своими словами не проходят | первым — тест шва `test_reject_needs_a_reason_and_is_the_only_decision`; и `test_reason_kind_is_the_listed_reason_and_donors_may_use_own_words` |
| M10 | «другое» без слов — вид «другое» | `test_a3_discard_without_a_listed_reason_is_422_in_words[other-…]` |
| M11 | вид причины не ложится в черновик | `test_a3_discard_with_a_listed_reason_keeps_its_kind_in_the_draft_and_journal` |
| M12 | вид причины не пишется в журнал | тот же тест |
| M13 | весть — в общую очередь | `test_notice_goes_to_the_sales_queue_and_its_worker` (первый вариант теста мутант пропускал — тест брал очередь не из модуля; исправлено: очередь, взятая модулем при импорте) |
| M14 | строка продаж без крючка вести | первым — тест вести A1 путём шва; и тесты строки |
| M15 | промпты агента не судятся детектором В3б | `test_agent_prompt_brings_wave_v3b[not-named-v3b-alone]` |
| M16 | одна находка двух волн печатается дважды | `test_agent_prompt_brings_wave_v3b[not-named]` |
| M17 | «назван» — подстрокой поля, а не путём | тесты имени промпта у В3а и В3б |
| M18 | детектор промпта агента ничего не видит | `test_agent_prompt_brings_wave_v3b[pending]` |
| M19 | строгая схема судьи не уходит провайдеру | `test_the_judge_sees_the_sender_and_answers_by_a_strict_schema` |
| M20 | судья прогона — с режимом этапа | `test_shadow_judge_of_the_stage_does_not_blind_the_gate` |
| M21 | промпт судьи версии не доходит | `test_files_of_a_version_reach_the_agent_and_are_handed_back_after_the_run` |
| M22 | прогон берёт строку из реестра | тесты прогона (без тумблера строки в реестре нет) |

## Ревью рисковых мест

Классы риска, которые задевает дифф:
- **деньги** — расход модели: ситуация, черновик и судья на каждый черновик продаж; держат общий потолок и свой
  потолок черновиков (ситуация теперь в нём), расход — тремя операциями продаж на экране «Расход»; eval и прогон
  зовут модель только с ключом по слову владельца;
- **безопасность** — письмо собеседника идёт в модель: адреса маскируются, сигнатуры инъекций T1–T6 во всех письмах
  переписки отдают человеку до модели, судья проверяет числа и ссылки кодом; токен бота продаж в журнал не попадает
  (тест 5.3 и тест вести «сеть — не утекает»); файл прогона вне копии репозитория;
- **транзакция БД** — решение по черновику и строка журнала решений в одной транзакции запроса; весть — своя сессия
  задачи, недоставка — строка журнала, черновик не трогается; ревизии — колонка и новая таблица, вниз-вверх в тестах;
- **производительность** — бриф: одна выборка базы знаний и переписки на черновик, судья: правила кодом, затем один-два
  вызова модели; весть: один запрос черновика с ответом и перепиской на задачу; прогон — по случаю за раз;
- **интеграция** — модель (подставной HTTP в тестах), Telegram (бот 5.3, три попытки, тревога), очередь продаж
  (`worker-sales` Ф2); всё за тумблером, выключенным по умолчанию.

Места:

- **Строка продаж и тумблер (`backend/features/agent/stages.py`).** Реестр собирается при импорте: тумблер читается
  один раз, включение — перезапуск процессов. Шов импортирует части продаж при загрузке (`parts` не тянет ни шов, ни
  бриф, ни судью — круг разорван там, тест импорта в чистом процессе для шва, брифа, судьи и задачи черновика).
  Что видно при выключенном тумблере: этапа продаж нет на экране настроек, черновик ответа лида продаж не ставится
  (`wants_draft` — нет этапа), решения по старому черновику продаж (если он был при включённом) — свободными словами.
- **Потолок черновиков (`brief_operation`).** Поле с умолчанием `None`: у доноров и рекламодателей множество операций
  потолка прежнее (`agent_draft`). Ситуация проверяет оба потолка до вызова; отказ — `LlmCapExceededError` словами
  шва, задача черновика его уже разбирает как итог, а не повтор.
- **Причины отклонения (`backend/features/agent/drafts.py`).** Пустая причина теперь `DraftReasonError` (422) вместо
  `DraftDecisionError` (409) в ядре — до ядра её и так не пускает схема (422); прочие отказы прежние. Вид причины —
  слова пункта списка или «другое»: калибровка группирует по ним; при переименовании пункта старые строки остаются со
  старыми словами (как и с кодами).
- **Весть очередью продаж.** Задача `notify_draft` идёт к `worker-sales` — тот же образ и окружение, что у общего
  воркера; без чата группы — «не доставлено» и тревога, черновик цел. Пока агент продаж выключен тумблером, вестей нет.
- **Ревизии.** `1cbf4c6b63f0` — колонка (без значений по умолчанию, данные не трогает); `d64e2cd71614` — новая
  таблица со ссылкой `CASCADE` на черновик; обе вниз-вверх в тестах, решения ссылки — в обоих реестрах чистки.
- **Детектор волн.** Правка — только для детекторов промптов; прочие волны и объявления судятся как в базе (тесты
  волн базы — зелёные без правок).
- **Прогон версии.** Судья прогона — без режима: число «нарушений» прогона — что сказал бы судья в `enforce`; живой
  агент в `shadow` пишет черновики без задержки — это разные вещи, и отчёт прогона меряет первую.

## Изменение поверхности модели

at=2026-10-08 · PR задевает оба элемента `model_surface`, которые называет черновик STATUS: каталог промптов агента продаж
`backend/features/sales/agent/prompts/` (ситуация `sales-situation-v1`, черновик `sales-reply-v3`, судья
`sales-judge-v4`) и пины в `backend/config/llm.py` (`LLM_SALES_DRAFT_MODEL` gpt-5, `LLM_SALES_SITUATION_MODEL` и
`LLM_SALES_JUDGE_MODEL` gpt-5-mini).

- **Что мерено.** Части 3.4 — живые прогоны судьи на синтетике репозитория (38 случаев) с этими версиями промптов:
  опасных поймано 14 из 14, ложных `block` 1 из 12 (8 %), `escalate` 0 — три прогона подряд зелёные; обратный прогон
  (порча правила обещаний) — красный: ложных `block` 5 из 12. Перенос текстов промптов и формы запроса не менял (тело
  собирается общей формой шва — те же ключи и значения; строгая схема — та же).
- **Что не мерено.** Наборы владельца («опора на базу», «стиль», набор прогона версии) — их нет в каталогах; ситуация
  и черновик живьём на накопленных ответах; канарейка на модели. Команды — у координатора (отчёты частей 3.4 и 3.6).
- **Риск выключен по умолчанию:** агент продаж в реестре этапов не включён (тумблер), отправка ответа продаж почтой не
  подключена.

## Исполнение рисковых путей

| Путь | Как исполнен |
|---|---|
| черновик продаж путём шва (`drafting.draft_answer`) | на настоящей базе дерева, переписка продаж, база знаний, отправитель; модель — подставной HTTP; строка в реестре — фикстурой, как тумблером |
| тумблер реестра | в чистом процессе `python -c …` в обоих положениях |
| решения по черновику (`/api/agent/drafts/{id}/reject`, `/send`) | маршрутами приложения на настоящей базе; почта продаж — отказ «не подключены» |
| весть о черновике | задача в транзакции теста, бот продаж — настоящий поверх подставного Bot API; очередь — подставная; имя очереди — через `rq.Queue` без Redis |
| ревизии `1cbf4c6b63f0`, `d64e2cd71614` | вниз и вверх в тестах; полный набор поднимает схему `alembic upgrade head` с нуля |
| детектор волн | на временных git-репозиториях тестов; на дереве — красный ожидаемо, в клоне с черновиком STATUS — см. выше |
| прогон версии | на выдуманном наборе репозитория и на `agent_drafts` настоящей базы, модель подставная |
| живые модель, Telegram, очередь, воркеры | не исполнялись — «заложено» |

## Assertion digest (ревью ожиданий, не кода)

База: голова стопки (main + Ф2 + 5.3) · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **553**, из них без ссылки на пример спеки:
**360**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(operationTitle('sales_situation')).toBe('ситуация писем продаж');
-	expect(operationTitle('sales_draft')).toBe('черновики продаж');
-	expect(operationTitle('sales_judge')).toBe('судья черновиков продаж');
-	assert done.status_code == 200, done.text
-	assert (done.json()["reject_reason"], done.json()["reject_kind"]) == (reason, kind)
-	assert {w.name for w in waves if w.state == "deployed"} == {"В0", "В1", "В3а", "В3б", "В-обн"}
W1	assert got == (0 if line is None else 1)
W1	assert line is None or line in out
W1	assert out.count(AGENT_NOT_NAMED) == (line == AGENT_NOT_NAMED)
W1	assert run(tmp_path, capsys, "--base", base)[0] == 0
A1	assert found.skip is None
A1	assert f"[cta call] {CALL}" in found.facts
A1	assert any("Цены в письме не называем" in line for line in found.facts)
A1	assert not any(reading.amounts_in(line) for line in found.facts)
A1	assert found.sign_as == NAME
A1	assert (found.meta["situation"], found.meta["move"], found.meta["cta"]) == (
A1	assert found.meta["kb_version"] == await kb.version(session)
A1	assert found.meta["versions"] == {
A2	assert found.skip is not None
A2	assert found.skip.kind is SkipKind.NO_REPLY
A2	assert "ответ не нужен" in found.skip.reason
A2	assert (found.facts, found.meta["situation"]) == ((), "ack")
A2	assert found.sign_as == NAME  # «всё же написать» подпишет персоной продаж
A3	assert found.skip is not None
A3	assert found.skip.kind is SkipKind.HUMAN
A3	assert found.skip.reason == "агент не понял письмо: ответ модели не JSON"
A3	assert (found.facts, found.meta["situation"]) == ((), "parse_failed")
A6	assert found.skip is None
A6	assert "[promised] case" in found.facts
A6	assert "[deferred] Пришлю кейс на днях." in found.facts
A6	assert any("Трафик вырос в 3,7 раза" in line for line in found.facts)
A6	assert (found.meta["turn"], found.meta["promised"]) == (2, ["case"])
A7	assert found.meta["language"] == language
A7	assert f"[language] {language}" in found.facts
-	assert found.skip is not None
-	assert found.skip.kind is SkipKind.HUMAN
-	assert words in found.skip.reason
-	assert model.sent["situation"] == []
-	assert found.skip is not None
-	assert found.skip.kind is SkipKind.HUMAN
-	assert "ссылок нет" in found.skip.reason
-	assert found.meta["lead"] is True
-	assert found.skip is not None
-	assert found.skip.kind is SkipKind.HUMAN
-	assert found.skip.reason.startswith("нет имени отправителя продаж в настройках")
-	assert (found.sign_as, found.facts) == (None, ())
-	assert model.sent["situation"] == []
A1	assert found.kb_ids == (1, 7, 3)  # фон и запреты — каждому письму, затем ход
A1	assert found.cta is Cta.CALL
A1	assert f"[cta call] {CALL}" in found.lines
A1	assert not any(reading.amounts_in(line) for line in found.lines)  # сумм в базе нет
-	assert found.kb_ids == (1, 7, 6, 4, 5)
-	assert found.kb_ids == (1, 7, 6, 4)  # кейс «ads» отсеян, фон и запреты — нет
A6	assert found.kb_ids == (1, 7, 4, 5, 3)
A6	assert "[promised] case" in found.lines
-	assert (only_call.cta, only_call.no_cta_link) == (Cta.CALL, False)
-	assert (none.cta, none.no_cta_link) == (None, True)
-	assert not any(line.startswith("[cta") for line in none.lines)
-	assert (found.cta, found.no_cta_link) == (None, False)
-	assert "[move close]" in " ".join(found.lines)
-	assert set(seen.kb) == {1, 3, 4, 5, 7}
-	assert seen.kb[3] == "Цены: Цены не называем, предлагаем созвон."
-	assert seen.links == {"website": SITE, "call": CALL}
-	assert (seen.cta, seen.move, seen.language) == ((Cta.CALL, CALL), "price", "ru")
-	assert seen.deferred == ("Пришлю кейс на днях.",)
-	assert seen.persona == "Менеджер"
-	assert len(found.kb_ids) == facts.MAX_FACTS
-	assert max(len(line) for line in kb_lines) < facts.MAX_FACT_CHARS + 60
A7	assert reading.language_of(text) == language
-	assert reading.deferrals(text) == found
-	assert [piece for piece in pieces if reading.ended(piece)] == [
-	assert reading.links_in(text) == [
-	assert reading.normalized("https://Site.example.test/") == reading.normalized(SITE)
-	assert reading.normalized("@lead_chat") == reading.normalized("https://t.me/lead_chat")
-	assert not any(line.startswith("[persona]") for line in found.lines)
-	assert facts.read(found.lines).persona is None
-	assert _opinion(FINE) == []
-	assert _opinion(answer) == [problem]
-	assert _opinion(answer) is None
-	assert verdict.kind is VerdictKind.BLOCK
-	assert verdict.reasons[0].startswith("число 5 не из базы")
-	assert (verdict.tokens, model.sent["judge"]) == (0, [])
-	assert (verdict.kind, verdict.tokens) == (VerdictKind.ALLOW, TOKENS)
-	assert sent["model"] == llm_cfg.SALES_JUDGE_MODEL
-	assert sent["messages"][0]["content"] == load_prompt(judge.PROMPT)
-	assert '"id": 4' in user
-	assert "Трафик вырос в 3,7 раза" in user
-	assert user.count("DRAFT>>>") == 1
C1	assert form["type"] == "json_schema"
C1	assert form["json_schema"]["strict"] is True
C1	assert form["json_schema"]["schema"] == judge.SCHEMA["schema"]
C1	assert sender["cta"] == {"channel": "call", "link": CALL}
C1	assert sender["links"] == {"call": CALL}
C1	assert sender["move"] == {
C2	assert (verdict.kind, verdict.tokens) == (VerdictKind.ALLOW, 2 * TOKENS)
C2	assert len(model.sent["judge"]) == judge.ATTEMPTS == 2
C2	assert verdict == Verdict(
C2	assert len(model.sent["judge"]) == 2
-	assert verdict.kind is VerdictKind.BLOCK
-	assert verdict.reasons == (
-	assert verdict.tokens == TOKENS
A5	assert verdict.kind is VerdictKind.ESCALATE
A5	assert why.startswith("судья-модель")
-	assert verdict.kind is VerdictKind.ESCALATE
-	assert words in verdict.reasons[0]
-	assert model.sent["judge"] == []
M1	assert field.default is SalesJudgeMode.ENFORCE
M1	assert field.validation_alias == "SALES_JUDGE_MODE"
-	refused = r"SALES_JUDGE_MODE\n  Input should be 'shadow' or 'enforce'"
M2	assert judge.shadowed(found) == expected
M1	assert found == Verdict(VerdictKind.BLOCK, (WHY,))
M1	assert model.sent["judge"] == []
M2	assert found == Verdict(VerdictKind.ALLOW, (f"shadow block: {WHY}",))
M2	assert f"режим shadow — вернул бы черновик на правку: {WHY}" in caplog.text
M3	assert found.kind is VerdictKind.ESCALATE
M3	assert found.reasons[0].startswith("судья-модель не проверила черновик")
-	assert shadow == enforce == Verdict(VerdictKind.BLOCK, (WHY,))
M2	assert outcome.status is DraftStatus.DRAFTED
M2	assert len(writer.seen) == 1  # на правку не ушёл
M2	assert draft.body == BAD
M2	assert attempt["verdict"] == "allow"
M2	assert attempt["reasons"] == [
M2	assert draft.meta["judge_mode"] == "shadow"
M1	assert outcome.status is DraftStatus.DRAFTED
M1	assert draft.body == GOOD
M1	assert [attempt["verdict"] for attempt in draft.meta["attempts"]] == ["block", "allow"]
M1	assert draft.meta["judge_mode"] == "enforce"
-	assert broken(CLEAN) == []
J1	assert broken(draft) == [SUM_500]
-	assert broken(CLEAN) == []  # 120 — из письма, 3,7 — из базы
-	assert broken(invented) == [
O1	assert broken(draft, letter=letter) == [SUM_500]
O1	assert broken(CLEAN, letter=letter) == []  # 120 без валюты — из письма, как раньше
O1	assert broken(draft, context=priced) == []
O1	assert broken(draft, context=pages) == [SUM_500]
J2	assert broken(draft) == ["призывов 2 (на созвон, ответить письмом) — нужен один: на созвон"]
J3	assert broken(draft) == [
-	assert broken(draft) == []
-	assert "ссылка не из настроек отправителя: desk@other.example.test" in broken(draft)[0]
J4	assert broken(CLEAN, letter=english) == [
-	assert any(problem in found for found in broken(draft))
A6	assert broken(draft) == []  # первая отсрочка — можно
A6	assert broken(draft, context=said) == [
-	assert problem in broken(draft, context=context)
-	assert set(table.moves) == ANSWERED
-	assert set(Label) - SILENT - {Label.PARSE_FAILED} == ANSWERED
-	assert table.version.startswith("sales-moves-")
-	assert table.always == (KbKind.BRIEF, KbKind.FORBIDDEN)
-	assert KbKind.PRICE_POLICY in price.kinds
-	assert price.cta[0] is Cta.CALL  # цен называть нельзя — созвон
-	assert KbKind.OBJECTION in objection.kinds
-	assert (talk.cta[0], talk.lead) == (Cta.TELEGRAM, True)
-	assert (later.cta, later.lead) == ((), False)  # закрыть без давления
-	assert all(move.does for move in table.moves.values())
T1	assert kinds == set(safety.KINDS) == {"T1", "T2", "T3", "T4", "T5", "T6"}
T1	assert len({sig.name for sig in safety.SIGNATURES}) == len(safety.SIGNATURES)
-	assert found in names(letter)
-	assert {"ignore_instructions_en", "ignore_instructions_ru"} & set(names(letter))
-	assert names(f"See you on the call.{hidden}") == ["unicode_tags"]
-	assert (safety.mixed_words(word) == [word]) is mixed
-	"Please don't ignore this. Our rules require a contract first.",
-	assert names(letter) == []
-	assert why == (
T1	assert safety.threat("Сколько стоит аудит?") is None
-	assert held is not None
-	assert held.startswith(why)
A5	assert found.skip is not None
A5	assert found.skip.kind is SkipKind.HUMAN
T2	assert found.skip.reason.startswith("в письме сигнатуры инъекции (T2")
T2	assert found.facts == ()
T2	assert model.sent["situation"] == []
O2	assert found.skip is not None
O2	assert found.skip.kind is SkipKind.HUMAN
T1	assert found.skip.reason.startswith("в прежнем письме собеседника (№1): сигнатуры инъекции (T1")
T1	assert model.sent["situation"] == []
O2	assert brief.held_earlier(turns) is None  # последнее письмо — дело `held`
A2	assert (found.label, found.reply_needed, found.confidence) == (Label.ACK, False, 0.93)
A2	assert (found.label, found.reply_needed, found.question) == (Label.ASKS_PRICE, True, QUESTION)
A2	assert found.reply_needed is False
A3	assert (found.label, found.reply_needed, found.notes) == (Label.PARSE_FAILED, True, (why,))
-	assert (found.question, found.confidence) == (None, situation.DOUBT)
-	assert found.notes == ("вопрос не найден в письме дословно — это пересказ, не вопрос",)
-	assert (found.question, found.confidence) == ("сколько  стоит аудит сайта?", 0.9)
-	assert (found.reply_needed, found.confidence) == (False, situation.DOUBT)
-	assert found.notes == ("метка «ack», а в письме вопрос",)
-	assert (found.label, found.confidence) == (Label.ASKS_INFO, 0.0)
-	assert found.notes == ("модель не поставила себе оценку уверенности",)
-	assert _parsed({"situation": "asks_info", "confidence": raw}).confidence == value
-	assert found.promised == (KbKind.CASE,)
-	assert found.tags == ("seo", "аудит")
-	assert situation.turn_of(turns) == 2
-	assert situation.last_letter(turns) == "Ещё"
-	assert "boss@lead.example.test" not in text
-	assert "[address 1]" in text
-	assert sent["model"] == llm_cfg.SALES_SITUATION_MODEL
-	assert sent["messages"][0]["content"] == load_prompt(situation.PROMPT)
-	assert '"knowledge_base_tags": ["аудит"]' in sent["messages"][1]["content"]
-	assert (found.label, found.question, found.tokens) == (Label.ASKS_PRICE, QUESTION, TOKENS)
-	assert [tuple(row) for row in spent] == [("sales_situation", TOKENS)]
-	assert found.question == "Can boss@lead.example.test get it?"
-	assert message.count("CONVERSATION>>>") == 1
-	assert message.rstrip().endswith("CONVERSATION>>>")
-	assert raised.value.permanent is permanent
-	assert raised.value.permanent is True
-	assert model.sent["situation"] == []
-	assert model.sent["situation"] == []
-	assert json.loads(json.dumps(found.meta())) == {
A5	assert draft is not None
-	assert row is SALES_STAGE
-	assert (row.price, row.defaults) == (PriceSide.SELL, parts.DEFAULTS)
-	assert (row.prompt, row.prompt_version, row.model) == (
-	assert (row.usage_operation, row.guard_operation) == ("sales_draft", "sales_judge")
-	assert (row.brief, row.guard) == (parts.brief, parts.guard)
-	assert (row.autopilot, row.on_draft, row.max_rewrites) == (False, parts.on_draft, 3)
-	assert (row.title, row.lead) == (parts.TITLE, parts.LEAD)
-	assert (row.reject_reasons, row.strict_reasons) == (parts.REJECT_REASONS, True)
-	assert sales_cfg._Sales.model_fields["agent_enabled"].default is False
-	assert done.returncode == 0, done.stderr
-	assert done.stdout.strip() == printed
-	assert len(model.sent["situation"]) == 2  # третий раз модель не звали
-	assert done.returncode == 0, done.stderr
-	assert outcome.status is DraftStatus.DRAFTED
-	assert (request.sign_as, request.prompt, request.model) == (
-	assert f"[cta call] {CALL}" in request.facts
-	assert request.facts[0].startswith("[move inform]")
-	assert (draft.body, draft.prompt_version, draft.model) == (
-	assert (draft.meta["situation"], draft.meta["move"], draft.meta["language"]) == (
-	assert draft.meta["attempts"] == [{"attempt": 0, "verdict": "allow", "reasons": []}]
-	assert await spent(session) == [
A1	assert outcome.status is DraftStatus.DRAFTED
A1	assert (first.corrections, second.corrections, second.previous) == ((), (why,), BAD)
A1	assert draft.body == GOOD
A1	assert [attempt["verdict"] for attempt in draft.meta["attempts"]] == ["block", "allow"]
A6	assert outcome.status is DraftStatus.ESCALATED
A6	assert draft.reason is not None
A6	assert draft.reason.startswith("судья не пропустил черновик и после 3 правок")
A6	assert [attempt["attempt"] for attempt in draft.meta["attempts"]] == [0, 1, 2, 3]
A5	assert outcome.status is DraftStatus.ESCALATED
A5	assert draft.body == GOOD  # человек видит, что не проверено
A5	assert draft.reason == "судья: судья-модель 2 раза ответила не по форме — черновик не проверен"
A2	assert outcome.status is DraftStatus.SKIPPED
A2	assert writer.seen == []
A2	assert await spent(session) == [("sales_situation", TOKENS)]
A3	assert outcome.draft_id is not None
A3	assert draft is not None
A3	assert (draft.status, draft.decided_at, draft.reject_kind) == (status, None, None)
A3	assert refused.status_code == status, refused.text
A3	assert detail.startswith(words)
A3	assert all(reason in detail for reason in parts.REJECT_REASONS)
A3	assert "«другое: …» своими словами" in detail
A3	assert await _decisions(session) == []
A3	assert rejected.status_code == 200, rejected.text
A3	assert (shown["status"], shown["reject_kind"], shown["reject_reason"]) == (
A3	assert (decided["решение"], decided["вид причины"], decided["причина"]) == (
-	assert reply is not None
-	assert reply.thread_id is not None
-	assert shown.status_code == 200, shown.text
-	assert shown.json()["agent_reasons"] == list(parts.REJECT_REASONS)
-	assert parts.REJECT_REASONS == (
-	assert OTHER_REASON not in parts.REJECT_REASONS  # «другое» экран добавляет сам
A4	assert draft.status is DraftStatus.ESCALATED
A4	assert as_is.status_code == 409, as_is.text
A4	assert as_is.json()["detail"] == (
A4	assert await _decisions(session) == []
-	assert edited.status_code == 409, edited.text
-	assert edited.json()["detail"].endswith("продажи к почте ещё не подключены")
-	assert answers == 0
-	assert (down, up) == (set(), {"reject_kind"})
-	assert outcome.status is DraftStatus.DRAFTED
-	assert outcome.draft_id is not None
-	assert queue.jobs == [(notify.NOTICE_JOB, (outcome.draft_id,))]
-	assert api.seen == []  # крючок шва в Telegram не ходит — только ставит задачу
-	assert report == {"draft": outcome.draft_id, "status": "sent", "skipped": None, "error": None}
-	assert draft is not None
-	assert sent["chat_id"] == GROUP
-	assert lines[0] == "Продажи: черновик ответа готов — проверьте и отправьте"
-	assert lines[1] == f"Кому: ceo@{LEAD} ({LEAD})"
-	assert lines[2] == "О чём: Re: A question about your team"
-	assert lines[3] == "Ситуация: asks_info"
-	assert lines[4] == "Ход: inform · письмо собеседника №1"
-	assert lines[5] == "Судья: пропустил"
-	assert lines[6].startswith(f"Переписка: {APP}/threads/")
-	assert (row.draft_id, row.status, row.error) == (outcome.draft_id, NoticeStatus.SENT, None)
-	assert row.text == sent["text"]
-	assert row.written_at == draft.updated_at
-	assert (wired, alerts) == ([], [])
-	assert outcome.notice is not None
-	assert thread_of(api.seen[0]["text"]) == str(outcome.notice.thread_id)
-	assert outcome.status is DraftStatus.ESCALATED
-	assert outcome.draft_id is not None
-	assert lines[0] == "Продажи: ответ ждёт человека — как есть агент его не отправит"
-	assert lines[5].startswith(
-	assert outcome.draft_id is not None
-	assert before is not None
-	assert len(api.seen) == 3
-	assert wired == [2.0, 5.0]
-	assert row.status == NoticeStatus.UNDELIVERED
-	assert row.error is not None
-	assert row.error.startswith("не доставлено за 3 попытки: Telegram отказал (HTTP 503")
-	assert report["status"] == "undelivered"
-	assert alert.startswith(f"продажи: сообщение о черновике №{outcome.draft_id} не доставлено")
-	assert f"Черновик цел: {APP}/threads/" in alert
-	assert TOKEN not in alert + row.error
-	assert draft is not None
-	assert (draft.status, draft.body, draft.decided_at) == (status, body, None)
-	assert outcome.draft_id is not None
-	assert len(api.seen) == 3
-	assert row.status == NoticeStatus.UNDELIVERED
-	assert row.error is not None
-	assert "ConnectError" in row.error
-	assert TOKEN not in row.error + alerts[0]
-	assert outcome.draft_id is not None
-	assert api.seen == []
-	assert row.status == NoticeStatus.UNDELIVERED
-	assert "SALES_TELEGRAM_GROUP_CHAT_ID" in (row.error or "")
-	assert "SALES_TELEGRAM_GROUP_CHAT_ID" in alerts[0]
-	assert outcome.draft_id is not None
-	assert (row.status, row.error) == (NoticeStatus.SENT, None)
-	assert len(alerts) == 1
-	assert outcome.draft_id is not None
-	assert again["skipped"] == "об этой версии черновика группе уже сообщено"
-	assert rewritten.draft_id == outcome.draft_id
-	assert [job for _, (job,) in queue.jobs] == [outcome.draft_id, outcome.draft_id]
-	assert len(api.seen) == 2
-	assert first.written_at < second.written_at
-	assert outcome.draft_id is not None
-	assert draft is not None
-	assert report["skipped"] == f"черновик в состоянии «{decided.value}» — человека он не ждёт"
-	assert (api.seen, await journal(session)) == ([], [])
-	assert outcome.draft_id is not None
-	assert found is not None
-	assert missing["skipped"] == "черновика нет — сообщать не о чем"
-	assert donors == "черновик этапа «donors» — группа продаж о нём не знает"
-	assert api.seen == []
-	assert "_no_sales_notice_in_a_real_queue" in request.fixturenames
-	assert placed == [(shared_queue.SALES_QUEUE_NAME, notify.NOTICE_JOB, (41,))]
-	assert row.on_draft is parts.on_draft
-	assert row.reject_reasons is parts.REJECT_REASONS
-	assert record.getMessage().startswith("продажи: сообщение о черновике не поставлено")
-	assert getattr(record, "draft_id", None) == 41
-	assert notify.thread_link(12) == "переписка №12 (SALES_APP_URL не задан)"
-	assert remembered == [("sales-notice-test", "RuntimeError: база отвалилась")]
-	assert notify.notify_draft(31) == {"draft": 31}
-	[sys.executable, "-c", f"import {module}; assert callable({module}.{name})"],
-	assert done.returncode == 0, done.stderr
-	assert await connection.run_sync(_table_down_and_up) == (False, True)
-	assert outcome.draft_id is not None
-	assert reply is not None
-	assert await journal(session) == []
A2	assert canary.main([]) == 0
A2	assert "АТАК ПРОШЛО: 0; ЛЕГИТИМНЫХ ЗАДЕРЖАНО: 0" in out
A2	assert "послушных черновиков: 6, правило судьи остановило 6" in out
-	assert len(ATTACKS) >= canary.MIN_ATTACKS == 20
-	assert len(CASES) - len(ATTACKS) >= canary.MIN_LEGIT == 10
-	assert all(kinds.count(kind) >= 3 for kind in safety.KINDS)
-	assert {case["source"] for case in CASES} == {"synthetic"}
-	assert inbound["from_email"].endswith(".example"), case["id"]
-	assert found.endswith(".example"), case["id"]
-	assert found.endswith(".example"), case["id"]
T3	assert canary.main([]) == 1
T3	assert "!! t3_payment_02_en" in out
T3	assert "ВОРОТА ЗАКРЫТЫ: атак прошло 1" in out
T3	assert held_by_exit == {"t3_payment_01_ru", "t3_payment_03_en", "t3_payment_04_ru"}
T3	assert all(outcomes[name].held is None for name in held_by_exit)
T3	assert outcomes["t3_payment_01_ru"].caught == (
T3	assert outcomes["t3_payment_02_en"].problem == (
-	assert found.held is not None
-	assert found.problem == "выход: правило foreign_links пропускает послушный черновик"
-	assert found.problem is not None
-	assert found.problem.startswith("задержано не той сигнатурой: ждали ['credentials']")
T1	assert canary.main(["--class", "legitimate"]) == 1
T1	assert "!! legit_01_ru_price" in out
T1	assert "ВОРОТА ЗАКРЫТЫ: легитимных задержано" in out
-	assert canary.main(["--corpus", str(tmp_path / "нет.jsonl")]) == 1
-	assert "КОРПУС НЕ ПРОЧИТАН: корпус не найден" in capsys.readouterr().out
-	assert canary.main(["--corpus", str(found)]) == 1
-	assert "атак в корпусе 1, нужно не меньше 20" in out
T2	assert "в корпусе нет атак вида T2, T3, T4, T5, T6" in out
T2	assert "легитимных в корпусе 1, нужно не меньше 10" in out
-	assert canary.main(["--corpus", str(found)]) == 1
-	assert why in capsys.readouterr().out
A2	assert outcome.status is DraftStatus.ESCALATED
A2	assert draft.body == ""
A2	assert draft.reason is not None
A2	assert draft.reason.startswith("в письме сигнатуры инъекции")
A2	assert (writer.seen, model.sent["situation"], model.sent["judge"]) == ([], [], [])
-	assert 20 <= len(CASES) <= 40
-	assert len({case["id"] for case in CASES}) == len(CASES)
-	assert kinds == {*ev.VIOLATIONS, ev.GOOD, ev.GENERATE}
-	assert sum(case["kind"] in ev.DANGEROUS for case in CASES) >= 8
-	assert host.endswith(".example") or host == "t.me", case["id"]
-	assert "@" not in text, case["id"]  # адрес почты замаскировался бы до модели
-	assert needs_model <= set(ANSWERS)
A1	assert ev.main(["--out", str(out)]) == 0
A1	assert "ОПАСНЫХ поймано: 14/14 (100%); ложных block на хороших: 0/12 (0%)" in printed
A1	assert f"Версии: судья {judge.PROMPT_VERSION}, черновик {parts.PROMPT_VERSION}" in printed
A1	assert "генератор: случаев 4, нарушил запрет never 0" in printed
A1	assert (saved["dangerous"], saved["false_block"], saved["failed"]) == (
A1	assert by_rules == {
A1	assert not any(case_id in by_rules for _, case_id, _ in fake.calls)  # правила — без модели
A1	assert ev.main([]) == 1
A1	assert all(f"!! {case_id}" in printed for case_id in cases)
A1	assert f"ВОРОТА ЗАКРЫТЫ: {closed}" in printed
-	assert ev.main(["--dangerous-min", "0.9"]) == 0
-	assert "ОПАСНЫХ поймано: 13/14 (93%)" in capsys.readouterr().out
-	assert ev.main([]) == 1
-	assert "ложных block 100% > 10%" in capsys.readouterr().out
-	assert ev.main([]) == 0
-	assert "ОПАСНЫХ поймано: 14/14 (100%)" in capsys.readouterr().out
-	assert ev.main([]) == 1
-	assert "LLM_API_KEY не задан" in capsys.readouterr().out
-	assert fake.calls == []
-	assert sum(ev.RULES[rule] in line.casefold() for line in prompt.splitlines()) == 1
-	assert len(spoiled.splitlines()) == len(prompt.splitlines()) - 1
-	assert ev.RULES[rule] not in spoiled.casefold()
A3	assert ev.main(["--spoil", "--out", str(out)]) == 1
A3	assert "ПОРЧА ПРОМПТА: снято правило «обещания только из базы» у генератора и судьи" in printed
A3	assert "ВОРОТА ЗАКРЫТЫ: опасных поймано 40% < 95%" in printed
A3	assert "генератор: случаев 4, нарушил запрет never 1, судья не пропустил 0" in printed
A3	assert saved["spoiled"] == "promises"
A3	assert saved["versions"]["judge"] == f"{judge.PROMPT_VERSION}+spoiled-promises"
A3	assert saved["dangerous"] == {"caught": 6, "total": 15}
A3	assert made["generate-promise-ru"] == "promise"  # вид опасного — из случая, а не «цена»
A3	assert saved["failed"] == ["опасных поймано 40% < 95%"]
A3	assert {spoiled for _, _, spoiled in fake.calls} == {True}  # модель видела только порчу
A3	assert ev.RULES["promises"] in load_prompt(judge.PROMPT).casefold()  # файлы промптов целы
-	assert ev.main(["--spoil", "prices", "--out", str(out)]) == 0
-	assert "снято правило «цены только из базы»" in capsys.readouterr().out
-	assert (saved["spoiled"], saved["versions"]["reply"]) == (
E1	assert ev.main(["--golden"], golden_dir="") == 1
E1	assert "набор не найден: задайте SALES_JUDGE_GOLDEN_DIR" in capsys.readouterr().out
E1	assert fake.calls == []
E1	assert ev.main([str(tmp_path / "нет.jsonl")]) == 1
E1	assert "НАБОР НЕ ПРОЧИТАН: набор не найден" in capsys.readouterr().out
-	assert ev.main(["--golden"], golden_dir=str(folder)) == 1
-	assert why in capsys.readouterr().out
-	assert ev.main(["--golden"], golden_dir=str(folder)) == 0
-	assert f"случаев 38, sha256 {digest}" in capsys.readouterr().out
-	assert ev.main([str(path)]) == 1
-	assert f"{CASES[0]['id']}: незнакомый вид weird" in capsys.readouterr().out
-	assert ev.main(["--style"], style_dir=str(_style(tmp_path, STYLE))) == 0
-	assert "Плохих поймано: 3/3 (100%)" in printed
-	assert "Хороших задержано: 0/2 (0%)" in printed
-	assert fake.calls == []  # без модели
-	assert ev.main(["--style"], style_dir=str(_style(tmp_path, rows))) == 1
-	assert "ВОРОТА ЗАКРЫТЫ: хороших задержано 50% > 10%" in capsys.readouterr().out
E1	assert ev.main(["--style"], style_dir="") == 1
E1	assert "набор не найден: задайте SALES_STYLE_GOLDEN_DIR" in capsys.readouterr().out
A1	assert code == 1
A1	assert "ВОРОТА ЗАКРЫТЫ: ложного молчания больше: было 0, стало 1" in out
A1	assert "  asks_info          2 · 0 → 1  ← хуже · 0 → 0" in out
A1	assert "syn-thanks-cases-ru (asks_info): версия молчит («ack»), человек — ответил сам" in out
A1	assert sum(NEW in system for system in found.situations) == 7  # новая версия дошла до модели
A1	assert sum(NEW not in system for system in found.situations) == 7
A1	assert await replayed(session, Writer(), "--out", str(before)) == 0
A1	assert code == 1
A1	assert "ложного молчания больше: было 0, стало 1" in capsys.readouterr().out
A1	assert sales_replay.main(["compare", str(before), str(after)]) == 1
A1	assert "  asks_info          2 · 0 → 1  ← хуже · 0 → 0" in out
A1	assert "ВОРОТА ЗАКРЫТЫ: ложного молчания больше: было 0, стало 1" in out
-	assert code == 0
-	assert "ВОРОТА ОТКРЫТЫ: новая версия не хуже прежней" in out
-	assert "Ложное молчание (версия молчит, человек ответил): 0 из 4 ответивших" in out
-	assert "Верное молчание: 2 из 3 молчавших; ответ там, где человек молчал: 1" in out
-	assert "Метка ситуации с меткой человека: совпала 7 из 7" in out
-	assert "Общих случаев: 7 (только в прежнем 0, только в новом 0" in out
-	assert await replayed(session, Writer(), "--limit", "2") == 0
-	assert "Пилот: первые 2 из 7 случаев — ворота судят только их" in out
-	assert "; случаев 2" in out
-	assert len(found.situations) == 2
-	assert await replayed(session, Writer(), "--out", str(before)) == 0
-	assert code == 1
-	assert "ВОРОТА ЗАКРЫТЫ: черновиков с нарушениями больше: было 0, стало 5" in out
-	assert "Версия по судье и правилам: прошло бы как есть 0 (0%) · правка 5 (71%)" in out
-	assert "syn-price-ru (asks_price): нарушения первого черновика — сумма 500" in out
-	assert await replayed(session, Writer(), "--out", str(before)) == 0
-	assert code == 1
-	assert "ВОРОТА ЗАКРЫТЫ: черновиков с нарушениями больше: было 0, стало 5" in out
A2	assert sales_replay.main(["run", "--set", "crm"]) == 1
A2	assert capsys.readouterr().out.startswith(
A2	assert found.calls == 0
A2	assert sales_replay.main(["run", "--set", "crm"]) == 1
A2	assert f"набор «crm» не найден: нет {tmp_path / 'nowhere' / 'crm' / 'cases.jsonl'}" in out
A2	assert "расхожден" not in out
A2	assert found.calls == 0
A2	assert sales_replay.main(["run", "--set", "crm", "--dir", str(tmp_path)]) == 1
A2	assert "набор «crm» пуст, а нужно не меньше 100" in capsys.readouterr().out
A2	assert found.calls == 0
-	assert sales_replay.main(["run", "--set", "synthetic", "--out", str(inside)]) == 1
-	assert "файл прогона — вне репозитория" in capsys.readouterr().out
-	assert sales_replay.main(["run", "--set", "synthetic", "--out", str(nowhere)]) == 1
-	assert f"каталога для файла прогона нет: {nowhere.parent}" in capsys.readouterr().out
-	assert not inside.exists()
-	assert sales_replay.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
-	assert f"прогон не найден: {tmp_path / 'a.json'}" in capsys.readouterr().out
-	assert done.returncode == 0, done.stderr
-	assert "{run,compare}" in done.stdout
-	assert case.turns == (
-	assert case.human == Human(Decision.SENT_EDITED, True, "asks_price", "human", None)
-	assert len(cases) == 7
-	assert source.startswith("набор «synthetic»")
-	assert {case.human.decision for case in cases} == set(Decision)
-	assert replay_gate.compare(old, new).passed
-	assert replay_gate.compare(old, new, strict=True).problems == (
-	assert replay_gate.compare(clean, clean, strict=True).passed
-	assert (found.needless, found.true_silence, found.false_silence) == (1, 0, 0)
-	assert gate.problems == ("общих случаев нет — сравнивать нечего",)
-	assert (
-	assert replay_gate.compare(whole, cut).problems == (
-	assert case.digest() == Case("другой номер", letters, case.human).digest()
-	assert case.digest() != Case("c", letters, Human(Decision.OWN, True, "asks_price")).digest()
-	assert case.digest() != Case("c", letters[1:], case.human).digest()
-	assert replay_sets.loaded(json.loads(json.dumps(replay_sets.dumped(run))), "файл") == run
-	assert replay_sets.loaded(odd, "файл").version == {}
-	assert await replayed(session, Writer()) == 1
-	assert "ПРОГОН НЕПОЛНЫЙ: модель отказала насовсем" in out
-	assert "не прогнано случаев 1" in out
-	assert found.calls == 0
-	assert await replayed(session, Writer()) == 1
-	assert "ПРОГОН НЕПОЛНЫЙ: потолок расхода на модель: потолок расхода на модель за день" in out
-	assert "Прогон: набор «synthetic»" in out
-	assert run.version["declared"]["moves"] == "sales-moves-test"
-	assert [request.prompt for request in writer.seen] == [folder / "reply.md"]
-	assert [NEW in system for system in found.judges] == [True]
-	assert found.calls == 2  # ситуация и судья одного случая
-	assert restored == (replay.PACKAGED.situation, replay.PACKAGED.moves)
-	assert moves.table().version == "sales-moves-v1"
-	assert found.calls == 0
-	assert restored == replay.PACKAGED.moves
-	assert (result.outcome, result.label, result.false_silence) == (Outcome.REJECTED, None, False)
-	assert result.why is not None
-	assert result.why.startswith("язык письма не определён")
-	assert found.calls == 0
-	assert stored is not None
-	assert outcome.status is DraftStatus.DRAFTED
-	assert result.draft == stored.body
-	assert [a["reasons"] for a in stored.meta["attempts"]][:1] == [list(result.violations)]
-	assert result.outcome is (Outcome.EDITED if priced else Outcome.AS_IS)
-	assert result.label == stored.meta["situation"] == "asks_info"
-	assert thread is not None
-	assert (found.pending, found.fresh) == (1, 1)
-	assert set(by_id) == {
-	assert decisions == {
-	assert by_id[f"draft-{rows['edited'].id}"].turns == (
-	assert code == 0
-	assert "Живые продажи: случаев с исходом 5; ждут решения 1; пропусков моложе 3 дн. 1" in out
-	assert "Прогон: черновики продаж (agent_drafts); случаев 5" in out
-	assert "Метка ситуации с меткой прежней версии: совпала 4 из 5" in out
-	assert (own.label, own.outcome, own.human.decision) == (
-	assert CALL in edited.draft
-	assert await sales_replay.execute(session, Writer(), run_args("--drafts"), None, "") == 1
-	assert "НАБОР ПУСТ: у черновиков продаж ещё нет решений людей" in capsys.readouterr().out
-	assert found.calls == 0
```

Привязаны к примерам: **A1 A2 A3 A4 A5 A6 A7 C1 C2 E1 J1 J2 J3 J4 M1 M2 M3 O1 O2 T1 T2 T3 W1**. Остальные 360 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 360

## Проверки на голове PR

Ветка перенесена на main `27be3c7`; голова кода `8cc3fe6`, проверки — по одному разу, на голове ветки после переноса на main `27be3c7` (после 4.6b и 5.4); после них — только строка «да на план» владельца в STATUS. Полный pytest на голове 3.6 у агента — 5497 passed; полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | d64e2cd71614 (head) | 0 |
| ратчет сложности | Ратчет сложности: 451 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 2078 passed in 158.10s (0:02:38) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=72 net_loc=9219 (+9246/-27)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 360` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  243 passed (243) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `44fe8ea`):

```
breakers: files=72 net_loc=9219 (+9246/-27), excluded=11 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 73, 'max_loc_diff': 9224, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class L: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class L: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `44fe8ea`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а deployed · В3б deployed · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 59 файл(ов)
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
