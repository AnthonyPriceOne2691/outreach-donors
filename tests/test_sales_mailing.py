"""Рассылка продаж — адреса частями (`sales/mailing.py`): каждый лид ровно в одной части.

Каждое состояние лида и письма — своим тестом: в какую часть и с какими словами причины, включая
странные (диалог без первого письма, первое письмо снято не по правилу `again`, «новый» с письмом,
добивка без первого письма). Затем все разом и случайный набор: итог гипотезы — число её лидов прямо
из базы, а части — те же числа, что считают другие экраны: «в очереди» — очередь пачки и прогноза
(`queued_count`), «ушло» — шаг «отправлено» воронки без периода и письма в полёте (сколько их —
`in_flight`, и «ушло» без них — ровно воронка, до исхода письма и после), «сборка напишет» — её счёт (`writable_counts`, запас прогноза), «не напишет» — её же слова о каждом лиде
(`queue._plan`). И счёт — партией: втрое больше лидов — столько же запросов. Мир — подключённые
продажи (`tests/test_sales_send_world.py`); адреса, домены и тексты выдуманы.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from backend.features.core import stage_forecast
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import attempts
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.sales import (
    autosend_refusals,
    chain,
    chain_text,
    elsewhere,
    funnel,
    hypotheses,
    mailing,
    off_queue,
    queue,
    queue_plan,
)
from backend.features.sales.clean_report import REASON_LABELS
from backend.features.sales.mailing import Part, Parts
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHandoffModel,
    SalesLeadModel,
    SalesStoplistModel,
    SalesThreadModel,
)
from backend.features.sales.queue_report import NO_LANGUAGE, no_chain
from backend.features.sales.unconfirmed import FIRST_LETTER
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_queue_plan import LONG_HOST, _statements

NOW = w.NOW
#: Причины снятого письма — выдуманными словами там, где их пишет не наш код.
REFUSED = autosend_refusals.words(3, "550 made-up mailbox is unavailable")
ELSEWHERE = attempts.elsewhere_reason(Stage.DONORS)
GOV = "info@bureau.gov.zz"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@dataclass
class Book:
    """Заводит лидов продаж в нужном состоянии — прямо в базе, без сборки и отправки."""

    session: AsyncSession
    hypothesis_id: int
    campaigns: dict[int, int] = field(default_factory=dict)
    made: int = 0

    async def lead(
        self, *, email: str | None = None, hypothesis_id: int | None = None, **fields: object
    ) -> SalesLeadModel:
        """Лид «готов» (или с полями `fields`) на своём выдуманном сайте."""
        self.made += 1
        address = email or f"lead{self.made}@site-{self.made}.example.test"
        owner = hypothesis_id or self.hypothesis_id
        return await w.lead(self.session, owner, address, **fields)

    async def _campaign(self, hypothesis_id: int) -> int:
        if hypothesis_id not in self.campaigns:
            made = CampaignModel(
                stage=Stage.SALES, name=f"Выдуманная рассылка №{hypothesis_id}", status="running"
            )
            self.session.add(made)
            await self.session.flush()
            self.campaigns[hypothesis_id] = made.id
        return self.campaigns[hypothesis_id]

    async def thread(self, lead: SalesLeadModel, *, link: bool = True) -> int:
        """Диалог лида в рассылке его гипотезы; `link` — со связью продаж, как у сборки."""
        campaign = await self._campaign(lead.hypothesis_id)
        made = ThreadModel(domain_id=lead.domain_id, campaign_id=campaign, contact_id=None)
        self.session.add(made)
        await self.session.flush()
        if link:
            self.session.add(
                SalesThreadModel(
                    thread_id=made.id,
                    lead_id=lead.id,
                    chain_hypothesis_id=None,
                    language="en",
                    chain_version="chain-madeup0001",
                )
            )
            await self.session.flush()
        return made.id

    async def letter(
        self,
        lead: SalesLeadModel,
        thread_id: int,
        status: MessageStatus,
        *,
        step: int = FIRST_STEP,
        reason: str | None = None,
        message_id: str | None = None,
    ) -> MessageModel:
        """Письмо цепочки диалога; ушедшее — со временем ухода."""
        self.made += 1
        made = MessageModel(
            campaign_id=await self._campaign(lead.hypothesis_id),
            thread_id=thread_id,
            domain_id=lead.domain_id,
            contact_id=None,
            step=step,
            status=status,
            subject="A made-up question",
            body="A made-up letter.",
            failure_reason=reason,
            internet_message_id=message_id,
            sent_at=NOW - timedelta(days=2) if status in funnel.GONE else None,
            idempotency_key=f"mailing-test:{self.made}:{step}",
        )
        self.session.add(made)
        await self.session.flush()
        return made

    async def first(
        self,
        lead: SalesLeadModel,
        status: MessageStatus,
        *,
        reason: str | None = None,
        message_id: str | None = None,
    ) -> int:
        """Диалог лида с первым письмом в состоянии `status`; номер диалога."""
        thread_id = await self.thread(lead)
        await self.letter(lead, thread_id, status, reason=reason, message_id=message_id)
        return thread_id

    async def busy(self, lead: SalesLeadModel) -> None:
        """Сайт лида в работе у доноров: их письмо в очереди — одна роль на сайт."""
        self.made += 1
        donors = CampaignModel(stage=Stage.DONORS, name=f"Доноры {self.made}", status="running")
        self.session.add(donors)
        await self.session.flush()
        self.session.add(
            MessageModel(
                campaign_id=donors.id,
                domain_id=lead.domain_id,
                step=FIRST_STEP,
                status=MessageStatus.QUEUED,
                idempotency_key=f"mailing-test:donors:{self.made}",
            )
        )
        await self.session.flush()


Make = Callable[[Book], Awaitable[int]]


@dataclass(frozen=True, slots=True)
class State:
    """Состояние лида и письма: в какую часть он ложится и с какими словами причины."""

    name: str
    part: Part
    words: str | None
    make: Make
    #: «Ушло» только письмом в полёте — лид и в `in_flight`.
    flying: bool = False


async def _ready(book: Book) -> int:
    return (await book.lead()).hypothesis_id


async def _again_elsewhere(book: Book) -> int:
    """Первое письмо сняла отправка: сайт был в работе у доноров, теперь свободен."""
    lead = await book.lead()
    await book.first(lead, MessageStatus.STOPPED, reason=ELSEWHERE)
    return lead.hypothesis_id


async def _again_closed(book: Book) -> int:
    """Лида сняли после сборки письма, а он снова «готов»."""
    lead = await book.lead()
    await book.first(lead, MessageStatus.STOPPED, reason=off_queue.closed("стоп-лист продаж"))
    return lead.hypothesis_id


async def _handed(book: Book) -> int:
    lead = await book.lead()
    thread_id = await book.thread(lead, link=False)
    book.session.add(SalesHandoffModel(thread_id=thread_id, lead_id=lead.id))
    await book.session.flush()
    return lead.hypothesis_id


async def _stoplist(book: Book) -> int:
    lead = await book.lead()
    book.session.add(SalesStoplistModel(email=lead.email))
    await book.session.flush()
    return lead.hypothesis_id


async def _unsubscribed(book: Book) -> int:
    lead = await book.lead()
    book.session.add(SuppressionModel(email=lead.email, reason=SuppressionReason.UNSUBSCRIBED))
    await book.session.flush()
    return lead.hypothesis_id


async def _not_writing(book: Book) -> int:
    return (await book.lead(email=GOV)).hypothesis_id


async def _held(book: Book) -> int:
    return (await book.lead(verification_status="made-up:unknown")).hypothesis_id


async def _busy(book: Book) -> int:
    lead = await book.lead()
    await book.busy(lead)
    return lead.hypothesis_id


async def _again_busy(book: Book) -> int:
    """Снятое письмо пишется заново, только когда сайт свободен; он всё ещё в работе."""
    lead = await book.lead()
    await book.first(lead, MessageStatus.STOPPED, reason=ELSEWHERE)
    await book.busy(lead)
    return lead.hypothesis_id


async def _no_name(book: Book) -> int:
    return (await book.lead(name=None)).hypothesis_id


async def _no_chain(book: Book) -> int:
    return (await book.lead(language="de")).hypothesis_id


async def _no_language(book: Book) -> int:
    return (await book.lead(language=None)).hypothesis_id


async def _long_key(book: Book) -> int:
    email = f"someone.with.a.long.address@{LONG_HOST}"
    return (await book.lead(email=email)).hypothesis_id


async def _incomplete(book: Book) -> int:
    """Своя английская цепочка гипотезы — один шаг из трёх."""
    own = (await hypotheses.add(book.session, f"Гипотеза с неполной цепочкой {book.made}", None)).id
    step = chain_text.step_template(step=1, language="en", subject=w.SUBJECT, body=w.FIRST_BODY)
    await chain.save(book.session, step, hypothesis_id=own, author="тест", author_id=None)
    return (await book.lead(hypothesis_id=own)).hypothesis_id


def _with_first(
    status: MessageStatus,
    lead_status: LeadStatus = LeadStatus.READY,
    *,
    reason: str | None = None,
    message_id: str | None = None,
) -> Make:
    """Лид (отклонённый — дублем) с диалогом и первым письмом в состоянии `status`."""

    async def made(book: Book) -> int:
        fields: dict[str, object] = {"status": lead_status}
        if lead_status is LeadStatus.REJECTED:
            fields["rejection_reason"] = RejectionReason.DUPLICATE.value
        lead = await book.lead(**fields)
        await book.first(lead, status, reason=reason, message_id=message_id)
        return lead.hypothesis_id

    return made


async def _new(book: Book) -> int:
    return (await book.lead(status=LeadStatus.NEW)).hypothesis_id


async def _followups(book: Book) -> int:
    """Первое письмо дошло, добивка тоже: лид — один."""
    lead = await book.lead()
    thread_id = await book.first(lead, MessageStatus.DELIVERED)
    await book.letter(lead, thread_id, MessageStatus.DELIVERED, step=1)
    return lead.hypothesis_id


async def _followup_sending(book: Book) -> int:
    """Первое письмо дошло, добивка в полёте: письмо лиду уже ушло — воронка его считает, и в
    `in_flight` его нет: исход неизвестен у добивки, а не у «ушло»."""
    lead = await book.lead()
    thread_id = await book.first(lead, MessageStatus.DELIVERED)
    await book.letter(lead, thread_id, MessageStatus.SENDING, step=1)
    return lead.hypothesis_id


async def _handed_after_reply(book: Book) -> int:
    lead = await book.lead()
    thread_id = await book.first(lead, MessageStatus.DELIVERED)
    book.session.add(SalesHandoffModel(thread_id=thread_id, lead_id=lead.id))
    await book.session.flush()
    return lead.hypothesis_id


async def _followup_without_first(book: Book) -> int:
    """Странное: в диалоге ушла добивка, а первого письма нет — письмо лиду ушло."""
    lead = await book.lead()
    await book.letter(lead, await book.thread(lead), MessageStatus.SENT, step=1)
    return lead.hypothesis_id


async def _queued_held(book: Book) -> int:
    """Письмо в очереди, а адрес держит проверка: не уйдёт, пока его не поправят, — но в очереди."""
    lead = await book.lead(verification_status="made-up:unknown")
    await book.first(lead, MessageStatus.QUEUED)
    return lead.hypothesis_id


def _rejected(code: str | None) -> Make:
    async def made(book: Book) -> int:
        lead = await book.lead(status=LeadStatus.REJECTED, rejection_reason=code)
        return lead.hypothesis_id

    return made


async def _rejected_closed(book: Book) -> int:
    """Лида закрыл стоп-лист продаж после сборки письма: письмо снято, лид отклонён."""
    lead = await book.lead(
        status=LeadStatus.REJECTED, rejection_reason=RejectionReason.STOPLIST.value
    )
    await book.first(lead, MessageStatus.STOPPED, reason=off_queue.closed("стоп-лист продаж"))
    return lead.hypothesis_id


async def _no_first(book: Book) -> int:
    """Странное: диалог есть, а писем в нём нет."""
    lead = await book.lead()
    await book.thread(lead)
    return lead.hypothesis_id


STATES = [
    # Без письма — сборка напишет.
    State("ready", Part.WILL_WRITE, None, _ready),
    State("again_elsewhere", Part.WILL_WRITE, None, _again_elsewhere),
    State("again_closed", Part.WILL_WRITE, None, _again_closed),
    # Без письма — сборка ждёт, словами её отчёта.
    State("handed", Part.WONT_WRITE, queue_plan.HANDED_OFF, _handed),
    State("stoplist", Part.WONT_WRITE, queue_plan.STOPLIST, _stoplist),
    State("unsubscribed", Part.WONT_WRITE, queue_plan.STOPLIST, _unsubscribed),
    State(
        "not_writing",
        Part.WONT_WRITE,
        "кому не пишем: гос. и учебным сайтам не пишем",
        _not_writing,
    ),
    State("held", Part.WONT_WRITE, FIRST_LETTER, _held),
    State("busy", Part.WONT_WRITE, elsewhere.waiting(Stage.DONORS), _busy),
    State("again_busy", Part.WONT_WRITE, elsewhere.waiting(Stage.DONORS), _again_busy),
    State("no_name", Part.WONT_WRITE, "у лида нет значения для {{name}}", _no_name),
    State("no_chain", Part.WONT_WRITE, no_chain("de"), _no_chain),
    State("no_language", Part.WONT_WRITE, NO_LANGUAGE, _no_language),
    State("long_key", Part.WONT_WRITE, queue_plan.LONG_KEY, _long_key),
    State("incomplete", Part.WONT_WRITE, queue_plan.incomplete("en"), _incomplete),
    # Ушло — шаг «отправлено» воронки и письмо в полёте.
    State("sending", Part.SENT, None, _with_first(MessageStatus.SENDING), flying=True),
    State(
        "rejected_sending",
        Part.SENT,
        None,
        _with_first(MessageStatus.SENDING, LeadStatus.REJECTED),
        flying=True,
    ),
    State("followup_sending", Part.SENT, None, _followup_sending),
    State("sent", Part.SENT, None, _with_first(MessageStatus.SENT)),
    State("bounced", Part.SENT, None, _with_first(MessageStatus.BOUNCED)),
    State("followups", Part.SENT, None, _followups),
    State("handed_after_reply", Part.SENT, None, _handed_after_reply),
    State("followup_without_first", Part.SENT, None, _followup_without_first),
    State("new_sent", Part.SENT, None, _with_first(MessageStatus.SENT, LeadStatus.NEW)),
    State(
        "rejected_sent", Part.SENT, None, _with_first(MessageStatus.DELIVERED, LeadStatus.REJECTED)
    ),
    # В очереди — отбором очереди пачки, как бы ни стоял лид.
    State("queued", Part.QUEUED, None, _with_first(MessageStatus.QUEUED)),
    State("queued_held", Part.QUEUED, None, _queued_held),
    State("new_queued", Part.QUEUED, None, _with_first(MessageStatus.QUEUED, LeadStatus.NEW)),
    State(
        "rejected_queued",
        Part.QUEUED,
        None,
        _with_first(MessageStatus.QUEUED, LeadStatus.REJECTED),
    ),
    # Не готов — на очистке, даже со снятым письмом.
    State("new", Part.NOT_READY, None, _new),
    State("new_stopped", Part.NOT_READY, None, _with_first(MessageStatus.STOPPED, LeadStatus.NEW)),
    # Снят — словами экрана лидов и снятия письма.
    State("rejected", Part.REMOVED, "дубль", _rejected(RejectionReason.DUPLICATE.value)),
    State("rejected_closed", Part.REMOVED, "стоп-лист продаж", _rejected_closed),
    State("rejected_unsaid", Part.REMOVED, mailing.UNSAID, _rejected(None)),
    State(
        "rejected_unknown",
        Part.REMOVED,
        "другая причина (made-up-reason)",
        _rejected("made-up-reason"),
    ),
    State(
        "refused",
        Part.REMOVED,
        f"{mailing.STOPPED}: {REFUSED}",
        _with_first(MessageStatus.STOPPED, reason=REFUSED),
    ),
    State(
        "stopped_unsaid",
        Part.REMOVED,
        f"{mailing.STOPPED}: {mailing.UNSAID}",
        _with_first(MessageStatus.STOPPED),
    ),
    State(
        "stopped_with_message_id",
        Part.REMOVED,
        f"{mailing.STOPPED}: {ELSEWHERE}",
        _with_first(
            MessageStatus.STOPPED, reason=ELSEWHERE, message_id="<made-up-1@mail.example.test>"
        ),
    ),
    State("no_first", Part.REMOVED, mailing.NO_FIRST, _no_first),
]


def _counts(parts: Parts) -> dict[Part, int]:
    """Ненулевые части."""
    found = {part: int(getattr(parts, part.value)) for part in Part}
    return {part: count for part, count in found.items() if count}


def _whys(parts: Parts) -> Counter[str]:
    return Counter({why.words: why.count for why in (*parts.wont_write_why, *parts.removed_why)})


def _row(board: mailing.Board, hypothesis_id: int) -> Parts:
    [found] = [row.parts for row in board.rows if row.hypothesis_id == hypothesis_id]
    return found


async def _leads_of(session: AsyncSession) -> dict[int, int]:
    """Лидов по гипотезам — прямо из базы, мимо счёта частей."""
    rows = await session.execute(
        select(SalesLeadModel.hypothesis_id, func.count()).group_by(SalesLeadModel.hypothesis_id)
    )
    return dict(rows.tuples().all())


def _invariant(board: mailing.Board, leads: dict[int, int]) -> None:
    """Итог — сумма частей и число лидов из базы; сумма причин — своя часть; итог — сумма строк."""
    for row in [*board.rows, mailing.Row(0, "итог", board.total)]:
        parts = row.parts
        assert parts.total == sum(_counts(parts).values())
        assert sum(why.count for why in parts.wont_write_why) == parts.wont_write
        assert sum(why.count for why in parts.removed_why) == parts.removed
        assert 0 <= parts.in_flight <= parts.sent
    assert {row.hypothesis_id: row.parts.total for row in board.rows} == {
        row.hypothesis_id: leads.get(row.hypothesis_id, 0) for row in board.rows
    }
    for part in Part:
        assert getattr(board.total, part.value) == sum(
            getattr(row.parts, part.value) for row in board.rows
        )
    assert board.total.in_flight == sum(row.parts.in_flight for row in board.rows)
    assert board.total.total == sum(leads.values())


async def _funnel_sent(session: AsyncSession) -> dict[int, int]:
    """Шаг «отправлено» воронки без периода — по гипотезам."""
    found = await funnel.counts(session, funnel.Period())
    return {hypothesis_id: numbers.sent for hypothesis_id, numbers in found.items() if numbers.sent}


def _sent_without_flying(board: mailing.Board) -> dict[int, int]:
    """«Ушло» без писем в полёте — по гипотезам."""
    rows = ((row.hypothesis_id, row.parts.sent - row.parts.in_flight) for row in board.rows)
    return {hypothesis_id: sent for hypothesis_id, sent in rows if sent}


# --- каждое состояние — в своей части ----------------------------------------------------------


@pytest.mark.parametrize("state", STATES, ids=lambda state: state.name)
async def test_each_state_of_a_lead_lands_in_one_part_with_its_words(
    session: AsyncSession, world: w.World, state: State
) -> None:
    hypothesis_id = await state.make(Book(session, world.hypothesis_id))

    found = await mailing.board(session, now=NOW)

    parts = _row(found, hypothesis_id)
    assert _counts(parts) == {state.part: 1}
    assert parts.in_flight == int(state.flying)
    assert _whys(parts) == (Counter({state.words: 1}) if state.words else Counter())
    _invariant(found, await _leads_of(session))


async def test_a_gone_letter_wins_over_a_queued_first_letter(
    session: AsyncSession, world: w.World
) -> None:
    """Странное и кодом недостижимое: первое письмо в очереди, а добивка уже ушла (добивка рождается
    только после ушедшего первого письма). По старшинству лид — в «ушло»; очередь пачки его письмо
    считает — единственный случай, где «в очереди» вкладки меньше `queued_first` прогноза."""
    book = Book(session, world.hypothesis_id)
    lead = await book.lead()
    thread_id = await book.first(lead, MessageStatus.QUEUED)
    await book.letter(lead, thread_id, MessageStatus.SENT, step=1)

    found = await mailing.board(session, now=NOW)

    assert _counts(_row(found, world.hypothesis_id)) == {Part.SENT: 1}
    assert await LetterRepository(session).queued_count(stage=Stage.SALES) == 1


# --- письмо в полёте: в «ушло», пока исход неизвестен ---------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "part"),
    [
        (MessageStatus.SENT, Part.SENT),
        (MessageStatus.BOUNCED, Part.SENT),
        (MessageStatus.QUEUED, Part.QUEUED),
    ],
    ids=["ушло", "вернулось", "снова в очереди"],
)
async def test_a_letter_in_flight_is_sent_until_its_outcome_and_then_sent_is_the_funnel(
    session: AsyncSession, world: w.World, outcome: MessageStatus, part: Part
) -> None:
    """Первое письмо «отправляется»: лид — в «ушло» и в `in_flight`, воронка его ещё не считает, и
    «ушло» без писем в полёте — её шаг «отправлено». Исход решён — в полёте никого, «ушло» =
    воронке: письмо ушло или вернулось — лид в «ушло», отправка вернула его в очередь — «в очереди»."""
    book = Book(session, world.hypothesis_id)
    await _with_first(MessageStatus.DELIVERED)(book)
    lead = await book.lead()
    flying = await book.letter(lead, await book.thread(lead), MessageStatus.SENDING)

    before = _row(await mailing.board(session, now=NOW), world.hypothesis_id)

    assert (before.sent, before.in_flight, before.queued) == (2, 1, 0)
    assert await _funnel_sent(session) == {world.hypothesis_id: 1}
    assert before.sent - before.in_flight == 1

    flying.status = outcome
    flying.sent_at = NOW - timedelta(minutes=1) if outcome in funnel.GONE else None
    await session.flush()
    after = await mailing.board(session, now=NOW)

    assert _counts(_row(after, world.hypothesis_id)) == dict(Counter([Part.SENT, part]))
    assert after.total.in_flight == 0
    assert {world.hypothesis_id: after.total.sent} == await _funnel_sent(session)
    _invariant(after, await _leads_of(session))


# --- все состояния разом: части — числа других экранов ------------------------------------------


async def _every_state(session: AsyncSession, world: w.World) -> Book:
    book = Book(session, world.hypothesis_id)
    for state in STATES:
        await state.make(book)
    # Гипотеза без лидов стоит строкой с нулями.
    await hypotheses.add(session, "Пустая выдуманная гипотеза", None)
    return book


async def _builder(session: AsyncSession, hypothesis_id: int) -> Counter[str]:
    """Что сборка скажет о каждом лиде гипотезы без письма в очереди (`queue._leads`)."""
    said: Counter[str] = Counter()
    for row in await queue._leads(session, hypothesis_id, 0):
        if row.queued is not None and row.queued.status is MessageStatus.QUEUED:
            continue
        plan = await queue._plan(session, row, hypothesis_id)
        said[plan if isinstance(plan, str) else "пишет"] += 1
    return said


async def test_every_state_at_once_adds_up_and_agrees_with_the_other_screens(
    session: AsyncSession, world: w.World
) -> None:
    await _every_state(session, world)

    found = await mailing.board(session, now=NOW)

    _invariant(found, await _leads_of(session))
    expected = Counter(state.part for state in STATES)
    assert _counts(found.total) == dict(expected)
    assert _whys(found.total) == Counter(state.words for state in STATES if state.words)
    assert [row.parts for row in found.rows if row.name == "Пустая выдуманная гипотеза"] == [
        Parts()
    ]
    # «В очереди» — очередь пачки и прогноза (`queued_first`).
    queued = await LetterRepository(session).queued_count(stage=Stage.SALES)
    assert found.total.queued == queued == expected[Part.QUEUED]
    # «Сборка напишет» — её счёт по гипотезам и запас прогноза; «не напишет» — её слова.
    counted = await queue_plan.writable_counts(session, now=NOW)
    assert {
        row.hypothesis_id: row.parts.will_write for row in found.rows if row.parts.will_write
    } == counted
    assert found.total.will_write == await stage_forecast.sales_supply(session)
    for row in found.rows:
        said = await _builder(session, row.hypothesis_id)
        wont = Counter({why.words: why.count for why in row.parts.wont_write_why})
        assert said == wont + Counter({"пишет": row.parts.will_write})
    # «Ушло» — шаг «отправлено» воронки без периода и письма в полёте: без них — ровно воронка.
    assert found.total.in_flight == sum(state.flying for state in STATES) == 2
    assert _sent_without_flying(found) == await _funnel_sent(session)


async def test_reasons_are_ranked_by_count_then_by_words(
    session: AsyncSession, world: w.World
) -> None:
    book = Book(session, world.hypothesis_id)
    for make in (_no_name, _held, _held, _stoplist, _rejected("unusable"), _rejected("duplicate")):
        await make(book)
    await _rejected("duplicate")(book)

    found = await mailing.board(session, now=NOW)

    parts = _row(found, world.hypothesis_id)
    assert [(why.words, why.count) for why in parts.wont_write_why] == [
        (FIRST_LETTER, 2),
        (queue_plan.STOPLIST, 1),
        ("у лида нет значения для {{name}}", 1),
    ]
    assert [(why.words, why.count) for why in parts.removed_why] == [
        (REASON_LABELS[RejectionReason.DUPLICATE], 2),
        (REASON_LABELS[RejectionReason.UNUSABLE], 1),
    ]


def test_rejection_words_are_the_words_of_the_leads_screen() -> None:
    """Слова отказа — те же, что у экрана лидов и консоли очистки (`REASON_LABELS`, сверку с
    фронтом держит `test_api_sales_screen.py`); незнакомый код — общими словами с кодом."""
    assert {mailing.rejected(reason.value) for reason in RejectionReason} == set(
        REASON_LABELS.values()
    )
    assert mailing.rejected("made-up-reason") == "другая причина (made-up-reason)"
    assert mailing.rejected(None) == mailing.UNSAID


# --- случайный набор ------------------------------------------------------------------------------

#: Письмо случайного лида: первое письмо в состоянии, снятое «по правилу again», снятое платформой,
#: диалог без письма — или ничего.
LETTERS: list[tuple[str, MessageStatus | None, str | None]] = [
    ("нет", None, None),
    ("в очереди", MessageStatus.QUEUED, None),
    ("в полёте", MessageStatus.SENDING, None),
    ("ушло", MessageStatus.SENT, None),
    ("дошло", MessageStatus.DELIVERED, None),
    ("вернулось", MessageStatus.BOUNCED, None),
    ("снято, пишется заново", MessageStatus.STOPPED, ELSEWHERE),
    ("снято платформой", MessageStatus.STOPPED, REFUSED),
    ("диалог без письма", None, "диалог"),
]


async def _random_lead(book: Book, chance: random.Random, owners: list[int]) -> None:
    status = chance.choice(list(LeadStatus))
    fields: dict[str, object] = {"status": status, "hypothesis_id": chance.choice(owners)}
    if status is LeadStatus.REJECTED:
        fields["rejection_reason"] = chance.choice(list(RejectionReason)).value
    if chance.random() < 0.2:
        fields["verification_status"] = "made-up:unknown"
    if chance.random() < 0.1:
        fields["language"] = chance.choice(["de", None, "ru"])
    lead = await book.lead(**fields)
    _, letter, reason = chance.choice(LETTERS)
    if letter is not None:
        await book.first(lead, letter, reason=reason)
    elif reason is not None:
        await book.thread(lead)
    if chance.random() < 0.1:
        book.session.add(SalesStoplistModel(email=lead.email))
        await book.session.flush()


@pytest.mark.parametrize("seed", [7, 2026])
async def test_a_random_set_adds_up_by_hypothesis_and_in_total(
    session: AsyncSession, world: w.World, seed: int
) -> None:
    chance = random.Random(seed)
    owners = [world.hypothesis_id]
    for number in range(2):
        owners.append((await hypotheses.add(session, f"Случайная гипотеза {number}", None)).id)
    book = Book(session, world.hypothesis_id)
    for _ in range(60):
        await _random_lead(book, chance, owners)

    found = await mailing.board(session, now=NOW)

    _invariant(found, await _leads_of(session))
    assert _sent_without_flying(found) == await _funnel_sent(session)
    assert found.total.queued == await LetterRepository(session).queued_count(stage=Stage.SALES)
    counted = await queue_plan.writable_counts(session, now=NOW)
    assert {
        row.hypothesis_id: row.parts.will_write for row in found.rows if row.parts.will_write
    } == counted


# --- счёт партией ---------------------------------------------------------------------------------


async def _mixed(book: Book, times: int) -> None:
    """Лиды разных частей — `times` раз каждого вида."""
    for _ in range(times):
        for make in (_ready, _held, _with_first(MessageStatus.QUEUED), _rejected("duplicate")):
            await make(book)
        await _with_first(MessageStatus.DELIVERED)(book)


async def test_parts_are_counted_by_aggregates_not_per_lead(
    session: AsyncSession, world: w.World
) -> None:
    """Вкладка спрашивает раз в минуту: втрое больше лидов — столько же запросов."""
    book = Book(session, world.hypothesis_id)
    await _mixed(book, 1)
    with _statements(session) as few:
        small = await mailing.board(session, now=NOW)

    await _mixed(book, 2)
    with _statements(session) as many:
        large = await mailing.board(session, now=NOW)

    assert (small.total.total, large.total.total) == (5, 15)
    assert len(many) == len(few)
