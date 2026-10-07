# Verify report

**Поставка:** шов агента переписки по этапам, часть «А1» из семи — реестр этапов и таблица черновиков.

**Date:** 2026-10-07
**Verifier:** process:ci (на PR); до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов части — в дайджесте в конце отчёта; подписывает ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `649c943` — голова части, ветка `sales/3.0-agent-seam-58b` на main `58b9806` (#180; не запушена). Перенос 07.10: ночная голова `34ce1c5` на `7c28116`, промежуточная `90bffd0` на `356251d` (ветка `sales/3.0-agent-seam-main`)

## Shape oracles (на голове `649c943`)

| Проверка | Итог | exit |
|---|---|---|
| `mypy backend` (strict) | ошибок нет | 0 |
| `scripts/gates.py` | нарушений нет | 0 |
| `scripts/complexity.py` | расхождений нет | 0 |
| `alembic heads` | 7ccbaf6d840a (одна голова; после fc721be3d031) | 0 |
| хуки pre-commit на коммите | все Passed | 0 |
| тесты шва и соседей (33 файлов: агент, переписка, ответы, письма, схема, чистка, миграции) | 836 passed | 0 |
| `delivery_check --diff-base` (строка `breakers:`) | файлов 14, net 474 (+512/−38) против `58b9806` | — |

## Behavior oracles

- [x] PASS — тесты шва и соседей на голове: см. `agent-report.md`, «Проверки по головам» (exit 0).
- [x] Красный прогон до кода (07.10 ночью, до переноса на main; при переносе к тестам добавлены только две строки `REVIEWED` в `tests/test_prune_test_traces.py` — их красный прогон отдельной строкой) — на `7c28116`: `tests/test_agent_stages.py` не собирается (`ModuleNotFoundError: backend.features.agent.stages`); `test_schema` — 2 failed (нет `agent_drafts`), `test_prune` — 1 failed (разобраны ссылки, которых нет), `test_agent_settings::test_migration_goes_down_and_up` — failed (нет ревизии черновиков); итог 4 failed, 1 error, exit 1.
- [x] Красный прогон строк `REVIEWED` чистки следов (07.10 днём): `tests/test_prune_test_traces.py` части на коде `356251d` — 1 failed («больше нет: agent_drafts.reply_id → replies CASCADE, agent_drafts.sent_message_id → messages SET NULL»), exit 1; обратно — код части без строк: «не разобраны: …» те же две, exit 1.
- [x] Обратные прогоны — мутанты шва (M1–M15) бьют по коду следующих PR; у А1 контракт держат тесты «части прежние» и «запрос прежний».

## Живой прогон

Не выполнялся: модель — подставной HTTP, почта — `NullTransport`; стенд (порты 8104–8106 / 5177–5179) не поднимался,
воркеры очереди не поднимались (общий Redis). Статус — «написано и под тестами», живьём — «заложено».

## Ревью рисковых мест

- **деньги** — риска нет, потому что сумм часть не считает: `price_limit_usd` и `PriceSide` (`BUY`/`SELL`) — объявление
  стороны цены этапа в `stages.py`; сравнение сумм с пределом — в Г (`autopilot.money_beyond`).
- **безопасность** — риска нет: права маршрутов не менялись (`_settler`, `_viewer` в `api/agent/routes.py`);
  `.secrets.baseline` — хэш ревизии `7ccbaf6d840a` (номер ревизии, ложное срабатывание) и сдвиг строки
  `domain.py` (161 → 176, тот же хэш).
- **производительность** — `json.loads` в тесте и `load_prompt` с `lru_cache(maxsize=8)`: промпт читается с диска
  один раз на путь.
- **интеграция** — внешних вызовов часть не добавляет: `httpx.MockTransport` — подставная модель тестов
  (`tests/test_agent_writer.Model`); `AgentWriter.write` зовёт ту же `post_chat`, меняется только `model` и промпт.
- **чистка** — две ссылки `agent_drafts` (на ответ CASCADE, на письмо SET NULL) разобраны в обоих реестрах:
  `tests/test_prune.py` (липовые прогоны) и `tests/test_prune_test_traces.py` (следы проверки, #192 —
  `outreach/own_inboxes.remove_inbox_trace` удаляет ответы раньше писем, черновик уходит с ответом);
  `backend/features/runs/prune.py` не тронут.
- **новый модуль** — `backend/features/agent/stages.py` (типы и реестр, состояния нет), ревизия
  `7ccbaf6d840a_agent_drafts` (таблица и тип), тесты `test_agent_stages`.

## Предохранитель

Файлов 14 ≤ 25, net 474 ≤ 800 — без вейвера.

## Verdict

Готово к ревью общего кода; слив — после окна соседней сессии, по решению владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `58b9806` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **17**, из них без ссылки на пример спеки:
**17**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	assert set(AGENT_STAGES) == {Stage.DONORS, Stage.ADVERTISERS}
-	assert parts.price is price
-	assert parts.defaults == settings.defaults(stage)
-	assert (parts.prompt, parts.prompt_version) == (
-	assert (parts.model, parts.usage_operation) == (llm_cfg.AGENT_MODEL, "agent_draft")
-	assert parts.brief is stages.no_brief
-	assert (parts.autopilot, parts.guard, parts.on_draft) == (False, None, None)
-	assert sent["model"] == "gpt-5"
-	assert sent["messages"][0]["content"] == agent_writer.load_prompt()
-	assert set(facts) == {"stage", "settings", "sign_as", "parsed_from_last_message"}
-	assert sent["model"] == "stage-model"
-	assert sent["messages"][0]["content"] == "Stage prompt: use the facts."
-	assert facts["facts"] == ["Аудит — цена на созвоне"]
-	assert spec is not None
-	assert spec.loader is not None
-	assert (down, up) == (False, True)
-	assert statuses == ["drafted", "skipped", "escalated", "sent", "rejected"]
```

⚠ **Ни одно утверждение не ссылается на пример из спеки.** Значит все
ожидания придумал исполнитель — это ровно тот круг, о котором §3.1d.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 17

## Проверки на голове PR

Ветка перенесена на main `0dcbcf2`; голова кода `5059c82`, проверки — по одному разу. pytest среза, diff-coverage, pre-commit и фронт прошли на базе `9ee3d9e`; после переноса на `0dcbcf2` (пересечение — `backend/features/core/domain.py`, без конфликта; снимки `.secrets.baseline` и сложности сведены) заново — быстрые проверки таблицы и тесты пересечения (`test_message_gone`, `test_agent_stages`, `test_schema`, `test_prune_test_traces`, `test_mail_test` — 53 passed); полный pytest — в CI PR.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 7ccbaf6d840a (head) | 0 |
| ратчет сложности | Ратчет сложности: 394 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 3300 passed in 517.51s (0:08:37) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=14 net_loc=474 (+512/-38)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 17` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | см. лог | — |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `2f87883`):

```
breakers: files=14 net_loc=474 (+512/-38), excluded=10 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `2f87883`):

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
