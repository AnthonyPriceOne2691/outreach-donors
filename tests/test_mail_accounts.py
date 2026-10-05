"""Учётки почтовой платформы по направлениям: ключ, ключ событий, предохранитель.

Решение Anthony 05.10.2026: механизм — общий, направление кладёт свой
конфиг. У «Продаж» свой субаккаунт и свои домены: их письма должны уходить
их ключом (домены отправителя подтверждены в их учётке, наш ключ платформа
не примет), события их учётки — приниматься тем же вебхуком, предохранитель —
работать у направления отдельно. Чего своего у направления нет — общее.
"""

from __future__ import annotations

import time
from datetime import timedelta

import httpx
import pytest
from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus
from backend.features.letters import identity, transport_factory
from backend.features.letters.followups import send_due
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import (
    MaybeSentError,
    NullTransport,
    Outgoing,
    Transport,
    TransportError,
)
from backend.features.letters.transport_factory import Transports, build_transport, in_use
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events import _keypair, _sign
from tests.test_followups import _chain_start
from tests.test_mail_identity import NOW, SECRET, SENDER, Recording, _queued
from tests.test_transport_lifecycle import ClosingTransport

#: Выдуманные значения ключей: по ним видно, чей ключ ушёл в запрос.
SHARED_VALUE = "shared-value"
SALES_VALUE = "sales-value"


@pytest.fixture
def shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общая учётка: свой ключ и предохранитель на один адрес."""
    monkeypatch.setattr(cfg, "SENDGRID_API_KEY", SHARED_VALUE)
    monkeypatch.setattr(cfg, "ALLOWED_RECIPIENTS", ("me@site.test",))
    monkeypatch.setattr(cfg, "EVENTS_PUBLIC_KEY", "")


class TestAccount:
    def test_without_own_settings_it_is_the_shared_one(self, shared: None) -> None:
        for account in (cfg.mail_account(), cfg.mail_account("donors")):
            assert account.api_key == SHARED_VALUE
            assert account.allowed_recipients == ("me@site.test",)

    def test_own_key_and_list_win_and_are_named(
        self, shared: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OUTREACH_SALES_SENDGRID_API_KEY", SALES_VALUE)
        monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "Lead@Sales.test, @sales.test")

        sales = cfg.mail_account("sales")

        assert sales.api_key == SALES_VALUE
        assert sales.allowed_recipients == ("lead@sales.test", "@sales.test")
        assert sales.key_setting == "OUTREACH_SALES_SENDGRID_API_KEY"
        assert sales.allowlist_setting == "OUTREACH_SALES_ALLOWED_RECIPIENTS"

    def test_own_key_without_own_list_is_refused_not_borrowed(
        self, shared: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Общий список снимут по своим причинам — направление со своей
        учёткой не должно молча остаться без предохранителя."""
        monkeypatch.setenv("OUTREACH_SALES_SENDGRID_API_KEY", SALES_VALUE)

        with pytest.raises(TransportError, match="OUTREACH_SALES_ALLOWED_RECIPIENTS не задан"):
            build_transport("sendgrid", stage="sales")

    def test_empty_own_list_is_no_safety_on_purpose_and_keys_are_trimmed(
        self, shared: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OUTREACH_SALES_SENDGRID_API_KEY", f"{SALES_VALUE}\n")
        monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")

        sales = cfg.mail_account("sales")

        assert sales.api_key == SALES_VALUE
        assert sales.allowed_recipients == ()
        assert sales.refusal is None

    def test_event_keys_are_all_accounts_without_blanks_or_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "EVENTS_PUBLIC_KEY", "shared-pub")
        monkeypatch.setenv("OUTREACH_SALES_EVENTS_PUBLIC_KEY", "sales-pub")
        monkeypatch.setenv("OUTREACH_ADVERTISERS_EVENTS_PUBLIC_KEY", "shared-pub")

        keys = cfg.events_public_keys(["donors", "advertisers", "sales"])

        assert keys == ("shared-pub", "sales-pub")


def _outgoing(to: str) -> Outgoing:
    return Outgoing(
        message_id=1,
        to=to,
        from_email=SENDER,
        from_name="Anna",
        reply_to=None,
        subject="Hello",
        body="Hi",
        internet_message_id=identity.new_message_id(1, sender_email=SENDER),
    )


class TestTransport:
    async def test_letter_of_a_stage_goes_with_its_key_and_its_list(
        self, shared: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OUTREACH_SALES_SENDGRID_API_KEY", SALES_VALUE)
        monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "@sales.test")
        keys: list[str] = []

        def platform(request: httpx.Request) -> httpx.Response:
            keys.append(request.headers["Authorization"])
            return httpx.Response(202, headers={PROVIDER_ID_HEADER: "sg-1"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(platform))
        transport = SendGridTransport(account=cfg.mail_account("sales"), http=http)

        await transport.send(_outgoing("lead@sales.test"))
        with pytest.raises(TransportError, match="OUTREACH_SALES_ALLOWED_RECIPIENTS"):
            await transport.send(_outgoing("me@site.test"))
        await transport.aclose()

        assert keys == [f"Bearer {SALES_VALUE}"]

    def test_no_key_anywhere_names_both_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "SENDGRID_API_KEY", "")

        with pytest.raises(
            TransportError,
            match=r"OUTREACH_SALES_SENDGRID_API_KEY \(или общий OUTREACH_SENDGRID_API_KEY\)",
        ):
            build_transport("sendgrid", stage="sales")

    async def test_one_transport_per_stage_and_all_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[tuple[str | None, ClosingTransport]] = []

        def build(name: str | None = None, *, stage: str | None = None) -> Transport:
            built.append((stage, ClosingTransport()))
            return built[-1][1]

        monkeypatch.setattr(transport_factory, "build_transport", build)

        async with in_use(Transports()) as transports:
            first = transports.for_stage("donors")
            assert transports.for_stage("donors") is first
            transports.for_stage("sales")

        assert [stage for stage, _ in built] == ["donors", "sales"]
        assert all(transport.closed for _, transport in built)


class _ByStage:
    """Транспорты по этапам — запоминает, о каком этапе спросили."""

    def __init__(self, **transports: Transport) -> None:
        self.transports = transports
        self.asked: list[str] = []

    def for_stage(self, stage: str) -> Transport:
        self.asked.append(stage)
        return self.transports[stage]


class TestSending:
    async def test_letter_goes_through_the_transport_of_its_stage(
        self, session: AsyncSession
    ) -> None:
        message = await _queued(session)
        donors, advertisers = Recording(), Recording()
        source = _ByStage(donors=donors, advertisers=advertisers)

        await Sending(session, source, now=NOW).send(message.id)

        assert source.asked == ["donors"]
        assert [outgoing.message_id for outgoing in donors.seen] == [message.id]
        assert advertisers.seen == []

    async def test_stage_mail_not_built_is_a_refusal_and_the_letter_stays(
        self, session: AsyncSession
    ) -> None:
        """Отказ отправки, а не падение: проход добивок отложит письмо на час."""
        message = await _queued(session)

        class Unbuilt:
            def for_stage(self, stage: str) -> Transport:
                raise TransportError("OUTREACH_DONORS_SENDGRID_API_KEY не задан")

        with pytest.raises(SendError, match="Почта этапа «donors» не собрана"):
            await Sending(session, Unbuilt(), now=NOW).send(message.id)
        assert message.status is MessageStatus.QUEUED


class TestEvents:
    async def test_event_of_a_stage_account_is_taken_and_a_stranger_is_not(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, shared_public = _keypair()
        own, own_public = _keypair()
        stranger, _ = _keypair()
        monkeypatch.setattr(cfg, "EVENTS_PUBLIC_KEY", shared_public)
        monkeypatch.setenv("OUTREACH_ADVERTISERS_EVENTS_PUBLIC_KEY", own_public)
        payload = b'[{"event":"delivered","message_id":"999999"}]'
        stamp = str(int(time.time()))

        async def post(signed_by: object) -> httpx.Response:
            return await client.post(
                "/api/events/delivery",
                content=payload,
                headers={
                    "X-Twilio-Email-Event-Webhook-Signature": _sign(signed_by, payload, stamp),  # type: ignore[arg-type]
                    "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
                    "Content-Type": "application/json",
                },
            )

        taken = await post(own)
        refused = await post(stranger)

        assert taken.status_code == 200, taken.text
        assert taken.json()["accepted"] is True
        assert refused.status_code == 403


def _platform_account() -> cfg.MailAccount:
    return cfg.MailAccount(api_key=SHARED_VALUE, events_public_key="", allowed_recipients=())


class TestMaybeSent:
    async def test_no_answer_after_the_platform_got_it_keeps_the_letter_sending(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Находка «Продаж» 05.10.2026: таймаут ответа возвращал письмо в очередь
        как «точно не ушло» — и кнопка или добивка отправляли его второй раз."""
        monkeypatch.setattr(cfg, "INBOUND_SECRET", SECRET)
        message = await _queued(session)
        got: list[httpx.Request] = []

        def platform(request: httpx.Request) -> httpx.Response:
            got.append(request)
            raise httpx.ReadTimeout("ответа нет", request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(platform))
        transport = SendGridTransport(account=_platform_account(), http=http)

        with pytest.raises(MaybeSentError, match="могло уйти"):
            await Sending(session, transport, now=NOW).send(message.id)
        await transport.aclose()

        assert len(got) == 1
        assert message.status is MessageStatus.SENDING

    async def test_unsent_request_names_no_key_and_has_no_cause(self) -> None:
        def platform(request: httpx.Request) -> httpx.Response:
            raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {SALES_VALUE}\\n'")

        http = httpx.AsyncClient(transport=httpx.MockTransport(platform))
        transport = SendGridTransport(account=_platform_account(), http=http)

        with pytest.raises(TransportError) as caught:
            await transport.send(_outgoing("lead@sales.test"))
        await transport.aclose()

        assert SALES_VALUE not in str(caught.value)
        assert caught.value.__cause__ is None

    async def test_followup_with_unknown_outcome_is_not_sent_again(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        first, _ = await _chain_start(session, followup_days=[1, 1])
        later = NOW + timedelta(days=2)

        class Silent(NullTransport):
            async def send(self, outgoing: Outgoing) -> str:
                raise MaybeSentError("платформа не ответила — письмо могло уйти")

        report = await send_due(session, transport=Silent(), limit=5, now=later)
        again = await send_due(session, transport=Silent(), limit=5, now=later + timedelta(hours=2))

        assert (report.unknown, report.postponed) == (1, 0)
        assert (again.unknown, again.sent, again.postponed) == (0, 0, 0)
        await session.refresh(first)
        assert first.next_action_at is None
