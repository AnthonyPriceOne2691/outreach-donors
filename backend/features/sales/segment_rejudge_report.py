"""Отчёт пересуда прогона — ключ `rejudge` в `run.report` (без миграции): ход и «было → стало».

Формат один у пишущего (пересуд, `segment_rejudge.py`, и запуск — `api/sales/segment_sites.py`) и
у читающих (список сайтов прогона — `segment_sites.py`, экран):

- `state` — поставлен, идёт, кончился, остановлен (`error` — почему, словами);
- `sites` — снимок до пересуда: номер кандидата → [полоса до, полоса после]; «после» пусто —
  пересуд до сайта ещё не дошёл; `human` — решил человек посреди пересуда;
- `would` — у сайтов с лидом: какой стала бы полоса, почему и цитата (сама строка не меняется);
- числа: решения человека, «ещё не судили», токены (всего и раздельно: ввод, вывод,
  рассуждение, «раздельно неизвестно» — как у судьи прогона), «модель не ответила», сбои.

Пишется одной SQL-правкой своего ключа (`||`): прочие ключи отчёта прогона (выдача, судья, адреса)
остаются как есть, даже если их пишет кто-то ещё.

**Сумма всех пересудов прогона** — своим ключом `rejudges` (`add_totals`): блок `rejudge` хранит
последний пересуд и следующим заменяется, а расход прогона (`segment_spend`) — за все пересуды:
сколько их начиналось и токены — всего и раздельно. Копится на каждом чекпоинте пересуда той же
транзакцией, что блок, — повтор задачи не считает дважды.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import literal

from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_window import SPLIT_TOKENS

Run = SalesSegmentRunModel

#: Ключ пересуда в отчёте прогона.
KEY = "rejudge"
#: Состояния пересуда: поставлен, идёт, кончился, остановлен с причиной.
QUEUED, GOING, DONE, STOPPED = "queued", "going", "done", "stopped"
#: Сайт решил человек посреди пересуда — полосы у него нет, решение его.
HUMAN_KEPT = "human"
#: Сумма всех пересудов прогона в отчёте прогона.
TOTALS = "rejudges"
#: Токены пересуда, которые идут в сумму, — всего и раздельно, как у судьи прогона.
SPENT = ("tokens", *SPLIT_TOKENS)


@dataclass(frozen=True, slots=True)
class Change:
    """Сайт, сменивший полосу при пересуде, — или сменивший бы: у сайта с лидом полоса та же."""

    was: str
    now: str
    would: bool = False
    #: У «стало бы» — слова и цитата нового вердикта: строка кандидата их не хранит.
    reason: str | None = None
    quote: str | None = None


def block_of(report: dict[str, Any] | None) -> dict[str, Any]:
    """Пересуд из отчёта прогона; пусто — пересуда не было."""
    found = (report or {}).get(KEY)
    return dict(found) if isinstance(found, dict) else {}


def left_of(block: dict[str, Any], digest: str) -> list[int] | None:
    """Незаконченный пересуд того же описания — номера сайтов его снимка, до которых он не дошёл;
    `None` — продолжать нечего, и следующий пересуд — новый со своим снимком: прошлый закончен,
    описание другое (отпечаток не тот), пересуд не начинался (снимка нет) или все сайты пройдены.

    Незаконченный — «идёт» (воркер умер посреди) или «остановлен» (потолок модели, отказ посреди
    работы): его снимок хранит «было» у уже пересуженных сайтов — это и есть калибровка, и новый
    снимок с уже сменённых полос его бы потерял, а пересуженные сайты снова пошли бы к модели."""
    if block.get("state") not in (GOING, STOPPED) or block.get("digest") != digest:
        return None
    left = [int(key) for key, (_, now) in (block.get("sites") or {}).items() if now is None]
    return left or None


def changes(report: dict[str, Any] | None) -> dict[int, Change]:
    """Сайты последнего пересуда, сменившие полосу (или сменившие бы): номер кандидата → что
    было и что стало."""
    block = block_of(report)
    would = block.get("would") or {}
    found: dict[int, Change] = {}
    for key, (was, now) in (block.get("sites") or {}).items():
        if now is None or now in (was, HUMAN_KEPT):
            continue
        detail = would.get(key)
        if detail is None:
            found[int(key)] = Change(was, now)
        else:
            found[int(key)] = Change(was, now, True, detail.get("reason"), detail.get("quote"))
    return found


def summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Пересуд для экрана: ход и «было → стало» числами, без списков по сайтам."""
    block = block_of(report)
    if not block:
        return None
    sites = block.get("sites") or {}
    moved: Counter[tuple[str, str, bool]] = Counter(
        (change.was, change.now, change.would) for change in changes(report).values()
    )
    return {
        "state": block.get("state"),
        "by": block.get("by"),
        "asked_at": block.get("asked_at"),
        "started_at": block.get("started_at"),
        "finished_at": block.get("finished_at"),
        "total": len(sites),
        "done": sum(1 for _, now in sites.values() if now is not None),
        "human": sum(1 for _, now in sites.values() if now == HUMAN_KEPT),
        "kept_human": int(block.get("kept_human", 0)),
        "pending": int(block.get("pending", 0)),
        "moved": [
            {"was": was, "now": now, "would": would, "count": count}
            for (was, now, would), count in sorted(moved.items(), key=lambda item: -item[1])
        ],
        "tokens": int(block.get("tokens", 0)),
        # Раздельно — как у судьи прогона (`segment_window.JudgeTally`): у вывода своя цена.
        **{key: int(block.get(key, 0)) for key in SPLIT_TOKENS},
        "unanswered": int(block.get("unanswered", 0)),
        "failed": int(block.get("failed", 0)),
        "error": block.get("error"),
    }


async def write_block(session: AsyncSession, run_id: int, block: dict[str, Any]) -> None:
    """Пересуд — в отчёт прогона одной SQL-правкой своего ключа: прочие ключи отчёта (выдача,
    судья, адреса) остаются как есть, даже если их пишет кто-то ещё. Коммит — за вызывающим."""
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(report=Run.report.op("||")(literal({KEY: block}, type_=JSONB)))
        .execution_options(synchronize_session=False)
    )


def _count(value: object) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def totals_of(report: dict[str, Any] | None) -> dict[str, int]:
    """Сумма всех пересудов прогона: сколько начиналось (`count`) и токены (`SPENT`); пересудов не
    было — нули."""
    found = (report or {}).get(TOTALS)
    found = found if isinstance(found, dict) else {}
    return {key: _count(found.get(key)) for key in ("count", *SPENT)}


async def add_totals(
    session: AsyncSession,
    run_id: int,
    *,
    spent: dict[str, int] | None = None,
    started: bool = False,
) -> None:
    """В сумму всех пересудов прогона: начатый пересуд — счётом, чекпоинт — токенами с прошлого.
    Читается и пишется в транзакции блока пересуда: пересуд прогона идёт один (номер задачи — от
    прогона), а проход прогона в это время не идёт (`api/sales/segment_sites`). Коммит — за
    вызывающим."""
    current = await session.scalar(select(Run.report[TOTALS]).where(Run.id == run_id))
    totals = totals_of({TOTALS: current})
    totals["count"] += int(started)
    for key, value in (spent or {}).items():
        totals[key] += value
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(report=Run.report.op("||")(literal({TOTALS: totals}, type_=JSONB)))
        .execution_options(synchronize_session=False)
    )
