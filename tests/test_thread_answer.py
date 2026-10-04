"""Наш ответ в переписке: письмо собеседнику на его ответ.

До 04.10.2026 из переписки уходили только первое письмо и добивки. Проверяется
то, что отличает ответ от добивки и чего по зелёному прогону не видно: ветка
к письму собеседника, ящик переписки, адрес ответившего, отсутствие добивок
после ответа — и все проверки обычной отправки (стоп-лист).
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, ReplyKind, SuppressionReason, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters import answers
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.followups import Chain
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.sending import Sending, SuppressedError
from backend.features.letters.transport import NullTransport, Outgoing
from backend.features.replies.pipeline import Inbox
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.test_replies_inbox import NOW, SECRET, reply_from, sent

__all__ = ["sent"]  # фикстура приёма — отсюда её видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class Recording(NullTransport):
    def __init__(self) -> None:
        self.seen: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        return await super().send(outgoing)


@pytest.fixture
async def conversation(
    session: AsyncSession, sent: MessageModel, filled_legal: None, monkeypatch: pytest.MonkeyPatch
) -> tuple[MessageModel, ReplyModel]:
    """Первое письмо ушло с ящика переписки, донор ответил с другого адреса.

    Секрет приёма — после `filled_legal`: тот ставит свой, и метка в адресе
    ответа, подписанная секретом приёма, иначе не сошлась бы.
    """
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    sender = await make_sender(session, "anna@mail.test")
    sent.sender_id = sender.id
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == sent.domain_id))
    assert donor is not None
    donor.review = "accepted"
    await session.commit()
    got = await Inbox(session, now=NOW).accept(
        reply_from(sent, "Our price is $90. Which topic?", sender="boss@donor.example.test")
    )
    await session.commit()
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return sent, reply


class TestAnswer:
    async def test_answer_goes_in_the_thread_from_its_mailbox_to_who_answered(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        transport = Recording()

        outcome = await answers.answer_reply(
            session,
            Sending(session, transport),
            thread_id=first.thread_id or 0,
            reply_id=reply.id,
            body="Thanks! A guide on home repair, 1500 words.",
            author_id=None,
        )

        [out] = transport.seen
        assert out.in_reply_to == reply.inbound_message_id  # ветка к его письму
        assert out.to == "boss@donor.example.test"  # кто ответил, а не куда писали
        assert outcome.sender_email == "anna@mail.test"  # ящик переписки
        answer = await session.get(MessageModel, outcome.message_id)
        assert answer is not None
        assert answer.step == ANSWER_STEP
        assert answer.answers_reply_id == reply.id
        assert answer.subject == "Re: Advertising rates"
        assert answer.status is MessageStatus.SENT
        assert answer.next_action_at is None  # после ответа добивок нет

    async def test_answer_does_not_eat_the_followup_quota_of_its_mailbox(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Ответ — письмо с того же ящика, но не добивка: часовой запас добивок
        ящика он не трогает (ревью «Продаж» #162)."""
        first, reply = conversation
        outcome = await answers.answer_reply(
            session,
            Sending(session, Recording()),
            thread_id=first.thread_id or 0,
            reply_id=reply.id,
            body="Thanks! Which topics do you accept?",
            author_id=None,
        )
        answer = await session.get(MessageModel, outcome.message_id)
        assert answer is not None
        assert answer.sender_id is not None

        assert await Chain(session).sent_this_hour(answer.sender_id) == 0

    async def test_second_click_does_not_send_twice(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        transport = Recording()
        sending = Sending(session, transport)
        args = {"thread_id": first.thread_id or 0, "reply_id": reply.id, "author_id": None}

        await answers.answer_reply(session, sending, body="Thanks!", **args)  # type: ignore[arg-type]
        with pytest.raises(answers.AnswerRefusedError, match="уже ответили"):
            await answers.answer_reply(session, sending, body="Thanks!", **args)  # type: ignore[arg-type]
        assert len(transport.seen) == 1

    async def test_unsubscribed_address_is_not_answered(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        session.add(
            SuppressionModel(email="boss@donor.example.test", reason=SuppressionReason.UNSUBSCRIBED)
        )
        await session.commit()
        transport = Recording()

        with pytest.raises(SuppressedError, match=r"стоп-лист"):
            await answers.answer_reply(
                session,
                Sending(session, transport),
                thread_id=first.thread_id or 0,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )
        assert transport.seen == []

    async def test_ahrefs_metrics_are_not_sent_in_an_answer(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Рекламодатель спрашивает о площадке, человек отвечает «DR 45» — правила
        Ahrefs это запрещают в любом письме, и ответ не исключение."""
        first, reply = conversation
        transport = Recording()

        with pytest.raises(ForbiddenContentError, match="метрики Ahrefs"):
            await answers.answer_reply(
                session,
                Sending(session, transport),
                thread_id=first.thread_id or 0,
                reply_id=reply.id,
                body="Sure! Our site has DR 45 and steady traffic.",
                author_id=None,
            )
        assert transport.seen == []

    async def test_robot_reply_is_not_answered(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        reply.kind = ReplyKind.AUTO_REPLY
        await session.commit()

        with pytest.raises(answers.AnswerRefusedError, match="не письмо человека"):
            await answers.answer_reply(
                session,
                Sending(session, Recording()),
                thread_id=first.thread_id or 0,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )

    async def test_reply_from_another_thread_is_unknown(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        _, reply = conversation
        with pytest.raises(answers.UnknownAnswerTargetError):
            await answers.answer_reply(
                session,
                Sending(session, Recording()),
                thread_id=999_999,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )


class TestWords:
    @pytest.mark.parametrize(
        ("incoming", "subject"),
        [("Advertising rates", "Re: Advertising rates"), ("RE: Rates", "RE: Rates"), (None, "Re:")],
    )
    def test_subject(self, incoming: str | None, subject: str) -> None:
        assert answers.subject_for(incoming) == subject

    def test_empty_text_is_refused_before_anything(self) -> None:
        with pytest.raises(answers.AnswerRefusedError, match="пустой"):
            asyncio.run(
                answers.answer_reply(
                    None,  # type: ignore[arg-type]
                    None,  # type: ignore[arg-type]
                    thread_id=1,
                    reply_id=1,
                    body="   ",
                    author_id=None,
                )
            )


class TestScreen:
    async def test_answer_needs_the_send_right(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        first, reply = conversation
        await make_user("смотрит@site.com", role=UserRole.OPERATOR)  # без права send
        token = await sign_in("смотрит@site.com")

        refused = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": reply.id, "body": "Thanks!"},
            headers=bearer(token),
        )

        assert refused.status_code == 403

    async def test_answer_is_sent_and_shown_under_the_reply(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        first, reply = conversation
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")

        sent_now = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": reply.id, "body": "Thanks! Which topics do you accept?"},
            headers=bearer(token),
        )
        shown = await client.get(f"/api/threads/{first.thread_id}", headers=bearer(token))

        assert sent_now.status_code == 200, sent_now.text
        assert sent_now.json()["sender_email"] == "anna@mail.test"
        letters = shown.json()["letters"]
        assert [letter["answers_reply_id"] for letter in letters] == [None, reply.id]

    async def test_unknown_reply_is_404(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        first, _ = conversation
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")

        missing = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": 999_999, "body": "Thanks!"},
            headers=bearer(token),
        )

        assert missing.status_code == 404


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "backend/migrations/versions/8dbc46c01caf_message_answers_reply.py"
    )
    spec = importlib.util.spec_from_file_location("answers_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection: Connection) -> list[str]:
    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'messages' AND column_name = 'answers_reply_id'"
            )
        ).all()
        migration.upgrade()
    assert down == []
    return list(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'messages' AND column_name = 'answers_reply_id'"
            )
        ).scalars()
    )


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_columns) == ["answers_reply_id"]
