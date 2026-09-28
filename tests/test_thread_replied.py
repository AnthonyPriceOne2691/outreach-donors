"""Ответивший донор не «молчит»: приём ставит диалогу «отвечен».

До 28.09.2026 статус «отвечен» не ставил никто, а гейт молчания отбора
(`runs/exclusions._silent`) опирался именно на него: донор, ответивший
ценой, выпадал бы из новых прогонов на год как «писали, не ответил».
Прежний тест гейта ставил статус руками в фикстуре и этого не видел —
здесь ответ идёт настоящим путём, через приём.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.features.core.domain import ReplyKind, ThreadStatus
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.replies.pipeline import Inbox
from backend.features.runs.exclusions import Exclusions
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_replies_inbox import HOST, NOW, inbound_secret, reply_from, sent
from tests.test_selection_gates import _wrote_to

__all__ = ["inbound_secret", "sent"]  # фикстуры приёма — отсюда их видит pytest


async def _thread(session: AsyncSession, message: MessageModel) -> ThreadModel:
    assert message.thread_id is not None
    thread = await session.get(ThreadModel, message.thread_id)
    assert thread is not None
    await session.refresh(thread)
    return thread


class TestReceptionMarksTheThread:
    async def test_human_reply_marks_the_thread_replied(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        accepted = await Inbox(session, now=NOW).accept(
            reply_from(sent, "Hi Anna, a guest post is $120, dofollow.")
        )
        await session.commit()

        assert accepted.kind is ReplyKind.HUMAN
        assert (await _thread(session, sent)).status is ThreadStatus.REPLIED

    async def test_auto_reply_leaves_the_thread_open(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """«Я в отпуске» — не ответ: диалог остаётся открытым, как и цепочка."""
        accepted = await Inbox(session, now=NOW).accept(
            reply_from(
                sent,
                "I am out of the office until Monday.",
                headers={"auto-submitted": "auto-replied"},
            )
        )
        await session.commit()

        assert accepted.kind is ReplyKind.AUTO_REPLY
        assert (await _thread(session, sent)).status is ThreadStatus.OPEN

    async def test_unsubscribed_thread_stays_unsubscribed(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Отписка кнопкой сильнее ответа: закрытый ею диалог не открывается."""
        thread = await _thread(session, sent)
        thread.status = ThreadStatus.UNSUBSCRIBED
        await session.commit()

        await Inbox(session, now=NOW).accept(reply_from(sent, "Our price is $90."))
        await session.commit()

        assert (await _thread(session, sent)).status is ThreadStatus.UNSUBSCRIBED


class TestSilenceGate:
    async def test_donor_who_answered_through_reception_is_not_silent(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Сквозной путь: письмо ушло, донор ответил, приём записал — отбор
        его не вычёркивает как молчащего."""
        await Inbox(session, now=NOW).accept(reply_from(sent, "Price: $150 per article."))
        await session.commit()

        found = await Exclusions(session).excluded_hosts([HOST], now=NOW + timedelta(days=5))

        assert found == {}

    async def test_human_reply_counts_even_without_the_status(self, session: AsyncSession) -> None:
        """Диалог, отвеченный до того, как приём начал ставить статус: сам
        ответ человека на нём — тоже «ответил»."""
        moment = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        domain = await _wrote_to(
            session, "legacy.example.test", sent_at=moment - timedelta(days=10)
        )
        message = (
            await session.execute(
                MessageModel.__table__.select().where(MessageModel.domain_id == domain.id)
            )
        ).one()
        session.add(
            ReplyModel(
                thread_id=message.thread_id,
                message_id=message.id,
                from_email="editor@legacy.example.test",
                subject="Re: Hi",
                raw_body="Price is $80.",
                kind=ReplyKind.HUMAN,
                inbound_message_id="<legacy-1@site.test>",
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["legacy.example.test"], now=moment)

        assert found == {}

    async def test_auto_reply_alone_is_still_silence(self, session: AsyncSession) -> None:
        """Автоответ — не ответ: гейт молчания его не засчитывает."""
        moment = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        domain = await _wrote_to(session, "away.example.test", sent_at=moment - timedelta(days=10))
        message = (
            await session.execute(
                MessageModel.__table__.select().where(MessageModel.domain_id == domain.id)
            )
        ).one()
        session.add(
            ReplyModel(
                thread_id=message.thread_id,
                message_id=message.id,
                from_email="editor@away.example.test",
                subject="Automatic reply",
                raw_body="I am out of the office.",
                kind=ReplyKind.AUTO_REPLY,
                inbound_message_id="<away-1@site.test>",
            )
        )
        await session.flush()

        found = await Exclusions(session).excluded_hosts(["away.example.test"], now=moment)

        assert "away.example.test" in found
