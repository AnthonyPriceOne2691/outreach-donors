"""Главная: сколько чего в базе и что ждёт человека — одним запросом.

Числа здесь не свои. Каждое посчитано тем же правилом, что на экране,
где его видят подробно: состояние диалога — функцией списка диалогов,
очередь форм — функцией экрана форм, расход — таблицей экрана расхода.
Своё правило для главной разошлось бы с экраном при первой правке одного
из них, и сводка показывала бы «ждут разбора 3» над списком, где их два.

**«Ждут человека» отдельно от остального.** Сводка отвечает на два
вопроса: где мы и что делать сейчас. Работа, спрятанная среди итогов,
выглядит итогом — и человек закрывает вкладку.

**Доноры — воронка от проверенных доменов до цены** (решение 26.09.2026).
Запись в `donors` есть у каждого домена, за чьи метрики заплатил прогон, —
это «проверено доменов», а не доноры. Донор — принятый человеком
(`donors/standing.py`), и всё, что ниже него в воронке, — адрес, письмо,
ответ, цена — считается среди доноров: плитка «С адресом» и список
`/donors?has_contact=true` отвечают на один вопрос одним числом. Адреса,
найденные до правила «ищем только принятым», в воронку не входят — пока
домен не принят, он не донор, и адрес у него ничего не значит.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.config import filters as filters_cfg
from backend.features.contacts import forms
from backend.features.core.domain import (
    ContactStatus,
    DonorStatus,
    MessageStatus,
    Stage,
    UsageProvider,
)
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import review as advertiser_review
from backend.features.donors import standing
from backend.features.outreach.repository import OutreachRepository, ThreadRow
from backend.features.outreach.threads import ThreadState
from backend.features.review.candidates import Decision
from backend.features.runs.browse import RunBrowser, RunRow
from backend.features.runs.spending import SpendingRepository

#: Письмо ушло: отказ доставки — тоже ушедшее письмо, просто не дошедшее.
_GONE = (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)

#: Донор ответил человеком — в любом из состояний, куда ведёт ответ.
_ANSWERED = frozenset(
    {
        ThreadState.REPLIED,
        ThreadState.NEEDS_REVIEW,
        ThreadState.PRICED,
        ThreadState.DECLINED,
        ThreadState.FREE,
    }
)


@dataclass(frozen=True, slots=True)
class DonorCounts:
    """Воронка: от проверенных доменов до доноров с ценой.

    `total`, `unchecked` и `suitable` — все записи `donors` (домены, за чьи
    метрики заплачено); `accepted` — доноры; всё ниже — среди доноров.
    """

    #: Проверено доменов: у каждого куплены метрики.
    total: int
    #: Метрик у домена не нашлось — не отсеян, его добирают позже.
    unchecked: int
    suitable: int
    #: Доноры — принятые человеком.
    accepted: int
    rejected: int
    #: Доноры с найденным адресом — то же правило, что у фильтра списка.
    with_email: int
    #: Доноры с формой вместо адреса — их ведут руками.
    form_only: int
    #: Скольким донорам ушло хотя бы одно письмо.
    written: int
    #: Сколько доноров ответили человеком, а не автоответчиком.
    replied: int
    priced: int
    #: Цена не старше срока годности — по ней можно работать.
    priced_fresh: int


@dataclass(frozen=True, slots=True)
class Waiting:
    """Работа, которую без человека не сделает никто."""

    #: Доменов ждут решения в очередях прогонов (домен — один раз).
    review: int
    #: Прогоны, в очередях которых они ждут, новые первыми.
    review_runs: list[int]
    #: Ответов доноров, где цену подтверждает человек.
    prices: int
    #: Ответов рекламодателей, ещё не взятых в работу.
    leads: int
    forms: int
    #: Спорных рекламодателей на ручной проверке.
    advertisers: int


@dataclass(frozen=True, slots=True)
class LetterCounts:
    """Письма одного этапа."""

    queued: int
    #: Ушло всего, с отказами доставки.
    sent: int
    delivered: int
    bounced: int


@dataclass(frozen=True, slots=True)
class Overview:
    donors: DonorCounts
    waiting: Waiting
    letters: dict[Stage, LetterCounts]
    #: Последний прогон — той же строкой, что в истории прогонов.
    last_run: RunRow | None
    #: Юниты Ahrefs, потраченные нами с начала месяца, — по своей таблице.
    ahrefs_units: int
    ahrefs_cap: int
    serp_usd: Decimal


async def overview(session: AsyncSession, *, now: datetime | None = None) -> Overview:
    """Собрать главную. Каждое число — правилом своего экрана."""
    moment = now or datetime.now(UTC)
    # Все диалоги, а не последние двести, как в списке: сводка считает по всем.
    # Состояние выводится в питоне правилом `summarize`, поэтому грузятся
    # и письма. На тысячах диалогов запрос станет заметным — тогда счёт
    # переводить в SQL, сохранив правило одним местом.
    threads = await OutreachRepository(session).threads(limit=None)
    spending = await SpendingRepository(session).since_month_start(now=moment)
    decisions = await standing.waiting(session)
    return Overview(
        donors=await _donors(session, threads, moment),
        waiting=Waiting(
            review=decisions.domains,
            review_runs=decisions.runs,
            prices=_in_state(threads, ThreadState.NEEDS_REVIEW),
            leads=_in_state(threads, ThreadState.LEAD),
            forms=await forms.total(session),
            advertisers=await advertiser_review.waiting(session),
        ),
        letters=await _letters(session),
        last_run=await _last_run(session),
        ahrefs_units=spending.units_by_provider.get(UsageProvider.AHREFS, 0),
        ahrefs_cap=ahrefs_cfg.UNITS_CAP,
        serp_usd=spending.amount_by_provider.get(UsageProvider.SERP, Decimal(0)),
    )


async def _donors(
    session: AsyncSession, threads: Sequence[ThreadRow], now: datetime
) -> DonorCounts:
    fresh_since = now - timedelta(days=filters_cfg.PRICE_TTL_DAYS)
    donor = standing.is_donor()
    row = (
        await session.execute(
            select(
                func.count(DonorModel.id),
                _count(DonorModel.status == DonorStatus.UNCHECKED),
                _count(DonorModel.status == DonorStatus.SUITABLE),
                _count(donor),
                _count(DonorModel.review == Decision.REJECTED.value),
                _count(and_(donor, DonorModel.contact_status == ContactStatus.FOUND)),
                _count(and_(donor, DonorModel.contact_status == ContactStatus.FORM_ONLY)),
                _count(and_(donor, DonorModel.last_price.is_not(None))),
                _count(and_(donor, DonorModel.last_price_at >= fresh_since)),
            )
        )
    ).one()
    total, unchecked, suitable, accepted, rejected, email, form, priced, fresh = row
    donors = set((await session.execute(select(DonorModel.domain_id).where(donor))).scalars().all())
    return DonorCounts(
        total=int(total),
        unchecked=int(unchecked),
        suitable=int(suitable),
        accepted=int(accepted),
        rejected=int(rejected),
        with_email=int(email),
        form_only=int(form),
        written=await _written(session),
        replied=len(
            {
                thread.thread.domain_id
                for thread in threads
                if thread.stage is Stage.DONORS
                and thread.summary.state in _ANSWERED
                and thread.thread.domain_id in donors
            }
        ),
        priced=int(priced),
        priced_fresh=int(fresh),
    )


def _count(condition: ColumnElement[bool]) -> ColumnElement[int]:
    """Счёт строк по условию внутри одного запроса."""
    return func.count().filter(condition)


def _in_state(threads: Sequence[ThreadRow], state: ThreadState) -> int:
    return sum(1 for row in threads if row.summary.state is state)


async def _written(session: AsyncSession) -> int:
    """Скольким донорам ушло письмо — по письмам, а не по диалогам: диалог
    заводится на адрес, и у донора их бывает несколько. Среди доноров:
    домен, решение по которому сменилось, из воронки выходит целиком."""
    return int(
        await session.scalar(
            select(func.count(func.distinct(MessageModel.domain_id)))
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .join(DonorModel, DonorModel.domain_id == MessageModel.domain_id)
            .where(CampaignModel.stage == Stage.DONORS, MessageModel.status.in_(_GONE))
            .where(standing.is_donor())
        )
        or 0
    )


async def _letters(session: AsyncSession) -> dict[Stage, LetterCounts]:
    rows = await session.execute(
        select(CampaignModel.stage, MessageModel.status, func.count())
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .group_by(CampaignModel.stage, MessageModel.status)
    )
    by_stage: dict[Stage, dict[MessageStatus, int]] = {stage: {} for stage in Stage}
    for stage, status, count in rows.tuples().all():
        by_stage[stage][status] = int(count)
    return {
        stage: LetterCounts(
            queued=counts.get(MessageStatus.QUEUED, 0),
            sent=sum(counts.get(status, 0) for status in _GONE),
            delivered=counts.get(MessageStatus.DELIVERED, 0),
            bounced=counts.get(MessageStatus.BOUNCED, 0),
        )
        for stage, counts in by_stage.items()
    }


async def _last_run(session: AsyncSession) -> RunRow | None:
    """Последний прогон — строкой истории, а не своей выборкой: числа
    у него на главной обязаны совпадать с таблицей прогонов."""
    run_id = await session.scalar(select(RunModel.id).order_by(RunModel.id.desc()).limit(1))
    return None if run_id is None else await RunBrowser(session).one(run_id)
