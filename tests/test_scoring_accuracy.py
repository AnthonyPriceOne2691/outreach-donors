"""Доля ложных рекламодателей: «куплено» скоринга против «не пишем» человека —
по случайной выборке.

Сверка ТЗ 11.10.2026: требование — «не более 10% ложных рекламодателей», а механика
«решение человека против балла» была, и числа не было нигде. Решение Anthony
12.10.2026: замер честный — по постоянной случайной выборке «куплено» (около пятой
части доменов, по хэшу домена), которую очередь подмешивает между сильными.

Проверяется то, чем замер может соврать: кандидат выборки меняется от запроса
к запросу; выборку решают после всех сильных; доля считается по всем решённым,
а не по выборке; вывод делается на горстке решений.

Вывод — по границам доли (03.11.2026): в допуске — верхняя граница не выше 10 %, вне
допуска — нижняя выше, иначе «не доказано» и сколько решений ещё нужно. Спорные меряются
той же выборкой: их доля «не пишем» — ошибка базы «писать всем спорным».
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from itertools import count

import pytest
from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl import sample
from backend.features.crawl.accuracy import (
    FALSE_LIMIT,
    Decisions,
    Within,
    in_turn,
    scoring_check,
    share_of,
)
from backend.shared import stats
from httpx import AsyncClient
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


def hosts(*, in_sample: bool, number: int, prefix: str = "site") -> list[str]:
    """Домены, которые в выборке (или нет), — по самому правилу, а не списком."""
    found: list[str] = []
    for index in count():
        host = f"{prefix}-{index}.example.test"
        if sample.sampled(host) is in_sample:
            found.append(host)
        if len(found) == number:
            return found
    raise AssertionError("недостижимо")


def turn_order(roots: list[str]) -> list[str]:
    """Домены в порядке выборки — md5 имени, как у очереди и у доли (`accuracy.in_turn`)."""
    return sorted(
        roots, key=lambda root: hashlib.md5(root.encode(), usedforsecurity=False).hexdigest()
    )


async def _bought(
    session: AsyncSession,
    roots: list[str],
    *,
    answers: list[bool | None] | None = None,
    verdict: Verdict = Verdict.BOUGHT,
) -> list[CandidateModel]:
    """Кандидаты на домены `roots` (по умолчанию «куплено») с решениями человека по порядку
    (`None` — не решали)."""
    run = await crawled(session, f"donor-{roots[0]}")
    decided = answers or [None] * len(roots)
    return [
        await scored(session, run, root, verdict=verdict, confirmed=answer)
        for root, answer in zip(roots, decided, strict=True)
    ]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("скоринг@site.com", role=UserRole.ADMIN)
    return await sign_in("скоринг@site.com")


class TestTheSample:
    async def test_a_domain_is_in_the_sample_for_good(self, session: AsyncSession) -> None:
        """Признак считается, а не хранится: питон и база отвечают одинаково, и тот же
        домен — тот же ответ, сколько ни спрашивай."""
        mixed_hosts = hosts(in_sample=True, number=5) + hosts(in_sample=False, number=5)
        expected = [sample.sampled(host) for host in mixed_hosts]

        row = (
            await session.execute(select(*(sample.in_sample(literal(h)) for h in mixed_hosts)))
        ).one()

        assert list(row) == expected
        assert [sample.sampled(host) for host in mixed_hosts] == expected
        assert expected.count(True) == 5

    def test_about_a_fifth_of_the_domains(self) -> None:
        picked = sum(sample.sampled(f"domain-{n}.example.test") for n in range(20_000))

        assert 0.18 < picked / 20_000 < 0.22

    async def test_the_same_domain_from_two_donors_is_in_or_out_together(
        self, session: AsyncSession
    ) -> None:
        [root] = hosts(in_sample=True, number=1)
        await _bought(session, [root])
        await scored(session, await crawled(session, "second-donor.example.test"), root)

        flags = (
            await session.scalars(
                select(sample.in_sample()).where(CandidateModel.target_root == root)
            )
        ).all()

        assert flags == [True, True]


class TestTheQueue:
    async def test_every_fifth_row_of_bought_is_from_the_sample(
        self, session: AsyncSession
    ) -> None:
        """Выборку решают среди сильных, а не после них: слабые кандидаты выборки
        стоят пятой и десятой строкой, хоть балл у них меньше всех."""
        strong = hosts(in_sample=False, number=8, prefix="strong")
        weak = hosts(in_sample=True, number=2, prefix="weak")
        run = await crawled(session, "donor.example.test")
        for points, root in zip(range(20, 12, -1), strong, strict=True):
            await scored(session, run, root, points=points)
        for root in weak:
            await scored(session, run, root, points=4)

        rows = await sample.queue(session, limit=10, verdict=Verdict.BOUGHT)

        roots = [row.target_root for row in rows]
        assert {roots[4], roots[9]} == set(weak)
        assert [root for root in roots if root in strong] == strong

    async def test_the_sample_runs_out_and_the_strong_fill_the_rest(
        self, session: AsyncSession
    ) -> None:
        strong = hosts(in_sample=False, number=6, prefix="only")
        await _bought(session, strong)

        rows = await sample.queue(session, limit=5, verdict=Verdict.BOUGHT)

        assert len(rows) == 5
        assert all(not sample.sampled(row.target_root) for row in rows)

    async def test_every_fifth_row_of_disputed_is_from_the_sample(
        self, session: AsyncSession
    ) -> None:
        """Спорные — тем же правилом (02.11.2026): решения легли на верх очереди по баллу, и
        выборку решают среди трёхбалльных, а не после всех. Двухбалльные выборки стоят пятой и
        десятой строкой; остальные — по баллу вниз, как были."""
        strong = hosts(in_sample=False, number=8, prefix="three")
        weak = hosts(in_sample=True, number=2, prefix="two")
        run = await crawled(session, "donor.example.test")
        for root in strong:
            await scored(session, run, root, verdict=Verdict.PENDING, points=3)
        for root in weak:
            await scored(session, run, root, verdict=Verdict.PENDING, points=2)

        rows = await sample.queue(session, limit=10, verdict=Verdict.PENDING)

        roots = [row.target_root for row in rows]
        assert {roots[4], roots[9]} == set(weak)
        assert [root for root in roots if root in strong] == strong

    async def test_the_disputed_sample_goes_by_its_hash_not_by_points(
        self, session: AsyncSession
    ) -> None:
        """Выборка решается в порядке хэша домена: иначе её снова решали бы от сильных."""
        picked = hosts(in_sample=True, number=3, prefix="mix")
        run = await crawled(session, "donor.example.test")
        for points, root in zip((2, 3, 2), picked, strict=True):
            await scored(session, run, root, verdict=Verdict.PENDING, points=points)

        rows = await sample.queue(session, limit=10, verdict=Verdict.PENDING)

        assert [row.target_root for row in rows] == sorted(
            picked, key=lambda host: hashlib.md5(host.encode(), usedforsecurity=False).hexdigest()
        )

    async def test_decided_disputed_leave_the_queue(self, session: AsyncSession) -> None:
        picked = hosts(in_sample=True, number=2, prefix="done")
        await _bought(session, picked, answers=[True, None], verdict=Verdict.PENDING)

        rows = await sample.queue(session, limit=10, verdict=Verdict.PENDING)
        everything = await sample.queue(
            session, limit=10, verdict=Verdict.PENDING, include_decided=True
        )

        assert [row.target_root for row in rows] == [picked[1]]
        assert {row.target_root for row in everything} == set(picked)


class TestTheShare:
    async def test_the_share_is_counted_on_the_sample_alone(self, session: AsyncSession) -> None:
        """Решения вне выборки — сильные, решённые первыми, — в долю не идут."""
        outside = hosts(in_sample=False, number=4, prefix="out")
        inside = turn_order(hosts(in_sample=True, number=4, prefix="in"))
        await _bought(session, outside, answers=[True, True, True, True])
        await _bought(session, inside, answers=[True, True, False, None])

        check = await scoring_check(session)

        assert (check.sample.total, check.sample.decided, check.sample.skip) == (4, 3, 1)
        assert check.of(Verdict.BOUGHT).decided == 7
        assert check.false_share == pytest.approx(1 / 3)
        assert check.within_limit is Within.UNPROVEN

    async def test_decisions_out_of_turn_are_counted_aside(self, session: AsyncSession) -> None:
        """Решения до подмешивания выборки легли на верх очереди: строки выборки, решённые не по
        её порядку, в долю не идут — их видно числом, а вывод их не берёт (03.11.2026). Иначе
        три «пишем» сверху дали бы «писать всем спорным» по выбору человека, а не жребия."""
        roots = turn_order(hosts(in_sample=True, number=4, prefix="late"))
        await _bought(session, roots, answers=[None, True, True, True], verdict=Verdict.PENDING)

        check = await scoring_check(session)

        assert (check.disputed.decisions.decided, check.disputed.aside) == (0, 3)
        assert check.disputed.within is Within.UNMEASURED
        assert check.disputed.to_go == 29

    async def test_the_turn_reaches_the_decided_ones(self, session: AsyncSession) -> None:
        """Решённое не по порядку входит в долю, как только решены строки выборки перед ним."""
        roots = turn_order(hosts(in_sample=True, number=3, prefix="gap"))
        rows = await _bought(session, roots, answers=[True, None, False])

        before = await scoring_check(session)
        rows[1].confirmed = True
        await session.flush()
        after = await scoring_check(session)

        assert (before.bought.decisions.decided, before.bought.aside) == (1, 1)
        assert (after.bought.decisions.decided, after.bought.aside) == (3, 0)
        assert after.bought.decisions.skip == 1

    async def test_no_decisions_in_the_sample_is_no_share(self, session: AsyncSession) -> None:
        """«Ложных нет» и «не смотрели ни одного» — разные новости."""
        await _bought(session, hosts(in_sample=False, number=3), answers=[False, False, True])

        check = await scoring_check(session)

        assert check.false_share is None
        assert check.within_limit is Within.UNMEASURED

    async def test_the_verdict_comes_from_the_bound_not_from_two_hundred(
        self, session: AsyncSession
    ) -> None:
        """До 03.11.2026 вывода не было до 200 решений выборки — у «куплено» их не набралось бы
        никогда. Теперь 29 решений без «не пишем» — уже «в допуске», 28 — ещё нет."""
        roots = turn_order(hosts(in_sample=True, number=29, prefix="clean"))
        rows = await _bought(session, roots, answers=[*([True] * 28), None])

        before = await scoring_check(session)
        rows[-1].confirmed = True
        await session.flush()
        after = await scoring_check(session)

        assert (before.sample.decided, before.within_limit) == (28, Within.UNPROVEN)
        assert before.bought.to_go == 1
        assert (after.sample.decided, after.within_limit) == (29, Within.YES)
        assert after.bought.to_go is None

    async def test_ten_percent_seen_is_not_ten_percent_proven(self, session: AsyncSession) -> None:
        """Ровно 10 % «не пишем» среди решённых — доля на границе допуска, а не под ней:
        верхняя граница выше, вывода нет."""
        roots = hosts(in_sample=True, number=40, prefix="edge")
        await _bought(session, roots, answers=[*([False] * 4), *([True] * 36)])

        check = await scoring_check(session)

        assert check.false_share == FALSE_LIMIT == 0.10
        assert check.within_limit is Within.UNPROVEN
        assert check.bought.high is not None
        assert check.bought.high > FALSE_LIMIT

    async def test_many_false_ones_are_proven_outside(self, session: AsyncSession) -> None:
        """8 «не пишем» из 30 — нижняя граница выше 10 %: доказанно вне допуска, и «сколько
        ещё решить» тут не утешение — его нет."""
        roots = hosts(in_sample=True, number=30, prefix="bad")
        await _bought(session, roots, answers=[*([False] * 8), *([True] * 22)])

        check = await scoring_check(session)

        assert check.within_limit is Within.NO
        assert check.bought.low == pytest.approx(stats.lower_bound(8, 30))
        assert check.bought.to_go is None


def test_in_turn_takes_the_run_up_to_the_first_gap() -> None:
    decisions, aside = in_turn([True, False, None, True, None, False])

    assert (decisions.total, decisions.write, decisions.skip, aside) == (6, 1, 1, 2)
    assert in_turn([]) == (Decisions(), 0)
    assert in_turn([True, True]) == (Decisions(total=2, write=2), 0)


class TestShareOf:
    """Вывод и «сколько ещё» — без базы, на числах."""

    def test_three_decisions_say_nothing_yet(self) -> None:
        """Случай вроде нынешнего «куплено» (числа выдуманы): решений три, одно из них «не
        пишем». Вывода нет, и для «в допуске» нужно ещё 43 решения без «не пишем»: в выборке
        из 30 строк их не набрать, из 60 — набрать."""
        small = share_of(Decisions(total=30, write=2, skip=1))
        large = share_of(Decisions(total=60, write=2, skip=1))

        assert small.within is Within.UNPROVEN
        assert small.to_go == 43
        assert (small.decisions.left, small.reachable) == (27, False)
        assert large.reachable is True

    def test_no_decisions_need_twenty_nine(self) -> None:
        """Решений нет: мерить нечем, а для вывода нужно 29 решений без «не пишем»."""
        empty = share_of(Decisions(total=100))

        assert (empty.within, empty.share, empty.low, empty.high) == (
            Within.UNMEASURED,
            None,
            None,
            None,
        )
        assert empty.to_go == 29

    def test_the_bounds_hold_the_share(self) -> None:
        share = share_of(Decisions(total=200, write=150, skip=50))

        assert share.low is not None
        assert share.high is not None
        assert share.low < 0.25 < share.high
        assert share.within is Within.NO


class TestDisputedSample:
    async def test_the_baseline_is_counted_on_the_disputed_sample_alone(
        self, session: AsyncSession
    ) -> None:
        """База «писать всем спорным» ошибается на доле «не пишем» выборки спорных: решения
        вне выборки — верх очереди — в неё не идут, «куплено» — тоже."""
        outside = hosts(in_sample=False, number=3, prefix="top")
        inside = turn_order(hosts(in_sample=True, number=4, prefix="rand"))
        await _bought(session, outside, answers=[True, True, True], verdict=Verdict.PENDING)
        await _bought(session, inside, answers=[True, False, False, None], verdict=Verdict.PENDING)
        await _bought(
            session, hosts(in_sample=True, number=2, prefix="paid"), answers=[False, False]
        )

        check = await scoring_check(session)

        disputed = check.disputed.decisions
        assert (disputed.total, disputed.decided, disputed.skip) == (4, 3, 2)
        assert check.disputed.share == pytest.approx(2 / 3)
        assert check.of(Verdict.PENDING).decided == 6
        assert check.sample.decided == 2


async def test_the_queue_carries_the_sample_share_and_the_limit(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Доля — в ответе очереди, где принимаются решения: «не пишем» кандидату выборки
    сдвигает её сразу, а какие строки в выборке, ответ не говорит."""
    [root] = hosts(in_sample=True, number=1)
    [candidate] = await _bought(session, [root])
    await session.commit()

    decided = await client.post(
        f"/api/advertisers/{candidate.id}/decide", json={"confirmed": False}, headers=bearer(token)
    )
    body = (
        await client.get(
            "/api/advertisers?verdict=bought&include_decided=true", headers=bearer(token)
        )
    ).json()

    assert decided.status_code == 200, decided.text
    scoring = body["scoring"]
    assert scoring["bought"] == {"total": 1, "decided": 1, "write": 0, "skip": 1}
    assert scoring["pending"] == {"total": 0, "decided": 0, "write": 0, "skip": 0}
    assert scoring["false_limit"] == 0.1
    # Одно «не пишем» из одного решения — доля 100 %, а доказано лишь «не меньше 5 %»:
    # вывода нет, и для «в допуске» нужно ещё 45 решений без «не пишем».
    assert scoring["bought_sample"] == {
        "total": 1,
        "decided": 1,
        "write": 0,
        "skip": 1,
        "left": 0,
        "aside": 0,
        "share": 1.0,
        "low": pytest.approx(0.05),
        "high": 1.0,
        "within": "unproven",
        "to_go": 45,
    }
    assert scoring["pending_sample"] == {
        "total": 0,
        "decided": 0,
        "write": 0,
        "skip": 0,
        "left": 0,
        "aside": 0,
        "share": None,
        "low": None,
        "high": None,
        "within": "unmeasured",
        "to_go": 29,
    }
    # Строка выборки — обычная строка: ответ её не помечает.
    assert body["rows"][0]["target_root"] == root
    assert not any("sample" in key for key in body["rows"][0])


async def test_the_disputed_queue_does_not_mark_the_sample(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Спорные выборки в ответе очереди — обычные строки: решение не должно зависеть от того,
    что идёт в замер."""
    inside = hosts(in_sample=True, number=1, prefix="blind")
    outside = hosts(in_sample=False, number=4, prefix="plain")
    await _bought(session, [*outside, *inside], verdict=Verdict.PENDING)
    await session.commit()

    body = (await client.get("/api/advertisers", headers=bearer(token))).json()

    assert [row["target_root"] for row in body["rows"]][4] == inside[0]
    assert all(set(row) == set(body["rows"][0]) for row in body["rows"])
    assert not any("sample" in key for key in body["rows"][0])
    assert body["scoring"]["pending_sample"]["total"] == 1
