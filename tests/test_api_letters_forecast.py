"""Прогноз рассылки по HTTP (`GET /api/letters/forecast`) — экран «Рассылка».

Кто пускается — как у очереди писем: смотреть может каждый с доступом к базе, этап продаж — ещё с
правом «Продажи» (П2): без него 403 словами, а прогноз доноров открыт. Что видно — на базе: доноры
без окна получателя (пишут каждый день, вручную — срок без часов), продажи с подставным модулем
продаж (окно пн–пт, автоотправка с паузой, запас сборки и пояса очереди — ответы моста). Часы
прогноза стоят на выдуманной среде; ящики, домены и адреса — выдуманные.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

import pytest
from backend.features.core import stage_forecast, stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.core.stages import MailPolicy
from backend.features.core.window import Pace, SendWindow
from backend.features.letters import forecast as forecast_module
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_stage_bridge import FakeSalesMail, unregistered

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Среда, 10:00 UTC: окно в Берлине (летнее время) — 07:00–15:00 UTC.
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
TODAY = NOW.date()
URL = "/api/letters/forecast"
#: Почему письмо очереди не уйдёт, пока его не поправят, — выдуманными словами подставного модуля.
HELD = "выдуманная причина: адрес лида ждёт проверки"

__all__ = ["unregistered"]  # подставной модуль продаж — фикстурой соседнего набора


class _Frozen(datetime):
    """Часы прогноза на выдуманной среде: дни недели и разгон в ответе не плывут."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return NOW if tz is not None else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forecast_module, "datetime", _Frozen)


@dataclass
class ForecastSales(FakeSalesMail):
    """Подставной модуль продаж, который отвечает и прогнозу (`stage_forecast.SalesForecast`)."""

    extra: int = 40
    queued_in: Mapping[str | None, int] | None = None
    stuck_in: Mapping[str, int] | None = None

    async def supply(self, _session: AsyncSession) -> int:
        self.asked.append("supply")
        return self.extra

    async def queue_zones(self, _session: AsyncSession) -> Mapping[str | None, int]:
        self.asked.append("queue_zones")
        return self.queued_in or {}

    async def queue_stuck(self, _session: AsyncSession) -> Mapping[str, int]:
        self.asked.append("queue_stuck")
        return self.stuck_in or {}


@pytest.fixture
def sales_module(unregistered: None, monkeypatch: pytest.MonkeyPatch) -> ForecastSales:
    """Продажи подключены подставным модулем — и к почте, и к прогнозу (`register_forecast`): окно
    пн–пт 09–17, автоотправка с паузой 15 минут."""
    found = ForecastSales(
        rules=MailPolicy(
            window=SendWindow(days=frozenset(range(5)), start=time(9), end=time(17)),
            pace=Pace(gap=timedelta(minutes=15), spread=0.5),
        ),
        queued_in={"Europe/Berlin": 3, "America/New_York": 1, None: 2},
        stuck_in={HELD: 1},
    )
    stages.register_sales(lambda: found)
    monkeypatch.setattr(stage_forecast._ANSWERS, "load", lambda: found)
    return found


async def _box(
    session: AsyncSession, email: str, stage: Stage, *, wrote: int | None = 40, enabled: bool = True
) -> SenderModel:
    """Ящик этапа: `wrote` — сколько дней назад ушло первое письмо разгона."""
    box = SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=stage,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=enabled,
        warmup_started_at=NOW - timedelta(days=100),
        warmup_first_sent_at=None if wrote is None else NOW - timedelta(days=wrote),
    )
    session.add(box)
    await session.flush()
    return box


@dataclass
class _Letters:
    """Письма рассылки этапа на выдуманных доменах."""

    session: AsyncSession
    campaign: CampaignModel
    made: int = 0

    async def add(self, **fields: Any) -> MessageModel:
        self.made += 1
        domain = DomainModel(host=f"site-{self.campaign.stage.value}-{self.made}.example.test")
        self.session.add(domain)
        await self.session.flush()
        letter = MessageModel(
            campaign_id=self.campaign.id,
            domain_id=domain.id,
            contact_id=None,
            idempotency_key=f"forecast:{self.campaign.stage.value}:{self.made}",
            subject="A made-up subject",
            body="A made-up body.",
            **{"step": 0, "status": MessageStatus.QUEUED, **fields},
        )
        self.session.add(letter)
        await self.session.flush()
        return letter


async def _letters(session: AsyncSession, stage: Stage) -> _Letters:
    campaign = CampaignModel(name=f"Прогноз {stage.value}", stage=stage, status="draft")
    session.add(campaign)
    await session.flush()
    return _Letters(session, campaign)


@pytest.fixture
async def donors(session: AsyncSession) -> SenderModel:
    """Доноры: ящик с законченным разгоном, ящик на втором дне разгона и выключенный; 30 писем в
    очереди, две добивки в сроке, ушедшие письма вчера, позавчера и сегодня."""
    finished = await _box(session, "outreach1@mail-d.example.test", Stage.DONORS)
    young = await _box(session, "outreach2@mail-e.example.test", Stage.DONORS, wrote=1)
    await _box(session, "outreach3@mail-e.example.test", Stage.DONORS, enabled=False)
    letters = await _letters(session, Stage.DONORS)
    for _ in range(30):
        await letters.add()
    sent = MessageStatus.SENT
    await letters.add(
        status=sent, sent_at=NOW - timedelta(days=9), next_action_at=NOW - timedelta(days=2)
    )
    await letters.add(
        status=sent, sent_at=NOW - timedelta(days=9), next_action_at=NOW + timedelta(days=1)
    )
    # Ответ погасил срок — добивки нет; отказ доставки и последний шаг цепочки — тоже не в счёт.
    await letters.add(status=sent, sent_at=NOW - timedelta(days=9), next_action_at=None)
    await letters.add(
        status=MessageStatus.BOUNCED, sent_at=NOW - timedelta(days=9), next_action_at=NOW
    )
    await letters.add(step=2, status=sent, sent_at=NOW - timedelta(days=9), next_action_at=NOW)
    for _ in range(3):
        await letters.add(status=sent, sent_at=NOW - timedelta(days=2), sender_id=finished.id)
    await letters.add(step=1, status=sent, sent_at=NOW - timedelta(days=1), sender_id=finished.id)
    for _ in range(2):
        await letters.add(status=sent, sent_at=NOW - timedelta(hours=1), sender_id=finished.id)
    await session.commit()
    return young


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор: смотреть может, право «Продажи» у него в роли."""
    await make_user("viewer@team.example.com", role=UserRole.OPERATOR)
    return bearer(await sign_in("viewer@team.example.com"))


@pytest.fixture
async def outsider(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор, у которого право «Продажи» снято поимённо."""
    await make_user("outsider@team.example.com", permissions={"sales": False})
    return bearer(await sign_in("outsider@team.example.com"))


# --- кто пускается --------------------------------------------------------------------------


async def test_without_a_pass_nobody_sees_the_forecast(client: AsyncClient) -> None:
    response = await client.get(URL)

    assert response.status_code == 403


async def test_without_the_sales_right_the_sales_forecast_is_refused_in_words(
    client: AsyncClient, outsider: dict[str, str], unregistered: None
) -> None:
    """Очередь и ящики продаж — данные этапа (П2): без права «Продажи» — 403 словами, а прогноз
    доноров открыт, как и их очередь."""
    refused = await client.get(URL, params={"stage": "sales"}, headers=outsider)
    donors = await client.get(URL, params={"stage": "donors"}, headers=outsider)

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "Письма продаж — только с правом «Продажи»"
    assert donors.status_code == 200, donors.text


@pytest.mark.parametrize(
    ("params", "said"),
    [({"days": 32}, "31"), ({"days": 0}, "1"), ({"back": 32}, "31"), ({"back": -1}, "0")],
)
async def test_days_are_bounded_by_a_month(
    client: AsyncClient, viewer: dict[str, str], params: dict[str, int], said: str
) -> None:
    response = await client.get(URL, params=params, headers=viewer)

    assert response.status_code == 422, response.text
    assert said in response.text


# --- доноры: окна нет, вручную -------------------------------------------------------------


async def test_donors_write_every_day_and_their_queue_date_has_no_hours(
    client: AsyncClient, viewer: dict[str, str], donors: SenderModel
) -> None:
    response = await client.get(URL, params={"days": 3, "back": 2}, headers=viewer)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["stage"], body["queued_first"], body["supply_extra"]) == ("donors", 30, None)
    assert body["queued_stuck"] is None
    assert (body["window"], body["zone"], body["pace"], body["blocked"]) == (None, None, None, None)
    assert body["today"] == {
        "sent_first": 2,
        "sent_followups": 0,
        "allowance": 25,
        "remaining_first": 23,
        "cut": None,
    }
    days = [
        (
            day["day"],
            day["sending_day"],
            day["capacity_first"],
            day["planned_first"],
            day["followups_due"],
        )
        for day in body["days"]
    ]
    assert days == [
        ("2026-10-21", True, 25, 23, 1),
        ("2026-10-22", True, 30, 7, 1),
        ("2026-10-23", True, 30, 0, 0),
    ]
    assert body["days"][0]["why_not"] == "сегодня уже ушло 2 первых писем — остаток дня 23"
    assert body["past"] == [
        {"day": "2026-10-19", "sent_first": 3, "sent_followups": 0},
        {"day": "2026-10-20", "sent_first": 0, "sent_followups": 1},
    ]
    eta = body["queue_eta"]
    assert (eta["day"], eta["sending_days"], eta["hours"]) == ("2026-10-22", 2, None)
    assert "вручную: пачка выбирает дневной лимит за секунды" in eta["words"]


async def test_donor_mailboxes_show_their_warmup_and_the_next_step(
    client: AsyncClient, viewer: dict[str, str], donors: SenderModel
) -> None:
    """Новые ящики видны сами; у ящика на втором дне разгона — завтрашняя ступень 10."""
    response = await client.get(URL, headers=viewer)

    senders = {box["email"]: box for box in response.json()["senders"]}
    assert set(senders) == {
        "outreach1@mail-d.example.test",
        "outreach2@mail-e.example.test",
        "outreach3@mail-e.example.test",
    }
    young = senders["outreach2@mail-e.example.test"]
    assert (young["warmup_day"], young["allowance_today"], young["sent_today"]) == (2, 5, 0)
    assert (young["next_step_on"], young["next_step_allowance"]) == ("2026-10-22", 10)
    finished = senders["outreach1@mail-d.example.test"]
    assert (finished["warmup_finished"], finished["next_step_on"], finished["sent_today"]) == (
        True,
        None,
        2,
    )
    assert senders["outreach3@mail-e.example.test"]["enabled"] is False


async def test_a_new_mailbox_warms_up_from_the_first_sending_day(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str]
) -> None:
    """Ящик заведён, а первого письма ещё нет: прогноз предполагает его сегодня (у доноров окна
    нет), и мощность растёт ступенями разгона, а следующая ступень названа днём."""
    await _box(session, "fresh@mail-n.example.test", Stage.DONORS, wrote=None)
    letters = await _letters(session, Stage.DONORS)
    await letters.add()
    await session.commit()

    body = (await client.get(URL, params={"days": 5}, headers=viewer)).json()

    assert [day["capacity_first"] for day in body["days"]] == [5, 5, 10, 10, 15]
    [fresh] = body["senders"]
    assert (fresh["first_letter_at"], fresh["warmup_day"], fresh["allowance_today"]) == (None, 1, 5)
    assert (fresh["next_step_on"], fresh["next_step_allowance"]) == ("2026-10-23", 10)


# --- продажи: окно, автоотправка и ответы моста --------------------------------------------


async def test_sales_follow_the_recipient_window_and_the_autosend_pace(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str], sales_module: ForecastSales
) -> None:
    """Четыре письма в очереди (одно не уйдёт, пока адрес не проверят) и сорок у сборки, один ящик по
    двадцать: очередь уходит сегодня — три паузы по 15 минут от сейчас; в пятницу кончается запас, в
    выходные окна нет."""
    await _box(session, "sales1@mail-s.example.test", Stage.SALES)
    letters = await _letters(session, Stage.SALES)
    for _ in range(4):
        await letters.add()
    await session.commit()

    response = await client.get(URL, params={"stage": "sales"}, headers=viewer)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["queued_first"], body["supply_extra"]) == (4, 40)
    assert body["queued_stuck"] == {"letters": 1, "reasons": [{"words": HELD, "letters": 1}]}
    assert (body["window"], body["zone"]) == ("пн–пт 09:00–17:00", "Europe/Berlin")
    assert body["pace"] == {"gap_minutes": 15, "spread": 0.5}
    assert [(day["capacity_first"], day["planned_first"]) for day in body["days"]] == [
        (20, 20),
        (20, 20),
        (20, 3),
        (0, 0),
        (0, 0),
        (20, 0),
        (20, 0),
    ]
    saturday = body["days"][3]
    assert (saturday["sending_day"], saturday["why_not"]) == (
        False,
        "не день отправки: окно получателя — пн–пт 09:00–17:00 по его часам",
    )
    eta = body["queue_eta"]
    assert (eta["day"], eta["sending_days"], eta["hours"]) == ("2026-10-21", 1, 0.8)
    assert {"supply", "queue_zones", "queue_stuck"} <= set(sales_module.asked)


async def test_sales_not_connected_send_nothing_and_say_why(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str], sales_module: ForecastSales
) -> None:
    sales_module.connected_now = False
    await _box(session, "sales1@mail-s.example.test", Stage.SALES)
    letters = await _letters(session, Stage.SALES)
    await letters.add()
    await session.commit()

    body = (await client.get(URL, params={"stage": "sales", "days": 1}, headers=viewer)).json()

    assert body["blocked"] is not None
    assert body["blocked"].startswith("Первые письма этапа не уходят: продажи к почте")
    assert body["days"][0]["planned_first"] == 0
    assert body["queue_eta"]["words"] == f"очередь не уходит: {body['blocked']}"


async def test_without_the_sales_module_the_forecast_does_not_guess_the_supply(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str], unregistered: None
) -> None:
    """Модуль продаж не подключён: запаса сборки прогноз не знает — пусто, а не ноль."""
    letters = await _letters(session, Stage.SALES)
    await letters.add()
    await session.commit()

    body = (await client.get(URL, params={"stage": "sales"}, headers=viewer)).json()

    assert (body["supply_extra"], body["window"], body["zone"]) == (None, None, None)
    assert body["blocked"] == "Первые письма этапа не уходят: продажи к почте ещё не подключены"


async def test_the_most_common_zone_wins_and_ties_go_by_name(
    session: AsyncSession, sales_module: ForecastSales
) -> None:
    sales_module.queued_in = {"Europe/Berlin": 2, "America/New_York": 2, None: 5}

    found = await forecast_module.forecast(session, Stage.SALES, days=1, back=0, now=NOW)

    assert found.clock.zone is not None
    assert found.clock.zone.key == "Europe/Berlin"


def test_the_frozen_clock_is_a_wednesday() -> None:
    assert date(2026, 10, 21) == TODAY
    assert TODAY.weekday() == 2
