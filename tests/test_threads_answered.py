"""Переписка с ответом — диалог, без ответа — письмо (`answered`, 16.11.2026).

«Диалоги» доноров и рекламодателей показывают только переписки с ответом, а остальные — список
«Ждём ответа» на экране «Письма». Оба экрана судят по одному полю карточки, и правило у него
одно (`outreach/threads.is_answered`): ответил человек — диалог; ответа человека нет, но
переписка ждёт человека (автоответ с суммой) или он по ней уже решил (цена из такого
автоответа) — тоже диалог. Автоответ «я в отпуске», отказ доставки, отписка и цепочка,
остановленная руками, ответом не считаются.

Проверяется правилом без базы — по случаю на каждое слово брифа — и по HTTP: список отдаёт
поле, сужается этапом и ответом, а этап без права на него — отказ словами.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.outreach.threads import (
    ANSWER_STATES,
    ThreadState,
    ThreadSummary,
    summarize,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

#: Автоответ с суммой в валюте: модель его не разбирает, цену подтверждает человек.
PRICED_AUTO = "I am out of the office until Monday. Our sponsored post rate is $150."
AWAY = "I am out of the office until Monday."

HUMAN, AUTO = ReplyKind.HUMAN, ReplyKind.AUTO_REPLY
SURE = {"kind": HUMAN, "confidence": 0.95}


def _letter(status: MessageStatus) -> MessageModel:
    return MessageModel(
        campaign_id=1,
        domain_id=1,
        step=0,
        status=status,
        idempotency_key="k",
        sent_at=None if status is MessageStatus.QUEUED else NOW - timedelta(days=1),
    )


def _reply(number: int, fields: dict[str, Any]) -> ReplyModel:
    reply = ReplyModel(**{"raw_body": "Hello.", "thread_id": 1, **fields})
    reply.id = number + 1
    reply.created_at = NOW - timedelta(hours=10 - number)
    return reply


def _summary(
    stage: Stage, letters: tuple[MessageStatus, ...], *replies: dict[str, Any]
) -> ThreadSummary:
    return summarize(
        [_letter(status) for status in letters],
        [_reply(number, fields) for number, fields in enumerate(replies)],
        stage,
    )


DELIVERED = (MessageStatus.DELIVERED,)

#: Случай брифа: этап, письма, ответы — и какое состояние и какой ответ от них ждут.
CASES: dict[
    str, tuple[Stage, tuple[MessageStatus, ...], tuple[dict[str, Any], ...], ThreadState]
] = {
    "в очереди": (Stage.DONORS, (MessageStatus.QUEUED,), (), ThreadState.QUEUED),
    "ждём ответа": (Stage.DONORS, DELIVERED, (), ThreadState.WAITING),
    "только автоответ": (
        Stage.DONORS,
        DELIVERED,
        ({"kind": AUTO, "raw_body": AWAY},),
        ThreadState.WAITING,
    ),
    "отказ доставки": (
        Stage.DONORS,
        (MessageStatus.BOUNCED,),
        ({"kind": ReplyKind.BOUNCE},),
        ThreadState.BOUNCED,
    ),
    "отписка": (
        Stage.DONORS,
        DELIVERED,
        ({"kind": ReplyKind.UNSUBSCRIBE},),
        ThreadState.UNSUBSCRIBED,
    ),
    "остановлена руками": (
        Stage.DONORS,
        (MessageStatus.SENT, MessageStatus.STOPPED),
        (),
        ThreadState.STOPPED,
    ),
    "автоответ рекламодателя с суммой": (
        Stage.ADVERTISERS,
        DELIVERED,
        ({"kind": AUTO, "raw_body": PRICED_AUTO},),
        ThreadState.WAITING,
    ),
    "ответил человек": (Stage.DONORS, DELIVERED, (SURE,), ThreadState.REPLIED),
    "автоответ с суммой ждёт разбора": (
        Stage.DONORS,
        DELIVERED,
        ({"kind": AUTO, "raw_body": PRICED_AUTO},),
        ThreadState.NEEDS_REVIEW,
    ),
    "цена из автоответа подтверждена": (
        Stage.DONORS,
        DELIVERED,
        (
            {
                "kind": AUTO,
                "raw_body": PRICED_AUTO,
                "price_white": Decimal(150),
                "currency": "USD",
                "reviewed_at": NOW,
            },
        ),
        ThreadState.PRICED,
    ),
    "лид": (Stage.ADVERTISERS, DELIVERED, ({"kind": HUMAN},), ThreadState.LEAD),
    "лид в работе": (
        Stage.ADVERTISERS,
        DELIVERED,
        ({"kind": HUMAN, "reviewed_at": NOW},),
        ThreadState.LEAD_TAKEN,
    ),
    "отписка после ответа человека": (
        Stage.DONORS,
        DELIVERED,
        (SURE, {"kind": ReplyKind.UNSUBSCRIBE}),
        ThreadState.UNSUBSCRIBED,
    ),
    "отказ доставки после ответа человека": (
        Stage.DONORS,
        (MessageStatus.DELIVERED, MessageStatus.BOUNCED),
        (SURE, {"kind": ReplyKind.BOUNCE}),
        ThreadState.REPLIED,
    ),
}

ANSWERED = {
    "ответил человек",
    "автоответ с суммой ждёт разбора",
    "цена из автоответа подтверждена",
    "лид",
    "лид в работе",
    "отписка после ответа человека",
    "отказ доставки после ответа человека",
}

#: Состояния без ответа: их ставят письма и отписка, а не правила ответа этапа.
NOT_ANSWER_STATES = {
    ThreadState.QUEUED,
    ThreadState.WAITING,
    ThreadState.BOUNCED,
    ThreadState.UNSUBSCRIBED,
    ThreadState.STOPPED,
}


@pytest.mark.parametrize("name", list(CASES))
def test_answered_follows_the_brief(name: str) -> None:
    stage, letters, replies, state = CASES[name]

    summary = _summary(stage, letters, *replies)

    assert (summary.state, summary.answered) == (state, name in ANSWERED)


def test_every_state_is_decided() -> None:
    """Новое состояние — решение, диалог оно или письмо: без него тест падает, а не список
    молча прячет переписки из обоих экранов."""
    assert set(ThreadState) == ANSWER_STATES | NOT_ANSWER_STATES
    assert not ANSWER_STATES & NOT_ANSWER_STATES


# --- по HTTP -------------------------------------------------------------------------------

#: Мир для HTTP: имя переписки, этап, письма, ответы — и диалог ли она.
WORLD: tuple[
    tuple[str, Stage, tuple[MessageStatus, ...], tuple[dict[str, Any], ...], bool], ...
] = (
    ("queued", Stage.DONORS, (MessageStatus.QUEUED,), (), False),
    ("away", Stage.DONORS, DELIVERED, ({"kind": AUTO, "raw_body": AWAY},), False),
    ("bounced", Stage.DONORS, (MessageStatus.BOUNCED,), ({"kind": ReplyKind.BOUNCE},), False),
    ("unsubscribed", Stage.DONORS, DELIVERED, ({"kind": ReplyKind.UNSUBSCRIBE},), False),
    ("auto-sum", Stage.DONORS, DELIVERED, ({"kind": AUTO, "raw_body": PRICED_AUTO},), True),
    ("replied", Stage.DONORS, DELIVERED, (SURE,), True),
    ("lead", Stage.ADVERTISERS, DELIVERED, ({"kind": HUMAN},), True),
    ("offer", Stage.ADVERTISERS, DELIVERED, (), False),
)


async def _world(session: AsyncSession) -> dict[str, int]:
    campaigns = {
        stage: CampaignModel(stage=stage, name=f"Ответы {stage.value}")
        for stage in (Stage.DONORS, Stage.ADVERTISERS)
    }
    session.add_all(campaigns.values())
    ids: dict[str, int] = {}
    for name, stage, letters, replies, _ in WORLD:
        host = f"{name}.example.com"
        domain = await make_donor(session, host)
        campaign = campaigns[stage]
        thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
        session.add(thread)
        await session.flush()
        ids[name] = thread.id
        for step, status in enumerate(letters):
            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    thread_id=thread.id,
                    domain_id=domain.id,
                    step=step,
                    status=status,
                    subject="Advertising",
                    body="Good afternoon!",
                    sent_at=None if status is MessageStatus.QUEUED else NOW,
                    idempotency_key=f"answered:{name}:{step}",
                )
            )
        for number, reply in enumerate(replies):
            session.add(
                ReplyModel(
                    **{"raw_body": "Hello.", **reply},
                    thread_id=thread.id,
                    from_email=f"editor@{host}",
                    subject="Re: Advertising",
                    to_addresses=["outreach@mail.example.org"],
                    created_at=NOW + timedelta(hours=number + 1),
                )
            )
    await session.commit()
    return ids


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@example.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@example.com")


async def test_list_gives_answered_and_the_card_agrees(
    session: AsyncSession, client: AsyncClient, operator_token: str
) -> None:
    ids = await _world(session)
    headers = bearer(operator_token)

    listed = (await client.get("/api/threads", headers=headers)).json()

    expected = {ids[name]: dialog for name, _, _, _, dialog in WORLD}
    assert {row["id"]: row["answered"] for row in listed} == expected
    one = (await client.get(f"/api/threads/{ids['auto-sum']}", headers=headers)).json()
    assert one["card"]["answered"] is True


async def test_list_narrows_by_stage_and_answer(
    session: AsyncSession, client: AsyncClient, operator_token: str
) -> None:
    """«Ждём ответа» на экране «Письма» спрашивает свой этап без ответа — и только его."""
    ids = await _world(session)
    headers = bearer(operator_token)

    async def names(query: str) -> set[str]:
        response = await client.get(f"/api/threads?{query}", headers=headers)
        assert response.status_code == 200, response.text
        by_id = {number: name for name, number in ids.items()}
        return {by_id[row["id"]] for row in response.json()}

    assert await names("stage=donors&answered=false") == {
        "queued",
        "away",
        "bounced",
        "unsubscribed",
    }
    assert await names("stage=advertisers&answered=false") == {"offer"}
    assert await names("stage=donors&answered=true") == {"auto-sum", "replied"}
    assert await names("answered=true") == {"auto-sum", "replied", "lead"}
    assert await names("stage=advertisers") == {"lead", "offer"}


async def test_stage_without_its_right_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await _world(session)
    await make_user("доноры@example.com", role=UserRole.DONORS)
    headers = bearer(await sign_in("доноры@example.com"))

    refused = await client.get("/api/threads?stage=advertisers&answered=false", headers=headers)
    own = await client.get("/api/threads?stage=donors&answered=false", headers=headers)

    assert refused.status_code == 403
    assert "Рекламодатели" in refused.json()["detail"]
    assert own.status_code == 200
    assert len(own.json()) == 4
