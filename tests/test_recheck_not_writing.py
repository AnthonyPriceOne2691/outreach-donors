"""Перезапрос цены донору из «кому не пишем» не уходит (решение владельца 28.10.2026).

«Кому не пишем» ТЗ (`runs.exclusions`) держит перезапрос на обоих путях — новым первым
письмом и в переписку: условием перезапроса до письма (`recheck_rules`) и проверкой при
отправке (`donor_standing`). Принять такого донора нельзя, но принятый до правила 11.10.2026
или чей DR вырос после принятия остаётся принятым: кнопка перезапроса его разрешала, а
отправка пропускала письмо в переписку — шаг перезапроса вне цепочки, как у ответа, и под
исключение ответа он попадал тоже. Исключение осталось только у ответа в переписке: донор
написал сам, и ответ ему — не рассылка.

Домены списков берутся из самих списков (`crawl/denylist.py`), а не пишутся здесь.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.features.core.domain import DonorStatus, MessageStatus, Stage
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.denylist import MAJOR_MEDIA
from backend.features.donors.verdict import big_site_reason
from backend.features.letters import answers, price_recheck
from backend.features.letters.chain import ANSWER_STEP, FIRST_STEP, RECHECK_STEP
from backend.features.letters.donor_standing import Refusal, donor_refusal
from backend.features.letters.followups import send_due
from backend.features.letters.price_recheck import RecheckRefusedError, Way
from backend.features.letters.recheck_facts import Boxes, Facts, Waiting
from backend.features.letters.recheck_rules import refusal
from backend.features.letters.sending import NotWritingError, Sending
from backend.features.runs.exclusions import NOT_WRITING_WORDS, ExclusionReason
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.recheck_world import (
    ANSWERED_FROM,
    BOX,
    HOST,
    Recording,
    World,
    manual_stale,
    stale_conversation,
)

pytestmark = pytest.mark.usefixtures("filled_legal")

MEDIA = sorted(MAJOR_MEDIA)[0]
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

#: По какому слову узнаётся каждое условие перезапроса — в порядке шапки `recheck_rules`.
ORDER = (
    "из «кому не пишем»",
    "не донор",
    "не прошёл пороги",
    "цены нет",
    "донор-поставщик",
    "в стоп-листе",
    "ждёт отправки",
    "уже перезапросили",
)


def _not_writing_words(host: str, why: str) -> str:
    return (
        f"{host} — из «кому не пишем» ({why}): цену ему не перезапрашивают ни новым письмом, "
        "ни в переписке."
    )


def _closed_on_every_count() -> tuple[Facts, DonorModel]:
    """Донор, которого держит каждое условие перезапроса разом, — факты без базы."""
    donor = DonorModel(id=1, domain_id=1, review="rejected", status=DonorStatus.UNSUITABLE)
    facts = Facts(
        moment=NOW,
        donors={1: (donor, HOST)},
        not_writing={HOST: big_site_reason(80)},
        suppliers=frozenset({HOST}),
        stopped_domains=frozenset({1}),
        stopped_emails=frozenset(),
        waiting={1: [Waiting(7, FIRST_STEP, MessageStatus.QUEUED, Stage.DONORS)]},
        asked={1: NOW - timedelta(days=1)},
        talks={},
        contacts={},
        elsewhere={},
        firsts={},
        written=frozenset(),
        boxes=Boxes(senders={}, domains={}, sent_today={}, this_hour={}, cuts={}),
    )
    return facts, donor


@pytest.fixture
def moment() -> datetime:
    """Минута нажатия — настоящая: время ответов в базе ставит сама база."""
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def world(session: AsyncSession, moment: datetime) -> World:
    return await stale_conversation(session, moment)


async def _letters(session: AsyncSession, domain_id: int) -> int:
    total = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.domain_id == domain_id)
    )
    return int(total or 0)


class TestTheCondition:
    def test_it_comes_first_and_the_others_keep_their_order(self) -> None:
        """Донор, закрытый всеми условиями разом, слышит «кому не пишем»; снимая условия по
        одному, слышит остальные в прежнем порядке — и в конце перезапрос уходит."""
        facts, donor = _closed_on_every_count()

        def said() -> str | None:
            return refusal(facts, donor, HOST, sales_visible=False)

        heard = [said()]
        facts = replace(facts, not_writing={})
        heard.append(said())
        donor.review = "accepted"
        heard.append(said())
        donor.status = DonorStatus.SUITABLE
        heard.append(said())
        donor.last_price, donor.last_price_at = Decimal("90"), NOW - timedelta(days=200)
        heard.append(said())
        facts = replace(facts, suppliers=frozenset())
        heard.append(said())
        facts = replace(facts, stopped_domains=frozenset())
        heard.append(said())
        facts = replace(facts, waiting={})
        heard.append(said())
        facts = replace(facts, asked={})
        heard.append(said())

        *refused, gone = heard
        assert gone is None, "все условия сняты — перезапрос уходит"
        assert refused[0] == _not_writing_words(HOST, big_site_reason(80))
        assert len(refused) == len(ORDER)
        for marker, said in zip(ORDER, refused, strict=True):
            assert said is not None
            assert marker in said, (marker, said)

    async def test_by_dr_the_letter_to_the_thread_is_refused(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Донор ответил, принят до правила, DR вырос после — в переписку перезапрос не уходит."""
        found = await price_recheck.look(session, world.donor.id, now=moment)
        assert found.plan is not None
        assert found.plan.way is Way.THREAD
        world.donor.dr = 88
        await session.commit()
        before = await _letters(session, world.donor.domain_id)

        found = await price_recheck.look(session, world.donor.id, now=moment)

        assert found.plan is None
        assert found.refusal == _not_writing_words(HOST, big_site_reason(80))
        with pytest.raises(RecheckRefusedError) as refused:
            await price_recheck.ask(
                session, Sending(session, Recording(), now=moment), world.donor.id, author_id=None
            )
        assert str(refused.value) == found.refusal
        assert await _letters(session, world.donor.domain_id) == before, "до письма"

    async def test_by_name_a_new_first_letter_is_refused(
        self, session: AsyncSession, moment: datetime
    ) -> None:
        """Цену крупного СМИ вписали руками, писем ему не было, — нового письма тоже нет."""
        donor = await manual_stale(session, moment, host=MEDIA)
        await make_sender(session, BOX)
        transport = Recording()

        found = await price_recheck.look(session, donor.id, now=moment)

        words = NOT_WRITING_WORDS[ExclusionReason.MAJOR_MEDIA]
        assert found.refusal == _not_writing_words(MEDIA, words)
        with pytest.raises(RecheckRefusedError, match="из «кому не пишем»"):
            await price_recheck.ask(
                session, Sending(session, transport, now=moment), donor.id, author_id=None
            )
        assert transport.seen == []
        assert await _letters(session, donor.domain_id) == 0

    async def test_an_ordinary_donor_is_asked_as_before(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """DR на самом пороге — ещё не крупный: граница строго больше, перезапрос уходит."""
        world.donor.dr = 80
        await session.commit()
        transport = Recording()

        asked = await price_recheck.ask(
            session, Sending(session, transport, now=moment), world.donor.id, author_id=None
        )

        [out] = transport.seen
        assert (asked.way, out.to) == (Way.THREAD, ANSWERED_FROM)
        letter = await session.get(MessageModel, asked.message_id)
        assert letter is not None
        assert (letter.step, letter.status) == (RECHECK_STEP, MessageStatus.SENT)


class TestSending:
    @pytest.mark.parametrize(
        ("step", "held"),
        [(FIRST_STEP, True), (1, True), (RECHECK_STEP, True), (ANSWER_STEP, False)],
    )
    async def test_only_an_answer_is_not_held(
        self, session: AsyncSession, step: int, held: bool
    ) -> None:
        """Вне цепочки — и ответ, и перезапрос, но не держит «кому не пишем» только ответ."""
        domain = await make_donor(session, MEDIA)

        refused = await donor_refusal(
            session, MessageModel(domain_id=domain.id, step=step), host=MEDIA
        )

        assert (refused is not None and refused.reason is Refusal.NOT_WRITING) is held

    async def test_a_recheck_in_the_thread_is_stopped_with_its_reason(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Перезапрос собран, пока донор был годен, а DR вырос до отправки: письмо в переписку
        почте не отдано, «остановлено» с причиной, добивок нет."""
        found = await price_recheck.look(session, world.donor.id, now=moment)
        assert found.plan is not None
        letter = await price_recheck._materialize(session, found, found.plan)
        await session.commit()
        world.donor.dr = 88
        await session.commit()
        transport = Recording()

        with pytest.raises(NotWritingError) as refused:
            await Sending(session, transport, now=moment).send(
                letter.id, from_sender_id=found.plan.sender_id, in_reply_to=found.plan.in_reply_to
            )

        await session.refresh(letter)
        assert transport.seen == []
        assert (letter.step, letter.status) == (RECHECK_STEP, MessageStatus.STOPPED)
        assert letter.failure_reason == str(refused.value)
        assert str(refused.value) == (
            f"Письмо №{letter.id} остановлено: {big_site_reason(80)} ({HOST}). Добивки не уйдут"
        )
        assert letter.next_action_at is None
        later = await send_due(
            session, transport=transport, limit=10, now=moment + timedelta(days=30)
        )
        assert (later.sent, transport.seen) == (0, [])

    async def test_an_answer_in_the_thread_still_goes(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """Донор из «кому не пишем» написал сам — ответ ему не рассылка и уходит, как раньше."""
        world.donor.dr = 88
        await session.commit()
        transport = Recording()

        outcome = await answers.answer_reply(
            session,
            Sending(session, transport, now=moment),
            thread_id=world.thread.id,
            reply_id=world.reply.id,
            body="Thanks! We will come back with a topic.",
            author_id=None,
        )

        [out] = transport.seen
        assert (out.to, out.in_reply_to) == (ANSWERED_FROM, world.reply.inbound_message_id)
        letter = await session.get(MessageModel, outcome.message_id)
        assert letter is not None
        assert (letter.step, letter.status) == (ANSWER_STEP, MessageStatus.SENT)
