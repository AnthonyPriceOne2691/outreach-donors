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

**В очереди — только доноры** (`in_queue`, проверка прода 10.10.2026). Исход
«только форма» лежит и на кандидатах, которых человек не принимал: их адреса
искали до правила «ищем только принятым». Очередь их брала — первыми, по DR, —
и меню с главной звали «Формы 5» рядом с «2 с формой» в воронке доноров.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts import manual
from backend.features.core.domain import ContactSource, ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.donors.standing import is_donor

logger = logging.getLogger(__name__)

#: Доноров на странице — замечание 28.09.2026: «пагинация, 20 записей на
#: странице». Размер называет сервер, экран узнаёт его из ответа: второй
#: экземпляр числа на фронте разошёлся бы с этим при первой правке.
PAGE_SIZE = 20

#: Больше за раз не отдаём: страница очереди — работа руками, а не выгрузка.
MAX_PAGE_SIZE = 100


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


def in_queue() -> ColumnElement[bool]:
    """Кто ждёт рук — одно условие для страницы очереди, её числа, строки под
    действие и сводки главной: разойдись они, меню звало бы «Формы 5» над
    очередью из двух.

    Донор — тем же правилом, что везде (`standing.is_donor`): форма кандидата —
    работа до решения «берём ли», и месячный потолок ушёл бы на домены, которые
    человек потом отклонит.
    """
    return and_(is_donor(), DonorModel.contact_status == ContactStatus.FORM_ONLY)


async def queue(session: AsyncSession, *, page: int = 1, size: int = PAGE_SIZE) -> list[FormRow]:
    """Кого заполнять руками — страница очереди, номер с единицы. Сильные
    доноры сверху: их форма стоит потраченного времени, слабые подождут.

    Порядок полный — DR, за ним трафик, за ним номер донора: при равных
    DR (их в очереди много — 91, 92, 93) база вольна отдавать строки
    в любом порядке, и донор со стыка страниц показывался бы на обеих
    или ни на одной. Страница за концом — пустая, а не отказ: число
    страниц экран узнаёт по `total`.
    """
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
        .where(in_queue())
        .order_by(
            DonorModel.dr.desc().nullslast(),
            DonorModel.org_traffic.desc().nullslast(),
            DonorModel.id,
        )
        .limit(size)
        .offset((page - 1) * size)
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
    return int(await session.scalar(select(func.count(DonorModel.id)).where(in_queue())) or 0)


async def monthly_left(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Остаток месячного потолка ручных форм.

    Считается по заведённым руками контактам за последние тридцать
    дней, а не хранимым счётчиком: счётчик, который некому обнулять,
    однажды застревает — этот урок в сервисе уже оплачен дневным
    лимитом ящиков.

    Только адреса доноров (проверка прода 10.10.2026): «вписан руками» — и адрес
    кандидата с его карточки, и адрес, с которого ответил рекламодатель или лид
    продаж; потолок форм доноров тратился на них.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=30)
    used = int(
        await session.scalar(
            select(func.count(ContactModel.id))
            .join(DonorModel, DonorModel.domain_id == ContactModel.domain_id)
            .where(
                ContactModel.source == ContactSource.MANUAL,
                ContactModel.created_at >= since,
                is_donor(),
            )
        )
        or 0
    )
    return max(0, cfg.MANUAL_QUEUE_MONTHLY_CAP - used)


async def filled(session: AsyncSession, donor_id: int, *, email: str) -> FormRow:
    """Форму заполнили, донор дал адрес. Дальше он обычный донор.

    Адрес записывается тем же правилом, что вписанный с карточки донора
    (`manual.add`): с той же проверкой, отказом на дубликат словами и тем же
    исходом «адрес найден».
    """
    row = await _row(session, donor_id)
    donor = await session.get(DonorModel, donor_id)
    if donor is None:
        raise UnknownFormError(f"Донора №{donor_id} нет")
    await manual.add(session, donor, email)
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
        .where(DonorModel.id == donor_id, in_queue())
    )
    one = found.first()
    if one is None:
        raise UnknownFormError(
            f"Донора №{donor_id} нет в ручной очереди: либо адрес у него уже есть, "
            "либо его не принимал человек — форму заполняют только донору, — "
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
