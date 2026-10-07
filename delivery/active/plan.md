# Plan: шов агента переписки, части «Б»–«Д» одним PR

## Approach

- **Б.**
  - `guarding.compose(session, stage, writer, request, *, incoming)`: попытка = потолки → писатель → расход;
    сомнение или пусто, либо судьи нет — выход; иначе `judged` (таймаут, исключение, `block` без причины →
    `escalate`), расход судьи его операцией; `allow`/`escalate` — выход, `block` —
    `replace(request, corrections=…, previous=…)`.
  - `cleaning.clean(text)` — NFKC → `translate` невидимых → управляющие в пробел → разметка ролей в метку →
    `written_by_hand`; `notes` словами.
  - `drafting._written` зовёт `compose`; `conversation()` чистит ответы собеседника и собирает `notes`.
- **В.**
  - `agent_jobs.draft_answer` — `asyncio.run(_draft_answer)`: движок, сессия, `drafting.draft_answer`, коммит,
    `announce`; `DraftUnavailableError(permanent)` и `LlmCapExceededError` — итог `{"error", "permanent"}`, остальное —
    повтор очереди. `queue_draft` — `enqueue(..., job_id=draft-reply-N, unique=True)`, сбой очереди — в лог.
  - Маршрут «написать заново» — из заготовки, с `force`; переписка — черновики (`drafts_of`) и `agent_writes`;
    `ThreadView.of(detail, files, mail, *, drafts, agent_writes)` — `mail` (#206) третьим позиционным.
  - `Decider.of(user)` — кто решает, из пользователя, в трёх маршрутах (отправить, отклонить, ответить из переписки).
  - Потолок черновиков: `guarding.drafts_cap()` → `usage.OwnCap(операции агента из реестра, AGENT_DAILY_TOKEN_CAP
    или 30 % общего)`; `usage.ensure_llm_within_cap(session, own=…)` — тот же день и журнал
    (`llm_tokens_spent(operations=…)`), общий проверяется как прежде.
- **Г.**
  - `autopilot.refusal(stage)` — флаг этапа и сервер; `run(session, sending, draft_id)` — режим и `drafted`, этап
    разрешён, `_beyond_bounds`, `drafts.send_draft(body=None, by=Decider("autopilot"))`; отказ пути (`SendError`,
    `ForbiddenContentError`, `TransportError`, `DraftDecisionError`) — в лог и `_hold` (`escalated`, причина).
  - Задача: после записи и коммита — `_autopilot`; не ушло — `announce` с причиной автопилота.
  - Настройки: `mode`, `max_turns`; `AgentSettingsBody.to_settings(current, defaults)` берёт не присланное из
    текущей версии; право send — по итоговому режиму; 409 — при включении.
- **Д.**
  - `jobs._parse_reply`: после `Parser(...).parse` и коммита — `await agent_jobs.after_parse(session, reply_id)`
    (внутри — `drafting.wants_draft` и `queue_draft`).
  - `job_outcome.KINDS["backend.workers.agent_jobs.draft_answer"] = "черновик ответа"`.

## Rejected alternatives

- **Б:** судья, пропускающий при ошибке (CRM) — отклонено, потому что такой судья пропускает ровно тогда, когда
  проверить не смог; каталог сигнатур инъекций CRM в общую очистку — отклонено, потому что ложные срабатывания на
  донорах не мерились.
- **В:** `DRAFT_JOB` в `shared/queue.py` и вынос `_settled` в `workers/settling.py` (заготовка) — отклонено, потому
  что эти файлы правит соседняя сессия; пере-снять снимок дублей jscpd — отклонено, потому что у main снимок без
  запаса, а повтор `Decider(name=…, user_id=…)` убирается кодом; запас разбору ответов или «оба» вместо своего
  потолка черновиков — отклонено, потому что свой потолок закрывает все прочие операции модели и живёт в одном
  месте.
- **Г:** ветка `stage is Stage.DONORS` для стороны цены (заготовка) — отклонено, потому что новый этап молча стал бы
  «продаём»; колонка `auto_message_id` (заготовка) — отклонено, потому что `sent_message_id` + `decided_by` говорят
  то же; экран переключателя (заготовка) — отклонено, потому что разрешённых этапов нет; 409 по итоговому режиму —
  отклонено, потому что при снятом выключателе экран без переключателя не сохранил бы ни одной правки.
- **Д:** постановка из приёма ответа (вебхук) — отклонено, потому что агенту нужна разобранная цена; постановка
  внутри `replies/pipeline.Parser` — отклонено, потому что ядро разбора не знает об очереди; пять строк прямо в
  `workers/jobs.py` — отклонено, потому что файл после #209 у предела длины 500 строк.

## Risks

- Промпт этапа с судьёй обязан понимать ключ `rewrite` (у первых двух этапов судьи нет — промпт не тронут).
- `asyncio.timeout` отменяет судью: его HTTP-запрос обрывается — расход оборванного вызова не пишется.
- Повтор задачи черновика (сеть) не запоминает причину рядом с задачей (`remember_job_error` — в `_settled` файла
  `workers/jobs.py`).
- **Поверхность необратимого**: автопилот — письмо живому человеку без человека. Механизм влит выключенным; до
  включения на любом этапе — дописать в `irreversible_surfaces:` и переподписать владельцем.
- `MaybeSentError` не перехватывается: задача падает, повтор видит письмо-ответ.
- Потолок черновиков считает операции агента из реестра: этап со своими операциями черновика и судьи встанет под
  тот же потолок; явно заданный свой не урезается до 30 % (оба потолка проверяются) — на подтверждение.
- `backend/workers/jobs.py` — путь соседней сессии: правка — импорт и вызов, поверх #209.

## Rollout / migration

Голова миграций — 75242c2ed7ba (одна голова; `3924977db911` → `75242c2ed7ba`). Только добавление; откат — в
`downgrade` ревизии. Слив — по решению владельца, ревью общего кода — соседняя сессия; вейвер на размер — координатор.
Новая настройка `AGENT_DAILY_TOKEN_CAP` не обязательна: не задана — 30 % `LLM_DAILY_TOKEN_CAP` (общий 0 — потолка нет).

## Test / eval strategy

`tests/test_agent_guarding.py`, `tests/test_agent_cleaning.py`, `tests/test_agent_thread.py`, `tests/test_agent_cap.py`,
`tests/test_agent_autopilot.py`, `tests/test_agent_after_parse.py` — на настоящей базе дерева
(`outreach_test_<хэш дерева>`); модель — подставной HTTP (`httpx.MockTransport`) или писатель-подмена; разбор ответа —
настоящий `Parser` с подменённой платной моделью; почта — `NullTransport`/`Recording`; экран — vitest
`frontend/src/agent/agentDraft.test.ts`. Красный прогон — тесты части на голове предыдущей; мутанты — по обязательным
пунктам шва и по каждой правке (таблицы в `agent-report.md`).
