"""Лиды из кандидатов сбора: принятым с живым адресом на домене — лид гипотезы, затем
та же очистка, что у загрузки базы (`cleaning.clean_leads`).

**Адрес — лучший живой адрес домена** по общему порядку (`contacts/preference.preferred_first`:
с которого отвечали, вписанный человеком, по оценке проверки, старший); мёртвый (отказ
доставки) не берётся вовсе. Ссылка лида на общую строку адреса — та же, что у лида из
файла (`contact_id`, `SET NULL`), а сам адрес лид хранит у себя (решение 01.10).

**Лид — источник `collected`**, страна — рынок выдачи прогона, пояс — по стране, язык —
язык писем прогона. Имени, должности и компании сбор не знает: первое письмо и так
спрашивает, кто отвечает за SEO и маркетинг.

**Дубли, стоп-листы, отписки, чужое направление и мёртвые адреса** отсекает очистка — та же,
что у загрузки, без копии: адрес уже у лида продаж — «дубль», домен в работе у доноров или
рекламодателей — «домен в работе у другого направления». Журнал — запись загрузки базы:
по нему ищут, откуда взялся лид.

**Сайт, у домена которого уже есть живой лид продаж любой гипотезы, лида не даёт**
(решение владельца 14.10, «компания — в одной гипотезе»), даже если готовый адрес домена
лежит в `contacts`: у компании лид уже есть, а заведённый оплатил бы проверку адреса — и
своей гипотезе, где очистка его пропустила бы. Условие то же, что у очереди поиска адреса
(`company_hold.held`); итог такого сайта в отчёте — «компания уже в работе гипотезы …».
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Exists, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.access.repository import AccessRepository
from backend.features.contacts.preference import DEAD, preferred_first
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.sales import clean_jobs, cleaning, company_hold, geo
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.verifier import EmailVerifier

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel


def _alive() -> ColumnElement[bool]:
    return ContactModel.verification_status.is_distinct_from(DEAD)


def living_address(domain_id: ColumnElement[int] | InstrumentedAttribute[int]) -> Exists:
    """У домена есть живой адрес — строка `contacts` без отметки отказа доставки."""
    return select(ContactModel.id).where(ContactModel.domain_id == domain_id, _alive()).exists()


def lead_ready(run_id: int) -> ColumnElement[bool]:
    """Принятые кандидаты прогона без лида, чей домен даёт лида из живого адреса, — без чужого
    живого лида на домене (`company_hold.held`). Одно условие у этапа лидов и у числа «ждут» в
    отчёте прогона (`segment_report`)."""
    return and_(
        Candidate.run_id == run_id,
        Candidate.lane == Lane.ACCEPT,
        Candidate.lead_id.is_(None),
        living_address(Candidate.domain_id),
        ~company_hold.held(Candidate.domain_id),
    )


async def _ready(
    session: AsyncSession, run_id: int, since: datetime | None = None
) -> list[tuple[Candidate, int, str]]:
    """Принятые кандидаты прогона без лида — и лучший живой адрес их домена; домен с живым
    лидом продаж лида не даёт (`company_hold.held`). `since` — только те, кому поиск записал
    исход с этого момента: повтор по сроку заводит лиды лишь из найденного им самим.

    **Строки взятых — под замком до конца транзакции заведения** (`FOR UPDATE OF` кандидатов):
    человек может отменить решение «принят» посреди прохода (`segment_undo`). Без замка проход,
    прочитавший сайт принятым, заводил лида уже после отмены — сайт «на проверке», а лид есть.
    С замком отмена ждёт конца заведения и видит лид (отказ словами), а заведение, пришедшее
    посреди отмены, ждёт её и перечитывает строку: сайт уже не принят — лида нет."""
    best = (
        select(ContactModel.domain_id, ContactModel.id, ContactModel.email)
        .distinct(ContactModel.domain_id)
        .where(_alive())
        .order_by(ContactModel.domain_id, *preferred_first())
        .subquery()
    )
    query = (
        select(Candidate, best.c.id, best.c.email)
        .join(best, best.c.domain_id == Candidate.domain_id)
        .where(lead_ready(run_id))
        .order_by(Candidate.id)
        .with_for_update(of=Candidate)
    )
    if since is not None:
        query = query.where(Candidate.contact_attempted_at >= since)
    return list((await session.execute(query)).tuples().all())


def _lead(
    run: SalesSegmentRunModel, candidate: Candidate, contact_id: int, email: str
) -> SalesLeadModel:
    return SalesLeadModel(
        hypothesis_id=run.hypothesis_id,
        domain_id=candidate.domain_id,
        contact_id=contact_id,
        email=email.strip().lower(),
        country=run.country,
        timezone=geo.timezone_for(run.country),
        language=run.language,
        source=LeadSource.COLLECTED,
        status=LeadStatus.NEW,
    )


async def make_leads(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    verifier: EmailVerifier,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Завести лидов принятым с адресом и очистить их. Коммит — за вызывающим: лиды,
    ссылки кандидатов, журнал, итог очистки и строка расхода проверки — одна транзакция.
    `since` — только из найденного поиском с этого момента (`_ready`)."""
    ready = await _ready(session, run.id, since)
    if not ready:
        return {"created": 0}
    pairs = [
        (candidate, _lead(run, candidate, contact_id, email))
        for candidate, contact_id, email in ready
    ]
    session.add_all([lead for _, lead in pairs])
    await session.flush()
    for candidate, lead in pairs:
        candidate.lead_id = lead.id
    await AccessRepository(session).record(
        AuditAction.SALES_LEADS_IMPORTED,
        target=f"sales_hypothesis:{run.hypothesis_id}",
        details={
            "источник": f"сбор по ключам, прогон №{run.id}",
            "загружено": len(pairs),
            "отклонено": 0,
            "запустил": run.created_by,
        },
        stage=Stage.SALES,
    )
    found = await cleaning.clean_leads(session, [lead for _, lead in pairs], verifier)
    logger.info("сбор продаж: лиды заведены", extra={"run": run.id, "leads": len(pairs)})
    return {"created": len(pairs), "cleaning": clean_jobs.report_of(found)}
