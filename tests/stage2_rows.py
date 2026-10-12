"""Строки Этапа 2 для тестов экранов и отчётов: обход, кандидаты, рекламодатели, письма.

Строки — такими, какими их пишет сервис: кандидат — от последнего обхода донора
(`crawl/gate.py`), рекламодатель — переводом (`crawl/promote.py`), письмо — сборкой
оффера в рассылку Этапа 2 со своим диалогом. Ничего не решают сами: решение человека,
адрес и исход письма тест называет явно.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.features.core.domain import (
    ContactSource,
    ContactStatus,
    CrawlOutcome,
    CrawlStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    StopReason,
    Verdict,
)
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: Когда «ушло» письмо, если тесту время не важно.
SENT_AT = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


async def crawled(session: AsyncSession, host: str, *, pages: int = 400) -> CrawlRunModel:
    """Законченный обход донора — по нему считаются кандидаты."""
    run = CrawlRunModel(
        host=host,
        status=CrawlStatus.DONE,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=pages,
        articles=pages,
        finished_at=SENT_AT,
    )
    session.add(run)
    await session.flush()
    return run


async def scored(
    session: AsyncSession,
    run: CrawlRunModel,
    target: str,
    *,
    verdict: Verdict = Verdict.BOUGHT,
    points: int | None = None,
    confirmed: bool | None = None,
) -> CandidateModel:
    """Кандидат обхода `run` на домен `target` с вердиктом скоринга и решением человека."""
    row = CandidateModel(
        crawl_run_id=run.id,
        donor_host=run.host,
        target_root=target,
        points=points if points is not None else (5 if verdict is Verdict.BOUGHT else 2),
        verdict=verdict,
        reasons=["rel=sponsored +5"],
        links=1,
        pages=1,
        best_page_url=f"https://{run.host}/post/{target}",
        best_anchor=f"best {target}",
        confirmed=confirmed,
        decided_by=None if confirmed is None else "человек@site.com",
        decided_at=None if confirmed is None else SENT_AT,
    )
    session.add(row)
    await session.flush()
    return row


async def domain_of(session: AsyncSession, host: str) -> DomainModel:
    """Домен по имени — заводится, если его нет: рекламодатель и донор делят его."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    return domain


async def promoted(
    session: AsyncSession,
    host: str,
    *,
    donor_host: str | None = "donor.example.test",
    points: int = 5,
    source: str = "links",
    confirmed: bool = False,
    decided: bool = False,
    contact_status: ContactStatus | None = None,
) -> AdvertiserModel:
    """Рекламодатель: строка `advertisers` с лучшей ссылкой. У бизнеса ниши
    (`source="niche"`) ссылки нет; `decided` — человек уже сказал «пишем / не пишем»."""
    domain = await domain_of(session, host)
    niche = source == "niche"
    row = AdvertiserModel(
        domain_id=domain.id,
        points=0 if niche else points,
        donors=0 if niche else 1,
        links=0 if niche else 1,
        best_donor_host=donor_host,
        best_page_url=None if niche else f"https://{donor_host}/post/{host}",
        best_anchor=None if niche else f"best {host}",
        source=source,
        confirmed_by_human=confirmed,
        decided_at=SENT_AT if decided else None,
        decided_by="человек@site.com" if decided else None,
        contact_status=contact_status,
    )
    session.add(row)
    await session.flush()
    return row


async def addressed(session: AsyncSession, host: str, email: str | None = None) -> ContactModel:
    """Адрес домена, найденный лестницей."""
    domain = await domain_of(session, host)
    contact = ContactModel(
        domain_id=domain.id, email=email or f"ads@{host}", source=ContactSource.PAGE
    )
    session.add(contact)
    await session.flush()
    return contact


async def campaign(session: AsyncSession, stage: Stage = Stage.ADVERTISERS) -> CampaignModel:
    """Рассылка этапа: одна на тест и этап."""
    found = await session.scalar(select(CampaignModel).where(CampaignModel.stage == stage).limit(1))
    if found is not None:
        return found
    made = CampaignModel(stage=stage, name=f"Проверка {stage.value}", status="running")
    session.add(made)
    await session.flush()
    return made


async def lettered(
    session: AsyncSession,
    host: str,
    status: MessageStatus,
    *,
    stage: Stage = Stage.ADVERTISERS,
    step: int = 0,
    attempt: int = 1,
) -> MessageModel:
    """Письмо домену в своём диалоге — со статусом `status`: собрано, ушло, не дошло…"""
    domain = await domain_of(session, host)
    contact = await session.scalar(
        select(ContactModel).where(ContactModel.domain_id == domain.id).limit(1)
    )
    contact = contact or await addressed(session, host)
    mailing = await campaign(session, stage)
    thread = await session.scalar(
        select(ThreadModel).where(
            ThreadModel.domain_id == domain.id,
            ThreadModel.campaign_id == mailing.id,
            ThreadModel.contact_id == contact.id,
        )
    )
    if thread is None:
        thread = ThreadModel(domain_id=domain.id, campaign_id=mailing.id, contact_id=contact.id)
        session.add(thread)
        await session.flush()
    gone = status in (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)
    letter = MessageModel(
        campaign_id=mailing.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=step,
        status=status,
        subject="Offer",
        body="We are cheaper.",
        sent_at=SENT_AT if gone else None,
        idempotency_key=f"{stage.value}:{host}:{step}:a{attempt}",
    )
    session.add(letter)
    await session.flush()
    return letter


async def answered(
    session: AsyncSession, letter: MessageModel, kind: ReplyKind = ReplyKind.HUMAN
) -> ReplyModel:
    """Ответ на письмо — в его диалоге."""
    reply = ReplyModel(
        thread_id=letter.thread_id,
        message_id=letter.id,
        kind=kind,
        raw_body="Interested, tell me more.",
        from_email="ceo@answer.example.test",
    )
    session.add(reply)
    await session.flush()
    return reply
