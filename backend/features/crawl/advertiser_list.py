"""Рекламодатели списком: строки с фильтрами, счётчики под фильтрами и страницы.

До 12.10.2026 рекламодателей было не увидеть списком: очередь проверки показывает
кандидатов, «К письму» — числа, а выгрузка файлом была только у лидов (сверка ТЗ
11.10.2026). Здесь — чтение: строка, за что балл, её адрес и что с оффером; пишут
переводом (`crawl/promote.py`), бизнесом ниши (`crawl/niche.py`) и сборкой писем.

**В списке — те, кому пишем или напишем** (`advertiser_rules.in_work`): рекламодатель
по найденной ссылке — всякий, бизнес ниши — после «пишем» человека. То же условие
у поиска адресов и у ручной очереди форм.

**За что балл — причины скоринга той ссылки, под которую письмо** (ТЗ: «найденные
рекламодатели со скорингом и причиной»): кандидат лучшего донора на этот домен.
Веса пересчитывают, а перевод переписывает рекламодателя по нажатию — балл после
пересчёта, если он другой, отдаётся рядом (`rescored`), а не подменяет тот, с которым
рекламодателя перевели.

**Адрес — строкой в базе, а не исходом поиска** (`has_address`), как у списка доноров
и у сборки офферов. Без адреса — с формой или без неё: форма — работа рук («Формы»).

**Донор фильтра — любой донор, который на рекламодателя ссылается**, как его считает
приёмка (`advertiser_rules.writes_to`): у рекламодателя трёх доноров список одного из
них показывает и его. В строке — донор лучшей ссылки: под неё пишется письмо.

**Один проход по письмам** (проверка ветки 12.10.2026): слово оффера — группировкой
по домену (`offer_state.OfferFlags`), строки страницы и их число — одним запросом
(число — окном), а счётчики под фильтрами — своим запросом, который экран не повторяет
на смену страницы и фильтра: они про весь список.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.contacts.repository import has_address
from backend.features.core.domain import ContactStatus
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl.advertiser_rules import in_work, writes_to
from backend.features.crawl.niche import NICHE
from backend.features.crawl.offer_state import OfferFlags, OfferState
from backend.shared.database.text_search import contains

#: Рекламодателей на странице: размер называет сервер, экран берёт его из ответа.
PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class AddressState(StrEnum):
    """Есть ли куда писать: адрес в базе, только форма или ничего."""

    FOUND = "found"
    FORM = "form"
    NONE = "none"


def address_state() -> ColumnElement[str]:
    """Состояние адреса строкой — одним условием у строки, фильтра и счётчиков."""
    return case(
        (has_address(AdvertiserModel.domain_id), AddressState.FOUND.value),
        (AdvertiserModel.contact_status == ContactStatus.FORM_ONLY, AddressState.FORM.value),
        else_=AddressState.NONE.value,
    )


@dataclass(frozen=True, slots=True)
class AdvertiserFilters:
    """Чем сужают список. Пустое поле — «не сужать»."""

    #: Подстрока домена рекламодателя.
    search: str | None = None
    #: Донор, который на рекламодателя ссылается, — любой, а не только лучшей ссылки.
    donor: str | None = None
    source: str | None = None
    address: AddressState | None = None
    letter: OfferState | None = None
    limit: int = PAGE_SIZE
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AdvertiserRow:
    """Строка списка: кто, за что балл, под какую ссылку, есть ли адрес, что с оффером."""

    id: int
    host: str
    #: `links` — ссылка на нашем доноре, `niche` — бизнес из выдачи прогона.
    source: str
    #: Прогон, в выдаче которого нашёлся бизнес ниши; у рекламодателя по ссылке пусто.
    run_id: int | None
    donor_host: str | None
    page_url: str | None
    anchor: str | None
    points: int
    #: Причины балла словами скоринга: «rel=sponsored +5». Пусто у бизнеса ниши и
    #: у ссылки, которой нет в последнем обходе её донора.
    reasons: list[str]
    #: Балл той же ссылки после пересчёта весов, если он не тот, с которым перевели.
    rescored: int | None
    #: Сколько разных наших доноров на него ссылается.
    donors: int
    #: Исход последнего поиска адреса; пусто — не искали.
    contact_status: ContactStatus | None
    address: AddressState
    letter: OfferState


@dataclass(frozen=True, slots=True)
class AdvertiserPage:
    rows: list[AdvertiserRow]
    #: Сколько строк под фильтром на всех страницах.
    total: int


@dataclass(frozen=True, slots=True)
class AdvertiserFacets:
    """Счётчики под фильтрами — по всему списку, а не по фильтру и не по странице:
    они отвечают «что вообще есть», и пустой результат отличает «сузили до пустоты»
    от «такого нет вовсе»."""

    sources: dict[str, int] = field(default_factory=dict)
    addresses: dict[AddressState, int] = field(default_factory=dict)
    letters: dict[OfferState, int] = field(default_factory=dict)
    #: Донор → сколько рекламодателей списка на него ссылается.
    donors: dict[str, int] = field(default_factory=dict)


def _screen(flags: OfferFlags, *columns: Any) -> Select[Any]:
    """Основа списка: рекламодатель с доменом и признаками оффера — только те,
    кому пишем (`in_work`)."""
    statement = (
        select(*columns)
        .select_from(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(in_work())
    )
    return flags.joined(statement, AdvertiserModel.domain_id)


def _links_from(donor: str) -> ColumnElement[bool]:
    """На рекламодателя ссылается этот донор — кандидатом, которому пишем."""
    candidate = aliased(CandidateModel)
    return (
        select(candidate.id)
        .where(
            candidate.donor_host == donor.strip().lower(),
            candidate.target_root == DomainModel.host,
            writes_to(candidate),
        )
        .exists()
    )


def _narrow(statement: Select[Any], filters: AdvertiserFilters, flags: OfferFlags) -> Select[Any]:
    if filters.search:
        # Набранное — текст, а не шаблон: «_» и «%» находили бы всех (`text_search`).
        statement = statement.where(contains(DomainModel.host, filters.search))
    if filters.donor:
        statement = statement.where(_links_from(filters.donor))
    if filters.source:
        statement = statement.where(AdvertiserModel.source == filters.source)
    if filters.address is not None:
        statement = statement.where(address_state() == filters.address.value)
    if filters.letter is not None:
        statement = statement.where(flags.state() == filters.letter.value)
    return statement


def _best_candidate(name: str) -> Any:
    """Поле кандидата лучшей ссылки: донор и домен — те, под которые письмо."""
    best = aliased(CandidateModel)
    return (
        select(getattr(best, name))
        .where(
            best.donor_host == AdvertiserModel.best_donor_host,
            best.target_root == DomainModel.host,
        )
        .order_by(best.id.desc())
        .limit(1)
        .scalar_subquery()
    )


async def page(session: AsyncSession, filters: AdvertiserFilters) -> AdvertiserPage:
    """Страница списка: самые доказательные сверху — балл, число наших доноров.

    Порядок полный, до номера: при равных баллах база вольна отдавать строки как
    угодно, и рекламодатель со стыка страниц показывался бы на обеих или ни на одной.
    Число строк — окном того же запроса: второй проход по признакам оффера ради него
    не нужен.
    """
    flags = OfferFlags.build()
    statement = _narrow(
        _screen(
            flags,
            AdvertiserModel,
            DomainModel.host,
            address_state().label("address"),
            flags.state().label("letter"),
            _best_candidate("reasons").label("reasons"),
            _best_candidate("points").label("scored"),
            func.count().over().label("total"),
        ),
        filters,
        flags,
    )
    found = (
        await session.execute(
            statement.order_by(
                AdvertiserModel.points.desc(), AdvertiserModel.donors.desc(), AdvertiserModel.id
            )
            .limit(filters.limit)
            .offset(filters.offset)
        )
    ).all()
    if found:
        total = int(found[0].total)
    else:
        # Страница за концом: окну считать нечего — число отдельным запросом.
        counted = OfferFlags.build()
        total = int(
            await session.scalar(
                _narrow(_screen(counted, func.count(AdvertiserModel.id)), filters, counted)
            )
            or 0
        )
    return AdvertiserPage(rows=[_row(line) for line in found], total=total)


def _row(line: Any) -> AdvertiserRow:
    advertiser: AdvertiserModel = line.AdvertiserModel
    niche = advertiser.source == NICHE
    scored = None if niche else line.scored
    return AdvertiserRow(
        id=advertiser.id,
        host=line.host,
        source=advertiser.source,
        run_id=advertiser.found_run_id if niche else None,
        # У бизнеса ниши «донор» — пример площадки для письма, а не место находки:
        # в списке он назвал бы ссылку, которой нет.
        donor_host=None if niche else advertiser.best_donor_host,
        page_url=advertiser.best_page_url,
        anchor=advertiser.best_anchor,
        points=advertiser.points,
        reasons=[] if niche else [str(reason) for reason in line.reasons or []],
        rescored=scored if scored is not None and scored != advertiser.points else None,
        donors=advertiser.donors,
        contact_status=advertiser.contact_status,
        address=AddressState(line.address),
        letter=OfferState(line.letter),
    )


async def facets(session: AsyncSession) -> AdvertiserFacets:
    """Счётчики под фильтрами — по всему списку: откуда, адрес и письмо — одним
    проходом, доноры — своим запросом по кандидатам."""
    flags = OfferFlags.build()
    # Через подзапрос, а не `group_by(выражение)`: слова в выражении едут параметрами,
    # и в списке колонок и в группировке у них разные номера — базе они разные.
    rows = _screen(
        flags,
        AdvertiserModel.source.label("source"),
        address_state().label("address"),
        flags.state().label("letter"),
    ).subquery()
    grouped = await session.execute(
        select(rows.c.source, rows.c.address, rows.c.letter, func.count()).group_by(
            rows.c.source, rows.c.address, rows.c.letter
        )
    )
    counted = AdvertiserFacets()
    for source, state, word, number in grouped.tuples():
        _bump(counted.sources, str(source), int(number))
        _bump(counted.addresses, AddressState(state), int(number))
        _bump(counted.letters, OfferState(word), int(number))
    donors = await session.execute(
        select(CandidateModel.donor_host, func.count(func.distinct(AdvertiserModel.id)))
        .join(DomainModel, DomainModel.host == CandidateModel.target_root)
        .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
        .where(writes_to(), in_work())
        .group_by(CandidateModel.donor_host)
    )
    counted.donors.update({str(host): int(number) for host, number in donors.tuples()})
    return counted


def _bump(counts: dict[Any, int], key: Any, number: int) -> None:
    counts[key] = counts.get(key, 0) + number
