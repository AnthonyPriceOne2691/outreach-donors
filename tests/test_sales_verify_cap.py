"""Месячный потолок проверки адресов продаж (`SALES_VERIFIER_MONTH_CAP`, `sales/verify_cap.py`).

Счётчик проверок у Hunter свой, отдельный от поисков доноров: потолок держит проверки продаж
в пределах тарифа и называет остаток. Счёт — единицы `sales_verify` журнала расхода с первого
числа месяца (та же граница, что у капа Ahrefs); у живого проверяльщика потолок обязателен,
у выдуманного не нужен. Потолок выбран или не задан — платная часть прохода встаёт словами,
лиды остаются `new` с причиной, бесплатные правила им применены. Сеть — подделки; база настоящая.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from backend.config import sales as sales_cfg
from backend.features.contacts.provider import ProviderError
from backend.features.core import usage
from backend.features.sales import cleaning, verify_cap
from backend.features.sales.models import LeadStatus, RejectionReason, SalesHypothesisModel
from backend.features.sales.verifier import Verdict
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_cleaning import (
    IVAN,
    NOW,
    PAID_READY,
    READY,
    Row,
    Scripted,
    World,
    _usage,
    rejected,
)

MARIA = "maria@acme.example.test"
OLGA = "olga@beta.example.test"
KIM = "kim@gamma.example.test"
#: Прошлый месяц для `NOW` (4 октября): его проверки в потолок октября не входят.
LAST_MONTH = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
async def world(session: AsyncSession) -> World:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return World(session, hypothesis)


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> cleaning.MailRoute:
        return cleaning.MailRoute.MX

    monkeypatch.setattr(cleaning, "mail_route", route)


def _cap(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", value)


async def _spent(world: World, units: int, *, at: datetime | None = None) -> None:
    """Проверки, уже записанные в журнал расхода: в этом месяце или в названный день."""
    row = usage.record(world.session, operation="sales_verify", units=units)
    if at is not None:
        row.created_at = at
    await world.session.flush()


def _waiting(words: str) -> Row:
    return Row(LeadStatus.NEW, note=f"проверка не выполнена: {words}")


async def test_paid_checks_go_up_to_the_month_cap_and_the_rest_wait_new_in_words(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В этом месяце уже ушла одна проверка, потолок — три: проход проверяет двоих, остальные
    ждут `new` словами «выбран: 3 из 3»; прошлый месяц в счёт не идёт."""
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    _cap(monkeypatch, "3")
    await _spent(world, 1)
    await _spent(world, 50, at=LAST_MONTH)
    hunter = Scripted()
    for email in (IVAN, MARIA, OLGA, KIM):
        await world.lead(email)

    report = await cleaning.clean(world.session, hunter, now=NOW)

    words = "потолок проверки адресов на месяц выбран: 3 из 3 — следующий месяц продолжит"
    assert hunter.asked == [IVAN, MARIA]
    assert await world.rows() == [PAID_READY, PAID_READY, _waiting(words), _waiting(words)]
    assert (report.ready, report.unverified, report.paid_units, report.stopped) == (
        2,
        2,
        2,
        words,
    )
    assert await verify_cap.month_spent(world.session, now=NOW) == 3


async def test_spent_cap_asks_nothing_and_the_free_rules_still_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, "2")
    await _spent(world, 2)
    hunter = Scripted()
    first = await world.lead(IVAN)
    await world.lead(IVAN)  # дубль — бесплатное правило решает без Hunter

    report = await cleaning.clean(world.session, hunter, now=NOW)

    words = "потолок проверки адресов на месяц выбран: 2 из 2 — следующий месяц продолжит"
    assert hunter.asked == []
    assert await world.rows() == [
        _waiting(words),
        rejected(RejectionReason.DUPLICATE, f"дубль: адрес уже у лида №{first.id}"),
    ]
    assert report.stopped == words


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("", "потолок проверки адресов на месяц не задан — задайте SALES_VERIFIER_MONTH_CAP"),
        (
            "1 500",
            "SALES_VERIFIER_MONTH_CAP «1 500» — нужно целое число проверок адреса в месяц, "
            "не меньше нуля",
        ),
        (
            "-1",
            "SALES_VERIFIER_MONTH_CAP «-1» — нужно целое число проверок адреса в месяц, "
            "не меньше нуля",
        ),
    ],
)
async def test_live_check_without_a_good_cap_stops_the_paid_part_in_words_without_a_crash(
    world: World, monkeypatch: pytest.MonkeyPatch, value: str, words: str
) -> None:
    """Потолок не задан или негоден — не падение и не тихий «без потолка»: платная часть встаёт
    словами с именем настройки, лиды ждут `new`."""
    _cap(monkeypatch, value)
    hunter = Scripted()
    await world.lead(IVAN)

    report = await cleaning.clean(world.session, hunter, now=NOW)

    assert hunter.asked == []
    assert await world.rows() == [_waiting(words)]
    assert report.stopped == words
    assert await _usage(world.session) == []


async def test_the_made_up_verifier_needs_no_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, "")
    await world.lead(IVAN)

    report = await world.clean()

    assert await world.rows() == [READY]
    assert report.stopped is None


async def test_a_refusal_costs_no_place_under_the_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ провайдера единиц не стоит — место под потолком возвращается следующему адресу."""
    monkeypatch.setattr(cleaning, "VERIFY_CONCURRENCY", 1)
    _cap(monkeypatch, "1")
    why = "провайдер не ответил (HTTP 503)"
    hunter = Scripted({IVAN: ProviderError(why)})
    await world.lead(IVAN)
    await world.lead(MARIA)

    report = await cleaning.clean(world.session, hunter, now=NOW)

    assert hunter.asked == [IVAN, MARIA]
    assert await world.rows() == [_waiting(why), PAID_READY]
    assert (report.paid_units, report.stopped) == (1, None)


class Slow(Scripted):
    """Проверяльщик, который отдаёт ход, пока Hunter думает: четыре потока прохода идут разом."""

    async def verify(self, email: str) -> Verdict:
        await asyncio.sleep(0)
        return await super().verify(email)


async def test_four_threads_at_once_do_not_overshoot_the_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Место бронируется до запроса: проход в четыре потока не уходит за потолок."""
    _cap(monkeypatch, "2")
    hunter = Slow()
    for index in range(6):
        await world.lead(f"lead{index}@acme.example.test")

    report = await cleaning.clean(world.session, hunter, now=NOW)

    assert (len(hunter.asked), report.paid_units, report.unverified) == (2, 2, 4)


async def test_one_lead_cleaning_of_a_referral_or_a_collection_keeps_the_same_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Пишите другому» и сбор базы чистят своих лидов `clean_leads` — под тем же потолком."""
    _cap(monkeypatch, "1")
    await _spent(world, 1)
    lead = await world.lead(IVAN)
    hunter = Scripted()

    report = await cleaning.clean_leads(world.session, [lead], hunter, now=NOW)

    words = "потолок проверки адресов на месяц выбран: 1 из 1 — следующий месяц продолжит"
    assert (hunter.asked, report.stopped) == ([], words)
    assert await world.rows() == [_waiting(words)]


async def test_month_words_for_the_screens(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, "1500")
    await _spent(world, 120)
    await _spent(world, 9, at=LAST_MONTH)

    found = await verify_cap.month(world.session, now=NOW)

    assert (found.words, found.sentence) == (
        "в этом месяце 120 из 1500",
        "Проверок адреса в этом месяце 120 из 1500",
    )
