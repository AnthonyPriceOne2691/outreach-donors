"""Ответ с файлами: файлы приложены к переписке заранее и уходят с письмом ответа.

Что по зелёному прогону не видно и проверяется здесь:

- файлы ответа сверяются до заведения письма: чужой переписки, ушедший с другим письмом,
  сверх предела — отказ словами, и письма ответа в базе нет;
- письмо ответа получает файлы, транспорт — их тела под нашим типом, журнал — их имена;
- повтор ответа после отказа почты берёт то же письмо вместе с файлами: файлы не теряются,
  даже если повтор их не назвал, и не удваиваются;
- привязка — захватом: файл, который секундой раньше взял другой ответ, не уводится;
- ответ лиду продаж несёт файлы так же: мост продаж правит только текст.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.letters import answers
from backend.features.letters.outgoing_files import PENDING_DAYS, OutgoingFileError
from backend.features.letters.outgoing_store import (
    OutgoingFiles,
    OutgoingFileTakenError,
    UnknownOutgoingFileError,
)
from backend.features.letters.sending import SendError, Sending, SendOutcome
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import make_donor
from tests.test_outgoing_files import DOCX, PDF, XLSX
from tests.test_replies_inbox import sent
from tests.test_sales_send import SIGNED, _seen
from tests.test_sales_thread_answer import TEXT, Answered, answered
from tests.test_thread_answer import Recording, conversation
from tests.thread_letters import Refusing

__all__ = ["answered", "conversation", "sent"]  # фикстуры — отсюда их видит pytest

BODY = "Thanks! Our rates and terms are attached."


async def _uploaded(
    session: AsyncSession, thread_id: int, *files: tuple[str, bytes]
) -> list[OutgoingAttachmentModel]:
    """Файлы, приложенные к переписке, как их прикладывает экран: без письма."""
    store = OutgoingFiles(session)
    rows = [await store.keep(thread_id, name, data, by=None) for name, data in files]
    await session.commit()
    return rows


async def _answer(
    session: AsyncSession,
    conversation: tuple[MessageModel, ReplyModel],
    transport: object,
    file_ids: list[int],
) -> SendOutcome:
    first, reply = conversation
    return await answers.answer_reply(
        session,
        Sending(session, transport),  # type: ignore[arg-type]
        thread_id=first.thread_id or 0,
        reply_id=reply.id,
        body=BODY,
        author_id=None,
        file_ids=file_ids,
    )


async def _answers(session: AsyncSession, reply_id: int) -> int:
    """Сколько писем-ответов на этот ответ в базе — с любым состоянием."""
    found = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.answers_reply_id == reply_id)
    )
    return int(found or 0)


async def _bound(session: AsyncSession, thread_id: int) -> list[tuple[str, int | None]]:
    rows = await session.execute(
        select(OutgoingAttachmentModel.name, OutgoingAttachmentModel.message_id)
        .where(OutgoingAttachmentModel.thread_id == thread_id)
        .order_by(OutgoingAttachmentModel.id)
    )
    return [(name, message_id) for name, message_id in rows.all()]


class TestFilesGoWithTheAnswer:
    async def test_answer_carries_its_files_in_order_with_our_types(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, _ = conversation
        price, rates = await _uploaded(
            session, first.thread_id or 0, ("Прайс 2026.pdf", PDF), ("rates.xlsx", XLSX)
        )
        transport = Recording()

        outcome = await _answer(session, conversation, transport, [price.id, rates.id])

        [out] = transport.seen
        assert [(f.name, f.content_type, f.data) for f in out.attachments] == [
            ("Прайс 2026.pdf", "application/pdf", PDF),
            (
                "rates.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                XLSX,
            ),
        ]
        assert await _bound(session, first.thread_id or 0) == [
            ("Прайс 2026.pdf", outcome.message_id),
            ("rates.xlsx", outcome.message_id),
        ]

    async def test_journal_names_the_files_of_the_answer(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, _ = conversation
        files = await _uploaded(
            session, first.thread_id or 0, ("price.pdf", PDF), ("kit.docx", DOCX)
        )

        outcome = await _answer(session, conversation, Recording(), [row.id for row in files])

        record = await session.scalar(
            select(AuditLogModel).where(
                AuditLogModel.action == AuditAction.LETTER_SENT,
                AuditLogModel.target == f"message:{outcome.message_id}",
            )
        )
        assert record is not None
        assert record.details is not None
        assert record.details["вложения"] == ["price.pdf", "kit.docx"]

    async def test_answer_without_files_goes_as_before(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Файл, приложенный к переписке, но не названный, с ответом не уходит."""
        first, _ = conversation
        [loose] = await _uploaded(session, first.thread_id or 0, ("draft.pdf", PDF))
        transport = Recording()

        outcome = await _answer(session, conversation, transport, [])

        [out] = transport.seen
        assert out.attachments == ()
        record = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.target == f"message:{outcome.message_id}")
        )
        assert record is not None
        assert "вложения" not in (record.details or {})
        assert await _bound(session, first.thread_id or 0) == [(loose.name, None)]


class TestAbandonedFiles:
    async def test_file_without_a_letter_for_a_week_goes_and_the_rest_stay(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Брошенный — без письма дольше срока. Свежий ждёт дальше; ушедший с письмом не
        уходит, сколько бы ему ни было: он часть письма."""
        first, _ = conversation
        thread_id = first.thread_id or 0
        old, _fresh, sent = await _uploaded(
            session, thread_id, ("old.pdf", PDF), ("fresh.pdf", PDF), ("sent.pdf", PDF)
        )
        now = datetime.now(UTC)
        stale = now - timedelta(days=PENDING_DAYS, minutes=1)
        _File = OutgoingAttachmentModel
        await session.execute(
            update(_File).where(_File.id.in_([old.id, sent.id])).values(created_at=stale)
        )
        await session.execute(update(_File).where(_File.id == sent.id).values(message_id=first.id))
        await session.commit()

        gone = await OutgoingFiles(session).drop_abandoned(now=now)
        await session.commit()

        assert gone == [(old.id, thread_id)]
        assert await _bound(session, thread_id) == [("fresh.pdf", None), ("sent.pdf", first.id)]

    async def test_file_exactly_at_the_term_still_waits(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, _ = conversation
        thread_id = first.thread_id or 0
        (row,) = await _uploaded(session, thread_id, ("price.pdf", PDF))
        await session.refresh(row)

        gone = await OutgoingFiles(session).drop_abandoned(
            now=row.created_at + timedelta(days=PENDING_DAYS)
        )

        assert gone == []


class TestRepeatedAnswer:
    async def test_repeat_after_a_refusal_reuses_the_letter_and_its_files_once(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Почта не приняла ответ — он ждёт в очереди с файлами; повтор с теми же номерами
        не упирается в «файл уже приложен» и не удваивает ни строк, ни вложений."""
        first, reply = conversation
        files = await _uploaded(
            session, first.thread_id or 0, ("price.pdf", PDF), ("kit.docx", DOCX)
        )
        named = [row.id for row in files]

        with pytest.raises(SendError, match="Платформа отказала"):
            await _answer(session, conversation, Refusing(), named)
        waiting = await session.scalar(
            select(MessageModel).where(MessageModel.answers_reply_id == reply.id)
        )
        assert waiting is not None
        assert waiting.status is MessageStatus.QUEUED
        transport = Recording()

        outcome = await _answer(session, conversation, transport, named)

        assert outcome.message_id == waiting.id
        [out] = transport.seen
        assert [f.name for f in out.attachments] == ["price.pdf", "kit.docx"]
        assert await _bound(session, first.thread_id or 0) == [
            ("price.pdf", waiting.id),
            ("kit.docx", waiting.id),
        ]

    async def test_repeat_that_does_not_name_the_files_does_not_lose_them(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Повтор без номеров — отправка черновика агента или экран после перезагрузки:
        файлы, приложенные к письму прежде, уходят с ним, а новые добавляются."""
        first, _ = conversation
        price, kit = await _uploaded(
            session, first.thread_id or 0, ("price.pdf", PDF), ("kit.docx", DOCX)
        )
        with pytest.raises(SendError):
            await _answer(session, conversation, Refusing(), [price.id])
        transport = Recording()

        await _answer(session, conversation, transport, [kit.id])

        [out] = transport.seen
        assert [f.name for f in out.attachments] == ["price.pdf", "kit.docx"]


class TestRefusedBeforeTheLetter:
    async def test_file_of_another_thread_is_unknown_and_no_answer_is_stored(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        domain = await make_donor(session, "other.example.test")
        other = ThreadModel(domain_id=domain.id, campaign_id=first.campaign_id)
        session.add(other)
        await session.flush()
        [stranger] = await _uploaded(session, other.id, ("price.pdf", PDF))
        transport = Recording()

        with pytest.raises(UnknownOutgoingFileError) as refused:
            await _answer(session, conversation, transport, [stranger.id])

        assert str(refused.value) == (
            f"В переписке №{first.thread_id} нет файла №{stranger.id} — приложить к ответу нечего"
        )
        assert await _answers(session, reply.id) == 0
        assert transport.seen == []

    async def test_file_sent_with_another_letter_is_taken(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        first, reply = conversation
        [gone] = await _uploaded(session, first.thread_id or 0, ("price.pdf", PDF))
        gone.message_id = first.id
        await session.commit()

        with pytest.raises(OutgoingFileTakenError) as refused:
            await _answer(session, conversation, Recording(), [gone.id])

        assert str(refused.value) == (
            f"Файл «price.pdf» уже приложен к письму №{first.id} — "
            "к этому ответу приложите его заново"
        )
        assert await _answers(session, reply.id) == 0

    async def test_more_files_than_a_letter_takes_are_refused_before_the_query(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Номеров нет в базе вовсе — отказ по числу приходит раньше, чем «нет файла»."""
        _, reply = conversation

        with pytest.raises(OutgoingFileError, match="не больше 5 файлов, а приложено 6"):
            await _answer(session, conversation, Recording(), [1, 2, 3, 4, 5, 6])

        assert await _answers(session, reply.id) == 0

    async def test_files_over_the_letter_limit_together_are_refused(
        self, session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
    ) -> None:
        """Размер берётся из строки: тела в предел файла, вместе — сверх предела письма."""
        first, reply = conversation
        mb = 1_000_000
        rows = [
            OutgoingAttachmentModel(
                thread_id=first.thread_id or 0,
                name=name,
                content_type="application/pdf",
                size=size,
                data=PDF,
            )
            for name, size in (("a.pdf", 3 * mb), ("b.pdf", 3 * mb), ("c.pdf", mb + mb // 2))
        ]
        session.add_all(rows)
        await session.commit()

        with pytest.raises(OutgoingFileError) as refused:
            await _answer(session, conversation, Recording(), [row.id for row in rows])

        assert str(refused.value).startswith(
            "Файлы письма вместе — 7,5 МБ, больше предела 7 МБ на письмо: письмо вышло бы "
            "больше 10 МБ"
        )
        assert await _answers(session, reply.id) == 0


async def test_binding_is_a_claim_and_a_file_taken_meanwhile_is_not_moved(
    session: AsyncSession, conversation: tuple[MessageModel, ReplyModel]
) -> None:
    """Между проверкой и привязкой файл взял другой ответ: привязка — отказ словами,
    файл остаётся при том письме, к которому его приложили первым."""
    first, _ = conversation
    [row] = await _uploaded(session, first.thread_id or 0, ("price.pdf", PDF))
    row.message_id = first.id
    await session.commit()

    with pytest.raises(OutgoingFileTakenError, match="секундой раньше приложили к другому письму"):
        await OutgoingFiles(session).attach([row], 999_999)

    await session.refresh(row)
    assert row.message_id == first.id


async def test_sales_lead_answer_carries_the_file_and_the_bridge_signs_only_the_text(
    session: AsyncSession, answered: Answered
) -> None:
    """Мост продаж дописывает к тексту подпись и физический адрес — и только: файл
    уходит лиду тем же путём, что донору."""
    thread_id = answered.first.thread_id or 0
    [offer] = await _uploaded(session, thread_id, ("offer.pdf", PDF))

    await answers.answer_reply(
        session,
        Sending(session, answered.source, now=w.NOW),
        thread_id=thread_id,
        reply_id=answered.reply.id,
        body=TEXT,
        author_id=None,
        file_ids=[offer.id],
    )

    out = _seen(answered.source)[-1]
    assert out.body == f"{TEXT}{SIGNED}"
    assert [(f.name, f.content_type, f.data) for f in out.attachments] == [
        ("offer.pdf", "application/pdf", PDF)
    ]
