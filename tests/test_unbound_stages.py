"""Ответы без письма на ящики продаж — только с правом «Продажи» (ревью стыков 12.10.2026).

Вкладка «Не привязаны», её число, число в «Обзоре» и ответ по номеру были открыты правом
«смотреть»: ответ лида, не привязавшийся к письму продаж, видел и тот, у кого право «Продажи»
снято (решение Anthony 10.10.2026, П2). Этап такого ответа — по нашему адресу, на который он
пришёл (`replies/recipient_stage.py`): ящик или поддомен ответов домена рассылки. Нашего адреса
нет — этапа нет, ответ виден всем, как был.

Каждое правило — с двух сторон: без права ответ продаж скрыт или отказан словами; с правом —
как было. Ответ на ящик доноров и ответ без наших адресов видны обоим.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from backend.features.core.domain import ReplyKind, SenderStatus, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import ReplyModel, SenderModel, SendingDomainModel
from backend.features.replies import recipient_stage, unbound
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

LIST = "/api/replies/unbound"
CLOSED = "Ответы в переписках продаж — только с правом «Продажи»"


@dataclass(frozen=True, slots=True)
class Replies:
    """Номера ответов без письма: на ящик доноров, на поддомен ответов продаж, без наших адресов."""

    donors: int
    sales: int
    nowhere: int


async def _box(session: AsyncSession, email: str, stage: Stage) -> None:
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


async def _reply(session: AsyncSession, *addresses: str, reason: str | None = None) -> int:
    reply = ReplyModel(
        kind=ReplyKind.HUMAN,
        raw_body="Hello, what is this about?",
        from_email="someone@site.example.test",
        to_addresses=list(addresses) or None,
        unbound_reason=reason,
    )
    session.add(reply)
    await session.flush()
    return reply.id


@pytest.fixture
async def replies(session: AsyncSession) -> Replies:
    await _box(session, "anna@mail-donors.example.test", Stage.DONORS)
    await _box(session, "hello@mail-sales.example.test", Stage.SALES)
    donors = await _reply(session, "anna@mail-donors.example.test")
    sales = await _reply(
        session, "hello+m7.0123456789@replies.mail-sales.example.test", "cc@site.example.test"
    )
    nowhere = await _reply(session)
    await session.commit()
    return Replies(donors=donors, sales=sales, nowhere=nowhere)


@pytest.fixture
async def outsider(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор, у которого право «Продажи» снято поимённо."""
    await make_user("outsider@team.example.test", permissions={"sales": False})
    return bearer(await sign_in("outsider@team.example.test"))


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор: право «Продажи» у него в роли."""
    await make_user("seller@team.example.test")
    return bearer(await sign_in("seller@team.example.test"))


async def _listed(client: AsyncClient, headers: dict[str, str], query: str = "") -> dict:
    response = await client.get(f"{LIST}{query}", headers=headers)
    assert response.status_code == 200, response.text
    answer: dict = response.json()
    return answer


async def test_the_list_leaves_sales_replies_out_without_the_right(
    client: AsyncClient, replies: Replies, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    hidden = await _listed(client, outsider)
    shown = await _listed(client, seller)

    assert [row["id"] for row in hidden["rows"]] == [replies.nowhere, replies.donors]
    assert hidden["total"] == 2
    assert [row["id"] for row in shown["rows"]] == [replies.nowhere, replies.sales, replies.donors]
    assert shown["total"] == 3


async def test_hidden_replies_take_no_place_on_a_page(
    client: AsyncClient, replies: Replies, outsider: dict[str, str]
) -> None:
    """Страница — из видимых: скрытый ответ не оставляет на ней дыры и не съедает место."""
    pages = [await _listed(client, outsider, f"?limit=1&page={page}") for page in (1, 2, 3)]

    assert [[row["id"] for row in one["rows"]] for one in pages] == [
        [replies.nowhere],
        [replies.donors],
        [],
    ]


async def test_the_overview_counts_sales_replies_only_with_the_right(
    client: AsyncClient, replies: Replies, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    hidden = (await client.get("/api/overview", headers=outsider)).json()
    shown = (await client.get("/api/overview", headers=seller)).json()

    assert (hidden["unbound_replies"], shown["unbound_replies"]) == (2, 3)


async def test_a_sales_reply_by_number_is_refused_in_words_without_the_right(
    client: AsyncClient, replies: Replies, outsider: dict[str, str], seller: dict[str, str]
) -> None:
    """Ответ по номеру — его вложение: без права — отказ словами до маршрута, с правом — «не
    найдено» самого маршрута (вложения 999999 нет)."""
    path = f"/api/replies/{replies.sales}/attachments/999999"

    refused = await client.get(path, headers=outsider)
    allowed = await client.get(path, headers=seller)

    assert (refused.status_code, refused.json()["detail"]) == (403, CLOSED)
    assert allowed.status_code == 404, allowed.text


@pytest.mark.parametrize("which", ["donors", "nowhere"])
async def test_other_replies_by_number_stay_open_without_the_right(
    client: AsyncClient, replies: Replies, outsider: dict[str, str], which: str
) -> None:
    number = getattr(replies, which)

    response = await client.get(f"/api/replies/{number}/attachments/999999", headers=outsider)

    assert response.status_code == 404, response.text


# --- кого искать: слово причины — по направлению ответа (11.11.2026) -------------------------

BY_HAND = "ищут по отправителю и тексту."


async def test_the_reason_names_whom_to_look_for_by_the_box_of_the_reply(
    client: AsyncClient, session: AsyncSession, seller: dict[str, str]
) -> None:
    """«Донора ищут…» стояло и у ответа на ящик продаж, где доноров нет. Направление — по
    нашему адресу ответа, тем же правилом, каким список прячет чужой этап; нашего адреса
    нет — прежнее слово. Метка строки от направления не зависит."""
    await _box(session, "anna@mail-donors.example.test", Stage.DONORS)
    await _box(session, "offers@mail-offers.example.test", Stage.ADVERTISERS)
    await _box(session, "hello@mail-sales.example.test", Stage.SALES)
    whom = {
        await _reply(session, "anna@mail-donors.example.test", reason="no_label"): "Донора",
        await _reply(session, "offers@mail-offers.example.test", reason="no_label"): (
            "Рекламодателя"
        ),
        await _reply(
            session, "cc@site.example.test", "hello@mail-sales.example.test", reason="no_label"
        ): "Лида",
        await _reply(session, "team@site.example.test", reason="no_label"): "Донора",
        await _reply(session, reason="no_label"): "Донора",
    }
    await session.commit()

    rows = (await _listed(client, seller))["rows"]

    assert {row["id"]: row["reason_text"].rsplit(". ", 1)[1] for row in rows} == {
        number: f"{word} {BY_HAND}" for number, word in whom.items()
    }
    assert {row["reason_short"] for row in rows} == {"написано заново, не ответом на наше письмо"}


async def test_directions_take_one_look_at_the_domains_per_page(session: AsyncSession) -> None:
    """Направление ответов страницы — одним вопросом о доменах (ящики и строки доменов), а
    не вопросом на строку; пустой странице он не нужен вовсе."""
    await _box(session, "anna@mail-donors.example.test", Stage.DONORS)
    await _box(session, "hello@mail-sales.example.test", Stage.SALES)
    sales = [await _reply(session, "hello@mail-sales.example.test") for _ in range(3)]
    donors = [await _reply(session, "anna@mail-donors.example.test") for _ in range(3)]
    nowhere = await _reply(session)
    rows = await unbound.page(session, stages=frozenset(Stage))
    sent: list[str] = []

    def spy(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        sent.append(statement)

    target = session.bind.sync_connection  # type: ignore[union-attr]
    event.listen(target, "before_cursor_execute", spy)
    try:
        found = await unbound.stages_of(session, rows)
        nothing = await unbound.stages_of(session, [])
    finally:
        event.remove(target, "before_cursor_execute", spy)

    assert found == {
        **dict.fromkeys(sales, Stage.SALES),
        **dict.fromkeys(donors, Stage.DONORS),
        nowhere: None,
    }
    assert nothing == {}
    assert len(sent) == 2, sent
    assert sum("FROM senders" in statement for statement in sent) == 1
    assert sum("FROM sending_domains" in statement for statement in sent) == 1


# --- правило этапа по адресу ---------------------------------------------------------------


async def test_the_domain_row_decides_before_the_boxes(session: AsyncSession) -> None:
    """У строки домена рассылки — направление домена целиком: ящик другого этапа на нём его
    не меняет."""
    await _box(session, "anna@mixed.example.test", Stage.DONORS)
    session.add(SendingDomainModel(domain="mixed.example.test", stage=Stage.SALES, daily_limit=5))
    await session.flush()

    domains = await recipient_stage.known_domains(session)

    assert recipient_stage.stage_of(["anna+m1.0123456789@replies.mixed.example.test"], domains) == (
        Stage.SALES
    )


@pytest.mark.parametrize(
    ("addresses", "stage"),
    [
        (["x@elsewhere.example.test", "anna@MAIL-A.example.test"], Stage.DONORS),
        (["hello@mail-b.example.test", "anna@mail-a.example.test"], Stage.SALES),
        (["anna@notmail-a.example.test"], None),
        ([], None),
        (None, None),
    ],
    ids=["первый-наш-после-чужого", "первый-из-наших", "похожий-домен", "пусто", "не-записаны"],
)
def test_the_first_of_our_addresses_decides(
    addresses: list[str] | None, stage: Stage | None
) -> None:
    domains = {"mail-a.example.test": Stage.DONORS, "mail-b.example.test": Stage.SALES}

    assert recipient_stage.stage_of(addresses, domains) == stage
