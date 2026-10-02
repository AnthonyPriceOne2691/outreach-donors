# Verify report

**Date:** 2026-10-02
**Verifier:** process:ci — обязательные `check`, `web`, `docker`, на PR ещё `gates` и `delivery`; до пуша — локальные прогоны ниже
**asserts_reviewed_by:** deferred reason=4 утверждения без примера спеки ждут подписи человека — путь «почта → реестр моделей → продажи» при обоих значениях `allow_indirect_imports` (`test_mail_may_read_the_model_registry_that_lists_sales_models`, 3) и предусловие помощника `_v1` (1); человека в цепочке агента нет — подписывает владелец при ревью, дайджест в конце отчёта
**CI run:** нет — ветка не запушена. Она стоит стеком на ветке #135: пуш, PR и прогон CI — после слияния #135 и перебазирования на main, делает координатор
**Commit:** T4 — прогоны сняты на дереве T3 (`1b7fbba`), T4 меняет только документы среза; T5 — правка проверки волн, её прогоны точечные, раздел «T5» ниже

## Shape oracles

- [x] PASS — `pre-commit run --all-files`: 27 хуков, 27 прошли, 0 упавших,
      exit 0.
- [x] PASS — гейт слоёв `scripts/lint/check_layers_gate.sh`: python —
      просмотрено 298 файлов, TS — 109 модулей, exit 0. `lint-imports` —
      4 контракта целы, 0 сломано, «Analyzed 298 files», exit 0.
- [x] PASS — `ruff check backend/ tests/ scripts/` (как CI) — exit 0;
      `ruff format --check` — 468 файлов, exit 0; `mypy backend/` — 298 файлов,
      exit 0; `scripts/gates.py` — 464 файла, нарушений нет, в том числе
      `public-repo`, exit 0; `scripts/complexity.py` — 312 файлов,
      расхождений со снимком нет, exit 0.
- [x] PASS — DRY-гейт `check_jscpd_gate.sh`: 45 пар клонов при снимке 45
      (387 файлов), exit 0. Гейт сложности функций — 317 файлов
      в области pre-commit и 478 в области CI, exit 0.
- [x] PASS — `python scripts/contour_waves.py --base sales/1.1a-model` (режим
      CI, база PR стеком): exit 0, «В1 deployed», «нарушений нет».
      До T3 тот же прогон давал exit 1: «В1: в базе PR уже есть
      backend/features/sales/, волна не развёрнута».
- [x] PASS — `delivery_check.py --require-ci --diff-base sales/1.1a-model`:
      0 errors, 2 warnings, exit 0; предупреждения разобраны ниже.
      Breakers на T4: файлов 4, net_loc 179 (+180/−1) при пределах 25 и 800;
      класс S — до 5 файлов и 200 строк. С T5 — 5 файлов и 223 строки,
      разбор — в разделе «T5».
- [x] PASS — `scripts/check_irreversible_signature.sh`: подпись сходится,
      exit 0.

`contour_waves --base origin/main` с этой ветки даёт exit 1, и это свойство
стека, а не среза. Дифф с main несёт ещё и #135, и тринадцать общих файлов
#135 (`backend/cli/main.py`, `core/domain.py` и другие) этот STATUS не
объявляет: их объявлял STATUS 1.1a, он в архиве. Волны в том же прогоне
зелёные: «В1 deployed». После слияния #135 и перебазирования дифф с main —
только этот срез.

## A2 — что видит каждый гейт

Число файлов `backend/features/sales/`, просмотренных каждым гейтом на дереве
T3. Считал сам гейт своим способом выбора файлов: ноль означал бы, что гейт
смотрит мимо продаж. В `sales/` три файла: `__init__.py`, `hypotheses.py`,
`models.py`.

| Гейт | Как считал | Всего | `sales/` |
|---|---|---|---|
| ruff check (как CI) | `--show-files` | 464 | 3 |
| ruff format --check (как CI) | `-v`, строки `format_path` | 468 | 3 |
| mypy `backend/` | `--linecount-report` | 298 | 3 |
| `scripts/gates.py` | его обход `_python_files` | 464 | 3 |
| `scripts/gates.py`, `public-repo` | `_tracked_text_files` | 827 | 3 |
| `lint-imports` | граф `grimp.build_graph("backend")` — тот же, что строит гейт | 298 | 3 |
| ратчет сложности `scripts/complexity.py` | `measure()` | 312 | 3, все в снимке |
| гейт сложности функций | `git ls-files` области и ruff с его правилами | 174 / 335 | 3 / 3 |
| DRY, jscpd | JSON-отчёт с флагами гейта (`-k 50`, без тестов) | 293 | 3, клонов 0 |
| diff-coverage, база `origin/main` (дифф несёт #135) | таблица гейта | 10 | 3: 100 %, 100 %, 100 % |
| diff-coverage, база `sales/1.1a-model` (дифф среза) | таблица гейта | 0 | 0 |
| гейт слоёв, TS | правило `mail-does-not-know-sales` | 109 | 0 — экранов продаж нет |

- Гейт сложности функций: 174 — область pre-commit (`backend/features`),
  335 — область CI (`backend`).
- diff-coverage: проценты — от прогона тестов продаж, а не всего сьюта:
  второй полный прогон на перегруженной машине не делали. В том же отчёте
  `backend/cli/main.py` — 42,1 %, это артефакт узкого прогона: на всём сьюте
  его держат тесты #139.
- На базе среза гейт сказал: «Python-файлов в диффе нет, изменено 2
  prod-файла — `.dependency-cruiser.cjs`, `.importlinter` — этот гейт их не
  мерит». Покрытие их и не меряет: оба конфига судит сам гейт слоёв, их
  проверка — обратные прогоны ниже.
- Половине TS гейта слоёв судить пока нечего: каталога `frontend/src/sales/`
  нет. Правило ловит подложенный импорт — строка в таблице обратных прогонов.

## Behavior oracles

- [x] PASS — `tests/test_sales_boundary.py`: 10 тестов. A1 — импорт продаж,
      подложенный в каждый из четырёх пакетов почты на копии дерева, краснит
      гейт слоёв (4); копия без импорта — зелёная, просмотрено больше нуля
      файлов (1); без контракта тот же импорт проходит (1). Импорт реестра
      моделей из почты — зелёный при `allow_indirect_imports = True`,
      красный при `False` (2). A3 — реестр называет В1 развёрнутой, улика
      держится (1), без контракта проверка волн красная (1).
- [x] PASS — `tests/test_contour_waves.py`: 25 тестов, с T5 — 28; зеркало реестра —
      с В1 среди развёрнутых.
- [x] PASS — полный набор на своей базе `outreach_test_998450dfa8`
      (`TEST_STORAGE_DSN` не задан): 3181 passed за 441 с, exit 0.
- [x] PASS — фронт не тронут: дифф по `frontend/` с базой ветки пуст.
      eslint, prettier, `tsc` — exit 0.
- [ ] Весь vitest — красный под нагрузкой машины (load average 16–32):
      2 failed из 442, повтор — 1 failed. Упал
      `src/donors/DonorPage.test.tsx`: заголовок не появился за 1,5 с. Тот же
      файл отдельно с `--maxWorkers=2` — 11 из 11. Полный vitest повторно не
      гоняли по просьбе координатора: фронт срезом не тронут.

### Обратные прогоны

| Что сломано | Ожидание | Факт |
|---|---|---|
| новые тесты на конфиге до T1 (`cb1aa7e`) | красные | 7 failed, 1 passed — зелёным остался только положительный контроль |
| реестр до T3: В1 `pending` | A3 и зеркало красные | 3 failed |
| `backend.features.contacts` убран из `source_modules` | A1 для `contacts` красный | 1 failed, 9 passed — ровно `[contacts]` |
| файлом: контракт убран, импорт продаж подложен | гейт зелёный — красное даёт этот контракт | тест зелёный, гейт exit 0 |
| файлом: `allow_indirect_imports = False`, почта импортирует реестр моделей | гейт красный цепочкой `core.models → sales.models` | тест зелёный, гейт exit 1 |
| файлом: `.importlinter` без контракта, комментарий о нём оставлен | проверка волн: В1 без улики | тест зелёный: «В1: в .importlinter нет контракта с backend.features.sales» |
| живьём: `from backend.features.sales import models` в `backend/features/letters/planted_sales.py` | гейт слоёв красный | exit 1: контракт BROKEN, `backend.features.letters.planted_sales -> backend.features.sales.models`; после возврата exit 0 |
| живьём: `frontend/src/letters/probeSales.ts` импортирует `frontend/src/sales/probe.ts` | половина TS красная | exit 1: `error mail-does-not-know-sales: frontend/src/letters/probeSales.ts → frontend/src/sales/probe.ts`, 111 модулей |
| живьём: экран продаж читает почту — `sales/readsMail.ts` → `letters/letterText.ts` | зелёный — направление разрешено | exit 0, 111 модулей; после возврата — 109 |

Каждую порчу вносили по одной и возвращали. Дерево после прогонов — как
в коммитах: `git status` пуст.

## Product oracles

- [x] PASS — `active/eval-smoke.md`: четыре пункта, все отмечены.

## Ревью рисковых мест

Срез меняет только `.importlinter`, `.dependency-cruiser.cjs`, реестр волн
и тесты — путей исполнения продукта в нём нет. По классам, которые поднял
детектор:

- **цепочка гейтов** — `scripts/lint/check_layers_gate.sh` тест зовёт как
  есть, скрипт не меняется; меняются конфиги, которые он читает. Риск —
  красное у соседей: контракт `mail-does-not-know-sales` краснит любой прямой
  импорт продаж из `letters`, `outreach`, `replies`, `contacts`. Это и есть
  цель, а сегодня таких импортов ноль. Контракт цел на дереве, где слиты
  #133, #134 и #139, а в почте свежего main (`9937261`, с #137 и #143)
  `git grep` не находит ни одного упоминания `features.sales`. Импорт
  реестра моделей `core.models` из почты контракт пропускает — держит тест.
- **транзакция БД** — риска нет, потому что `commit` в диффе — это слово
  `pre-commit` в докстринге `tests/test_sales_boundary.py`. Базы срез не
  трогает: тесты гейта в неё не ходят.
- **производительность** — риска нет, потому что `re.search` стоит только
  в тесте: он разбирает текст `.importlinter` и вывод гейта, по разу на тест.
  Десять тестов файла идут 2,5 с, копия `backend/` — во временной папке pytest.
- **новый модуль** — `test_sales_boundary`: тестовый модуль без состояния;
  копии дерева живут во временных папках pytest и в дерево не пишут.

## Предупреждения delivery_check, разобранные

- «`irreversible_surfaces:` не называет отправка наружу» — было у В0, 1.1a
  и `front-polish`: строка подписана и перенесена дословно. На handoff это
  ошибка; дополненную строку владелец переподпишет позже.
- «Нет блока `agent-permissions`» в CONSTITUTION — было до среза.
- «Рисковый дифф: не разобраны транзакция БД, производительность, цепочка
  гейтов» — было до блока ревью выше; после него ушло. Итог: 0 errors,
  2 warnings — два пункта выше.
- `asserts_reviewed_by: deferred` у класса S проверка не судит, но подпись
  всё равно нужна: шесть утверждений ждут человека — перечень в шапке,
  строки в дайджесте ниже.

## Spec coverage gaps

- Правило dependency-cruiser пока не судит ничего: экранов продаж нет. Его
  обратный прогон — живьём, без файла: тест с пропуском без `node_modules`
  был бы зелёным на непроверенном в джобе `check` (урок К L73).
- diff-coverage в диффе самого среза судит ноль файлов `sales/`: кода продаж
  срез не меняет. Число больше нуля — на базе с кодом продаж.

## T5 — улика В1 читает парсер ini

Находка №2 ниже — наш собственный скрипт волны В0, его починили в этом же
срезе по решению координатора. `scripts/contour_waves.py`: улику В1 проверяет
`forbids_sales` — `.importlinter` разбирает `configparser`, и засчитывается
только секция `importlinter:contract:…`, где `backend.features.sales` стоит
в `forbidden_modules`. Комментарий со словами модуля уликой больше не
считается. Битый конфиг тоже не улика, и проверка говорит об этом вслух.

- [x] Красный до правки — `test_v1_evidence_is_a_contract_not_a_comment`
      в `tests/test_contour_waves.py`, три случая, на старом скрипте:
      2 failed, 1 passed. Красные — «контракт удалён, комментарий со словами
      модуля остался» и «продажи — источник контракта, а не запрет»; зелёный —
      настоящий контракт, он контрольный.
- [x] PASS — после правки `pytest tests/test_contour_waves.py
      tests/test_sales_boundary.py`: 38 passed; оба теста A3 — зелёные.
- [x] PASS — ратчет сложности: длина скрипта 460 → 479 строк, снимок
      `delivery/complexity-snapshot.json` поднят ровно в этой строке; худшая
      функция прежняя — `collect`, сложность 14.
- [x] PASS — `pre-commit run --files` по семи файлам T5: 6 прошли,
      21 без файлов для проверки, exit 0; `ruff check` и `ruff format --check`
      по `scripts/`, `tests/`, `backend/` — exit 0; `scripts/gates.py` —
      464 файла, нарушений нет.
- [x] PASS — на коммите T5: `contour_waves --base sales/1.1a-model` — exit 0,
      «нарушений нет»; `delivery_check --require-ci --diff-base
      sales/1.1a-model` — 0 errors, 3 warnings, exit 0; подпись необратимого —
      exit 0. Полный сьют не гоняли по просьбе координатора: его сделает
      pre-push при перебазировании.
- Breakers: 5 файлов, net 223 (+229/−6). `delivery_check` предупреждает
  «class S, а тронуто 5 файлов / 223 строк — класс занижен?». Класс оставлен
  S: работа та же — граница модуля, а 44 строки сверх неё — правка улики
  этой же волны по решению координатора.

## Находки в общем коде — не чинились

- `.dependency-cruiser.cjs:16–17` и `:23–24`: канонные правила
  `models-is-leaf` (`^src/models`) и `services-no-web` (`^src/services`) не
  совпадают ни с одним модулем. Гейт зовёт depcruise из корня, модули
  называются `frontend/src/…`, а каталогов `models/` и `services/` во фронте
  нет. Живой прогон: `frontend/src/models/probe.ts` с импортом
  `../api/client` — гейт зелёный, exit 0. Из старых правил судит только
  `no-circular`.
- ~~`scripts/contour_waves.py:124` и `:273–275`: улика В1 — регулярное
  выражение по всему тексту `.importlinter`, включая комментарии~~ —
  исправлено в T5, раздел выше.
- `.pre-commit-config.yaml:242`: хук `layers-gate` зовёт гейт без
  `LINT_VENV`. Локально половину python судит первый `lint-imports` в PATH:
  на машине разработки это глобальный 2.13, а не 2.15 из `.venv`; без него —
  пропуск с предупреждением. В CI `LINT_VENV` задан на уровне джобы.

## Verdict

- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Мерж — в фазе verify, решение координатора и владельца; PR — после слияния
#135 и перебазирования на main.

## Assertion digest (ревью ожиданий, не кода)

База: `sales/1.1a-model` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **20**, из них без ссылки на пример спеки:
**4**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
A3	assert got == code
A3	assert ("✗ В1: в .importlinter нет контракта с backend.features.sales" in out) is (code == 1)
A3	assert {w.name for w in waves if w.state == "deployed"} == {"В0", "В1", "В-обн"}
A3	assert found is not None, "в .importlinter нет контракта mail-does-not-know-sales"
A1	assert code == 1, out
A1	assert f"{CONTRACT} BROKEN" in out
A1	assert f"backend.features.{package}.planted -> backend.features.sales" in out
A1	assert code == 0, out
A1	assert seen is not None, out
A1	assert int(seen.group(1)) > 0
A1	assert code == 0, out
A1	assert "слои (python): OK" in out
-	assert "allow_indirect_imports = True" in section
-	assert got == code, out
-	assert ("backend.features.core.models -> backend.features.sales.models" in out) is (code == 1)
-	assert errors == []
A3	assert wave.state == "deployed"
A3	assert "tests/test_sales_boundary.py" in cw.evidence_paths(wave.evidence)
A3	assert cw.judge([wave], tree) == []
A3	assert found == [
```

Привязаны к примерам: **A1 A3**. Остальные 4 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 4
