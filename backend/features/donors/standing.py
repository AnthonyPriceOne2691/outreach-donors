"""Кто донор: домен, принятый человеком, — и больше никто.

Решение 26.09.2026. Запись в `donors` появляется у каждого домена, за чьи
метрики прогон заплатил Ahrefs, — подходит он по порогам, не подходит или
не проверен. Это память о покупке: метрики живут `METRICS_TTL_DAYS`,
и повторный прогон их не покупает. Решением «берём» она не является.
Прогон кончается не базой доноров, а очередью (`okf/run-review.md`):
донором домен становится, когда его принимает человек на рассмотрении
прогона. Так же устроено и в соседней системе — донор заводится в момент
принятия, кандидаты и отказы лежат отдельно.

Экран «Доноры», его счётчики, выгрузка и плитки главной называют донором
только принятого. Иначе одно слово значило бы на одном экране «проверенный
домен», а на другом — «тот, кому пишем», и «доноров 1 065» стояло бы рядом
с нулём принятых.

Правило здесь одно: условием для запросов (`is_donor`) и тем же условием
для одной записи (`donor_now`). Таблица `donors` при этом не меняется —
меняется только то, что видно.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.review.candidates import Decision


def is_donor() -> ColumnElement[bool]:
    """Условие «донор» для запроса по `donors`. Решение человека — последнее
    по всем прогонам (`donors.review`, его пишет `review.candidates`)."""
    return DonorModel.review == Decision.ACCEPTED.value


def donor_now(donor: DonorModel) -> bool:
    """То же правило для одной записи."""
    return donor.review == Decision.ACCEPTED.value


@dataclass(frozen=True, slots=True)
class Waiting:
    """Кто ждёт решения человека — путь к первым донорам."""

    #: Доменов ждут решения — каждый один раз, даже в очередях двух прогонов:
    #: решение человек принимает о домене, а не о прогоне.
    domains: int
    #: Прогоны, в очередях которых они ждут, новые первыми.
    runs: list[int]


async def waiting(session: AsyncSession) -> Waiting:
    """Сколько доменов ждёт решения и в очередях каких прогонов.

    Одно правило для главной («Рассмотреть домены») и для пустого экрана
    доноров: число на главной и число в подсказке «рассмотреть» совпадают.
    Разобранная очередь в список прогонов не попадает.
    """
    domains = await session.scalar(
        select(func.count(func.distinct(RunCandidateModel.domain_id))).where(
            RunCandidateModel.status == Decision.PENDING.value
        )
    )
    runs = await session.execute(
        select(RunCandidateModel.run_id)
        .where(RunCandidateModel.status == Decision.PENDING.value)
        .group_by(RunCandidateModel.run_id)
        .order_by(RunCandidateModel.run_id.desc())
    )
    return Waiting(domains=int(domains or 0), runs=[int(one) for one in runs.scalars().all()])


async def decided_in(session: AsyncSession, domain_id: int, review: str | None) -> int | None:
    """Прогон, в очереди которого о домене решают или решили.

    Не решали — самый новый прогон, где домен ждёт; решали — прогон
    последнего решения (перенесённая копия решением не считается, как
    и у `review.candidates._settle_donor`). Туда ведёт карточка кандидата:
    решение принимают в очереди прогона, а не в карточке.
    """
    statement = select(RunCandidateModel.run_id).where(RunCandidateModel.domain_id == domain_id)
    if review is None:
        statement = statement.where(RunCandidateModel.status == Decision.PENDING.value).order_by(
            RunCandidateModel.run_id.desc()
        )
    else:
        statement = (
            statement.where(RunCandidateModel.status == review)
            .where(RunCandidateModel.carried.is_(False))
            .order_by(
                RunCandidateModel.decided_at.desc().nullslast(), RunCandidateModel.run_id.desc()
            )
        )
    found = await session.scalar(statement.limit(1))
    return None if found is None else int(found)
