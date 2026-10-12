"""Ручная очередь: доноры с формой вместо адреса.

Ступень лестницы, которую нельзя пройти кодом. У сайта нет почты,
но есть контактная форма — её заполняет человек, и до этого модуля
увидеть такую очередь было негде: исход лежал значком в общей таблице
доноров, а работать с ним было нельзя.

**Потолок в месяц — ориентир темпа, а не запрет** (решение Anthony 11.10.2026).
Форм на тысяче доменов десятки, на пятидесяти тысячах — тысячи; число говорит,
успевают ли руки за очередью, но очередь не закрывает: форма сверх него — та же
строка (`okf/contact-ladder.md`).

**Заполненная форма даёт адрес, а не отметку.** Если донор ответил
на форму письмом — у нас появился адрес, и донор становится обычным:
ему уйдёт письмо из очереди. Если не ответил, запись закрывается
как «без контакта»: висеть в очереди вечно она не должна.

**В очереди — только доноры** (`in_queue`, проверка прода 10.10.2026). Исход
«только форма» лежит и на кандидатах, которых человек не принимал: их адреса
искали до правила «ищем только принятым». Очередь их брала — первыми, по DR, —
и меню с главной звали «Формы 5» рядом с «2 с формой» в воронке доноров.

**Рекламодатели — своей очередью рядом** (`advertiser_queue`, сверка ТЗ 11.10.2026):
строка ТЗ «только форма, нет email — заполняем вручную» общая на оба этапа, а очередь
была только у доноров, и рекламодатель с одной формой не получал оффера никогда.
В ней — те, кому пишем (`crawl/advertiser_rules.in_work`): по ссылке — всякий, бизнес
ниши — после «пишем». Вписанный адрес — тот же `manual.add`: строка в `contacts`
домена, и сборка оффера берёт его, как найденный лестницей.

**Ориентир темпа — один на обе очереди** (`monthly_left`): он про руки, а руки одни;
считаются только адреса, вписанные человеком, — доноров и рекламодателей.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts import manual
from backend.features.contacts.repository import has_address
from backend.features.core.domain import ContactStatus
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.crawl.advertiser_rules import in_work
from backend.features.crawl.niche import NICHE
from backend.features.donors.standing import is_donor
from backend.shared.database.ids import storable

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
    человек потом отклонит. И без адреса в базе (`has_address`): форма — вместо
    адреса, а исход «только форма» пишет и повторный поиск рядом с прежним адресом;
    на главной «и N с формой» стоит рядом с «С адресом» и с ним не пересекается.
    """
    return and_(
        is_donor(),
        DonorModel.contact_status == ContactStatus.FORM_ONLY,
        ~has_address(DonorModel.domain_id),
    )


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
    """Остаток ориентира темпа «Форм»: сколько адресов ещё вписать руками, чтобы
    за последние тридцать дней остаться в ориентире. Ничего не держит: при нуле
    очередь та же, и вписывать можно (решение Anthony 11.10.2026).

    Считается по заведённым руками контактам за последние тридцать
    дней, а не хранимым счётчиком: счётчик, который некому обнулять,
    однажды застревает — этот урок в сервисе уже оплачен дневным
    лимитом ящиков.

    Только вписанные человеком (`manual.BY_HAND`) — на «Формах» и в карточке донора
    (решение Anthony 11.10.2026). Источник `manual` есть и у адреса, с которого донор
    ответил, и у адреса из автоответа, и у ящика проверки — ориентир тратился на них.
    Адрес, на который письмо не дошло, из счёта выходит: отметка мёртвого
    (`preference.DEAD`) ложится поверх.

    Только адреса доноров и рекламодателей, которым пишем (проверка прода 10.10.2026,
    сверка ТЗ 11.10.2026): «вписан руками» — и адрес кандидата с его карточки, а
    очередь форм кандидату не заполняют. Ориентир один на обе очереди — он про руки,
    а руки одни; адрес домена, который и донор, и рекламодатель, — один адрес.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=30)
    donor = select(DonorModel.id).where(DonorModel.domain_id == ContactModel.domain_id, is_donor())
    advertiser = select(AdvertiserModel.id).where(
        AdvertiserModel.domain_id == ContactModel.domain_id, in_work()
    )
    used = int(
        await session.scalar(
            select(func.count(ContactModel.id)).where(
                ContactModel.verification_status == manual.BY_HAND,
                ContactModel.created_at >= since,
                or_(donor.exists(), advertiser.exists()),
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


# --- рекламодатели: та же ручная очередь Этапа 2 ---


@dataclass(frozen=True, slots=True)
class AdvertiserFormRow:
    """Рекламодатель из ручной очереди: форма есть, адреса нет."""

    advertiser_id: int
    domain_id: int
    host: str
    #: `links` — ссылка на нашем доноре, `niche` — бизнес из выдачи прогона.
    source: str
    #: Донор, на котором нашли ссылку; у бизнеса ниши — пусто (у него пример площадки).
    donor_host: str | None
    #: Прогон, в выдаче которого нашёлся бизнес ниши.
    run_id: int | None
    points: int
    attempted_at: datetime | None


def advertiser_in_queue() -> ColumnElement[bool]:
    """Кто из рекламодателей ждёт рук — одно условие для страницы, числа и строки
    под действие, как у доноров (`in_queue`): ему пишем (`in_work`), лестница нашла
    только форму, адреса в базе нет."""
    return and_(
        in_work(),
        AdvertiserModel.contact_status == ContactStatus.FORM_ONLY,
        ~has_address(AdvertiserModel.domain_id),
    )


def _advertiser_rows() -> Select[tuple[AdvertiserModel, str]]:
    return (
        select(AdvertiserModel, DomainModel.host)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(advertiser_in_queue())
    )


def _advertiser_row(advertiser: AdvertiserModel, host: str) -> AdvertiserFormRow:
    niche = advertiser.source == NICHE
    return AdvertiserFormRow(
        advertiser_id=advertiser.id,
        domain_id=advertiser.domain_id,
        host=host,
        source=advertiser.source,
        donor_host=None if niche else advertiser.best_donor_host,
        run_id=advertiser.found_run_id if niche else None,
        points=advertiser.points,
        attempted_at=advertiser.contact_attempted_at,
    )


async def advertiser_queue(
    session: AsyncSession, *, page: int = 1, size: int = PAGE_SIZE
) -> list[AdvertiserFormRow]:
    """Кого из рекламодателей заполнять руками — страница, номер с единицы. Самые
    доказательные сверху — балл, как у сборки офферов; порядок полный, до номера."""
    rows = await session.execute(
        _advertiser_rows()
        .order_by(AdvertiserModel.points.desc(), AdvertiserModel.id)
        .limit(size)
        .offset((page - 1) * size)
    )
    return [_advertiser_row(advertiser, host) for advertiser, host in rows.tuples()]


async def advertiser_total(session: AsyncSession) -> int:
    """Сколько рекламодателей ждёт рук."""
    return int(
        await session.scalar(select(func.count(AdvertiserModel.id)).where(advertiser_in_queue()))
        or 0
    )


async def waiting(session: AsyncSession) -> int:
    """Сколько ждёт рук на «Формах» всего — доноры и рекламодатели: число меню."""
    return await total(session) + await advertiser_total(session)


async def _advertiser(session: AsyncSession, advertiser_id: int) -> AdvertiserFormRow:
    # Номер больше столбца — «нет в очереди», а не пятисотка базы (`storable`).
    found = (
        (
            await session.execute(_advertiser_rows().where(AdvertiserModel.id == advertiser_id))
        ).first()
        if storable(advertiser_id)
        else None
    )
    if found is None:
        raise UnknownFormError(
            f"Рекламодателя №{advertiser_id} нет в ручной очереди: либо адрес у него уже "
            "есть, либо ему не пишем — бизнесу ниши форму заполняют после «пишем», — "
            "либо очередь успел разобрать кто-то другой"
        )
    advertiser, host = found._tuple()
    return _advertiser_row(advertiser, host)


async def advertiser_filled(
    session: AsyncSession, advertiser_id: int, *, email: str
) -> AdvertiserFormRow:
    """Форму заполнили, рекламодатель дал адрес — дальше ему уходит оффер.

    Адрес — тем же правилом, что у донора (`manual.add`): строка в `contacts` домена
    с отметкой «вписан руками»; сборка оффера берёт адреса домена (`letters/recipients`).
    """
    row = await _advertiser(session, advertiser_id)
    advertiser = await session.get(AdvertiserModel, advertiser_id)
    if advertiser is None:
        raise UnknownFormError(f"Рекламодателя №{advertiser_id} нет")
    await manual.add(session, advertiser, email)
    logger.info("ручная очередь: рекламодатель %s дал адрес", row.host)
    return row


async def advertiser_gave_up(session: AsyncSession, advertiser_id: int) -> AdvertiserFormRow:
    """Заполнить не вышло — рекламодатель уходит из очереди «без адреса»: висеть
    в ней вечно он не должен. Отметка попытки не трогается — срок годности
    (`CONTACT_TTL_DAYS` от прошлого поиска) вернёт его в поиск адреса, как донора;
    до этого оффера ему не будет. Так и говорит подтверждение на экране."""
    row = await _advertiser(session, advertiser_id)
    advertiser = await session.get(AdvertiserModel, advertiser_id)
    if advertiser is not None:
        advertiser.contact_status = ContactStatus.NOT_FOUND
    logger.info("ручная очередь: рекламодатель %s закрыт без адреса", row.host)
    return row
