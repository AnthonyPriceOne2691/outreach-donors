"""Кого обходить: доноры с известной и свежей ценой.

Требование прямое — «по кому запускаем: только доноры с известной ценой»
и «свежесть цены 150 дней; старше — сначала перезапрос цены у донора».
До этого модуля обход брал домены из командной строки и проверял
их только на robots.txt.

**Почему это не формальность.** Весь оффер рекламодателю строится
на фразе «мы дешевле». Дешевле чего — знает только цена донора, и цена
протухшая делает эту фразу обещанием, которого мы не сдержим. Обход
донора без цены тратит трафик и время на площадку, по которой всё равно
нельзя написать ни одного письма.

**Протухшая цена и отсутствие цены — разные состояния**, и различать их
обязательно: у первой есть что перезапросить, у второй нет. Поэтому
у отбора два исхода, а не один.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as cfg
from backend.features.core.domain import DonorStatus
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Targets:
    """Кого обходим и кого не стали, с причинами.

    Причины нужны не для красоты отчёта: «доноров с ценой нет»
    и «у всех цена протухла» — разные новости, и вторая означает, что
    сначала надо перезапросить цены, а не чинить обход.
    """

    hosts: list[str] = field(default_factory=list)
    no_price: list[str] = field(default_factory=list)
    stale_price: list[str] = field(default_factory=list)
    supplier: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "к обходу": len(self.hosts),
            "цены нет": len(self.no_price),
            "цена протухла": len(self.stale_price),
            "донор-поставщик": len(self.supplier),
        }


async def choose(
    session: AsyncSession,
    *,
    limit: int = 10,
    ttl_days: int = cfg.PRICE_TTL_DAYS,
    now: datetime | None = None,
) -> Targets:
    """Доноры, по которым можно искать рекламодателей.

    Порядок — по свежести цены: у самых свежих оффер «мы дешевле»
    опирается на самое надёжное число.
    """
    moment = now or datetime.now(UTC)
    border = moment - timedelta(days=ttl_days)
    targets = Targets()

    suppliers = {
        host.lower()
        for host in (await session.execute(select(SupplierDonorModel.host))).scalars().all()
    }

    rows = (
        await session.execute(
            select(DomainModel.host, DonorModel.last_price, DonorModel.last_price_at)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DonorModel.status == DonorStatus.SUITABLE)
            .order_by(DonorModel.last_price_at.desc().nullslast())
        )
    ).all()

    for host, price, priced_at in rows:
        if host.lower() in suppliers:
            targets.supplier.append(host)
        elif price is None or priced_at is None:
            targets.no_price.append(host)
        elif priced_at < border:
            targets.stale_price.append(host)
        elif len(targets.hosts) < limit:
            targets.hosts.append(host)

    logger.info("обход: отбор доноров — %s", targets.as_dict())
    return targets


def explain(targets: Targets) -> list[str]:
    """Что сказать человеку, если обходить некого.

    Сообщение называет, что делать: «доноров нет» без продолжения —
    полсообщения.
    """
    notes: list[str] = []
    if targets.hosts:
        return notes
    if targets.stale_price:
        notes.append(
            f"У {len(targets.stale_price)} доноров цена старше {cfg.PRICE_TTL_DAYS} дней. "
            "Оффер «мы дешевле» на протухшей цене — обещание, которого не сдержать: "
            "сначала перезапрос цены."
        )
    if targets.no_price:
        notes.append(
            f"У {len(targets.no_price)} подходящих доноров цены нет вовсе — "
            "по ним Этап 2 не запускается."
        )
    if targets.supplier:
        notes.append(
            f"{len(targets.supplier)} доноров в стоп-листе поставщиков — их рекламодателей "
            "не трогаем."
        )
    if not notes:
        notes.append("Подходящих доноров в базе нет: сначала прогон Этапа 1.")
    return notes
