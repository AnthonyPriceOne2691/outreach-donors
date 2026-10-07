# Verify report

**Поставка:** шов агента переписки по этапам, часть «А3» из семи — решения по черновику — отправить как есть, с правкой, отклонить с причиной.

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов части — в дайджесте в конце отчёта; подписывает ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `a636d11` — голова части, ветка `sales/3.0-agent-seam-0710` на main `9ee3d9e` (#210; не запушена). Перенос 07.10: ночная голова `d3480df` на `7c28116`, затем ветки `-main` (`356251d`) и `-58b` (`58b9806`; там голова `b13c0ea`)

## Shape oracles (на голове `a636d11`)

| Проверка | Итог | exit |
|---|---|---|
| `mypy backend` (strict) | ошибок нет | 0 |
| `scripts/gates.py` | нарушений нет | 0 |
| `scripts/complexity.py` | расхождений нет | 0 |
| `alembic heads` | 3924977db911 (одна голова; после 7ccbaf6d840a) | 0 |
| хуки pre-commit на коммите | все Passed | 0 |
| тесты шва и соседей (41 файлов: агент, переписка, ответы, письма, схема, чистка, миграции) | 924 passed | 0 |
| `delivery_check --diff-base` (строка `breakers:`) | файлов 8, net 614 (+620/−6) против `dcf237b` | — |

## Behavior oracles

- [x] PASS — тесты шва и соседей на голове: см. `agent-report.md`, «Проверки по головам» (exit 0).
- [x] Красный прогон до кода (07.10 ночью, до переноса на main; код и тесты части при переносе не менялись) — на `001d935`: 6 failed (маршрутов и значения журнала нет), exit 1.
- [x] Обратные прогоны — M9 (отклонить без причины) — убит; M10 («как есть» у `escalated` уходит) — убит; M11 (второе решение ложится) — убит.

## Живой прогон

Не выполнялся: модель — подставной HTTP, почта — `NullTransport`; стенд (порты 8104–8106 / 5177–5179) не поднимался,
воркеры очереди не поднимались (общий Redis). Статус — «написано и под тестами», живьём — «заложено».

## Ревью рисковых мест

- **деньги** — риска нет: решения сумм не считают; текст черновика уходит тем же `answer_reply`, где
  `guards.assert_no_metrics` и прочие проверки письма (суммы в тексте — дело автора).
- **безопасность** — права маршрутов: `_viewer` на `GET /drafts`, `GET /drafts/{draft_id}`; `_sender` (право send)
  на `send_draft` и `reject_draft`; тест 403 у оператора. `bearer(token)` — помощник тестов.
- **транзакция БД** — `answer_reply` коммитит письмо до отправки; `settle_sent` и журнал (`AccessRepository.record`)
  ложатся после отправки и коммитятся маршрутом (`await session.commit()`); отказ отправки до `settle_sent` — черновик
  нерешён, второе решение невозможно (`_undecided`). Ответ из переписки: `settle_sent` + `commit` после
  `answer_reply` в `backend/api/threads/routes.py`.
- **производительность** — список с пределом (`limit` ≤ `DRAFTS_PAGE=200`, гейт `unbounded-list`), один запрос с
  соединением настроек и ответа (`_shown`).
- **интеграция** — внешних вызовов часть не добавляет: `httpx.AsyncClient` — клиент тестов маршрутов; почта
  маршрута `send_draft` — те же `Transports()` через `in_use`, что у ответа человека, в тестах — нулевой транспорт.
- **новый модуль** — `backend/features/agent/drafts.py`, ревизия `3924977db911`, тесты `test_agent_decisions`.

## Предохранитель

Файлов 8 ≤ 25, net 614 ≤ 800 — без вейвера.

## Verdict

Готово к ревью общего кода; слив — после окна соседней сессии, по решению владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `945d701` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **29**, из них без ссылки на пример спеки:
**29**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert (missing.status_code, blank.status_code) == (422, 422)
-	assert rejected.status_code == 200, rejected.text
-	assert (shown["status"], shown["reject_reason"]) == ("rejected", "тон не тот")
-	assert shown["decided_by"] == "админ@site.com"
-	assert (again.status_code, send.status_code) == (409, 409)
-	assert audit is not None
-	assert audit.details is not None
-	assert (audit.details["решение"], audit.details["причина"]) == ("отклонён", "тон не тот")
-	assert draft.status is DraftStatus.ESCALATED
-	assert as_is.status_code == 409
-	assert "как есть он не уходит" in as_is.json()["detail"]
-	assert edited.status_code == 200, edited.text
-	assert (draft.status, draft.edited, draft.final_body) == (
-	assert draft.sent_message_id == edited.json()["id"]
-	assert sent_now.status_code == 200, sent_now.text
-	assert sent_now.json()["sender_email"] == "anna@mail.test"  # ящик переписки
-	assert letter is not None
-	assert (letter.answers_reply_id, letter.body) == (reply.id, draft.body)
-	assert (draft.status, draft.edited, draft.decided_by) == (
-	assert answered.status_code == 200, answered.text
-	assert (draft.status, draft.edited) == (DraftStatus.SENT, True)
-	assert draft.sent_message_id == answered.json()["id"]
-	assert [card["id"] for card in waiting.json()] == [draft.id]
-	assert ready.json() == []
-	assert detail.json()["meta"] == {"situation": "asks_price"}
-	assert detail.json()["reason"] == "цена за пределом"
-	assert missing.status_code == 404
-	assert refused.status_code == 403  # решать — право send
-	assert values.count("agent_draft_decided") == 1
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 29

## Проверки на голове PR

Ветка перенесена на main `b9a2de3`; голова кода `5df520f`, проверки — по одному разу, после переноса.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 3924977db911 (head) | 0 |
| ратчет сложности | Ратчет сложности: 396 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 261 passed in 100.31s (0:01:40) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=8 net_loc=669 (+675/-6)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 34` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | см. лог | — |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `6e91eea`):

```
breakers: files=8 net_loc=669 (+675/-6), excluded=9 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `6e91eea`):

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
