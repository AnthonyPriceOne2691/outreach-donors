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
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import ColumnElement, Exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.access.repository import AccessRepository
from backend.features.contacts.preference import DEAD, preferred_first
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.sales import clean_jobs, cleaning, geo
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


async def _ready(session: AsyncSession, run_id: int) -> list[tuple[Candidate, int, str]]:
    """Принятые кандидаты прогона без лида — и лучший живой адрес их домена."""
    best = (
        select(ContactModel.domain_id, ContactModel.id, ContactModel.email)
        .distinct(ContactModel.domain_id)
        .where(_alive())
        .order_by(ContactModel.domain_id, *preferred_first())
        .subquery()
    )
    rows = await session.execute(
        select(Candidate, best.c.id, best.c.email)
        .join(best, best.c.domain_id == Candidate.domain_id)
        .where(
            Candidate.run_id == run_id,
            Candidate.lane == Lane.ACCEPT,
            Candidate.lead_id.is_(None),
        )
        .order_by(Candidate.id)
    )
    return list(rows.tuples().all())


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
    session: AsyncSession, run: SalesSegmentRunModel, verifier: EmailVerifier
) -> dict[str, Any]:
    """Завести лидов принятым с адресом и очистить их. Коммит — за вызывающим: лиды,
    ссылки кандидатов, журнал, итог очистки и строка расхода проверки — одна транзакция."""
    ready = await _ready(session, run.id)
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
