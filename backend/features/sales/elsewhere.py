"""Одна роль на сайт — сторона продаж: лид, чей сайт в работе у другого направления, ждёт.

Отправка не пускает первое письмо продаж сайту, у которого есть живое письмо доноров или
рекламодателей (`letters/site_role.py`, ревью стыков 12.10.2026): снимает его с причиной,
называющей направление, а диалог продаж оставляет — к нему привязан лид. Живое — то же, что у
отправки (`attempts.in_other_role`): в очереди, в пути, дошедшее, остановленное человеком. Тем же
правилом продажи решают, писать ли лиду, до отправки:

- очистка отклоняет нового лида такого сайта (`cleaning._busy`, направление — `roles`);
- сборка очереди — кнопкой и автосборкой — лиду «готов» на таком сайте письма не собирает:
  модель не платит за письмо, которое отправка снимет, а в отчёте сборки и на экране лида — почему
  он ждёт (`waiting`, `waiting_of`). Ни новое письмо, ни снятое до отправки (`off_queue.again`);
- сайт освободился (письма того направления не дошли) — лид снова берётся сборкой (`free`), и
  снятое письмо пишется заново на том же месте (`off_queue.py`).

Направление словами — первое по порядку этапов, как у отправки: у сайта бывают живые письма и
доноров, и рекламодателей разом.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import attempts
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesThreadModel
from backend.features.sales.off_queue import ready_unwritten, unwritten


def free(domain_id: InstrumentedAttribute[int]) -> ColumnElement[bool]:
    """Сайт не в работе у другого направления: первое письмо продаж ему уходит."""
    return ~attempts.live_elsewhere(domain_id, Stage.SALES)


def writable() -> ColumnElement[bool]:
    """Лиду «готов» сборка напишет первое письмо сейчас: письма нет или оно снято до отправки
    (`off_queue.ready_unwritten`; связь лида — внешним соединением), а сайт свободен (`free`).
    Одно условие у кандидатов автосборки, её поиска лида и счёта «лидов без письма»."""
    return and_(ready_unwritten(), free(SalesLeadModel.domain_id))


def _first(stages: Iterable[Stage]) -> Stage:
    """Направление, которое назвать: первое по порядку этапов (`site_role._elsewhere`)."""
    found = set(stages)
    return next(stage for stage in Stage if stage in found)


async def roles(session: AsyncSession, domain_ids: Collection[int]) -> dict[int, Stage]:
    """Домены, у которых сайт в работе у другого направления, → направление. Одним чтением на
    партию — правилом отправки (`attempts.in_other_role`)."""
    if not domain_ids:
        return {}
    rows = await session.execute(
        select(MessageModel.domain_id, CampaignModel.stage)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            MessageModel.domain_id.in_(sorted(domain_ids)),
            attempts.in_other_role(MessageModel, CampaignModel, Stage.SALES),
        )
        .distinct()
    )
    found: dict[int, set[Stage]] = {}
    for domain_id, stage in rows.tuples().all():
        found.setdefault(domain_id, set()).add(stage)
    return {domain_id: _first(stages) for domain_id, stages in found.items()}


def waiting(stage: Stage) -> str:
    """Почему лид ждёт — словами отчёта сборки и экрана лида."""
    return (
        f"{attempts.elsewhere_reason(stage)} — лид ждёт: одному сайту письма в двух ролях не "
        "уходят; письма того направления не дойдут — сборка напишет ему снова"
    )


async def waiting_of(session: AsyncSession, leads: Iterable[SalesLeadModel]) -> dict[int, str]:
    """Лиды «готов» без ушедшего письма (диалога нет или письмо снято до отправки), чей сайт в
    работе у другого направления, → почему ждут, словами (`waiting`). Для экрана лидов."""
    ready = sorted(lead.id for lead in leads if lead.status is LeadStatus.READY)
    if not ready:
        return {}
    rows = await session.execute(
        select(SalesLeadModel.id, SalesLeadModel.domain_id)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesLeadModel.id.in_(ready), unwritten(SalesThreadModel))
    )
    domains = dict(rows.tuples().all())
    busy = await roles(session, set(domains.values()))
    return {lead: waiting(busy[domain]) for lead, domain in domains.items() if domain in busy}
