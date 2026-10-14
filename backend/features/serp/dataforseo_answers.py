"""Ответы DataForSEO: принятые и отклонённые задачи, позиции выдачи, ход покупки.

Вынесено из адаптера (`dataforseo.py`), когда к отложенной очереди прибавились
приоритетная и синхронный запрос (14.10.2026): разбор ответов у всех трёх режимов
один, а запросы и ожидание — свои. Здесь — то, что читает ответ провайдера и
ничего не спрашивает у сети.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.features.serp.protocol import SerpProgress, SerpResult, SerpWatch

logger = logging.getLogger(__name__)

#: Код задачи «принята» и код «готово». Всё остальное — отказ, и его
#: надо называть, а не считать пустой выдачей.
TASK_CREATED = 20100
TASK_OK = 20000
#: Задача ещё в работе — законное состояние, ждём дальше.
TASK_IN_QUEUE = (40601, 40602)


class SerpError(RuntimeError):
    """Провайдер выдачи отказал или ответил непонятным. `permanent` — повтор
    не поможет (ключ, права, страна); без признака — временный сбой."""

    permanent: bool = False

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent or type(self).permanent


def organic_results(items: Sequence[Any], wanted: int) -> list[SerpResult]:
    """Органические позиции из ответа.

    Выдача содержит и карты, и «люди также спрашивают», и рекламу.
    Доноры — только органика; всё остальное пропускается, но пропуск
    не считается ошибкой, это устройство выдачи.
    """
    out: list[SerpResult] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "organic":
            continue
        url, position = item.get("url"), item.get("rank_absolute")
        if not isinstance(url, str) or not isinstance(position, int):
            continue
        # Заголовок и описание берём здесь же: провайдер отдаёт их в каждой
        # органической позиции, отдельного запроса они не стоят, а без них
        # судья площадки не по чему судить (`donors/publisher_judge.py`).
        title = item.get("title")
        description = item.get("description")
        out.append(
            SerpResult(
                position=position,
                url=url,
                title=title if isinstance(title, str) and title.strip() else None,
                description=(
                    description if isinstance(description, str) and description.strip() else None
                ),
            )
        )
        if len(out) >= wanted:
            break
    return out


def task_items(task: dict[str, Any]) -> tuple[list[Any], int | None]:
    """Позиции готовой задачи и сколько страниц провайдер собрал.
    Пустой результат — законный исход: провайдер отработал и ничего не нашёл."""
    result = task.get("result")
    if not result:
        return [], None
    first = result[0] or {}
    pages = first.get("pages_count")
    return list(first.get("items") or []), pages if isinstance(pages, int) else None


def live_items(body: dict[str, Any], keyword: str) -> tuple[list[Any], int | None]:
    """Позиции из ответа синхронного запроса. Отклонённая задача — пустая выдача
    с записью в журнал, как в отложенном режиме: ключ остаётся в ответе."""
    tasks = body.get("tasks") or []
    if not tasks or not isinstance(tasks[0], dict):
        raise SerpError(f"Ответ по ключу «{keyword}» не разобран: нет задач")
    task = tasks[0]
    if task.get("status_code") != TASK_OK:
        logger.error(
            "выдача: ключ «%s» отклонён (%s %s)",
            keyword,
            task.get("status_code"),
            task.get("status_message"),
        )
        return [], None
    return task_items(task)


def log_task(keyword: str, cost: float | None, pages: int | None) -> None:
    """Цена и страницы по задаче — чтобы сверить смету с ценой провайдера."""
    logger.info(
        "выдача: «%s» — страниц %s, $%s",
        keyword,
        "?" if pages is None else pages,
        "?" if cost is None else f"{cost:.4f}",
    )


def sort_posted(
    tasks: Sequence[Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, float]]:
    """Разложить ответ постановки: принятые («ключ → номер»), отклонённые
    («ключ → причина») и цены задач («ключ → $»).

    Вынесено из постановки задач: там собиралась и просьба, и разбор
    ответа, и решение об отказе — три темы в одной функции.
    """
    created: dict[str, str] = {}
    refused: dict[str, str] = {}
    costs: dict[str, float] = {}

    for task in tasks:
        if not isinstance(task, dict):
            # Строка без ключа: в ответе её не к чему привязать — причина под пустым ключом.
            refused[""] = f"строка ответа не разобрана: {task!r:.60}"
            continue
        keyword = str((task.get("data") or {}).get("tag") or "")
        cost = task.get("cost")
        if isinstance(cost, int | float):
            costs[keyword] = float(cost)
        if task.get("status_code") == TASK_CREATED and task.get("id"):
            created[keyword] = str(task["id"])
        else:
            refused[keyword] = f"{task.get('status_code')} {task.get('status_message')}"

    return created, refused, costs


@dataclass(slots=True)
class Ticker:
    """Счёт хода покупки: «началась» — один раз, дальше — по ключу с ответом."""

    watch: SerpWatch
    total: int
    done: int = 0
    started: bool = False

    async def start(self) -> None:
        if not self.started:
            self.started = True
            await self.watch.progress(SerpProgress(done=self.done, total=self.total))

    async def answered(self, keyword: str, *, cost: float | None, pages: int | None) -> None:
        self.done += 1
        await self.watch.progress(
            SerpProgress(
                done=self.done, total=self.total, keyword=keyword, cost_usd=cost, pages=pages
            )
        )


async def report_refused(
    refused: dict[str, str], costs: dict[str, float], ticker: Ticker, *, none_taken: bool
) -> None:
    """Отклонённые задачи — в журнал и в ход покупки; отклонены все — отказ.

    Отказ провайдера — не пустая выдача. Промолчав, мы получили бы прогон, где
    половина ключей «ничего не нашла». Ключ отклонённой задачи — ответ: ждать
    по нему нечего, и ход покупки его считает.
    """
    said = [f"{keyword}: {reason}" if keyword else reason for keyword, reason in refused.items()]
    logger.error("выдача: провайдер отклонил задачи (%d): %s", len(refused), said[:5])
    for keyword in refused:
        if keyword:
            await ticker.answered(keyword, cost=costs.get(keyword), pages=None)
    if none_taken:
        raise SerpError(f"Провайдер отклонил все задачи пачки: {said[0]}")
