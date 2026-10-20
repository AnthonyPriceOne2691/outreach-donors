"""Период добавления лида на вкладке «emails» продаж: `GET /sales/leads?added_since=…&added_until=…`.

Экран переводит дни человека в моменты — полночью первого дня и полночью после последнего по
часам браузера, как свои даты воронки; сервер берёт полуинтервал `[начало, конец)` по моменту,
когда заведена строка лида (`created_at`). Здесь — что граница дня держится в любом поясе (и в
том виде, в каком момент шлёт браузер), что период сочетается с остальными фильтрами, что пустой
период — пустая таблица с настоящей сводкой, а негодная граница — отказ словами.

Гипотезы — разных ниш, имена и компании — разными письменностями: кириллица, латиница, греческий,
японский. Люди, компании и домены выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
LEADS = "/api/sales/leads"

#: Пояса человека — с летним временем и без, к востоку и к западу от Гринвича, со сдвигом не в
#: целый час: сутки у каждого свои, а сервер сравнивает моменты.
ZONES = ("Europe/Moscow", "America/Mexico_City", "Asia/Kathmandu", "Europe/Berlin")

#: Период экрана: с 1 по 15 октября включительно.
FIRST, LAST = (2026, 10, 1), (2026, 10, 15)
#: Миг до полуночи: последняя микросекунда суток.
TICK = timedelta(microseconds=1)


def midnight(zone: ZoneInfo, day: tuple[int, int, int], plus: int = 0) -> datetime:
    """Полночь дня `day` (и через `plus` суток) по часам пояса — как считает её браузер."""
    return datetime(*day, tzinfo=zone) + timedelta(days=plus)


async def _headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _lead(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    email: str,
    at: datetime,
    *,
    name: str | None = None,
    company: str | None = None,
    status: LeadStatus = LeadStatus.NEW,
    reason: RejectionReason | None = None,
) -> SalesLeadModel:
    """Лид, заведённый в момент `at`; домен компании — домен адреса, один на всех его лидов."""
    host = email.rpartition("@")[2]
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=email,
        name=name,
        company=company,
        source=LeadSource.IMPORT,
        status=LeadStatus.REJECTED if reason is not None else status,
        rejection_reason=None if reason is None else reason.value,
        created_at=at,
    )
    session.add(lead)
    await session.flush()
    return lead


@dataclass(frozen=True, slots=True)
class Niches:
    """Три гипотезы разных ниш."""

    dental: SalesHypothesisModel
    saas: SalesHypothesisModel
    wine: SalesHypothesisModel


@pytest.fixture
async def niches(session: AsyncSession) -> Niches:
    dental = SalesHypothesisModel(name="стоматологии · DE")
    saas = SalesHypothesisModel(name="SaaS for logistics · US")
    wine = SalesHypothesisModel(name="οινοποιεία · GR")
    session.add_all([dental, saas, wine])
    await session.flush()
    return Niches(dental, saas, wine)


def _emails(body: dict[str, Any]) -> list[str]:
    return [row["email"] for row in body["rows"]]


@pytest.mark.parametrize("zone_name", ZONES)
async def test_both_days_are_whole_in_the_persons_zone(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    niches: Niches,
    zone_name: str,
) -> None:
    """С полуночи первого дня до полуночи после последнего — по часам человека: миг до начала и
    полночь после конца не входят, первый миг первого дня и последний миг последнего — входят."""
    zone = ZoneInfo(zone_name)
    since, until = midnight(zone, FIRST), midnight(zone, LAST, plus=1)
    moments = {
        "before@zahnarzt.example.test": since - TICK,
        "first@zahnarzt.example.test": since,
        "middle@route-ai.example.test": midnight(zone, (2026, 10, 8)) + timedelta(hours=12),
        "last@krasi.example.test": until - TICK,
        "after@krasi.example.test": until,
    }
    owners = [niches.dental, niches.dental, niches.saas, niches.wine, niches.wine]
    for (email, at), owner in zip(moments.items(), owners, strict=True):
        await _lead(session, owner, email, at)
    await session.commit()

    response = await client.get(
        LEADS,
        params={"added_since": since.isoformat(), "added_until": until.isoformat()},
        headers=await _headers(make_user, sign_in),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert _emails(body) == [
        "last@krasi.example.test",
        "middle@route-ai.example.test",
        "first@zahnarzt.example.test",
    ]
    assert body["total"] == 3
    # Колонка «Дата добавления» читает тот же момент: строка заведена в первый миг периода.
    first = body["rows"][-1]
    assert datetime.fromisoformat(first["created_at"]) == since


async def test_moments_come_as_the_browser_writes_them(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    niches: Niches,
) -> None:
    """Браузер шлёт полночь своего пояса в UTC с миллисекундами (`toISOString`): сутки по Москве
    с 1 по 15 октября — это `2026-09-30T21:00:00.000Z` и `2026-10-15T21:00:00.000Z`."""
    zone = ZoneInfo("Europe/Moscow")
    await _lead(
        session, niches.dental, "late@zahnarzt.example.test", midnight(zone, LAST, 1) - TICK
    )
    await _lead(session, niches.dental, "next@zahnarzt.example.test", midnight(zone, LAST, 1))
    await session.commit()

    response = await client.get(
        LEADS,
        params={
            "added_since": "2026-09-30T21:00:00.000Z",
            "added_until": "2026-10-15T21:00:00.000Z",
        },
        headers=await _headers(make_user, sign_in),
    )

    assert response.status_code == 200, response.text
    assert _emails(response.json()) == ["late@zahnarzt.example.test"]


async def test_one_bound_narrows_from_its_side_only(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    niches: Niches,
) -> None:
    """Задано только «с» — всё с того дня; только «по» — всё до конца того дня."""
    zone = ZoneInfo("Asia/Kathmandu")
    await _lead(session, niches.saas, "early@route-ai.example.test", midnight(zone, (2026, 9, 3)))
    await _lead(session, niches.saas, "late@route-ai.example.test", midnight(zone, (2026, 10, 20)))
    await session.commit()
    headers = await _headers(make_user, sign_in)
    since = midnight(zone, FIRST).isoformat()
    until = midnight(zone, LAST, plus=1).isoformat()

    from_side = await client.get(LEADS, params={"added_since": since}, headers=headers)
    to_side = await client.get(LEADS, params={"added_until": until}, headers=headers)

    assert _emails(from_side.json()) == ["late@route-ai.example.test"]
    assert _emails(to_side.json()) == ["early@route-ai.example.test"]


async def test_empty_period_is_an_empty_table_with_the_whole_summary(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    niches: Niches,
) -> None:
    """Никого не добавили за период — пустая страница и `total` ноль; сводка — по всем лидам:
    по ней экран говорит «всего лидов — N» под пустой таблицей."""
    zone = ZoneInfo("Europe/Berlin")
    await _lead(session, niches.wine, "nikos@krasi.example.test", midnight(zone, (2026, 9, 2)))
    await _lead(
        session,
        niches.dental,
        "twin@zahnarzt.example.test",
        midnight(zone, (2026, 9, 2)),
        reason=RejectionReason.DUPLICATE,
    )
    await session.commit()

    response = await client.get(
        LEADS,
        params={
            "added_since": midnight(zone, FIRST).isoformat(),
            "added_until": midnight(zone, LAST, plus=1).isoformat(),
        },
        headers=await _headers(make_user, sign_in),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["rows"], body["total"]) == ([], 0)
    assert body["states"] == {"new": 1, "ready": 0, "rejected": 1}
    assert body["reasons"]["duplicate"] == 1


async def test_period_combines_with_every_other_filter(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    niches: Niches,
) -> None:
    """Период — ещё одно «и»: с состоянием, причиной, гипотезой и поиском любой письменностью."""
    zone = ZoneInfo("Europe/Moscow")
    inside = midnight(zone, (2026, 10, 7)) + timedelta(hours=9)
    outside = midnight(zone, (2026, 9, 20))
    leads = [
        ("anna@zahnarzt.example.test", niches.dental, inside, "Анна Смирнова", "Зубная клиника"),
        (
            "old.anna@zahnarzt.example.test",
            niches.dental,
            outside,
            "Анна Смирнова",
            "Зубная клиника",
        ),
        ("ops@route-ai.example.test", niches.saas, inside, "Jane Doe", "Route AI"),
        ("nikos@krasi.example.test", niches.wine, inside, "Νίκος Παπαδόπουλος", "Κτήμα Άλφα"),
        ("yamada@sake.example.test", niches.wine, inside, "山田太郎", "山田酒造"),
        ("old.yamada@sake.example.test", niches.wine, outside, "山田太郎", "山田酒造"),
    ]
    for email, owner, at, name, company in leads:
        await _lead(session, owner, email, at, name=name, company=company)
    await _lead(
        session, niches.saas, "dup@route-ai.example.test", inside, reason=RejectionReason.DUPLICATE
    )
    await _lead(
        session,
        niches.saas,
        "old.dup@route-ai.example.test",
        outside,
        reason=RejectionReason.DUPLICATE,
    )
    await _lead(
        session, niches.saas, "ready@route-ai.example.test", inside, status=LeadStatus.READY
    )
    await session.commit()
    headers = await _headers(make_user, sign_in)
    period = {
        "added_since": midnight(zone, FIRST).isoformat(),
        "added_until": midnight(zone, LAST, plus=1).isoformat(),
    }

    async def found(**more: str | int) -> list[str]:
        response = await client.get(LEADS, params={**period, **more}, headers=headers)
        assert response.status_code == 200, response.text
        return _emails(response.json())

    assert await found(search="анна") == ["anna@zahnarzt.example.test"]
    assert await found(search="Νίκος") == ["nikos@krasi.example.test"]
    assert await found(search="酒造") == ["yamada@sake.example.test"]
    assert await found(state="rejected", reason="duplicate") == ["dup@route-ai.example.test"]
    assert await found(state="ready") == ["ready@route-ai.example.test"]
    assert await found(hypothesis=niches.wine.id) == [
        "yamada@sake.example.test",
        "nikos@krasi.example.test",
    ]
    assert await found(hypothesis=niches.saas.id, state="new") == ["ops@route-ai.example.test"]
    # Без периода те же условия находят и старых — сужает именно он.
    response = await client.get(LEADS, params={"search": "酒造"}, headers=headers)
    assert _emails(response.json()) == ["old.yamada@sake.example.test", "yamada@sake.example.test"]


@pytest.mark.parametrize(
    ("params", "said"),
    [
        (
            {"added_since": "вчера"},
            "начало периода добавления (added_since) — не дата: нужен момент с поясом",
        ),
        (
            {"added_until": "2026-02-30T00:00:00+03:00"},
            "конец периода добавления (added_until) — не дата: нужен момент с поясом",
        ),
        (
            {"added_since": "1e3"},
            "начало периода добавления (added_since) — не дата: нужен момент с поясом",
        ),
        (
            {"added_since": "2026-10-01"},
            "начало периода добавления (added_since) — без пояса: нужен момент с поясом",
        ),
        (
            {"added_until": "2026-10-16T00:00:00"},
            "конец периода добавления (added_until) — без пояса: нужен момент с поясом",
        ),
    ],
)
async def test_a_bound_that_is_not_a_moment_is_refused_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, params: dict[str, str], said: str
) -> None:
    """Экран шлёт моменты сам; руками адрес API правят — и отказ говорит, какая граница и что
    прислать, а не «значение не подходит»."""
    response = await client.get(LEADS, params=params, headers=await _headers(make_user, sign_in))

    assert response.status_code == 422, response.text
    message = response.json()["detail"][0]["msg"]
    assert message.startswith(said), message
    assert message.endswith("например 2026-10-01T00:00:00+03:00"), message


@pytest.mark.parametrize("gap", [timedelta(0), timedelta(days=-1)])
async def test_a_period_that_ends_before_it_starts_is_refused_with_the_funnels_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, gap: timedelta
) -> None:
    """Конец не позже начала — период пуст по определению: 422 теми же словами, что у воронки."""
    since = datetime(2026, 10, 15, tzinfo=UTC)

    response = await client.get(
        LEADS,
        params={"added_since": since.isoformat(), "added_until": (since + gap).isoformat()},
        headers=await _headers(make_user, sign_in),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["msg"] == (
        "начало периода не раньше его конца — поменяйте даты местами"
    )
