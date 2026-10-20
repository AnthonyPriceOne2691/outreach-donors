"""Ответы Hunter на проверку адреса, которые не вердикт: 202 и отказ домену (`hunter_codes.py`).

202 («ещё проверяет») проход переспрашивает тут же — бесплатно, не больше двух раз, с паузой
общих повторов и под ограничителем частоты; не дождались — `StillCheckingError`, и перепроверка
отложена по сроку (`verify_again.py`, свои тесты — `test_sales_verify_again.py`). 400
`blacklisted_domain` — общий `ProviderDomainRefusedError`, отказ домену постоянный: лид отклонён
сразу словами, без повтора и без единицы расхода.

Сеть — только `httpx.MockTransport`, формы ответов — по документации Hunter, числа некруглые.
Паузы повторов записываются, а не спятся.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import ProviderDomainRefusedError, ProviderError
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import cleaning, hunter_codes, verify_again
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.models import LeadStatus, SalesHypothesisModel
from backend.features.sales.verifier import HunterVerifier, Verdict
from backend.features.sales.verify_cap import MonthCapped
from backend.shared.net import retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_cleaning import NOW, Row, World

EMAIL = "ivan@acme.example.test"
KEY = "k-test"
VALID = httpx.Response(200, json={"data": {"status": "valid", "score": 97}})
PENDING = httpx.Response(202, json={"data": {"status": "pending"}})
#: Hunter ещё проверяет — перепроверка через двадцать минут, первая из трёх (умолчания настроек).
AGAIN_AT = NOW + timedelta(minutes=20)
NOT_YET = verify_again.checking(1, AGAIN_AT)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Паузы повторов (`net/retry`) — записью, без сна."""
    taken: list[float] = []

    async def pause(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(retry, "_sleep", pause)
    return taken


@pytest.fixture(autouse=True)
def _mail_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Почту домены принимают; месячный потолок проверок — с запасом: здесь не о нём."""

    async def route(_host: str, **_kwargs: object) -> MailRoute:
        return MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr("backend.config.sales.VERIFIER_MONTH_CAP", "100000")


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


def _script(*answers: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    """Ответы по очереди — последний повторяется; запросы — в список."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers[min(len(seen), len(answers)) - 1]

    return handler, seen


async def _verify(handler: Handler, email: str = EMAIL) -> Verdict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await HunterVerifier(http, api_key=KEY).verify(email)


async def _clean(world: World, handler: Handler) -> CleaningReport:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await cleaning.clean(world.session, HunterVerifier(http, api_key=KEY), now=NOW)


async def _units(session: AsyncSession) -> list[int | None]:
    found = await session.scalars(
        select(UsageRecordModel.units)
        .where(UsageRecordModel.operation == "sales_verify")
        .order_by(UsageRecordModel.id)
    )
    return list(found.all())


# --- 202: переспросить в том же проходе -------------------------------------------------------


@pytest.mark.parametrize("pending", [1, 2])
async def test_still_checking_is_asked_again_in_the_same_pass_until_the_verdict(
    pauses: list[float], pending: int
) -> None:
    handler, seen = _script(*[PENDING] * pending, VALID)

    assert await _verify(handler) == Verdict("valid", 97, units=1)
    assert len(seen) == pending + 1
    # Пауза общих повторов: секунда, затем две — с разбросом до полсекунды.
    assert [int(pause) for pause in pauses] == [1, 2][:pending]
    assert all(seconds - int(seconds) <= 0.5 for seconds in pauses)
    assert {request.url.params["email"] for request in seen} == {EMAIL}


async def test_still_checking_after_two_asks_is_its_own_refusal_for_a_later_recheck(
    pauses: list[float],
) -> None:
    """Не дождались — своё исключение, а не общий отказ сервиса: его очистка откладывает."""
    handler, seen = _script(PENDING)

    with pytest.raises(ProviderError) as refused:
        await _verify(handler)

    assert type(refused.value) is hunter_codes.StillCheckingError
    assert str(refused.value) == (
        "провайдер ещё проверяет адрес и после 2 переспросов — вердикта пока нет"
    )
    assert (len(seen), len(pauses)) == (3, 2)


async def test_still_checking_waits_as_long_as_hunter_says_but_not_longer_than_the_cap(
    pauses: list[float],
) -> None:
    told = httpx.Response(202, headers={"Retry-After": "7"}, json={"data": {}})
    handler, _ = _script(told, httpx.Response(202, headers={"Retry-After": "600"}), VALID)

    await _verify(handler)

    assert [int(pause) for pause in pauses] == [7, int(retry.MAX_DELAY_SEC)]


async def test_every_ask_again_goes_through_the_rate_limiter_of_the_pass() -> None:
    taken: list[str] = []

    class Counting(retry.RateLimiter):
        async def acquire(self) -> None:
            taken.append("взят")

    handler, _ = _script(PENDING, PENDING, VALID)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        hunter = HunterVerifier(http, api_key=KEY)
        hunter._limiter = Counting(per_minute=1)
        await hunter.verify(EMAIL)

    assert taken == ["взят"] * 3


async def test_asks_again_and_retries_of_the_network_are_counted_apart(
    pauses: list[float],
) -> None:
    """Переспрос «ещё проверяет» и повтор перегрузки — разные счёты: 503 после 202 повторяет
    сам запрос (`verifier._get`), и вердикт приходит третьим ответом."""
    handler, seen = _script(PENDING, httpx.Response(503, text="<html>упал</html>"), VALID)

    assert await _verify(handler) == Verdict("valid", 97, units=1)
    assert (len(seen), len(pauses)) == (3, 2)


async def test_cleaning_gets_the_verdict_in_the_same_pass_or_defers_the_lead_to_its_term(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Дождались — лид «готов» тем же проходом и платит одну проверку; не дождались — лид в
    прежнем статусе со словами, сроком и счётом, а следующая очистка спросит снова и сочтёт."""
    planned: list[object] = []
    monkeypatch.setattr(verify_again.deferred, "plan", lambda *a, **k: planned.append(a))
    answers = {EMAIL: [PENDING, VALID], "olga@beta.example.test": [PENDING]}
    seen: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        email = request.url.params["email"]
        seen[email] += 1
        script = answers[email]
        return script[min(seen[email], len(script)) - 1]

    await world.lead(EMAIL)
    await world.lead("olga@beta.example.test")

    report = await _clean(world, handler)

    assert await world.rows() == [
        Row(LeadStatus.READY, verification="hunter:valid", score=97, verified_at=NOW),
        Row(LeadStatus.NEW, note=NOT_YET),
    ]
    assert (report.ready, report.still_checking, report.unverified) == (1, 1, 0)
    assert (report.again_at, report.paid_units) == (AGAIN_AT, 1)
    assert await _units(world.session) == [1]
    assert seen == Counter({EMAIL: 2, "olga@beta.example.test": 3})
    assert len(planned) == 1

    await _clean(world, handler)

    assert seen["olga@beta.example.test"] == 6
    assert (await world.rows())[1] == Row(LeadStatus.NEW, note=verify_again.checking(2, AGAIN_AT))


# --- 400 blacklisted_domain: отказ домену постоянный -------------------------------------------

BLACKLISTED = httpx.Response(
    400, json={"errors": [{"id": "blacklisted_domain", "details": "This domain is blacklisted"}]}
)
REFUSED = "адрес не проверить: сервис проверки не работает с этим доменом (acme.example.test)"


async def test_a_domain_the_service_refuses_is_the_shared_refusal_asked_once(
    pauses: list[float],
) -> None:
    """Маркер разбирает общее правило (`contacts/provider._raise_refusal`) — своей копии у
    проверки адреса нет; повтор такой отказ не лечит."""
    handler, seen = _script(BLACKLISTED)

    with pytest.raises(ProviderDomainRefusedError) as refused:
        await _verify(handler)

    assert str(refused.value) == (
        "провайдер не работает с доменом: This domain is blacklisted — владелец домена "
        "запретил обработку адресов"
    )
    assert (len(seen), pauses) == (1, [])


async def test_cleaning_rejects_such_a_lead_at_once_without_a_unit_and_never_asks_again(
    world: World,
) -> None:
    """Отказ домену — не сбой: лид отклонён сразу, кодом «не подтверждён» и словами; проход идёт
    дальше, единица расхода — только за вердикт соседа, а следующая очистка лида не берёт."""
    answers = {EMAIL: BLACKLISTED, "olga@beta.example.test": VALID}
    seen: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        email = request.url.params["email"]
        seen[email] += 1
        return answers[email]

    await world.lead(EMAIL)
    await world.lead("olga@beta.example.test")

    report = await _clean(world, handler)

    assert await world.rows() == [
        Row(LeadStatus.REJECTED, "unconfirmed", REFUSED),
        Row(LeadStatus.READY, verification="hunter:valid", score=97, verified_at=NOW),
    ]
    assert report == CleaningReport(
        checked=2,
        ready=1,
        rejected=Counter({"unconfirmed": 1}),
        verified=1,
        paid_units=1,
        verifier="hunter",
    )
    assert await _units(world.session) == [1]

    assert await _clean(world, handler) == CleaningReport(verifier="hunter")
    assert seen == Counter({EMAIL: 1, "olga@beta.example.test": 1})


async def test_the_month_cap_gets_its_place_back_on_a_refused_domain() -> None:
    handler, _ = _script(BLACKLISTED)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capped = MonthCapped(HunterVerifier(http, api_key=KEY), cap=5, spent=2)
        with pytest.raises(ProviderDomainRefusedError):
            await capped.verify(EMAIL)

    assert capped.used == 2
