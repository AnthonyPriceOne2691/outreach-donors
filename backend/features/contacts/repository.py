"""Хранение исхода поиска контакта.

Два запроса, и оба про идемпотентность. `pending_hosts` отбирает доноров,
по которым за контактом ещё не ходили или ходили давно, — иначе повторный
запуск платил бы за уже пройденные домены. `save` пишет и адрес, и исход
попытки вместе с отметкой времени: без отметки нельзя отличить «не нашли»
от «ещё не искали» (okf/unchecked-vs-unsuitable.md).

Адрес принадлежит домену, а не донору: на Этапе 2 тот же сайт выступает
рекламодателем, и второй раз его контакт искать незачем.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel

#: Исходы, которые повторяются при следующем прогоне: мы не спросили, а не
#: узнали, что контакта нет.
RETRIABLE = frozenset({ContactStatus.NO_QUOTA, ContactStatus.RATE_LIMITED, ContactStatus.ERROR})


class ContactQueue(Protocol):
    """Очередь на поиск контакта.

    Лестница одна на оба этапа, а очередей две: доноры и рекламодатели.
    Протокол ровно поэтому — чтобы у поиска был один порядок работы,
    а не два, разъезжающихся на первой правке.
    """

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        """Кому пора искать контакт."""
        ...

    async def save(self, results: Sequence[LadderResult]) -> int:
        """Сохранить исходы. Возвращает число доменов с адресом."""
        ...


async def domain_ids(session: AsyncSession, hosts: Sequence[str]) -> dict[str, int]:
    """Номера доменов по хостам. Общее у обеих очередей."""
    rows = await session.execute(
        select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(list(hosts)))
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def save_addresses(
    session: AsyncSession, results: Sequence[LadderResult], ids: dict[str, int]
) -> int:
    """Записать найденные адреса. Общее у обеих очередей: адрес
    принадлежит домену, а не роли, в которой он выступает."""
    payload = [
        {"domain_id": ids[r.host], "email": r.contact.email, "source": r.contact.source}
        for r in results
        if r.contact is not None and r.host in ids
    ]
    if not payload:
        return 0

    statement = insert(ContactModel).values(payload)
    # Тот же адрес на том же домене — не ошибка: его мог найти прошлый
    # прогон. Обновляем источник: он мог стать дешевле.
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["domain_id", "email"],
            set_={"source": statement.excluded["source"]},
        )
    )
    return len(payload)


class ContactRepository:
    """Доступ к контактам доноров."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pending_hosts(
        self,
        *,
        limit: int = 100,
        ttl_days: int = cfg.CONTACT_TTL_DAYS,
        now: datetime | None = None,
    ) -> list[str]:
        """Подходящие доноры, которым пора искать контакт.

        Берём тех, по кому попытки не было вовсе, чья попытка устарела или
        кончилась нехваткой квоты. Домены с найденным адресом и свежим
        отказом не трогаем — за них уже заплачено.
        """
        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)

        rows = await self._session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DonorModel.status == DonorStatus.SUITABLE)
            .where(
                DonorModel.contact_attempted_at.is_(None)
                | (DonorModel.contact_attempted_at < border)
                | DonorModel.contact_status.in_(tuple(RETRIABLE))
            )
            .order_by(DonorModel.dr.desc().nullslast())
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def pending_count(
        self, *, ttl_days: int = cfg.CONTACT_TTL_DAYS, now: datetime | None = None
    ) -> int:
        """Сколько доноров ждёт контакта. Тот же отбор, что и у `pending_hosts`:
        два разных правила «кому нужен контакт» разошлись бы на первой правке,
        и экран показывал бы одно число, а поиск брал другое."""
        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)
        return int(
            await self._session.scalar(
                select(func.count(DomainModel.host))
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DonorModel.status == DonorStatus.SUITABLE)
                .where(
                    DonorModel.contact_attempted_at.is_(None)
                    | (DonorModel.contact_attempted_at < border)
                    | DonorModel.contact_status.in_(tuple(RETRIABLE))
                )
            )
            or 0
        )

    async def save(self, results: Sequence[LadderResult], *, now: datetime | None = None) -> int:
        """Сохранить пачку исходов. Возвращает число доноров с адресом.

        Пачка — чекпоинт: сохранённые домены выпадают из повторного прогона,
        и падение на следующей пачке не стоит уже оплаченной работы.
        """
        if not results:
            return 0

        moment = now or datetime.now(UTC)
        ids = await domain_ids(self._session, [r.host for r in results])

        await self._save_statuses(results, ids, moment)
        return await save_addresses(self._session, results, ids)

    async def _save_statuses(
        self, results: Sequence[LadderResult], ids: dict[str, int], moment: datetime
    ) -> None:
        """Исход и отметка времени по каждому донору — их пара и есть
        идемпотентность."""
        for result in results:
            domain_id = ids.get(result.host)
            if domain_id is None:
                continue
            await self._session.execute(
                update(DonorModel)
                .where(DonorModel.domain_id == domain_id)
                .values(contact_status=result.status, contact_attempted_at=moment)
            )
