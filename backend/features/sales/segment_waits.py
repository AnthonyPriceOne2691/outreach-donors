"""Что в прогоне ждёт повтора — словами, сроком; что возьмёт «Повторить сейчас» и сколько заплатит.

Разбор прогона сбора 20.10: у «ждут очистки» и «без адреса» в отчёте прогона не было ни
причины, ни кнопки — лид, за которого Hunter «ещё проверял», и сайт, который не ответил, висели
молча. Теперь отчёт (`segment_report.run_view`) несёт строки ожидания (`Wait`) и предложения
повтора (`Offer`):

- **ждут адреса** — сайты, не ответившие поиску: срок впереди — «срок ещё не настал» и когда
  повтор пойдёт сам (`segment_queue.retry_at`), без кнопки; срок прошёл, а повтора ещё не было
  (задачу потеряла очередь, воркер лежал) — «наступил», с кнопкой. «Не спросили» платный сервис —
  строкой, только когда платная ступень у повтора есть (`segment_queue.paid_left`): без неё
  повтор отправил бы сайт в «адреса нет»;
- **ждут очистки** — новые лиды прогона: Hunter «ещё проверяет» (срок — `verify_again.py`;
  впереди — без кнопки, прошёл — с кнопкой), первый `unknown` ждёт дня перепроверки (платная
  проверка раньше срока — трата впустую, кнопки нет), «ещё не очищали» и прочие — одной строкой,
  без слов лида: слова лида могли устареть («повторим следующей очисткой»), а разбирать их по
  словам — правило одного языка. Почему у каждого — на вкладке лидов.

**Число у кнопки — то, что она возьмёт** (ревью 20.10): предложение (`Offer`) считается тем же
условием, что у прохода повтора (`SegmentContactQueue` с `Retry.NOW`, `waiting_leads`), и строки
с кнопкой в сумме дают его же. Рядом — сколько платных запросов Hunter самое большее: у поиска —
не больше остатка сметы, у очистки — проверка на лида при живом проверяльщике.

Срок — моментом (UTC), а не словами: время по часам человека пишет экран.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.models import LeadStatus, SalesLeadModel
from backend.features.sales.segment_models import SalesSegmentCandidateModel, SalesSegmentRunModel
from backend.features.sales.segment_queue import (
    UNASKED,
    Retry,
    SegmentContactQueue,
    needs_address,
    paid_left,
)
from backend.features.sales.segment_sites import address_words
from backend.features.sales.unconfirmed import recheck_on
from backend.features.sales.verifier import waits
from backend.features.sales.verify_again import CHECKING, ahead

Candidate = SalesSegmentCandidateModel

#: Что ждёт: адрес сайта или очистка лида.
ADDRESS = "address"
CLEANING = "cleaning"
#: Что повторяет кнопка у группы (`segment_run.Again`).
ADDRESSES = "addresses"
#: Сайт не ответил поиску адреса — повтор пойдёт сам.
SILENT = "сайт не ответил поиску адреса"
#: Лид прогона, которого очистка ещё не видела.
NOT_CLEANED = "ещё не очищали"
#: Очистка видела лида, а он ждёт: проверка не выполнена, ждёт держателя компании и прочее.
OTHER = "проверка не выполнена или лид ждёт — почему, сказано у каждого лида на вкладке лидов"


@dataclass(frozen=True, slots=True)
class Wait:
    """Строка ожидания: что ждёт, почему словами, сколько, срок повтора (`at`, UTC; пусто — срока
    нет) и берёт ли её «Повторить сейчас» (`again`)."""

    kind: str
    words: str
    count: int
    at: datetime | None = None
    again: bool = True


@dataclass(frozen=True, slots=True)
class Offer:
    """«Повторить сейчас» у группы: что повторит (`what`, `segment_run.Again`), скольких возьмёт
    самое большее и до скольких платных запросов Hunter — поисков адреса (`searches`) и проверок
    адреса (`checks`: найденный адрес становится лидом, и его проверка тоже платная)."""

    what: str
    count: int
    searches: int
    checks: int


def leads_waiting(run_id: int, *, real: bool, now: datetime) -> list[ColumnElement[bool]]:
    """Условие «лид ждёт очистки, и кнопка его возьмёт»: новый лид прогона, кроме ждущих дня
    перепроверки `unknown` и тех, чей срок «ещё проверяет» впереди."""
    unknown_waits = SalesLeadModel.verify_again_at.is_(None) & waits(real, now)
    return [
        Candidate.run_id == run_id,
        SalesLeadModel.status == LeadStatus.NEW,
        ~ahead(now),
        ~unknown_waits,
    ]


async def waiting_leads(
    session: AsyncSession, run_id: int, *, real: bool, now: datetime, lock: bool = False
) -> list[SalesLeadModel]:
    """Лиды прогона, которых возьмёт «Повторить сейчас» у очистки (`leads_waiting`). `lock` —
    строки под блокировкой до конца транзакции; занятые задачей перепроверки — пропустить."""
    found = (
        select(SalesLeadModel)
        .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(*leads_waiting(run_id, real=real, now=now))
        .order_by(SalesLeadModel.id)
    )
    if lock:
        found = found.with_for_update(of=SalesLeadModel, skip_locked=True)
    return list((await session.scalars(found)).all())


async def _silent(session: AsyncSession, run_id: int, now: datetime) -> list[Wait]:
    """Сайты, не ответившие поиску: чей срок впереди — без кнопки, чей прошёл — с кнопкой."""
    terms = await SegmentContactQueue(session, run_id).silent_retries()
    future = [at for at in terms if at > now]
    passed = [at for at in terms if at <= now]
    found = [Wait(ADDRESS, SILENT, len(passed), min(passed))] if passed else []
    return found + (
        [Wait(ADDRESS, SILENT, len(future), min(future), again=False)] if future else []
    )


async def _unasked(session: AsyncSession, run_id: int) -> list[Wait]:
    """«Не спросили» платный сервис — по исходу, словами списка сайтов (`address_words`)."""
    rows = await session.execute(
        select(Candidate.contact_status, func.count(Candidate.id))
        .where(needs_address(run_id), Candidate.contact_status.in_(UNASKED))
        .group_by(Candidate.contact_status)
        .order_by(Candidate.contact_status)
    )
    return [
        Wait(ADDRESS, address_words(status), count)
        for status, count in rows.tuples().all()
        if status is not None  # условие выборки: исход есть
    ]


async def _cleaning(session: AsyncSession, run_id: int, *, real: bool, now: datetime) -> list[Wait]:
    """Новые лиды прогона по причине ожидания — без слов лида (шапка модуля)."""
    rows = await session.execute(
        select(
            SalesLeadModel.verify_again_at,
            SalesLeadModel.verified_at,
            waits(real, now),
            SalesLeadModel.cleaning_note.is_(None),
        )
        .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(Candidate.run_id == run_id, SalesLeadModel.status == LeadStatus.NEW)
    )
    checking: list[datetime] = []
    unknown: list[datetime] = []
    fresh = other = 0
    for again_at, verified_at, unconfirmed, never in rows.tuples().all():
        if again_at is not None:
            checking.append(again_at)
        elif unconfirmed and verified_at is not None:
            unknown.append(verified_at)
        elif never:
            fresh += 1
        else:
            other += 1
    passed = [at for at in checking if at <= now]
    future = [at for at in checking if at > now]
    found = [Wait(CLEANING, CHECKING, len(passed), min(passed))] if passed else []
    if future:
        found.append(Wait(CLEANING, CHECKING, len(future), min(future), again=False))
    if unknown:
        day = f"{recheck_on(min(unknown)):%d.%m.%Y}"
        words = f"адрес не подтверждён (unknown) — перепроверка не раньше {day}"
        found.append(Wait(CLEANING, words, len(unknown), again=False))
    found += [Wait(CLEANING, NOT_CLEANED, fresh)] if fresh else []
    return found + ([Wait(CLEANING, OTHER, other)] if other else [])


async def run_waits(
    session: AsyncSession, run: SalesSegmentRunModel, *, real: bool, now: datetime
) -> list[Wait]:
    """Что в прогоне ждёт повтора: адреса, затем очистка. «Не спросили» — строкой, только когда
    повтор их возьмёт (платная ступень есть)."""
    unasked = await _unasked(session, run.id) if paid_left(run.report, run.estimate) else []
    return [
        *await _silent(session, run.id, now),
        *unasked,
        *await _cleaning(session, run.id, real=real, now=now),
    ]


async def offer(
    session: AsyncSession, run: SalesSegmentRunModel, what: str, *, real: bool, now: datetime
) -> Offer:
    """Что возьмёт «Повторить сейчас» — тем же условием, что у прохода повтора, — и до скольких
    платных запросов Hunter: поисков — не больше остатка сметы, проверок — лид на проверку (у
    поиска — каждый найденный адрес)."""
    if what == ADDRESSES:
        left = paid_left(run.report, run.estimate)
        queue = SegmentContactQueue(session, run.id, retry=Retry.NOW, paid=left > 0, now=now)
        count = await queue.pending_count()
        return Offer(what, count, min(count, left), count if real else 0)
    count = int(
        await session.scalar(
            select(func.count(SalesLeadModel.id))
            .join(Candidate, Candidate.lead_id == SalesLeadModel.id)
            .where(*leads_waiting(run.id, real=real, now=now))
        )
        or 0
    )
    return Offer(what, count, 0, count if real else 0)


async def run_offers(
    session: AsyncSession, run: SalesSegmentRunModel, *, real: bool, now: datetime
) -> list[Offer]:
    """Предложения повтора у групп отчёта: только где есть что повторить."""
    found = [await offer(session, run, what, real=real, now=now) for what in (ADDRESSES, CLEANING)]
    return [one for one in found if one.count > 0]
