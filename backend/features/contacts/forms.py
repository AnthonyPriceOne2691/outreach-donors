"""Ручная очередь: доноры с формой вместо адреса.

Ступень лестницы, которую нельзя пройти кодом. У сайта нет почты,
но есть контактная форма — её заполняет человек, и до этого модуля
увидеть такую очередь было негде: исход лежал значком в общей таблице
доноров, а работать с ним было нельзя.

**Потолок в месяц — часть правила, а не украшение.** Форм на тысяче
доменов десятки, на пятидесяти тысячах — тысячи; без потолка очередь
превращается в список, который никто никогда не разберёт
(`okf/contact-ladder.md`).

**Заполненная форма даёт адрес, а не отметку.** Если донор ответил
на форму письмом — у нас появился адрес, и донор становится обычным:
ему уйдёт письмо из очереди. Если не ответил, запись закрывается
как «без контакта»: висеть в очереди вечно она не должна.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.core.domain import ContactSource, ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel

logger = logging.getLogger(__name__)


class UnknownFormError(ValueError):
    """Такого донора в ручной очереди нет."""


@dataclass(frozen=True, slots=True)
class FormRow:
    """Строка очереди в том виде, в каком её читает человек."""

    donor_id: int
    domain_id: int
    host: str
    dr: int | None
    org_traffic: int | None
    attempted_at: datetime | None


async def queue(session: AsyncSession, *, limit: int = 200) -> list[FormRow]:
    """Кого заполнять руками. Сильные доноры сверху: их форма стоит
    потраченного времени, слабые подождут."""
    rows = await session.execute(
        select(
            DonorModel.id,
            DonorModel.domain_id,
            DomainModel.host,
            DonorModel.dr,
            DonorModel.org_traffic,
            DonorModel.contact_attempted_at,
        )
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DonorModel.contact_status == ContactStatus.FORM_ONLY)
        .order_by(DonorModel.dr.desc().nullslast())
        .limit(limit)
    )
    return [
        FormRow(
            donor_id=donor_id,
            domain_id=domain_id,
            host=host,
            dr=dr,
            org_traffic=traffic,
            attempted_at=attempted,
        )
        for donor_id, domain_id, host, dr, traffic, attempted in rows.all()
    ]


async def total(session: AsyncSession) -> int:
    """Сколько всего ждёт рук."""
    return int(
        await session.scalar(
            select(func.count(DonorModel.id)).where(
                DonorModel.contact_status == ContactStatus.FORM_ONLY
            )
        )
        or 0
    )


async def monthly_left(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Остаток месячного потолка ручных форм.

    Считается по заведённым руками контактам за последние тридцать
    дней, а не хранимым счётчиком: счётчик, который некому обнулять,
    однажды застревает — этот урок в сервисе уже оплачен дневным
    лимитом ящиков.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=30)
    used = int(
        await session.scalar(
            select(func.count(ContactModel.id)).where(
                ContactModel.source == ContactSource.MANUAL,
                ContactModel.created_at >= since,
            )
        )
        or 0
    )
    return max(0, cfg.MANUAL_QUEUE_MONTHLY_CAP - used)


async def filled(session: AsyncSession, donor_id: int, *, email: str) -> FormRow:
    """Форму заполнили, донор дал адрес. Дальше он обычный донор."""
    row = await _row(session, donor_id)
    session.add(
        ContactModel(
            domain_id=row.domain_id,
            email=email.strip().lower(),
            source=ContactSource.MANUAL,
            # Адрес пришёл от самого донора — проверять его нечем
            # и незачем: он написал его нам сам.
            verification_status="manual",
            verification_score=100,
        )
    )
    donor = await session.get(DonorModel, donor_id)
    if donor is not None:
        donor.contact_status = ContactStatus.FOUND
    logger.info("ручная очередь: %s дал адрес", row.host)
    return row


async def gave_up(session: AsyncSession, donor_id: int) -> FormRow:
    """Заполнить не вышло. Донор уходит из очереди — висеть в ней
    вечно он не должен, а срок годности вернёт его, если что-то
    изменится."""
    row = await _row(session, donor_id)
    donor = await session.get(DonorModel, donor_id)
    if donor is not None:
        donor.contact_status = ContactStatus.NOT_FOUND
    logger.info("ручная очередь: %s закрыт без адреса", row.host)
    return row


async def _row(session: AsyncSession, donor_id: int) -> FormRow:
    found = await session.execute(
        select(
            DonorModel.id,
            DonorModel.domain_id,
            DomainModel.host,
            DonorModel.dr,
            DonorModel.org_traffic,
            DonorModel.contact_attempted_at,
        )
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DonorModel.id == donor_id, DonorModel.contact_status == ContactStatus.FORM_ONLY)
    )
    one = found.first()
    if one is None:
        raise UnknownFormError(
            f"Донора №{donor_id} нет в ручной очереди: либо адрес у него уже есть, "
            "либо очередь успел разобрать кто-то другой"
        )
    donor_id_, domain_id, host, dr, traffic, attempted = one
    return FormRow(
        donor_id=donor_id_,
        domain_id=domain_id,
        host=host,
        dr=dr,
        org_traffic=traffic,
        attempted_at=attempted,
    )
