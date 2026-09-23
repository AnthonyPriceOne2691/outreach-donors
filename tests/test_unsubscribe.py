"""Метка отписки и её последствия.

Проверяется не только результат, но и ширина: отписка закрывает донора
целиком, а не адрес, и снимает уже назначенное — иначе добивка уйдёт
по сроку тому, кто попросил больше не писать.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ThreadModel,
)
from backend.features.letters import compose, unsubscribe
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.optout import unsubscribe_domain
from backend.features.letters.sending import Sending, SuppressedError
from backend.features.letters.transport import NullTransport, Outgoing
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender
from tests.test_letters_queue import FakeRewriter

SECRET = "u" * 32
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class TestLabel:
    def test_label_survives_the_round_trip(self) -> None:
        label = unsubscribe.label_for(417, secret=SECRET)

        assert unsubscribe.domain_id_from(label, secret=SECRET) == 417

    def test_forged_signature_is_not_accepted(self) -> None:
        """Иначе донора отписывает кто угодно, а выглядит это как его решение."""
        forged = "u417." + "0" * unsubscribe.SIGNATURE_LEN

        assert unsubscribe.domain_id_from(forged, secret=SECRET) is None

    def test_another_donor_label_does_not_fit(self) -> None:
        label = unsubscribe.label_for(417, secret=SECRET)
        moved = label.replace("u417.", "u418.")

        assert unsubscribe.domain_id_from(moved, secret=SECRET) is None

    def test_another_secret_does_not_fit(self) -> None:
        label = unsubscribe.label_for(417, secret=SECRET)

        assert unsubscribe.domain_id_from(label, secret="другой") is None

    @pytest.mark.parametrize("label", ["", "u417", "417.abcdef0123", "m417.abcdef0123", "u4a.zz"])
    def test_nonsense_is_not_a_label(self, label: str) -> None:
        assert unsubscribe.domain_id_from(label, secret=SECRET) is None

    def test_url_carries_the_label(self) -> None:
        url = unsubscribe.url_for(417, base="https://ours.test/stop", secret=SECRET)

        assert url == "https://ours.test/stop/" + unsubscribe.label_for(417, secret=SECRET)

    def test_trailing_slash_does_not_double(self) -> None:
        url = unsubscribe.url_for(417, base="https://ours.test/stop/", secret=SECRET)

        assert "//" not in url.removeprefix("https://")


class TestNoWorkingLink:
    """Ссылка, которая никого не отписывает, хуже отсутствующей: кнопка
    отписки в почтовом клиенте есть, а не работает ни для кого.

    Отправку отсутствие ссылки больше не держит: в тексте письма её нет
    (юридический блок снят 23.09.2026), а заголовок просто не ставится."""

    def test_no_secret_means_no_link(
        self, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", "")

        assert unsubscribe.url_for(417) == ""
        assert unsubscribe.missing_setting() == "OUTREACH_INBOUND_SECRET"
        assert compose.missing_settings() == []

    def test_no_page_means_no_link(
        self, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "UNSUBSCRIBE_URL", "")

        assert unsubscribe.url_for(417) == ""
        assert unsubscribe.missing_setting() == "OUTREACH_UNSUBSCRIBE_URL"
        assert compose.missing_settings() == []

    def test_letter_without_donor_has_no_link(self, filled_legal: None) -> None:
        """Предпросмотр шаблона — не письмо: ссылки у него нет."""
        values = compose.values_for(host="donor.example.test")

        assert values["unsubscribe_url"] == ""

    def test_letter_of_a_donor_has_one(self, filled_legal: None) -> None:
        values = compose.values_for(host="donor.example.test", domain_id=417)

        assert values["unsubscribe_url"].startswith("https://ours.test/stop/u417.")
        assert compose.missing(values) == []


@pytest.fixture
async def written(session: AsyncSession) -> MessageModel:
    """Донор, которому уже написали и назначили добивку."""
    domain = await make_donor(session, "donor.example.test", email="editor@donor.example.test")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add(campaign)
    await session.flush()

    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, status=ThreadStatus.OPEN)
    session.add(thread)
    await session.flush()

    sent = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Hi",
        body="Hi",
        sent_at=NOW,
        next_action_at=NOW + timedelta(days=7),
        idempotency_key="donors:donor.example.test:0",
    )
    session.add(sent)
    await session.flush()
    return sent


class TestConsequences:
    async def test_donor_goes_to_the_stop_list_whole(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        outcome = await unsubscribe_domain(session, written.domain_id, source="проверка")

        assert outcome is not None
        assert outcome.host == "donor.example.test"
        assert outcome.first_time
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert [(row.domain_id, row.email, row.reason, row.stage) for row in rows] == [
            (written.domain_id, None, SuppressionReason.UNSUBSCRIBED, None)
        ]

    async def test_the_due_followup_is_cancelled(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """Добивка живёт сроком у отправленного письма, а не строкой
        в очереди: не погаси срок — и она уйдёт отписавшемуся."""
        await unsubscribe_domain(session, written.domain_id, source="проверка")

        await session.refresh(written)
        assert written.next_action_at is None

    async def test_queued_letter_is_taken_off(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        queued = MessageModel(
            campaign_id=written.campaign_id,
            thread_id=written.thread_id,
            domain_id=written.domain_id,
            step=0,
            status=MessageStatus.QUEUED,
            subject="Hi again",
            body="Hi again",
            idempotency_key="donors:donor.example.test:другое",
        )
        session.add(queued)
        await session.flush()

        outcome = await unsubscribe_domain(session, written.domain_id, source="проверка")

        assert outcome is not None
        assert outcome.stopped == 2
        await session.refresh(queued)
        assert queued.status is MessageStatus.STOPPED

    async def test_dialog_says_unsubscribed(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        await unsubscribe_domain(session, written.domain_id, source="проверка")

        thread = await session.get(ThreadModel, written.thread_id)
        assert thread is not None
        assert thread.status is ThreadStatus.UNSUBSCRIBED

    async def test_second_time_adds_nothing(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """По ссылкам в письмах ходят роботы, и один донор жмёт кнопку
        дважды. Вторая строка стоп-листа не нужна ни одному из них."""
        await unsubscribe_domain(session, written.domain_id, source="проверка")
        again = await unsubscribe_domain(session, written.domain_id, source="проверка")

        assert again is not None
        assert not again.first_time
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert len(rows) == 1

    async def test_unknown_donor_is_not_invented(self, session: AsyncSession) -> None:
        assert await unsubscribe_domain(session, 10_000, source="проверка") is None


class TestInTheLetter:
    """Ссылка в тексте, ссылка в заголовках и ссылка на странице — одна
    и та же. Разойдись они, кнопка отписки отписывала бы не того."""

    async def test_letter_text_has_no_link(self, session: AsyncSession, filled_legal: None) -> None:
        """Ссылка отписки живёт в заголовке, а не в тексте: юридический
        блок снят 23.09.2026."""
        domain = await make_donor(session, "one.example.test", email="info@one.example.test")

        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Проверка")
        )
        await session.flush()

        letter = (await session.execute(select(MessageModel))).scalars().one()
        assert letter.body is not None
        assert unsubscribe.url_for(domain.id) not in letter.body
        assert "unsubscribe" not in letter.body.lower()

    async def test_transport_gets_the_same_link(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Заголовок `List-Unsubscribe` — это кнопка отписки в интерфейсе
        почты, и нажимают её чаще, чем ссылку в тексте."""

        class Recording(NullTransport):
            def __init__(self) -> None:
                self.seen: list[Outgoing] = []

            async def send(self, outgoing: Outgoing) -> str:
                self.seen.append(outgoing)
                return await super().send(outgoing)

        domain = await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Проверка")
        )
        await session.flush()
        letter = (await session.execute(select(MessageModel))).scalars().one()
        transport = Recording()

        await Sending(session, transport, now=NOW).send(letter.id)

        assert [out.unsubscribe_url for out in transport.seen] == [unsubscribe.url_for(domain.id)]

    async def test_after_unsubscribe_the_letter_does_not_go(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        domain = await make_donor(session, "one.example.test", email="info@one.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await QueueBuilder(session, FakeRewriter()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Проверка")
        )
        await session.flush()
        letter = (await session.execute(select(MessageModel))).scalars().one()

        await unsubscribe_domain(session, domain.id, source="проверка")
        letter.status = MessageStatus.QUEUED  # снято отпиской, возвращаем в очередь руками

        with pytest.raises(SuppressedError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)
