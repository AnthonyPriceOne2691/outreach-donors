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

**Числа — этапов, которые видит спрашивающий** (`stages`, решение Anthony
10.10.2026, П2): без права «Продажи» письма и ответы продаж не входят ни в
сводку, ни в числа меню — считаются доноры и рекламодатели. С правом — как было.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.config import filters as filters_cfg
from backend.features.contacts import forms
from backend.features.core.domain import (
    GONE_STATUSES,
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
from backend.features.letters.chain import FIRST_STEP
from backend.features.outreach.repository import EVERY_STAGE, OutreachRepository, ThreadMark
from backend.features.outreach.threads import ThreadState
from backend.features.replies import unbound
from backend.features.review.candidates import Decision
from backend.features.runs.browse import RunBrowser, RunRow
from backend.features.runs.spending import SpendingRepository

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

#: Диалог ждёт человека: разобрать цену, взять лида. То же множество, что у списка
#: «Диалогов» (`frontend/src/threads/useThreadList.ts`, `WAITS_FOR_PERSON`): число
#: у пункта меню и «Ждут человека: N» на экране диалогов — одно число.
WAITS_FOR_PERSON = frozenset(
    {ThreadState.NEEDS_REVIEW, ThreadState.LEAD, ThreadState.SALES_PENDING}
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
    #: Ответов, не привязанных ни к одному нашему письму, — тем же условием,
    #: что у вкладки «Не привязаны» (`replies/unbound.py`). Не в «Ждут
    #: человека»: убрать такой ответ оттуда пока нечем, и плитка стояла бы
    #: янтарной вечно — с первого же ответа на пробное письмо.
    unbound_replies: int


@dataclass(frozen=True, slots=True)
class Work:
    """Числа у пунктов меню: сколько в разделе ждёт человека (аудит экранов 09.10.2026).

    Только «Ждут человека», без воронки, писем и расхода: меню спрашивает числа
    с каждого экрана раз в минуту, а сводку целиком — только «Обзор».
    """

    #: «Прогон» — доменов ждут решения в очередях прогонов («Рассмотреть домены»).
    run: int
    #: «Диалоги» — диалогов ждут человека (`WAITS_FOR_PERSON`).
    threads: int
    forms: int
    #: «Рекламодатели» — спорных на ручной проверке.
    advertisers: int


async def overview(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stages: Collection[Stage] = EVERY_STAGE,
) -> Overview:
    """Собрать главную. Каждое число — правилом своего экрана; письма и диалоги — только
    видимых этапов (`stages`)."""
    moment = now or datetime.now(UTC)
    # Все диалоги, состоянием по правилу списка (`threads.state_of`) — одним местом
    # правила, а не его копией в SQL: сумму в автоответе ищет питон. Из базы — только
    # то, что правило читает: статусы писем и поля ответов без текстов (аудит 10.10.2026).
    threads = await OutreachRepository(session).states(stages=stages)
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
        letters=await _letters(session, stages),
        last_run=await _last_run(session),
        ahrefs_units=spending.units_by_provider.get(UsageProvider.AHREFS, 0),
        ahrefs_cap=ahrefs_cfg.UNITS_CAP,
        serp_usd=spending.amount_by_provider.get(UsageProvider.SERP, Decimal(0)),
        unbound_replies=await unbound.total(session),
    )


async def work(session: AsyncSession, *, stages: Collection[Stage] = EVERY_STAGE) -> Work:
    """Числа меню — теми же правилами, что «Ждут человека» на главной, и тех же этапов."""
    threads = await OutreachRepository(session).states(stages=stages)
    return Work(
        run=(await standing.waiting(session)).domains,
        threads=sum(1 for mark in threads if mark.state in WAITS_FOR_PERSON),
        forms=await forms.total(session),
        advertisers=await advertiser_review.waiting(session),
    )


async def _donors(
    session: AsyncSession, threads: Sequence[ThreadMark], now: datetime
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
                mark.domain_id
                for mark in threads
                if mark.stage is Stage.DONORS
                and mark.state in _ANSWERED
                and mark.domain_id in donors
            }
        ),
        priced=int(priced),
        priced_fresh=int(fresh),
    )


def _count(condition: ColumnElement[bool]) -> ColumnElement[int]:
    """Счёт строк по условию внутри одного запроса."""
    return func.count().filter(condition)


def _in_state(threads: Sequence[ThreadMark], state: ThreadState) -> int:
    return sum(1 for mark in threads if mark.state is state)


async def _written(session: AsyncSession) -> int:
    """Скольким донорам ушло письмо — по письмам, а не по диалогам: диалог
    заводится на адрес, и у донора их бывает несколько. Среди доноров:
    домен, решение по которому сменилось, из воронки выходит целиком."""
    return int(
        await session.scalar(
            select(func.count(func.distinct(MessageModel.domain_id)))
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .join(DonorModel, DonorModel.domain_id == MessageModel.domain_id)
            .where(CampaignModel.stage == Stage.DONORS, MessageModel.status.in_(GONE_STATUSES))
            .where(standing.is_donor())
        )
        or 0
    )


async def _letters(session: AsyncSession, stages: Collection[Stage]) -> dict[Stage, LetterCounts]:
    """Письма по видимым этапам. «В очереди» — первые письма, как на экране писем,
    куда ведёт число: добивка и ответ ждут своим путём (`letters/mailbox.py`),
    и с ними главная разошлась бы с экраном. Этапа, которого спрашивающий не видит,
    в ответе нет вовсе, а не нулями: нули читались бы как «писем продаж нет»."""
    rows = await session.execute(
        select(CampaignModel.stage, MessageModel.status, func.count())
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(or_(MessageModel.status != MessageStatus.QUEUED, MessageModel.step == FIRST_STEP))
        .where(CampaignModel.stage.in_(stages))
        .group_by(CampaignModel.stage, MessageModel.status)
    )
    by_stage: dict[Stage, dict[MessageStatus, int]] = {
        stage: {} for stage in Stage if stage in stages
    }
    for stage, status, count in rows.tuples().all():
        by_stage[stage][status] = int(count)
    return {
        stage: LetterCounts(
            queued=counts.get(MessageStatus.QUEUED, 0),
            sent=sum(counts.get(status, 0) for status in GONE_STATUSES),
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
