"""Отсрочка доставки у письма (`letters/deferrals.py`) — на настоящей базе, без настоящей платформы.

Письмо №404 рекламодателю шесть часов было «отправлено» без исхода: платформа прислала четырнадцать
отсрочек с одним текстом — сервер получателя не принимал соединение, — а мы их отбрасывали. Здесь
отсрочка приходит строкой события платформы через настоящую ручку (подпись — своей парой ключей)
и остаётся у письма: вид причины, попытки, ответ сервера; статус «ушло» она не трогает, доставка
или отказ после неё — исход как раньше, а сама отсрочка видна в карточке переписки и на экране
писем.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import events
from backend.features.letters.deferrals import deferral_of, refusal_of
from backend.features.letters.delivery_trouble import WORDS, Trouble
from backend.features.letters.events import DeliveryEvent, apply_events
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import columns_down_and_up
from tests.test_delivery_events import _keypair, _sign
from tests.thread_letters import Conversation, conversation

HOST = "deferred.example.test"
#: Кому ушло письмо — адрес событий о нём.
TO = f"editor@{HOST}"
#: Чужое письмо с тем же номером: стенд с боевым ключом или база из копии (`events._foreign`).
STRANGER = "someone@elsewhere.example.test"
#: Ушло в 10:26 по Москве; последняя отсрочка — 16:22.
SENT = datetime(2026, 10, 29, 7, 26, tzinfo=UTC)
LAST = datetime(2026, 10, 29, 13, 22, tzinfo=UTC)
#: Ответ сервера у письма №404 — адрес из сети для документации.
TIMEOUT = "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout"
UNREACHABLE = "сервер получателя не отвечает (порт 25, тайм-аут)"
COLUMNS = {
    "deferred_at",
    "deferred_since",
    "deferred_attempts",
    "deferred_reason",
    "deferred_kind",
}

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def talk(session: AsyncSession, filled_legal: None) -> Conversation:
    """Первое письмо донору ушло в 10:26 по Москве — отправкой, с переписки и ящика."""
    return await conversation(session, sent_at=SENT, host=HOST)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Своя пара ключей платформы: ручка проверяет подпись, как в бою."""
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    return private


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("admin@example.test", role=UserRole.ADMIN)
    return await sign_in("admin@example.test")


def _row(kind: str, letter: int | None, at: datetime, number: int, **fields: Any) -> dict[str, Any]:
    """Строка события в том виде, в каком её шлёт платформа: номер письма в `custom_args`."""
    row: dict[str, Any] = {
        "event": kind,
        "email": TO,
        "timestamp": round(at.timestamp()),
        "sg_event_id": f"sg-{kind}-{number}",
        "sg_message_id": "14c5d75ce93.dfd.64b469.filter0001.16648.5515E0B88.0",
        "smtp-id": "<14c5d75ce93.dfd.64b469@ismtpd-555>",
        **fields,
    }
    if letter is not None:
        row["message_id"] = str(letter)
    return row


async def _deliver(client: AsyncClient, key: Any, rows: list[dict[str, Any]]) -> Response:
    payload = json.dumps(rows).encode()
    stamp = str(int(time.time()))
    answer = await client.post(
        "/api/events/delivery",
        content=payload,
        headers={
            "X-Twilio-Email-Event-Webhook-Signature": _sign(key, payload, stamp),
            "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
            "Content-Type": "application/json",
        },
    )
    assert answer.status_code == 200, answer.text
    return answer


def _deferrals(letter: int, count: int) -> list[dict[str, Any]]:
    """Отсрочки письма №404: попытки 1…`count` с 10:26 до 16:22 по Москве, один ответ сервера."""
    step = (LAST - SENT) / (count - 1)
    return [
        _row("deferred", letter, SENT + step * n, n, response=TIMEOUT, attempt=str(n + 1))
        for n in range(count)
    ]


class TestEveryKindArrives:
    """Строка отсрочки платформы каждого вида — у письма своим видом; статус «ушло» прежний."""

    @pytest.mark.parametrize(
        ("kind", "response"),
        [
            (Trouble.UNREACHABLE, TIMEOUT),
            (
                Trouble.NO_MAIL_SERVER,
                "unable to get mx info: failed to get IPs from PTR record: lookup <nil>",
            ),
            (Trouble.TRY_LATER, "451 4.7.1 Greylisting in action, please come back later"),
            (Trouble.MAILBOX_FULL, "452 4.2.2 The email account that you tried to reach is full"),
            (
                Trouble.RATE_LIMITED,
                "421 4.7.0 Too many concurrent SMTP connections from this IP address",
            ),
            (Trouble.BLOCKED, "450 4.7.25 Client host rejected: cannot find your hostname"),
            (Trouble.TLS, "remote error: tls: handshake failure"),
            (Trouble.UNKNOWN, "something odd happened"),
        ],
    )
    async def test_deferral_row_is_kept_by_its_kind(
        self,
        client: AsyncClient,
        key: Any,
        talk: Conversation,
        kind: Trouble,
        response: str,
    ) -> None:
        letter = talk.first
        follow_up = letter.next_action_at
        at = SENT + timedelta(minutes=1)

        answer = await _deliver(
            client, key, [_row("deferred", letter.id, at, 1, response=response, attempt="3")]
        )

        assert answer.json()["deferred"] == 1
        assert letter.status is MessageStatus.SENT
        assert (letter.deferred_kind, letter.deferred_attempts) == (kind.value, 3)
        assert (letter.deferred_at, letter.deferred_since) == (at, at)
        assert letter.deferred_reason == response
        assert letter.next_action_at == follow_up  # добивку отсрочка не двигает

    async def test_long_answer_is_cut_to_the_column(
        self, client: AsyncClient, key: Any, talk: Conversation
    ) -> None:
        long = "451 4.3.0 " + "y" * 600
        await _deliver(client, key, [_row("deferred", talk.first.id, SENT, 1, response=long)])

        assert talk.first.deferred_reason == long[:256]
        assert talk.first.deferred_kind == Trouble.TRY_LATER.value


class TestTheLetterKeepsItsHistory:
    async def test_the_404_case_is_named_in_the_card(
        self,
        client: AsyncClient,
        key: Any,
        talk: Conversation,
        token: str,
    ) -> None:
        """Четырнадцать отсрочек одним текстом: в карточке — вид словами, попытки и часы."""
        answer = await _deliver(client, key, _deferrals(talk.first.id, 14))

        assert answer.json()["deferred"] == 14
        view = await client.get(f"/api/threads/{talk.thread.id}", headers=bearer(token))
        [card] = view.json()["letters"]
        assert card["status"] == "sent"
        assert card["refusal"] is None
        deferral = card["deferral"]
        assert (deferral["kind"], deferral["words"], deferral["attempts"]) == (
            "unreachable",
            UNREACHABLE,
            14,
        )
        assert datetime.fromisoformat(deferral["since"]) == SENT
        assert datetime.fromisoformat(deferral["last"]) == LAST

    async def test_delivered_after_deferrals_keeps_them(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        """Доставка после отсрочек — исход как раньше, отсрочка остаётся историей."""
        rows = [*_deferrals(talk.first.id, 3), _row("delivered", talk.first.id, LAST, 99)]

        answer = await _deliver(client, key, rows)

        assert (answer.json()["delivered"], answer.json()["deferred"]) == (1, 3)
        assert talk.first.status is MessageStatus.DELIVERED
        view = await client.get(f"/api/threads/{talk.thread.id}", headers=bearer(token))
        [card] = view.json()["letters"]
        assert (card["status"], card["refusal"]) == ("delivered", None)
        assert (card["deferral"]["attempts"], card["deferral"]["words"]) == (3, UNREACHABLE)

    async def test_platform_gave_up_reads_as_the_deferrals_reason(
        self, client: AsyncClient, key: Any, talk: Conversation, session: AsyncSession, token: str
    ) -> None:
        """Платформа сдалась: мягкий отказ после отсрочек — исход отказа, а слова — причина
        отсрочек, а не «срок вышел». Мягкий отказ адрес не хоронит."""
        gave_up = _row(
            "bounce",
            talk.first.id,
            LAST + timedelta(hours=60),
            99,
            reason="Message expired",
            type="blocked",
        )

        answer = await _deliver(client, key, [*_deferrals(talk.first.id, 14), gave_up])

        assert answer.json()["bounced"] == 1
        assert talk.first.status is MessageStatus.BOUNCED
        assert talk.first.failure_reason == "Message expired"
        contact = await session.get(ContactModel, talk.first.contact_id)
        assert contact is not None
        assert contact.verification_status != "bounced"
        view = await client.get(f"/api/threads/{talk.thread.id}", headers=bearer(token))
        [card] = view.json()["letters"]
        assert card["refusal"] == UNREACHABLE
        assert card["deferral"]["attempts"] == 14

    async def test_a_real_refusal_after_deferrals_is_named_itself(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        """Серый список, потом «ящика нет»: отказ назван своими словами, не словами отсрочки."""
        greylisted = _row(
            "deferred",
            talk.first.id,
            SENT,
            1,
            response="450 4.2.0 Recipient address rejected: Greylisted",
            attempt="1",
        )
        refused = _row(
            "bounce",
            talk.first.id,
            SENT + timedelta(minutes=10),
            2,
            reason="550 5.1.1 The email account that you tried to reach does not exist.",
            status="5.1.1",
            type="bounce",
        )

        await _deliver(client, key, [greylisted, refused])

        view = await client.get(f"/api/threads/{talk.thread.id}", headers=bearer(token))
        [card] = view.json()["letters"]
        assert card["refusal"] == "ящика получателя нет или он отключён (5.1.1)"
        assert card["deferral"]["kind"] == "try_later"

    async def test_dropped_by_the_platform_says_why(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        await _deliver(
            client, key, [_row("dropped", talk.first.id, SENT, 1, reason="Bounced Address")]
        )

        view = await client.get(f"/api/threads/{talk.thread.id}", headers=bearer(token))
        [card] = view.json()["letters"]
        assert card["refusal"] == f"{WORDS[Trouble.SUPPRESSED]} (раньше отказывал)"
        assert card["deferral"] is None


class TestNotOurs:
    async def test_deferral_without_our_number_touches_nothing(
        self, client: AsyncClient, key: Any, talk: Conversation
    ) -> None:
        answer = await _deliver(client, key, [_row("deferred", None, SENT, 1, response=TIMEOUT)])

        assert (answer.json()["unknown"], answer.json()["deferred"]) == (1, 0)
        assert talk.first.deferred_at is None

    async def test_foreign_deferral_touches_nothing(
        self,
        client: AsyncClient,
        key: Any,
        talk: Conversation,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Отсрочка чужого письма с нашим номером: адрес события не адрес получателя."""
        caplog.set_level(logging.WARNING, logger=events.__name__)
        stranger = _row("deferred", talk.first.id, SENT, 1, response=TIMEOUT, email=STRANGER)

        answer = await _deliver(client, key, [stranger])

        assert (answer.json()["unknown"], answer.json()["deferred"]) == (1, 0)
        assert (talk.first.deferred_at, talk.first.deferred_kind) == (None, None)
        assert any("о чужом письме" in record.getMessage() for record in caplog.records)


class TestRepeatsAndOrder:
    """События приходят «хотя бы один раз» и не по порядку: последняя отсрочка — по времени
    платформы, повтор попытку не прибавляет."""

    async def test_repeat_of_the_same_deferral_counts_once(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        one = DeliveryEvent("deferred", talk.first.id, TO, reason=TIMEOUT, at=SENT, attempt=5)

        first = await apply_events(session, [one], now=LAST)
        again = await apply_events(session, [one], now=LAST)

        assert (first.deferred, again.deferred) == (1, 0)
        assert talk.first.deferred_attempts == 5

    async def test_late_earlier_deferral_does_not_undo_the_last(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        later = DeliveryEvent("deferred", talk.first.id, TO, reason=TIMEOUT, at=LAST, attempt=9)
        earlier = DeliveryEvent(
            "deferred",
            talk.first.id,
            TO,
            reason="451 4.7.1 Greylisting in action",
            at=SENT,
            attempt=1,
        )

        report = await apply_events(session, [later, earlier], now=LAST)

        assert report.deferred == 1
        letter = talk.first
        assert (letter.deferred_at, letter.deferred_since) == (LAST, SENT)
        assert (letter.deferred_kind, letter.deferred_attempts) == ("unreachable", 9)

    async def test_late_deferral_with_a_bigger_attempt_still_counts(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        """Номер попытки — платформы: опоздавшее событие с бо́льшим номером его поднимает."""
        last = DeliveryEvent("deferred", talk.first.id, TO, reason=TIMEOUT, at=LAST)
        numbered = DeliveryEvent("deferred", talk.first.id, TO, reason=TIMEOUT, at=SENT, attempt=7)

        await apply_events(session, [last, numbered], now=LAST)

        assert talk.first.deferred_attempts == 7

    async def test_without_attempt_numbers_deferrals_are_counted(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        events_at = [SENT, SENT + timedelta(minutes=5), SENT + timedelta(minutes=5)]
        batch = [
            DeliveryEvent("deferred", talk.first.id, TO, reason=TIMEOUT, at=at) for at in events_at
        ]

        report = await apply_events(session, batch, now=LAST)

        assert (report.deferred, talk.first.deferred_attempts) == (2, 2)

    async def test_attempt_beyond_the_column_is_not_a_number(
        self, client: AsyncClient, key: Any, talk: Conversation
    ) -> None:
        """Номер попытки длиннее `integer` не роняет пачку пятисоткой: он не номер."""
        huge = _row("deferred", talk.first.id, SENT, 1, response=TIMEOUT, attempt="9" * 15)

        await _deliver(client, key, [huge])

        assert talk.first.deferred_attempts == 1


class TestLettersScreen:
    async def test_deferred_letters_are_listed_with_their_reason(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        await _deliver(client, key, _deferrals(talk.first.id, 14))

        answer = await client.get("/api/letters/deferred?stage=donors", headers=bearer(token))

        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert (body["stage"], body["total"]) == ("donors", 1)
        [shown] = body["letters"]
        assert (shown["id"], shown["host"], shown["email"]) == (talk.first.id, HOST, TO)
        assert shown["thread_id"] == talk.thread.id
        assert (shown["deferral"]["words"], shown["deferral"]["attempts"]) == (UNREACHABLE, 14)

    async def test_outcome_takes_the_letter_off_the_list(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        await _deliver(
            client,
            key,
            [*_deferrals(talk.first.id, 2), _row("delivered", talk.first.id, LAST, 99)],
        )

        answer = await client.get("/api/letters/deferred?stage=donors", headers=bearer(token))

        assert (answer.json()["letters"], answer.json()["total"]) == ([], 0)

    async def test_other_stage_and_audience_are_apart(
        self, client: AsyncClient, key: Any, talk: Conversation, token: str
    ) -> None:
        await _deliver(client, key, _deferrals(talk.first.id, 2))

        advertisers = await client.get(
            "/api/letters/deferred?stage=advertisers", headers=bearer(token)
        )
        niche = await client.get(
            "/api/letters/deferred?stage=donors&audience=niche", headers=bearer(token)
        )

        assert advertisers.json()["total"] == 0
        assert niche.status_code == 422
        assert "Этапе 2" in niche.json()["detail"]


async def test_the_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()
    down, up = await connection.run_sync(
        columns_down_and_up, "015f15107ded_letter_deferrals.py", "messages", COLUMNS
    )

    assert (down, up) == (set(), COLUMNS)


def test_a_letter_never_deferred_has_no_deferral() -> None:
    letter = MessageModel(status=MessageStatus.SENT)

    assert (deferral_of(letter), refusal_of(letter)) == (None, None)
