"""Выборка спорных по срезам: балл, признак скоринга, ниша донора.

Проверяется то, чем разбивка может соврать: признак скоринга сменил слова и выпал из
таблицы молча; пояснение без балла сочтено признаком; строка вне выборки попала в срез;
ниша без темы потерялась; балл «10» встал раньше «2».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl import scoring
from backend.features.crawl.links import OutLink
from backend.features.crawl.strata import FEATURES, OTHER, SCORED, features_of, strata
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored
from tests.test_scoring_accuracy import hosts, turn_order

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

DONOR = "donor.example.test"
#: Ключи донора, с которыми ссылка о кредитах — чужая тематика.
BETTING = ["best betting sites south africa", "free bets za"]


def _link(
    *,
    root: str = "advertiser.example.test",
    anchor: str = "подробнее об истории клуба",
    page: str = f"https://{DONOR}/news/1",
    url: str | None = None,
    nofollow: bool = False,
    sponsored: bool = False,
) -> OutLink:
    return OutLink(
        page_url=page,
        url=url or f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=nofollow,
        sponsored=sponsored,
        ugc=False,
    )


def _scoring_reasons() -> list[str]:
    """Причины, которые пишет сам скоринг, — по признаку на ссылку или домен."""
    reasons: list[str] = []
    for link, source in (
        (_link(sponsored=True), False),
        (_link(page=f"https://{DONOR}/sponsored/betting-guide"), False),
        (_link(url="https://advertiser.example.test/signup?aff_id=77", nofollow=True), False),
        (_link(anchor="Play now"), False),
        (_link(anchor="Play now", nofollow=True), False),
        (_link(sponsored=True), True),
    ):
        reasons.extend(scoring.score_link(link, source=source).reasons)
    pages = [
        _link(sponsored=True, page=f"https://{DONOR}/news/{n}") for n in range(scoring.REPEAT_PAGES)
    ]
    repeated = scoring.score_candidates(pages, DONOR, today=date(2026, 10, 11))
    scoring.boost_across_donors(repeated, {"advertiser.example.test": scoring.BOOST_DONORS})
    reasons.extend(repeated[0].reasons)
    loans = _link(
        root="lender.example.test",
        url="https://lender.example.test/payday-loans/",
        anchor="Apply now for payday loans",
    )
    off = scoring.score_candidates([loans], DONOR, today=date(2026, 10, 11), topic=BETTING)
    reasons.extend(off[0].reasons)
    return reasons


class TestFeatures:
    def test_every_scored_reason_of_scoring_has_its_feature(self) -> None:
        """Причина с баллом, которую пишет скоринг, — признак таблицы, а не «прочее»; и каждый
        признак таблицы скоринг действительно пишет: таблица не разошлась со словами скоринга
        ни в одну сторону."""
        scored_reasons = [reason for reason in _scoring_reasons() if SCORED.search(reason)]

        unknown = [reason for reason in scored_reasons if features_of([reason]) == [OTHER]]
        seen = {feature.key for reason in scored_reasons for feature in features_of([reason])}

        assert unknown == []
        assert seen == {feature.key for feature in FEATURES}

    def test_reasons_without_points_explain_and_do_not_count(self) -> None:
        reasons = [
            "ссылки с 40 страниц, но сильных признаков рекламы нет — автор, блогролл или навигация",
            "ссылки на документы (PDF и т. п.) не в счёт: 2",
            "«куплено» дала бы одна тематика, сильных признаков нет — решает человек",
        ]

        assert features_of(reasons) == []

    def test_a_new_scored_reason_is_other_not_lost(self) -> None:
        """Новая причина скоринга с баллом не выпадает из разбивки молча."""
        found = features_of(["совсем новый признак +2", "rel=sponsored +5", "rel=sponsored +5"])

        assert [feature.key for feature in found] == ["sponsored", "other"]

    def test_no_reasons_no_features(self) -> None:
        assert features_of(None) == []
        assert features_of([]) == []


class TestStrata:
    def test_points_go_by_number_not_by_text(self) -> None:
        rows = [
            (10, [], None, None),
            (2, [], True, None),
            (3, [], False, None),
            (2, [], None, None),
        ]

        found = strata(rows)

        assert [stratum.key for stratum in found.points] == ["2", "3", "10"]
        two = found.points[0].decisions
        assert (two.total, two.write, two.skip) == (2, 1, 0)

    def test_a_row_counts_in_each_of_its_features(self) -> None:
        reasons = ["коммерческий анкор (призыв) +1", "коммерческая ссылка под nofollow +2"]
        rows = [(3, reasons, False, None), (2, ["rel=sponsored +5"], True, None)]

        found = strata(rows)

        by_key = {stratum.key: stratum.decisions for stratum in found.features}
        assert set(by_key) == {"anchor", "nofollow", "sponsored"}
        assert (by_key["anchor"].skip, by_key["nofollow"].skip, by_key["sponsored"].write) == (
            1,
            1,
            1,
        )
        assert [stratum.key for stratum in found.features] == ["sponsored", "anchor", "nofollow"]

    def test_niches_by_size_and_the_unknown_last(self) -> None:
        rows = [
            (2, [], None, None),
            (2, [], None, None),
            (2, [], True, None),
            (2, [], False, "finance"),
            (3, [], None, "gambling"),
            (3, [], None, "gambling"),
            (2, [], None, "retired-topic"),
        ]

        found = strata(rows)

        assert [(stratum.key, stratum.title) for stratum in found.niches] == [
            ("gambling", "Азартные игры"),
            ("finance", "Финансы"),
            ("retired-topic", "retired-topic"),
            ("unknown", "неизвестна"),
        ]
        unknown = found.niches[-1].decisions
        assert (unknown.total, unknown.decided) == (3, 1)

    def test_nothing_in_the_sample_is_empty_strata(self) -> None:
        found = strata([])

        assert (found.points, found.features, found.niches) == ([], [], [])


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("срезы@site.com", role=UserRole.ADMIN)
    return await sign_in("срезы@site.com")


async def test_the_strata_route_counts_the_disputed_sample_alone(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Только спорные выборки: верх очереди вне выборки и «куплено» в срезы не идут. Ниша —
    тема сайта донора по его имени."""
    session.add(DomainModel(host=DONOR, site_topic="finance"))
    run = await crawled(session, DONOR)
    inside = turn_order(hosts(in_sample=True, number=3, prefix="cut"))
    for root, points, answer in zip(inside, (2, 3, 3), (True, False, None), strict=True):
        await scored(session, run, root, verdict=Verdict.PENDING, points=points, confirmed=answer)
    for root in hosts(in_sample=False, number=2, prefix="top"):
        await scored(session, run, root, verdict=Verdict.PENDING, points=3, confirmed=True)
    other = await crawled(session, "other-donor.example.test")
    await scored(session, other, hosts(in_sample=True, number=1, prefix="paid")[0], confirmed=False)
    await session.commit()

    answer = await client.get("/api/advertisers/pending/strata", headers=bearer(token))

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["points"] == [
        {"key": "2", "title": "2", "total": 1, "decided": 1, "write": 1, "skip": 0},
        {"key": "3", "title": "3", "total": 2, "decided": 1, "write": 0, "skip": 1},
    ]
    assert body["features"] == [
        {
            "key": "sponsored",
            "title": "rel=sponsored",
            "total": 3,
            "decided": 2,
            "write": 1,
            "skip": 1,
        }
    ]
    assert body["niches"] == [
        {"key": "finance", "title": "Финансы", "total": 3, "decided": 2, "write": 1, "skip": 1}
    ]


async def test_the_strata_take_decisions_in_turn(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Срезы берут решения, как доля: подряд в порядке выборки. Решённые дальше первой
    нерешённой строки в «пишем» и «не пишем» не идут — строка остаётся в срезе нерешённой."""
    run = await crawled(session, DONOR)
    inside = turn_order(hosts(in_sample=True, number=3, prefix="turn"))
    for root, answer in zip(inside, (None, True, False), strict=True):
        await scored(session, run, root, verdict=Verdict.PENDING, points=3, confirmed=answer)
    await session.commit()

    body = (await client.get("/api/advertisers/pending/strata", headers=bearer(token))).json()

    assert body["points"] == [
        {"key": "3", "title": "3", "total": 3, "decided": 0, "write": 0, "skip": 0}
    ]


async def test_the_strata_route_needs_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/advertisers/pending/strata")).status_code == 403
