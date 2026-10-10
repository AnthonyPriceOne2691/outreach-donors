"""Стоп-лист: как в него попадают руками и как выходят.

Главное здесь — ширина и след. Запись решает, придёт ли донору письмо,
а снятие отписки разрешает написать тому, кто просил не писать: такое
действие обязано оставлять причину и автора.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import stoplist
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"


@pytest.fixture
async def written(session: AsyncSession) -> MessageModel:
    """Донор с контактом, письмом в очереди и добивкой по сроку."""
    domain = await make_donor(session, HOST, email=f"editor@{HOST}")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, status=ThreadStatus.OPEN)
    session.add(thread)
    await session.flush()

    contact = (
        (await session.execute(select(ContactModel).where(ContactModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    queued = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Hi",
        body="Hi",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(queued)
    await session.flush()
    return queued


async def status_in_base(session: AsyncSession, message: MessageModel) -> MessageStatus:
    """Состояние письма по базе, а не по памяти сессии.

    Через `refresh` этот вопрос не задать: он гасит ещё не записанное
    и показывает прежнее значение — на этом тест сначала и соврал.
    """
    await session.flush()
    found = await session.execute(select(MessageModel.status).where(MessageModel.id == message.id))
    return found.scalar_one()


class TestAddingByHand:
    async def test_domain_goes_in(self, session: AsyncSession, written: MessageModel) -> None:
        row = await stoplist.add(
            session, HOST, reason=SuppressionReason.SUPPLIER, author="анна@site.com"
        )

        assert row.host == HOST
        assert row.email is None
        assert row.created_by == "анна@site.com"

    async def test_unknown_domain_is_created(self, session: AsyncSession) -> None:
        """Список поставщиков приходит раньше первого прогона: ждать,
        пока домен найдётся сам, значит написать ему до того."""
        await stoplist.add(
            session, "never-seen.example.test", reason=SuppressionReason.SUPPLIER, author="а@б.в"
        )

        found = await session.execute(
            select(DomainModel).where(DomainModel.host == "never-seen.example.test")
        )
        assert found.scalars().first() is not None

    async def test_address_goes_in(self, session: AsyncSession) -> None:
        row = await stoplist.add(
            session, "Editor@Site.Test", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert row.email == "editor@site.test"
        assert row.host is None

    async def test_adding_takes_letters_off(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """Иначе письмо висит в очереди готовым, а отказ придёт человеку
        в момент отправки — не в базу."""
        await stoplist.add(session, HOST, reason=SuppressionReason.MANUAL, author="а@б.в")

        assert await status_in_base(session, written) is MessageStatus.STOPPED

    async def test_address_row_takes_its_letters_off(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """Запись по адресу снимает письма этому адресу, а не всему домену."""
        await stoplist.add(
            session, f"editor@{HOST}", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert await status_in_base(session, written) is MessageStatus.STOPPED

    async def test_second_row_is_refused(self, session: AsyncSession) -> None:
        await stoplist.add(session, HOST, reason=SuppressionReason.MANUAL, author="а@б.в")

        with pytest.raises(stoplist.StopListError, match="уже в стоп-листе"):
            await stoplist.add(session, HOST, reason=SuppressionReason.SUPPLIER, author="а@б.в")

    @pytest.mark.parametrize(
        "reason", [SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINED]
    )
    async def test_donor_decision_is_not_entered_by_hand(
        self, session: AsyncSession, reason: SuppressionReason
    ) -> None:
        """Отписку заводит страница, жалобу — приём ответов. Рука здесь
        означала бы запись о решении донора, которого он не принимал."""
        with pytest.raises(stoplist.StopListError, match="ставит сам сервис"):
            await stoplist.add(session, HOST, reason=reason, author="а@б.в")

    @pytest.mark.parametrize(
        "target",
        ["", "  ", "не домен", "@site.test", "site", "a@b@c.test", "ex ample.com", "-bad-.test"],
    )
    async def test_nonsense_target_is_refused(self, session: AsyncSession, target: str) -> None:
        with pytest.raises(stoplist.StopListError, match="не похоже"):
            await stoplist.add(session, target, reason=SuppressionReason.MANUAL, author="а@б.в")


async def domain_hosts(session: AsyncSession) -> list[str]:
    found = await session.execute(select(DomainModel.host).order_by(DomainModel.id))
    return list(found.scalars().all())


class TestDomainKey:
    """Домен ложится на ключ базы, а не туда, как его вписали.

    Проверка QA 10.10.2026: `www.` и поддомен донора, ссылка на сайт заводились
    новыми строками `domains` — донор оставался открыт, а экран говорил
    «в стоп-листе». Прогон и отправка ищут запись по ключу `donors/host.py`,
    значит и стоп-лист обязан писать её туда же.
    """

    @pytest.mark.parametrize(
        "entered",
        [
            "www.example.com",
            "https://www.example.com/blog/post?page=2",
            "EXAMPLE.COM",
            "example.com:8080",
            "blog.example.com",
            "http://shop.blog.example.com/",
        ],
    )
    async def test_any_spelling_of_the_site_lands_on_the_donor(
        self, session: AsyncSession, entered: str
    ) -> None:
        await make_donor(session, "example.com")

        row = await stoplist.add(session, entered, reason=SuppressionReason.MANUAL, author="а@б.в")

        assert row.host == "example.com"
        assert row.new_domain is False
        assert await domain_hosts(session) == ["example.com"]

    async def test_www_link_takes_the_donor_letters_off(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """Зона `.test` списку суффиксов неизвестна: корнем её не свести, но `www.`,
        схема и путь отходят — как у обхода."""
        await stoplist.add(
            session, f"https://www.{HOST}/contact", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert await status_in_base(session, written) is MessageStatus.STOPPED
        assert await domain_hosts(session) == [HOST]

    async def test_subdomain_in_an_unknown_zone_is_a_new_domain_and_says_so(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        """Корня `.test` список суффиксов не знает, и угадывать его стоп-лист
        не берётся: заводит новый домен — и говорит, что он новый."""
        row = await stoplist.add(
            session, f"blog.{HOST}", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert row.host == f"blog.{HOST}"
        assert row.new_domain is True
        assert await status_in_base(session, written) is MessageStatus.QUEUED

    async def test_cyrillic_domain_lands_on_the_donor_as_the_base_writes_it(
        self, session: AsyncSession
    ) -> None:
        """База хранит домен в написании источника: кириллица остаётся кириллицей."""
        await make_donor(session, "пример.рф")

        row = await stoplist.add(
            session, "https://WWW.Пример.РФ/о-нас", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert row.host == "пример.рф"
        assert row.new_domain is False

    async def test_address_stays_an_address(self, session: AsyncSession) -> None:
        """Адрес на поддомене не сводится к домену: запись по адресу держит один ящик."""
        row = await stoplist.add(
            session, "Editor@Blog.Example.com", reason=SuppressionReason.MANUAL, author="а@б.в"
        )

        assert row.email == "editor@blog.example.com"
        assert row.host is None
        assert row.new_domain is False
        assert await domain_hosts(session) == []


class TestTooLong:
    """Пределы длины — словами стоп-листа, а не английским разбором запроса."""

    async def test_domain_longer_than_dns_allows(self, session: AsyncSession) -> None:
        with pytest.raises(stoplist.StopListError, match="Домен длиннее 253 знаков"):
            await stoplist.add(
                session, "x" * 300 + ".test", reason=SuppressionReason.MANUAL, author="а@б.в"
            )

    async def test_address_longer_than_the_column(self, session: AsyncSession) -> None:
        with pytest.raises(stoplist.StopListError, match="Адрес длиннее 255 знаков"):
            await stoplist.add(
                session, "x" * 250 + "@site.test", reason=SuppressionReason.MANUAL, author="а@б.в"
            )

    async def test_long_link_to_a_short_domain_is_fine(self, session: AsyncSession) -> None:
        """Предел — у домена, а не у ссылки: путь в 600 знаков домену не помеха."""
        row = await stoplist.add(
            session,
            "https://www.example.com/" + "p" * 600,
            reason=SuppressionReason.MANUAL,
            author="а@б.в",
        )

        assert row.host == "example.com"


class TestRemoving:
    async def test_own_row_needs_no_explanation(self, session: AsyncSession) -> None:
        row = await stoplist.add(session, HOST, reason=SuppressionReason.SUPPLIER, author="а@б.в")

        taken = await stoplist.remove(session, row.id, reason=None)

        assert taken.host == HOST
        assert (await session.execute(select(SuppressionModel))).scalars().all() == []

    async def test_unsubscribe_without_a_reason_is_refused(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        session.add(
            SuppressionModel(
                domain_id=written.domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by="страница отписки",
            )
        )
        await session.flush()
        row = (await session.execute(select(SuppressionModel))).scalars().one()

        with pytest.raises(stoplist.StopListError, match="надо написать, почему"):
            await stoplist.remove(session, row.id, reason="   ")

        assert await session.get(SuppressionModel, row.id) is not None

    async def test_unsubscribe_with_a_reason_is_taken_off(
        self, session: AsyncSession, written: MessageModel
    ) -> None:
        session.add(
            SuppressionModel(
                domain_id=written.domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by="страница отписки",
            )
        )
        await session.flush()
        row = (await session.execute(select(SuppressionModel))).scalars().one()

        taken = await stoplist.remove(session, row.id, reason="написал «пишите, передумал»")

        assert taken.donor_decision
        assert (await session.execute(select(SuppressionModel))).scalars().all() == []

    async def test_missing_row_is_named(self, session: AsyncSession) -> None:
        with pytest.raises(stoplist.StopListError, match="№10000"):
            await stoplist.remove(session, 10_000, reason=None)


class TestListing:
    async def test_fresh_on_top_with_the_host(self, session: AsyncSession) -> None:
        await stoplist.add(session, HOST, reason=SuppressionReason.MANUAL, author="а@б.в")
        session.add(
            SuppressionModel(
                email="old@site.test",
                reason=SuppressionReason.UNSUBSCRIBED,
                created_by="приём ответов",
                created_at=NOW - timedelta(days=1),
            )
        )
        await session.flush()

        rows = await stoplist.rows(session)

        assert [row.target for row in rows] == [HOST, "old@site.test"]
        assert [row.donor_decision for row in rows] == [False, True]
