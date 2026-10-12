"""Добивки лида продаж на экране «Очередь писем»: `GET /api/sales/leads/{номер}/followups`.

Под строкой письма сборки человек смотрит, какими уйдут добивки, — до отправки пачки. Правило
текста — то же, что у отправки (`followup_texts.planned`, `text_of`): собранный при сборке той
же версией цепочки или шаблон нынешней — и словами, почему такой. Добивки, письмо которых уже
есть, и добивки кончившейся цепочки не впереди. Тексты и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from backend.api.sales.queue import NOT_WRITTEN, FollowupCard, LeadFollowupsView
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.sending import Sending
from backend.features.sales import chain, chain_text
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_clean_api import _screen_fields
from tests.test_sales_followup_texts import (
    EVERY,
    FOLLOW,
    ZONES,
    Recorder,
    _build,
    _chain,
    _follow,
    _links,
)
from tests.test_sales_send import FIRST_DUE, SECOND_DUE, _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
LEADS = "/api/sales/leads"
JANE = "jane@acme.example.test"
#: У добивок одна зона переписана — 10%, ни один набор не в коридоре: шаблоном после трёх.
ONE_ZONE = {"intro": 1.0, "step2_pa": 1.0, "step3_pa": 1.0, "pa": 1.0}
OFF = "не легла в коридор отличия после 3 попыток: 10%, 10%, 10% — уйдёт текстом шаблона"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Продавец: смотреть добивки — право `sales`, отправки не нужно."""
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


def _plain(template: str) -> str:
    """Текст шаблона из слов-меток без заголовков зон — как его соберёт сборка."""
    return "\n\n".join(part.split("\n", 1)[1] for part in template.split("\n\n"))


async def _ahead(client: AsyncClient, headers: dict[str, str], lead_id: int) -> list[dict]:  # type: ignore[type-arg]
    response = await client.get(f"{LEADS}/{lead_id}/followups", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["lead_id"] == lead_id
    return list(response.json()["followups"])


async def _queued(session: AsyncSession, world: w.World, model: Recorder) -> int:
    """Письмо Джейн собрано и стоит в очереди; вернуть номер лида."""
    jane = (await w.lead(session, world.hypothesis_id, JANE)).id
    await _build(session, world, model)
    return jane


async def test_followups_of_a_queued_letter_are_the_texts_written_at_the_build(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(EVERY))
    [link] = await _links(session)
    assert link.followups is not None
    kept = link.followups["steps"]

    shown = await _ahead(client, headers, jane)

    assert shown == [
        {
            "step": step,
            "body": kept[str(step)]["body"],
            "words": "переписана при сборке — отличие 20%",
            "difference": kept[str(step)]["difference"],
            "difference_text": "20%",
            "zones": kept[str(step)]["zones"],
        }
        for step in (2, 3)
    ]


async def test_followup_off_the_corridor_is_shown_as_the_template_with_every_number(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(ONE_ZONE))

    shown = await _ahead(client, headers, jane)

    assert [(item["step"], item["words"], item["difference"]) for item in shown] == [
        (2, OFF, None),
        (3, OFF, None),
    ]
    assert shown[0]["body"] == _plain(FOLLOW)


async def test_chain_changed_after_the_build_shows_the_template_of_today_and_says_why(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Тем же правилом, что у отправки: текст чужой версии цепочки не уйдёт — экран его не
    показывает и говорит, почему уйдёт шаблон."""
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(EVERY))
    edited = _follow(*ZONES, fixed="edited")
    await _chain(session, world, second=edited)

    first, _ = await _ahead(client, headers, jane)

    assert first["body"] == _plain(edited)
    assert first["words"] == (
        "текст шаблона нынешней цепочки — цепочку правили после сборки письма"
    )
    assert first["difference"] is None


async def test_fixed_step_and_old_thread_say_why_the_template_goes(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await _chain(session, world, third=w.FOLLOW_BODY[3])
    jane = await _queued(session, world, Recorder(EVERY))
    [link] = await _links(session)
    link.followups = None  # собрано до того, как добивки стали переписываться
    await session.flush()

    second, third = await _ahead(client, headers, jane)

    assert second["words"] == "текст шаблона — письмо собрано, когда добивки ещё не переписывались"
    assert second["body"] == _plain(FOLLOW)
    assert third["words"] == "текст шаблона — зон rewrite в шаге нет"
    assert third["body"] == "A made-up last reminder about Example Test Co."


async def test_only_followups_still_to_go_are_ahead(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Добивка ушла — её текст в переписке, впереди — следующая; ушли все — впереди пусто."""
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(EVERY))
    [letter] = await session.scalars(select(MessageModel))
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)

    assert [item["step"] for item in await _ahead(client, headers, jane)] == [2, 3]
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    assert [item["step"] for item in await _ahead(client, headers, jane)] == [3]
    await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)
    assert await _ahead(client, headers, jane) == []


async def test_chain_that_ended_has_nothing_ahead(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Ответ, отписка или стоп-лист гасят срок добивки — впереди ничего, хотя писем нет."""
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(EVERY))
    [letter] = await session.scalars(select(MessageModel))
    await Sending(session, _transports(), now=w.NOW).send(letter.id)
    await session.refresh(letter)
    assert letter.next_action_at == w.NOW + timedelta(days=3)
    letter.next_action_at = None
    await session.flush()

    assert await _ahead(client, headers, jane) == []


async def test_a_step_switched_off_in_the_chain_waits_in_words(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await _chain(session, world)
    jane = await _queued(session, world, Recorder(EVERY))
    off = chain_text.step_template(step=3, language="en", body=FOLLOW, active=False)
    await chain.save(session, off, hypothesis_id=world.hypothesis_id, author="т", author_id=None)

    _, third = await _ahead(client, headers, jane)

    assert third == {
        "step": 3,
        "body": None,
        "words": "шага нет в цепочке — добивка ждёт, пока цепочку дополнят",
        "difference": None,
        "difference_text": None,
        "zones": [],
    }


async def test_lead_without_a_letter_is_refused_in_words(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    jane = (await w.lead(session, world.hypothesis_id, JANE)).id

    response = await client.get(f"{LEADS}/{jane}/followups", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (404, NOT_WRITTEN)


async def test_without_the_sales_right_the_followups_are_refused(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(f"{LEADS}/1/followups", headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (
        403,
        no_right(Permission.SALES),
    )


def test_screen_reads_the_followups_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки."""
    assert _screen_fields("SalesLeadFollowupsView") == set(LeadFollowupsView.model_fields)
    assert _screen_fields("SalesFollowupCard") == set(FollowupCard.model_fields)
