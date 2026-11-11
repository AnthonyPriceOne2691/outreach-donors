"""Метка судьи в «кто решил»: решение по совету судьи отличимо от решения человека.

Совет судьи рекламодателей, применённый решением (`crawl/judge_apply.py`, решение владельца
10.11.2026), ложится в те же колонки, что решение человека: `confirmed`, `decided_by`, `decided_at`.
Отличает их только автор — `судья:<партия>`: по нему решение видно на экране как решение судьи,
партия находится и откатывается, а замер доли ложных его не считает (`crawl/accuracy.py`) — иначе
судья мерил бы сам себя.

Метка едет вместе с решением через пересчёт и повтор обхода (`CARRIED`, `crawl/gate.py`): строки
кандидатов обход заводит заново, и номер строки партию не удержал бы.

Своим модулем, без импортов из соседей: метку читают и замер (`accuracy.py`), и применение
(`judge_apply.py`), а применение идёт через очередь проверки, которая читает замер, — импорт друг у
друга замкнул бы круг.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, false, func
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.core.models.advertiser import CandidateModel

#: Автор решения по совету судьи — начало «кто решил». Почта человека с него не начинается: в почте
#: нет двоеточия до «@».
JUDGE = "судья"
PREFIX = f"{JUDGE}:"

#: Партия: полоса, дата и время начала (UTC) и четыре случайных знака — `skip-20261111-093015-a3f9`.
#: Полоса в имени: партия одной полосы не продолжается другой, и по строке базы видно, что это было.
BATCH = re.compile(r"^(skip|write)-\d{8}-\d{6}-[0-9a-f]{4}$")


def marker_of(batch: str) -> str:
    """«Кто решил» у решений партии: `судья:<партия>` — влезает в колонку (128 знаков)."""
    return f"{PREFIX}{batch}"


def batch_of(decided_by: str | None) -> str | None:
    """Партия из «кто решил»; решал не судья — `None`."""
    if decided_by is None or not decided_by.startswith(PREFIX):
        return None
    return decided_by.removeprefix(PREFIX)


def started_at(batch: str) -> datetime:
    """Когда партия начата — из её имени (UTC). Имя не той формы или с невозможной датой —
    `ValueError`: что с ним делать, решает вызывающий (`judge_apply.band_of` — отказ словами)."""
    if BATCH.fullmatch(batch) is None:
        raise ValueError(f"имя партии не той формы: {batch!r}")
    return datetime.strptime(batch.split("-", 1)[1][:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def by_judge(
    column: InstrumentedAttribute[str | None] = CandidateModel.decided_by,
) -> ColumnElement[bool]:
    """Решение поставил судья — условием базы. Пустое «кто решил» — не судья, а не «неизвестно»:
    условие годится под отрицание."""
    result: ColumnElement[bool] = func.coalesce(column.startswith(PREFIX, autoescape=True), false())
    return result
