"""Решение человека о бизнесе ниши — и при отправке (28.10.2026).

«Не пишем» (`crawl/niche.decide`) снимает письма бизнеса из очереди и сроки его добивок, а
письмо в «отправляется» не трогает: его исход решает человек. Щель была здесь: письмо или
добивка висели в «отправляется» в минуту решения, человек не нашёл их в журнале платформы
и вернул в очередь (`letters/unknown_outcome.py`), и пачка или проход добивок отправили бы
их бизнесу, о котором сказали «не пишем», — отправка решение не перепроверяла.

Теперь перепроверяет (`letters/offer_standing.py`) условием «в работе» у списка и поиска
адреса (`crawl/advertiser_rules.in_work`): «не пишем» — письмо «остановлено» с причиной
и цепочка кончается; решения нет — письмо ждёт его; ответ в переписке уходит — бизнес
написал сам. Сценарии — целиком и тем кодом, что в бою: отправка, пачка, проход добивок,
решение исхода по журналу платформы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import ContactSource, MessageStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, ThreadModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.letters import answers, batch, unknown_outcome
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.followups import POSTPONE, send_due
from backend.features.letters.niche_recipients import run_hosts
from backend.features.letters.sending import (
    NotWritingError,
    RemovedAdvertiserError,
    Sending,
    UndecidedDonorError,
)
from backend.features.letters.transport import NullTransport
from backend.features.letters.unknown_outcome import Outcome
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import NOW, build_offers, offers, priced_donor, stage_two_sender
from tests.test_niche_business_not_writing import _measured
from tests.test_niche_letters import BOOKIE, DONOR, _business, _queued_niche_offer
from tests.thread_letters import (
    Conversation,
    Recording,
    Unanswered,
    age,
    hang,
    human_reply,
    stuck_followup,
)

#: Минута, когда человек решает исход письма по журналу платформы.
AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
#: Начало передачи почте: письмо висит в «отправляется» дольше, чем бывает в пути.
SINCE = AT - timedelta(minutes=12)
#: «Не пишем» — пока письмо висело.
DECIDED = AT - timedelta(minutes=6)
#: Второй бизнес того же прогона — со «пишем».
OTHER = "shop.example.test"
BY = "anthony@site.test"


async def _advertiser(session: AsyncSession, host: str = BOOKIE) -> AdvertiserModel:
    advertiser = await session.scalar(
        select(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == host)
    )
    assert advertiser is not None
    return advertiser


async def _decide(session: AsyncSession, *, write: bool) -> int:
    """Решение человека о бизнесе — тем путём, что экран. Число снятых им писем."""
    advertiser = await _advertiser(session)
    decided = await niche.decide(session, advertiser.id, write=write, by=BY, now=DECIDED)
    await session.commit()
    return decided.stopped


async def _back(session: AsyncSession, letter: MessageModel) -> None:
    """Человек не нашёл письмо в журнале платформы — «Вернуть в очередь»."""
    done = await unknown_outcome.resolve(session, letter.id, Outcome.QUEUED, author_id=None, now=AT)
    await session.commit()
    assert done.status is MessageStatus.QUEUED


async def _stuck_first(session: AsyncSession) -> MessageModel:
    """Оффер бизнесу ниши собран при «пишем»; на нём почта не ответила — «отправляется»."""
    letter = await _queued_niche_offer(session)
    await hang(Sending(session, Unanswered()).send(letter.id))
    await age(session, letter, since=SINCE)
    assert letter.status is MessageStatus.SENDING
    return letter


async def _talk(session: AsyncSession) -> Conversation:
    """Оффер бизнесу ниши ушёл при «пишем» — с ящика Этапа 2, со сроками добивок."""
    await priced_donor(session, DONOR)
    await _business(session)
    await build_offers(session, audience=niche.NICHE, campaign_name="Ниша", followup_days=(1, 2))
    sender = await stage_two_sender(session)
    [first] = await offers(session)
    await Sending(session, NullTransport(), now=NOW).send(first.id)
    await session.refresh(first)
    thread = await session.get(ThreadModel, first.thread_id)
    assert thread is not None
    return Conversation(first=first, thread=thread, sender=sender)


async def _recollected(session: AsyncSession) -> None:
    """Строку бизнеса после сборки сняли (так снимают стоп-листы перевода, `promote._drop`),
    а сбор прогона завёл её заново: решения о бизнесе нет."""
    advertiser = await _advertiser(session)
    run_id = advertiser.found_run_id
    assert run_id is not None
    await session.delete(advertiser)
    await session.flush()
    assert (await niche.collect(session, run_id)).added == 1
    assert (await _advertiser(session)).decided_at is None


def _declined(letter: MessageModel) -> str:
    return (
        f"Письмо №{letter.id} бизнесу ниши {BOOKIE} остановлено: человек сказал о нём "
        f"«не пишем» {DECIDED:%d.%m.%Y}. Добивки не уйдут"
    )


async def _steps(session: AsyncSession) -> list[int]:
    """Шаги писем бизнесу — заводилась ли добивка."""
    rows = await session.scalars(select(MessageModel.step).order_by(MessageModel.step))
    return list(rows.all())


class TestTheGap:
    """«Не пишем», пока письмо висело в «отправляется», и «Вернуть в очередь» после."""

    async def test_the_first_letter_is_stopped_with_its_reason(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await _stuck_first(session)
        assert await _decide(session, write=False) == 0  # «отправляется» решение не трогает
        await _back(session, letter)
        transport = Recording()

        with pytest.raises(RemovedAdvertiserError) as refused:
            await Sending(session, transport, now=AT).send(letter.id)

        assert str(refused.value) == _declined(letter)
        assert transport.seen == []
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, _declined(letter))
        assert letter.next_action_at is None
        later = await send_due(session, transport=transport, limit=5, now=AT + timedelta(days=30))
        assert (later.sent, later.stopped, transport.seen) == (0, 0, [])
        assert await _steps(session) == [0]  # добивки не завелось

    async def test_the_followup_ends_the_chain_through_the_pass(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивку «Вернуть в очередь» возвращает в цепочку сроком на первом письме — проход
        добивок забирает её в ближайшую минуту, и отправка её останавливает."""
        talk = await _talk(session)
        followup = await stuck_followup(session, talk, since=SINCE)
        assert await _decide(session, write=False) == 0
        await _back(session, followup)
        await session.refresh(talk.first)
        assert talk.first.next_action_at == AT
        transport = Recording()

        report = await send_due(
            session, transport=transport, limit=5, now=AT + timedelta(minutes=1)
        )

        assert (report.sent, report.stopped, transport.seen) == (0, 1, [])
        await session.refresh(followup)
        await session.refresh(talk.first)
        assert (followup.status, followup.failure_reason) == (
            MessageStatus.STOPPED,
            _declined(followup),
        )
        assert talk.first.next_action_at is None
        later = await send_due(session, transport=transport, limit=5, now=AT + timedelta(days=30))
        assert (later.sent, later.postponed, transport.seen) == (0, 0, [])

    async def test_the_batch_goes_on_to_the_next_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отказ одному письму пачку не останавливает: письмо бизнесу со «пишем» уходит."""
        await priced_donor(session, DONOR)
        run_id = await _business(session)
        await _also_writing(session, run_id)
        await build_offers(session, audience=niche.NICHE, campaign_name="Ниша")
        await stage_two_sender(session)
        to_bookie, to_other = await _letters_to(session, BOOKIE, OTHER)
        await hang(Sending(session, Unanswered()).send(to_bookie.id))
        await age(session, to_bookie, since=SINCE)
        await _decide(session, write=False)
        await _back(session, to_bookie)
        transport = Recording()

        report = await batch.send_queue(
            session, transport, stage=Stage.ADVERTISERS, audience=niche.NICHE
        )

        assert (report.sent, report.refused, report.left, report.stopped) == (
            1,
            {"рекламодатель снят после сборки": 1},
            0,
            None,
        )
        assert [outgoing.message_id for outgoing in transport.seen] == [to_other.id]
        await session.refresh(to_bookie)
        assert (to_bookie.status, to_bookie.failure_reason) == (
            MessageStatus.STOPPED,
            _declined(to_bookie),
        )


class TestWriting:
    """«Пишем» — письмо, вернувшееся в очередь, уходит, как уходило."""

    async def test_the_first_letter_goes(self, session: AsyncSession, filled_legal: None) -> None:
        letter = await _stuck_first(session)
        await _back(session, letter)
        transport = Recording()

        sent = await Sending(session, transport, now=AT).send(letter.id)

        assert [outgoing.message_id for outgoing in transport.seen] == [sent.message_id]
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT

    async def test_the_followup_goes(self, session: AsyncSession, filled_legal: None) -> None:
        talk = await _talk(session)
        followup = await stuck_followup(session, talk, since=SINCE)
        await _back(session, followup)
        transport = Recording()

        report = await send_due(
            session, transport=transport, limit=5, now=AT + timedelta(minutes=1)
        )

        assert (report.sent, report.stopped) == (1, 0)
        assert [outgoing.message_id for outgoing in transport.seen] == [followup.id]


class TestTheAnswer:
    async def test_an_answer_goes_after_not_writing(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Бизнес написал сам: ответ ему — не рассылка, и «не пишем» его не держит, как
        и «кому не пишем»."""
        talk = await _talk(session)
        reply = await human_reply(session, talk)
        await _decide(session, write=False)
        transport = Recording()

        sent = await answers.answer_reply(
            session,
            Sending(session, transport),
            thread_id=talk.thread.id,
            reply_id=reply.id,
            body="Thanks for writing! The example site is open for your topic.",
            author_id=None,
        )

        assert [outgoing.message_id for outgoing in transport.seen] == [sent.message_id]
        answer = await session.get(MessageModel, sent.message_id)
        assert answer is not None
        assert (answer.step, answer.status) == (ANSWER_STEP, MessageStatus.SENT)


class TestNoDecision:
    """Решения нет: письмо ждёт его — не уходит и не пропадает (`offer_standing.py`)."""

    async def test_the_letter_waits_and_goes_after_writing(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await _queued_niche_offer(session)
        await _recollected(session)
        transport = Recording()

        with pytest.raises(UndecidedDonorError) as refused:
            await Sending(session, transport, now=AT).send(letter.id)
        waited = await batch.send_queue(
            session, transport, stage=Stage.ADVERTISERS, audience=niche.NICHE
        )

        assert str(refused.value) == (
            f"Бизнес ниши {BOOKIE} снова ждёт решения человека: пока о нём не скажут «пишем», "
            f"письмо №{letter.id} ждёт. Решить — на экране «Рекламодатели», карточка «Бизнесы "
            "ниши из выдачи»"
        )
        assert (waited.sent, waited.refused, waited.left) == (0, {"ждёт решения человека": 1}, 1)
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.QUEUED, None)
        await _decide(session, write=True)
        sent = await Sending(session, transport, now=AT).send(letter.id)
        assert [outgoing.message_id for outgoing in transport.seen] == [sent.message_id]

    async def test_the_followup_waits_and_the_chain_lives(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        talk = await _talk(session)
        await _recollected(session)
        due = NOW + timedelta(days=2)
        transport = Recording()

        waited = await send_due(session, transport=transport, limit=5, now=due)

        assert (waited.sent, waited.postponed, waited.stopped) == (0, 1, 0)
        await session.refresh(talk.first)
        assert talk.first.next_action_at == due + POSTPONE
        await _decide(session, write=True)
        report = await send_due(session, transport=transport, limit=5, now=due + POSTPONE)
        assert report.sent == 1
        assert len(transport.seen) == 1

    async def test_not_writing_by_the_list_does_not_wait(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Бизнес к тому же из «кому не пишем»: ждать решения ему нечего — письмо
        останавливается с причиной списка, решение его не вернёт."""
        letter = await _queued_niche_offer(session)
        await _recollected(session)
        await _measured(session, BOOKIE, dr=88)

        with pytest.raises(NotWritingError):
            await Sending(session, Recording(), now=AT).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.STOPPED


async def _also_writing(session: AsyncSession, run_id: int) -> None:
    """Второй бизнес того же прогона — со «пишем» человека и адресом."""
    domain = DomainModel(host=OTHER, site_intent=niche.SELLS_OWN)
    session.add(domain)
    run = await session.get(RunModel, run_id)
    assert run is not None
    run.candidates = {**(run.candidates or {}), "hosts": [*run_hosts(run), OTHER]}
    await session.flush()
    assert (await niche.collect(session, run_id)).added == 1
    advertiser = await _advertiser(session, OTHER)
    await niche.decide(session, advertiser.id, write=True, by=BY)
    session.add(
        ContactModel(domain_id=domain.id, email=f"partners@{OTHER}", source=ContactSource.PAGE)
    )
    await session.flush()


async def _letters_to(session: AsyncSession, *hosts: str) -> list[MessageModel]:
    """Письма бизнесам `hosts` — в этом порядке."""
    by_host: dict[str, MessageModel] = {}
    for letter in await offers(session):
        domain = await session.get(DomainModel, letter.domain_id)
        assert domain is not None
        by_host[domain.host] = letter
    return [by_host[host] for host in hosts]
