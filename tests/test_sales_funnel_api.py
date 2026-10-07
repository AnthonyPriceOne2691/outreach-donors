"""Воронка продаж через API — срез 5.4, T2: A4 Spec 5.4 — числа экрана сходятся с базой.

Сверка — запросом к базе в самом тесте: письма, ответы и передачи читаются сырым SQL, а
шаги считаются здесь же, циклом, по словам Spec — другим путём, чем считает сервер. Права —
`sales`; отказы словами. Адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from backend.api.sales.funnel import EMPTY_PERIOD, FunnelCounts, FunnelRow, SalesFunnelView
from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.access import UserModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_funnel_rows import NOW, Rows

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
FUNNEL = "/api/sales/funnel"
NO_RIGHT = "Действие «sales» недоступно этой учётке"
STEPS = ("queued", "sent", "delivered", "bounced", "answered", "handed_off")
TYPES = (Path(__file__).resolve().parent.parent / "frontend/src/api/salesTypes.ts").read_text(
    encoding="utf-8"
)

#: Письма цепочки лидов продаж — без наших ответов в переписке.
LETTERS = text(
    """
    SELECT st.lead_id, l.hypothesis_id, m.status::text, coalesce(m.sent_at, m.created_at),
           m.created_at
    FROM sales_threads st
    JOIN sales_leads l ON l.id = st.lead_id
    JOIN messages m ON m.thread_id = st.thread_id
    WHERE m.answers_reply_id IS NULL
    """
)
#: Лиды, ответившие сами или попросившие не писать.
ANSWERED = text(
    """
    SELECT DISTINCT st.lead_id
    FROM sales_threads st JOIN replies r ON r.thread_id = st.thread_id
    WHERE r.kind::text IN ('human', 'unsubscribe')
    """
)
HANDED_OFF = text("SELECT DISTINCT lead_id FROM sales_handoffs")


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _world(session: AsyncSession) -> tuple[int, int, int]:
    """Две гипотезы с лидами на всех шагах, третья пустая, и письмо доноров рядом."""
    rows = Rows(session)
    first = await rows.hypothesis("свои сайты")
    waiting = await rows.lead(first, "wait@one.example.test")
    await rows.letter(waiting, step=0, status=MessageStatus.QUEUED, days_ago=1)
    warm = await rows.lead(first, "warm@two.example.test")
    for step, days in enumerate((11, 8, 3)):
        await rows.letter(warm, step=step, status=MessageStatus.DELIVERED, days_ago=days)
    answer = await rows.reply(warm, ReplyKind.HUMAN, days_ago=2)
    await rows.hand_off(warm)
    await rows.letter(warm, step=0, status=MessageStatus.BOUNCED, days_ago=1, answers=answer)
    dead = await rows.lead(first, "dead@three.example.test")
    await rows.letter(dead, step=0, status=MessageStatus.BOUNCED, days_ago=6)
    away = await rows.lead(first, "away@four.example.test")
    await rows.letter(away, step=0, status=MessageStatus.SENT, days_ago=2)
    await rows.reply(away, ReplyKind.AUTO_REPLY, days_ago=1)
    stop = await rows.lead(first, "stop@five.example.test")
    await rows.letter(stop, step=0, status=MessageStatus.DELIVERED, days_ago=5)
    await rows.reply(stop, ReplyKind.UNSUBSCRIBE, days_ago=4)
    late = await rows.lead(first, "late@six.example.test")
    await rows.letter(late, step=0, status=MessageStatus.DELIVERED, days_ago=9)
    await rows.letter(late, step=1, status=MessageStatus.BOUNCED, days_ago=6)
    second = await rows.hypothesis("англоязычные")
    old = await rows.lead(second, "old@seven.example.test")
    await rows.letter(old, step=0, status=MessageStatus.DELIVERED, days_ago=23)
    await rows.reply(old, ReplyKind.HUMAN, days_ago=1)
    fresh = await rows.lead(second, "fresh@eight.example.test")
    await rows.letter(fresh, step=0, status=MessageStatus.QUEUED, days_ago=17)
    talk = await rows.lead(second, "talk@nine.example.test")
    await rows.letter(talk, step=0, status=MessageStatus.DELIVERED, days_ago=4)
    await rows.reply(talk, ReplyKind.HUMAN, days_ago=3)
    await rows.hand_off(talk)
    empty = await rows.hypothesis("пустая")
    await rows.donor_letter("donor@ten.example.test")
    await session.commit()
    return first, second, empty


async def _expected(
    session: AsyncSession, hypothesis: int | None, since: datetime | None, until: datetime | None
) -> dict[int, dict[str, int]]:
    """Шаги по гипотезам — из сырых строк базы, по словам Spec, циклом."""
    letters: dict[int, list[tuple[str, datetime, datetime]]] = {}
    hypotheses: dict[int, int] = {}
    for lead, of, status, moment, written in (await session.execute(LETTERS)).tuples():
        letters.setdefault(lead, []).append((status, moment, written))
        hypotheses[lead] = of
    answered = set((await session.scalars(ANSWERED)).all())
    handed = set((await session.scalars(HANDED_OFF)).all())

    def inside(moment: datetime) -> bool:
        return (since is None or moment >= since) and (until is None or moment < until)

    found: dict[int, Counter[str]] = {}
    for lead, path in letters.items():
        if hypothesis is not None and hypotheses[lead] != hypothesis:
            continue
        counter = found.setdefault(hypotheses[lead], Counter())
        statuses = {status for status, _, _ in path}
        gone = [moment for status, moment, _ in path if status in {"sent", "delivered", "bounced"}]
        if not gone:
            queued = [written for status, _, written in path if status == "queued"]
            counter["queued"] += bool(queued) and inside(min(queued))
            continue
        if not inside(min(gone)):
            continue
        counter["sent"] += 1
        counter["bounced"] += "bounced" in statuses
        counter["delivered"] += "delivered" in statuses and "bounced" not in statuses
        counter["answered"] += lead in answered
        counter["handed_off"] += lead in handed
    return {of: {step: int(counter[step]) for step in STEPS} for of, counter in found.items()}


def _zero() -> dict[str, int]:
    return dict.fromkeys(STEPS, 0)


@pytest.mark.parametrize("narrow", [False, True])
@pytest.mark.parametrize(
    ("since_days", "until_days"),
    [(None, None), (14, None), (10, 2), (None, 7)],
)
async def test_a4_screen_numbers_match_a_query_to_the_base(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    narrow: bool,
    since_days: int | None,
    until_days: int | None,
) -> None:
    # A4: числа экрана — запросом к базе в тесте
    first, second, empty = await _world(session)
    since = None if since_days is None else NOW - timedelta(days=since_days)
    until = None if until_days is None else NOW - timedelta(days=until_days)
    params: dict[str, Any] = {}
    if narrow:
        params["hypothesis"] = first
    if since is not None:
        params["since"] = since.isoformat()
    if until is not None:
        params["until"] = until.isoformat()

    response = await client.get(FUNNEL, params=params, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    expected = await _expected(session, first if narrow else None, since, until)
    shown = {row["hypothesis_id"]: row["counts"] for row in body["rows"]}
    wanted = [first] if narrow else [first, second, empty]
    assert list(shown) == wanted
    assert shown == {of: expected.get(of, _zero()) for of in wanted}
    assert body["total"] == {step: sum(row[step] for row in shown.values()) for step in STEPS}
    # Сверка не пустая: у первой гипотезы за всё время заняты все шаги.
    if (narrow, since, until) == (False, None, None):
        assert all(shown[first][step] > 0 for step in STEPS)


async def test_a4_numbers_of_the_whole_period_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Те же числа — явно, чтобы ошибка сверки не пряталась в общем правиле теста."""
    # A4: те же числа явно
    first, second, _ = await _world(session)

    body = (await client.get(FUNNEL, headers=headers)).json()

    rows = {row["hypothesis_id"]: row["counts"] for row in body["rows"]}
    assert rows[first] == {
        "queued": 1,
        "sent": 5,
        "delivered": 2,
        "bounced": 2,
        "answered": 2,
        "handed_off": 1,
    }
    assert rows[second] == {
        "queued": 1,
        "sent": 2,
        "delivered": 2,
        "bounced": 0,
        "answered": 2,
        "handed_off": 1,
    }
    assert body["total"]["sent"] == 7


async def test_period_and_hypothesis_come_back_as_asked(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    first, _, _ = await _world(session)
    since = NOW - timedelta(days=14)

    response = await client.get(
        FUNNEL, params={"hypothesis": first, "since": since.isoformat()}, headers=headers
    )

    body = response.json()
    assert (body["hypothesis_id"], body["until"]) == (first, None)
    assert datetime.fromisoformat(body["since"]) == since


async def test_empty_period_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    moment = NOW.isoformat()

    response = await client.get(FUNNEL, params={"since": moment, "until": moment}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (422, EMPTY_PERIOD)


async def test_moment_without_a_time_zone_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(FUNNEL, params={"since": "2026-10-01T00:00:00"}, headers=headers)

    assert response.status_code == 422


async def test_unknown_hypothesis_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(FUNNEL, params={"hypothesis": 917}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "гипотезы №917 нет — обновите список гипотез",
    )


async def test_without_the_sales_right_the_funnel_refuses_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(FUNNEL, headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст."""
    found = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", TYPES, re.DOTALL)
    assert found is not None, f"в salesTypes.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_screen_reads_the_funnel_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки."""
    assert _screen_fields("SalesFunnelView") == set(SalesFunnelView.model_fields)
    assert _screen_fields("FunnelRow") == set(FunnelRow.model_fields)
    assert _screen_fields("FunnelCounts") == set(FunnelCounts.model_fields) == set(STEPS)


async def test_funnel_route_is_this_one(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(FUNNEL)
        for method in methods
    }

    assert in_app == {("GET", FUNNEL)}
