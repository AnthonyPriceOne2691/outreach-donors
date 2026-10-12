"""Фильтры и порядок таблицы доноров — условиями запроса.

Отдельно от чтения (`browse.py`) с 12.10.2026: по сверке ТЗ у таблицы появились цена
(колонка, фильтр «с ценой» и пометка «цена устарела»), решение человека и порядок не только
по DR, и модуль чтения упёрся в потолок длины файла. Шов по смыслу: здесь — какие строки
и в каком порядке, там — что из них собрать.

**Срок годности — одно условие на правило.** Значок в строке и фильтр считают одним
выражением (`freshness`, `price_is`): два экземпляра правила «в сроке» разошлись бы
на самой границе срока, и фильтр «в сроке» показывал бы строку со значком «пора обновить».
Свежесть цены — правило `donors/price_freshness.py`, одно с офферами Этапа 2, обходом
и главной: ТЗ — «свежесть цены 150 дней; старше — сначала перезапрос цены у донора».

**Решение человека — основа экрана, а не фильтр колонки** (`Review`). Экран «Доноры» —
принятые человеком (решение 26.09.2026, `standing.py`), и без фильтра он прежний. По ТЗ
таблица — «со всеми сайтами, статусом и объяснением, по какому именно порогу сайт отсеян»:
«все проверенные», «отклонены» и «не решали» показывают остальные записи `donors`, и с ними
— их счётчики, страницы и выгрузку.

**Порядок — полный** (`ordered`): при равных значениях ключа — номер записи, иначе строка
со стыка страниц показывалась бы на обеих или ни на одной. Пусто — в конце при любом
направлении. Цены в разных валютах сравниваются числом: конвертации в сервисе нет, а валюта
видна рядом с ценой.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, assert_never

from sqlalchemy import ColumnElement, Select, case, or_, true

from backend.config import filters as filters_cfg
from backend.features.contacts.repository import has_address
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.price_freshness import PriceState, price_is
from backend.features.donors.standing import is_donor
from backend.features.review.candidates import Decision
from backend.shared.database.text_search import contains


class Freshness(StrEnum):
    """Метрики донора: в сроке, пора обновить, не проверялись.

    «Не проверялись» — не «пора обновить»: у второго данные были и устарели,
    у первого их не было вовсе, и обновлять нечего.
    """

    FRESH = "fresh"
    STALE = "stale"
    NEVER = "never"


def _freshness_is(state: Freshness, now: datetime) -> ColumnElement[bool]:
    """Условие одного состояния. Три условия не пересекаются и покрывают всё.

    Граница — та же, что у прогона, решающего, платить ли за метрики снова
    (`DonorRepository.fresh_hosts`): строго позже, чем срок назад.
    """
    refreshed = DonorModel.metrics_refreshed_at
    border = now - timedelta(days=filters_cfg.METRICS_TTL_DAYS)
    if state is Freshness.NEVER:
        return refreshed.is_(None)
    if state is Freshness.FRESH:
        return refreshed > border
    return refreshed <= border


def freshness(now: datetime) -> ColumnElement[str]:
    """Состояние метрик строкой — теми же условиями, что у фильтра."""
    return case(
        *((_freshness_is(state, now), state.value) for state in Freshness),
    )


class Review(StrEnum):
    """Решение человека о домене (`donors.review`) — кто на экране."""

    #: Доноры — принятые человеком; экран без фильтра.
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    #: Решения нет: ждёт в очереди прогона или не прошёл пороги.
    UNDECIDED = "undecided"
    #: Все проверенные сайты — как таблица ТЗ.
    ANY = "any"


def decided(review: Review) -> ColumnElement[bool]:
    """Условие решения. «Принят» — то же правило, что у всего экрана (`is_donor`)."""
    match review:
        case Review.ACCEPTED:
            return is_donor()
        case Review.REJECTED:
            return DonorModel.review == Decision.REJECTED.value
        case Review.UNDECIDED:
            known = (Decision.ACCEPTED.value, Decision.REJECTED.value)
            return or_(DonorModel.review.is_(None), DonorModel.review.not_in(known))
        case Review.ANY:
            return true()
        case unknown:
            assert_never(unknown)


class DonorSort(StrEnum):
    """Порядок таблицы: по DR (как было), трафику, цене и дате цены."""

    DR = "dr"
    TRAFFIC = "traffic"
    #: Цена — сначала дешёвые.
    PRICE = "price"
    PRICE_DESC = "price_desc"
    #: Дата цены — сначала свежие или сначала давние (кому пора перезапросить).
    PRICED_NEW = "priced_new"
    PRICED_OLD = "priced_old"


def ordered(sort: DonorSort) -> tuple[Any, ...]:
    """Ключи порядка — полные, до номера записи."""
    keys: dict[DonorSort, tuple[Any, ...]] = {
        DonorSort.DR: (DonorModel.dr.desc().nullslast(),),
        DonorSort.TRAFFIC: (DonorModel.org_traffic.desc().nullslast(),),
        DonorSort.PRICE: (
            DonorModel.last_price.asc().nullslast(),
            DonorModel.last_price_currency.asc().nullslast(),
        ),
        DonorSort.PRICE_DESC: (
            DonorModel.last_price.desc().nullslast(),
            DonorModel.last_price_currency.asc().nullslast(),
        ),
        DonorSort.PRICED_NEW: (DonorModel.last_price_at.desc().nullslast(),),
        DonorSort.PRICED_OLD: (DonorModel.last_price_at.asc().nullslast(),),
    }
    return (*keys[sort], DonorModel.id.desc())


@dataclass(frozen=True, slots=True)
class DonorFilters:
    """Чем сужают таблицу и как её упорядочить. Пустое поле — «не сужать»."""

    status: DonorStatus | None = None
    search: str | None = None
    min_dr: int | None = None
    has_contact: bool | None = None
    #: Органический трафик не ниже. Бывает до миллиардов — колонка `BigInteger`.
    min_traffic: int | None = None
    #: Страна донора, код ISO-2 (`donors.geo`).
    geo: str | None = None
    freshness: Freshness | None = None
    #: Есть ли цена и в сроке ли (`PriceState`).
    price: PriceState | None = None
    #: Решение человека — кто на экране; без фильтра — доноры, как было.
    review: Review = Review.ACCEPTED
    sort: DonorSort = DonorSort.DR
    #: Только эти номера — выгрузка отмеченных. Пусто — не сужать.
    ids: tuple[int, ...] = ()
    limit: int = 100
    offset: int = 0


def _by_metrics(statement: Select[Any], filters: DonorFilters, now: datetime) -> Select[Any]:
    """Фильтры колонок с числами Ahrefs: вердикт, DR, трафик, гео, данные."""
    if filters.status is not None:
        statement = statement.where(DonorModel.status == filters.status)
    if filters.min_dr is not None:
        statement = statement.where(DonorModel.dr >= filters.min_dr)
    if filters.min_traffic is not None:
        statement = statement.where(DonorModel.org_traffic >= filters.min_traffic)
    if filters.geo:
        statement = statement.where(DonorModel.geo == filters.geo.strip().lower())
    if filters.freshness is not None:
        statement = statement.where(_freshness_is(filters.freshness, now))
    return statement


def narrowed(statement: Select[Any], filters: DonorFilters, now: datetime) -> Select[Any]:
    """Фильтры колонок поверх основы экрана (`browse._screen`)."""
    if filters.ids:
        statement = statement.where(DonorModel.id.in_(filters.ids))
    statement = _by_metrics(statement, filters, now)
    if filters.price is not None:
        statement = statement.where(price_is(filters.price, now))
    if filters.search:
        # Набранное — текст, а не шаблон: «_» и «%» находили всех (`text_search`).
        statement = statement.where(
            or_(
                contains(DomainModel.host, filters.search),
                contains(DonorModel.reject_reason, filters.search),
            )
        )
    if filters.has_contact is not None:
        # «С адресом» — адрес есть в базе (`has_address`), одним правилом с плиткой
        # главной и ступенью писем (проверка прода 10.10.2026), а не исход поиска:
        # тот пишет «адреса нет» рядом с адресом, оставшимся от прежнего прохода.
        found = has_address(DonorModel.domain_id)
        statement = statement.where(found if filters.has_contact else ~found)
    return statement
