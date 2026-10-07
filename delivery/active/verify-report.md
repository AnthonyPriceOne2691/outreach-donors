# Verify report

**Поставка:** модуль «Продажи», Ф2 одним PR — части 2.1, 2.2, 2.3a и 2.3b: ответ лида продаж своей очередью и своим
воркером; вид ответа моделью, путь по виду кодом (волна В3а); «пишите другому» — новый лид той же компании; автоответ
переносит шаг, отписка словами закрывает адрес во всех направлениях. Отдельным коммитом — правка консоли доменов
рассылки (4.5a) по ревью соседней сессии.

**Date:** 2026-10-07
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=утверждения тестов частей — в дайджесте в конце отчёта (примеры названы в именах тестов прежними номерами частей — `test_a1_…`, дайджест такие не привязывает); подписывает владелец или ревьюер общего кода при ревью
**CI run:** нет — снимается на PR
**Commit:** `8bb3e29` — голова PR (голова части 2.3b), ветка `sales/2.x-replies-0710` на `aeb35ff` (main `7b56b50` + мост #216 + шов агента Б–Д + 5.1), не запушена

## Сборка

Восемь коммитов на `aeb35ff`, головы частей:

| Часть | Коммиты | Голова | Против головы предыдущей (`delivery_check`, без `delivery/`) |
|---|---|---|---|
| 2.1 своя очередь | `8440b43`, `e62609e` | `e62609e` | files=14 net_loc=715 |
| 2.2 вид ответа | `2058b37`, `37933c4`, `5a6a0e0` | `5a6a0e0` | files=21 net_loc=1812 |
| 2.3a другой контакт | `d312633` | `d312633` | files=9 net_loc=566 |
| 2.3b автоответ и отписка | `dca2a95`, `8bb3e29` | `8bb3e29` | files=11 net_loc=639 |

**Перенос на `aeb35ff`** (`git rebase --onto aeb35ff`): конфликты — `ops/job_outcome.py` (в `KINDS` рядом встала задача
черновика агента — обе строки), `core/usage.py` (`agent_draft` и `sales_reply_kind` — обе), второй реестр чистки
`tests/test_prune_test_traces.py` (строки `agent_drafts` и `referred_from_thread_id` — все), снимок сложности
(`--update` на каждой части). Миграция `739817077a57` — после головы базы `75242c2ed7ba` (ревизия автопилота шва
поверх `3924977db911`): на `3924977db911` было бы две головы. Остальное легло без правки.

**Правки предыдущего переноса (на main после 1.1b), сохранены:** обе подписи задач в `job_outcome.py` и правка
`_next_try`; docstring `replies/pipeline.py` на строку короче (498 строк); шаг образа и package-data — промпты #200 и
промпт продаж; `reply_kind.build_payload` без копии чужой формы (снимок дублей main без запаса); строка журнала точки
передачи с ключом `thread_id` (`5a6a0e0`: `thread` — поле записи журнала, на уровне INFO задача падала после вызова
модели); решение ссылки во втором реестре чистки.

**Правка консоли доменов рассылки — по ревью соседней сессии к окнам и лимитам (#219).** Отдельный коммит поверх
2.3b: `backend/cli/sending_domains.py`, `tests/test_sending_limits.py` и снимок сложности, files=2 net_loc=148.
Сценарий ревью: `outreach sending-domain --domain <домен доноров> --stage donors --daily-limit 40` заводил строку с
неделей выдержки, фильтр отсеивал все ящики домена, пачка доноров отвечала «Сегодня писать некому», а консоль
печатала «пишет». Теперь выдержка по умолчанию — только домену без отправленных писем (`_wrote`), `--stage`, чужой
ящикам домена, — отказ с кодом 6 и именами ящиков до записи строки, строка `_said` — «на выдержке до … — первые
письма с домена не уходят», неверные доводы — отказ разбора словами. Проверки агента на ветке окон и лимитов (тот же
код консоли): 9 новых тестов красные на старом коде; мутанты консоли — 7 из 7 убиты, по одному на правило;
`test_sending_limits`, `test_cli_main`, `test_schema`, `test_mail_watch`, `test_api_outreach`,
`test_migrations_match_models`, `test_letters_send_queue` — 182 passed; mypy, ruff, формат, `gates.py --commits`,
`lint-imports`, ратчет сложности, шаг образа — exit 0. В PR — cherry-pick без конфликтов; тесты консоли — в прогоне
«Проверки на голове PR».

## Shape oracles

На голове `8bb3e29` (07.10, дерево `sales-ac`):

| Проверка | Итог | exit |
|---|---|---|
| хуки pre-commit на коммитах с конфликтом при переносе (`8440b43`, `2058b37`, `d312633`) | Passed | 0 |
| ruff / ruff format (`backend`, `scripts`, `tests`) | чисто | 0 / 0 |
| `mypy backend` (strict) | 390 файлов, ошибок нет | 0 |
| `scripts/gates.py` (в том числе `inline-prompt`) / `scripts/complexity.py` / `lint-imports` | 655 файлов, нарушений нет / 407 файлов, расхождений нет / 4 контракта | 0 / 0 / 0 |
| jscpd (хук DRY) | 55 пар при снимке 55 | 0 |
| `alembic heads` | одна голова — `739817077a57` (после `75242c2ed7ba`) | 0 |
| шаг образа (`docker_step_local.py`: читает промпты колеса, в том числе `reply_kind.load_prompt()`) | 1 шаг | 0 |
| фронт: `tsc --noEmit` / ESLint / Prettier | чисто | 0 / 0 / 0 |
| `check_irreversible_signature.sh` (черновик STATUS в `delivery/active/`, четыре строки — из STATUS базы) | подпись сходится | 0 |
| `check_baseline_ratchet.sh`, `BASE=aeb35ff` | baseline-ratchet: OK (15 снимков сверено с aeb35ff) | 0 |
| `OKF_BUNDLE=okf okf_sync_gate.py --base aeb35ff` | OK, понятий с `implementation:` нет — гейт инертен | 0 |
| `pre-commit run --all-files` | 27 Passed (07.10, 46 с, нагрузка 4,0, чужих прогонов не было) | 0 |

## Behavior oracles

- [x] PASS — тесты частей и соседей на голове `8bb3e29` (86 файлов: `test_sales_*`, `test_replies*`, `test_reply_*`,
      `test_thread*`, `test_agent_*`, `test_job_outcome`, `test_api_jobs`, `test_backup_*`, `test_schema`, `test_prune*`,
      `test_migrations_match_models`, `test_queue*`, `test_workers_health`, `test_reaper`, вебхук и приём, стоп-лист и
      отписка, `test_api_outreach`): **1908 passed**, exit 0.
- [x] PASS — vitest `src/settings src/api` (`--maxWorkers=2`): 6 файлов, 59 passed, exit 0.
- [x] PASS — **полный pytest с покрытием на `8bb3e29`** (07.10, `--cov=backend --cov-report=json:coverage.json`, старт
      21:59 при нагрузке 4,0, чужих прогонов не было): **5019 passed за 8:18**, exit 0 (список не пуст: весь `tests/`).
      **diff-coverage PR:** `SKIP_TESTS=1 STRICT=1 BASE=aeb35ff LINT_BE_DIR=. LINT_COV_PKG=backend LINT_PY_SRC=backend
      LINT_VENV=.venv bash scripts/lint/check_diff_coverage.sh` — exit 0, все 22 изменённых файла не ниже цели 70 %
      (минимум `sales/referral.py` 87,3 % и `workers/sales_jobs.py` 87,5 %). Дословно:

```
файл                                                                    stmts  miss   cov%
backend/api/inbound/routes.py                                             118     4   96.6
backend/config/llm.py                                                      36     0  100.0
backend/config/sales.py                                                    29     0  100.0
backend/features/core/usage.py                                             51     1   98.0
backend/features/ops/job_outcome.py                                        66     3   95.5
backend/features/outreach/threads.py                                       99     4   96.0
backend/features/replies/calibration.py                                    67     7   89.6
backend/features/replies/outcome.py                                       103     8   92.2
backend/features/replies/pipeline.py                                      196     6   96.9
backend/features/replies/repository.py                                    171    15   91.2
backend/features/sales/cleaning.py                                        228     1   99.6
backend/features/sales/models.py                                          110     0  100.0
backend/features/sales/ooo.py                                              72     0  100.0
backend/features/sales/referral.py                                         55     7   87.3
backend/features/sales/replies.py                                         165     3   98.2
backend/features/sales/reply_kind.py                                      127     5   96.1
backend/features/sales/unsubscribe.py                                      27     2   92.6
backend/migrations/versions/739817077a57_sales_lead_referred_from_thread.py     14     0  100.0
backend/shared/queue.py                                                   127     0  100.0
backend/workers/health.py                                                  70     3   95.7
backend/workers/main.py                                                    18     1   94.4
backend/workers/sales_jobs.py                                              56     7   87.5
```

### Красный прогон до кода и обратные прогоны

- **2.1:** на базе без части `tests/test_sales_reply_routing.py` не собирается; «имена есть, логика прежняя» — 8 из 22
  красные; правка `_next_try` — без неё 2 из 3 красные (`sales`, `crawl`).
- **2.2:** на голове 2.1 три файла тестов не собираются; зеркало реестра волн — 1 красный; подпись операции — без
  строки 1 из 10 красный; строка журнала — без правки `test_a1_job_with_the_default_handoff_survives_the_production_log_level`
  красный (`KeyError`).
- **2.3a:** на голове 2.2 все 8 тестов среза и решение ссылки в `test_prune.py` красные; неверная настройка проверки
  адресов — без ловли `ConfigError` красный; второй реестр чистки без строки — красный.
- **2.3b:** на голове 2.3a `test_sales_ooo_unsubscribe.py` не собирается, изменённые тесты — 6 красных; `suppress` без
  правки — 4 красных.
- **Мутанты — 26 из 26 убиты** (M1–M26 по ключевым правилам: очередь продаж, отказ доставки, флаг воркера, 503 и
  повтор, путь кодом, цитата, отказ модели не кэшируется, порог, маскирование, точка передачи, калибровка, ворота eval,
  «ждёт ли» из снимка, отписка во всех направлениях, автоответ, дата возвращения, сдвиг только вперёд, исходный
  диалог, очистка, «другое направление», назначенное, отписка правилами, «отписался»). Красные прогоны и мутанты —
  прежние, на ветке до переносов; код частей тот же, кроме правок переноса — их держат тесты выше.

## Исполнение рисковых путей

- `docker-compose.yml` с боевым наложением —
  `POSTGRES_PASSWORD=… ACCESS_JWT_SECRET=… docker compose -f docker-compose.yml -f docker-compose.prod.yml config --format json`
  на голове `8bb3e29`, at=2026-10-07T18:59Z, exit 0: 10 сервисов (`api crawler followups migrate postgres reaper redis
  web worker worker-sales`); `worker-sales` — команда `python -m backend.workers.main --queue sales`, проверка
  здоровья `python -m backend.workers.health sales`, `mem_limit` 1536 МиБ, `cpus` 1, `restart: unless-stopped`, зависит
  от `migrate`, `postgres`, `redis`, портов наружу нет; `WRITERS` у `scripts/restore.sh` — с `worker-sales`.
- Отказ на незнакомой очереди — `python -m backend.workers.main --queue nope` на `8bb3e29`: «invalid choice: 'nope'
  (choose from runs, sales)», exit 2 — до подключения к базе и Redis.
- Сам воркер `worker-sales` локально не поднимался: общий Redis — он забрал бы чужие задачи. Живой воркер с настоящей
  задачей — «заложено» до выкатки по слову владельца (скрипт выкатки: healthy 11 → 12, `worker-sales` в список
  образов).

## Изменение поверхности модели

Записано at=2026-10-07 по диффу PR; дифф задел `backend/config/llm.py` и `backend/features/sales/prompts/`, оба — в
первой строке `model_surface:`.

- **Промпт** — новый файл `backend/features/sales/prompts/reply_kind.md` (25 строк), версия `sales-reply-kind-v1`
  (пишется в снимок ответа); package-data и чтение в шаге CI.
- **Пин** — `LLM_SALES_CLASSIFY_MODEL` в `backend/config/llm.py`, по умолчанию `gpt-5`; прочие пины не тронуты.
- **Сэмплинг** — у рассуждающих моделей `reasoning_effort: minimal` и `max_completion_tokens` 1200, у прочих
  `temperature 0` и `max_tokens` 300.
- **Схема выхода** — `response_format: json_object`; читаются только `kind`, `confidence`, `quote`, `contact`; не-JSON и
  чужой вид — `parse_failed`.
- **Судья** — нет; свои проверки ниже модели — `temper` (цитата и адрес дословно из письма), путь — таблица кода `ROUTES`.
- **Чем судится** — механика ворот под тестом без сети и живой прогон координатора на синтетике (ниже). Пороги утвердил
  владелец 07.10: уверенность 0,8; полнота `wants_to_talk` ≥ 95 %; полнота `unsubscribe` 100 %; опасных 0.

## Живой прогон

Координатор, 07.10, синтетика 28 (`scripts/eval_sales_reply.py`, промпт `sales-reply-kind-v1`; запрос модели тот же):
`gpt-5` — вид верно 28 из 28, человеку 0, опасных 0, ложных отписок 0; `gpt-5-mini` — 28 из 28, человеку 1; порча
`--drop-kind wants_to_talk` — полнота `wants_to_talk` 0 %, ворота закрыты. Пин оставлен `gpt-5`; пересмотр — на наборе
владельца. Живых ответов продаж через вебхук нет, пока почта им отказывает — «заложено».

## Product oracles

- [x] PASS — `eval-smoke.md`: Q1–Q5, K1–K10, R1–R4, U1–U3 на настоящей базе и под vitest; живой eval на синтетике —
      прогон координатора; живой воркер и вебхук — «заложено».

## Ревью рисковых мест

- **деньги** — главный риск: ответ продаж не должен уйти в разбор цены и в карточку донора. Закрыт: в `Inbox._settle`
  `parse_pending` — только `outcome.priced_by_model(stage)` (доноры), `sales_pending` — только `Stage.SALES`; калибровка
  цены (`calibration.calibrate`) пропускает снимки продаж. Расход модели — `usage.ensure_llm_within_cap` до вызова (общий
  потолок; свой — только у черновиков агента), `usage.record(…, operation="sales_reply_kind", …)` с токенами; потолок —
  задача на начало следующих суток UTC. Падение задачи после вызова модели стёрло бы вид и расход и позвало модель на
  каждом повторе — отсюда правки `5a6a0e0` (журнал) и 2.3a (неверная настройка проверки адресов не роняет задачу).
- **безопасность** — письмо собеседника идёт в модель: письмо между `<<<EMAIL`/`EMAIL>>>`, метки в тексте погашены
  (`_quiet`), адреса — метками (`masking.mask`), утёкший — запроса нет; цитата и адрес из ответа модели обязаны найтись
  в письме (`temper`), иначе не хранятся — пересказ промпта при инъекции не попадает ни в снимок, ни в карточку.
  Подпись вебхука и секреты не тронуты; `worker-sales` берёт те же `env_file` и `environment`, что `worker`.
- **необратимое без человека** — отписка словами по виду модели закрывает адрес во всех направлениях: `close_address`
  — `suppress` без этапа, `stop_pending` (очередь и сроки любого направления), `stop_chain`, диалог `UNSUBSCRIBED`;
  вход по виду — только не ниже порога, неуверенная отписка ничего не закрывает; ворота eval «ложных отписок 0»; пункт —
  в общей строке необратимого продаж (подпись владельца в PR 5.3).
- **общий код ответов** — `suppress`: условие `forever = stage IS NULL AND expires_at IS NULL`; доноров меняет только
  в двух краях, в сторону docstring; уникальности на (email, stage) нет — конфликта вставки нет. `_next_try` — читает
  реестр отложенных очереди `job.origin`, только чтение.
- **транзакция БД** — постановка задачи после коммита ответа (`take_reply`); тело задачи — одна сессия и один
  `commit` после `SalesReplies.handle`; отказ модели — записка коммитится, затем исключение для повтора очереди;
  `refer` и `close_address` не коммитят сами.
- **интеграция** — внешний вызов один (`post_chat`, `Refusal` вместо исключения), `KindClient` и `httpx.AsyncClient`
  закрываются в `finally`; живая проверка адреса — только для «пишите другому», лениво; очередь rq `unique`,
  `DuplicateJobError` — второй не ставится, `RedisError` — 503; воркер со своим планировщиком.
- **производительность** — ответ модели не длиннее 1200 токенов, письмо — 20 000 знаков, выражения дат — `re.compile`
  на модуль, `.all()` — письма одного диалога и тесты; в `_load_known` к запросу пачками добавлен join `campaigns` —
  число запросов прежнее.
- **выкатка** — новый сервис `worker-sales`: healthy 11 → 12, `worker-sales` в список образов скрипта соседней сессии;
  выкатка по слову владельца.
- **консоль доменов рассылки** — правка закрывает тихий отказ доноров: новая строка пишущего домена больше не
  получает выдержку, чужой этап не записывается. Остаток риска: домен с ящиками двух этапов не заводится ни одним
  `--stage` — отказ словами с именами ящиков, оператор сначала разводит ящики; фильтр отправки и пачка не тронуты.
- **новый модуль** — `sales/replies.py`, `sales/reply_kind.py`, `sales/referral.py`, `sales/ooo.py`, `sales/unsubscribe.py`,
  `workers/sales_jobs.py`, `scripts/eval_sales_reply.py`, ревизия `739817077a57`.

## Предохранитель

`breakers:` PR против `aeb35ff` (правилом исключений `delivery_check`) — строка в выводе ниже. Сверх 25 файлов и 800
строк — четыре части и правка консоли одним PR по слову владельца; вейвер на размер — в STATUS PR, с числами головы
(files=45 net_loc=3880). Общая часть — 30 файлов, +688/−68.

## Предупреждения delivery_check, разобранные

Дословно (временное дерево на `8bb3e29`, черновики PR в `delivery/active/` — четыре строки из STATUS базы, как их
подставит сборка; `--require-ci --diff-base aeb35ff`), exit 1:

```
ERROR: circuit breaker: files_touched 43 > 25 — split the PR or add a human waiver line to STATUS (§3.4)
ERROR: circuit breaker: net loc_diff 3732 > 800 — split the PR or add a human waiver line to STATUS (§3.4)
breakers: files=43 net_loc=3732 (+3782/-50), excluded=3 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 800, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 2 error(s), 4 warning(s)
```

- «circuit breaker» — размер: четыре части одним PR, вейвер — у координатора.
- «`irreversible_surfaces:` не называет отправка наружу» — детектор видит маркеры отправки во всём продуктовом коде
  (письма доноров); предупреждение стоит и на STATUS main; строка подписана владельцем и переносится дословно.
- «asserts_reviewed_by deferred» — утверждения ждут подписи человека (дайджест ниже).
- «Ни одного реляционного оракула» — hypothesis и fast-check не в зависимостях проекта (новая зависимость — решение
  владельца); свойства держат параметризованные тесты (правило очереди, «ниже порога любой вид — человек», разбор даты).
- «Нет блока `agent-permissions`» в CONSTITUTION — было до PR.

## Проверка волн

Дословно (то же дерево, `--base aeb35ff`), exit 0:

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а deployed · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 27 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```

## Spec coverage gaps

- Q2 доказан устройством (свой процесс, своя очередь, тело задачи на базе); живой воркер рядом с прогоном —
  «заложено».
- K1: точка передачи — заглушка `mark_for_handoff`; передача 5.3 — два коммита стыка (ветка проверки
  `sales/2.x-5.3-wire`).
- `question` / `interested` — путь агента (Ф3); пока ответ ждёт человека с видом.
- Письмо лиду из «пишите другому» — очередь Ф4 (4.6b); живые автоответ, отписка и «пишите другому» через вебхук —
  «заложено» (писем продаж нет).

## Находки

1. **Строка журнала точки передачи с ключом `thread` роняла задачу на боевом уровне журнала — исправлено** (`5a6a0e0`).
2. **Сборка запроса модели повторяется** в модулях агента, разбора ответа, переписывания письма и судьи; снимок дублей
   main — 55 из 55: кандидат в общий помощник `shared/llm.py` отдельной правкой общего кода.
3. **Голова alembic базы — `75242c2ed7ba`** (ревизия автопилота шва поверх `3924977db911`): ревизия Ф2 перецеплена на
   неё; перед сливом — на голову main того дня.
4. **Пределы длины:** `replies/pipeline.py` 498/500, `replies/repository.py` 495/500, `sales/cleaning.py` 493/500.
5. **`replies.confidence` ответа продаж — уверенность вида**, а не цены (открытый вопрос владельцу).

## Verdict

Готово к ревью общего кода соседней сессией, в том числе правки консоли доменов рассылки — её обязательного пункта
к #219. Слив — после подписи владельцем строки необратимого: отписка словами (2.3b) закрывает адрес во всех
направлениях без человека, это первый PR с такой поверхностью; 5.3 и следующие возьмут строку из main уже
подписанной. Вейвер на размер и голова миграций — координатор; выкатка `worker-sales` (healthy 12) — по слову
владельца.

## Assertion digest (ревью ожиданий, не кода)

База: `aeb35ff` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **281**, из них без ссылки на пример спеки:
**277**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(operationTitle('sales_reply_kind')).toBe('разбор ответов продаж');
-	assert {w.name for w in waves if w.state == "deployed"} == {"В0", "В1", "В3а", "В-обн"}
-	assert outcome is not None
-	assert (outcome.state, outcome.next_try_at, outcome.error) == ("retry_wait", AT, "сеть")
-	assert await self._rows(session) == [("sales", "manual", True), ("—", "unsubscribed", True)]
-	assert await self._rows(session) == sorted(
-	assert await self._rows(session) == [("—", "unsubscribed", True)]
-	assert reply is not None
-	assert reply.kind is ReplyKind.AUTO_REPLY
-	assert letter.next_action_at == datetime(2026, 10, 14, tzinfo=UTC), "не раньше 14.10"
-	assert model.calls == 0, "автоответ модели не отдаётся"
-	assert "не раньше 14.10.2026 (дата возвращения из письма)" in str(handled.reason)
-	assert thread is not None
-	assert thread.status is ThreadStatus.OPEN, "автоответ цепочку не останавливает"
-	assert letter.next_action_at == NOW + timedelta(days=7)
-	assert letter.next_action_at == later
-	assert "сдвинуто сроков — 0" in str(handled.reason)
-	assert outcome.to_sales_queue(ReplyKind.AUTO_REPLY, Stage.SALES) is True
-	assert outcome.to_sales_queue(ReplyKind.AUTO_REPLY, Stage.DONORS) is False
-	assert outcome.to_sales_queue(ReplyKind.BOUNCE, Stage.SALES) is False
-	assert return_date(text, RECEIVED) == back
-	assert return_date("Back on 05.01", date(2026, 12, 28)) == date(2027, 1, 5)
A4	assert contact is not None
-	assert reply.kind is ReplyKind.UNSUBSCRIBE
-	assert model.calls == 0
-	assert await _suppressions(session) == [(f"ceo@{HOST}", None)], "стоп-лист без этапа"
-	assert (queued.status, queued.next_action_at) == (MessageStatus.STOPPED, None)
-	assert sent.next_action_at is None
-	assert thread is not None
-	assert thread.status is ThreadStatus.UNSUBSCRIBED
-	assert "адрес закрыт во всех направлениях" in str(handled.reason)
-	assert reply.kind is ReplyKind.HUMAN
-	assert (handled.route, handled.waits) == ("unsubscribe", False)
-	assert await _suppressions(session) == [(f"ceo@{HOST}", None)]
-	assert queued.status is MessageStatus.STOPPED
-	assert summarize(messages, [reply], Stage.SALES).state is ThreadState.UNSUBSCRIBED
-	assert (handled.route, handled.waits) == ("unsubscribe", False)
-	assert set(await _suppressions(session)) == {(address, Stage.SALES), (address, None)}
-	assert held == 1
-	assert (handled.route, handled.waits) == ("manual", True)
-	assert await _suppressions(session) == []
-	assert (words.kind, words.to_sales, words.to_parse) == (ReplyKind.HUMAN, None, words.reply_id)
-	assert outcome.to_sales_queue(ReplyKind.UNSUBSCRIBE, Stage.DONORS) is False
-	assert await _suppressions(session) == []
-	assert reply is not None
-	assert (lead.source, lead.status, lead.email) == (
-	assert (lead.domain_id, lead.hypothesis_id) == (origin.domain_id, origin.hypothesis_id)
-	assert lead.referred_from_thread_id == letter.thread_id
-	assert (lead.company, lead.country, lead.language) == ("Компания-пример", "de", "en")
-	assert lead.verification_status == "fixture:valid", "очистка — как у любого лида"
-	assert thread is not None
-	assert thread.status is ThreadStatus.CLOSED
-	assert (handled.route, handled.waits) == ("referral", False)
-	assert handled.reason == (
-	assert (review.waiting, review.reason) == (False, handled.reason)
-	assert len(await _leads(session)) == 1, "лида без адреса не выдумываем"
-	assert (handled.route, handled.waits) == ("referral", True)
-	assert "адреса в ответе нет" in str(handled.reason)
-	assert review_of(reply, Stage.SALES).waiting is True
-	assert await _leads(session) == []
-	assert handled.waits is True
-	assert f"лид исходного диалога не найден — завести лида {COLLEAGUE} руками" in str(
-	assert model.calls == 1, "повтор задачи модель не зовёт"
-	assert (first["route"], first["waits"]) == ("referral", True)
-	assert first["reason"] == (
-	assert again == {"reply": reply.id, "skipped": "уже разобран"}
-	assert (spent.operation, spent.units) == ("sales_reply_kind", 41), "расход не стёрт"
-	assert len(await _leads(session)) == 1, "лида без проверки адреса не заводим"
-	assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, RejectionReason.STOPLIST)
-	assert lead.cleaning_note == f"стоп-лист продаж: адрес {COLLEAGUE}"
-	assert thread is not None
-	assert thread.status is ThreadStatus.CLOSED
-	assert handled.waits is False
-	assert "отсеян очисткой — стоп-лист продаж" in str(handled.reason)
-	assert (lead.status, lead.rejection_reason) == (
-	assert colleague.status is LeadStatus.READY
-	assert colleague.rejection_reason == RejectionReason.OTHER_DIRECTION
-	assert await connection.run_sync(_cycle) == [
-	expect = ORACLE[text]
-	assert 20 <= len(cases) <= 30
-	assert len({case["id"] for case in cases}) == len(cases)
-	assert kinds == reply_kind.MODEL_KINDS
-	assert any("injection" in case["tags"] for case in cases)
-	assert address.endswith(".example"), case["id"]
-	assert case["expect"]["kind"] == "referral"
-	assert contact in case["text"], case["id"]
-	assert ev.main([]) == 0
-	assert "ОПАСНЫХ: 0" in out
-	assert f"Версия промпта: {reply_kind.PROMPT_VERSION}" in out
-	assert ev.main(["--drop-kind", "wants_to_talk"]) == 1
-	assert "ПОРЧА ПРОМПТА" in out
-	assert "полнота wants_to_talk 0% < 95%" in out
-	assert '- "wants_to_talk":' not in reply_kind.load_prompt()
-	call = next(text for text, expect in ORACLE.items() if expect["kind"] == "wants_to_talk")
-	assert ev.main([]) == 1
-	assert "опасных 1 > 0" in capsys.readouterr().out
-	question = next(text for text, expect in ORACLE.items() if expect["kind"] == "question")
-	assert ev.main([]) == 1
-	assert "ложных отписок 1 > 0" in capsys.readouterr().out
-	assert (shaky.dangerous, refused.dangerous) == (False, False)
-	assert refused.refusal == "модель не ответила: сеть"
-	assert ev.main(["--golden"], golden_dir="") == 1
-	assert "набор не найден" in capsys.readouterr().out
-	assert manifest["file"] == golden.name
-	assert ev.golden(str(tmp_path))  # манифест без хэша — набор берётся, хэш печатается
-	assert digest in capsys.readouterr().out
-	assert set(manifest) >= {"file", "count", "sha256", "date", "baseline", "gates"}
-	assert manifest["gates"] == {
A1	assert got.to_sales == got.reply_id is not None
A1	assert reply is not None
A1	assert thread is not None
-	assert (handled.kind, handled.route, handled.waits) == ("wants_to_talk", "handoff", True)
-	assert handover.threads == [reply.thread_id], "точка передачи лида вызвана"
-	assert snap["quote"] == "Давайте созвонимся во вторник"
-	assert snap["quote"] in text
-	assert (snap["stage"], snap["kind"], snap["route"]) == ("sales", "wants_to_talk", "handoff")
-	assert snap["prompt_version"] == reply_kind.PROMPT_VERSION
-	assert snap["model"] == "gpt-5"
-	assert reply.confidence == pytest.approx(0.93)
-	assert review.waiting is True
-	assert review.reason == "хочет говорить: передать лида на созвон; пока — человек"
-	assert await _state(session, reply) is ThreadState.SALES_PENDING
-	assert (spent.operation, spent.units, spent.provider) == (
-	assert (report["kind"], report["route"]) == ("wants_to_talk", "handoff")
-	assert any(getattr(r, "thread_id", None) == reply.thread_id for r in caplog.records)
-	assert (handled.kind, handled.route, handled.waits) == ("question", "agent", True)
-	assert handover.threads == []
-	assert review_of(reply, Stage.SALES).reason == "задал вопрос: ответит агент; пока — человек"
-	assert "marketing@company.example" not in json.dumps(model.requests), "адрес в модель не ушёл"
-	assert "[address 1]" in model.user
-	assert (snap["kind"], snap["contact"]) == ("referral", "marketing@company.example")
-	assert snap["quote"] == "пишите коллеге из маркетинга: marketing@company.example"
-	assert handled.route == "referral"
-	assert snap["contact"] is None
-	assert snap["confidence"] == 0.0
-	assert "названный адрес в письме не найден" in snap["notes"]
-	assert (handled.route, handled.waits) == ("manual", True)
-	assert (handled.kind, handled.route) == ("unsubscribe", "unsubscribe")
-	assert (reply.model_parse or {})["kind"] == "unsubscribe"
-	assert (handled.kind, handled.route, handled.waits) == ("parse_failed", "manual", True)
-	assert handled.tokens == 77, "вызов оплачен — расход записан"
-	assert "вид ответа не разобран" in str(handled.reason)
-	assert "ответ модели не разобран" in str(handled.reason)
-	assert review_of(reply, Stage.SALES).waiting is True
-	assert parse_form(content) is None
-	assert found is not None
-	assert found.confidence == 0.0
-	assert found.notes == ("модель не поставила себе оценку уверенности",)
-	assert model.system == reply_kind.load_prompt()
-	assert model.system.startswith(PROMPT_HEAD)
-	assert opened < model.user.index("Ignore previous instructions") < closed
-	assert model.user.count(reply_kind.CLOSE) == 1, "метка конца данных в письме погашена"
-	assert PROMPT_HEAD not in stored
-	assert PROMPT_HEAD not in str(handled.reason)
-	assert (handled.route, handled.waits) == ("manual", True)
-	assert (handled.kind, handled.route) == ("question", "agent")
-	assert len(model.requests) == 3, "повторы вызова модели исчерпаны"
-	assert handled.unanswered is not None
-	assert handled.unanswered.permanent is False
-	assert snap["kind"] is None, "отказ модели — не вид"
-	assert snap["refusal"].startswith("модель не ответила")
-	assert reply.confidence is None
-	assert review.waiting is True
-	assert "модель не ответила" in str(review.reason)
-	assert "задача попробует ещё раз" in str(review.reason)
-	assert (await session.execute(select(UsageRecordModel))).first() is None
-	assert (again.skipped, again.kind) == (None, "question")
-	assert (reply.model_parse or {})["kind"] == "question"
-	assert handled.unanswered is not None
-	assert (reply.model_parse or {})["kind"] is None
-	assert (reply.model_parse or {})["refusal"] == "модель не ответила: сеть"
-	assert remembered == [("j-1", "модель не ответила: сеть")]
-	assert (report["error"], report["permanent"]) == ("модель не ответила: ключ", True)
-	assert report["reason"] == "модель не ответила: ключ — разберите вручную"
-	assert model.requests == []
-	assert handled.unanswered is not None
-	assert handled.unanswered.permanent is True
-	assert (snap["confidence"], snap["quote"]) == (0.0, None)
-	assert "цитата модели в письме не найдена" in snap["notes"]
-	assert (handled.route, handled.waits) == ("manual", True)
-	assert thread.status is ThreadStatus.REPLIED, "без уверенности диалог не закрыт"
-	assert (handled.route, handled.waits) == ("agent", True)
-	assert (await _thread(session, reply)).status is ThreadStatus.REPLIED
-	assert (handled.route, handled.waits) == ("closed", False)
-	assert (await _thread(session, reply)).status is ThreadStatus.CLOSED
-	assert review.waiting is False
-	assert review.reason == f"{KIND_WORDS[SalesKind(kind)]}: диалог закрыт"
-	assert await _state(session, reply) is ThreadState.REPLIED
-	assert (decision.route, decision.waits) == (Route.MANUAL, True)
-	assert "уверенность 79% ниже порога 80%" in decision.reason
-	assert set(ROUTES) == set(SalesKind) == set(KIND_WORDS)
-	assert set(ROUTE_WORDS) == set(Route)
-	assert (first.kind, second.skipped) == ("question", "уже разобран")
-	assert model.calls == 1
-	assert isinstance(found, KindFound)
-	assert "Our offer" not in model.user, "цитата нашего письма снята"
-	assert "ceo@company.example" not in model.user
-	assert "[address 1]" in model.user
-	assert len(model.user) < 20_500
-	assert payload["response_format"] == {"type": "json_object"}
-	assert payload["model"] == "gpt-5"
-	assert payload["reasoning_effort"] == "minimal"
-	assert model.requests == []
-	assert isinstance(found, Unanswered)
-	assert found.permanent is True
-	assert model.requests == []
-	assert isinstance(found, KindFound)
-	assert found.kind is SalesKind.PARSE_FAILED
-	assert KindClient(api_key=KEY).model == llm_cfg.SALES_CLASSIFY_MODEL
-	assert OPERATION_PROVIDERS[reply_kind.OPERATION] is UsageProvider.LLM
-	assert 0.0 < sales_cfg.REPLY_CONFIDENCE <= 1.0
-	assert reply_kind.PROMPT_PATH.name == "reply_kind.md"
-	assert model.calls == 0
-	assert when == datetime(2026, 10, 7, tzinfo=UTC)
-	assert args == (queue.SALES_REPLY_JOB, reply.id)
-	assert options["job_id"].endswith("-after-cap-20261007")
-	assert report["postponed_until"] == "2026-10-07T00:00:00+00:00"
-	assert reply.kind is ReplyKind.HUMAN
-	assert reply_kind.PROMPT_VERSION not in versions
-	assert outcome.sales_review(snapshot) == (waits, reason)
-	assert response.status_code == 200, response.text
-	assert reply.kind is ReplyKind.HUMAN
-	assert queues[queue.QUEUE_NAME].jobs == [], "разбора цены нет"
-	assert (job, args) == (queue.SALES_REPLY_JOB, (reply.id,))
-	assert options["job_id"] == queue.sales_job_id(reply.id, MESSAGE_ID)
-	assert options["unique"] is True
-	assert options["retry"].max == len(queue.RETRY_INTERVALS)
-	assert (body["needs_review"], body["reason"]) == (True, outcome.SALES_WAITING)
-	assert summary.state is ThreadState.SALES_PENDING
-	assert summary.price_white is None
-	assert (got.to_sales, got.to_parse) == (got.reply_id, None)
-	assert (got.sales_pending, got.parse_pending) == (True, False)
-	assert got.as_report["продажам"] == got.reply_id
-	assert letter.next_action_at is None, "ответил человек — добивок нет"
-	assert thread is not None
-	assert thread.status is ThreadStatus.REPLIED
-	assert outcome.to_sales_queue(kind, stage) is to_sales
-	assert response.status_code == 200, response.text
-	assert [job for job, _, _ in queues[queue.QUEUE_NAME].jobs] == [queue.PARSE_JOB]
-	assert queues[queue.SALES_QUEUE_NAME].jobs == []
-	assert started == {"queues": listens, "with_scheduler": True}
-	assert built.name == queue.SALES_QUEUE_NAME == "sales"
-	assert built._default_timeout == queue.JOB_TIMEOUT
-	assert queue.sales_job_id(5, MESSAGE_ID) == f"sales-{queue.parse_job_id(5, MESSAGE_ID)}"
-	assert service["command"] == "python -m backend.workers.main --queue sales"
-	assert service["healthcheck"]["test"][-1] == "sales"
-	assert "deploy" not in service, "один процесс воркера"
-	assert compose["services"]["worker"]["command"] == "python -m backend.workers.main"
-	assert "mem_limit: 1536m" in limits
-	assert "cpus: 1.0" in limits
-	assert " worker-sales " in writers
-	assert health.main(["sales"]) == 0
-	assert asked == ["sales"]
-	assert capsys.readouterr().out.strip() == "здоров"
-	assert got.reply_id is not None
-	assert report == {
-	assert job_outcome.KINDS[queue.SALES_REPLY_JOB] == "разбор ответа продаж"
-	assert (await sales.handle(10**9)).skipped == "ответа нет: удалён до разбора"
-	assert donor_reply.reply_id is not None
-	assert (await sales.handle(donor_reply.reply_id)).skipped == "не ответ продаж"
-	assert reply is not None
-	assert (await sales.handle(reply.id)).skipped == "решён человеком"
-	assert model.calls == 0, "чужой ответ модели не отдаётся"
-	assert first.status_code == 503, first.text
-	assert "разбор ответа продаж не поставлен" in first.json()["reason"]
-	assert sales.jobs == []
-	assert (again.status_code, third.status_code) == (200, 200)
-	assert again.json()["duplicate"] is True
-	assert [(job, args) for job, args, _ in sales.jobs] == [(queue.SALES_REPLY_JOB, (reply.id,))]
-	assert queues[queue.QUEUE_NAME].jobs == []
-	assert again.status_code == 200
-	assert queues[queue.SALES_QUEUE_NAME].jobs == []
-	assert response.status_code == 200, response.text
-	assert reply.kind is ReplyKind.AUTO_REPLY
-	assert [args for _, args, _ in queues[queue.SALES_QUEUE_NAME].jobs] == [(reply.id,)]
-	assert queues[queue.QUEUE_NAME].jobs == []
-	assert model.calls == 0
-	assert letter.next_action_at is not None, "автоответ цепочку не останавливает"
-	assert got.kind is ReplyKind.BOUNCE
-	assert (got.to_sales, got.to_parse) == (None, None)
-	assert letter.status is MessageStatus.BOUNCED
```

Привязаны к примерам: **A1 A4**. Остальные 277 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 277

## Проверки на голове PR

Ветка перенесена на main `554f7d5`; голова кода `5d285be`, проверки — по одному разу, после переноса и правки консоли. Полный pytest на голове 2.3b у агента — 5019 passed; полный pytest PR — в CI.
Числа выше — прогоны агента на его ветке (тот же код среза).

| Проверка | Итог | exit |
|---|---|---|
| `alembic heads` | 739817077a57 (head) | 0 |
| ратчет сложности | Ратчет сложности: 415 файлов, расхождений со снимком нет. | 0 |
| pytest среза и соседей с покрытием | 1737 passed in 129.30s (0:02:09) | 0 |
| `STRICT=1 check_diff_coverage.sh` (BASE — main) | покрытие изменённых файлов ≥ 70 % | 0 |
| `pre-commit run --all-files` | ни одного Failed | 0 |
| `check_baseline_ratchet.sh` | baseline-ratchet: OK (15 снимков сверено с origin/main) | 0 |
| `check_irreversible_signature.sh` | подпись строки необратимого сошлась | 0 |
| `contour_waves --base origin/main` | ниже, дословно | 0 |
| `delivery_check --require-ci --diff-base origin/main` | `breakers: files=45 net_loc=3880 (+3953/-73)` | 0 |
| `assert_digest.sh` | `asserts_without_example: 286` | 0 |
| `tsc` / `eslint` / `prettier` | чисто | 0 / 0 / 0 |
| vitest раздела (`--maxWorkers=2`) | Tests  59 passed (59) | 0 |

Дословно (`delivery_check --require-ci --diff-base origin/main`, голова `6d50fa8`):

```
breakers: files=45 net_loc=3880 (+3953/-73), excluded=11 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 45, 'max_loc_diff': 3880, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
WARNING: необратимое без человека: `irreversible_surfaces:` не называет отправка наружу (§3.4a) — детектор видит это в коде. Либо назови, либо объясни в той же строке, почему это не необратимо
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: class M: ни одного реляционного оракула (§6.5) — не найдено ни `@given` (hypothesis), ни `fc.property` (fast-check). Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур контролирует выход и молчит про действия агента
delivery_check: 0 error(s), 4 warning(s)
```

Дословно (`contour_waves --base origin/main`, голова `6d50fa8`):

```
contour-waves: CI — нарушение красное
волны: В0 deployed · В1 deployed · В3а deployed · В3б pending · В3в pending · В4 pending · В2 pending · В-обн deployed
в дереве продаж: 28 файл(ов)
○ В3б: промпт агента и судьи назовёт срез агента — пока волну судит человек
○ В3в: промпт судьи сегмента назовёт его срез — пока волну судит человек
○ В4: маркер автоотправки назовёт срез В4 — пока волну судит человек
○ В2: файл порогов агента назовёт срез агента — пока волну судит человек
contour-waves: нарушений нет
```
