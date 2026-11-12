"""Очередь проверки страницами и с фильтрами «Балл» и «За что» (слово Anthony 05.11.2026).

До этого сервер отдавал первые полсотни, и остальных человек не видел вовсе. Проверяется то, чем
страницы и фильтры могут соврать замеру доли ложных (`crawl/sample.py`): страница 2 начинается не с
того места и строка на стыке теряется или задваивается; пятая строка второй страницы — не пятая во
всём списке; фильтр прошёл мимо выборки; числа шапки сузились вместе с фильтром; совет судьи
вылез до решения; кривой фильтр дошёл до 500.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import pytest
from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl import review
from backend.features.crawl.judge_mark import marker_of
from backend.features.crawl.strata import FEATURES
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored
from tests.test_advertiser_judge_advice import DONOR, _judgement
from tests.test_scoring_accuracy import hosts, turn_order

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ANCHOR = "коммерческий анкор (призыв) +1"
NOFOLLOW = "коммерческая ссылка под nofollow +2"
REPEATED = "ссылки с 12 страниц донора +3"
#: Поясняет, а не считает: балла в конце нет.
EXPLAINS = "ссылки на документы (PDF и т. п.) не в счёт: 2"
TITLES = {one.key: one.title for one in FEATURES}


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("очередь@site.com", role=UserRole.ADMIN)
    return await sign_in("очередь@site.com")


async def _rows(
    session: AsyncSession,
    run: CrawlRunModel,
    roots: Iterable[str],
    *,
    points: int = 3,
    reasons: tuple[str, ...] = (ANCHOR,),
    verdict: Verdict = Verdict.PENDING,
    confirmed: bool | None = None,
) -> list[CandidateModel]:
    """Кандидаты на домены `roots` с причинами, какими их пишет скоринг."""
    made = []
    for root in roots:
        row = await scored(session, run, root, verdict=verdict, points=points, confirmed=confirmed)
        row.reasons = list(reasons)
        made.append(row)
    await session.flush()
    return made


async def _queue(client: AsyncClient, token: str, **params: Any) -> dict[str, Any]:
    answer = await client.get("/api/advertisers", params=params, headers=bearer(token))
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


def _roots(body: dict[str, Any]) -> list[str]:
    return [row["target_root"] for row in body["rows"]]


class TestThePages:
    @pytest.mark.parametrize("verdict", [Verdict.PENDING, Verdict.BOUGHT])
    async def test_page_two_goes_on_where_page_one_stopped(
        self, client: AsyncClient, token: str, session: AsyncSession, verdict: Verdict
    ) -> None:
        """Сквозной порядок: каждая пятая строка всего списка — из выборки, в порядке хэша, хоть балл
        у неё самый низкий; ни одна строка на стыке страниц не теряется и не задваивается."""
        strong = hosts(in_sample=False, number=16, prefix="strong")
        sample = turn_order(hosts(in_sample=True, number=5, prefix="sample"))
        run = await crawled(session, DONOR)
        for points, root in zip(range(40, 24, -1), strong, strict=True):
            await _rows(session, run, [root], points=points, verdict=verdict)
        await _rows(session, run, reversed(sample), points=2, verdict=verdict)
        await session.commit()

        pages = [
            await _queue(client, token, verdict=verdict.value, page=number) for number in (1, 2, 3)
        ]

        assert _roots(pages[0]) == [*strong[:4], sample[0], *strong[4:8], sample[1]]
        assert _roots(pages[1]) == [*strong[8:12], sample[2], *strong[12:16], sample[3]]
        # Сильные кончились — выборка добирает: пустых мест нет.
        assert _roots(pages[2]) == [sample[4]]
        assert [body["total"] for body in pages] == [21, 21, 21]
        assert [(body["page"], body["limit"]) for body in pages] == [(1, 10), (2, 10), (3, 10)]

    async def test_any_page_size_gives_the_same_order(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Равные баллы не меняются местами от страницы к странице: порядок — до номера строки."""
        run = await crawled(session, DONOR)
        await _rows(session, run, hosts(in_sample=False, number=20, prefix="even"))
        await _rows(session, run, hosts(in_sample=True, number=5, prefix="draw"))
        await session.commit()

        whole = _roots(await _queue(client, token, limit=200))
        paged: list[str] = []
        for number in range(1, 5):
            paged += _roots(await _queue(client, token, page=number, limit=7))

        assert paged == whole
        assert len(set(whole)) == 25

    async def test_decisions_down_the_pages_go_into_the_share_in_turn(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Порядок выборки в очереди — тот же, что у доли: человек решает строку выборки там, где
        её показала очередь, на любой странице, — и решение идёт в долю подряд, а не мимо неё."""
        strong = hosts(in_sample=False, number=12, prefix="top")
        sample = turn_order(hosts(in_sample=True, number=3, prefix="turn"))
        run = await crawled(session, DONOR)
        await _rows(session, run, strong)
        made = await _rows(session, run, reversed(sample))
        await session.commit()
        ids = {row.target_root: row.id for row in made}

        fifth = [
            _roots(await _queue(client, token, page=1))[4],
            _roots(await _queue(client, token, page=1))[9],
            _roots(await _queue(client, token, page=2))[4],
        ]
        assert fifth == sample

        for done, root in enumerate(fifth, start=1):
            answer = await client.post(
                f"/api/advertisers/{ids[root]}/decide",
                json={"confirmed": True},
                headers=bearer(token),
            )
            assert answer.status_code == 200, answer.text
            share = (await _queue(client, token))["scoring"]["pending_sample"]
            assert (share["decided"], share["aside"]) == (done, 0)

    async def test_the_sample_stays_unmarked_on_every_page(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        run = await crawled(session, DONOR)
        await _rows(session, run, hosts(in_sample=False, number=12, prefix="plain"))
        await _rows(session, run, hosts(in_sample=True, number=3, prefix="blind"))
        await session.commit()

        for number in (1, 2):
            body = await _queue(client, token, page=number)
            assert all(set(row) == set(body["rows"][0]) for row in body["rows"])
            assert not any("sample" in key for key in body["rows"][0])
            assert set(body["facets"]) == {"listed", "points", "features"}

    async def test_a_page_past_the_end_is_empty_and_says_how_many_there_are(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Решена последняя строка последней страницы: страница за концом пуста, а `total` говорит
        экрану, куда уйти."""
        run = await crawled(session, DONOR)
        rows = await _rows(session, run, hosts(in_sample=False, number=11, prefix="last"))
        await session.commit()
        assert _roots(await _queue(client, token, page=2)) == [rows[-1].target_root]

        await client.post(
            f"/api/advertisers/{rows[-1].id}/decide",
            json={"confirmed": False},
            headers=bearer(token),
        )
        body = await _queue(client, token, page=2)

        assert (body["rows"], body["total"], body["page"]) == ([], 10, 2)
        assert len(_roots(await _queue(client, token, page=1))) == 10

    async def test_the_advice_is_only_under_a_decision_on_any_page(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Совет судьи на второй странице — как на первой: только у решённой строки."""
        run = await crawled(session, DONOR)
        roots = hosts(in_sample=False, number=12, prefix="said")
        decided, waiting = roots[10], roots[11]
        await _rows(session, run, roots[:10])
        await _rows(session, run, [decided], confirmed=False)
        await _rows(session, run, [waiting])
        session.add_all([_judgement(decided), _judgement(waiting)])
        await session.commit()

        body = await _queue(client, token, include_decided="true", page=2)

        cards = {card["target_root"]: card for card in body["rows"]}
        assert set(cards) == {decided, waiting}
        assert cards[decided]["judge"]["advice"] == "write"
        assert cards[waiting]["judge"] is None


class TestTheFilters:
    async def test_the_points_filter_narrows_the_sample_too(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Фильтр сужает обе части: строка выборки с другим баллом не проходит мимо него, а с тем
        же баллом — всё так же пятая среди подошедших."""
        strong_three = hosts(in_sample=False, number=6, prefix="three")
        strong_two = hosts(in_sample=False, number=3, prefix="two")
        sample_three = turn_order(hosts(in_sample=True, number=2, prefix="three-s"))
        sample_two = turn_order(hosts(in_sample=True, number=2, prefix="two-s"))
        run = await crawled(session, DONOR)
        await _rows(session, run, strong_three, points=3)
        await _rows(session, run, strong_two, points=2)
        await _rows(session, run, sample_three, points=3)
        await _rows(session, run, sample_two, points=2)
        await session.commit()

        three = await _queue(client, token, points=3)
        two = await _queue(client, token, points=2)

        assert _roots(three) == [
            *strong_three[:4],
            sample_three[0],
            *strong_three[4:],
            sample_three[1],
        ]
        assert three["total"] == 8
        assert {row["points"] for row in three["rows"]} == {3}
        assert _roots(two) == [*strong_two, *sample_two]
        assert two["total"] == 5

    async def test_the_feature_filter_narrows_the_sample_too(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """У строки признаков бывает несколько, и она находится по каждому; строка выборки без
        признака под фильтр не идёт."""
        strong = hosts(in_sample=False, number=5, prefix="anchor")
        both = hosts(in_sample=False, number=1, prefix="both")
        plain = hosts(in_sample=False, number=2, prefix="plain")
        sample_anchor = hosts(in_sample=True, number=1, prefix="anchor-s")
        sample_plain = hosts(in_sample=True, number=1, prefix="plain-s")
        run = await crawled(session, DONOR)
        await _rows(session, run, strong, reasons=(ANCHOR,))
        await _rows(session, run, both, reasons=(ANCHOR, NOFOLLOW))
        await _rows(session, run, plain, reasons=(NOFOLLOW,))
        await _rows(session, run, sample_anchor, reasons=(ANCHOR,))
        await _rows(session, run, sample_plain, reasons=(NOFOLLOW,))
        await session.commit()

        anchor = await _queue(client, token, feature="anchor")
        nofollow = await _queue(client, token, feature="nofollow")

        assert _roots(anchor) == [*strong[:4], *sample_anchor, strong[4], *both]
        assert anchor["total"] == 7
        assert _roots(nofollow) == [*both, *plain, *sample_plain]

    async def test_pages_go_on_under_a_filter(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        strong = hosts(in_sample=False, number=9, prefix="wanted")
        sample = turn_order(hosts(in_sample=True, number=3, prefix="wanted-s"))
        run = await crawled(session, DONOR)
        await _rows(session, run, strong, reasons=(REPEATED,))
        await _rows(session, run, hosts(in_sample=False, number=6, prefix="other"))
        await _rows(session, run, hosts(in_sample=True, number=2, prefix="other-s"))
        await _rows(session, run, sample, reasons=(REPEATED,))
        await session.commit()

        first = await _queue(client, token, feature="pages")
        second = await _queue(client, token, feature="pages", page=2)

        assert _roots(first) == [*strong[:4], sample[0], *strong[4:8], sample[1]]
        assert _roots(second) == [strong[8], sample[2]]
        assert first["total"] == second["total"] == 12

    async def test_a_reason_is_a_feature_only_with_its_points(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Признак — тем же разбором, что у разбивки выборки (`strata.features_of`): пояснение без
        балла — не признак, причина с баллом не из таблицы — «прочее», а не пропажа."""
        run = await crawled(session, DONOR)
        await _rows(session, run, ["explained.example.test"], reasons=(EXPLAINS,))
        await _rows(session, run, ["new.example.test"], reasons=("совсем новый признак +2",))
        await session.commit()

        other = await _queue(client, token, feature="other")
        everyone = await _queue(client, token)

        assert _roots(other) == ["new.example.test"]
        assert everyone["facets"]["features"] == {
            "total": 2,
            "choices": [{"key": "other", "title": "прочее", "count": 1}],
        }

    async def test_a_decided_row_is_filtered_only_when_asked_for(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        run = await crawled(session, DONOR)
        await _rows(session, run, ["open.example.test"], points=3)
        await _rows(session, run, ["done.example.test"], points=2, confirmed=True)
        await session.commit()

        assert _roots(await _queue(client, token, points=2)) == []
        assert _roots(await _queue(client, token, points=2, include_decided="true")) == [
            "done.example.test"
        ]


def _choices(facet: dict[str, Any]) -> dict[str, int]:
    return {one["key"]: one["count"] for one in facet["choices"]}


class TestTheFacets:
    @pytest.fixture
    async def rows(self, session: AsyncSession) -> None:
        """Балл 3: два с анкором, у одного ещё nofollow. Балл 2: один с nofollow, один без признаков.
        Решённый — только под «показывать решённые»."""
        run = await crawled(session, DONOR)
        await _rows(session, run, ["a.example.test"], points=3, reasons=(ANCHOR,))
        await _rows(session, run, ["b.example.test"], points=3, reasons=(ANCHOR, NOFOLLOW))
        await _rows(session, run, ["c.example.test"], points=2, reasons=(NOFOLLOW,))
        await _rows(session, run, ["d.example.test"], points=2, reasons=(EXPLAINS,))
        await _rows(
            session, run, ["e.example.test"], points=12, reasons=(REPEATED,), confirmed=True
        )
        await session.commit()

    async def test_every_choice_with_its_count_in_table_order(
        self, client: AsyncClient, token: str, rows: None
    ) -> None:
        """Слова признаков — те же, что у разбивки спорных; балл «12» — после «3», а не раньше."""
        facets = (await _queue(client, token))["facets"]

        assert facets["listed"] == 4
        assert facets["points"] == {
            "total": 4,
            "choices": [
                {"key": "2", "title": "2", "count": 2},
                {"key": "3", "title": "3", "count": 2},
            ],
        }
        assert facets["features"] == {
            "total": 4,
            "choices": [
                {"key": "anchor", "title": TITLES["anchor"], "count": 2},
                {"key": "nofollow", "title": TITLES["nofollow"], "count": 2},
            ],
        }
        decided = (await _queue(client, token, include_decided="true"))["facets"]
        assert decided["listed"] == 5
        assert [one["key"] for one in decided["points"]["choices"]] == ["2", "3", "12"]
        assert _choices(decided["features"])["pages"] == 1

    async def test_a_count_holds_all_conditions_but_its_own(
        self, client: AsyncClient, token: str, rows: None
    ) -> None:
        """Выбранный балл не обнуляет соседние баллы, а признаки считает под собой — и наоборот."""
        by_points = (await _queue(client, token, points=3))["facets"]
        by_feature = (await _queue(client, token, feature="nofollow"))["facets"]

        assert (by_points["points"]["total"], _choices(by_points["points"])) == (
            4,
            {"2": 2, "3": 2},
        )
        assert (by_points["features"]["total"], _choices(by_points["features"])) == (
            2,
            {"anchor": 2, "nofollow": 1},
        )
        assert (by_feature["points"]["total"], _choices(by_feature["points"])) == (
            2,
            {"2": 1, "3": 1},
        )
        assert (by_feature["features"]["total"], _choices(by_feature["features"])) == (
            4,
            {"anchor": 2, "nofollow": 2},
        )

    async def test_a_choice_stays_when_a_neighbour_leaves_it_at_zero(
        self, client: AsyncClient, token: str, rows: None
    ) -> None:
        body = await _queue(client, token, points=2, feature="anchor")

        assert (body["rows"], body["total"]) == ([], 0)
        assert body["facets"]["listed"] == 4
        assert _choices(body["facets"]["points"]) == {"2": 0, "3": 2}
        assert _choices(body["facets"]["features"]) == {"anchor": 0, "nofollow": 1}


async def test_the_numbers_of_the_head_do_not_follow_the_filter_or_the_page(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """`waiting`, `counts`, `scoring` — по всем: числа в шапке экрана от фильтра и страницы не
    меняются. Строка выборки решена — видно, что считается настоящее."""
    run = await crawled(session, DONOR)
    await _rows(session, run, hosts(in_sample=False, number=12, prefix="head"), points=3)
    [root, *rest] = turn_order(hosts(in_sample=True, number=3, prefix="head-s"))
    await _rows(session, run, [root], points=2, confirmed=False)
    await _rows(session, run, rest, points=2)
    await _rows(session, run, ["paid.example.test"], points=9, verdict=Verdict.BOUGHT)
    await session.commit()

    plain = await _queue(client, token)
    narrowed = await _queue(client, token, points=2, feature="anchor", page=2, limit=1)
    nothing = await _queue(client, token, points=77)

    head = ("waiting", "counts", "scoring")
    assert plain["waiting"] == 14
    assert plain["counts"] == {"bought": 1, "pending": 15}
    assert plain["scoring"]["pending_sample"]["total"] == 3
    assert plain["scoring"]["pending_sample"]["decided"] == 1
    for body in (narrowed, nothing):
        assert {key: body[key] for key in head} == {key: plain[key] for key in head}


async def test_the_head_says_how_many_of_the_decided_the_judge_decided(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """«Решено» шапки — и людьми, и судьёй (с 11.11.2026): сколько из них судьёй, говорит `judged`.
    Решение судьи, переигранное человеком, — уже человека; «куплено» в число не идёт, как и в
    «решено»; фильтр числа не меняет."""
    run = await crawled(session, DONOR)
    judge = marker_of("skip-20261111-093015-a3f9")
    [card, replayed] = await _rows(
        session, run, ["card.example.test", "replayed.example.test"], confirmed=False
    )
    [paid] = await _rows(
        session, run, ["paid.example.test"], points=9, verdict=Verdict.BOUGHT, confirmed=False
    )
    card.decided_by = replayed.decided_by = paid.decided_by = judge
    await _rows(session, run, ["person.example.test"], confirmed=True)
    await _rows(session, run, ["open.example.test"])
    await session.commit()

    before = await _queue(client, token)
    narrowed = await _queue(client, token, points=77)
    await review.decide(session, replayed.id, confirmed=True, by="оператор@site.com", force=True)
    await session.commit()
    after = await _queue(client, token)

    assert (before["waiting"], before["counts"]["pending"], before["judged"]) == (1, 4, 2)
    assert narrowed["judged"] == 2
    assert (after["waiting"], after["judged"]) == (1, 1)


class TestCrookedQueries:
    @pytest.mark.parametrize(
        "query",
        [
            "limit=0",
            f"limit={review.MAX_PAGE_SIZE + 1}",
            "page=0",
            "page=1000001",
            "points=abc",
            "points=1.5",
            f"feature={'x' * 65}",
            "verdict=skipped",
            "verdict=nonsense",
            "include_decided=maybe",
            "search=shop",
        ],
    )
    async def test_refused_in_words(self, client: AsyncClient, token: str, query: str) -> None:
        answer = await client.get(f"/api/advertisers?{query}", headers=bearer(token))

        assert answer.status_code == 422, answer.text
        detail = answer.json()["detail"]
        said = detail if isinstance(detail, str) else detail[0]["msg"]
        # Слова — по-русски: экран показывает их как есть.
        assert any("а" <= letter.lower() <= "я" for letter in said), said

    @pytest.mark.parametrize(
        "query",
        ["points=99", "points=-1", "points=99999999999999999999", "feature=zzz", "feature=%00"],
    )
    async def test_nothing_of_the_kind_is_an_empty_list(
        self, client: AsyncClient, token: str, session: AsyncSession, query: str
    ) -> None:
        """Балла и признака, которых нет, ни у кого нет: пустой список, а не 500 и не вся очередь."""
        run = await crawled(session, DONOR)
        await _rows(session, run, ["one.example.test"])
        await session.commit()

        answer = await client.get(f"/api/advertisers?{query}", headers=bearer(token))

        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert (body["rows"], body["total"], body["facets"]["listed"]) == ([], 0, 1)
        assert body["waiting"] == 1

    async def test_the_limit_has_a_ceiling_and_the_default_is_ten(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        run = await crawled(session, DONOR)
        await _rows(session, run, hosts(in_sample=False, number=11, prefix="ten"))
        await session.commit()

        body = await _queue(client, token)
        wide = await _queue(client, token, limit=review.MAX_PAGE_SIZE)

        assert (len(body["rows"]), body["limit"], body["total"]) == (review.PAGE_SIZE, 10, 11)
        assert (len(wide["rows"]), wide["limit"]) == (11, review.MAX_PAGE_SIZE)
