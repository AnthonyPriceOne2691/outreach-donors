"""Запись ручной цены донору — общие куски кнопки и загрузки списком.

Вынесено из `manual_price.py` 29.10.2026, когда у ручного ввода появился второй вход —
загрузка доноров таблицей (`bulk_entry.py`): принятие без прогона, цена в карточку,
запись журнала и отказ «слово новее» — одни у кнопки, консоли и загрузки, а не копии.
Здесь только запись в объекты сессии; что можно записывать — `price_refusal.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, DonorStatus, PriceSource
from backend.features.core.models.donor import DonorModel
from backend.features.donors.price import PriceDetails, put_price
from backend.features.review.candidates import Decision

if TYPE_CHECKING:
    from backend.features.donors.manual_price import ManualPrice


def accept_by_hand(donor: DonorModel, *, by: str, at: datetime) -> None:
    """Принят человеком без прогона — пометки на строке донора, без записи в базу.

    «Годен» — слово человека, а не порогов: метрики платные, и ручной ввод их не
    покупает. Найдёт донора прогон — запишет вердикт своего замера (`measured_status`),
    а этот, для выходов, не тронет: принятого понижает только прогон, где его приняли,
    а этого приняли без прогона (`donors/held.py`). Одно на кнопку и на загрузку
    списком (`bulk_entry.py`).
    """
    donor.status = DonorStatus.SUITABLE
    donor.review, donor.review_at, donor.review_by = Decision.ACCEPTED.value, at, by
    donor.entered_by = by


def suitable(donor: DonorModel) -> bool:
    """Годен ли донор порогам: обход берёт только годного (`crawl/targets.choose`)."""
    return donor.status is DonorStatus.SUITABLE


def later_refusal(host: str) -> str:
    """Отказ, когда у донора слово новее вписанной цены (`price.said_later`)."""
    return (
        f"Цену {host} не записали: у донора записано слово новее этой цены — ответ или "
        "цена позже неё, а последнее слово сильнее. Обновите карточку и проверьте цену."
    )


def write_price(donor: DonorModel, price: ManualPrice, at: datetime) -> None:
    """Ручная цена — в карточку донора: белая, серая, оплата, откуда и кто (`put_price`)."""
    put_price(
        donor,
        amount=price.amount,
        currency=price.currency,
        at=at,
        source=PriceSource.MANUAL,
        details=PriceDetails(
            white=price.amount,
            grey=price.grey,
            payment_methods=price.payment_methods,
            note=price.note,
            by=price.by,
        ),
    )


async def record_price(
    session: AsyncSession,
    donor: DonorModel,
    host: str,
    price: ManualPrice,
    *,
    created: bool,
    before: str | None,
    author_id: int | None,
) -> None:
    """Запись журнала о ручной цене одному донору. Без коммита."""
    await AccessRepository(session).record(
        AuditAction.PRICE_REVIEWED,
        author_id=author_id,
        target=f"donor:{donor.id}",
        details=price_details(host, price, created=created, before=before),
    )


def price_details(
    host: str, price: ManualPrice, *, created: bool, before: str | None, via: str | None = None
) -> dict[str, Any]:
    """Подробности записи журнала о ручной цене: кто, сколько, откуда и что было до. `via` —
    как она пришла, если не кнопкой: «загрузка списком» (`bulk_entry.py`)."""
    details: dict[str, Any] = {
        "действие": "донор заведён вручную, с ценой" if created else "цена указана вручную",
        "донор": host,
        "цена": str(price.amount),
        "валюта": price.currency,
        "серая": None if price.grey is None else str(price.grey),
        "оплата": list(price.payment_methods) or None,
        "откуда цена": price.note,
        "кто": price.by,
        "прежняя цена": before,
    }
    if via is not None:
        details["как"] = via
    return details


def shown_price(donor: DonorModel) -> str | None:
    """Прежняя цена для журнала: «120.00 EUR, из ответа». Пусто — цены не было."""
    if donor.last_price is None:
        return None
    source = "вручную" if donor.last_price_source == PriceSource.MANUAL else "из ответа"
    amount = " ".join(part for part in (str(donor.last_price), donor.last_price_currency) if part)
    return f"{amount}, {source}"
