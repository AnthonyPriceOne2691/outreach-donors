"""Стоп Этапа 2 без юнитов Ahrefs (`crawl/units_stop.py`, слово Anthony 06.11.2026).

Проверяется то, ради чего он: кончились юниты — обходы не начинаются, кандидат без DR не идёт к письму,
человек видит почему и что делать, а появились юниты — всё идёт само, без правки и без кнопки. Ночной круг
и выдача очереди — в `test_crawl_night.py`: там оснастка настоящих фиксаций.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend.api.crawls import routes
from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.units import MIN_REQUEST_UNITS
from backend.features.core import usage
from backend.features.core.domain import Stage, UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import AhrefsCapModel
from backend.features.crawl import promote
from backend.features.crawl.units_stop import NO_UNITS, UNKNOWN, ensure_units, units_stop
from backend.features.ops.crawl_watch import units_stopped
from backend.features.runs.budget import CapExceededError
from backend.features.runs.repository import RunRepository
from backend.shared.crawl_marks import Held
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.stage2_rows import crawled, scored
from tests.test_ahrefs_cap import KEY, known_key

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

DONOR = "donor.example.test"
RESET = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ есть: без него стопа нет вовсе (`test_without_a_key_nothing_stops`)."""
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "ключ")


async def _out_of_units(session: AsyncSession, left: int = 12) -> None:
    await known_key(session, key_used=KEY - left, reset_at=RESET)


@pytest.mark.parametrize(
    ("left", "stops"),
    [
        (0, True),
        (MIN_REQUEST_UNITS - 1, True),
        (MIN_REQUEST_UNITS, False),
        (KEY, False),
    ],
)
async def test_it_stops_when_the_cheapest_request_does_not_fit(
    session: AsyncSession, left: int, stops: bool
) -> None:
    await _out_of_units(session, left)

    assert (await units_stop(session) is not None) is stops


async def test_the_words_say_what_is_left_what_stands_and_when_it_ends(
    session: AsyncSession,
) -> None:
    await _out_of_units(session, 12)

    stop = await units_stop(session)

    assert stop is not None
    assert stop.why == NO_UNITS
    assert stop.title == "Обходы остановлены: закончились юниты Ahrefs"
    assert "Свободно 12, а самый дешёвый запрос DR стоит 50 юнитов" in stop.detail
    assert "лимит ключа" in stop.detail
    assert "ночной обход стоят" in stop.detail
    assert "кандидаты без DR к письму не идут" in stop.detail
    assert "письма уже проверенным рекламодателям уходят" in stop.detail
    assert "обнуление ключа 14 ноября" in stop.detail
    assert "поднять лимит ключа — в Ahrefs" in stop.detail


async def test_our_own_cap_is_named_with_where_to_raise_it(session: AsyncSession) -> None:
    await known_key(session)
    session.add(AhrefsCapModel(units_cap=100))
    usage.record(session, operation="batch_metrics", units=100, stage=Stage.ADVERTISERS)
    await session.flush()

    stop = await units_stop(session)

    assert stop is not None
    assert "в наш кап" in stop.detail
    assert "на экране «Расход»" in stop.detail


async def test_units_promised_to_a_running_run_are_not_free(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обещанное прогоном ещё не потрачено, но занято: правило `budget.units_free`."""

    async def promised(_self: RunRepository, **_kwargs: object) -> int:
        return 500

    monkeypatch.setattr(RunRepository, "claimed_units", promised)
    await _out_of_units(session, 520)

    stop = await units_stop(session)

    assert stop is not None
    assert "Свободно 20" in stop.detail


async def test_an_unknown_rest_stops_too_and_ends_by_itself(session: AsyncSession) -> None:
    """Ключ есть, а Ahrefs ещё не ответил: тратить вслепую нельзя, и кандидаты пришли бы без DR."""
    stop = await units_stop(session)

    assert stop is not None
    assert stop.why == UNKNOWN
    assert "как только Ahrefs ответит" in stop.detail


async def test_without_a_key_nothing_stops(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Спросить DR не у кого в принципе: это настройка стенда, а не кончившийся остаток."""
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "")

    assert await units_stop(session) is None
    await ensure_units(session)


async def test_a_button_is_refused_with_the_same_words(session: AsyncSession) -> None:
    await _out_of_units(session)

    with pytest.raises(CapExceededError, match="Обходы остановлены: закончились юниты Ahrefs"):
        await ensure_units(session)


async def test_the_stop_ends_by_itself_when_the_key_resets(session: AsyncSession) -> None:
    await _out_of_units(session)
    assert await units_stop(session) is not None

    await known_key(session, key_used=0, reset_at=RESET, asked_at=datetime(2027, 1, 1, tzinfo=UTC))

    assert await units_stop(session) is None


async def test_the_alarm_is_of_the_advertisers_stage_and_says_what_to_do(
    session: AsyncSession,
) -> None:
    await _out_of_units(session)

    alarm = await units_stopped(session)

    assert alarm is not None
    assert alarm.code == "crawl-no-units"
    assert alarm.stage is Stage.ADVERTISERS
    assert alarm.concern is not None
    assert alarm.concern.cause
    assert "обнуление ключа" in alarm.concern.todo


# --- кнопки и экран -------------------------------------------------------------------------------


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    put: list[int] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        put.append(run_id)
        return job_id or "x"

    monkeypatch.setattr(routes, "enqueue_crawl", enqueue)
    monkeypatch.setattr(routes, "workers_alive", lambda **_: 4)
    monkeypatch.setattr(routes, "crawlers_held", lambda **_: Held(0))
    monkeypatch.setattr(routes, "job_positions", lambda *_args, **_kwargs: {})
    return put


@pytest.fixture
async def operator(session: AsyncSession, make_user: MakeUser, sign_in: SignIn) -> str:
    domain = await make_donor(session, DONOR)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price, donor.last_price_at = Decimal("150.00"), datetime.now(UTC)
    await session.commit()
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/crawls", {"hosts": [DONOR]}),
        ("/api/crawls/deep", {"hosts": [DONOR]}),
        ("/api/crawls/donors/crawl", {"expected": 1}),
    ],
)
async def test_every_crawl_button_is_refused_in_words_and_nothing_is_queued(
    client: AsyncClient,
    session: AsyncSession,
    operator: str,
    queue: list[int],
    path: str,
    body: dict[str, object],
) -> None:
    await _out_of_units(session)
    await session.commit()

    response = await client.post(path, json=body, headers=bearer(operator))

    assert response.status_code == 409, response.text
    assert "закончились юниты Ahrefs" in response.json()["detail"]
    assert queue == []


async def test_a_crawl_goes_as_usual_while_units_are_there(
    client: AsyncClient, session: AsyncSession, operator: str, queue: list[int]
) -> None:
    await known_key(session)
    await session.commit()

    response = await client.post("/api/crawls", json={"hosts": [DONOR]}, headers=bearer(operator))

    assert response.status_code == 200, response.text
    assert len(queue) == 1


async def test_the_screen_asks_one_light_route(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    await known_key(session)
    await session.commit()
    going = await client.get("/api/crawls/units-stop", headers=bearer(operator))
    assert going.status_code == 200, going.text
    assert going.json() is None

    await _out_of_units_later(session)
    await session.commit()
    stopped = await client.get("/api/crawls/units-stop", headers=bearer(operator))

    assert stopped.json()["title"] == "Обходы остановлены: закончились юниты Ahrefs"
    assert "Свободно 12" in stopped.json()["detail"]


async def _out_of_units_later(session: AsyncSession) -> None:
    """Новый ответ Ahrefs поверх прежнего: снимок принимает только более свежий."""
    await known_key(
        session, key_used=KEY - 12, reset_at=RESET, asked_at=datetime(2027, 1, 1, tzinfo=UTC)
    )


# --- кандидат без DR не идёт к письму --------------------------------------------------------------


async def _hosts(session: AsyncSession) -> set[str]:
    rows = await session.scalars(
        select(DomainModel.host).join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
    )
    return set(rows.all())


async def test_a_bought_candidate_without_dr_waits_and_the_screen_says_so(
    session: AsyncSession,
) -> None:
    run = await crawled(session, DONOR)
    checked = await scored(session, run, "checked.example.test")
    checked.dr_checked_at = datetime.now(UTC)
    await scored(session, run, "unchecked.example.test")
    await session.flush()

    standing = await promote.standing(session)
    report = await promote.promote(session)

    assert (standing.ready, standing.fresh, standing.waiting_dr) == (1, 1, 1)
    assert (report.considered, report.waiting_dr) == (1, 1)
    assert report.as_dict()["ждут проверки DR"] == 1
    assert await _hosts(session) == {"checked.example.test"}


async def test_a_person_who_said_write_is_not_held_by_dr(session: AsyncSession) -> None:
    """«Пишем» человека сильнее: так и крупный сайт переводится, если человек сказал сам."""
    run = await crawled(session, DONOR)
    await scored(session, run, "decided.example.test", verdict=Verdict.PENDING, confirmed=True)
    await session.flush()

    report = await promote.promote(session)

    assert report.waiting_dr == 0
    assert await _hosts(session) == {"decided.example.test"}


async def test_one_checked_candidate_is_enough_for_the_domain(session: AsyncSession) -> None:
    """Домен ждёт, только когда ни один его кандидат не проверен: ссылки у него бывают от многих."""
    first, second = await crawled(session, DONOR), await crawled(session, "other.example.test")
    await scored(session, first, "both.example.test")
    checked = await scored(session, second, "both.example.test")
    checked.dr_checked_at = datetime.now(UTC)
    await session.flush()

    standing = await promote.standing(session)
    report = await promote.promote(session)

    assert (standing.ready, standing.waiting_dr) == (1, 0)
    assert report.waiting_dr == 0
    assert await _hosts(session) == {"both.example.test"}


async def test_without_a_key_nobody_waits_for_dr(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "")
    run = await crawled(session, DONOR)
    await scored(session, run, "unchecked.example.test")
    await session.flush()

    standing = await promote.standing(session)
    report = await promote.promote(session)

    assert (standing.ready, standing.waiting_dr) == (1, 0)
    assert report.waiting_dr == 0
    assert await _hosts(session) == {"unchecked.example.test"}
