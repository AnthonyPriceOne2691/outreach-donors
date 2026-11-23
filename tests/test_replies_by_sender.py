"""Привязка по отправителю — третья ступень приёма (`replies/by_sender.py`), на настоящей базе.

Ответ, написанный заново на адрес ящика, — без метки и без заголовков цепочки — ищет адресата
по адресу отправителя среди наших ушедших писем направления, на ящик которого он пришёл.
Привязка — только к единственному адресату и к его последнему письму; ноль адресатов, двое,
наш собственный ящик, чужое направление или неотправленное письмо — ответ остаётся человеку,
как раньше. И только подтверждённому отправителю: подпись DKIM домена из «От кого» или SPF
с тем же доменом конверта; иначе — человеку, а адресата не ищут вовсе. Метка, цепочка
и остальные причины не привязки — как прежде. Адресаты здесь — контакты доноров
и рекламодателей; лиды продаж — `test_sales_reply_sender.py`, вид полей платформы —
`test_inbound_text.py`. Домены и адреса выдуманы.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ContactSource, MessageStatus, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    SendingDomainModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.replies import by_sender
from backend.features.replies.binding import BindingWay, Unbound
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Accepted, Inbox
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
SECRET = "b" * 32
#: Ящики направлений: на них пишут заново, их домены — наши домены рассылки.
DONORS_BOX = "anna@mail-donors.example.test"
OFFERS_BOX = "offers@mail-offers.example.test"
EDITOR = "editor@donor.example.test"
LOGGER = "backend.features.replies.by_sender"


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.fixture(autouse=True)
async def boxes(session: AsyncSession) -> None:
    """Ящики доноров и рекламодателей: по ним ответ узнаёт своё направление."""
    for email, stage in ((DONORS_BOX, Stage.DONORS), (OFFERS_BOX, Stage.ADVERTISERS)):
        session.add(
            SenderModel(
                domain=email.split("@", 1)[1],
                email=email,
                stage=stage,
                daily_cap=20,
                status=SenderStatus.FREE,
                enabled=True,
            )
        )
    await session.flush()


async def campaign(session: AsyncSession, stage: Stage = Stage.DONORS) -> CampaignModel:
    found = CampaignModel(stage=stage, name=f"Рассылка {stage.value}", status="running")
    session.add(found)
    await session.flush()
    return found


async def contact(
    session: AsyncSession, email: str = EDITOR, host: str | None = None
) -> ContactModel:
    """Адрес сайта: строка `contacts` своего домена (по умолчанию — домена адреса)."""
    domain = DomainModel(host=host or email.rpartition("@")[2].strip().lower())
    session.add(domain)
    await session.flush()
    found = ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE)
    session.add(found)
    await session.flush()
    return found


async def letter(
    session: AsyncSession,
    to: ContactModel,
    within: CampaignModel,
    *,
    sent_at: datetime | None = NOW,
    message_id: str | None = None,
) -> MessageModel:
    """Наше письмо контакту в своей переписке; `sent_at` пусто — ещё в очереди."""
    thread = ThreadModel(domain_id=to.domain_id, campaign_id=within.id, contact_id=to.id)
    session.add(thread)
    await session.flush()
    found = MessageModel(
        campaign_id=within.id,
        thread_id=thread.id,
        domain_id=to.domain_id,
        contact_id=to.id,
        step=0,
        status=MessageStatus.SENT if sent_at else MessageStatus.QUEUED,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=sent_at,
        internet_message_id=message_id,
        idempotency_key=f"by-sender:{thread.id}",
    )
    session.add(found)
    await session.flush()
    return found


def fresh(sender: str = EDITOR, *, to: str = DONORS_BOX, **extra: object) -> Incoming:
    """Письмо, написанное заново: на адрес ящика, без метки и без заголовков цепочки.
    Отправитель подтверждён подписью DKIM домена своего адреса, если `extra` не скажет иначе."""
    values: dict[str, object] = {
        "message_id": "<fresh-1@donor.example.test>",
        "to": (to,),
        "from_email": sender,
        "subject": "Guest post",
        "text": "Hello! Do you still need a guest post on our site?",
        "dkim_passed": (sender.rpartition("@")[2].strip().lower(),),
    }
    values.update(extra)
    return Incoming(**values)  # type: ignore[arg-type]


async def accepted(session: AsyncSession, incoming: Incoming) -> tuple[Accepted, ReplyModel]:
    """Приём по умолчанию — поиск только по контактам — и легший ответ."""
    got = await Inbox(session, now=NOW).accept(incoming)
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return got, reply


def assert_bound(got: Accepted, reply: ReplyModel, to: MessageModel) -> None:
    assert (got.bound, got.way, got.unbound) == (True, BindingWay.SENDER, None)
    assert (reply.message_id, reply.thread_id, reply.unbound_reason) == (to.id, to.thread_id, None)


def assert_left_to_a_human(
    got: Accepted, reply: ReplyModel, why: Unbound = Unbound.NO_LABEL
) -> None:
    assert (got.bound, got.way, got.unbound) == (False, BindingWay.NONE, why)
    assert (reply.message_id, reply.thread_id, reply.unbound_reason) == (None, None, why)


class TestOneAddressee:
    async def test_the_only_contact_with_this_address_takes_the_reply(
        self, session: AsyncSession
    ) -> None:
        ours = await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh())

        assert_bound(got, reply, ours)
        # Дальше — как у привязанного меткой: ответ донора уходит разбору цены.
        assert got.to_parse == reply.id

    async def test_one_contact_in_two_campaigns_is_one_addressee_and_its_latest_letter_wins(
        self, session: AsyncSession
    ) -> None:
        """Считаются адресаты, а не письма. Последнее — по времени отправки, а не по номеру:
        письмо с меньшим номером ушло позже."""
        editor = await contact(session)
        later = await letter(session, editor, await campaign(session), sent_at=NOW)
        earlier = await letter(
            session, editor, await campaign(session), sent_at=NOW - timedelta(days=9)
        )
        assert later.id < earlier.id

        got, reply = await accepted(session, fresh())

        assert_bound(got, reply, later)

    async def test_letters_sent_at_one_moment_the_higher_number_wins(
        self, session: AsyncSession
    ) -> None:
        editor = await contact(session)
        await letter(session, editor, await campaign(session))
        second = await letter(session, editor, await campaign(session))

        got, reply = await accepted(session, fresh())

        assert_bound(got, reply, second)

    async def test_case_and_edge_spaces_do_not_matter(self, session: AsyncSession) -> None:
        ours = await letter(
            session,
            await contact(session, " Editor@Donor.Example.Test "),
            await campaign(session),
        )

        got, reply = await accepted(session, fresh("  EDITOR@donor.example.test "))

        assert_bound(got, reply, ours)

    async def test_the_direction_is_the_box_the_reply_came_to(self, session: AsyncSession) -> None:
        """Тот же адрес у доноров и у рекламодателей — по одному адресату в каждом направлении:
        ответ на ящик рекламодателей ложится к письму рекламодателю."""
        editor = await contact(session)
        await letter(session, editor, await campaign(session, Stage.DONORS))
        offer = await letter(session, editor, await campaign(session, Stage.ADVERTISERS))

        got, reply = await accepted(session, fresh(to=OFFERS_BOX))

        assert_bound(got, reply, offer)


class TestLeftToAHuman:
    async def test_nobody_was_written_from_this_address(self, session: AsyncSession) -> None:
        await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh("someone@elsewhere.example.test"))

        assert_left_to_a_human(got, reply)

    async def test_the_same_domain_with_another_mailbox_is_not_a_match(
        self, session: AsyncSession
    ) -> None:
        """Совпадение только по домену не считается: `info@` и `editor@` — разные люди."""
        await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh("info@donor.example.test"))

        assert_left_to_a_human(got, reply)

    async def test_two_contacts_with_one_address_are_two_addressees(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Один адрес у двух сайтов: какой диалог продолжает человек, по адресу не понять."""
        caplog.set_level(logging.INFO, logger=LOGGER)
        agency = "seo@agency.example.test"
        within = await campaign(session)
        await letter(session, await contact(session, agency, "first-site.example.test"), within)
        await letter(session, await contact(session, agency, "second-site.example.test"), within)

        got, reply = await accepted(session, fresh(agency))

        assert_left_to_a_human(got, reply)
        [said] = [r for r in caplog.records if r.name == LOGGER]
        assert said.getMessage().startswith("приём: по отправителю не привязан")
        assert said.getMessage().endswith(": 2")
        assert (said.__dict__["addressees"], said.__dict__["stage"]) == (2, Stage.DONORS.value)
        assert agency not in repr(vars(said))

    async def test_a_letter_still_in_the_queue_does_not_count(self, session: AsyncSession) -> None:
        await letter(session, await contact(session), await campaign(session), sent_at=None)

        got, reply = await accepted(session, fresh())

        assert_left_to_a_human(got, reply)

    async def test_a_letter_of_another_direction_does_not_count(
        self, session: AsyncSession
    ) -> None:
        await letter(session, await contact(session), await campaign(session, Stage.ADVERTISERS))

        got, reply = await accepted(session, fresh(to=DONORS_BOX))

        assert_left_to_a_human(got, reply)

    async def test_without_our_address_there_is_no_direction_to_search_in(
        self, session: AsyncSession
    ) -> None:
        """Письмо пришло не на наш домен рассылки: направления нет — искать не в чем."""
        await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh(to="info@not-ours.example.test"))

        assert_left_to_a_human(got, reply)

    @pytest.mark.parametrize(
        "sender",
        [
            "team@mail-offers.example.test",  # домен ящика другого направления
            "bounce@replies.mail-donors.example.test",  # поддомен нашего домена
            "spare@mail-spare.example.test",  # домен рассылки без ящика
        ],
    )
    async def test_our_own_box_is_not_an_addressee(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture, sender: str
    ) -> None:
        """Отправитель — наш ящик: пробное письмо, пересылка коллеги или петля, — даже если
        письмо этому адресу у нас есть."""
        caplog.set_level(logging.INFO, logger=LOGGER)
        session.add(
            SendingDomainModel(domain="mail-spare.example.test", stage=Stage.DONORS, daily_limit=5)
        )
        await session.flush()
        await letter(session, await contact(session, sender), await campaign(session))

        got, reply = await accepted(session, fresh(sender))

        assert_left_to_a_human(got, reply)
        [said] = [r for r in caplog.records if r.name == LOGGER]
        assert said.getMessage().startswith("приём: ответ от нашего ящика")
        assert sender not in repr(vars(said))


#: Подпись пересылки или платформы рассылки: прошла, но домен — не домен «От кого».
RELAY = "relay.example.test"


class TestOnlyAConfirmedSender:
    """«От кого» — просто поле письма: по отправителю привязывается только подтверждённый —
    подписью DKIM домена из «От кого» или SPF pass с тем же доменом обратного адреса конверта.
    Домены — точно, без регистра: поддомен и домен организации — не тот же домен."""

    @pytest.mark.parametrize(
        ("sender", "proof"),
        [
            (EDITOR, {"dkim_passed": ("donor.example.test",)}),
            (EDITOR, {"dkim_passed": (RELAY, "donor.example.test")}),
            (EDITOR, {"spf_passed": True, "envelope_from": "bounce@donor.example.test"}),
            ("Editor@DONOR.Example.Test", {"dkim_passed": ("donor.example.test",)}),
            (EDITOR, {"dkim_passed": ("DONOR.Example.Test",)}),
            (EDITOR, {"spf_passed": True, "envelope_from": "Bounce@Donor.EXAMPLE.test"}),
        ],
        ids=[
            "dkim домена From",
            "dkim — одна из подписей",
            "SPF и тот же домен конверта",
            "регистр From",
            "регистр подписи",
            "регистр конверта",
        ],
    )
    async def test_a_confirmed_sender_is_bound(
        self, session: AsyncSession, sender: str, proof: dict[str, Any]
    ) -> None:
        ours = await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh(sender, **{"dkim_passed": (), **proof}))

        assert_bound(got, reply, ours)

    @pytest.mark.parametrize(
        "proof",
        [
            {},
            {"dkim_passed": (RELAY,), "envelope_from": EDITOR},
            {"spf_passed": True, "envelope_from": f"bounce@{RELAY}"},
            {"envelope_from": EDITOR},
            {
                "dkim_passed": ("mail.donor.example.test",),
                "spf_passed": True,
                "envelope_from": "bounce@mail.donor.example.test",
            },
            {
                "dkim_passed": ("example.test",),
                "spf_passed": True,
                "envelope_from": "bounce@example.test",
            },
        ],
        ids=[
            "ни подписи, ни SPF",
            "подпись пересылки, SPF не pass",
            "SPF pass, конверт чужого домена",
            "конверт свой, SPF не pass",
            "поддомен",
            "домен организации",
        ],
    )
    async def test_an_unconfirmed_sender_is_left_to_a_human(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture, proof: dict[str, Any]
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER)
        await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh(**{"dkim_passed": (), **proof}))

        # Адресат один — и причина своя: «однозначного адресата нет» было бы неправдой.
        assert_left_to_a_human(got, reply, Unbound.UNCONFIRMED_SENDER)
        [said] = [r for r in caplog.records if r.name == LOGGER]
        assert said.getMessage() == (
            "приём: по отправителю не привязан — отправитель не подтверждён (dkim/SPF), "
            f"направление {Stage.DONORS.value}"
        )
        assert said.__dict__["stage"] == Stage.DONORS.value
        # Ни адреса, ни его домена: строку журнала читают люди, которым чужой ящик не нужен.
        assert "donor.example.test" not in repr(vars(said))

    async def test_without_an_addressee_an_unconfirmed_sender_keeps_the_old_reason(
        self, session: AsyncSession
    ) -> None:
        """Подлинность решает, только когда адресат по адресу один: адресата нет — причина
        прежняя («однозначного адресата нет»), а не «отправитель не подтверждён»."""
        got, reply = await accepted(session, fresh(dkim_passed=(RELAY,)))

        assert_left_to_a_human(got, reply)

    async def test_two_addressees_and_an_unconfirmed_sender_keep_the_old_reason(
        self, session: AsyncSession
    ) -> None:
        for host in ("one.example.test", "two.example.test"):
            await letter(session, await contact(session, host=host), await campaign(session))

        got, reply = await accepted(session, fresh(dkim_passed=(RELAY,)))

        assert_left_to_a_human(got, reply)


class TestOtherWaysAsBefore:
    async def test_the_label_wins_over_the_sender(self, session: AsyncSession) -> None:
        """Метка называет одно письмо, отправитель — адресат другого: решает метка."""
        within = await campaign(session)
        labelled = await letter(session, await contact(session, "boss@other.example.test"), within)
        await letter(session, await contact(session), within)
        address = reply_to.address_for(labelled.id, sender_email=DONORS_BOX, secret=SECRET)

        got, reply = await accepted(session, fresh(to=address))

        assert (got.way, reply.message_id) == (BindingWay.LABEL, labelled.id)

    async def test_thread_headers_win_over_the_sender(self, session: AsyncSession) -> None:
        within = await campaign(session)
        threaded = await letter(
            session,
            await contact(session, "boss@other.example.test"),
            within,
            message_id="<ours-1@mail-donors.example.test>",
        )
        await letter(session, await contact(session), within)

        got, reply = await accepted(session, fresh(in_reply_to="<ours-1@mail-donors.example.test>"))

        assert (got.way, reply.message_id) == (BindingWay.HEADERS, threaded.id)

    @pytest.mark.parametrize(
        ("why", "extra"),
        [
            (Unbound.FOREIGN_THREAD, {"references": ("<theirs@elsewhere.example.test>",)}),
            (
                Unbound.BAD_SIGNATURE,
                {"to": f"anna+m7.{'0' * reply_to.SIGNATURE_LEN}@replies.mail-donors.example.test"},
            ),
            (
                Unbound.NO_SUCH_LETTER,
                {"to": reply_to.address_for(999_999, sender_email=DONORS_BOX, secret=SECRET)},
            ),
        ],
    )
    async def test_other_reasons_are_left_alone(
        self, session: AsyncSession, why: Unbound, extra: dict[str, Any]
    ) -> None:
        """Отправитель — ровно один адресат, но причина не «написано заново»: у неё своё
        действие человека, и по отправителю такой ответ не привязывается."""
        await letter(session, await contact(session), await campaign(session))

        got, reply = await accepted(session, fresh(**extra))

        assert_left_to_a_human(got, reply, why)


class TestContactsLookup:
    async def test_pairs_letters_with_the_contact_latest_first(self, session: AsyncSession) -> None:
        editor = await contact(session)
        earlier = await letter(
            session, editor, await campaign(session), sent_at=NOW - timedelta(days=3)
        )
        later = await letter(session, editor, await campaign(session))

        found = await by_sender.mail_letters(session, EDITOR, Stage.DONORS)

        assert found == [(later.id, f"contact:{editor.id}"), (earlier.id, f"contact:{editor.id}")]

    async def test_a_sales_letter_has_no_contact_addressee(self, session: AsyncSession) -> None:
        """Письмо продаж уходит лиду переписки; контакт у такого письма — не адресат."""
        await letter(session, await contact(session), await campaign(session, Stage.SALES))

        assert await by_sender.mail_letters(session, EDITOR, Stage.SALES) == []


class TestJournal:
    async def test_the_binding_names_the_letter_and_the_direction_but_not_the_address(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER)
        ours = await letter(session, await contact(session), await campaign(session))

        await accepted(session, fresh())

        [said] = [r for r in caplog.records if r.name == LOGGER]
        assert said.getMessage().startswith("приём: ответ привязан по отправителю")
        assert (said.__dict__["letter"], said.__dict__["stage"]) == (ours.id, Stage.DONORS.value)
        assert EDITOR not in repr(vars(said))
