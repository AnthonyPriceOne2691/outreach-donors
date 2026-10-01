# Реестр волн контура модуля «Продажи»

Модуль наследует контур репозитория, но не его полноту: граница модуля,
поведение модели на каждом промпте, минимум эксплуатации и понятия OKF
приходят волнами. У каждой волны два условия:
- **триггер** — «волна нужна, когда…»;
- **предел** — «не позже чем…».

Оба — идентификаторы детекторов из `scripts/contour_waves.py` (таблица
`DETECTORS`), то есть события в дереве, а не даты: дату машина не проверит.

**Непришедшая волна — строка с состоянием и причиной, а не тишина.** Состояния:
`deployed`, `pending`, `weak` / `absent` / `n/a reason=…`. Порядок осей
① → ② → ③ волны не переставляют; ⑤ — до мержа ветки с моделью. Каждая
волна — отдельный срез и отдельный PR, не вперемешку с фичей.

Проверка: `python scripts/contour_waves.py --base origin/main`. В CI она
красная, на pre-push (`--warn`) — только предупреждение. Без файлов
`backend/features/sales/` — зелёная и говорит это словами.

| Волна | Ось | Триггер | Предел | Состояние | Доказательство |
|---|---|---|---|---|---|
| В0 | ① delivery модуля: непреложное и проверка волн | `sales-code` | `this-pr` | deployed | `delivery/CONSTITUTION.md` (раздел «Продажи»), `scripts/contour_waves.py`, `tests/test_contour_waves.py` |
| В1 | ② граница модуля + ④ гейт мержа | `sales-code` | `sales-in-base` | pending | — |
| В3а | ⑤ разбор ответов | `sales-prompt`, `sales-llm` | `this-pr` | pending | — |
| В3б | ⑤ агент и судья | `sales-agent-prompt` | `this-pr` | pending | — |
| В3в | ⑤ судья сегмента | `sales-segment-prompt` | `this-pr` | pending | — |
| В4 | ⑥ минимум эксплуатации | `sales-autosend` | `this-pr` | pending | — |
| В2 | ③ понятия продаж с `implementation:` | `manual` | `sales-thresholds` | pending | — |
| В-обн | проект вровень с каноном | `canon-ahead` | `manual` | deployed | #123: delivery@1.95 · cqg@2.43 · okf@1.19 |

## Что значат детекторы

- `sales-code` — в дереве PR есть `backend/features/sales/`.
- `sales-in-base` — `backend/features/sales/` есть уже в базе PR: так читается
  «мерж второго PR продаж».
- `this-pr` — предел «мерж этого PR»: нарушение — уже само срабатывание триггера
  в дереве PR.
- `sales-prompt` — файл `backend/features/sales/**/prompts/*.md`.
- `sales-llm` — `.py` продаж импортирует клиент модели `backend.shared.llm`.
- `sales-agent-prompt`, `sales-segment-prompt`, `sales-autosend`,
  `sales-thresholds` — имя файла или маркер назовёт срез, который их приносит;
  до тех пор волну судит человек, и проверка печатает это при каждом прогоне
  с кодом продаж.
- `manual` — решает человек: например, что инварианты продаж застыли (В2).
- `canon-ahead` — канон рядом (`~/Documents/Prepare`) ушёл вперёд от записи
  в `delivery/STACK-ACCEPTANCE.md`; видно только на pre-push, в CI канона нет.

## Что ещё требует развёрнутая волна

- Пути из колонки «Доказательство» существуют.
- В1 — в `.importlinter` есть контракт с `backend.features.sales`.
- В2 — у понятий `okf/` есть `implementation:` в `backend/features/sales`.
- Волна с триггером `sales-prompt` — каждый промпт продаж назван
  в `model_surface` STATUS.
