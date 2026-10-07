# Verify report

**Поставка:** шов агента переписки по этапам, часть «А2» из семи — черновик по брифу этапа — кому положен, что видит этап, где лежит.

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов части — в дайджесте в конце отчёта; подписывает ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `945d701` — голова части, ветка `sales/3.0-agent-seam-58b` на main `58b9806` (#180; не запушена). Перенос 07.10: ночная голова `001d935` на `7c28116`, промежуточная `144228f` на `356251d` (ветка `sales/3.0-agent-seam-main`). Три коммита: `4d28f59` (перенос `001d935`), `2a42af5` (`Brief.sign_as`), `945d701` (пустое имя подписи запрещено)

## Shape oracles (на голове `945d701`)

| Проверка | Итог | exit |
|---|---|---|
| `mypy backend` (strict) | ошибок нет | 0 |
| `scripts/gates.py` | нарушений нет | 0 |
| `scripts/complexity.py` | расхождений нет | 0 |
| `alembic heads` | 7ccbaf6d840a (одна голова) | 0 |
| хуки pre-commit на коммите | все Passed | 0 |
| тесты шва и соседей (34 файлов: агент, переписка, ответы, письма, схема, чистка, миграции) | 855 passed | 0 |
| `delivery_check --diff-base` (строка `breakers:`) | файлов 5, net 779 (+781/−2) против `649c943` | — |

## Behavior oracles

- [x] PASS — тесты шва и соседей на голове: см. `agent-report.md`, «Проверки по головам» (exit 0).
- [x] Красный прогон до кода (07.10 ночью, до переноса на main; код и тесты коммита А2 (`4d28f59`, на `-main` — `ed34806`) при переносах не менялись, `Brief.sign_as` — отдельным коммитом и отдельным красным прогоном) — на `34ce1c5`: `tests/test_agent_drafting.py` не собирается (`ImportError: drafting`), exit 1.
- [x] `Brief.sign_as` (07.10 днём): красный прогон — `TestSignAs` на коде `ed34806` (поля нет): 2 failed, exit 1; мутант «писатель игнорирует `sign_as`» (поле есть, в запрос писателю — общее имя) — 1 failed (имя брифа), 1 passed (`None`), exit 1 — убит; второе прочтение — `writer.user_message` не кладёт `sign_as` в запрос модели — 2 failed, exit 1 — убит.
- [x] Пустое имя подписи (07.10, перестановка на `58b9806`): `Brief(sign_as="")` и пробелы — `ValueError` «пустое имя подписи — задай имя или None»; красный прогон на `2a42af5` (проверки нет) — 2 failed, exit 1; мутант «проверка без `strip()`» — 1 failed (пробелы), 1 passed, exit 1 — убит.
- [x] Обратные прогоны — M6 (skip `human` пишет черновик) — убит; M7 (`human` → `skipped`) — убит; M8 (`meta` не ложится) — убит.

## Живой прогон

Не выполнялся: модель — подставной HTTP, почта — `NullTransport`; стенд (порты 8104–8106 / 5177–5179) не поднимался,
воркеры очереди не поднимались (общий Redis). Статус — «написано и под тестами», живьём — «заложено».

## Ревью рисковых мест

- **деньги** — разобранная цена ответа (`_parsed`: `price_white`, `price_grey`, `currency`) уходит писателю строками,
  как в заготовке; сумм часть не считает. `meta` брифа с `Decimal` приводится к строкам (`_plain`).
- **транзакция БД** — `draft_answer` не коммитит: коммит — у вызывающего (задача, маршрут), `announce` — после него.
  Запись `_store` — одна вставка с заменой (`on_conflict_do_update`), гонка задачи и кнопки на одном ответе не
  упирается. Решённый черновик не переписывается (`_done` до записи).
- **производительность** — `json.loads(json.dumps(...))` в `_plain` — на одну `meta` черновика; `.all()` в `_turns` —
  письма и ответы одной переписки (в запрос уходят последние `MAX_TURNS=8`).
- **безопасность** — риска нет, потому что прав и секретов часть не трогает: `SECRET` и `INBOUND_SECRET` в
  `tests/test_agent_drafting.py` — ключ подписи адреса ответа из оснастки приёма (`tests/test_replies_inbox`),
  нужный, чтобы ответ лёг в переписку; маршрутов в части нет.
- **подпись** — `Brief.sign_as` не обрезается: имя — данные этапа (у этапа со своим отправителем — из его настроек); пустое имя и пробелы — `ValueError` словами в `Brief.__post_init__` (этап без имени отдаёт ответ человеку, а не пустую подпись); `None` — прежнее `OUTREACH_SENDER_NAME.strip()`. В модель имя уходит тем же ключом `sign_as`, что и раньше, — запрос доноров и рекламодателей без имени брифа прежний.
- **интеграция** — риска нет, потому что внешних вызовов часть не добавляет: `httpx.AsyncClient` с
  `httpx.MockTransport` — только в `tests/test_agent_drafting.py` (`signing_model` — подставная модель теста
  `TestSignAs`, подписывает именем из ключа `sign_as` запроса), сети нет; в коде части `AgentWriter` не создаётся —
  писателя передаёт вызывающий (задача и маршрут — В).
- **новый модуль** — `backend/features/agent/drafting.py`, тесты `test_agent_drafting`.

## Предохранитель

Файлов 5 ≤ 25, net 779 ≤ 800 — без вейвера.

## Verdict

Готово к ревью общего кода; слив — после окна соседней сессии, по решению владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `649c943` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **45**, из них без ссылки на пример спеки:
**45**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert got.reply_id is not None
-	assert found is not None
-	assert draft is not None
-	assert outcome.skipped is None
-	assert [turn.ours for turn in request.turns] == [True, False]
-	assert request.turns[0].text == "Good afternoon,"
-	assert request.turns[1].text == "Our price is $90. Which topic?"  # без цитаты
-	assert request.parsed == {"price_white": "90.00", "currency": "USD"}
-	assert request.sign_as == "Anna"
-	assert (request.prompt, request.model) == (agent_writer.PROMPT_PATH, llm_cfg.AGENT_MODEL)
-	assert (draft.status, draft.body) == (
-	assert (draft.prompt_version, draft.meta) == (agent_writer.PROMPT_VERSION, {})
-	assert thread is not None
-	assert outcome.skipped is not None
-	assert why in outcome.skipped
-	assert agent.seen == []
-	assert outcome.skipped == "на этот ответ уже ответили"
-	assert retried.skipped == "черновик уже написан"
-	assert retry_agent.seen == []
-	assert again.draft_id == first.draft_id  # тот же черновик, переписан
-	assert again.status is DraftStatus.ESCALATED  # агент сомневается — человеку
-	assert decided.skipped is not None
-	assert "уже решили" in decided.skipped
-	assert units == 40
-	assert agent.seen == []
-	assert agent.seen == []  # модель не звали
-	assert outcome.status is status
-	assert (draft.status, draft.body, draft.reason) == (status, "", "собеседник благодарит")
-	assert draft.meta == {"situation": "ack", "skip": kind.value}
-	assert await session.scalar(select(func.count()).select_from(UsageRecordModel)) == 0
-	assert conversation.turns[-1] == Turn(ours=False, text="Our price is $90. Which topic?")
-	assert conversation.settings.goal == defaults(Stage.DONORS).goal
-	assert agent.seen[0].facts == ("Аудит — цена на созвоне",)
-	assert draft.status is DraftStatus.DRAFTED
-	assert draft.meta == {"kb_version": "k7", "confidence": "0.87"}  # сумма — строкой
-	assert len(agent.seen) == 1
-	assert draft.status is DraftStatus.DRAFTED
-	assert draft.meta == {"forced_over_skip": {"kind": "no_reply", "reason": "спасибо"}}
-	assert body.splitlines()[-1] == signed
-	assert ("Anna" in body) is (sign_as is None)  # имя брифа — вместо общего
-	assert (notice.draft_id, notice.status) == (outcome.draft_id, DraftStatus.DRAFTED)
-	assert notice.thread_id == reply.thread_id
-	assert "черновик цел" in caplog.text
-	assert (await stored(session, reply.id)).status is DraftStatus.DRAFTED
-	assert heard == []
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 45

## Проверки на голове PR

Ветка перенесена на main `453288d`; голова кода `08a00a0`, проверки — по одному разу. pytest части и соседей, diff-coverage, pre-commit и фронт прошли на стопке поверх А1 (дерево main `cdb32f9` после сквоша #212, код части тот же); main с тех пор принял #211 (мост почты — ни одного файла части А2), после переноса на `453288d` заново — быстрые проверки таблицы (`delivery_check` и волны — против main, то есть только часть А2); полный pytest — в CI PR.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 7ccbaf6d840a (head) | 0 |
| ратчет сложности | Ратчет сложности: 395 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 241 passed in 56.03s | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=5 net_loc=779 (+781/-2)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 45` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  26 passed (26) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `6a5359b`):

```
breakers: files=5 net_loc=779 (+781/-2), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `6a5359b`):

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
