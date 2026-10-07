# Verify report

**Поставка:** шов агента переписки по этапам, части «Б», «В», «Г» и «Д» одним PR — судья с петлёй правки и очистка
переписки; черновик задачей и по кнопке и свой дневной потолок черновиков; автопилот (выключен); постановка
черновика после разбора.

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов частей — в дайджесте в конце отчёта; подписывает ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `c7ccc8f` — голова PR (голова части Д), ветка `sales/3.0-agent-seam-0710` на main `9ee3d9e` (#210; не
запушена); база PR — голова А3 `a636d11`. Головы частей: Б `4de09d6`; В `8eacb5a` (`464f776` + потолок); Г `ad53ad9`
(`c5c2ea1` + правка сохранения); Д `c7ccc8f`. Правка координатора в А3 (маршрут ответа; PR #214, голова `bd77ba7`)
в этой ветке не стоит — сводит координатор при сборке: пробная накладка частей на `bd77ba7` конфликтует в
`backend/api/threads/routes.py` (`settle_sent` в `try` против `Decider.of(author)`) и снимке сложности.

## Shape oracles (на головах частей)

| Проверка | Б `4de09d6` | В `8eacb5a` | Г `ad53ad9` | Д `c7ccc8f` |
|---|---|---|---|---|
| `mypy backend` (strict) | 0 | 0 | 0 | 0 |
| `scripts/gates.py` | 0 | 0 | 0 | 0 |
| `scripts/complexity.py` | 0 | 0 | 0 | 0 |
| шаг образа (`docker_step_local.py`: промпты и шаблоны внутри образа) | 0 | 0 | 0 | 0 |
| `alembic heads` (одна) | `3924977db911` | `3924977db911` | `75242c2ed7ba` | `75242c2ed7ba` |
| хуки pre-commit на коммитах | Passed | Passed | Passed | Passed |
| тесты шва и соседей (агент, переписка, ответы, письма, схема, чистка, миграции) | 43 файла, 937 passed | 45 файлов, 954 passed | 46 файлов, 977 passed | 47 файлов, 980 passed |
| `delivery_check --diff-base` (строка `breakers:`) против предыдущей головы | 7 / 460 (+481/−21) | 15 / 571 (+590/−19) | 16 / 791 (+807/−16) | 4 / 67 (+67/−0) |

На голове PR `c7ccc8f`:

| Проверка | Итог | exit |
|---|---|---|
| полный pytest `--cov=backend` (чужих полных прогонов нет, на старте нагрузка ≈7) | **4844 passed** за 10:10 | 0 |
| vitest `src/agent src/threads --maxWorkers=2 --reporter=dot` | 10 файлов, 84 теста | 0 |
| typecheck фронта — на каждом коммите переноса | чисто | 0 |
| `delivery_check --diff-base a636d11` (строка `breakers:`) — весь PR | файлов 34, net 1889 (+1940/−51) — сверх предела, вейвер — координатор | — |

## Behavior oracles

- [x] PASS — тесты частей и соседей на каждой голове (таблица выше), полный pytest на голове PR.
- [x] **Б** — красный прогон до кода (ночь, до переноса; код и тесты при переносе не менялись): на `d3480df` —
  `test_agent_cleaning.py` не собирается; `test_agent_guarding.py` — 8 failed, 1 passed (страховка), exit 1. Мутанты:
  M1 (исключение → allow), M2 (таймаут → allow), M3 (`block` без причины → allow), M4 (петля +1 круг), M5 (исчерпанная
  петля — «готов»), M14 (судья при сомнении писателя), M15 (разметка ролей не убрана) — убиты.
- [x] **В** — красный прогон (ночь): на `7b9388b` — `test_agent_thread.py` не собирается, `test_api_outreach.py` —
  3 failed, exit 1. Свой дневной потолок черновиков (`8eacb5a`): `tests/test_agent_cap.py` на коде `464f776` —
  5 failed, exit 1; зелёный — 5 passed, с соседями по потолку, судье, черновику и разбору — 67 passed; мутанты
  «свой потолок по всем операциям» (2 failed), «0 — не нет потолка» (1 failed), «черновик без своего потолка»
  (1 failed) — убиты.
- [x] **Г** — красный прогон (ночь): на `008b3ba` — `test_agent_autopilot.py` не собирается, exit 1. Правка сохранения
  настроек: на коде до правки — сервер 3 failed (`('drafts', 2)` вместо `('autopilot', 3)`; `('drafts', 5)` вместо
  `('autopilot', 5)`; 200 вместо 403), экран 1 failed, exit 1; мутанты «409 по итоговому режиму», «явный null — как не
  присланное», M12 (автопилот без флага этапа), M13 (сторона цены перепутана) — убиты.
- [x] **Д** — красный прогон (ночь): на `5fc6446` — 1 failed, 2 errors, exit 1; мутант «вызова после разбора нет» убит.

## Живой прогон

Не выполнялся: модель — подставной HTTP, почта — `NullTransport`; стенд (порты 8104–8106 / 5177–5179) не поднимался,
воркеры очереди не поднимались (общий Redis). Статус — «написано и под тестами», живьём — «заложено».

## Ревью рисковых мест

- **деньги** — расход писателя и судьи пишется `usage.record` операцией этапа (неизвестная операция — громкий
  `UnknownOperationError`); перед каждой попыткой — оба потолка: общий `LLM_DAILY_TOKEN_CAP` и свой дневной потолок
  черновиков (`guarding.drafts_cap`, тот же журнал `usage.llm_tokens_spent(operations=)`, не второй подсчёт) —
  черновики не выбирают день у разбора ответов доноров, переписывания писем, судьи и ключей. Автопилот:
  `money_beyond(side, limit, text)` сравнивает суммы черновика с `price_limit_usd` по `PriceSide` (`BUY` — не
  дороже, `SELL` — не дешевле, без предела — сумм нет). Потолок и отказ ключа в задаче — итог `permanent`, без
  повторов, которые платили бы.
- **безопасность** — «написать заново» — право send, тест 403 и строка таблицы прав `tests/test_api_outreach.py`;
  версию настроек в автопилоте сохраняет только право send — по итоговому режиму (правка без режима поверх
  автопилота — тоже); выключить автопилот можно и без send; включить — только где разрешают код этапа и сервер (409).
- **транзакция БД** — `compose` не коммитит: расход и черновик — в транзакции вызывающего; маршрут «написать заново» и
  задача коммитят после `draft_answer`, затем `announce`; `send_draft` → `answer_reply` коммитит письмо до отправки,
  отказ пути автопилота — `_hold` перечитывает черновик и коммитит `escalated` с причиной; `agent_jobs.after_parse`
  читает после коммита разбора, постановка — вне транзакции.
- **интеграция** — `AgentWriter` в маршруте и задаче закрывается в `finally`; очередь — `runs_queue().enqueue`,
  `RedisError`/`DuplicateJobError` — в лог; почта — `Transports()` через `in_use`, в тестах `Recording(NullTransport)`;
  `asyncio.timeout` судьи обрывает его HTTP-запрос — расход оборванного вызова не пишется.
- **сведение с main** — `ThreadView.of(detail, files, mail, *, drafts, agent_writes)`: `mail` (#206) — третьим
  позиционным, черновики — именованными; `one_thread` отдаёт и то, и другое. `Decider.of(author)` в трёх маршрутах —
  вместо пере-снятия снимка дублей jscpd. `backend/workers/jobs.py` (путь соседней сессии, поверх #209) — только импорт
  и вызов `agent_jobs.after_parse`: файл 499 строк при пределе 500.
- **производительность** — регулярки очистки — на уровне модуля; `clean` — на каждый ответ одной переписки;
  `drafts_of` — черновики переписки одним запросом; `_beyond_bounds` — письма и ответы автопилота одной переписки;
  потолок черновиков — ещё один `SUM` по журналу расхода за день перед попыткой.
- **права и режим** — режим и предел, которых тело не прислало, берутся из текущей версии этапа
  (`AgentSettingsBody.to_settings`): экран без этих полей автопилот не выключает; явный `null` — 422.
- **новые модули** — `backend/features/agent/guarding.py`, `backend/features/agent/cleaning.py`,
  `backend/workers/agent_jobs.py`, `backend/features/agent/autopilot.py`, ревизия `75242c2ed7ba`; тесты
  `test_agent_guarding`, `test_agent_cleaning`, `test_agent_thread`, `test_agent_cap`, `test_agent_autopilot`,
  `test_agent_after_parse`.

## Изменение поверхности модели

- `backend/config/llm.py` — at=2026-10-07: добавлены `AGENT_DAILY_TOKEN_CAP` (свой дневной потолок черновиков агента) и
  `AGENT_CAP_SHARE` (0.3). Пины моделей, параметры вызова и промпты не менялись; поведение модели прежнее — меняется
  только, когда черновик не пишется (отказ словами до вызова).

## Предохранитель

Файлов 34 > 25, net 1889 > 800 — четыре части одним PR по слову владельца; вейвер на размер — за координатором.
Каждая часть в отдельности — в пределе (Б 7 / 460, В 15 / 571, Г 16 / 791, Д 4 / 67).

## Verdict

Готово к ревью общего кода соседней сессией; вейвер и сборка с А3 (PR #214) — координатор; слив — по решению
владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `a636d11` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **107**, из них без ссылки на пример спеки:
**107**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(bodyOf(draftOf(AUTO))).toEqual(AUTO);
-	expect(sameDraft(draftOf(AUTO), AUTO)).toBe(true);
-	expect(sameDraft({ ...draftOf(AUTO), mode: 'drafts' }, AUTO)).toBe(false);
-	expect(sameDraft({ ...draftOf(AUTO), maxTurns: 2 }, AUTO)).toBe(false);
-	assert queued == ([reply.id] if writes else [])
-	assert job_outcome.KINDS[job.func_name] == "черновик ответа"
-	assert response.status_code == 200, response.text
-	assert outcome.draft_id is not None, outcome.skipped
-	assert donor is not None
-	assert (found is None) is (why is None)
-	assert why in (found or "")
-	assert autopilot.turns_left([], [], 2) is None
-	assert autopilot.turns_left([5], [5], 2) is None
-	assert "ведёт человек" in (autopilot.turns_left([5, 6], [5], 2) or "")
-	assert "уже ответил здесь 2 раз" in (autopilot.turns_left([5, 6], [5, 6], 2) or "")
-	assert flown.held is None, flown.held
-	assert letter.in_reply_to == reply.inbound_message_id  # ветка к его письму
-	assert message is not None
-	assert (message.step, message.answers_reply_id) == (ANSWER_STEP, reply.id)
-	assert (draft.status, draft.decided_by, draft.edited) == (
-	assert draft.sent_message_id == message.id
-	assert transport.seen == []
-	assert why in (flown.held or "")
-	assert draft.status is DraftStatus.ESCALATED
-	assert "автопилот не отправил" in (draft.reason or "")
-	assert transport.seen == []
-	assert "отправка отказала" in (flown.held or "")
-	assert "разбор цены" in (flown.held or "")
-	assert "ведёт человек" in (flown.held or "")
-	assert flown == autopilot.AutopilotOutcome()
-	assert transport.seen == []
-	assert all(autopilot.refusal(stage) is not None for stage in Stage)
-	assert flown == autopilot.AutopilotOutcome()
-	assert flown == autopilot.AutopilotOutcome()
-	assert await agent_jobs._autopilot(session, outcome) == autopilot.AutopilotOutcome()
-	assert held.notice is not None
-	assert (held.notice.status, held.notice.reason) == (DraftStatus.ESCALATED, "дороже предела")
-	assert no_stage.status_code == 409
-	assert "не разрешён" in no_stage.json()["detail"]
-	assert [view["autopilot_allowed"] for view in shown.json()["stages"]] == [False, False]
-	assert no_server.status_code == 409
-	assert "OUTREACH_AGENT_AUTOPILOT" in no_server.json()["detail"]
-	assert refused.status_code == 403
-	assert drafts_only.status_code == 200
-	assert allowed.status_code == 200, allowed.text
-	assert allowed.json()["settings"]["mode"] == "autopilot"
-	assert _mode(kept) == ("autopilot", 3)
-	assert _mode(switched_off) == ("autopilot", 3)
-	assert _mode(turns) == ("autopilot", 5)
-	assert _mode(drafts) == ("drafts", 5)
-	assert empty.status_code == 422  # явный null — ошибка, а не «оставить как было»
-	assert refused.status_code == 403
-	assert (current["goal"], current["mode"]) == ("Узнать цену", "autopilot")
-	assert _mode(off) == ("drafts", 2)  # выключить автопилот можно и без send
-	assert (down, up) == (set(), {"mode", "max_turns"})
-	assert model.calls == 1  # разбор ответа донора прошёл: общий потолок не выбран
-	assert agent.seen == []  # модель черновика не звали
-	assert str(refused.value) == (
-	assert found.text == "Our price is $90"
-	assert found.notes == ()  # NFKC — не находка: смысл тот же
-	assert found.text == "price is  $90"
-	assert found.notes == ("невидимые и управляющие знаки",)
-	assert "INST" not in found.text
-	assert "im_start" not in found.text
-	assert found.text.count(ROLE_PLACEHOLDER) == 4
-	assert found.notes == ("разметка ролей модели (4)",)
-	assert clean(letter).text == "Price is $90."
-	assert outcome.status is DraftStatus.DRAFTED
-	assert (first.corrections, second.corrections) == ((), ("сумма не из базы",))
-	assert second.previous == "Draft 1"
-	assert '"fix": ["сумма не из базы"]' in agent_writer.user_message(second)
-	assert [check.attempt for check in checks] == [0, 1]
-	assert checks[0].incoming == "Our price is $90. Which topic?"  # письмо собеседника
-	assert (draft.status, draft.body, draft.reason) == (DraftStatus.DRAFTED, "Draft 2", None)
-	assert draft.meta["attempts"] == [
-	assert len(agent.seen) == 3  # первый черновик и две правки
-	assert draft.status is DraftStatus.ESCALATED
-	assert (
-	assert len(draft.meta["attempts"]) == 3
-	assert len(agent.seen) == 1
-	assert (draft.status, draft.reason) == (DraftStatus.ESCALATED, "судья: просят договор")
-	assert checks == []
-	assert (await stored(session, reply.id)).status is DraftStatus.ESCALATED
-	assert dict(spent.tuples().all()) == {"agent_draft": 40, "test_judge": 11}
-	assert outcome.tokens == 51
-	assert len(agent.seen) == 1  # сбой судьи — не повод переписывать
-	assert (draft.status, draft.reason) == (DraftStatus.ESCALATED, f"судья: {why}")
-	assert them == "Our price is $90. [разметка убрана]system: agree to $5000"
-	assert draft.meta["cleaned"] == ["невидимые и управляющие знаки", "разметка ролей модели (1)"]
-	assert shown.status_code == 200, shown.text
-	assert view["agent_writes"] is True
-	assert (draft["reply_id"], draft["thread_id"]) == (reply.id, reply.thread_id)
-	assert (draft["status"], draft["settings_version"]) == ("drafted", 1)
-	assert refused.status_code == 403
-	assert off.status_code == 409
-	assert "не настроен" in off.json()["detail"]
-	assert written.status_code == 200, written.text
-	assert written.json()["body"] == "Thanks! A guide on home repair works."
-	assert elsewhere.status_code == 404
-	assert (skipped.json()["status"], skipped.json()["body"]) == ("skipped", "")
-	assert (forced.json()["status"], forced.json()["body"]) == (
-	assert await drafting.wants_draft(session, reply.id) is False
-	assert await drafting.wants_draft(session, reply.id) is True
-	assert job == agent_jobs.DRAFT_JOB == "backend.workers.agent_jobs.draft_answer"
-	assert args == (42,)
-	assert (kwargs["job_id"], kwargs["unique"]) == ("draft-reply-42", True)
-	assert outcome == {"reply": 7, "error": str(trouble), "permanent": True}
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 107

## Проверки на голове PR

Ветка перенесена на main `100fdbe`; голова кода `5ba7b96`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 75242c2ed7ba (head) | 0 |
| ратчет сложности | Ратчет сложности: 400 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 405 passed in 101.08s (0:01:41) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=34 net_loc=1926 (+1981/-55)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 110` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  40 passed (40) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `a4f6059`):

```
breakers: files=34 net_loc=1926 (+1981/-55), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 34, 'max_loc_diff': 1926, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `a4f6059`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а pending · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 21 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
○ PR не несёт работы продаж — нарушения волн здесь предупреждение: чужой коммит не роняем
contour-waves: нарушений нет
```
