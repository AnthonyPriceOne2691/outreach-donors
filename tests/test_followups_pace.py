"""Пауза ящика между письмами у прохода добивок (`letters/followup_pace.py`, 16.11.2026).

Ящик доноров и рекламодателей отдаёт добивки по одной: вторая с того же ящика в ту же минуту не
уходит, с другого — уходит. Пауза меряется от последнего письма ящика любого вида — первого тоже,
письма «в полёте» тоже, ответа в переписке — нет. Разброс даёт подставленный генератор. Ждущая
паузы добивка не теряется: её срок — на конце паузы, а не в прошлом. Ящики продаж паузы не ждут;
ноль в настройке паузу снимает. Ящики и адреса — выдуманные, почта — нулевая.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.core.window import Pace
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.followup_pace import Gaps, Rest, seeded
from backend.features.letters.followups import send_due
from backend.features.letters.transport import NullTransport
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_sender
from tests.test_followups import NOW, _chain_start

GAP = timedelta(minutes=10)
#: Срок добивок: первые письма ушли в `NOW`, добивка — через сутки; проход — на вторые.
DUE = NOW + timedelta(days=2)


def _gaps(draw: float = 0.0) -> Gaps:
    """Пауза 10 минут ±50%, разброс — подставленный: `draw` — доля паузы в [−1, 1]."""
    return Gaps(Pace(GAP, 0.5), draw=lambda _box, _letter: draw)


async def _on_one_box(session: AsyncSession, *hosts: str) -> list[MessageModel]:
    """Первые письма цепочек `hosts` — все с ящика первой: добивки подходят в один срок."""
    letters = [(await _chain_start(session, host=host, followup_days=[1, 1]))[0] for host in hosts]
    for letter in letters[1:]:
        letter.sender_id = letters[0].sender_id
    await session.flush()
    return letters


async def _letter_of(
    session: AsyncSession, box: int | None, host: str = "fresh.example.test", **values: object
) -> None:
    """Ещё одно письмо ящика `box` — первое письмо цепочки `host` со своими полями `values`."""
    fresh, _ = await _chain_start(session, host=host, followup_days=[5, 5])
    await session.execute(
        update(MessageModel).where(MessageModel.id == fresh.id).values(sender_id=box, **values)
    )


class TestOneLetterAtATime:
    async def test_the_second_followup_of_a_box_waits_and_another_box_goes(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Три добивки в одну минуту: две с ящика A, одна с ящика B. Уходят по одной с ящика,
        вторая с A ждёт паузы — срок на её конце, проход называет её и минуту, когда A освободится.
        В ту же минуту она не уходит, по концу паузы — уходит."""
        one, two = await _on_one_box(session, "one.example.test", "two.example.test")
        other, _ = await _chain_start(session, host="other.example.test", followup_days=[1, 1])

        report = await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps())

        assert (report.sent, report.resting, report.postponed) == (2, 1, 0)
        assert report.free_at == DUE + GAP
        assert "ждут паузы ящика между письмами 1, первый освободится в 12:10 UTC" in (
            report.as_report
        )
        senders = await session.scalars(
            select(MessageModel.sender_id).where(MessageModel.step == 1).order_by(MessageModel.id)
        )
        assert list(senders) == [one.sender_id, other.sender_id]
        await session.refresh(two)
        assert two.next_action_at == DUE + GAP  # не потеряна и не в прошлом — на конце паузы

        again = await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps())
        rested = await send_due(
            session, transport=NullTransport(), limit=5, now=DUE + GAP, gaps=_gaps()
        )

        assert (again.sent, rested.sent) == (0, 1)

    async def test_the_pause_counts_from_the_last_first_letter_of_the_box(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Первое письмо ушло с ящика пять минут назад — добивка ждёт ещё пять: два письма с ящика
        подряд не уходят, какого бы вида ни было первое."""
        (one,) = await _on_one_box(session, "one.example.test")
        await _letter_of(session, one.sender_id, sent_at=DUE - timedelta(minutes=5))

        report = await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps())

        assert (report.sent, report.resting) == (0, 1)
        await session.refresh(one)
        assert one.next_action_at == DUE + timedelta(minutes=5)

    async def test_a_letter_in_flight_holds_the_box_too(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо «отправляется» (почта не ответила) отдано с минуты захвата: ящик отдыхает и после
        него — как у автоотправки первых писем."""
        (one,) = await _on_one_box(session, "one.example.test")
        await _letter_of(
            session,
            one.sender_id,
            status=MessageStatus.SENDING,
            sent_at=None,
            updated_at=DUE - timedelta(minutes=1),
        )

        report = await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps())

        assert (report.sent, report.resting) == (0, 1)

    async def test_an_answer_in_the_thread_sets_no_pause(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ответ человеку в переписке — не повод держать добивку ящика: собеседник написал сам."""
        (one,) = await _on_one_box(session, "one.example.test")
        await _letter_of(session, one.sender_id, step=ANSWER_STEP, sent_at=DUE)

        report = await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps())

        assert (report.sent, report.resting) == (1, 0)


class TestTheSpread:
    @pytest.mark.parametrize(("draw", "pause"), [(-1.0, 5), (0.0, 10), (1.0, 15)])
    async def test_the_pause_spreads_by_the_given_generator(
        self, session: AsyncSession, filled_legal: None, draw: float, pause: int
    ) -> None:
        _, two = await _on_one_box(session, "one.example.test", "two.example.test")

        await send_due(session, transport=NullTransport(), limit=5, now=DUE, gaps=_gaps(draw))

        await session.refresh(two)
        assert two.next_action_at == DUE + timedelta(minutes=pause)

    def test_the_default_spread_is_one_per_letter_and_within_half_the_pause(self) -> None:
        """Зерно — ящик и письмо: разброс у каждого письма свой, на всех проходах один, и пауза —
        в пределах ±50%. Новый разброс на каждом проходе раз в минуту свёл бы паузу к нижнему краю."""
        gaps = Gaps(Pace(GAP, 0.5))
        pauses = {gaps.after(1, letter) for letter in range(50)}

        assert len(pauses) > 1
        assert all(GAP * 0.5 <= pause <= GAP * 1.5 for pause in pauses)
        assert seeded(1, 7) == seeded(1, 7)
        assert gaps.longest == GAP * 1.5


class TestWhoWaits:
    async def test_sales_boxes_are_not_held(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ящику продаж паузы нет: его добивки уходят, как раньше, — сколько пускают потолки. Письмо
        минуту назад держит ящик доноров, а ящик продаж — нет."""
        (one,) = await _on_one_box(session, "one.example.test")
        sales = await make_sender(session, "mira@mail-sales.example.test")
        sales.stage = Stage.SALES
        await session.flush()
        just_now = DUE - timedelta(minutes=1)
        await _letter_of(session, one.sender_id, sent_at=just_now)
        await _letter_of(session, sales.id, host="sales.example.test", sent_at=just_now)

        rest = await Rest.load(session, DUE, _gaps())
        rest.took(sales.id, 1, DUE)

        assert one.sender_id is not None
        assert rest.until == {one.sender_id: just_now + GAP}
        assert sales.id not in rest.paced

    async def test_zero_in_the_setting_takes_the_pause_off(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`OUTREACH_FOLLOWUP_GAP_MIN=0` — как до 16.11.2026: только потолки, обе добивки ящика
        уходят одним проходом."""
        monkeypatch.setattr(outreach_cfg, "FOLLOWUP_GAP_MIN", 0)
        await _on_one_box(session, "one.example.test", "two.example.test")

        report = await send_due(session, transport=NullTransport(), limit=5, now=DUE)

        assert (report.sent, report.resting) == (2, 0)
