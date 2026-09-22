"""Что отдают маршруты базы доноров."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models.donor import ContactModel
from backend.features.donors.browse import DonorCard as CardData
from backend.features.donors.browse import DonorPage as PageData
from backend.features.donors.browse import DonorRow


class DonorRowCard(BaseModel):
    """Строка таблицы доноров.

    `reject_reason` отдаётся всегда: «не подходит» без причины — это
    решение, которое нельзя оспорить, а пороги у нас версионируются
    именно затем, чтобы прошлые решения объяснялись.
    """

    id: int
    host: str
    status: DonorStatus
    reject_reason: str | None
    dr: int | None
    org_traffic: int | None
    geo: str | None
    geo_top_share: float | None
    contacts: int
    contact_status: ContactStatus | None
    last_price: Decimal | None
    last_price_currency: str | None
    metrics_refreshed_at: datetime | None
    fresh: bool

    @classmethod
    def of(cls, row: DonorRow) -> DonorRowCard:
        donor = row.donor
        return cls(
            id=donor.id,
            host=row.host,
            status=donor.status,
            reject_reason=donor.reject_reason,
            dr=donor.dr,
            org_traffic=donor.org_traffic,
            geo=donor.geo,
            geo_top_share=donor.geo_top_share,
            contacts=row.contacts,
            contact_status=donor.contact_status,
            last_price=donor.last_price,
            last_price_currency=donor.last_price_currency,
            metrics_refreshed_at=donor.metrics_refreshed_at,
            fresh=row.fresh,
        )


class DonorsPage(BaseModel):
    """Страница таблицы и общее число: без него фильтр не с чем сравнить,
    и «ничего не найдено» читается как «база пуста»."""

    rows: list[DonorRowCard]
    total: int
    counts: dict[str, int]

    @classmethod
    def of(cls, page: PageData, counts: dict[DonorStatus, int]) -> DonorsPage:
        return cls(
            rows=[DonorRowCard.of(row) for row in page.rows],
            total=page.total,
            counts={status.value: count for status, count in counts.items()},
        )


class ContactCard(BaseModel):
    """Адрес донора и ступень, которая его дала."""

    id: int
    email: str
    source: ContactSource
    last_contacted_at: datetime | None
    last_replied_at: datetime | None

    @classmethod
    def of(cls, contact: ContactModel) -> ContactCard:
        return cls(
            id=contact.id,
            email=contact.email,
            source=contact.source,
            last_contacted_at=contact.last_contacted_at,
            last_replied_at=contact.last_replied_at,
        )


class DonorFullCard(BaseModel):
    """Карточка донора.

    Срок годности метрик отдаётся отдельным полем, а не выводится
    на фронте: правило «за свежее не платим второй раз» живёт в ядре,
    и второй его экземпляр в интерфейсе разъехался бы с первым.
    """

    id: int
    host: str
    status: DonorStatus
    reject_reason: str | None
    dr: int | None
    org_traffic: int | None
    geo: str | None
    geo_top_share: float | None
    geo_breakdown: list[dict[str, Any]] | None
    #: Разбивка неполная: спрашивали только верхнюю страну, её хватило
    #: для вердикта. Экран обязан это сказать, иначе одна строка выглядит
    #: как «у домена трафик из одной страны».
    geo_partial: bool
    metrics: dict[str, Any] | None
    metrics_refreshed_at: datetime | None
    expires_at: datetime | None
    fresh: bool
    contact_status: ContactStatus | None
    contact_attempted_at: datetime | None
    last_price: Decimal | None
    last_price_currency: str | None
    last_price_at: datetime | None
    contacts: list[ContactCard]

    @classmethod
    def of(cls, card: CardData) -> DonorFullCard:
        donor = card.donor
        return cls(
            id=donor.id,
            host=card.host,
            status=donor.status,
            reject_reason=donor.reject_reason,
            dr=donor.dr,
            org_traffic=donor.org_traffic,
            geo=donor.geo,
            geo_top_share=donor.geo_top_share,
            geo_breakdown=donor.geo_breakdown,
            geo_partial=donor.geo_partial,
            metrics=donor.metrics,
            metrics_refreshed_at=donor.metrics_refreshed_at,
            expires_at=card.expires_at,
            fresh=card.fresh,
            contact_status=donor.contact_status,
            contact_attempted_at=donor.contact_attempted_at,
            last_price=donor.last_price,
            last_price_currency=donor.last_price_currency,
            last_price_at=donor.last_price_at,
            contacts=[ContactCard.of(contact) for contact in card.contacts],
        )
