"""Волны глубины плана работ (`sales/plan_waves.py`) на настоящей базе.

Проверяется то, ради чего модуль: волны — ступени книги, обрезанные предельной глубиной листа;
пачка — ключи одного рынка по порядку строк листа, не больше `keys_per_run`; волна кончилась —
следующая, а ключ, добавленный позже, сперва догоняет страницу 1; со второй волны первыми идут ключи,
чья страница 1 дала принятые сайты, а ключи с пустой выдачей глубже не идут; волны кончились — «нужны
ключи» значением; купленное отмечается по итогу выдачи, без потерянных ключей и только вперёд;
«найдено» (`plan_found.found_sites`) — уникальные принятые сайты прогонов гипотезы плана — в обычном
режиме судьи и в «тени», где полосы кладут те же правила, что у прогона (`segment_judge.effective`,
`segment_report.decide`).

Итог выдачи одного прогона пишет настоящий сбор (`segment_serp.collect`) с выдачей-подделкой. Ключи
— разных ниш и письменностей, сайты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.sales import plan_found, plan_waves, segment_report, segment_serp
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_models import (
    PlanMode,
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
    SalesPlanModel,
)
from backend.features.sales.plan_waves import Batch, NeedKeys, Wave
from backend.features.sales.segment_judge import SegmentVerdict, effective
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)
from backend.features.serp.protocol import SerpResult, SerpWatch, keyword_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

Key = SalesPlanKeywordModel
Run = SalesSegmentRunModel
Candidate = SalesSegmentCandidateModel

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
OWNER = "owner@example.test"
#: Ключи листа — разных ниш и письменностей; «ß» правило выдачи сводит к «ss».
KEYS = (
    "ремонт кофемашин",
    "dental implants price",
    "Fußpflege Praxis",
    "家具 批发",
    "طباعة ثلاثية الأبعاد",
)
_numbers = itertools.count(1)


async def make_plan(session: AsyncSession, *, cap: Decimal = Decimal("100.00")) -> SalesPlanModel:
    plan = SalesPlanModel(
        name="План недели",
        mode=PlanMode.ECONOMY.value,
        cap_usd=cap,
        estimate={},
        digest="0" * 16,
    )
    session.add(plan)
    await session.flush()
    return plan


async def add_keys(
    session: AsyncSession, row: SalesPlanHypothesisModel, keys: Sequence[str], *, start: int = 1
) -> None:
    """Ключи листа — в реестр на каждом рынке, строкой листа с `start`."""
    for market in row.markets:
        for position, phrase in enumerate(keys, start):
            session.add(
                Key(
                    plan_hypothesis_id=row.id,
                    market=market,
                    keyword=phrase,
                    keyword_key=keyword_key(phrase),
                    position=position,
                )
            )
    await session.flush()


async def sheet(
    session: AsyncSession,
    plan: SalesPlanModel | None = None,
    *,
    markets: Sequence[str] = ("de",),
    max_pages: int = 5,
    keys: Sequence[str] = KEYS,
    target: int = 100,
    cap: Decimal = Decimal("50.00"),
) -> SalesPlanHypothesisModel:
    """Лист плана: своя гипотеза продаж, строка гипотезы плана и реестр ключей."""
    plan = plan or await make_plan(session)
    number = next(_numbers)
    hypothesis = SalesHypothesisModel(name=f"Гипотеза плана {number}", description="Сегмент.")
    session.add(hypothesis)
    await session.flush()
    row = SalesPlanHypothesisModel(
        plan_id=plan.id,
        hypothesis_id=hypothesis.id,
        position=number,
        sheet=f"Лист {number}",
        markets=list(markets),
        language="en",
        target=target,
        max_pages=max_pages,
        cap_usd=cap,
    )
    session.add(row)
    await session.flush()
    await add_keys(session, row, keys)
    return row


async def take(session: AsyncSession, row: SalesPlanHypothesisModel, per_run: int = 2) -> Batch:
    found = await plan_waves.next_batch(session, row, keys_per_run=per_run)
    assert isinstance(found, Batch), found
    return found


async def make_run(
    session: AsyncSession,
    row: SalesPlanHypothesisModel,
    batch: Batch,
    *,
    report: dict[str, Any] | None = None,
    mode: JudgeMode = JudgeMode.ENFORCE,
    own: bool = True,
) -> Run:
    """Прогон пачки; `own=False` — прогон экрана «Сбор базы» той же гипотезы, без плана."""
    run = Run(
        hypothesis_id=row.hypothesis_id,
        plan_hypothesis_id=row.id if own else None,
        keywords=list(batch.keywords),
        country=batch.market,
        language=row.language,
        from_page=batch.wave.first,
        depth_pages=batch.wave.last,
        mode=mode.value,
        estimate={},
        report=report or {},
        stage=Stage.DONE,
        finished_at=NOW,
    )
    session.add(run)
    await session.flush()
    return run


def tally(*, empty: Sequence[str] = (), lost: Sequence[str] = ()) -> dict[str, Any]:
    """Итог выдачи в отчёте прогона — полями, как его пишет сбор (`segment_serp.collect`)."""
    return {
        segment_serp.KEY: {"hosts": 0, "empty_keywords": list(empty), "lost_keywords": list(lost)}
    }


async def bought(
    session: AsyncSession,
    row: SalesPlanHypothesisModel,
    batch: Batch,
    *,
    empty: Sequence[str] = (),
    mode: JudgeMode = JudgeMode.ENFORCE,
) -> Run:
    """Прогон пачки с купленной выдачей и отметкой купленного."""
    run = await make_run(session, row, batch, report=tally(empty=empty), mode=mode)
    await plan_waves.mark_bought(session, run)
    return run


async def site(
    session: AsyncSession, run: Run, keyword: str, lane: Lane = Lane.ACCEPT, **columns: Any
) -> Candidate:
    """Сайт выдачи прогона, найденный ключом, — в нужной полосе."""
    domain = DomainModel(host=f"site-{next(_numbers)}.example.test")
    session.add(domain)
    await session.flush()
    made = Candidate(
        run_id=run.id,
        hypothesis_id=run.hypothesis_id,
        domain_id=domain.id,
        keyword=keyword,
        lane=lane.value,
        **columns,
    )
    session.add(made)
    await session.flush()
    return made


async def pages(session: AsyncSession, row: SalesPlanHypothesisModel) -> dict[tuple[str, str], int]:
    rows = await session.execute(
        select(Key.market, Key.keyword, Key.pages).where(Key.plan_hypothesis_id == row.id)
    )
    return {(market, phrase): got for market, phrase, got in rows.tuples()}


# --- волны ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("max_pages", "expected"),
    [
        (1, [(1, 1)]),
        (2, [(1, 1), (2, 2)]),
        (3, [(1, 1), (2, 2), (3, 3)]),
        (5, [(1, 1), (2, 2), (3, 3), (4, 5)]),
        # Не ступень книги — последняя волна обрезается по предельной глубине.
        (4, [(1, 1), (2, 2), (3, 3), (4, 4)]),
    ],
)
def test_waves_are_the_book_steps_cut_by_the_sheet_depth(
    max_pages: int, expected: list[tuple[int, int]]
) -> None:
    assert [(wave.first, wave.last) for wave in plan_waves.waves(max_pages)] == expected


# --- пачка ----------------------------------------------------------------------------------


async def test_a_batch_is_one_market_in_sheet_order_up_to_keys_per_run(
    session: AsyncSession,
) -> None:
    row = await sheet(session, markets=("de", "fr"), keys=())
    # Строки листа вписаны в реестр не по порядку: пачка — по строке листа, а не по записи.
    await add_keys(session, row, KEYS[2:], start=3)
    await add_keys(session, row, KEYS[:2])

    first = await take(session, row, per_run=3)
    assert first == Batch(Wave(1, 1), "de", KEYS[:3])
    await bought(session, row, first)

    second = await take(session, row, per_run=3)
    assert second == Batch(Wave(1, 1), "de", KEYS[3:])
    await bought(session, row, second)

    # Рынок кончился — следующий рынок той же волны, своей пачкой.
    assert await take(session, row, per_run=3) == Batch(Wave(1, 1), "fr", KEYS[:3])


async def test_the_next_wave_starts_when_every_key_has_the_page(session: AsyncSession) -> None:
    row = await sheet(session, keys=KEYS[:3], max_pages=2)
    await bought(session, row, await take(session, row))
    left = await take(session, row)
    assert left == Batch(Wave(1, 1), "de", KEYS[2:3])
    run = await bought(session, row, left)
    assert await pages(session, row) == {("de", phrase): 1 for phrase in KEYS[:3]}
    owner = await session.scalar(
        select(Key.run_id).where(Key.plan_hypothesis_id == row.id, Key.keyword == KEYS[2])
    )
    assert owner == run.id

    assert await take(session, row, per_run=5) == Batch(Wave(2, 2), "de", KEYS[:3])


async def test_a_key_added_later_catches_up_page_one_before_the_hypothesis_goes_deeper(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=KEYS[:2], max_pages=3)
    await bought(session, row, await take(session, row))
    assert (await take(session, row)).wave == Wave(2, 2)

    await add_keys(session, row, KEYS[2:4], start=3)

    assert await take(session, row) == Batch(Wave(1, 1), "de", KEYS[2:4])


async def test_from_the_second_wave_keys_whose_first_page_gave_accepted_sites_go_first(
    session: AsyncSession,
) -> None:
    row = await sheet(session, max_pages=2)
    first = await bought(session, row, await take(session, row, per_run=5))
    # Ключ 4 дал принятый сайт, ключ 2 — два; ключ 1 — только «на проверке» и отсечённый.
    await site(session, first, KEYS[3])
    await site(session, first, KEYS[1])
    await site(session, first, KEYS[1])
    await site(session, first, KEYS[0], Lane.REVIEW)
    await site(session, first, KEYS[0], Lane.REJECT)
    # Ключ 3 выдача назвала по-своему — сверка правилом выдачи, а не строкой.
    await site(session, first, " fusspflege   PRAXIS ")

    batch = await take(session, row, per_run=5)

    assert batch == Batch(Wave(2, 2), "de", (KEYS[1], KEYS[2], KEYS[3], KEYS[0], KEYS[4]))


async def test_a_key_with_an_empty_serp_goes_no_deeper(session: AsyncSession) -> None:
    row = await sheet(session, max_pages=3)
    await bought(session, row, await take(session, row, per_run=5), empty=[KEYS[1]])

    second = await take(session, row, per_run=5)
    assert second == Batch(Wave(2, 2), "de", (KEYS[0], KEYS[2], KEYS[3], KEYS[4]))
    # Страница 2 пуста у ключа 4: за пустой страницей выдача тоже пуста.
    await bought(session, row, second, empty=[KEYS[3]])

    third = await take(session, row, per_run=5)
    assert third == Batch(Wave(3, 3), "de", (KEYS[0], KEYS[2], KEYS[4]))


async def test_more_empty_keys_than_query_parameters_go_to_the_base_as_one_array(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=KEYS[:1], max_pages=2)
    # Параметров у запроса Postgres — не больше 32 767: список ключей — одним массивом.
    many = [f"ключ {number}" for number in range(33_000)]
    await bought(session, row, await take(session, row), empty=many)

    assert await take(session, row) == Batch(Wave(2, 2), "de", KEYS[:1])


async def test_the_last_wave_buys_pages_four_and_five_with_one_run(session: AsyncSession) -> None:
    row = await sheet(session, keys=KEYS[:1])
    for page in (1, 2, 3):
        batch = await take(session, row)
        assert batch.wave == Wave(page, page)
        await bought(session, row, batch)

    last = await take(session, row)

    assert last == Batch(Wave(4, 5), "de", KEYS[:1])
    await bought(session, row, last)
    assert await pages(session, row) == {("de", KEYS[0]): 5}


@pytest.mark.parametrize("max_pages", [1, 2, 3, 5])
async def test_when_every_wave_is_bought_the_answer_is_need_keys_as_a_value(
    session: AsyncSession, max_pages: int
) -> None:
    row = await sheet(session, markets=("de", "fr"), keys=KEYS[:2], max_pages=max_pages)
    for _ in range(2 * len(plan_waves.waves(max_pages))):
        await bought(session, row, await take(session, row))

    assert await plan_waves.next_batch(session, row, keys_per_run=2) == NeedKeys(max_pages)


async def test_keys_that_all_ran_empty_mean_need_keys_before_the_depth(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=KEYS[:2])
    await bought(session, row, await take(session, row), empty=list(KEYS[:2]))

    assert await plan_waves.next_batch(session, row, keys_per_run=2) == NeedKeys(5)


async def test_keys_per_run_below_one_is_a_call_error(session: AsyncSession) -> None:
    row = await sheet(session)

    with pytest.raises(ValueError, match="от одного"):
        await plan_waves.next_batch(session, row, keys_per_run=0)


# --- отметка купленного ---------------------------------------------------------------------


@dataclass
class Serp:
    """Выдача-подделка: строки по ключу; ключа нет в таблице — источник его не отдал."""

    answers: dict[str, list[SerpResult]]
    name: str = "fake"
    spent: float = 0.0
    asked: list[tuple[str, ...]] = field(default_factory=list)

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        start_page: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        self.asked.append(tuple(keywords))
        return {key: self.answers[key] for key in keywords if key in self.answers}


async def test_the_serp_tally_of_a_real_collection_marks_bought_keys_and_returns_lost_ones(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=KEYS[:3], max_pages=2)
    run = await make_run(session, row, await take(session, row, per_run=3))
    found = SerpResult(1, "https://shop.example.test/", "Каталог", "Сайт сегмента")
    # Ключ 1 — с результатами, ключ 2 — пустой, ключ 3 источник не отдал.
    serp = Serp({KEYS[0]: [found], KEYS[1]: []})

    assert await plan_waves.mark_bought(session, run) == 0  # выдача ещё не куплена
    serp_tally = await segment_serp.collect(session, run, serp)

    assert (serp_tally["empty_keywords"], serp_tally["lost_keywords"]) == ([KEYS[1]], [KEYS[2]])
    assert await plan_waves.mark_bought(session, run) == 2
    assert await pages(session, row) == {
        ("de", KEYS[0]): 1,
        ("de", KEYS[1]): 1,
        ("de", KEYS[2]): 0,
    }
    # Потерянный — в пачку той же волны; пустой глубже не идёт.
    retry = await take(session, row, per_run=3)
    assert retry == Batch(Wave(1, 1), "de", KEYS[2:3])
    await bought(session, row, retry)
    assert await take(session, row, per_run=3) == Batch(Wave(2, 2), "de", (KEYS[0], KEYS[2]))


async def test_marking_moves_only_forward_and_only_on_the_runs_market_and_sheet(
    session: AsyncSession,
) -> None:
    plan = await make_plan(session)
    row = await sheet(session, plan, markets=("de", "fr"), keys=KEYS[:2], max_pages=2)
    other = await sheet(session, plan, keys=KEYS[:2])
    first = await bought(session, row, Batch(Wave(1, 1), "de", KEYS[:2]))
    second = await bought(session, row, Batch(Wave(2, 2), "de", KEYS[:2]))

    # Повтор отметки прогона страницы 1 не отматывает купленное глубже и не отдаёт ему ключи.
    assert await plan_waves.mark_bought(session, first) == 0
    assert await plan_waves.mark_bought(session, second) == 2
    assert await pages(session, row) == {
        ("de", KEYS[0]): 2,
        ("de", KEYS[1]): 2,
        ("fr", KEYS[0]): 0,
        ("fr", KEYS[1]): 0,
    }
    owners = await session.scalars(
        select(Key.run_id).where(Key.plan_hypothesis_id == row.id, Key.market == "de")
    )
    assert set(owners.all()) == {second.id}
    assert set((await pages(session, other)).values()) == {0}


async def test_a_run_from_the_collection_screen_marks_nothing(session: AsyncSession) -> None:
    row = await sheet(session, keys=KEYS[:1])
    run = await make_run(session, row, await take(session, row), report=tally(), own=False)

    assert await plan_waves.mark_bought(session, run) == 0
    assert await pages(session, row) == {("de", KEYS[0]): 0}


# --- «найдено» ------------------------------------------------------------------------------


async def judged(
    session: AsyncSession, run: Run, verdict: Lane, *, by: Decider = Decider.MODEL
) -> Candidate:
    """Сайт, как его кладёт судья: полоса — по вердикту и режиму прогона (`effective`)."""
    said = SegmentVerdict(verdict, "по тексту сайта", decided_by=by)
    lane, reason = effective(said, JudgeMode(run.mode))
    return await site(
        session,
        run,
        run.keywords[0],
        lane,
        verdict=verdict.value,
        reason=reason,
        decided_by=by.value,
    )


async def test_found_counts_unique_accepted_sites_of_this_plan_hypothesis_runs(
    session: AsyncSession,
) -> None:
    plan = await make_plan(session)
    row = await sheet(session, plan, markets=("de", "fr"))
    other = await sheet(session, plan)
    one = await bought(session, row, await take(session, row, per_run=5))
    two = await bought(session, row, await take(session, row, per_run=5))
    assert (one.country, two.country) == ("de", "fr")
    await judged(session, one, Lane.ACCEPT)
    await judged(session, two, Lane.ACCEPT)
    await judged(session, two, Lane.REJECT)
    review = await judged(session, two, Lane.REVIEW)
    # Не в счёт: прогон другой гипотезы плана и прогон экрана «Сбор базы» той же гипотезы.
    await judged(session, await bought(session, other, await take(session, other)), Lane.ACCEPT)
    manual = await make_run(session, row, Batch(Wave(1, 1), "de", KEYS[:1]), own=False)
    await judged(session, manual, Lane.ACCEPT)

    assert await plan_found.found_sites(session, row.id) == 2

    await segment_report.decide(session, review.id, accept=True, author=OWNER)
    assert await plan_found.found_sites(session, row.id) == 3


async def test_in_shadow_found_counts_judge_and_human_accepts_only(session: AsyncSession) -> None:
    row = await sheet(session)
    run = await bought(session, row, await take(session, row), mode=JudgeMode.SHADOW)
    await judged(session, run, Lane.ACCEPT)
    would = await judged(session, run, Lane.REJECT)
    review = await judged(session, run, Lane.REVIEW)
    cut = await judged(session, run, Lane.REJECT, by=Decider.RULE)
    left = await judged(session, run, Lane.REJECT)
    assert (would.lane, review.lane, cut.lane) == (Lane.WOULD_REJECT, Lane.REVIEW, Lane.REJECT)

    assert await plan_found.found_sites(session, row.id) == 1

    await segment_report.decide(session, would.id, accept=True, author=OWNER)
    await segment_report.decide(session, review.id, accept=True, author=OWNER)
    await segment_report.decide(session, left.id, accept=False, author=OWNER)
    assert await plan_found.found_sites(session, row.id) == 3


async def test_a_plan_hypothesis_without_runs_found_nothing(session: AsyncSession) -> None:
    assert await plan_found.found_sites(session, (await sheet(session)).id) == 0
