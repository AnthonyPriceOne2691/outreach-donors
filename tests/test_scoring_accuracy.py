"""Доля ложных рекламодателей: «куплено» скоринга против «не пишем» человека —
по случайной выборке.

Сверка ТЗ 11.10.2026: требование — «не более 10% ложных рекламодателей», а механика
«решение человека против балла» была, и числа не было нигде. Решение Anthony
12.10.2026: замер честный — по постоянной случайной выборке «куплено» (около пятой
части доменов, по хэшу домена), которую очередь подмешивает между сильными.

Проверяется то, чем замер может соврать: кандидат выборки меняется от запроса
к запросу; выборку решают после всех сильных; доля считается по всем решённым,
а не по выборке; вывод делается на горстке решений.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from itertools import count

import pytest
from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl import sample
from backend.features.crawl.accuracy import FALSE_LIMIT, MIN_DECISIONS, scoring_check
from backend.features.review.accuracy import AUTO_ACCEPT_MIN_DECISIONS
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


async def _bought(
    session: AsyncSession, roots: list[str], *, answers: list[bool | None] | None = None
) -> list[CandidateModel]:
    """«Куплено» на домены `roots` с решениями человека по порядку (`None` — не решали)."""
    run = await crawled(session, f"donor-{roots[0]}")
    decided = answers or [None] * len(roots)
    return [
        await scored(session, run, root, confirmed=answer)
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

    async def test_borderline_queue_is_as_it_was(self, session: AsyncSession) -> None:
        """Спорные в замер не входят — их очередь не перемешивается."""
        run = await crawled(session, "donor.example.test")
        for points, root in ((3, "c.example.test"), (2, "a.example.test")):
            await scored(session, run, root, verdict=Verdict.PENDING, points=points)

        rows = await sample.queue(session, limit=10, verdict=Verdict.PENDING)

        assert [row.target_root for row in rows] == ["c.example.test", "a.example.test"]


class TestTheShare:
    async def test_the_share_is_counted_on_the_sample_alone(self, session: AsyncSession) -> None:
        """Решения вне выборки — сильные, решённые первыми, — в долю не идут."""
        outside = hosts(in_sample=False, number=4, prefix="out")
        inside = hosts(in_sample=True, number=4, prefix="in")
        await _bought(session, outside, answers=[True, True, True, True])
        await _bought(session, inside, answers=[True, True, False, None])

        check = await scoring_check(session)

        assert (check.sample.total, check.sample.decided, check.sample.skip) == (4, 3, 1)
        assert check.of(Verdict.BOUGHT).decided == 7
        assert check.false_share == pytest.approx(1 / 3)
        assert check.within_limit is None

    async def test_no_decisions_in_the_sample_is_no_share(self, session: AsyncSession) -> None:
        """«Ложных нет» и «не смотрели ни одного» — разные новости."""
        await _bought(session, hosts(in_sample=False, number=3), answers=[False, False, True])

        check = await scoring_check(session)

        assert check.false_share is None
        assert check.within_limit is None

    async def test_no_verdict_below_the_judge_sample(self, session: AsyncSession) -> None:
        """Вывод — с той же выборки, что у точности судьи Этапа 1: на одно решение меньше —
        вывода нет; ровно столько — есть, и ровно 10% ещё в допуске."""
        assert MIN_DECISIONS == AUTO_ACCEPT_MIN_DECISIONS == 200
        roots = hosts(in_sample=True, number=MIN_DECISIONS, prefix="many")
        skips = MIN_DECISIONS // 10
        answers: list[bool | None] = [False] * skips + [True] * (MIN_DECISIONS - skips - 1)
        rows = await _bought(session, roots, answers=[*answers, None])

        before = await scoring_check(session)
        rows[-1].confirmed = True
        await session.flush()
        after = await scoring_check(session)

        assert (before.sample.decided, before.within_limit) == (MIN_DECISIONS - 1, None)
        assert after.sample.decided == MIN_DECISIONS
        assert after.false_share == FALSE_LIMIT == 0.10
        assert after.within_limit is True


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
    assert body["scoring"] == {
        "bought": {"total": 1, "decided": 1, "write": 0, "skip": 1},
        "pending": {"total": 0, "decided": 0, "write": 0, "skip": 0},
        "sample": {"total": 1, "decided": 1, "write": 0, "skip": 1},
        "false_share": 1.0,
        "false_limit": 0.1,
        "within_limit": None,
        "min_decisions": 200,
    }
    # Строка выборки — обычная строка: ответ её не помечает.
    assert body["rows"][0]["target_root"] == root
    assert not any("sample" in key for key in body["rows"][0])
