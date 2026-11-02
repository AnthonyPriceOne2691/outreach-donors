"""Возврат не ставит в очередь второе письмо получателю (`letters/dns_return._taken`).

Отказ «писали» не считается (`letters/attempts.py`): пока письмо лежит отказом, сборка пишет сайту
на следующий адрес. Здесь это настоящая сборка на настоящей базе: отказ событием, новое письмо
отбором очереди, потом проход возврата. Получатель у доноров и рекламодателей — сайт, через все
рассылки; у продаж — переписка: два лида одной компании делят домен и законны оба.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import dns_return
from backend.features.letters.attempts import LINK_WITHDRAWN
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.followups import send_due
from backend.features.letters.transport import NullTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_next_address import TemplateOnly, _donor
from tests.test_sales_soft_signals import NOW
from tests.thread_letters import conversation

OUTAGE = (
    "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: unrecognized address"
)
NO_USER = "550 5.1.1 The email account that you tried to reach does not exist"
HOST = "donor.example.test"
EDITOR = f"editor@{HOST}"
SENT = NOW - timedelta(hours=2)
#: Письма здесь не уходят: зазор возврата пройден, срок трёх суток — нет.
LATER = SENT + dns_return.SETTLE + timedelta(minutes=1)


async def _thread(
    session: AsyncSession,
    domain: DomainModel,
    contact: ContactModel | None,
    *,
    stage: Stage = Stage.DONORS,
    key: str,
) -> ThreadModel:
    """Своя рассылка и переписка — у каждого письма, если не сказано иное."""
    campaign = CampaignModel(name=f"Рассылка {key}", stage=stage, status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(
        domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id if contact else None
    )
    session.add(thread)
    await session.flush()
    return thread


async def _letter(
    session: AsyncSession,
    thread: ThreadModel,
    *,
    status: MessageStatus,
    reason: str | None = None,
    key: str,
) -> MessageModel:
    """Письмо переписки в нужном состоянии: первое, ушло два часа назад."""
    letter = MessageModel(
        campaign_id=thread.campaign_id,
        thread_id=thread.id,
        domain_id=thread.domain_id,
        contact_id=thread.contact_id,
        step=0,
        status=status,
        subject="Hi",
        body="Hi",
        sent_at=SENT,
        failure_reason=reason,
        idempotency_key=key,
    )
    session.add(letter)
    await session.flush()
    return letter


async def _refused(
    session: AsyncSession,
    domain: DomainModel,
    contact: ContactModel | None,
    *,
    stage: Stage = Stage.DONORS,
    key: str = "refused",
) -> MessageModel:
    """Письмо, отбитое молчанием DNS."""
    thread = await _thread(session, domain, contact, stage=stage, key=key)
    return await _letter(session, thread, status=MessageStatus.BOUNCED, reason=OUTAGE, key=key)


async def _other(
    session: AsyncSession,
    domain: DomainModel,
    contact: ContactModel | None,
    status: MessageStatus,
    *,
    reason: str | None = None,
    stage: Stage = Stage.DONORS,
) -> MessageModel:
    """Другое письмо того же сайта — в своей рассылке и своей переписке."""
    thread = await _thread(session, domain, contact, stage=stage, key="other")
    return await _letter(session, thread, status=status, reason=reason, key="other")


async def _build(session: AsyncSession, name: str) -> None:
    """Настоящая сборка: отбор очереди, новый диалог, ключ с номером попытки."""
    builder = QueueBuilder(session, TemplateOnly())  # type: ignore[arg-type]
    await builder.build(BuildRequest(campaign_name=name, limit=10, niche=("home repair",)))
    await session.flush()


async def _queued(session: AsyncSession, domain: DomainModel) -> list[MessageModel]:
    rows = await session.scalars(
        select(MessageModel).where(
            MessageModel.domain_id == domain.id, MessageModel.status == MessageStatus.QUEUED
        )
    )
    return list(rows.all())


class TestTheNextAddress:
    async def test_the_builder_took_over_and_the_letter_stays(
        self, session: AsyncSession, filled_legal: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Как это бывает: письмо лежит отказом, адрес жив, сборка пишет на следующий адрес в
        другой рассылке. Возврат поставил бы в очередь второе первое письмо тому же сайту."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first)
        await session.commit()

        await _build(session, "Вторая рассылка")

        queued = await _queued(session, domain)
        assert [found.contact_id for found in queued] == [second.id]

        with caplog.at_level("INFO", logger=dns_return.__name__):
            report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED
        assert len(await _queued(session, domain)) == 1
        assert "вернулось в работу 0, осталось отказом 1" in caplog.text

    @pytest.mark.parametrize(
        "status", [MessageStatus.SENDING, MessageStatus.SENT, MessageStatus.DELIVERED]
    )
    async def test_a_letter_that_went_holds_the_site_too(
        self, session: AsyncSession, status: MessageStatus
    ) -> None:
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first)
        await _other(session, domain, second, status)

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED

    @pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS])
    async def test_the_site_is_one_across_campaigns_and_stages(
        self, session: AsyncSession, stage: Stage
    ) -> None:
        """Отбор не смотрит на рассылку: сайту пишет одно письмо, чьё бы оно ни было."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first, stage=stage)
        await _other(session, domain, second, MessageStatus.QUEUED)

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED

    async def test_two_refused_letters_of_one_site_bring_back_one(
        self, session: AsyncSession
    ) -> None:
        """Обе попытки легли в один сбой: вернувшееся живо, и второе видит его сразу."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        one = await _refused(session, domain, first, key="one")
        two = await _refused(session, domain, second, key="two")

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (1, 1)
        assert (one.status, two.status) == (MessageStatus.QUEUED, MessageStatus.BOUNCED)

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(MessageStatus.BOUNCED, NO_USER), (MessageStatus.STOPPED, LINK_WITHDRAWN)],
        ids=["отказ", "снято ссылкой"],
    )
    async def test_a_dead_letter_of_the_site_holds_nothing(
        self, session: AsyncSession, status: MessageStatus, reason: str
    ) -> None:
        """Мёртвое письмо сайт не занимает — то же слово, что у отбора (`attempts.dead`)."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first)
        await _other(session, domain, second, status, reason=reason)

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (1, 0)
        assert letter.status is MessageStatus.QUEUED

    async def test_a_letter_stopped_by_a_human_holds_the_site(self, session: AsyncSession) -> None:
        """«Не писать» — решение по донору, а не по письму (`review.skip`): возврат его не обходит."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first)
        await _other(session, domain, second, MessageStatus.STOPPED)

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED

    async def test_an_answer_from_the_site_holds_it(self, session: AsyncSession) -> None:
        """Донор ответил с другого ящика: разговор идёт там, первое письмо сюда не уходит."""
        domain, (first, second) = await _donor(
            session, "editor@donor.example.test", "ads@donor.example.test"
        )
        letter = await _refused(session, domain, first)
        other = await _other(session, domain, second, MessageStatus.BOUNCED, reason=NO_USER)
        session.add(
            ReplyModel(
                thread_id=other.thread_id,
                message_id=other.id,
                kind=ReplyKind.HUMAN,
                raw_body="Our price is $90",
                from_email="ads@donor.example.test",
                subject="Re: Hi",
            )
        )
        await session.flush()

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED


class TestSales:
    async def _company(self, session: AsyncSession) -> DomainModel:
        domain = DomainModel(host="company.example.test")
        session.add(domain)
        await session.flush()
        return domain

    async def test_two_leads_of_one_company_are_both_lawful(self, session: AsyncSession) -> None:
        """Сайт один, лида два: письмо коллеге возврату не мешает."""
        domain = await self._company(session)
        letter = await _refused(session, domain, None, stage=Stage.SALES)
        await _other(session, domain, None, MessageStatus.QUEUED, stage=Stage.SALES)

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (1, 0)
        assert letter.status is MessageStatus.QUEUED

    async def test_the_same_thread_is_the_same_recipient(self, session: AsyncSession) -> None:
        """Получатель продаж — переписка: живое письмо этого шага в ней — возврата нет."""
        domain = await self._company(session)
        letter = await _refused(session, domain, None, stage=Stage.SALES)
        thread = await session.get(ThreadModel, letter.thread_id)
        assert thread is not None
        await _letter(session, thread, status=MessageStatus.QUEUED, key="same-step")

        report = await dns_return.return_letters(session, now=LATER)

        assert (report.back, report.taken) == (0, 1)
        assert letter.status is MessageStatus.BOUNCED


class TestFollowups:
    async def _chain(self, session: AsyncSession) -> tuple[MessageModel, MessageModel]:
        """Первое письмо ушло, добивка ушла и получила отказ молчанием DNS."""
        talk = await conversation(session, sent_at=SENT, host=HOST)
        assert talk.first.next_action_at is not None
        due = talk.first.next_action_at
        assert (await send_due(session, transport=NullTransport(), limit=5, now=due)).sent == 1
        followup = await session.scalar(
            select(MessageModel).where(
                MessageModel.thread_id == talk.thread.id, MessageModel.step == 1
            )
        )
        assert followup is not None
        assert followup.sent_at is not None
        at = followup.sent_at + timedelta(minutes=1)
        event = DeliveryEvent("bounce", followup.id, EDITOR, reason=OUTAGE, soft=True, at=at)
        await apply_events(session, [event], now=at)
        await session.flush()
        assert followup.status is MessageStatus.BOUNCED
        return talk.first, followup

    async def _moment(self, followup: MessageModel) -> datetime:
        assert followup.sent_at is not None
        return followup.sent_at + timedelta(hours=1)

    async def test_the_step_is_already_held_by_another_row(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Шаг заведён другой строкой: `materialize` взял бы её, а отказ остался бы в очереди."""
        first, followup = await self._chain(session)
        other = MessageModel(
            campaign_id=followup.campaign_id,
            thread_id=followup.thread_id,
            domain_id=followup.domain_id,
            contact_id=followup.contact_id,
            step=followup.step,
            status=MessageStatus.SENT,
            sent_at=followup.sent_at,
            idempotency_key="another-row-of-the-step",
        )
        session.add(other)
        await session.flush()

        report = await dns_return.return_letters(session, now=await self._moment(followup))

        assert (report.back, report.taken) == (0, 1)
        assert followup.status is MessageStatus.BOUNCED
        assert first.next_action_at is None

    @pytest.mark.parametrize("step", [2, ANSWER_STEP], ids=["следующая добивка", "ответ"])
    async def test_a_later_step_went_while_this_one_was_deferred(
        self, session: AsyncSession, filled_legal: None, step: int
    ) -> None:
        """Следующая добивка уже ушла: повтор пришёл бы после неё, не по порядку."""
        first, followup = await self._chain(session)
        later = MessageModel(
            campaign_id=followup.campaign_id,
            thread_id=followup.thread_id,
            domain_id=followup.domain_id,
            contact_id=followup.contact_id,
            step=step,
            status=MessageStatus.SENT,
            sent_at=followup.sent_at,
            idempotency_key="later-step",
        )
        session.add(later)
        await session.flush()

        report = await dns_return.return_letters(session, now=await self._moment(followup))

        assert (report.back, report.taken) == (0, 1)
        assert first.next_action_at is None

    async def test_the_step_before_does_not_hold_it(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Контроль: первое письмо этой же цепочки живо, и добивка возвращается: своя цепочка —
        не второй получатель. Правило сайта у добивки не действует."""
        first, followup = await self._chain(session)

        moment = await self._moment(followup)
        report = await dns_return.return_letters(session, now=moment)

        assert (report.back, report.taken) == (1, 0)
        assert first.next_action_at == moment


async def test_a_thread_that_ended_is_not_counted_as_taken(
    session: AsyncSession, filled_legal: None
) -> None:
    """Цепочка кончилась — письмо не вернулось, но и «занято» о нём не сказано: числа не врут."""
    talk = await conversation(session, sent_at=SENT, host=HOST)
    assert talk.first.next_action_at is not None
    due = talk.first.next_action_at
    await send_due(session, transport=NullTransport(), limit=5, now=due)
    followup = await session.scalar(
        select(MessageModel).where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
    )
    assert followup is not None
    assert followup.sent_at is not None
    at = followup.sent_at + timedelta(minutes=1)
    event = DeliveryEvent("bounce", followup.id, EDITOR, reason=OUTAGE, soft=True, at=at)
    await apply_events(session, [event], now=at)
    talk.thread.status = ThreadStatus.REPLIED
    await session.flush()
    assert followup.status is MessageStatus.BOUNCED

    report = await dns_return.return_letters(session, now=at + timedelta(hours=1))

    assert (report.back, report.taken) == (0, 0)
