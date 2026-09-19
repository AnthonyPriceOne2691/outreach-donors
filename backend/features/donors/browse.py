"""Чтение базы доноров: таблица с фильтрами и карточка.

Отдельно от `repository.py`, который пишет: у чтения другие требования —
фильтры, страницы, соединения с контактами и переписки. Смешав их,
получаем модуль, в котором правка выборки задевает запись.

Одно правило отражено прямо в структуре: **«не проверен» и «не подходит» —
разные состояния.** Донор без данных Ahrefs не отсеян, его надо добрать
позже; поэтому фильтр по статусу их различает, а не сводит в «не годен».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel


class UnknownDonorError(ValueError):
    """Донора с таким номером нет."""


@dataclass(frozen=True, slots=True)
class DonorFilters:
    """Чем сужают таблицу. Пустое поле — «не сужать»."""

    status: DonorStatus | None = None
    search: str | None = None
    min_dr: int | None = None
    has_contact: bool | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class DonorRow:
    """Строка таблицы доноров."""

    donor: DonorModel
    host: str
    contacts: int
    fresh: bool


@dataclass(frozen=True, slots=True)
class DonorPage:
    """Страница таблицы и общее число — без него фильтр не с чем сравнить."""

    rows: list[DonorRow]
    total: int


@dataclass(frozen=True, slots=True)
class DonorCard:
    """Карточка донора: сам донор, его адреса и срок годности метрик."""

    donor: DonorModel
    host: str
    contacts: Sequence[ContactModel]
    fresh: bool
    expires_at: datetime | None


def _is_fresh(donor: DonorModel, *, now: datetime) -> bool:
    """Свежие данные — те, за которые уже заплачено и платить снова не надо."""
    if donor.metrics_refreshed_at is None:
        return False
    return donor.metrics_refreshed_at > now - timedelta(days=filters_cfg.METRICS_TTL_DAYS)


def _expires_at(donor: DonorModel) -> datetime | None:
    if donor.metrics_refreshed_at is None:
        return None
    return donor.metrics_refreshed_at + timedelta(days=filters_cfg.METRICS_TTL_DAYS)


class DonorBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _narrow(self, statement: Select[Any], filters: DonorFilters) -> Select[Any]:
        if filters.status is not None:
            statement = statement.where(DonorModel.status == filters.status)
        if filters.min_dr is not None:
            statement = statement.where(DonorModel.dr >= filters.min_dr)
        if filters.search:
            needle = f"%{filters.search.strip().lower()}%"
            statement = statement.where(
                or_(DomainModel.host.ilike(needle), DonorModel.reject_reason.ilike(needle))
            )
        if filters.has_contact is not None:
            condition = DonorModel.contact_status == ContactStatus.FOUND
            statement = statement.where(condition if filters.has_contact else ~condition)
        return statement

    async def page(self, filters: DonorFilters) -> DonorPage:
        base = select(DonorModel, DomainModel.host).join(
            DomainModel, DomainModel.id == DonorModel.domain_id
        )
        rows = await self._session.execute(
            self._narrow(base, filters)
            .order_by(DonorModel.dr.desc().nullslast(), DonorModel.id.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )
        found = rows.all()

        counted = await self._session.execute(
            self._narrow(
                select(func.count(DonorModel.id)).join(
                    DomainModel, DomainModel.id == DonorModel.domain_id
                ),
                filters,
            )
        )
        contacts = await self._contact_counts([donor.domain_id for donor, _ in found])

        now = datetime.now(UTC)
        return DonorPage(
            rows=[
                DonorRow(
                    donor=donor,
                    host=host,
                    contacts=contacts.get(donor.domain_id, 0),
                    fresh=_is_fresh(donor, now=now),
                )
                for donor, host in found
            ],
            total=counted.scalar_one(),
        )

    async def card(self, donor_id: int) -> DonorCard:
        rows = await self._session.execute(
            select(DonorModel, DomainModel.host)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DonorModel.id == donor_id)
        )
        found = rows.first()
        if found is None:
            raise UnknownDonorError(f"Донора №{donor_id} нет")

        donor, host = found
        contacts = await self._session.execute(
            select(ContactModel)
            .where(ContactModel.domain_id == donor.domain_id)
            .order_by(ContactModel.last_replied_at.desc().nullslast(), ContactModel.id)
        )
        return DonorCard(
            donor=donor,
            host=host,
            contacts=contacts.scalars().all(),
            fresh=_is_fresh(donor, now=datetime.now(UTC)),
            expires_at=_expires_at(donor),
        )

    async def counts_by_status(self) -> dict[DonorStatus, int]:
        """Сводка над таблицей. «Не проверен» показывается отдельным числом
        намеренно: ступень, которая ничего не отсеивает, иначе выглядит
        работающей."""
        rows = await self._session.execute(
            select(DonorModel.status, func.count(DonorModel.id)).group_by(DonorModel.status)
        )
        return dict(rows.tuples().all())

    async def _contact_counts(self, domain_ids: Sequence[int]) -> dict[int, int]:
        if not domain_ids:
            return {}
        rows = await self._session.execute(
            select(ContactModel.domain_id, func.count(ContactModel.id))
            .where(ContactModel.domain_id.in_(domain_ids))
            .group_by(ContactModel.domain_id)
        )
        return dict(rows.tuples().all())
