"""Адресат-лид для привязки ответа по отправителю (`replies/by_sender.py`).

Лид, написавший заново на адрес ящика продаж — без метки и без заголовков цепочки, — приходит
в приём ответом без письма. Приём ищет такому ответу адресата по адресу отправителя среди
адресатов направления, а адресат письма продаж — лид переписки: строки `contacts` у письма продаж
нет (сборка очереди заводит его с пустым контактом, `queue.py`), адрес живёт у лида, а лид —
у переписки по связи `sales_threads`. Знает это только модуль продаж, а почта его не импортирует
(контракт `mail-does-not-know-sales` в `.importlinter`), поэтому поиск лежит здесь и приходит
в приём параметром: его передаёт маршрут приёма (`api/inbound/routes.py`).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.sales.models import SalesLeadModel, SalesThreadModel


async def lead_letters(session: AsyncSession, address: str, stage: Stage) -> list[tuple[int, str]]:
    """Ушедшие письма продаж лиду с этим адресом — пары «номер письма — `lead:<номер лида>`»,
    последнее письмо первым (`by_sender.SenderLookup`). Вне продаж — пусто: лид — адресат
    только писем продаж. Адрес лида сверяется без регистра и краевых пробелов; два лида с одним
    адресом (две гипотезы) — два адресата, и ответ остаётся человеку."""
    if stage is not Stage.SALES:
        return []
    rows = await session.execute(
        select(MessageModel.id, SalesThreadModel.lead_id)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(
            MessageModel.sent_at.is_not(None),
            CampaignModel.stage == Stage.SALES,
            func.lower(func.trim(SalesLeadModel.email)) == address,
        )
        .order_by(MessageModel.sent_at.desc(), MessageModel.id.desc())
    )
    return [(letter, f"lead:{lead}") for letter, lead in rows.tuples()]
