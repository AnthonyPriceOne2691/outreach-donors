"""Приёмка Этапа 2 по донору: «100 рекламодателей с контактами, письма ушли 70%».

Сверка ТЗ 11.10.2026: этих чисел не было нигде. Проверяется то, чем отчёт может
соврать: кого он считает рекламодателем донора (то же условие, что перевод), доля —
от тех, что с адресом, домен двух доноров — у каждого, а в сумме — один раз, и
донор, обойдённый без находок, в отчёте есть, а не пропал.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, SuppressionReason, UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.crawl import promote
from backend.features.crawl.acceptance import (
    TARGET_SENT_SHARE,
    TARGET_WITH_ADDRESS,
    Tally,
    acceptance,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import addressed, crawled, domain_of, lettered, promoted, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ALPHA = "alpha-donor.example.test"
BETA = "beta-donor.example.test"
EMPTY = "empty-donor.example.test"


@pytest.fixture
async def donors(session: AsyncSession) -> None:
    """Альфа: «куплено» ×3 (одному человек сказал «не пишем»), спорный с «пишем»,
    спорный без решения, «мимо». Бета: один «куплено» — общий с альфой домен.
    Пустой: обойдён, рекламодателей не нашлось."""
    alpha = await crawled(session, ALPHA)
    await scored(session, alpha, "one.example.test")
    await scored(session, alpha, "two.example.test")
    await scored(session, alpha, "false.example.test", confirmed=False)
    await scored(session, alpha, "yes.example.test", verdict=Verdict.PENDING, confirmed=True)
    await scored(session, alpha, "maybe.example.test", verdict=Verdict.PENDING)
    await scored(session, alpha, "noise.example.test", verdict=Verdict.SKIPPED)
    beta = await crawled(session, BETA)
    await scored(session, beta, "one.example.test")
    await crawled(session, EMPTY)
    # Переведены трое; адрес — у двух; письмо ушло одному, другому — собрано.
    for host in ("one.example.test", "two.example.test", "yes.example.test"):
        await promoted(session, host, donor_host=ALPHA)
    await addressed(session, "one.example.test")
    await addressed(session, "two.example.test")
    await lettered(session, "one.example.test", MessageStatus.DELIVERED)
    await lettered(session, "two.example.test", MessageStatus.QUEUED)


async def test_each_donor_counts_its_advertisers(session: AsyncSession, donors: None) -> None:
    report = await acceptance(session, limit=50)

    tallies = {row.host: row.tally for row in report.donors}
    assert [row.host for row in report.donors] == [ALPHA, BETA, EMPTY]
    assert tallies[ALPHA] == Tally(found=3, promoted=3, with_address=2, sent=1)
    assert tallies[ALPHA].sent_share == 0.5
    assert tallies[ALPHA].accepted is False
    # Домен двух доноров — рекламодатель каждого: письмо ему одно, а донора — два.
    assert tallies[BETA] == Tally(found=1, promoted=1, with_address=1, sent=1)
    # Обойдён, никого не нашлось — тоже ответ приёмке, а не пропавшая строка.
    assert tallies[EMPTY] == Tally()
    assert tallies[EMPTY].sent_share is None


async def test_all_donors_together_count_a_domain_once(session: AsyncSession, donors: None) -> None:
    """ТЗ допускает считать критерий по нескольким донорам сразу: там домен — один."""
    report = await acceptance(session, limit=50)

    assert report.overall == Tally(found=3, promoted=3, with_address=2, sent=1)


async def test_a_donor_is_found_by_the_promotion_rule(session: AsyncSession, donors: None) -> None:
    """Рекламодатель донора — тот, кого переводит «Перевести»: «куплено» или «пишем»,
    без «не пишем». Правило одно (`advertiser_rules.writes_to`) — сверяем числом."""
    report = await acceptance(session, limit=50)

    standing = await promote.standing(session)
    assert report.overall.found == standing.ready == 3


async def test_the_limit_keeps_the_strongest(session: AsyncSession, donors: None) -> None:
    report = await acceptance(session, limit=1)

    assert [row.host for row in report.donors] == [ALPHA]
    # Сколько доноров всего — от сервера: по этому числу экран считает страницы.
    assert report.total == 3
    assert report.overall.found == 3


async def test_pages_go_on_in_the_same_order_and_the_sum_is_over_all_donors(
    session: AsyncSession, donors: None
) -> None:
    """Вторая страница — продолжение первой, порядок прежний; «Все доноры вместе» на каждой
    странице считается по всем донорам, а не по её строкам."""
    whole = await acceptance(session, limit=50)
    first = await acceptance(session, limit=2)
    second = await acceptance(session, limit=2, offset=2)

    assert [row.host for row in first.donors] == [ALPHA, BETA]
    assert [row.host for row in second.donors] == [EMPTY]
    assert first.total == second.total == whole.total == 3
    assert first.overall == second.overall == whole.overall
    assert first.overall_findings == second.overall_findings == whole.overall_findings
    assert second.overall.found == 3


async def test_address_and_letters_count_only_promoted_advertisers(
    session: AsyncSession, donors: None
) -> None:
    """Проверка ветки 12.10.2026: домен, снятый стоп-листом при переводе, с адресом от
    Этапа 1 считался «с адресом» — письма ему не будет, и приёмку он только украшал.
    То же — с письмом донору: «ушло» — оффер рекламодателю, которому пишем."""
    alpha = await crawled(session, "gamma-donor.example.test")
    await scored(session, alpha, "dropped.example.test")
    await addressed(session, "dropped.example.test")
    await lettered(session, "dropped.example.test", MessageStatus.DELIVERED)

    report = await acceptance(session, limit=50)

    tallies = {row.host: row.tally for row in report.donors}
    assert tallies["gamma-donor.example.test"] == Tally(found=1)
    assert report.overall == Tally(found=4, promoted=3, with_address=2, sent=1)


@pytest.mark.parametrize("listed_by", ["стоп-лист", "прежний список"])
async def test_a_supplier_is_marked_and_left_out_of_the_sum(
    session: AsyncSession, donors: None, listed_by: str
) -> None:
    """Рекламодателей поставщика не трогаем: строка его помечена, а «все доноры вместе»
    его не считает — порог ему не задача. Поставщик — правилом перевода, из любого
    места: до 12.10.2026 приёмка читала только прежний список консоли, и поставщик,
    внесённый экраном «Стоп-лист» или `suppliers-import`, шёл в ней обычным донором."""
    supplier = await crawled(session, "partner.example.test")
    await scored(session, supplier, "only-there.example.test")
    await promoted(session, "only-there.example.test", donor_host="partner.example.test")
    await addressed(session, "only-there.example.test")
    if listed_by == "стоп-лист":
        domain = await domain_of(session, "partner.example.test")
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.SUPPLIER))
    else:
        session.add(SupplierDonorModel(host="Partner.example.test", note="текущий партнёр"))
    await session.flush()

    report = await acceptance(session, limit=50)

    rows = {row.host: row for row in report.donors}
    assert rows["partner.example.test"].supplier is True
    assert rows[ALPHA].supplier is False
    assert report.overall == Tally(found=3, promoted=3, with_address=2, sent=1)


@pytest.mark.parametrize(
    ("with_address", "sent", "accepted"),
    [
        (TARGET_WITH_ADDRESS, 70, True),
        (TARGET_WITH_ADDRESS, 69, False),
        (TARGET_WITH_ADDRESS - 1, 99, False),
        (250, 175, True),
        (0, 0, False),
    ],
)
def test_the_threshold_of_the_requirement(with_address: int, sent: int, accepted: bool) -> None:
    """«100 рекламодателей с контактами, письма ушли 70% из них»: оба условия разом,
    ровно семьдесят процентов — уже пройдено."""
    tally = Tally(found=with_address, promoted=with_address, with_address=with_address, sent=sent)

    assert TARGET_SENT_SHARE == 0.70
    assert tally.accepted is accepted


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("приёмка@site.com", role=UserRole.OPERATOR)
    return await sign_in("приёмка@site.com")


async def test_the_screen_gets_rows_crawls_and_targets(
    client: AsyncClient, token: str, session: AsyncSession, donors: None
) -> None:
    await session.commit()

    response = await client.get("/api/advertisers/acceptance", headers=bearer(token))

    body = response.json()
    assert response.status_code == 200, body
    first = body["donors"][0]
    assert first["host"] == ALPHA
    assert first["crawl"]["status"] == "done"
    assert first["crawl"]["pages"] == 400
    assert first["tally"] == {
        "found": 3,
        "promoted": 3,
        "with_address": 2,
        "sent": 1,
        "sent_share": 0.5,
        "accepted": False,
    }
    assert first["supplier"] is False
    assert (body["overall"]["found"], body["total"]) == (3, 3)
    assert (body["target_with_address"], body["target_sent_share"]) == (100, 0.7)


async def test_the_screen_turns_pages_of_fifteen(
    client: AsyncClient, token: str, session: AsyncSession, donors: None
) -> None:
    """Страницы — на сервере, по пятнадцать доноров, а итог и пороги — на каждой. Страница за
    концом — без доноров, но с итогом и числом: экран уходит на последнюю."""
    for number in range(14):
        await crawled(session, f"donor-{number:02}.example.test")
    await session.commit()

    async def page(number: int) -> dict[str, Any]:
        answer = await client.get(
            f"/api/advertisers/acceptance?page={number}", headers=bearer(token)
        )
        assert answer.status_code == 200, answer.text
        body: dict[str, Any] = answer.json()
        return body

    first, second, past = await page(1), await page(2), await page(3)

    assert [row["host"] for row in first["donors"]][:3] == [ALPHA, BETA, "donor-00.example.test"]
    assert len(first["donors"]) == 15
    assert [row["host"] for row in second["donors"]] == [
        "donor-13.example.test",
        EMPTY,
    ]
    assert second["donors"][0]["crawl"]["host"] == "donor-13.example.test"
    assert [(body["page"], body["limit"], body["total"]) for body in (first, second, past)] == [
        (1, 15, 17),
        (2, 15, 17),
        (3, 15, 17),
    ]
    assert past["donors"] == []
    assert first["overall"] == second["overall"] == past["overall"]
    assert first["overall_findings"] == second["overall_findings"] == past["overall_findings"]
    assert second["overall"]["found"] == 3


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "page=0", "page=1000001", "page=abc"])
async def test_a_crooked_page_is_refused_in_words(
    client: AsyncClient, token: str, query: str
) -> None:
    answer = await client.get(f"/api/advertisers/acceptance?{query}", headers=bearer(token))

    assert answer.status_code == 422, answer.text
    assert any("а" <= letter.lower() <= "я" for letter in answer.json()["detail"][0]["msg"])
