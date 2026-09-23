"""Очередь писем на настоящей базе: сборка, отбор, отправка.

На заглушках эти правила проверить нельзя. «Одно письмо на донора»,
стоп-лист и дневной лимит ящика — все три про то, чего в базе быть
не должно, а не про то, что вернула функция.

Модель здесь заменена: её ответы проверяются отдельно и без базы.
Заменена нарочно так, чтобы было видно и переписанное письмо,
и письмо, оставшееся шаблонным, — второе на боевом прогоне будет
не реже первого.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    Stage,
    SuppressionReason,
    UsageProvider,
)
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.repository import LetterRepository
from backend.features.letters.rewrite import RewriteResult
from backend.features.letters.sending import (
    NoSenderError,
    NotReadyError,
    Sending,
    SuppressedError,
)
from backend.features.letters.transport import NullTransport, Outgoing, TransportError
from backend.features.outreach.repository import OutreachRepository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class FakeRewriter:
    """Модель, которая переписывает приветствие и больше ничего.

    Достаточно, чтобы отличие вышло ненулевым, и видно, что остальные
    зоны она не трогала.
    """

    def __init__(self, *, answer: bool = True) -> None:
        self.answer = answer
        self.seen: list[str] = []

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        self.seen.append(getattr(about, "host", ""))
        if not self.answer:
            return RewriteResult(notes=["модель недоступна или отказала — причина в логе"])
        return RewriteResult(
            zones={"greeting": "Good afternoon to you,"},
            tokens_spent=120,
        )


async def _build(
    session: AsyncSession, *, rewriter: FakeRewriter | None = None, limit: int = 50
) -> object:
    builder = QueueBuilder(session, rewriter or FakeRewriter())  # type: ignore[arg-type]
    report = await builder.build(
        BuildRequest(campaign_name="Проверка", limit=limit, niche=("home repair",))
    )
    await session.flush()
    return report


class TestBuildingTheQueue:
    async def test_prepares_a_letter_permake_donor(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_donor(session, "two.example.test", email="ads@two.example.test")

        report = await _build(session)

        assert report.prepared == 2  # type: ignore[attr-defined]
        letters = (
            (await session.execute(select(MessageModel).where(MessageModel.step == 0)))
            .scalars()
            .all()
        )
        assert {letter.status for letter in letters} == {MessageStatus.QUEUED}
        assert all(letter.body and letter.subject for letter in letters)

    async def test_donor_without_contact_is_not_written(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Донор без адреса — повод добрать контакты, а не письмо в никуда."""
        await make_donor(session, "mute.example.test")

        report = await _build(session)

        assert report.prepared == 0  # type: ignore[attr-defined]

    async def test_funnel_says_where_donors_ran_out(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Пустая очередь при «все написаны» и при «ни у кого нет адреса»
        выглядит одинаково — различает их только воронка."""
        await make_donor(session, "mute.example.test")
        await make_donor(session, "ok.example.test", email="info@ok.example.test")

        report = await _build(session)

        assert report.funnel["подходящих"] == 2  # type: ignore[attr-defined]
        assert report.funnel["с адресом"] == 1  # type: ignore[attr-defined]

    async def test_second_build_does_not_write_twice(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Повтор сборки — самый обычный случай: её запускают по расписанию.
        Второе письмо тому же донору для него рассылка по всем ящикам."""
        await make_donor(session, "one.example.test", email="info@one.example.test")

        await _build(session)
        second = await _build(session)

        assert second.prepared == 0  # type: ignore[attr-defined]
        assert await _count_messages(session) == 1

    async def test_suppressed_donor_is_skipped(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain = await make_donor(session, "stop.example.test", email="info@stop.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED))
        await session.flush()

        report = await _build(session)

        assert report.prepared == 0  # type: ignore[attr-defined]

    async def test_suppressed_address_is_skipped(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отписка — про адрес, стоп-лист донора — про все его адреса.
        Здесь проверяется первое."""
        await make_donor(session, "one.example.test", email="info@one.example.test")
        session.add(
            SuppressionModel(email="info@one.example.test", reason=SuppressionReason.COMPLAINED)
        )
        await session.flush()

        report = await _build(session)

        assert report.prepared == 0  # type: ignore[attr-defined]

    async def test_answering_address_is_preferred(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Дальше пишем тому, кто отвечает, а не в ящик, где письмо
        пролежало неделю."""
        domain = await make_donor(session, "one.example.test", email="info@one.example.test")
        session.add(
            ContactModel(
                domain_id=domain.id,
                email="elena@one.example.test",
                source=ContactSource.PAGE,
                last_replied_at=NOW,
            )
        )
        await session.flush()

        await _build(session)

        letter = (await session.execute(select(MessageModel))).scalars().one()
        contact = await session.get(ContactModel, letter.contact_id)
        assert contact is not None
        assert contact.email == "elena@one.example.test"

    async def test_one_letter_per_donor_even_with_many_addresses(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо на каждый найденный ящик выглядит как спам, даже если
        текст безупречный."""
        domain = await make_donor(session, "one.example.test", email="info@one.example.test")
        for local in ("editor", "ads"):
            session.add(
                ContactModel(
                    domain_id=domain.id,
                    email=f"{local}@one.example.test",
                    source=ContactSource.PAGE,
                )
            )
        await session.flush()

        await _build(session)

        assert await _count_messages(session) == 1

    async def test_model_refusal_leaves_the_template(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отказ модели — не отказ письма. Оно встаёт в очередь с нулевым
        отличием и честной пометкой: решает человек."""
        await make_donor(session, "one.example.test", email="info@one.example.test")

        report = await _build(session, rewriter=FakeRewriter(answer=False))

        letter = (await session.execute(select(MessageModel))).scalars().one()
        assert letter.uniqueness_pct == 0.0
        assert report.off_corridor == 1  # type: ignore[attr-defined]
        assert report.notes  # type: ignore[attr-defined]

    async def test_rewritten_zone_raises_the_difference(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")

        await _build(session)

        letter = (await session.execute(select(MessageModel))).scalars().one()
        assert letter.uniqueness_pct is not None
        assert letter.uniqueness_pct > 0
        assert "Good afternoon to you," in (letter.body or "")

    async def test_tokens_land_in_the_spending_table(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")

        await _build(session)

        rows = (
            (
                await session.execute(
                    select(UsageRecordModel).where(UsageRecordModel.provider == UsageProvider.LLM)
                )
            )
            .scalars()
            .all()
        )
        assert [row.units for row in rows] == [120]

    async def test_niche_reaches_the_model(self, session: AsyncSession, filled_legal: None) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        rewriter = FakeRewriter()

        await _build(session, rewriter=rewriter)

        assert rewriter.seen == ["one.example.test"]


class TestSending:
    async def test_sends_and_records(self, session: AsyncSession, filled_legal: None) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        sender = await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        outcome = await Sending(session, NullTransport(), now=NOW).send(letter.id)

        assert outcome.real is False
        assert outcome.sender_email == sender.email
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        assert letter.sent_at is not None
        assert letter.sender_id == sender.id

    async def test_sent_letter_counts_against_the_daily_cap(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Дневной расход считается по письмам: хранимого счётчика нет,
        и обнулять его некому — он упирался бы в кап навсегда."""
        await make_donor(session, "one.example.test", email="info@one.example.test")
        sender = await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        await Sending(session, NullTransport(), now=NOW).send(letter.id)

        today = await OutreachRepository(session).sent_today(now=NOW)
        assert today == {sender.id: 1}
        assert await OutreachRepository(session).sent_today(now=NOW + timedelta(days=1)) == {}

    async def test_second_send_is_refused(self, session: AsyncSession, filled_legal: None) -> None:
        """Повтор задачи не отправляет второе письмо."""
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()
        sending = Sending(session, NullTransport(), now=NOW)
        await sending.send(letter.id)

        with pytest.raises(Exception, match="не в очереди"):
            await sending.send(letter.id)

    async def test_stop_list_checked_at_send_not_only_at_build(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Между сборкой и нажатием кнопки проходят часы, и человек
        за это время мог отписаться."""
        domain = await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED))
        await session.flush()

        with pytest.raises(SuppressedError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED

    async def test_unset_sender_name_blocks_sending(self, session: AsyncSession) -> None:
        """Имя отправителя не задано — письмо не уходит: в подписи стоит
        громкая метка, а не имя."""
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        with pytest.raises(NotReadyError, match="ИМЯ ОТПРАВИТЕЛЯ"):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

    async def test_no_sender_leaves_the_letter_in_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        with pytest.raises(NoSenderError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED

    async def test_exhausted_box_does_not_send(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_donor(session, "two.example.test", email="info@two.example.test")
        await make_sender(session, "outreach1@mail.example.test", cap=1)
        await _build(session)
        letters = (
            (await session.execute(select(MessageModel).order_by(MessageModel.id))).scalars().all()
        )

        await Sending(session, NullTransport(), now=NOW).send(letters[0].id)

        with pytest.raises(NoSenderError):
            await Sending(session, NullTransport(), now=NOW).send(letters[1].id)

    async def test_refusal_returns_the_letter_to_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Почта сказала «нет» — письмо точно не ушло, и его можно вернуть
        в очередь без риска отправить второе."""

        class Refusing:
            name = "refusing"
            real = False

            async def send(self, outgoing: Outgoing) -> str:
                raise TransportError("платформа отказала")

        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        with pytest.raises(Exception, match="отказала"):
            await Sending(session, Refusing(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED
        assert letter.sender_id is None

    async def test_broken_connection_leaves_the_letter_sending(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ушло или нет — неизвестно. Возврат в очередь означал бы второе
        письмо тому же донору."""

        class Silent:
            name = "silent"
            real = False

            async def send(self, outgoing: Outgoing) -> str:
                raise TimeoutError("связь оборвалась")

        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()

        with pytest.raises(TimeoutError):
            await Sending(session, Silent(), now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.SENDING

    async def test_written_donor_is_not_offered_again(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await _build(session)
        letter = (await session.execute(select(MessageModel))).scalars().one()
        await Sending(session, NullTransport(), now=NOW).send(letter.id)

        funnel = await LetterRepository(session).funnel(Stage.DONORS)

        assert funnel.not_written == 0


async def _count_messages(session: AsyncSession) -> int:
    rows = await session.execute(select(func.count()).select_from(MessageModel))
    return int(rows.scalar_one())


class TestBlockedSending:
    async def test_build_names_what_blocks_sending(self, session: AsyncSession) -> None:
        """Нашлось живым прогоном: очередь собралась на 1361 токен, и ни
        одно письмо отправить было нельзя. Отчёт об этом молчал, и узнать
        это можно было только нажав «отправить». Держит теперь только имя
        отправителя: юридический блок снят 23.09.2026.
        """
        await make_donor(session, "one.example.test", email="info@one.example.test")

        report = await _build(session)

        assert report.prepared == 1  # type: ignore[attr-defined]
        assert report.blocked_by == ["OUTREACH_SENDER_NAME"]  # type: ignore[attr-defined]

    async def test_filled_settings_leave_nothing_blocking(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")

        report = await _build(session)

        assert report.blocked_by == []  # type: ignore[attr-defined]
