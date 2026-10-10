"""Второй такой же прогон, пока первый не закончен, — отказ (аудит 10.10.2026, №2).

Двойной щелчок или двое людей ставили два одинаковых прогона. Единственный воркер шёл
ими по очереди, и второй покупал ту же выдачу ещё раз, а до потолка на старте задачи
(`budget.ceiling_at_start`) — ещё и тратил юниты из уже съеденного остатка месяца.

**Такой же — по смыслу, а не по байтам.** Ключи сравниваются набором: без порядка,
повторов, регистра и пробелов по краям — выдача у таких списков одна. Страна — без
регистра. Глубина — часть запроса: глубже — другая покупка. Потолок юнитов не в счёт:
тот же список с другим потолком — та же выдача. Отказ — только пока первый в очереди
или идёт: закончился — повтор законен, свежие домены он не оплатит второй раз.

**Проверка и запись — под замком.** Два нажатия, пришедшие разом, оба прочли бы
«такого нет» раньше, чем любое зафиксирует свою строку. Рекомендательная блокировка
Postgres на транзакцию запуска ставит их в очередь: второй ждёт фиксации первого
и видит его прогон. Замок снимается фиксацией или откатом — сам, без уборки.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.run import RunModel
from backend.features.runs.repository import RunRepository

#: Ключ замка запуска — «RUNSTART» байтами. Один на все запуски: они редки и коротки.
START_LOCK = 0x52554E5354415254


class DuplicateRunError(RuntimeError):
    """Такой же прогон ещё не закончен. Текст называет его номер и что делать."""


def _keyset(keywords: Iterable[str]) -> frozenset[str]:
    """Ключи как набор: без порядка, повторов, регистра и пробелов по краям."""
    return frozenset(key.strip().casefold() for key in keywords if key.strip())


async def refuse_duplicate(
    session: AsyncSession, *, keywords: Sequence[str], country: str, depth_pages: int
) -> None:
    """Отказ, если такой же прогон стоит в очереди или идёт. Берёт замок запуска до конца
    транзакции: зовётся до записи своего прогона, в той же транзакции."""
    await session.execute(select(func.pg_advisory_xact_lock(START_LOCK)))
    rows = await session.scalars(
        select(RunModel)
        .where(
            RunModel.stage == Stage.DONORS,
            RunModel.status.in_(RunRepository.ACTIVE_STATUSES),
            func.lower(RunModel.country) == country.lower(),
            RunModel.depth_pages == depth_pages,
        )
        .order_by(RunModel.id)
    )
    wanted = _keyset(keywords)
    twin = next((run for run in rows if _keyset(run.keywords) == wanted), None)
    if twin is not None:
        raise DuplicateRunError(
            f"Такой же прогон №{twin.id} ещё не закончен: те же ключи, страна и глубина. "
            "Второй купил бы ту же выдачу ещё раз — дождитесь конца первого или поменяйте ключи"
        )
