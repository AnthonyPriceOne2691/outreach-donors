"""Прогон со смещением страниц и заведение прогона одной функцией (`segment_launch`).

Углубление плана работ покупает страницы `from_page..depth_pages`: смета считает выдачу и пишет
слова по покупаемым страницам, проверка счёта перед покупкой — тоже, а выдача получает первую
страницу (`start_page`). С первой страницы — всё прежнее. Первая страница вне `1..depth_pages` —
отказ сметы словами, и прогон по такой смете не заводится. Заведение — строка прогона со
страницами и гипотезой плана, одна задача очереди и запись журнала: с автором — как у
«Запустить», без автора (конвейер) — «автоматически».

Очередь — подделка с правилами rq (`test_sales_clean_api._Jobs`), Redis не трогается; выдача —
подделка, которая помнит, что ей передали, и называет остаток счёта. Ключи — трёх ниш и
письменностей; числа выдуманы и не круглы.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from backend.config import serp as serp_cfg
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import segment_serp
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_models import SalesPlanHypothesisModel, SalesPlanModel
from backend.features.sales.segment_estimate import FIRST_PAGE, estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_launch import AUTO, STARTED, LaunchRefusedError, launch
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_serp import collect
from backend.features.serp import funds
from backend.features.serp.modes import SerpMode, serp_cost_usd
from backend.features.serp.protocol import SerpResult
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import DuplicateJobError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Job, _Jobs
from tests.test_sales_segment_estimate import _settings, ask, collected, past_run
from tests.test_sales_segment_run import hypothesis

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов

MakeUser = Callable[..., Awaitable[UserModel]]

#: Велосипеды кириллицей, стоматологи латиницей, кафе — японским письмом.
WORDS = ("купить горный велосипед", "zahnarzt berlin mitte", "東京 カフェ 予約")
KEYWORDS = "\n".join(WORDS)
#: Глубина прогонов здесь — пять страниц: топ-50.
DEPTH = 5
SELLER = "seller@ours.example.test"


@dataclass
class Pages:
    """Выдача-подделка со счётом: помнит доводы каждого вызова; ответ — пустой по каждому ключу."""

    left: float = 61.9
    name: str = "fake"
    spent: float = 0.0
    named: list[dict[str, Any]] = field(default_factory=list)

    async def search(
        self, keywords: Sequence[str], _country: str, **named: Any
    ) -> dict[str, list[SerpResult]]:
        self.named.append({key: value for key, value in named.items() if key != "watch"})
        return {keyword: [] for keyword in keywords}

    async def balance(self) -> float:
        return self.left


class _Down(_Jobs):
    """Redis не отвечает."""

    def fetch_job(self, job_id: str) -> _Job | None:
        raise RedisConnectionError("очередь не отвечает")


class _Taken(_Jobs):
    """Номер задачи держит другая постановка: rq отвечает «уже есть»."""

    def enqueue(self, *_args: object, **kwargs: object) -> _Job:
        raise DuplicateJobError(f"Job with ID '{kwargs['job_id']}' already exists")


async def deep_run(
    session: AsyncSession, owner: SalesHypothesisModel, *, first: int
) -> SalesSegmentRunModel:
    """Прогон на топ-50 с первой покупаемой страницей `first`."""
    run = SalesSegmentRunModel(
        hypothesis_id=owner.id,
        keywords=list(WORDS),
        country="us",
        language="en",
        depth_pages=DEPTH,
        from_page=first,
        mode="shadow",
        estimate={"lines": ["смета"]},
        report={},
    )
    session.add(run)
    await session.commit()
    return run


async def sheet_of(session: AsyncSession, owner: SalesHypothesisModel) -> SalesPlanHypothesisModel:
    """Лист плана работ на гипотезу: прогон конвейера знает, чей он."""
    plan = SalesPlanModel(
        name="план недели",
        mode="economy",
        cap_usd=Decimal("48.70"),
        estimate={},
        digest="d" * 16,  # отпечаток превью плана: сам по себе прогону не нужен
    )
    session.add(plan)
    await session.flush()
    sheet = SalesPlanHypothesisModel(
        plan_id=plan.id,
        hypothesis_id=owner.id,
        position=1,
        sheet="Велосипеды",
        markets=["us"],
        language="en",
        target=310,
        max_pages=DEPTH,
        cap_usd=Decimal("17.30"),
    )
    session.add(sheet)
    await session.flush()
    return sheet


async def runs(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) or 0)


async def started(session: AsyncSession) -> list[AuditLogModel]:
    found = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    return list(found.all())


# --- смета ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "pages", "which", "cost"),
    [
        (1, 5, "", "$0,0105"),
        (2, 4, " (страницы 2–5)", "$0,0084"),
        (4, 2, " (страницы 4–5)", "$0,0042"),
        (5, 1, " (страница 5)", "$0,0021"),
    ],
    ids=["from-the-top", "from-page-2", "from-page-4", "the-last-page"],
)
async def test_the_estimate_prices_only_the_pages_the_run_buys(
    session: AsyncSession, first: int, pages: int, which: str, cost: str
) -> None:
    """Топ-50 со страницы 4 — две страницы на ключ: результатов, сайтов и денег — за них, а строка
    сметы называет какие. С первой страницы — строка прежняя, без скобок."""
    owner = await hypothesis(session)

    found = await estimate(
        session, ask(owner.id, keywords=KEYWORDS, depth_pages=DEPTH, from_page=first)
    )

    assert found.refusal is None
    assert (found.from_page, found.depth_pages) == (first, DEPTH)
    assert found.results == len(WORDS) * pages * 10
    assert found.serp_usd == serp_cost_usd(len(WORDS), pages, SerpMode.STANDARD)
    assert found.hosts == round(found.results * 0.85)
    assert found.lines[0] == (
        f"Выдача — стандарт: ключей 3 × страниц {pages}{which} — результатов до "
        f"{pages * 30}, по $0,0007 за страницу ≈ {cost}; ждать ≈ 5 мин."
    )


async def test_a_deeper_run_does_not_subtract_the_sites_of_the_first_pages(
    session: AsyncSession,
) -> None:
    """Сайты, которые гипотеза собрала теми же ключами, пришли с первых страниц: новые страницы
    их почти не дадут, и углубление их не вычитает; с первой страницы — вычитает, как прежде."""
    owner = await hypothesis(session)
    before = await past_run(session, owner.id, keywords=WORDS[:1])
    await collected(session, before, WORDS[0], 5)

    top = await estimate(session, ask(owner.id, keywords=WORDS[0], depth_pages=2))
    deeper = await estimate(session, ask(owner.id, keywords=WORDS[0], depth_pages=2, from_page=2))

    assert (top.hosts, top.known, top.sites) == (17, 5, 12)
    assert (deeper.hosts, deeper.known, deeper.sites) == (8, 0, 8)


async def test_the_history_share_counts_only_the_pages_a_run_bought(
    session: AsyncSession,
) -> None:
    """Прогон углубления купил страницы 2–3: его 13 сайтов — на 40 запрошенных результатов
    (2 ключа × 2 страницы × 10), а не на 60 всей глубины."""
    owner = await hypothesis(session)
    deeper = await past_run(session, owner.id, keywords=WORDS[:2], depth=3, hosts=13)
    deeper.from_page = 2
    await session.flush()

    found = await estimate(session, ask(owner.id))

    assert (found.share, found.share_runs) == (pytest.approx(13 / 40), 1)


@pytest.mark.parametrize("first", [0, -2, 4], ids=["zero", "negative", "beyond-the-depth"])
async def test_a_first_page_outside_the_depth_is_refused_before_any_money(
    session: AsyncSession, first: int
) -> None:
    owner = await hypothesis(session)
    jobs = _Jobs()

    found = await estimate(session, ask(owner.id, depth_pages=3, from_page=first))

    assert found.refusal == FIRST_PAGE.format(depth=3)
    assert (found.results, found.serp_usd, found.lines) == (0, 0.0, [])
    with pytest.raises(LaunchRefusedError, match=re.escape(FIRST_PAGE.format(depth=3))):
        await launch(session, owner.id, found, jobs=jobs)
    assert (await runs(session), jobs.enqueued, await started(session)) == (0, [], [])


async def test_a_bad_depth_is_named_before_the_first_page(session: AsyncSession) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, depth_pages=11, from_page=12))

    assert found.refusal == "глубина выдачи — от 1 до 10 страниц по 10 результатов"


# --- выдача и счёт перед покупкой ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "named"),
    [(1, {"depth_pages": DEPTH}), (4, {"depth_pages": DEPTH, "start_page": 4})],
    ids=["from-the-top", "from-page-4"],
)
async def test_the_serp_buys_from_the_first_page_of_the_run(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, first: int, named: dict[str, int]
) -> None:
    """`gather_candidates` получает первую страницу прогона; источнику она уходит, только когда
    это не первая: вызов выдачи у прогона с первой страницы — прежний."""
    asked: list[dict[str, Any]] = []
    gather = segment_serp.gather_candidates

    async def spy(*args: Any, **options: Any) -> Any:
        asked.append({key: value for key, value in options.items() if key != "watch"})
        return await gather(*args, **options)

    monkeypatch.setattr(segment_serp, "gather_candidates", spy)
    run = await deep_run(session, await hypothesis(session), first=first)
    serp = Pages()

    await collect(session, run, serp)

    assert asked == [{"depth_pages": DEPTH, "start_page": first}]
    assert serp.named == [named]


async def test_the_account_check_counts_only_the_pages_the_run_buys(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Страницы 4–5 из пяти: 3 ключа × 2 страницы × 2,90 $ = 17,40 $, а не 43,50 $ всей глубины —
    остатка 19,90 $ хватает, и выдача покупается."""
    monkeypatch.setattr(serp_cfg, "MODE", "standard")
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", 2.9)
    run = await deep_run(session, await hypothesis(session), first=4)
    serp = Pages(left=19.9)

    await collect(session, run, serp)

    assert serp.named == [{"depth_pages": DEPTH, "start_page": 4}]


async def test_a_short_account_is_refused_by_the_price_of_the_pages_bought(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остатка 15,30 $ на страницы 4–5 не хватает: отказ до покупки называет их цену — 17,40 $."""
    monkeypatch.setattr(serp_cfg, "MODE", "standard")
    monkeypatch.setattr(serp_cfg, "PRICE_PER_KEYWORD_USD", 2.9)
    run = await deep_run(session, await hypothesis(session), first=4)
    serp = Pages(left=15.3)

    with pytest.raises(funds.FundsTooLowError) as refused:
        await collect(session, run, serp)

    assert str(refused.value) == (
        f"Выдача этого прогона по смете ≈ {funds.dollars(17.4)}, а на счету DataForSEO "
        f"{funds.dollars(15.3)}: пополните счёт DataForSEO."
    )
    assert serp.named == []


# --- заведение прогона ---------------------------------------------------------------------------


async def test_the_conveyor_launches_a_deeper_run_of_its_plan_hypothesis(
    session: AsyncSession,
) -> None:
    """Без автора — конвейер: строка прогона со страницами 2–3 и гипотезой плана, ровно одна задача
    очереди под номером прогона и запись журнала «автоматически» без автора."""
    owner = await hypothesis(session)
    sheet = await sheet_of(session, owner)
    found = await estimate(session, ask(owner.id, depth_pages=3, from_page=2))
    jobs = _Jobs()

    launched = await launch(session, owner.id, found, jobs=jobs, plan_hypothesis_id=sheet.id)

    assert launched is not None
    run = launched.run
    await session.refresh(run)
    number = segment_job_id(run.id)
    assert (run.from_page, run.depth_pages, run.plan_hypothesis_id) == (2, 3, sheet.id)
    assert (run.stage, run.created_by, run.job_id, launched.job_id) == (
        "queued",
        None,
        number,
        number,
    )
    assert run.estimate == found.as_dict()
    [(args, options)] = jobs.enqueued
    assert args == (SEGMENT_JOB, run.id)
    assert (options["job_id"], options["unique"], "retry" in options) == (number, True, True)
    [journal] = await started(session)
    assert (journal.user_id, journal.target) == (None, f"job:{number}")
    assert journal.details == {
        "этап": "sales",
        "действие": AUTO,
        "гипотеза": "сайты EN",
        "ключей": 3,
        "рынок": "us",
        "смета": " ".join(found.lines),
        "гипотеза плана": sheet.id,
    }
    assert " (страницы 2–3) " in journal.details["смета"]


async def test_a_person_launches_from_the_first_page_as_the_screen_did(
    session: AsyncSession, make_user: MakeUser
) -> None:
    """С автором — как у «Запустить»: почта в строке прогона, автор и «сбор базы продаж» в журнале;
    страницы — с первой, гипотезы плана нет."""
    owner = await hypothesis(session)
    seller = await make_user(SELLER)
    found = await estimate(session, ask(owner.id))
    jobs = _Jobs()

    launched = await launch(session, owner.id, found, jobs=jobs, author=seller)

    assert launched is not None
    await session.refresh(launched.run)
    assert (launched.run.from_page, launched.run.plan_hypothesis_id) == (1, None)
    assert launched.run.created_by == SELLER
    assert len(jobs.enqueued) == 1
    [journal] = await started(session)
    assert journal.user_id == seller.id
    assert journal.details is not None
    assert journal.details["действие"] == STARTED
    assert "гипотеза плана" not in journal.details


async def test_a_queue_that_does_not_answer_leaves_no_run(session: AsyncSession) -> None:
    """Очередь не ответила — исключение Redis вызывающему, и прогона без задачи нет."""
    owner = await hypothesis(session)
    found = await estimate(session, ask(owner.id, depth_pages=2, from_page=2))

    with pytest.raises(RedisConnectionError):
        await launch(session, owner.id, found, jobs=_Down())

    assert (await runs(session), await started(session)) == (0, [])


async def test_a_taken_job_number_launches_nothing_and_says_so(session: AsyncSession) -> None:
    """Номер задачи свежего прогона занят — `None`: строка прогона остаётся без задачи, записи
    журнала о запуске нет (маршрут отвечает на это 409 словами)."""
    owner = await hypothesis(session)
    found = await estimate(session, ask(owner.id))

    launched = await launch(session, owner.id, found, jobs=_Taken())

    assert launched is None
    run = await session.scalar(select(SalesSegmentRunModel))
    assert run is not None
    assert run.job_id is None
    assert await started(session) == []
