"""Очередь поиска контакта для рекламодателей.

Лестница та же, что у доноров, — она работает с голым хостом и про роль
домена ничего не знает. Разное только две вещи: кого брать в очередь
и куда писать исход.

**Адрес искать второй раз незачем.** Контакт принадлежит домену, а не
роли: сайт, у которого адрес уже нашли донором, приходит сюда с готовым
адресом. Очередь таких пропускает — платная ступень стоит денег.

**Исход попытки хранится у рекламодателя**, как у донора: без отметки
времени «не нашли» и «ещё не искали» выглядят одинаково, и повторный
проход платит за уже пройденное.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import RETRIABLE, domain_ids, save_addresses
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel

logger = logging.getLogger(__name__)


class AdvertiserContactRepository:
    """Кому из рекламодателей пора искать адрес и куда писать исход."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _pending(self, moment: datetime, ttl_days: int) -> ColumnElement[bool]:
        """Условие «нужен контакт». Одно на счёт и на выборку: два разных
        правила разошлись бы на первой правке, и экран показывал бы одно
        число, а поиск брал другое."""
        border = moment - timedelta(days=ttl_days)
        has_contact = select(ContactModel.id).where(
            ContactModel.domain_id == AdvertiserModel.domain_id
        )
        return (
            AdvertiserModel.contact_attempted_at.is_(None)
            | (AdvertiserModel.contact_attempted_at < border)
            | AdvertiserModel.contact_status.in_(tuple(RETRIABLE))
        ) & ~has_contact.exists()

    async def pending_hosts(
        self,
        *,
        limit: int = 100,
        ttl_days: int = cfg.CONTACT_TTL_DAYS,
        now: datetime | None = None,
    ) -> list[str]:
        """Рекламодатели без адреса, по убыванию балла.

        Балл первым: если бюджет платной ступени кончится на середине,
        он кончится на самых убедительных, а не на случайных.
        """
        moment = now or datetime.now(UTC)
        rows = await self._session.execute(
            select(DomainModel.host)
            .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
            .where(self._pending(moment, ttl_days))
            .order_by(AdvertiserModel.points.desc())
            .limit(limit)
        )
        return list(rows.scalars().all())

    async def pending_count(
        self, *, ttl_days: int = cfg.CONTACT_TTL_DAYS, now: datetime | None = None
    ) -> int:
        moment = now or datetime.now(UTC)
        found = await self._session.execute(
            select(func.count(DomainModel.host))
            .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
            .where(self._pending(moment, ttl_days))
        )
        return int(found.scalar_one() or 0)

    async def save(self, results: Sequence[LadderResult], *, now: datetime | None = None) -> int:
        """Сохранить пачку исходов. Возвращает число доменов с адресом."""
        if not results:
            return 0

        moment = now or datetime.now(UTC)
        ids = await domain_ids(self._session, [r.host for r in results])

        for result in results:
            domain_id = ids.get(result.host)
            if domain_id is None:
                logger.warning(
                    "контакты рекламодателей: домена %s нет в базе — исход не записан", result.host
                )
                continue
            await self._session.execute(
                update(AdvertiserModel)
                .where(AdvertiserModel.domain_id == domain_id)
                .values(contact_status=result.status, contact_attempted_at=moment)
            )

        return await save_addresses(self._session, results, ids)
