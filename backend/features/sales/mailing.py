"""Рассылка продаж — адреса частями: каждый лид продаж ровно в одной части, и части складываются в
итог по гипотезе и по всем (вкладка «Рассылка», `GET /sales/mailing`). Без записи.

Части по старшинству — лид берёт первую, которая о нём верна:
- **ушло** (`sent`) — правило шага «отправлено» воронки без периода (`funnel.sent`: письмо цепочки
  ушло, дошло или вернулось) и письмо цепочки в полёте (`sending`): его уже взяла отправка, а в
  очереди пачки и прогноза его нет (`queued_count` считает только «в очереди»);
- **в очереди** (`queued`) — первое письмо «в очереди» отбором очереди пачки и прогноза
  (`LetterRepository.queued_count(stage=Stage.SALES)`: первое письмо, «в очереди», рассылка
  продаж). Письма, которые не уйдут, пока их не поправят (`autosend_window.queue_stuck`), — здесь
  же: письмо в очереди есть;
- **без письма** — лид «готов», а письма нет или оно снято до отправки и пишется заново
  (`off_queue.ready_unwritten`); о каждом решает сборка (`queue_plan.decisions`): **напишет**
  (`will_write`, их число — `writable_counts`, запас сборки прогноза) или **не напишет** сейчас
  (`wont_write`) — с причиной словами её отчёта;
- **не готов** (`not_ready`) — лид «новый»: на очистке;
- **снят** (`removed`) — лид «отклонён» (причина словами экрана лидов) или «готов», а первое письмо
  снято и заново не пишется (словами снятия) — или его нет вовсе, а диалог есть.

**Части — построением, а не подгонкой.** Лиды без письма — те, о ком решает сборка, а остальных
делит одно выражение по старшинству (`_rest`; условие «без письма» — то же, что у сборки, —
исключено): лид попадает ровно в одну часть, и итог — сумма частей, а не отдельный счёт, из которого
какая-то часть выводилась бы остатком. Письма в очереди или ушедшего у лида без письма не бывает:
диалога у него нет — или первое письмо снято до отправки, без нашего `Message-ID`, а добивки
рождаются только после ушедшего первого; поэтому место «без письма» в старшинстве ничего не меняет.
Счёт — агрегатом SQL, решение сборки — её партией (кандидаты одним запросом, цепочка — одна на пару
«гипотеза, язык»): вкладка спрашивает раз в минуту.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Subquery, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import funnel, queue_plan
from backend.features.sales.clean_report import REASON_LABELS
from backend.features.sales.models import (
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.off_queue import ready_unwritten


class Part(StrEnum):
    """Части адресов — в порядке старшинства."""

    SENT = "sent"
    QUEUED = "queued"
    WILL_WRITE = "will_write"
    WONT_WRITE = "wont_write"
    NOT_READY = "not_ready"
    REMOVED = "removed"


#: Причина не записана: у отклонённого лида нет кода, у снятого письма — слов.
UNSAID = "причина не записана"
#: Лид «готов», первое письмо снято и заново не пишется — слова снятия идут после двоеточия.
STOPPED = "первое письмо снято и заново не пишется"
#: Лид «готов», диалог есть, а первого письма в нём нет: сборка такому лиду не пишет (её лиды — без
#: диалога, с письмом в очереди или снятым до отправки, `queue._leads`).
NO_FIRST = "диалог есть, а первого письма в нём нет — сборка такому лиду не пишет"


@dataclass(frozen=True, slots=True)
class Why:
    """Причина словами и сколько лидов по ней."""

    words: str
    count: int


@dataclass(frozen=True, slots=True)
class Parts:
    """Адреса гипотезы (или всех) частями; причины — старшие по числу первыми, их сумма — часть."""

    sent: int = 0
    queued: int = 0
    will_write: int = 0
    wont_write: int = 0
    not_ready: int = 0
    removed: int = 0
    wont_write_why: tuple[Why, ...] = ()
    removed_why: tuple[Why, ...] = ()

    @property
    def total(self) -> int:
        """Всего лидов — сумма частей: каждый лид ровно в одной."""
        return (
            self.sent
            + self.queued
            + self.will_write
            + self.wont_write
            + self.not_ready
            + self.removed
        )


@dataclass(frozen=True, slots=True)
class Row:
    """Адреса одной гипотезы."""

    hypothesis_id: int
    name: str
    parts: Parts


@dataclass(frozen=True, slots=True)
class Board:
    """Вкладка «Рассылка»: строка на гипотезу (и без лидов — с нулями), по номеру, и итог по ним."""

    rows: list[Row]
    total: Parts


@dataclass(slots=True)
class _Tally:
    """Счёт одной гипотезы, пока он собирается: части и причины словами."""

    counts: Counter[str] = field(default_factory=Counter)
    wont: Counter[str] = field(default_factory=Counter)
    removed: Counter[str] = field(default_factory=Counter)

    def add(self, other: _Tally) -> None:
        self.counts.update(other.counts)
        self.wont.update(other.wont)
        self.removed.update(other.removed)

    def parts(self) -> Parts:
        """Части; «не напишет» и «снят» — суммой своих причин: число и причины не расходятся."""
        return Parts(
            sent=self.counts[Part.SENT],
            queued=self.counts[Part.QUEUED],
            will_write=self.counts[Part.WILL_WRITE],
            wont_write=sum(self.wont.values()),
            not_ready=self.counts[Part.NOT_READY],
            removed=sum(self.removed.values()),
            wont_write_why=_ranked(self.wont),
            removed_why=_ranked(self.removed),
        )


def _ranked(found: Counter[str]) -> tuple[Why, ...]:
    """Причины — старшие по числу первыми, равные — по словам: порядок не скачет между ответами."""
    return tuple(Why(words, count) for words, count in sorted(found.items(), key=_rank))


def _rank(item: tuple[str, int]) -> tuple[int, str]:
    return -item[1], item[0]


async def board(session: AsyncSession, *, now: datetime | None = None) -> Board:
    """Адреса частями по гипотезам и итог — без записи (см. модуль)."""
    tallies: dict[int, _Tally] = {}
    for hypothesis_id, part, status, code, first, stopped, count in await _rest(session):
        found = tallies.setdefault(hypothesis_id, _Tally())
        if part == Part.REMOVED:
            found.removed[_removed(status, code, first=first, stopped=stopped)] += count
        else:
            found.counts[part] += count
    decided = await queue_plan.decisions(session, now=now)
    for hypothesis_id, count in decided.writes.items():
        tallies.setdefault(hypothesis_id, _Tally()).counts[Part.WILL_WRITE] += count
    for hypothesis_id, waits in decided.waits.items():
        tallies.setdefault(hypothesis_id, _Tally()).wont.update(waits)
    hypotheses = await session.execute(
        select(SalesHypothesisModel.id, SalesHypothesisModel.name).order_by(SalesHypothesisModel.id)
    )
    rows = [
        Row(hypothesis_id, name, tallies.get(hypothesis_id, _Tally()).parts())
        for hypothesis_id, name in hypotheses.tuples().all()
    ]
    return Board(rows, _sum(tallies.get(row.hypothesis_id, _Tally()) for row in rows))


def _sum(tallies: Iterable[_Tally]) -> Parts:
    """Итог — по строкам гипотез: лид принадлежит одной гипотезе."""
    every = _Tally()
    for found in tallies:
        every.add(found)
    return every.parts()


def _waiting() -> ColumnElement[bool]:
    """Первое письмо «в очереди» рассылки продаж — отбор очереди пачки и прогноза
    (`LetterRepository._waiting` у `queued_count(stage=Stage.SALES)`)."""
    return and_(
        MessageModel.status == MessageStatus.QUEUED,
        MessageModel.step == FIRST_STEP,
        CampaignModel.stage == Stage.SALES,
    )


def _letters() -> Subquery:
    """Письма цепочки лида одной строкой — без наших ответов в переписке, как у воронки: в полёте ли
    хоть одно, стоит ли первое в очереди (`_waiting`), есть ли первое и почему оно снято."""
    first = MessageModel.step == FIRST_STEP
    stopped = and_(first, MessageModel.status == MessageStatus.STOPPED)
    return (
        select(
            SalesThreadModel.lead_id.label("lead_id"),
            func.bool_or(MessageModel.status == MessageStatus.SENDING).label("sending"),
            func.bool_or(_waiting()).label("queued"),
            func.bool_or(first).label("first"),
            func.min(MessageModel.failure_reason).filter(stopped).label("stopped"),
        )
        .join(
            MessageModel,
            and_(
                MessageModel.thread_id == SalesThreadModel.thread_id,
                MessageModel.answers_reply_id.is_(None),
            ),
        )
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .group_by(SalesThreadModel.lead_id)
        .subquery("letters")
    )


async def _rest(session: AsyncSession) -> Sequence[Any]:
    """Лиды, кроме лидов без письма (о них решает сборка), — числом по гипотезе и части одним
    запросом; у снятых — и по тому, что называет причину: состояние и код отказа лида, есть ли
    первое письмо и почему оно снято."""
    path, mine = funnel.journey(), _letters()
    gone = or_(funnel.sent(path, funnel.Period()), mine.c.sending.is_(True))
    part = case(
        (gone, literal(Part.SENT.value)),
        (mine.c.queued.is_(True), literal(Part.QUEUED.value)),
        (SalesLeadModel.status == LeadStatus.NEW, literal(Part.NOT_READY.value)),
        else_=literal(Part.REMOVED.value),
    )
    leads = (
        select(
            SalesLeadModel.hypothesis_id.label("hypothesis_id"),
            part.label("part"),
            SalesLeadModel.status.label("status"),
            SalesLeadModel.rejection_reason.label("code"),
            mine.c.first.label("first"),
            mine.c.stopped.label("stopped"),
        )
        .select_from(SalesLeadModel)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .outerjoin(path, path.c.lead_id == SalesLeadModel.id)
        .outerjoin(mine, mine.c.lead_id == SalesLeadModel.id)
        .where(~ready_unwritten())
        .subquery("leads")
    )
    removed = leads.c.part == Part.REMOVED.value
    keys = (
        leads.c.hypothesis_id,
        leads.c.part,
        case((removed, leads.c.status)),
        case((removed, leads.c.code)),
        case((removed, leads.c.first)),
        case((removed, leads.c.stopped)),
    )
    rows = await session.execute(select(*keys, func.count()).group_by(*keys))
    return rows.tuples().all()


def _removed(
    status: LeadStatus | None, code: str | None, *, first: bool | None, stopped: str | None
) -> str:
    """Почему лид снят — словами: отклонённый — причиной отказа, «готов» — снятым первым письмом
    или его отсутствием."""
    if status is LeadStatus.REJECTED:
        return rejected(code)
    if not first:
        return NO_FIRST
    return f"{STOPPED}: {stopped or UNSAID}"


def rejected(code: str | None) -> str:
    """Причина отказа лида словами экрана лидов (`clean_report.REASON_LABELS` — те же слова у фильтра
    экрана): незнакомый код — общими словами с кодом в скобках, как у экрана; кода нет — так и
    сказано."""
    if not code:
        return UNSAID
    known = {reason.value: words for reason, words in REASON_LABELS.items()}
    return known.get(code, f"другая причина ({code})")
