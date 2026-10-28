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

**Форма — тоже письмо: стоп-лист и одна роль на сайт** (28.10.2026). Очередь сверяла только
«кому не пишем» ТЗ: домен, внесённый в стоп-лист уже после приёма донора, и сайт, которому
пишут продажи или офферы, стояли в ней с готовым текстом письма, — а письмо им не ушло бы.
Теперь условие то же, что у отправки: запись стоп-листа на домен, держащая этап доноров
(`runs.exclusions.stoplisted`), и живое письмо другого этапа (`attempts.roles_elsewhere`). Им же
карточка донора говорит, стоит ли он в очереди (`queued`): звала туда всякого принятого.

**Рекламодатели — своей очередью рядом** (`advertiser_queue`, сверка ТЗ 11.10.2026):
строка ТЗ «только форма, нет email — заполняем вручную» общая на оба этапа, а очередь
была только у доноров, и рекламодатель с одной формой не получал оффера никогда.
В ней — те, кому пишем (`crawl/advertiser_rules.in_work`): по ссылке — всякий, бизнес
ниши — после «пишем». Вписанный адрес — тот же `manual.add`: строка в `contacts`
домена, и сборка оффера берёт его, как найденный лестницей. Стоп-лист и одна роль на сайт —
тем же правилом, что у доноров, своим этапом (`_form_allowed`, 28.10.2026): запись,
держащая рекламодателей, и живое письмо доноров или продаж.

**Ориентир темпа — один на обе очереди** (`monthly_left`): он про руки, а руки одни;
считаются только адреса, вписанные человеком, — доноров и рекламодателей.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.config import contacts as cfg
from backend.features.contacts import manual
from backend.features.contacts.repository import has_address
from backend.features.core.domain import ContactStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.crawl.advertiser_rules import in_work
from backend.features.crawl.niche import NICHE
from backend.features.donors.standing import is_donor
from backend.features.letters import attempts
from backend.features.runs.exclusions import not_writing, stoplisted
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

    #: Повтор задачи это не исправит (`runs/failures.is_permanent`): донора из очереди
    #: убрали — вписали адрес или закрыли «Не вышло».
    permanent = True


@dataclass(frozen=True, slots=True)
class FormRow:
    """Строка очереди в том виде, в каком её читает человек."""

    donor_id: int
    domain_id: int
    host: str
    dr: int | None
    org_traffic: int | None
    attempted_at: datetime | None
    #: Страница с формой (`domains.contact_form_url`): человек открывает её прямо.
    #: Пусто — искали до 20.10.2026, когда адрес формы ещё не запоминался.
    form_url: str | None = None


def in_queue() -> ColumnElement[bool]:
    """Кто ждёт рук — одно условие для страницы очереди, её числа, строки под
    действие, сводки главной и карточки донора: разойдись они, меню звало бы
    «Формы 5» над очередью из двух.

    Донор — тем же правилом, что везде (`standing.is_donor`): форма кандидата —
    работа до решения «берём ли», и месячный потолок ушёл бы на домены, которые
    человек потом отклонит. И без адреса в базе (`has_address`): форма — вместо
    адреса, а исход «только форма» пишет и повторный поиск рядом с прежним адресом;
    на главной «и N с формой» стоит рядом с «С адресом» и с ним не пересекается.

    Форма — тоже письмо, и кому письмо не ушло бы, того здесь нет: «кому не пишем» ТЗ
    (`runs.exclusions`), стоп-лист — запись на домен, держащая этап доноров, — и сайт,
    которому пишет другое направление (`_form_allowed`, общее с очередью рекламодателей).

    Срок записи стоп-листа судится «сейчас» самого условия, а не моментом-доводом: довод на
    пяти путях разошёлся бы с первым же вызовом со своим моментом — главная, например,
    считает цены на свой `now`, и меню судило бы о сроке записи не тем моментом, что страница.
    """
    return and_(
        is_donor(),
        DonorModel.contact_status == ContactStatus.FORM_ONLY,
        ~has_address(DonorModel.domain_id),
        ~not_writing(),
        _form_allowed(DonorModel.domain_id, Stage.DONORS),
    )


def _form_allowed(domain_id: InstrumentedAttribute[int], stage: Stage) -> ColumnElement[bool]:
    """Сайту можно писать формой этапа `stage` — форма тоже письмо: его домен не держит
    запись стоп-листа этапа (`runs.exclusions.stoplisted`), и у него нет живого письма другого
    этапа — тем же правилом, что держит первое письмо при отправке (`letters/site_role.py`).
    Живое — как там (`attempts.dead`): письмо другой роли не дошло или снято при отправке —
    сайт снова ждёт рук. Одно условие у очередей доноров и рекламодателей (28.10.2026)."""
    return and_(~stoplisted(domain_id, stage), ~attempts.roles_elsewhere(domain_id, stage).exists())


#: Колонки строки очереди — одни у страницы и у строки под действие.
_ROW_COLUMNS = (
    DonorModel.id,
    DonorModel.domain_id,
    DomainModel.host,
    DonorModel.dr,
    DonorModel.org_traffic,
    DonorModel.contact_attempted_at,
    DomainModel.contact_form_url,
)


def _form_row(
    donor_id: int,
    domain_id: int,
    host: str,
    dr: int | None,
    traffic: int | None,
    attempted: datetime | None,
    form_url: str | None,
) -> FormRow:
    return FormRow(
        donor_id=donor_id,
        domain_id=domain_id,
        host=host,
        dr=dr,
        org_traffic=traffic,
        attempted_at=attempted,
        form_url=form_url,
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
        select(*_ROW_COLUMNS)
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
    return [_form_row(*found) for found in rows.all()]


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
    row = await form_row(session, donor_id)
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
    row = await form_row(session, donor_id)
    donor = await session.get(DonorModel, donor_id)
    if donor is not None:
        donor.contact_status = ContactStatus.NOT_FOUND
    logger.info("ручная очередь: %s закрыт без адреса", row.host)
    return row


async def form_row(session: AsyncSession, donor_id: int) -> FormRow:
    """Строка очереди этого донора — или отказ словами: его в очереди нет."""
    found = await session.execute(
        select(*_ROW_COLUMNS)
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DonorModel.id == donor_id, in_queue())
    )
    one = found.first()
    if one is None:
        raise UnknownFormError(
            f"Донора №{donor_id} нет в ручной очереди: либо адрес у него уже есть, "
            "либо его не принимал человек — форму заполняют только донору, — "
            "либо ему не пишем: сайт в стоп-листе, в «кому не пишем» или в работе "
            "у другого направления, — либо очередь успел разобрать кто-то другой"
        )
    return _form_row(*one)


async def queued(session: AsyncSession, donor_id: int) -> bool:
    """Стоит ли донор в очереди — её условием (`in_queue`): «стоит в очереди «Формы»»
    карточка донора пишет только тогда, а не всякому принятому, как до 28.10.2026."""
    found = await session.scalar(select(DonorModel.id).where(DonorModel.id == donor_id, in_queue()))
    return found is not None


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
    #: Страница с формой — как у доноров (`FormRow.form_url`).
    form_url: str | None = None


def advertiser_in_queue() -> ColumnElement[bool]:
    """Кто из рекламодателей ждёт рук — одно условие для страницы, числа (вкладка, меню,
    главная) и строки под действие, как у доноров (`in_queue`): ему пишем (`in_work`),
    лестница нашла только форму, адреса в базе нет, и сайт открыт для формы — запись
    стоп-листа рекламодателей его не держит, другое направление ему не пишет (`_form_allowed`)."""
    return and_(
        in_work(),
        AdvertiserModel.contact_status == ContactStatus.FORM_ONLY,
        ~has_address(AdvertiserModel.domain_id),
        _form_allowed(AdvertiserModel.domain_id, Stage.ADVERTISERS),
    )


def _advertiser_rows() -> Select[tuple[AdvertiserModel, str, str | None]]:
    return (
        select(AdvertiserModel, DomainModel.host, DomainModel.contact_form_url)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(advertiser_in_queue())
    )


def _advertiser_row(
    advertiser: AdvertiserModel, host: str, form_url: str | None = None
) -> AdvertiserFormRow:
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
        form_url=form_url,
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
    return [_advertiser_row(*found) for found in rows.tuples()]


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
            "есть, либо ему не пишем: сайт в стоп-листе или в работе у другого направления, "
            "а бизнесу ниши форму заполняют после «пишем», — либо очередь успел разобрать "
            "кто-то другой"
        )
    return _advertiser_row(*found._tuple())


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
