"""Цепочка из трёх писем: сроки, захват, отправка и всё, что её обрывает.

Проверяется на настоящей базе то, чего заглушками не увидеть: добивка
не уходит дважды, не уходит ответившему, идёт с того же ящика и ложится
в тот же тред. Заодно правило, которое легко потерять при правке: срок
следующего письма назначает сама отправка, а не вызывающий.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import template
from backend.features.letters.chain import MAX_STEPS, cadence, due_after
from backend.features.letters.followups import Chain, send_due
from backend.features.letters.sending import Sending
from backend.features.letters.transport import NullTransport, Outgoing
from backend.features.outreach.repository import OutreachRepository
from backend.features.replies.repository import ReplyRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FILLED, make_donor, make_sender

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


async def _chain_start(
    session: AsyncSession,
    *,
    host: str = "donor.example.test",
    followup_days: list[int] | None = None,
) -> tuple[MessageModel, CampaignModel]:
    """Первое письмо, отправленное по-настоящему: с ящиком, тредом и сроком."""
    domain = await make_donor(session, host, email=f"editor@{host}")
    campaign = CampaignModel(
        name=f"Рассылка {host}", stage=Stage.DONORS, status="draft", followup_days=followup_days
    )
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    email_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=email_id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Advertising rates",
        body=f"Hi there,\n\n{FILLED['OUTREACH_POSTAL_ADDRESS']} {FILLED['OUTREACH_UNSUBSCRIBE_URL']}",
        idempotency_key=f"donors:{host}:0",
    )
    session.add(message)
    await make_sender(session, f"anna@mail-{host}")
    await session.flush()
    await Sending(session, NullTransport(), now=NOW).send(message.id)
    await session.refresh(message)
    return message, campaign


class TestCadence:
    def test_default_comes_from_settings(self) -> None:
        assert cadence(None) == outreach_cfg.FOLLOWUP_DAYS

    def test_campaign_overrides_it(self) -> None:
        """Сроки задаёт человек при создании рассылки: их подбирают
        по отклику, и настройка, общая на всё, для этого не годится."""
        assert cadence([1, 3]) == (1, 3)

    def test_first_followup_counts_from_the_previous_letter(self) -> None:
        when = due_after(NOW, step=0, days=[7, 14])

        assert when == NOW + timedelta(days=7)

    def test_chain_ends_after_the_last_step(self) -> None:
        """У последнего письма срока нет: пустой срок и означает конец."""
        assert due_after(NOW, step=MAX_STEPS - 1, days=[7, 14]) is None

    def test_short_cadence_ends_the_chain_early(self) -> None:
        """Одна добивка вместо двух — законная настройка, а не ошибка."""
        assert due_after(NOW, step=1, days=[7]) is None


class TestTemplates:
    @pytest.mark.parametrize("step", [1, 2])
    def test_followup_is_signed_and_names_the_site(self, step: int) -> None:
        """Добивка подписана и говорит, о каком сайте речь: без этого
        напоминание в треде читается как чужое письмо. Юридического блока
        нет — он снят 23.09.2026."""
        parsed = template.followup(step)

        assert "{{sender_name}}" in parsed.zone("signature").text
        assert "{{host}}" in parsed.zone("reminder").text
        assert "legal" not in {z.name for z in parsed.zones}

    def test_no_zone_goes_to_the_model(self) -> None:
        """Добивку модель не трогает — решение 21.09.2026."""
        assert template.followup(1).of_kind(template.ZoneKind.REWRITE) == ()

    def test_missing_step_is_loud(self) -> None:
        with pytest.raises(template.TemplateError, match="шага 9"):
            template.followup(9)


class TestSendingPlansTheChain:
    async def test_first_letter_gets_a_due_date(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Срок назначает сама отправка. Иначе третий вызывающий однажды
        забудет, и цепочка молча не начнётся."""
        message, _ = await _chain_start(session, followup_days=[7, 14])

        assert message.next_action_at == NOW + timedelta(days=7)

    async def test_campaign_cadence_wins(self, session: AsyncSession, filled_legal: None) -> None:
        message, _ = await _chain_start(session, host="fast.example.test", followup_days=[1, 2])

        assert message.next_action_at == NOW + timedelta(days=1)


class TestClaiming:
    async def test_due_letter_is_claimed_once(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Захват гасит срок тем же запросом, которым выбирает строку:
        второй проход не должен увидеть ту же добивку."""
        message, _ = await _chain_start(session, followup_days=[1, 2])
        later = NOW + timedelta(days=2)

        first = await Chain(session, now=later).claim()
        second = await Chain(session, now=later).claim()

        assert first is not None
        assert first.previous_id == message.id
        assert first.step == 1
        assert second is None
        await session.refresh(message)
        assert message.next_action_at is None

    async def test_not_due_yet_is_left_alone(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _chain_start(session, followup_days=[7, 14])

        assert await Chain(session, now=NOW + timedelta(days=1)).claim() is None

    async def test_anchor_is_the_first_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Якорь треда — идентификатор первого письма: у донора цепочка
        должна лежать одной веткой, а не лесенкой вложенных ответов."""
        message, _ = await _chain_start(session, followup_days=[1, 2])

        claimed = await Chain(session, now=NOW + timedelta(days=2)).claim()

        assert claimed is not None
        assert claimed.anchor == message.provider_message_id


class TestWhatStopsTheChain:
    async def test_reply_clears_the_pending_date(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивка не лежит в очереди заранее — она рождается по сроку.
        Пока срок цел, ответивший донор получит следующее письмо."""
        message, _ = await _chain_start(session, followup_days=[1, 2])

        stopped = await ReplyRepository(session).stop_chain(message.thread_id)
        await session.flush()
        await session.refresh(message)

        assert stopped == 1
        assert message.next_action_at is None
        assert await Chain(session, now=NOW + timedelta(days=2)).claim() is None

    async def test_suppressed_donor_gets_no_followup(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Донор отписался между первым письмом и сроком добивки."""
        message, _ = await _chain_start(session, followup_days=[1, 2])
        session.add(
            SuppressionModel(
                domain_id=message.domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                stage=Stage.DONORS,
            )
        )
        await session.flush()

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        assert report.stopped == 1
        assert report.sent == 0
        followup = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert followup is not None
        assert followup.status is MessageStatus.STOPPED


class TestThePass:
    async def test_followup_goes_out_in_the_same_thread(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        first, _ = await _chain_start(session, followup_days=[1, 2])
        later = NOW + timedelta(days=2)

        report = await send_due(session, transport=NullTransport(), limit=5, now=later)

        assert report.sent == 1
        followup = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert followup is not None
        assert followup.status is MessageStatus.SENT
        assert followup.thread_id == first.thread_id
        # Тот же ящик: менять его на середине переписки значит попасть
        # в спам и запутать собеседника.
        assert followup.sender_id == first.sender_id
        # И сразу назначен срок последнего письма цепочки.
        assert followup.next_action_at == later + timedelta(days=2)

    async def test_transport_gets_the_thread_anchor(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Якорь доезжает до самой почты, а не остаётся в наших полях.

        Проверяется на границе с транспортом: настоящего транспорта ещё
        нет, и без этой проверки заголовок цепочки появился бы только
        в замысле — у донора три письма легли бы отдельными.
        """

        class Recording(NullTransport):
            def __init__(self) -> None:
                self.seen: list[Outgoing] = []

            async def send(self, outgoing: Outgoing) -> str:
                self.seen.append(outgoing)
                return await super().send(outgoing)

        first, _ = await _chain_start(session, followup_days=[1, 2])
        transport = Recording()

        await send_due(session, transport=transport, limit=1, now=NOW + timedelta(days=2))

        assert [out.in_reply_to for out in transport.seen] == [first.provider_message_id]

    async def test_chain_ends_after_the_last_step(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _chain_start(session, followup_days=[1, 1])

        await send_due(session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2))
        await send_due(session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=4))
        # Третьего письма нет и быть не может: потолок цепочки жёсткий.
        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=9)
        )

        assert report.sent == 0
        steps = sorted(
            row.step for row in (await session.execute(select(MessageModel))).scalars().all()
        )
        assert steps == [0, 1, 2]

    async def test_hourly_lane_postpones_instead_of_bursting(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сотня подошедших добивок не уходит пачкой за минуту:
        почтовая платформа смотрит на скорость, а не на объём."""
        monkeypatch.setattr(outreach_cfg, "FOLLOWUP_PER_SENDER_PER_HOUR", 1)
        first, _ = await _chain_start(session, followup_days=[1, 1])
        later = NOW + timedelta(days=2)
        await send_due(session, transport=NullTransport(), limit=5, now=later)

        second, _ = await _chain_start(session, host="other.example.test", followup_days=[1, 1])
        second.sender_id = first.sender_id
        await session.flush()
        report = await send_due(session, transport=NullTransport(), limit=5, now=later)

        assert report.sent == 0
        assert report.postponed == 1
        await session.refresh(second)
        assert second.next_action_at == later + timedelta(hours=1)

    async def test_sent_followups_do_not_eat_the_daily_cap(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Дневной кап ящика считает первые письма: иначе каждая цепочка
        отнимает нового донора (решение 21.09.2026)."""
        first, _ = await _chain_start(session, followup_days=[1, 2])
        later = NOW + timedelta(days=2)
        await send_due(session, transport=NullTransport(), limit=5, now=later)

        for_cap = await OutreachRepository(session).sent_today(now=later, first_only=True)
        everything = await OutreachRepository(session).sent_today(now=later)

        assert for_cap == {}  # первое письмо ушло позавчера
        assert everything[first.sender_id] == 1  # а добивка сегодня
