# Active delivery status

- **slug:** sales-kommo (модуль «Продажи», срез 5.2 — клиент Kommo `fixture`/`live`: сделка с контактом и компанией, примечание)
- **stack:** delivery@1.99 · cqg@2.45 · okf@1.19 · stack-map@1.52
- **class:** M
- **kind:** feature
- **phase:** verify
- **builder:** agent:claude
- **verifier:** process:ci — обязательные джобы `check`, `web` и `docker`, на PR ещё `gates` и `delivery`; ревью общих точек — соседняя сессия outreach-donors, мерж — по решению владельца
- **human_ok_spec:** yes at=2026-10-01 by=human:anthony («даю да» — план фаз с примерами среза 5.2 A1–A5; модульный срез продаж под постоянный вейвер владельца 05.10)
- **waivers:** max_loc_diff=1716 reason=модульный срез продаж, постоянный вейвер владельца 05.10; общая часть около 40 строк (.env.example, tests/conftest.py, .secrets.baseline); из строк среза почти половина — тесты by=human:anthony
- **new_dependency:** no (httpx уже в зависимостях)
- **shared_changes:** срез трогает файлы вне масок `SALES_PATHS`:
  `backend/config/sales.py` — настройки `SALES_KOMMO_PROVIDER`, `SALES_KOMMO_SUBDOMAIN`, `SALES_KOMMO_TOKEN`, `SALES_KOMMO_PIPELINE_ID`, `SALES_KOMMO_STATUS_ID`, `SALES_KOMMO_RESPONSIBLE_USER_ID`, константы `KOMMO_RATE_PER_SEC` и `KOMMO_TIMEOUT_SEC` (модуль конфига продаж, как `SALES_VERIFIER_PROVIDER` в 1.4);
  `.env.example` — шесть переменных `SALES_KOMMO_*`, пустые, режим `fixture`;
  `tests/conftest.py` — автофикстура `_no_real_kommo` рядом с `_no_real_alerts`: весь набор на `fixture` без ключа (требование Spec 5.2);
  `.secrets.baseline` — сдвиг номера строки известной записи `tests/conftest.py` (370 → 380) и метка времени, хуком detect-secrets;
  `delivery/complexity-snapshot.json` — три новых модуля и рост `backend/config/sales.py`.
  Общие `backend/shared/net/retry.py` (`delay_for`, `RETRY_STATUSES`, `MAX_DELAY_SEC`), `backend/config/startup_checks.py` (`ConfigError`), `backend/features/runs/failures.py` — только читаются. Согласовано: с сессией outreach-donors (список общих файлов отправлен до PR, 05.10)

## Оракулы

- **shape-oracles:** cqg-deployed — ruff и формат, mypy, `scripts/gates.py`, ратчет сложности, гейт слоёв (import-linter), jscpd, хуки pre-commit, detect-secrets
- **behavior-oracles:** tests-present — `tests/test_sales_kommo.py` (89): A1–A5, повторы и пауза Kommo, запись без повтора после отправки, форма ответа, ключ не в адресе, тексте и журнале, частота, fixture, фабрика, алиасы настроек; сеть — только `httpx.MockTransport`
- **ci-oracles:** deployed — обязательные `check`, `web`, `docker`; quality — `gates` и `delivery`; шаг волн контура — в `check`
- **artifact_oracle:** n/a reason=новых точек входа и сборки нет: модуль библиотечный, зовёт его 5.3
- **runtime_paths:** none reason=клиент никто не зовёт до 5.3 (передача лида); живой Kommo — только явной настройкой `SALES_KOMMO_PROVIDER=live`, после доступа и переподписи в 5.3, первая запись — на тестовой воронке (5.5)
- **rule_enforcers:** n/a reason=срез не трогает модель: в `sales/` нет ни промптов, ни вызовов модели; поверхность модели продукта прежняя, строка ниже — слово в слово
- **stack-selftest:** external (`~/Documents/Prepare`) — вариант D: каноны лежат в корне
  ЛОКАЛЬНО и в коммит не идут (`.git/info/exclude`), поэтому в CI их физически нет и
  `stack_selftest.py` проверять нечего. §7.1 требует записать это, а не умолчать:
  иначе инвариант «payload соответствует канону» выглядит покрытым, не будучи покрыт
  ничем. Сверка снимка с upstream делается перевендориванием из репозитория канона.
- **model_surface:** `backend/features/keywords/prompts/` (guides · news · reviews · topics) — вызовы модели живут в продукте, а не в срезе. Пины из `backend/config/llm.py`: `LLM_KEYGEN_MODEL` (деф. gpt-5), `LLM_JUDGE_MODEL` (деф. gpt-5-mini), `LLM_LETTERS_MODEL` (деф. gpt-5). ⚠ Объявление исправлено при развёртывании контура 30.09: стояло «none — модель в срезе не вызывается», и это было верно про СРЕЗ и неверно про ПРОДУКТ — доктор поймал расхождение первым же прогоном (`поверхность модели`, DEAD)
- **irreversible_surfaces:** отправка писем донорам, и без человека в цепочке — добивки уходят по расписанию фоновым процессом; страница отписки без пропуска — нажатие постороннего пишет в стоп-лист, снимает письма с очереди и гасит сроки; публикация образов в публичный реестр при каждом слиянии в main; приём ответов вебхуком; трата юнитов Ahrefs и платных провайдеров; публичный репозиторий; автомерж по зелёному; **обход чужих живых сайтов нашим трафиком — чужие машины и наша репутация по IP, отозвать сделанные запросы нельзя**; **письма рекламодателям с доменов Этапа 2 — оффер незваным адресатам: первое уходит по нажатию человека, добивки по расписанию без человека, жалоба бьёт по репутации доменов Этапа 2 и не отзывается**; **копия базы вне машины — по расписанию и перед каждой выкаткой, без человека, дамп с перепиской и адресами уходит в стороннее хранилище (R2 или B2), тексты тревог — в Telegram; отправленное не отзывается**

Четыре строки — `stack:`, `stack-selftest:`, `model_surface:` и `irreversible_surfaces:` —
перенесены из STATUS main (`3695a7f`) слово в слово.

**Необратимое — подпись в 5.3 (план Ф5).** Запись во внешнюю CRM — Kommo (сделки, контакты, компании,
примечания; из сервиса не отзывается) становится достижимой в 5.3 — там клиент зовёт передача лида.
В этой части клиент никем не зовётся, а `live` включается только явной настройкой, поэтому строка
`irreversible_surfaces:` не меняется и переподпись не нужна. Формулировка для переподписи в 5.3:
«запись в Kommo при `SALES_KOMMO_PROVIDER=live` — сделки, контакты, компании и примечания в CRM,
где с ними работают люди; из сервиса не отзывается; первая живая — на тестовой воронке».


## Чего в срезе нет

- Передачи лида (5.3), Telegram, таблицы передач, тревог.
- Поиска и слияния компаний: компания заводится с каждой сделкой (открытый вопрос).
- Живого прогона: доступа к Kommo нет; первая живая запись — на тестовой воронке после
  переподписи (5.5).
